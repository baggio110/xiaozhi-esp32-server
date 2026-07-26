"""家庭身份领域模型。

本模块只表达身份数据及其不变量，不访问数据库、声纹服务或 PowerMem。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .errors import InvalidIdentityDecisionError, InvalidIdentityIdError


class IdentityStatus(str, Enum):
    """身份识别与路由状态。"""

    RECOGNIZED = "recognized"
    UNKNOWN_VOICEPRINT = "unknown_voiceprint"
    LOW_CONFIDENCE = "low_confidence"
    SERVICE_UNAVAILABLE = "service_unavailable"
    PERSON_NOT_BOUND = "person_not_bound"
    PERSON_DISABLED = "person_disabled"
    INVALID_RESULT = "invalid_result"


def _require_non_empty_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidIdentityIdError(f"{field_name} 不能为空")


def build_memory_user_id(family_id: str, person_id: str) -> str:
    """生成稳定的 PowerMem 用户身份。

    显示名称、声纹凭据和设备身份均不得参与该值。
    """

    _require_non_empty_id(family_id, "family_id")
    _require_non_empty_id(person_id, "person_id")
    return f"{family_id}:{person_id}"


@dataclass(frozen=True)
class RecognitionResult:
    """声纹服务的原始识别结果。"""

    voiceprint_id: Optional[str]
    speaker_name: Optional[str]
    confidence: Optional[float]
    status: IdentityStatus
    error_code: Optional[str] = None


@dataclass(frozen=True)
class PersonIdentity:
    """与声纹凭据解耦的稳定家庭成员身份。"""

    family_id: str
    person_id: str
    display_name: str
    enabled: bool = True

    def __post_init__(self) -> None:
        _require_non_empty_id(self.family_id, "family_id")
        _require_non_empty_id(self.person_id, "person_id")
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled 必须是布尔值")

    @property
    def memory_user_id(self) -> str:
        return build_memory_user_id(self.family_id, self.person_id)


@dataclass(frozen=True)
class IdentityDecision:
    """身份路由结果及私人记忆访问权限。"""

    family_id: str
    person_id: Optional[str]
    voiceprint_id: Optional[str]
    display_name: Optional[str]
    memory_user_id: Optional[str]
    identity_status: IdentityStatus
    allow_memory_read: bool
    allow_memory_write: bool
    failure_reason: Optional[str] = None

    def __post_init__(self) -> None:
        _require_non_empty_id(self.family_id, "family_id")
        if not isinstance(self.identity_status, IdentityStatus):
            raise InvalidIdentityDecisionError(
                "identity_status 必须是 IdentityStatus"
            )

        if self.identity_status is IdentityStatus.RECOGNIZED:
            if not self.person_id or not self.memory_user_id:
                raise InvalidIdentityDecisionError(
                    "已识别身份必须包含 person_id 和 memory_user_id"
                )
            expected_user_id = build_memory_user_id(self.family_id, self.person_id)
            if self.memory_user_id != expected_user_id:
                raise InvalidIdentityDecisionError(
                    "memory_user_id 必须由 family_id 和 person_id 生成"
                )
            if not self.allow_memory_read or not self.allow_memory_write:
                raise InvalidIdentityDecisionError(
                    "已识别身份必须同时允许读取和写入私人记忆"
                )
            return

        if self.allow_memory_read or self.allow_memory_write:
            raise InvalidIdentityDecisionError(
                "非 RECOGNIZED 状态不得访问私人记忆"
            )
        if self.memory_user_id is not None:
            raise InvalidIdentityDecisionError(
                "非 RECOGNIZED 状态不得携带 memory_user_id"
            )

    @classmethod
    def recognized(
        cls,
        person: PersonIdentity,
        voiceprint_id: str,
    ) -> "IdentityDecision":
        _require_non_empty_id(voiceprint_id, "voiceprint_id")
        return cls(
            family_id=person.family_id,
            person_id=person.person_id,
            voiceprint_id=voiceprint_id,
            display_name=person.display_name,
            memory_user_id=person.memory_user_id,
            identity_status=IdentityStatus.RECOGNIZED,
            allow_memory_read=True,
            allow_memory_write=True,
        )

    @classmethod
    def denied(
        cls,
        family_id: str,
        identity_status: IdentityStatus,
        *,
        voiceprint_id: Optional[str] = None,
        display_name: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ) -> "IdentityDecision":
        if identity_status is IdentityStatus.RECOGNIZED:
            raise InvalidIdentityDecisionError(
                "RECOGNIZED 状态不能通过 denied() 创建"
            )
        return cls(
            family_id=family_id,
            person_id=None,
            voiceprint_id=voiceprint_id,
            display_name=display_name,
            memory_user_id=None,
            identity_status=identity_status,
            allow_memory_read=False,
            allow_memory_write=False,
            failure_reason=failure_reason,
        )


@dataclass(frozen=True)
class TurnIdentityContext:
    """不可变的单轮身份快照。"""

    session_id: str
    turn_id: str
    device_id: str
    identity_decision: IdentityDecision
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        _require_non_empty_id(self.session_id, "session_id")
        _require_non_empty_id(self.turn_id, "turn_id")
        _require_non_empty_id(self.device_id, "device_id")
