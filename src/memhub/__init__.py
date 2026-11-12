"""memhub: reviewed, typed, versioned long-term memory for agents."""
from memhub.config import Config
from memhub.memory import AddResult, CallContext, Dropped, Memory, MemoryEvent, ScopedMemory
from memhub.service import (
    InjectionDetected,
    MemoryService,
    NotFound,
    PermissionDenied,
    ServiceError,
    ValidationError,
)
from memhub.storage.rules import EmbeddingMismatch, SchemaOutdated

__version__ = "0.1.0"

__all__ = [
    "AddResult",
    "CallContext",
    "Config",
    "Dropped",
    "EmbeddingMismatch",
    "InjectionDetected",
    "Memory",
    "MemoryEvent",
    "MemoryService",
    "NotFound",
    "PermissionDenied",
    "SchemaOutdated",
    "ScopedMemory",
    "ServiceError",
    "ValidationError",
]
