"""
Lambda: GET /links
Returns all short links owned by the authenticated user.
Uses a DynamoDB GSI (Global Secondary Index) on 'owner'
to efficiently query by user without a full table scan.

Taught concepts used:
  - Lambda (Lecture 16)
  - DynamoDB Query with GSI (Lecture 9/10)
  - Cognito JWT claims extraction (Lecture 17)
  - API Gateway Cognito authorizer (Lecture 16/17)
"""

import json
import os
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
table    = dynamodb.Table(os.environ["LINKS_TABLE"])


def lambda_handler(event, context):
    # Extract user email from Cognito claims (Lecture 17)
    claims = (
        event.get("requestContext", {})
             .get("authorizer", {})
             .get("claims", {})
    )
    user_email = claims.get("email", "")

    if not user_email:
        return {
            "statusCode": 401,
            "body": json.dumps({"error": "Unauthorized"}),
        }

    # Query DynamoDB GSI 'owner-index' (Lecture 9/10 - Query with KeyConditionExpression)
    response = table.query(
        IndexName="owner-index",
        KeyConditionExpression=Key("owner").eq(user_email),
    )

    links = response.get("Items", [])

    # Convert Decimal to int for JSON serialisation
    for link in links:
        link["click_count"] = int(link.get("click_count", 0))
        link["created_at"]  = int(link.get("created_at", 0))

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"links": links}),
    }
