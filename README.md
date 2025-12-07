# 🚀 Cloud-Native Real-Time Chat Application

A high-performance, scalable WebSocket chat application built with **FastAPI** (Python) and deployed on **AWS ECS Fargate**. This project demonstrates a modern "Cloud-Native" architecture, utilizing serverless databases, managed authentication, and container orchestration to handle real-time communication at scale.

## 📋 Table of Contents
- [Overview](#-overview)
- [Key Features](#-key-features)
- [Architecture & Tech Stack](#-architecture--tech-stack)
  - [Why FastAPI?](#why-fastapi)
  - [Real-Time State Management (Redis)](#real-time-state-management-redis)
  - [Database Schema (DynamoDB)](#database-schema-dynamodb)
- [Cloud Infrastructure (AWS)](#-cloud-infrastructure-aws)
  - [Deployment (ECS + ALB)](#deployment-ecs--alb)
  - [CI/CD Pipeline](#cicd-pipeline)
  - [Security & IAM](#security--iam)
- [Configuration](#-configuration)
- [Installation & Local Dev](#-installation--local-dev)

---

## 📖 Overview
This application allows users to chat in real-time, share files, and manage their sessions securely. Unlike traditional monolithic chat apps, this solution is designed to be **stateless and horizontally scalable**. By decoupling the WebSocket connections from the application state (using Redis) and the data storage (using DynamoDB), the system can auto-scale containers based on load without dropping active connections.

## ✨ Key Features
* **Real-Time Messaging**: Instant text delivery via WebSockets.
* **File Sharing**: Secure upload/download of images and files (<2MB) using **Amazon S3 Presigned URLs**.
* **Premium Membership**: Tiered system (Free vs. Premium) enforcing message limits and storage quotas.
* **Secure Authentication**: OAuth2 flow integrated with **Amazon Cognito** (User Pools).
* **Persistent History**: Chat history stored reliably in **DynamoDB**.
* **Multi-Container Sync**: Users connected to different containers can chat seamlessly via **Redis Pub/Sub**.

---

## 🏗 Architecture & Tech Stack

### Why FastAPI?
We chose **FastAPI** over Django or Flask for its native support for asynchronous programming (`async/await`). WebSockets require high-concurrency handling; FastAPI's underlying ASGI server (Uvicorn) handles thousands of concurrent connections efficiently with minimal overhead, making it the ideal choice for a Python-based real-time service.

### Real-Time State Management (Redis)
In an ECS Fargate environment, containers are ephemeral. A user connected to `Container A` cannot directly speak to a user on `Container B`.
* **Solution**: **Amazon ElastiCache (Redis)**.
* **Strategy**: We use a Pub/Sub mechanism. When a user sends a message, if the recipient is not on the *current* container, the message is published to a Redis channel. All containers listen to this channel and route the message to the correct connected client.

### Database Schema (DynamoDB)
We use **Amazon DynamoDB** for its single-digit millisecond latency and serverless scaling. The data model uses **4 Tables** designed for access patterns:

#### 1. `WebSocketUsers` (Profiles & Limits)
* **PK**: `user_id` (String)
* **Purpose**: Stores user configurations, premium status, and throttling counters.
* **Key Attributes**:
    * `active_chat_ids`: List of chat IDs the user belongs to (e.g., `["userA_userB", "userA_userC"]`).
    * `message_count`: Integer for rate limiting free users.
    * `is_premium`: Boolean flag.

#### 2. `ChatSessions` (Inbox Metadata)
* **PK**: `chat_id` (String, format: `UserA::CHAT::UserB`)
* **Purpose**: Represents a "Room" and serves the inbox preview.
* **Key Attributes**:
    * `last_message_content`: Preview text (e.g., "See you there!").
    * `last_message_timestamp`: ISO 8601 timestamp for sorting.
    * `user_ids`: List of participants.

#### 3. `ChatMessages` (History)
* **PK**: `chat_id`
* **SK**: `timestamp` (ISO 8601)
* **Purpose**: Stores individual messages.
* **Key Attributes**:
    * `message_type`: "text" or "file".
    * `content`: The message text or the S3 Key (for files).

#### 4. `UserSessions` (Auth State)
* **PK**: `session_id` (UUID from secure cookie)
* **Purpose**: Manages authenticated sessions without massive JWTs in headers.
* **Key Attributes**:
    * `access_token`: The actual Cognito JWT.
    * `ttl`: **Time-To-Live** timestamp. DynamoDB automatically deletes expired sessions, logging the user out.

---

## ☁ Cloud Infrastructure (AWS)

### Deployment (ECS + ALB)
The application runs on **Amazon ECS (Elastic Container Service)** with the **Fargate** launch type (serverless containers).
* **Load Balancer**: An **Application Load Balancer (ALB)** sits in front of the ECS tasks. It terminates HTTPS (SSL) and forwards traffic to the containers.
* **Sticky Sessions**: Not required due to the Redis architecture, making scaling seamless.
* **Domain & SSL**:
    * Domain managed via **Route 53**.
    * SSL Certificate provided by **AWS Certificate Manager (ACM)** attached to the ALB listener (Port 443).

### CI/CD Pipeline
We use **AWS CodeBuild** to automate the build process.
* **Source**: GitHub Repository.
* **Build**: Docker image creation via `buildspec.yml`.
* **Artifact**: Image pushed to **Amazon ECR** (Elastic Container Registry).

**Buildspec Overview:**
1.  **Pre-build**: Log in to Amazon ECR.
2.  **Build**: `docker build -t ...`
3.  **Post-build**: `docker push ...`

### Security & IAM
We strictly follow the principle of least privilege using two distinct IAM roles:

1.  **Task Execution Role** (The "Mover"):
    * Permissions: Pull images from ECR, write logs to CloudWatch, and read **SSM Parameter Store** (for env vars).
2.  **Task Role** (The "App"):
    * Permissions: `dynamodb:PutItem/GetItem` (scoped to specific tables), `s3:PutObject/GetObject` (scoped to bucket), and `cognito-idp` access.

**S3 Security**:
* Files are private by default.
* Users upload via **Presigned URLs** (generated by the backend).
* **CORS** is configured on the bucket to allow the browser to fetch images directly.

---

## 🔧 Configuration

The application is configured using **AWS Systems Manager (SSM) Parameter Store** to keep secrets out of the codebase.

**Required Environment Variables (Stored in SSM):**

| Variable | Description |
| :--- | :--- |
| `AWS_DEFAULT_REGION` | Your AWS Region (e.g., `us-east-1`) |
| `COGNITO_USER_POOL_ID` | User Pool ID from Cognito |
| `COGNITO_APP_CLIENT_ID` | App Client ID from Cognito |
| `COGNITO_APP_CLIENT_SECRET` | Client Secret for OAuth flow |
| `COGNITO_DOMAIN` | Custom domain prefix for Cognito Hosted UI |
| `S3_BUCKET_NAME` | Bucket for file uploads |
| `REDIS_CLUSTER_ENDPOINT` | ElastiCache endpoint (use `rediss://` for SSL) |
| `SECRET_KEY` | Key to encrypt session cookies |

---

## 💻 Installation & Local Dev

1.  **Clone the repo**
    ```bash
    git clone [https://github.com/yourusername/fastapi-chat.git](https://github.com/yourusername/fastapi-chat.git)
    cd fastapi-chat
    ```

2.  **Setup Virtual Environment**
    ```bash
    python -m venv venv
    source venv/bin/activate  # or venv\Scripts\activate on Windows
    pip install -r requirements.txt
    ```

3.  **Set Environment Variables**
    Create a `.env` file based on the table above.

4.  **Run with Docker (Recommended for Redis)**
    ```bash
    docker-compose up --build
    ```

5.  **Run Manually**
    Ensure you have a local Redis running or a tunnel to AWS.
    ```bash
    uvicorn main:app --reload
    ```
    Visit `http://localhost:8000` to start chatting!
