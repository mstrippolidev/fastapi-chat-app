"""
    Keep the logic for the aws sdk (POR REVISAR)
"""
import uuid
import aiobotocore
from aiobotocore.session import get_session
import config
from typing import Dict, Any
from auth import User
from datetime import datetime, timezone

# --- AWS Client Setup ---
# Use a single session for all AWS clients
aws_session = get_session()

# This context manager will be used by FastAPI's lifespan events
# to create and close the client sessions properly.
aws_client_context = aws_session.create_client

# These will be initialized in main.py's lifespan event
dynamodb_client = None 
s3_client = None

# --- Business Logic Helpers ---

async def get_user_details_from_dynamo(user_id: str) -> Dict[str, Any]:
    """
    Fetches user's current message count and premium status from DynamoDB.
    This is the "source of truth".
    """
    try:
        response = await dynamodb_client.get_item(
            TableName=config.DYNAMODB_USERS_TABLE,
            Key={'user_id': {'S': user_id}}
        )
        item = response.get('Item')
        if item:
            return {
                "user_id": item.get('user_id', {}).get('S'),
                "is_premium": item.get('is_premium', {}).get('BOOL', False),
                "message_count": int(item.get('message_count', {}).get('N', '0'))
            }
        else:
            # User not in DB yet, create them (or assume defaults)
            print(f"User {user_id} not found in DynamoDB. Creating defaults.")
            return {"user_id": user_id, "is_premium": False, "message_count": 0}
    except Exception as e:
        print(f"Error getting user from DynamoDB: {e}")
        # In case of DB error, deny service to be safe
        return {"user_id": user_id, "is_premium": False, "message_count": 99999}

async def increment_user_message_count(user_id: str):
    """Increments message count in DynamoDB for a non-premium user."""
    try:
        await dynamodb_client.update_item(
            TableName=config.DYNAMODB_USERS_TABLE,
            Key={'user_id': {'S': user_id}},
            UpdateExpression="SET message_count = if_not_exists(message_count, :zero) + :val",
            ExpressionAttributeValues={":val": {"N": "1"}, ":zero": {"N": "0"}},
        )
        print(f"Incremented message count for {user_id}")
    except Exception as e:
        print(f"Error incrementing message count: {e}")

async def create_s3_presigned_url(user_id: str, filename: str) -> Dict[str, str]:
    """Generates a pre-signed URL for S3 PUT operation."""
    object_key = f"uploads/{user_id}/{uuid.uuid4()}-{filename}"
    
    try:
        url = await s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': config.S3_BUCKET_NAME, 'Key': object_key},
            ExpiresIn=3600  # URL valid for 1 hour
        )
        return {"url": url, "s3_key": object_key}
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        return {}

async def save_message_to_dynamo(chat_id: str, sender_id: str, username: str, content: str, msg_type: str = "text"):
    """Saves the chat message to DynamoDB for persistence."""
    print(f"Saving message to DynamoDB for chat {chat_id}...")
    
    timestamp = datetime.now(timezone.utc).isoformat()

    try:
        # 1. Save the full message to the ChatMessages table
        await dynamodb_client.put_item(
            TableName=config.DYNAMODB_MESSAGES_TABLE,
            Item={
                'chat_id': {'S': chat_id},
                'timestamp': {'S': timestamp},
                'sender_id': {'S': sender_id},
                'username': {'S': username},
                'content': {'S': content},
                'message_type': {'S': msg_type}
            }
        )
        
        # 2. Update the "last message" preview in the ChatSessions table
        await update_chat_session_last_message(chat_id, timestamp, content, msg_type)

    except Exception as e:
        print(f"Error saving message to DynamoDB: {e}")

async def update_chat_session_last_message(chat_id: str, timestamp: str, content: str, msg_type: str):
    """
    Updates the ChatSessions table with the latest message preview.
    This is what a user would see in their "chat list" screen.
    """
    
    # Create a preview of the content
    if msg_type == "file":
        preview = "File"
    else:
        preview = content[:50] # Truncate to 50 chars for preview

    try:
        # This uses UpdateItem with 'Upsert' logic:
        # It creates the session item if it doesn't exist,
        # or updates it if it does.
        await dynamodb_client.update_item(
            TableName=config.DYNAMODB_SESSIONS_TABLE,
            Key={'chat_id': {'S': chat_id}},
            UpdateExpression="SET last_message_timestamp = :ts, last_message_content = :prev",
            ExpressionAttributeValues={
                ":ts": {"S": timestamp},
                ":prev": {"S": preview}
            }
        )
    except Exception as e:
        print(f"Error updating chat session {chat_id}: {e}")