from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable

class MessageContext(ABC):
    """Context block that allows workers to acknowledge or dead-letter a message."""
    
    @abstractmethod
    async def complete(self):
        """Marks the message as processed."""
        pass
        
    @abstractmethod
    async def dead_letter(self, reason: str, description: str):
        """Moves the message to a dead-letter queue (or equivalent)."""
        pass
        
    @abstractmethod
    async def renew_lock(self):
        """Renews message processing lock if supported by the underlying queue."""
        pass

class IMessageQueue(ABC):
    """Agnostic interface for Message Queues."""
    
    @abstractmethod
    async def enqueue(self, queue: str, payload: dict, session_id: str | None = None):
        """Enqueues a message. Session ID guarantees ordering (FIFO) for that specific session."""
        pass

    @abstractmethod
    async def consume_loop(self, queue: str, handler: Callable[[dict, MessageContext], Awaitable[None]], use_sessions: bool = False):
        """Starts an infinite loop listening for messages. Blocks the current task."""
        pass
        
    @abstractmethod
    async def close(self):
        """Closes connections."""
        pass
