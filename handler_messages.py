"""
    This file will handle all the different kind of messages.
"""
import json
from abc import ABCMeta, abstractmethod
from auth import User
from connection_manager import ConnectionManager
import config

class HandlerMessagesTypeI(metaclass = ABCMeta):
    """
        Interface for handler the differents messages types
    """
    @staticmethod
    @abstractmethod
    async def handle_message(connection_manager:ConnectionManager, user:User, message:str, *args, **kwargs):
        """
            Handler the message
        """

class HandlerMessageChat(HandlerMessagesTypeI):
    """
        Concrete class for a normal message chat
    """
    
    async def handle_message(connection_manager:ConnectionManager, user:User, data:dict, *args, **kwargs):
        """
            Send the message send by user to both users.
        """
        print("Dentro del handler chat", data)
        recipient_id = data.get("recipient_id")
        chat_id = data.get("chat_id")
        content = data.get("content", "")
        aws = kwargs.get('aws')
        if not chat_id or not content:
            err_msg = {"type": "error", "content": "Missing recipient_id or content."}
            await connection_manager.send_personal_message(json.dumps(err_msg), user.user_id)
            return (False, err_msg)

        # Create the consistent chat_id (sort IDs alphabetically)
        if aws is None:
            return (False, f"Cannot save data to aws. Missing aws parameter")
        # Save message to historial message
        await aws.save_message_to_dynamo(chat_id, user.user_id, user.username, content, "text")
        # Update the count for free users
        add_count = False
        if not user.is_premium:
            await aws.increment_user_message_count(user.user_id)
            add_count = True
            #current_message_count += 1 # Update local count
            
        # 3. Send message to recipient (if online) and to self (for UI sync)
        broadcast_msg = {
            "type": "chat", 
            "sender_id": user.user_id,
            "chat_id": chat_id, # So the client knows which chat to put this in
            "username": user.username, 
            "content": content
        }
        msg_json = json.dumps(broadcast_msg)
        # Send back to sender
        await connection_manager.send_personal_message(msg_json, user.user_id)
        # Send to recipient
        await connection_manager.send_personal_message(msg_json, recipient_id)
        return (True, '' if add_count is False else 'incremented')
        

class HandlerFileRequestUpload(HandlerMessagesTypeI):
    """
        Handler for file_request type of message (i dont know if this will be used).
    """
    async def handle_message(connection_manager:ConnectionManager, user:User, data:dict, *args, **kwargs):
        """
            The client send a request to our server to create a presigned url
            for the client upload the file directly to s3.
        """
        filename = data.get("filename")
        filesize = data.get("filesize")
        aws = kwargs.get('aws')
        if aws is None:
            return (False, f"Cannot save data to aws. Missing aws parameter")
        
        if not filename or not filesize:
            err_msg = {"type": "error", "content": "File request missing filename or filesize."}
            await connection_manager.send_personal_message(json.dumps(err_msg), user.user_id)
            return (False, err_msg)
        if not user.is_premium and filesize > config.MAX_FREE_FILE_SIZE_BYTES:
            err_msg = {"type": "error", "content": f"File size exceeds free limit of {config.MAX_FREE_FILE_SIZE_MB}MB."}
            await connection_manager.send_personal_message(json.dumps(err_msg), user.user_id)
            return (False, err_msg)

        url_data = await aws.create_s3_presigned_url(user.user_id, filename)
        if url_data:
            response = {
                "type": "file_upload_url",
                "filename": filename,
                "url": url_data.get("url"),
                "s3_key": url_data.get("s3_key")
            }
            await connection_manager.send_personal_message(json.dumps(response), user.user_id)
        else:
            err_msg = {"type": "error", "content": "Could not prepare file upload."}
            await connection_manager.send_personal_message(json.dumps(err_msg), user.user_id)
        return (True, '')

class HandlerShowFile(HandlerMessagesTypeI):
    """
        Concrete class to handle file upload to s3.
    """
    async def handle_message(connection_manager:ConnectionManager, user:User, data:dict, *args, **kwargs):
        """
            Show the file uploaded in our chat.
        """
        s3_key = data.get("s3_key")
        filename = data.get("filename")
        recipient_id = data.get("recipient_id")
        aws = kwargs.get('aws')
        if aws is None:
            return (False, f"Cannot save data to aws. Missing aws parameter")

        if not s3_key or not filename or not recipient_id:
            err_msg = {"type": "error", "content": "File confirmation missing s3_key, filename, or recipient_id."}
            await connection_manager.send_personal_message(json.dumps(err_msg), user.user_id)
            return (False, err_msg)
        
        # Create the consistent chat_id
        users = sorted([user.user_id, recipient_id])
        chat_id = f"{users[0]}_{users[1]}"

        if not user.is_premium:
            await aws.increment_user_message_count(user.user_id)
            current_message_count += 1
        
        await aws.save_message_to_dynamo(chat_id, user.user_id, user.username, s3_key, "file")
        
        broadcast_msg = {
            "type": "file",
            "sender_id": user.user_id,
            "chat_id": chat_id,
            "username": user.username,
            "filename": filename,
            "s3_key": s3_key
        }
        msg_json = json.dumps(broadcast_msg)

        # Send to recipient
        await connection_manager.send_personal_message(msg_json, recipient_id)
        # Send back to sender
        await connection_manager.send_personal_message(msg_json, user.user_id)
        return (True, '')

class FactoryHandler:
    """
        factory class
    """
    @staticmethod
    def get_instance_messages_type_handler(message_type:str) -> HandlerMessagesTypeI:
        """
            get the correct instance class to handle the message
        """
        print('dentro del message type factory',message_type)
        if message_type == "chat":
            return HandlerMessageChat
        if message_type == "file_request":
            return HandlerFileRequestUpload
        if message_type == "file_uploaded":
            return HandlerShowFile
        return None