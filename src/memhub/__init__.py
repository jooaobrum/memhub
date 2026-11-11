"""memhub: reviewed, typed, versioned long-term memory for agents."""
from memhub.memory import AddResult, CallContext, Dropped, Memory, MemoryEvent, ScopedMemory
from memhub.service import (
    InjectionDetected,
    MemoryService,
    NotFound,
    PermissionDenied,
    ServiceError,
    ValidationError,
)
from memhub.store import EmbeddingMismatch

__version__ = "0.1.0"

__all__ = [
    "AddResult",
    "CallContext",
    "Dropped",
    "EmbeddingMismatch",
    "InjectionDetected",
    "Memory",
    "MemoryEvent",
    "MemoryService",
    "NotFound",
    "PermissionDenied",
    "ScopedMemory",
    "ServiceError",
    "ValidationError",
]
