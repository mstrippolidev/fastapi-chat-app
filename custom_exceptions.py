"""Custom exceptions for domain-specific error signaling."""


class UserSessionExpired(Exception):
    """Raised when the user's session TTL has passed."""


class ChatSessionNotFoundError(Exception):
    """Raised when a chat_id does not correspond to any existing session."""


class UserChatNotAllowedError(Exception):
    """Raised when a user attempts to interact with a chat they are not a participant of."""