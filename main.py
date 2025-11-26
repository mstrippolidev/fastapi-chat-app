"""
    Wrap up all the logic for the websocket connection with file handlers and premium users
"""
from dotenv import load_dotenv 
import uvicorn, uuid
import aiobotocore
import json, requests
# import httpx # NEW: Needed for token exchange
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse,JSONResponse
from contextlib import asynccontextmanager
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

# Import from our new files
import config
from auth import User, get_current_user
from connection_manager import ConnectionManager
import aws_services as aws
from handler_messages import FactoryHandler

# --- App Lifespan (for managing AWS clients) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup and shutdown events.
    This is the place to create and close the real AWS clients.
    """
    print("Application startup...")
    
    # This creates the AWS clients when the app starts
    if not config.COGNITO_REGION:
        print("Fatal: COGNITO_REGION not set. AWS clients cannot be initialized.")
        # In a real app, you might want to raise an exception
    else:
        aws.dynamodb_client = await aws.aws_client_context('dynamodb', region_name=config.COGNITO_REGION).__aenter__()
        aws.s3_client = await aws.aws_client_context('s3', region_name=config.COGNITO_REGION, 
                                                    config=aiobotocore.config.AioConfig(signature_version='s3v4')).__aenter__()
        print("Real AWS clients initialized.")
    
    yield # The application runs here
    
    print("Application shutdown...")
    # This properly closes the clients when the app shuts down
    if aws.dynamodb_client:
        await aws.dynamodb_client.__aexit__(None, None, None)
    if aws.s3_client:
        await aws.s3_client.__aexit__(None, None, None)
    print("AWS clients closed.")

# Initialize FastAPI app with the lifespan manager
app = FastAPI(title="WebSocket API with Cognito & AWS", lifespan = lifespan)

app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY)

oauth = OAuth()
oauth.register(
    name='cognito',
    # Replace these with your actual AWS Cognito details
    client_id=config.COGNITO_APP_CLIENT_ID,
    authority=f'https://cognito-idp.{config.COGNITO_REGION}.amazonaws.com/{config.COGNITO_USER_POOL_ID}',
    client_secret=config.COGNITO_APP_CLIENT_SECRET,
    server_metadata_url=f'https://cognito-idp.{config.COGNITO_REGION}.amazonaws.com/{config.COGNITO_USER_POOL_ID}/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'email openid phone'
    }
)

# Create a single instance of the ConnectionManager
manager = ConnectionManager()


@app.get("/login")
async def login(request: Request):
    """
    Initiates the OAuth flow.
    Authlib generates a 'state' string and saves it in the SessionMiddleware cookie.
    """
    # Redirect to cognito user pool login page.
    redirect_uri = request.url_for('authorize')
    print('login')
    return await oauth.cognito.authorize_redirect(request, redirect_uri)

@app.route('/authorize')
async def authorize(request: Request):
    """
        Dedicated callback route. 
        1. Receives 'code' and 'state' from Cognito.
        2. Exchanges 'code' for tokens.
        3. Sets the user session.
        4. Set the session_id and save the session to dynamoDB or send the access_token
            as secure cookie
        5. Redirects to /chat.
    """
    try:
        # This function automatically checks request.query_params['state'] 
        # vs request.session['state']. If cookies were lost, this fails.
        token = await oauth.cognito.authorize_access_token(request)
        user = token.get('userinfo')
        access_token = token.get('access_token')
        if user:
            request.session['user'] = user
        # 3. Create the redirect response and save access token an secure cookie
        session_id = uuid.uuid4()
        response = RedirectResponse(url='/chat')
        # Optional: If you want to send your access_token through secureCookie
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,             # Prevents JavaScript from reading it (security)
            max_age=3600,              # Expires in 1 hour (matches Cognito default)
            samesite="Lax",
            secure=False               # Set to True if using HTTPS/Production
        )
        return response
    except Exception as e:
        # Common error: mismatching_state
        print(f"Auth Error: {e}")
        return HTMLResponse(f"<h1>Login Failed</h1><p>{e}</p><a href='/login'>Try Again</a>")

@app.get("/chat", response_class=HTMLResponse)
async def get_chat_interface(request: Request, response: Response, code: str = None):
    """
    Serves the chat interface ONLY.
    No auth logic here, just session checking.
    """
    user = request.session.get('user')
    
    if not user:
        # If not logged in, force them back to login
        return RedirectResponse(url='/login')

    print(f"Serving chat to: {user.get('email')}")
    
    # Render the HTML
    
    return HTMLResponse(content=read_client_html())

def read_client_html():
    try:
        with open("client.html", "r") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Error: client.html not found on server.</h1>"

# --- WebSocket Endpoint ---
@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    user: User = Depends(get_current_user)
):
    """
    Main WebSocket endpoint. A user connects here
    Token is passed as a query parameter: ?token=...
    """
    print("inside websocket endpoint")
    # Get the user's latest details from our database
    # The Cognito token says *who* they are, DynamoDB says *what* their status is.
    db_user_details = await aws.get_user_details_from_dynamo(user.user_id)
    print(db_user_details)
    # Update user object with DB-level premium status (the source of truth)
    user.is_premium = db_user_details.get("is_premium", False)
    current_message_count = db_user_details.get("message_count", 0)
    
    # Register the user's single connection
    await manager.connect(websocket, user.user_id)
    print('conectando...')
    try:
        while True:
            # Wait for a message from the client
            data_str = await websocket.receive_text()
            data = json.loads(data_str)
            
            msg_type = data.get("type")
            
            # --- Check Permissions ---
            is_allowed_to_send = False
            if user.is_premium:
                is_allowed_to_send = True
            elif current_message_count < config.MAX_FREE_MESSAGES:
                is_allowed_to_send = True
            
            if not is_allowed_to_send:
                err_msg = {"type": "error", "content": "You have reached your free message limit."}
                # Note: send_personal_message now just takes a user_id
                await manager.send_personal_message(json.dumps(err_msg), user.user_id)
                continue # Skip processing this message
            
            # Handle the different kind of messages
            valid, msg = await FactoryHandler.get_instance_messages_type_handler(msg_type).handle_message(
                manager, user, data, aws = aws
            )
            if not valid:
                print(msg)
                continue

    except WebSocketDisconnect:
        print(f"User {user.username} disconnected.")
        manager.disconnect(user.user_id)
    except json.JSONDecodeError:
        print(f"User {user.username} sent invalid JSON.")
        # Don't disconnect, just ignore
    except Exception as e:
        print(f"An error occurred with {user.username}: {e}")
        manager.disconnect(user.user_id)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    
    # 2. Create a redirect response
    response = RedirectResponse(url='/login')
    
    # 3. Delete the access_token cookie
    response.delete_cookie("access_token")
    
    return response

# --- Health Check Endpoint ---
@app.get("/health")
async def health_check():
    """Health check endpoint for the ALB."""
    return {"status": "ok"}

@app.get("/api/me")
async def get_current_user(request: Request):
    """
    API endpoint for the frontend to get user details.
    """
    user = request.session.get('user')
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    return user

# --- Main execution ---
if __name__ == "__main__":
    # This is for local development only.
    # In ECS, you'll run: uvicorn main:app --host 0.0.0.0 --port 8000
    print("Starting local development server...")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)