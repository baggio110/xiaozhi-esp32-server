"""家庭身份领域模型与安全策略。"""

from .errors import (
    FamilyIdentityError,
    FamilyMemoryConfigurationError,
    IdentityRepositoryError,
    InvalidDatabasePathError,
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
from .config import (
    DEFAULT_DATABASE_PATH,
    FamilyMemorySettings,
    resolve_database_path,
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
from .runtime import FamilyMemoryRuntime

__all__ = [
    "DEFAULT_DATABASE_PATH",
    "FamilyIdentityError",
    "FamilyMemoryConfigurationError",
    "FamilyMemoryRuntime",
    "FamilyMemorySettings",
    "IdentityDecision",
    "IdentityPolicy",
    "IdentityRepositoryError",
    "IdentityService",
    "IdentityStatus",
    "InvalidDatabasePathError",
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
    "resolve_database_path",
]
