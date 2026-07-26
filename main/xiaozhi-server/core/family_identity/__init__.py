"""家庭身份领域模型与安全策略。"""

from .errors import (
    FamilyIdentityError,
    IdentityRepositoryError,
    InvalidIdentityDecisionError,
    InvalidIdentityIdError,
    MemoryAccessDeniedError,
    PersonNotFoundError,
    UnsupportedSchemaVersionError,
    VoiceprintAlreadyBoundError,
    VoiceprintBindingError,
    VoiceprintNotFoundError,
    VoiceprintRevokedError,
)
from .models import (
    IdentityDecision,
    IdentityStatus,
    PersonIdentity,
    RecognitionResult,
    TurnIdentityContext,
    build_memory_user_id,
)
from .policy import IdentityPolicy, MemoryAccessPolicy
from .service import IdentityService
from .sqlite_repository import SCHEMA_VERSION, SQLiteIdentityRepository

__all__ = [
    "FamilyIdentityError",
    "IdentityDecision",
    "IdentityPolicy",
    "IdentityRepositoryError",
    "IdentityService",
    "IdentityStatus",
    "InvalidIdentityDecisionError",
    "InvalidIdentityIdError",
    "MemoryAccessDeniedError",
    "MemoryAccessPolicy",
    "PersonIdentity",
    "PersonNotFoundError",
    "RecognitionResult",
    "SCHEMA_VERSION",
    "SQLiteIdentityRepository",
    "TurnIdentityContext",
    "UnsupportedSchemaVersionError",
    "VoiceprintAlreadyBoundError",
    "VoiceprintBindingError",
    "VoiceprintNotFoundError",
    "VoiceprintRevokedError",
    "build_memory_user_id",
]
