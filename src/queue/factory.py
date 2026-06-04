from src.shared.config import settings
from src.queue.interfaces import IMessageQueue

_queue_instance = None

def get_queue() -> IMessageQueue:
    global _queue_instance
    if _queue_instance is None:
        if settings.QUEUE_BACKEND.lower() == "redis":
            from src.queue.redis_queue import RedisStreamsQueue
            _queue_instance = RedisStreamsQueue()
        else:
            from src.queue.servicebus_queue import AzureServiceBusQueue
            _queue_instance = AzureServiceBusQueue()
    return _queue_instance
