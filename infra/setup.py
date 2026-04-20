#!/usr/bin/env python3
"""
infrastructure/setup.py
=======================
AWS setup script for the URL Shortener project.
Run this once to provision all required AWS resources.

Maps directly to concepts taught in lectures:
  - S3 (Lecture 12): object storage, host static frontend
  - DynamoDB (Lecture 9/10): NoSQL table with GSI
  - Lambda (Lecture 16): serverless functions
  - API Gateway (Lecture 16): HTTP trigger for Lambda
  - Cognito (Lecture 17): user pool, app client, JWT authorizer
  - SNS (Lecture 20): pub/sub topic, Lambda + email subscriptions

Usage:
    pip install boto3
    python setup.py

Set environment variables before running:
    export AWS_ACCESS_KEY_ID=...
    export AWS_SECRET_ACCESS_KEY=...
    export AWS_DEFAULT_REGION=us-east-1
    export NOTIFICATION_EMAIL=you@example.com
"""

import boto3
import json
import os
import zipfile
import io
import time

REGION         = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
ACCOUNT_ID     = boto3.client("sts").get_caller_identity()["Account"]
NOTIFY_EMAIL   = os.environ.get("NOTIFICATION_EMAIL", "admin@example.com")
PROJECT        = "url-shortener"

# ── clients ──────────────────────────────────────────────────────────────────
s3       = boto3.client("s3",            region_name=REGION)
dynamodb = boto3.client("dynamodb",      region_name=REGION)
cognito  = boto3.client("cognito-idp",   region_name=REGION)
lam      = boto3.client("lambda",        region_name=REGION)
apigw    = boto3.client("apigateway",    region_name=REGION)
sns      = boto3.client("sns",           region_name=REGION)
iam      = boto3.client("iam")


# =============================================================================
# 1. S3 BUCKET — host the React/HTML frontend (Lecture 12)
# =============================================================================
def create_s3_bucket():
    bucket_name = f"{PROJECT}-frontend-{ACCOUNT_ID}"
    print(f"[S3] Creating bucket: {bucket_name}")

    if REGION == "us-east-1":
        s3.create_bucket(Bucket=bucket_name)
    else:
        s3.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )

    # Enable static website hosting
    s3.put_bucket_website(
        Bucket=bucket_name,
        WebsiteConfiguration={
            "IndexDocument": {"Suffix": "index.html"},
            "ErrorDocument": {"Key": "index.html"},
        },
    )

    # Public read policy (frontend assets are public)
    s3.put_bucket_policy(
        Bucket=bucket_name,
        Policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect":    "Allow",
                "Principal": "*",
                "Action":    "s3:GetObject",
                "Resource":  f"arn:aws:s3:::{bucket_name}/*",
            }],
        }),
    )

    website_url = f"http://{bucket_name}.s3-website-{REGION}.amazonaws.com"
    print(f"[S3] Frontend URL: {website_url}")
    return bucket_name, website_url


# =============================================================================
# 2. DYNAMODB TABLE — store link metadata (Lecture 9 / 10)
#    Partition key: code (string)
#    GSI: owner-index on 'owner' attribute  → efficient per-user queries
# =============================================================================
def create_dynamodb_table():
    table_name = f"{PROJECT}-links"
    print(f"[DynamoDB] Creating table: {table_name}")

    dynamodb.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "code", "KeyType": "HASH"},   # Partition key
        ],
        AttributeDefinitions=[
            {"AttributeName": "code",  "AttributeType": "S"},
            {"AttributeName": "owner", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "owner-index",
                "KeySchema": [
                    {"AttributeName": "owner", "KeyType": "HASH"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "BillingMode": "PAY_PER_REQUEST",
            }
        ],
        BillingMode="PAY_PER_REQUEST",  # No capacity planning needed
    )

    # Wait for table to be active before continuing
    waiter = dynamodb.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    print(f"[DynamoDB] Table active: {table_name}")
    return table_name


# =============================================================================
# 3. IAM ROLE — Lambda execution role with DynamoDB + SNS permissions
# =============================================================================
def create_lambda_role():
    role_name = f"{PROJECT}-lambda-role"
    print(f"[IAM] Creating Lambda role: {role_name}")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect":    "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action":    "sts:AssumeRole",
        }],
    }

    role = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
    )

    # Attach AWS-managed policies
    for policy_arn in [
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess",
        "arn:aws:iam::aws:policy/AmazonSNSFullAccess",
        "arn:aws:iam::aws:policy/AmazonSESFullAccess",
    ]:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

    time.sleep(10)  # IAM propagation delay
    return role["Role"]["Arn"]


# =============================================================================
# 4. SNS TOPIC — pub/sub for link events (Lecture 20)
#    Producers: shorten Lambda, redirect Lambda
#    Consumers: milestone_notifier Lambda (email)
# =============================================================================
def create_sns_topic():
    topic_name = f"{PROJECT}-events"
    print(f"[SNS] Creating topic: {topic_name}")
    response = sns.create_topic(Name=topic_name)
    topic_arn = response["TopicArn"]
    print(f"[SNS] Topic ARN: {topic_arn}")
    return topic_arn


# =============================================================================
# 5. COGNITO USER POOL — managed auth (Lecture 17)
#    - Handles sign-up, sign-in, password reset
#    - Issues JWT tokens (IdToken, AccessToken, RefreshToken)
#    - API Gateway uses Cognito as authorizer to validate JWTs
# =============================================================================
def create_cognito_user_pool():
    pool_name = f"{PROJECT}-users"
    print(f"[Cognito] Creating User Pool: {pool_name}")

    pool = cognito.create_user_pool(
        PoolName=pool_name,
        AutoVerifiedAttributes=["email"],
        UsernameAttributes=["email"],       # Sign in with email (like Lecture 17 shows)
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 8,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": False,
            }
        },
        Schema=[
            {
                "Name": "email",
                "AttributeDataType": "String",
                "Required": True,
                "Mutable": True,
            }
        ],
    )
    pool_id = pool["UserPool"]["Id"]

    # App client — no secret (browser-based SPA)
    client = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{PROJECT}-app-client",
        GenerateSecret=False,
        ExplicitAuthFlows=[
            "ALLOW_USER_PASSWORD_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
            "ALLOW_USER_SRP_AUTH",
        ],
        AccessTokenValidity=60,    # 60 minutes
        IdTokenValidity=60,
        RefreshTokenValidity=30,   # 30 days (Lecture 17)
        TokenValidityUnits={
            "AccessToken":  "minutes",
            "IdToken":      "minutes",
            "RefreshToken": "days",
        },
    )
    client_id = client["UserPoolClient"]["ClientId"]

    print(f"[Cognito] Pool ID: {pool_id} | Client ID: {client_id}")
    return pool_id, client_id


# =============================================================================
# 6. LAMBDA FUNCTIONS (Lecture 16)
#    Each function = one independently deployable unit of business logic
# =============================================================================
def package_lambda(filename: str) -> bytes:
    """Zip a single Python file into Lambda deployment package."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(f"lambdas/{filename}", filename)
    buf.seek(0)
    return buf.read()


def create_lambda(name, handler_file, handler_fn, role_arn, env_vars):
    fn_name = f"{PROJECT}-{name}"
    print(f"[Lambda] Creating: {fn_name}")
    code = package_lambda(handler_file)

    lam.create_function(
        FunctionName=fn_name,
        Runtime="python3.12",
        Role=role_arn,
        Handler=f"{handler_file.replace('.py', '')}.{handler_fn}",
        Code={"ZipFile": code},
        Timeout=30,
        MemorySize=256,
        Environment={"Variables": env_vars},
    )

    waiter = lam.get_waiter("function_active")
    waiter.wait(FunctionName=fn_name)
    arn = lam.get_function(FunctionName=fn_name)["Configuration"]["FunctionArn"]
    print(f"[Lambda] Active: {arn}")
    return arn


# =============================================================================
# 7. API GATEWAY REST API (Lecture 16)
#    Routes: POST /links, GET /links, DELETE /links/{code}, GET /r/{code}
#    Cognito Authorizer validates JWT on protected routes (Lecture 17)
# =============================================================================
def create_api(pool_id, lambda_arns, region):
    api_name = f"{PROJECT}-api"
    print(f"[API Gateway] Creating API: {api_name}")

    api = apigw.create_rest_api(
        name=api_name,
        description="URL Shortener REST API",
        endpointConfiguration={"types": ["REGIONAL"]},
    )
    api_id = api["id"]
    root_id = apigw.get_resources(restApiId=api_id)["items"][0]["id"]

    # --- Cognito Authorizer (Lecture 17) ---
    authorizer = apigw.create_authorizer(
        restApiId=api_id,
        name="CognitoAuth",
        type="COGNITO_USER_POOLS",
        providerARNs=[f"arn:aws:cognito-idp:{region}:{ACCOUNT_ID}:userpool/{pool_id}"],
        identitySource="method.request.header.Authorization",
    )
    authorizer_id = authorizer["id"]

    def add_route(resource_id, http_method, lambda_arn, protected=True):
        """Add an HTTP method to a resource and wire it to a Lambda."""
        apigw.put_method(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod=http_method,
            authorizationType="COGNITO_USER_POOLS" if protected else "NONE",
            authorizerId=authorizer_id if protected else None,
        )
        apigw.put_integration(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod=http_method,
            type="AWS_PROXY",
            integrationHttpMethod="POST",
            uri=(
                f"arn:aws:apigateway:{region}:lambda:path"
                f"/2015-03-31/functions/{lambda_arn}/invocations"
            ),
        )
        # Grant API Gateway permission to invoke Lambda
        lam.add_permission(
            FunctionName=lambda_arn,
            StatementId=f"apigw-{http_method}-{resource_id}",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{region}:{ACCOUNT_ID}:{api_id}/*/{http_method}/*",
        )

    # /links resource
    links_res = apigw.create_resource(
        restApiId=api_id, parentId=root_id, pathPart="links"
    )["id"]
    add_route(links_res, "POST", lambda_arns["shorten"],    protected=True)
    add_route(links_res, "GET",  lambda_arns["list_links"], protected=True)

    # /links/{code}
    code_res = apigw.create_resource(
        restApiId=api_id, parentId=links_res, pathPart="{code}"
    )["id"]
    add_route(code_res, "DELETE", lambda_arns["delete_link"], protected=True)

    # /r/{code} — public redirect endpoint (no auth required)
    r_res = apigw.create_resource(
        restApiId=api_id, parentId=root_id, pathPart="r"
    )["id"]
    r_code_res = apigw.create_resource(
        restApiId=api_id, parentId=r_res, pathPart="{code}"
    )["id"]
    add_route(r_code_res, "GET", lambda_arns["redirect"], protected=False)

    # Deploy to 'prod' stage
    apigw.create_deployment(restApiId=api_id, stageName="prod")
    invoke_url = f"https://{api_id}.execute-api.{region}.amazonaws.com/prod"
    print(f"[API Gateway] Invoke URL: {invoke_url}")
    return api_id, invoke_url


# =============================================================================
# MAIN — orchestrate all setup steps
# =============================================================================
def main():
    print("=" * 60)
    print("URL Shortener — AWS Infrastructure Setup")
    print("=" * 60)

    bucket_name, frontend_url = create_s3_bucket()
    table_name                 = create_dynamodb_table()
    role_arn                   = create_lambda_role()
    topic_arn                  = create_sns_topic()
    pool_id, client_id         = create_cognito_user_pool()

    # Common Lambda env vars
    base_env = {
        "AWS_REGION":    REGION,
        "LINKS_TABLE":   table_name,
        "SNS_TOPIC_ARN": topic_arn,
        "BASE_URL":      "PLACEHOLDER",   # updated after API GW created
    }

    # Create the four Lambda functions
    lambda_arns = {
        "shorten":    create_lambda("shorten",    "shorten.py",    "lambda_handler", role_arn, base_env),
        "redirect":   create_lambda("redirect",   "redirect.py",   "lambda_handler", role_arn, base_env),
        "list_links": create_lambda("list-links", "list_links.py", "lambda_handler", role_arn, base_env),
        "delete_link":create_lambda("delete-link","delete_link.py","lambda_handler", role_arn, base_env),
        "notifier":   create_lambda("notifier",   "milestone_notifier.py", "lambda_handler", role_arn,
                                    {**base_env, "FROM_EMAIL": NOTIFY_EMAIL}),
    }

    api_id, invoke_url = create_api(pool_id, lambda_arns, REGION)

    # Update Lambda env vars with real API Gateway URL
    for fn_key in ["shorten", "redirect"]:
        fn_name = f"{PROJECT}-{fn_key}"
        lam.update_function_configuration(
            FunctionName=fn_name,
            Environment={"Variables": {**base_env, "BASE_URL": invoke_url}},
        )

    # Subscribe milestone_notifier Lambda to SNS topic (Lecture 20)
    sns.subscribe(
        TopicArn=topic_arn,
        Protocol="lambda",
        Endpoint=lambda_arns["notifier"],
    )
    lam.add_permission(
        FunctionName=lambda_arns["notifier"],
        StatementId="sns-invoke",
        Action="lambda:InvokeFunction",
        Principal="sns.amazonaws.com",
        SourceArn=topic_arn,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("✅ Setup complete!")
    print("=" * 60)
    print(f"  Frontend S3 URL : {frontend_url}")
    print(f"  API Gateway URL : {invoke_url}")
    print(f"  Cognito Pool ID : {pool_id}")
    print(f"  Cognito Client  : {client_id}")
    print(f"  DynamoDB Table  : {table_name}")
    print(f"  SNS Topic ARN   : {topic_arn}")
    print(f"\nNext steps:")
    print(f"  1. Deploy frontend/index.html to the S3 bucket")
    print(f"  2. Update frontend/config.js with the values above")
    print(f"  3. Verify NOTIFY_EMAIL in SES before milestone emails will send")
    print("=" * 60)


if __name__ == "__main__":
    main()
