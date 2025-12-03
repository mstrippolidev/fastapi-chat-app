"""
    Keep the logic for the aws sdk (POR REVISAR)
"""
import uuid
# import aiobotocore
from aiobotocore.session import get_session
from botocore.exceptions import ClientError
import config
from typing import Dict, Any, List, Optional
# from auth import User
from datetime import datetime, timezone, timedelta

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
            TableName=config.DYNAMODB_WEBSOCKETS_USERS_TABLE,
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

async def save_user_session(session_id:str, access_token: str):
    """
        Save the session token to dynamoDB.
    """
    try:
        print('is aws file save_user_session')
        # make ttl for one hour forward
        now = datetime.now(timezone.utc)
        ttl = int((now + timedelta(hours=1)).timestamp())
        await dynamodb_client.put_item(
            TableName=config.DYNAMODB_USERS_COGNITO_SESSIONS_TABLE,
            Item={
                'session_id': {'S': str(session_id)},
                'access_token': {'S': str(access_token)},
                'ttl': {'N': str(ttl)}
            }
        )
    except Exception as e:
        raise Exception(str(e))

async def get_token_from_session(session_id: str) -> Optional[str]:
    """Retrieves the access_token given a session_id."""
    try:
        response = await dynamodb_client.get_item(
            TableName=config.DYNAMODB_USERS_COGNITO_SESSIONS_TABLE,
            Key={'session_id': {'S': str(session_id)}}
        )
        item = response.get('Item')
        if item:
            return item.get('access_token', {}).get('S')
        return None
    except Exception as e:
        print(f"Error retrieving session: {e}")
        return None
    
async def save_user_profile(user_info: dict):
    """
    Saves/Updates user in WebSocketUsers table on login.
    Ensures 'active_chat_ids' exists.
    """
    user_id = user_info.get('sub')
    username = user_info.get('email', user_info.get('username'))
    """
        User info from cognito looks like this:
         {'at_hash': 'OgwuUN9YJIHaAwS2bUoKWQ', 
         'sub': '64e8c488-90b1-706d-5bc1-6e3cadb2f5ea', 'email_verified': True, 
         'iss': 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_ImQugGgar', 
         'cognito:username': '64e8c488-90b1-706d-5bc1-6e3cadb2f5ea', 
         'nonce': 'Bm0TCAt3fJjFROIDCV2X', 'origin_jti': 'eeec3f29-adc0-4475-81e1-3bc577ee85ce', 
         'aud': '4sgv53ns7fabd3590hrfb4irk7', 'token_use': 'id', 'auth_time': 1764636423, 'exp': 1764640023, 'iat': 1764636423, 
         'jti': 'ee04fc84-23ce-4362-a2ca-321aeea29b38', 'email': 'test3web@mailinator.com'}
    """
    if not user_id:
        return

    try:
        # usage of 'SET active_chat_ids = if_not_exists(...)' ensures we don't wipe their chats on re-login
        await dynamodb_client.update_item(
            TableName=config.DYNAMODB_WEBSOCKETS_USERS_TABLE,
            Key={'user_id': {'S': user_id}},
            UpdateExpression="SET username = :u, active_chat_ids = if_not_exists(active_chat_ids, :empty), is_premium = if_not_exists(is_premium, :false), message_count = if_not_exists(message_count, :zero)",
            ExpressionAttributeValues={
                ':u': {'S': username},
                ':empty': {'L': []},
                ':false': {'BOOL': False},
                ':zero': {'N': '0'}
            }
        )
        print(f"User profile updated for {username}")
    except Exception as e:
        print(f"Error saving user profile: {e}")

async def get_user_active_chats(user_id: str) -> List[Dict]:
    """
    1. Reads 'active_chat_ids' from WebSocketUsers.
    2. Batch gets details from ChatSessions.
    """
    # A. Get List of IDs
    try:
        user_resp = await dynamodb_client.get_item(
            TableName=config.DYNAMODB_WEBSOCKETS_USERS_TABLE,
            Key={'user_id': {'S': user_id}}
        )
        if 'Item' not in user_resp:
            return []
            
        # Extract list of strings: ["id1", "id2"]
        chat_ids_dynamo = user_resp['Item'].get('active_chat_ids', {}).get('L', [])
        chat_ids = [c['S'] for c in chat_ids_dynamo]
        
        if not chat_ids:
            return []
        # B. Batch Get Details (Optimization)
        # We need to construct keys for BatchGetItem
        keys = [{'chat_id': {'S': cid}} for cid in chat_ids]
        # DynamoDB BatchGetItem (max 100 items)
        batch_resp = await dynamodb_client.batch_get_item(
            RequestItems={
                config.DYNAMODB_CHATS_TABLE: {
                    'Keys': keys,
                    'ProjectionExpression': 'chat_id, last_message_content, last_message_timestamp, user_ids'
                }
            }
        )
        
        items = batch_resp.get('Responses', {}).get(config.DYNAMODB_CHATS_TABLE, [])
        print('items batch', items)
        # Format for frontend
        results = []
        for item in items:
            chat_id = item['chat_id']['S']
            participants = str(chat_id).split('::CHAT::')
            results.append({
                "chat_id": item['chat_id']['S'],
                "last_message": item.get('last_message_content', {}).get('S', ''),
                "timestamp": item.get('last_message_timestamp', {}).get('S', ''),
                "participants": participants
            })
            
        # Sort by timestamp desc
        results.sort(key=lambda x: x['timestamp'], reverse=True)
        return results

    except Exception as e:
        print(f"Error fetching active chats: {e}")
        return []

async def get_chat_history(chat_id: str, limit: int = 20) -> List[Dict]:
    """
    Fetches the last N messages for a chat_id using Query (reversed).
    """
    try:
        response = await dynamodb_client.query(
            TableName=config.DYNAMODB_MESSAGES_TABLE,
            KeyConditionExpression="chat_id = :cid",
            ExpressionAttributeValues={
                ":cid": {"S": chat_id}
            },
            ScanIndexForward=False, # False = Descending order (Newest first)
            Limit=limit
        )
        
        items = response.get('Items', [])
        
        # Convert to cleaner JSON and Reverse back to [Oldest -> Newest] for UI
        history = []
        for item in items:
            history.append({
                "sender_id": item['sender_id']['S'],
                "content": item['content']['S'],
                "type": item.get('message_type', {}).get('S', 'text'),
                "timestamp": item['timestamp']['S'],
                "username": item.get('username', {}).get('S', 'Unknown')
            })
            
        return history[::-1] # Reverse list to show chronologically

    except Exception as e:
        print(f"Error fetching history: {e}")
        return []

async def check_user_exists(user_id: str) -> bool:
    """Checks if a user exists in the WebSocketUsers table."""
    try:
        response = await dynamodb_client.get_item(
            TableName=config.DYNAMODB_WEBSOCKETS_USERS_TABLE,
            Key={'user_id': {'S': user_id}}
        )
        exists = 'Item' in response
        user = None
        if exists:
            user = response.get('Item')
        return (exists, user)
    except Exception as e:
        print(f"Error checking user existence: {e}")
        return False
    
async def add_chat_to_user_list(user_id: str, chat_id: str):
    """
    Adds a chat_id to the user's active_chat_ids list.
    Uses 'if_not_exists' to handle new users and 'NOT contains' to avoid duplicates.
    """
    try:
        await dynamodb_client.update_item(
            TableName=config.DYNAMODB_WEBSOCKETS_USERS_TABLE,
            Key={'user_id': {'S': user_id}},
            UpdateExpression="SET active_chat_ids = list_append(if_not_exists(active_chat_ids, :empty), :new_chat)",
            ConditionExpression="NOT contains(active_chat_ids, :chat_id_str)",
            ExpressionAttributeValues={
                ':new_chat': {'L': [{'S': chat_id}]},
                ':empty': {'L': []},
                ':chat_id_str': {'S': chat_id}
            }
        )
        print(f"Added chat {chat_id} to user {user_id}")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            # This is fine, it means the chat was already in the list
            pass
        else:
            print(f"Error adding chat to user list: {e}")

async def create_new_chat_session(current_user_id: str, target_user_id: str, current_username:str, target_username:str) -> str:
    """
    1. Creates the ChatSession item (empty last message).
    2. Updates both users to include this chat_id.
    """
    # 1. Build Chat ID (Sorted)
    users = sorted([current_username, target_username])
    chat_id = f"{users[0]}::CHAT::{users[1]}"
    try:
        # 2. Add to ChatSessions Table
        # Note: We leave last_message_content/timestamp empty as requested.
        await dynamodb_client.put_item(
            TableName=config.DYNAMODB_CHATS_TABLE,
            Item={
                'chat_id': {'S': chat_id},
                'last_message_content': {'S': ''},
                'last_message_timestamp': {'S': ''},
                'user_ids': {'L': [{'S': current_user_id}, {'S': target_user_id}]},
                # We add 'updated_at' so it doesn't break sorting if your UI relies on it, 
                # but set it to now.
                'updated_at': {'S': datetime.now(timezone.utc).isoformat()} 
            }
        )
        
        # 3. Add to Current User's List
        await add_chat_to_user_list(current_user_id, chat_id)
        
        # 4. Add to Target User's List
        await add_chat_to_user_list(target_user_id, chat_id)
        
        return chat_id
        
    except Exception as e:
        print(f"Error creating chat session: {e}")
        return None