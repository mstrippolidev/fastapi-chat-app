"""
    Handle the conection for the websocket connections and their functions.
    Remember that this class will have a memory of who is online right know 
"""
import json
from typing import Dict
from fastapi import WebSocket
from auth import User

class ConnectionManager:
    """
    Manages active WebSocket connections.
    This will tell us what user an online connected to our server.
    """
    def __init__(self):
        # Stores a single WebSocket per connected user_id
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept and register a new WebSocket connection for a user."""
        await websocket.accept()
        self.active_connections[user_id] = websocket
        print(f"User connected: {user_id}")

    def disconnect(self, user_id: str):
        """Remove a WebSocket connection for a user."""
        if user_id in self.active_connections:
            del self.active_connections[user_id]
            print(f"User disconnected: {user_id}")

    async def send_personal_message(self, message: str, user_id: str) -> bool:
        """
        Send a message to a single user_id.
        Returns True if sent, False if user is not connected.
        """
        websocket = self.active_connections.get(user_id)
        print("ENviando mensage a ", user_id)
        if websocket:
            try:
                await websocket.send_text(message)
                return True
            except Exception as e:
                print(f"Error sending to {user_id}: {e}")
                # The connection might be broken, clear it
                self.disconnect(user_id)
                return False
        else:
            # User is not online
            return False

    async def broadcast(self, message: str):
        """(Optional) Send a message to ALL connected users."""
        # We create a copy of the values for safe iteration
        for user_id, websocket in list(self.active_connections.items()):
            try:
                await websocket.send_text(message)
            except Exception as e:
                print(f"Error broadcasting to {user_id}: {e}")
                # Pass the user_id, not the websocket object
                self.disconnect(user_id)