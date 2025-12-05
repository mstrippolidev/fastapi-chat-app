"""
    Handle the conection for the websocket connections and their functions.
    Remember that this class will have a memory of who is online right know 
"""
import json
from typing import Dict
import asyncio
from fastapi import WebSocket
import redis.asyncio as redis
from redis.asyncio.cluster import RedisCluster
import config

class ConnectionManager:
    """
    Manages active WebSocket connections.
    This will tell us what user an online connected to our server.
    """
    def __init__(self):
        # Stores a single WebSocket per connected user_id
        self.active_connections: Dict[str, WebSocket] = {}
        # Detect if it's a Redis Cluster or a single node
        if "clustercfg" in config.REDIS_CLUSTER_ENDPOINT:
            # RedisCluster handles topology and slot mapping automatically
            self.redis = RedisCluster.from_url(config.REDIS_CLUSTER_ENDPOINT, decode_responses=True,
                                               socket_timeout=5)
            self.pubsub_client = redis.from_url(config.REDIS_CLUSTER_ENDPOINT, decode_responses=True)
        else:
            # Standard Redis client for local dev or non-cluster mode
            self.redis = redis.from_url(config.REDIS_CLUSTER_ENDPOINT, decode_responses=True)
            self.pubsub_client = self.redis
        # Create the pubsub object from redis client
        self.pubsub = self.pubsub_client.pubsub()

    async def check_redis_connection(self):
        """
        Pings Redis to verify the connection is alive.
        """
        try:
            print(f"Connecting to Redis at: {config.REDIS_CLUSTER_ENDPOINT} ...")
            await self.redis.ping()
            print("✅ Redis connection successful!")
            return True
        except Exception as e:
            print(f"❌ Redis connection failed: {e}")
            return False

    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept and register a new WebSocket connection for a user."""
        await websocket.accept()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str):
        """Remove a WebSocket connection for a user."""
        if user_id in self.active_connections:
            del self.active_connections[user_id]

    async def subscribe_to_channel(self):
        """
        Background task: Begin listening to Redis pub/sub channel for broadcast messages.
        """
        await self.pubsub.subscribe("chat_broadcast")
        async for message in self.pubsub.listen():
            if message["type"] == "message":
                await self.handle_redis_message(message["data"])

    async def handle_redis_message(self, raw_data: str):
        """
        Executed this function when received a message from Redis pub/sub.
        """
        try:
            data = json.loads(raw_data)
            target_user_id = data["target_user_id"]
            message_content = data["message"]
            
            # If the user is connected HERE, send it.
            if target_user_id in self.active_connections:
                socket = self.active_connections[target_user_id]
                await socket.send_text(message_content)
                
        except Exception as e:
            print(f"Redis handler error: {e}")

    async def send_personal_message(self, message: str, user_id: str) -> bool:
        """
        Send a message to a single user_id.
        Returns True if sent, False if user is not connected.
        """
        websocket = self.active_connections.get(user_id)
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
            # User not connected here, publish to Redis for other instances
            payload = json.dumps({
                'target_user_id': user_id,
                'message': message
            })
            await self.redis.publish("chat_broadcast", payload)
        return True

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