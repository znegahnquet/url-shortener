"""
Lambda: SNS Subscriber - Milestone Notifier
Triggered whenever SNS delivers a message to this Lambda subscription.
Sends an email alert when a link hits a milestone click count (10, 100, 1000…).

Taught concepts used:
  - SNS → Lambda subscription (Lecture 20: SNS supported protocols include Lambda)
  - Event-driven architecture (Lecture 20): producer (redirect Lambda) publishes,
    this consumer reacts independently without tight coupling
  - Lambda triggered by SNS event (Lecture 16: Triggers & Events - Storage/SNS events)
"""

import json
import os
import boto3

ses = boto3.client("ses", region_name=os.environ["AWS_REGION"])
FROM_EMAIL = os.environ["FROM_EMAIL"]   # SES-verified sender address


def lambda_handler(event, context):
    for record in event.get("Records", []):
        # SNS delivers the message inside record["Sns"]["Message"]
        raw_message = record.get("Sns", {}).get("Message", "{}")

        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            print("Could not parse SNS message:", raw_message)
            continue

        # Only act on milestone events
        if "milestone" not in message:
            continue

        owner       = message.get("owner", "")
        code        = message.get("code", "")
        short_url   = f"https://your-domain.com/r/{code}"
        long_url    = message.get("long_url", "")
        milestone   = message["milestone"]

        if not owner:
            continue

        # Send milestone email via SES
        ses.send_email(
            Source=FROM_EMAIL,
            Destination={"ToAddresses": [owner]},
            Message={
                "Subject": {
                    "Data": f"🎉 Your link hit {milestone:,} clicks!",
                },
                "Body": {
                    "Text": {
                        "Data": (
                            f"Congratulations!\n\n"
                            f"Your short link {short_url} just reached {milestone:,} clicks.\n"
                            f"Original URL: {long_url}\n\n"
                            f"Keep sharing!"
                        )
                    }
                },
            },
        )

        print(f"Milestone email sent to {owner} for /{code} @ {milestone} clicks")
