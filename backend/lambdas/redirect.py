"""
Lambda: GET /r/{code}
Redirects the visitor to the original long URL.
Increments click_count atomically in DynamoDB.
Publishes a "link_clicked" event to SNS so analytics/notifications
can react independently (Lecture 20 - event-driven, pub/sub).

Taught concepts used:
  - Lambda (Lecture 16): FaaS handler pattern
  - DynamoDB UpdateItem (Lecture 9/10): atomic counter increment
  - DynamoDB GetItem (Lecture 9/10)
  - SNS publish (Lecture 20): fan-out to milestone notifier + analytics
  - API Gateway (Lecture 16)
"""

import json
import os
import time
import boto3

dynamodb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
table    = dynamodb.Table(os.environ["LINKS_TABLE"])

sns          = boto3.client("sns", region_name=os.environ["AWS_REGION"])
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]

MILESTONE_COUNTS = {10, 100, 1_000, 10_000}


def lambda_handler(event, context):
    code = (event.get("pathParameters") or {}).get("code", "")

    if not code:
        return {"statusCode": 400, "body": "Missing code"}

    # Atomic increment of click_count (DynamoDB UpdateItem - Lecture 10)
    try:
        result = table.update_item(
            Key={"code": code},
            UpdateExpression="SET click_count = click_count + :inc, last_clicked = :ts",
            ExpressionAttributeValues={":inc": 1, ":ts": int(time.time())},
            ConditionExpression="attribute_exists(code)",   # 404 if code missing
            ReturnValues="ALL_NEW",
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return {"statusCode": 404, "body": "Short link not found"}

    item        = result["Attributes"]
    long_url    = item["long_url"]
    click_count = int(item["click_count"])

    # Publish click event to SNS (Lecture 20 - pub/sub fan-out)
    message = {
        "event":       "link_clicked",
        "code":        code,
        "long_url":    long_url,
        "owner":       item.get("owner", ""),
        "click_count": click_count,
    }

    # If milestone hit, add flag so SNS subscriber can send email/SMS
    if click_count in MILESTONE_COUNTS:
        message["milestone"] = click_count

    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"Link /{code} clicked (total: {click_count})",
        Message=json.dumps(message),
        MessageAttributes={
            "event_type": {
                "DataType":    "String",
                "StringValue": "link_clicked",
            }
        },
    )

    # HTTP 301 redirect
    return {
        "statusCode": 301,
        "headers": {
            "Location": long_url,
            "Cache-Control": "no-cache",
        },
        "body": "",
    }
