"""
Lambda: DELETE /links/{code}
Deletes a short link — but only if it belongs to the requesting user.

Taught concepts used:
  - Lambda (Lecture 16)
  - DynamoDB DeleteItem with ConditionExpression (Lecture 9/10)
  - Cognito JWT claims (Lecture 17)
  - REST API design: DELETE /links/{code} (Lecture 7)
"""

import json
import os
import boto3

dynamodb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
table    = dynamodb.Table(os.environ["LINKS_TABLE"])


def lambda_handler(event, context):
    claims = (
        event.get("requestContext", {})
             .get("authorizer", {})
             .get("claims", {})
    )
    user_email = claims.get("email", "")

    if not user_email:
        return {"statusCode": 401, "body": json.dumps({"error": "Unauthorized"})}

    code = (event.get("pathParameters") or {}).get("code", "")
    if not code:
        return {"statusCode": 400, "body": json.dumps({"error": "Missing code"})}

    # Fetch to verify ownership before deleting (Lecture 9 - GetItem)
    response = table.get_item(Key={"code": code})
    item = response.get("Item")

    if not item:
        return {"statusCode": 404, "body": json.dumps({"error": "Link not found"})}

    if item.get("owner") != user_email:
        # 403 Forbidden - authenticated but not authorized (Lecture 7 HTTP status codes)
        return {"statusCode": 403, "body": json.dumps({"error": "Forbidden"})}

    # DynamoDB DeleteItem (Lecture 9/10)
    table.delete_item(Key={"code": code})

    return {
        "statusCode": 200,
        "headers": {"Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"message": f"Link /{code} deleted"}),
    }
