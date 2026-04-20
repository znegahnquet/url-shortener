"""
Lambda: POST /links
Creates a shortened URL, stores metadata in DynamoDB,
and publishes a "link created" event to SNS.

Taught concepts used:
  - Lambda (Lecture 16): FaaS, stateless, event-driven
  - DynamoDB PutItem (Lecture 9/10): partition key, put_item
  - SNS publish (Lecture 20): pub/sub, one event -> many consumers
  - JWT / Cognito (Lecture 13/17): jwt_required via API Gateway Cognito authorizer
  - API Gateway (Lecture 16): routes HTTP requests to Lambda
"""

import json
import os
import string
import random
import time
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
table = dynamodb.Table(os.environ["LINKS_TABLE"])          # e.g. "url-shortener-links"

sns = boto3.client("sns", region_name=os.environ["AWS_REGION"])
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]

BASE_URL = os.environ["BASE_URL"]   # e.g. "https://abc123.execute-api.us-east-1.amazonaws.com/prod"


def generate_code(length: int = 6) -> str:
    """Generate a random alphanumeric short code."""
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))


def lambda_handler(event, context):
    # API Gateway passes the Cognito-validated claims in requestContext
    claims = (
        event.get("requestContext", {})
             .get("authorizer", {})
             .get("claims", {})
    )
    user_email = claims.get("email", "unknown")

    # Parse request body
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid JSON"})}

    long_url = body.get("long_url", "").strip()
    if not long_url:
        return {"statusCode": 400, "body": json.dumps({"error": "long_url is required"})}

    # Generate a unique short code (retry on collision)
    for _ in range(5):
        code = generate_code()
        existing = table.get_item(Key={"code": code}).get("Item")
        if not existing:
            break
    else:
        return {"statusCode": 500, "body": json.dumps({"error": "Could not generate unique code"})}

    # DynamoDB PutItem (Lecture 9/10)
    item = {
        "code":       code,                      # Partition key
        "long_url":   long_url,
        "owner":      user_email,
        "created_at": int(time.time()),
        "click_count": 0,
    }
    table.put_item(Item=item)

    short_url = f"{BASE_URL}/r/{code}"

    # SNS publish - "link_created" event (Lecture 20)
    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject="New short link created",
        Message=json.dumps({
            "event":     "link_created",
            "code":      code,
            "long_url":  long_url,
            "short_url": short_url,
            "owner":     user_email,
        }),
        MessageAttributes={
            "event_type": {
                "DataType":    "String",
                "StringValue": "link_created",
            }
        },
    )

    return {
        "statusCode": 201,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({
            "code":      code,
            "short_url": short_url,
            "long_url":  long_url,
        }),
    }
