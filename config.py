"""
    All the configuration set up for our chat app
"""
import os

# --- Constants ---
MAX_FREE_MESSAGES = 50
MAX_FREE_FILE_SIZE_MB = 2
MAX_FREE_FILE_SIZE_BYTES = MAX_FREE_FILE_SIZE_MB * 1024 * 1024
SECRET_KEY=os.environ.get("SECRET_KEY")

# --- AWS Service Names ---
DYNAMODB_USERS_TABLE = os.environ.get("DYNAMODB_USERS_TABLE", "WebSocketUsers")
DYNAMODB_SESSIONS_TABLE = os.environ.get("DYNAMODB_SESSIONS_TABLE", "ChatSessions")
DYNAMODB_MESSAGES_TABLE = os.environ.get("DYNAMODB_MESSAGES_TABLE", "ChatMessages")
DYNAMODB_FILES_TABLE = os.environ.get("DYNAMODB_FILES_TABLE", "UserFiles")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "your-websocket-files-bucket")

# --- Cognito Config ---
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID")
COGNITO_REGION = os.environ.get("COGNITO_REGION")
COGNITO_APP_CLIENT_ID = os.environ.get("COGNITO_APP_CLIENT_ID")
COGNITO_APP_CLIENT_SECRET = os.environ.get("COGNITO_APP_CLIENT_SECRET") # Optional if you didn't set one
# The full domain prefix, e.g., "https://my-app.auth.us-east-1.amazoncognito.com"
COGNITO_DOMAIN = os.environ.get("COGNITO_DOMAIN") 
COGNITO_REDIRECT_URI = os.environ.get("COGNITO_REDIRECT_URI", "http://localhost:8000/chat")

if not all([COGNITO_USER_POOL_ID, COGNITO_REGION, COGNITO_APP_CLIENT_ID]):
    print("Warning: Cognito environment variables are not fully set. Auth will fail.")

COGNITO_JWKS_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
COGNITO_SIGNED_TOKEN = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"