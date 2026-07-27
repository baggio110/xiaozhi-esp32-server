"""家庭身份解析与私人记忆访问策略。"""

from math import isfinite
from typing import Optional

from .errors import MemoryAccessDeniedError
from .interfaces import IdentityRepository
from .models import (
    IdentityDecision,
    IdentityStatus,
    RecognitionResult,
)


class IdentityPolicy:
    """将声纹结果安全地解析为稳定人员身份。"""

    def __init__(
        self,
        repository: IdentityRepository,
    ) -> None:
        self._repository = repository

    def decide(
        self,
        family_id: str,
        recognition: Optional[RecognitionResult],
    ) -> IdentityDecision:
        """执行身份映射；任何不确定状态都禁止私人记忆访问。"""

        if recognition is None or not isinstance(recognition, RecognitionResult):
            return IdentityDecision.denied(
                family_id,
                IdentityStatus.INVALID_RESULT,
                failure_reason="voiceprint_result_missing_or_invalid",
            )

        if not isinstance(recognition.status, IdentityStatus):
            return IdentityDecision.denied(
                family_id,
                IdentityStatus.INVALID_RESULT,
                voiceprint_id=recognition.voiceprint_id,
                display_name=recognition.speaker_name,
                failure_reason="identity_status_invalid",
            )

        if recognition.status is not IdentityStatus.RECOGNIZED:
            return IdentityDecision.denied(
                family_id,
                recognition.status,
                voiceprint_id=recognition.voiceprint_id,
                display_name=recognition.speaker_name,
                failure_reason=(
                    recognition.error_code or recognition.status.value
                ),
            )

        voiceprint_id = recognition.voiceprint_id
        if not isinstance(voiceprint_id, str) or not voiceprint_id.strip():
            return IdentityDecision.denied(
                family_id,
                IdentityStatus.INVALID_RESULT,
                display_name=recognition.speaker_name,
                failure_reason="voiceprint_id_missing",
            )

        confidence = recognition.confidence
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            return IdentityDecision.denied(
                family_id,
                IdentityStatus.INVALID_RESULT,
                voiceprint_id=voiceprint_id,
                display_name=recognition.speaker_name,
                failure_reason="confidence_invalid",
            )

        try:
            person = self._repository.find_by_voiceprint_id(
                family_id,
                voiceprint_id,
            )
        except Exception:
            return IdentityDecision.denied(
                family_id,
                IdentityStatus.INVALID_RESULT,
                voiceprint_id=voiceprint_id,
                display_name=recognition.speaker_name,
                failure_reason="identity_repository_error",
            )

        if person is None or person.family_id != family_id:
            return IdentityDecision.denied(
                family_id,
                IdentityStatus.PERSON_NOT_BOUND,
                voiceprint_id=voiceprint_id,
                display_name=recognition.speaker_name,
                failure_reason="voiceprint_not_bound_to_family_person",
            )

        if not person.enabled:
            return IdentityDecision.denied(
                family_id,
                IdentityStatus.PERSON_DISABLED,
                voiceprint_id=voiceprint_id,
                display_name=person.display_name,
                failure_reason="person_disabled",
            )

        return IdentityDecision.recognized(person, voiceprint_id)


class MemoryAccessPolicy:
    """在调用 PowerMem 前强制执行身份决策。"""

    @staticmethod
    def user_id_for_read(decision: IdentityDecision) -> str:
        if (
            decision.identity_status is not IdentityStatus.RECOGNIZED
            or not decision.allow_memory_read
            or not decision.memory_user_id
        ):
            raise MemoryAccessDeniedError(
                f"身份状态 {decision.identity_status.value} 不允许读取私人记忆"
            )
        return decision.memory_user_id

    @staticmethod
    def user_id_for_write(decision: IdentityDecision) -> str:
        if (
            decision.identity_status is not IdentityStatus.RECOGNIZED
            or not decision.allow_memory_write
            or not decision.memory_user_id
        ):
            raise MemoryAccessDeniedError(
                f"身份状态 {decision.identity_status.value} 不允许写入私人记忆"
            )
        return decision.memory_user_id
