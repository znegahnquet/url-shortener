# URL Shortener — Final Project
**CSC 352: Platform Development**

---

## AWS Services Used (6 of 13)

| Service | Lecture | How It's Used |
|---|---|---|
| **S3** | Lecture 12 | Hosts the static frontend (HTML/JS) as a website bucket |
| **Lambda** | Lecture 16 | Four serverless functions handle all business logic |
| **API Gateway** | Lecture 16 | Routes HTTP requests to the correct Lambda, enforces auth |
| **Cognito** | Lecture 17 | User sign-up/sign-in, issues JWT tokens, API GW authorizer |
| **DynamoDB** | Lectures 9–10 | Stores short link metadata; GSI for per-user queries |
| **SNS** | Lecture 20 | Pub/sub fan-out: publishes link events; Lambda subscriber sends milestone emails |

---

## Architecture Overview

```
Browser (S3-hosted SPA)
    │
    ├─── Cognito Hosted UI ──► User Pool (sign-in/sign-up/JWT)
    │
    └─── API Gateway (REST)  ──► Cognito Authorizer (validates JWT)
              │
              ├── POST   /links        ──► Lambda: shorten.py
              │                               │─► DynamoDB PutItem
              │                               └─► SNS Publish (link_created)
              │
              ├── GET    /links        ──► Lambda: list_links.py
              │                               └─► DynamoDB Query (owner-index GSI)
              │
              ├── DELETE /links/{code} ──► Lambda: delete_link.py
              │                               └─► DynamoDB DeleteItem
              │
              └── GET    /r/{code}     ──► Lambda: redirect.py  (public, no auth)
                                              │─► DynamoDB UpdateItem (+1 click)
                                              │─► SNS Publish (link_clicked / milestone)
                                              └─► HTTP 301 → long URL

SNS Topic ──► Lambda: milestone_notifier.py ──► SES email on 10/100/1000 clicks
```

---

## File Structure

```
url-shortener/
├── backend/
│   └── lambdas/
│       ├── shorten.py             # POST /links — create short URL
│       ├── redirect.py            # GET /r/{code} — redirect + track click
│       ├── list_links.py          # GET /links — list user's links
│       ├── delete_link.py         # DELETE /links/{code} — remove a link
│       └── milestone_notifier.py  # SNS subscriber — sends milestone emails
├── frontend/
│   ├── index.html                 # Single-page app (deployed to S3)
│   └── config.js                  # Fill in your AWS resource IDs here
├── infra/
│   └── setup.py                   # One-time AWS provisioning script
└── README.md
```

---

## Deployment Steps

### Prerequisites
- AWS account with CLI configured (`aws configure`)
- Python 3.9+ with `boto3` installed (`pip install boto3`)
- A verified email address in SES (for milestone notifications)

### Step 1 — Set environment variables
```bash
export AWS_ACCESS_KEY_ID=your-key
export AWS_SECRET_ACCESS_KEY=your-secret
export AWS_DEFAULT_REGION=us-east-1
export NOTIFICATION_EMAIL=you@example.com
```

### Step 2 — Run the infrastructure setup script
```bash
cd infra/
python setup.py
```

This script provisions (in order):
1. **S3 bucket** with static website hosting enabled (Lecture 12)
2. **DynamoDB table** `url-shortener-links` with a GSI on `owner` (Lectures 9–10)
3. **IAM role** for Lambda execution with DynamoDB + SNS permissions
4. **SNS topic** `url-shortener-events` (Lecture 20)
5. **Cognito User Pool** with email sign-in + JWT token config (Lecture 17)
6. **Four Lambda functions** (Lecture 16)
7. **API Gateway REST API** with Cognito Authorizer + all routes (Lecture 16–17)
8. **SNS → Lambda subscription** for milestone notifications (Lecture 20)

### Step 3 — Configure the frontend
Open `frontend/config.js` and fill in the values printed by `setup.py`:
```js
const CONFIG = {
  API_BASE_URL:          "https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod",
  COGNITO_REGION:        "us-east-1",
  COGNITO_USER_POOL_ID:  "us-east-1_XXXXXXXXX",
  COGNITO_CLIENT_ID:     "XXXXXXXXXXXXXXXXXXXXXXXXXX",
  COGNITO_DOMAIN:        "your-app.auth.us-east-1.amazoncognito.com",
  REDIRECT_URI:          "http://YOUR_BUCKET.s3-website-us-east-1.amazonaws.com",
};
```

In the Cognito console, set up a Hosted UI domain:
- Go to **Cognito → User Pools → your pool → App Integration → Domain**
- Create a Cognito domain prefix (e.g., `snip-yourname`)

### Step 4 — Deploy frontend to S3
```bash
aws s3 cp frontend/index.html s3://YOUR_BUCKET_NAME/index.html
aws s3 cp frontend/config.js  s3://YOUR_BUCKET_NAME/config.js
```

### Step 5 — Verify SES email
Before milestone emails will send, verify your email in SES:
```bash
aws ses verify-email-identity --email-address you@example.com
```
Then click the verification link in the email.

---

## Key Design Decisions (mapped to lectures)

### Why Lambda + API Gateway instead of EC2 + Flask?
Lecture 16 explains that serverless functions scale automatically, cost nothing when idle, and have high availability built in. Since this is an API with bursty traffic (links can go viral), Lambda scales to zero in quiet periods and to thousands of concurrent executions during spikes — without any infrastructure management.

### Why DynamoDB instead of RDS?
Lecture 9 explains NoSQL databases are designed for horizontal scalability and high availability. Short URL lookups (`GET /r/{code}`) are single-key reads that map perfectly to DynamoDB's partition key model (Lecture 10). The `click_count` atomic increment via `UpdateItem` avoids any race condition without needing ACID transactions.

### Why Cognito instead of rolling our own auth?
Lecture 17 explains that Cognito handles password hashing, brute-force protection, JWT signing, and token refresh automatically. Lecture 13 shows how complex bcrypt/JWT must be implemented carefully — Cognito moves all of that risk away from our code.

### Why SNS for milestone notifications?
Lecture 20 explains the pub/sub pattern: the redirect Lambda (producer) publishes a `link_clicked` event without knowing or caring who receives it. The milestone notifier Lambda (subscriber) reacts independently. This decoupling means we can add new subscribers (e.g., analytics, Slack bot) without changing any existing code.

### Why S3 for the frontend?
Lecture 12 explains that S3 provides virtually unlimited scalability, extremely high durability (11 nines), and can serve static websites directly. No EC2 instance is needed to serve HTML/CSS/JS files.
