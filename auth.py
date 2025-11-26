"""
    Handle logic for authentication and authorization
"""
import json
import httpx
from typing import Optional, Dict, Any
from fastapi import Depends, Query, WebSocketDisconnect, status, Request, WebSocket
from jose import jwt, jws, exceptions as jose_exceptions

import config

# --- User Class ---

class User:
    """Class to hold user data validated from Cognito JWT."""
    def __init__(self, user_id: str, username: str, is_premium: bool = False):
        self.user_id = user_id
        self.username = username
        self.is_premium = is_premium # This will be updated from DynamoDB


# This cache will hold the Cognito JWKS (JSON Web Key Set)
jwks_cache = {}

async def fetch_jwks() -> Dict[str, Any]:
    """
    Fetches and caches the JWKS from Cognito.
    In a real app, you'd add caching with a timeout (e.g., 1 hour).
    """
    global jwks_cache
    if not jwks_cache:
        # Check if URL is configured. If not, auth is impossible.
        if not config.COGNITO_JWKS_URL or "None" in config.COGNITO_JWKS_URL:
            print("Error: COGNITO_JWKS_URL is not configured in config.py. Cannot fetch keys.")
            return {}
            
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(config.COGNITO_JWKS_URL)
                response.raise_for_status() # Raise exception for 4xx/5xx
                jwks_cache = response.json()
                print("Successfully fetched and cached JWKS.")
            except httpx.RequestError as e:
                print(f"Error fetching JWKS: {e}")
                return {}
    return jwks_cache

async def validate_cognito_token(token: str) -> Optional[User]:
    """
    Validates a Cognito JWT.
    
    This function:
    1. Fetches the Cognito JWKS (public keys).
    2. Decodes the JWT and validates its signature, expiration, and issuer.
    3. Extracts claims and returns a User object.
    """
    
    if not token:
        return None
        
    try:
        # Get the JWKS
        jwks = await fetch_jwks()
        if not jwks:
            print("Unable to fetch JWKS, cannot validate token.")
            return None

        # Get the 'kid' (Key ID) from the token's header
        try:
            header = jws.get_unverified_header(token)
            kid = header.get('kid')
        except Exception:
             print("Invalid token header.")
             return None
        
        # Find the matching key in the JWKS
        key_data = next((key for key in jwks.get('keys', []) if key.get('kid') == kid), None)
        if not key_data:
            print(f"No matching key found for kid: {kid}")
            return None

        # Decode and validate the token
        payload = jwt.decode(
            token,
            key_data,
            algorithms=['RS256'],
            audience=config.COGNITO_APP_CLIENT_ID, # Validate audience (Client ID)
            issuer=f"https://cognito-idp.{config.COGNITO_REGION}.amazonaws.com/{config.COGNITO_USER_POOL_ID}" # Validate issuer
        )
        
        # Extract claims and create User
        user_id = payload.get('sub')
        # Different Cognito setups use different claims for username
        username = payload.get('cognito:username', payload.get('username'))
        
        # This custom attribute must be in your Cognito token
        is_premium_claim = payload.get('custom:is_premium', 'false')
        
        if not user_id or not username:
             print("Token missing 'sub' or 'username' claim.")
             return None

        return User(
            user_id=user_id,
            username=username,
            is_premium=(is_premium_claim == 'true' or is_premium_claim is True)
        )
        
    except jose_exceptions.ExpiredSignatureError:
        print("Token has expired.")
        return None
    except jose_exceptions.JWTClaimsError as e:
        print(f"Token claims validation failed: {e}")
        return None
    except Exception as e:
        print(f"Error validating token: {e}")
        return None

# --- Dependency Injection ---

async def get_current_user(
    websocket: WebSocket,
    token: Optional[str] = Query(None)
) -> User:
    """
    Dependency to validate token.
    Priority:
    1. Query Param ?token=... (Testing/Legacy)
    2. Secure Cookie 'access_token' (Production/Standard)
    """
    
    # 1. Try Query Param
    token_to_validate = token
    print("Is there a token in query params?", token)
    # 2. Try Cookie if no query param
    if not token_to_validate:
        token_to_validate = websocket.cookies.get("access_token")
        print('is there a token in cookies?', token_to_validate)
    
    if not token_to_validate:
        print("Auth failed: No token found in Query or Cookies.")
        raise WebSocketDisconnect(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing auth token in query or cookie"
        )
        
    user = await validate_cognito_token(token_to_validate)
    if user is None:
        print("Auth failed: Token validation returned None.")
        raise WebSocketDisconnect(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid authentication token"
        )
    return user