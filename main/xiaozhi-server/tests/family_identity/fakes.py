"""家庭身份阶段 A 测试替身。"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from core.family_identity.models import (
    IdentityStatus,
    PersonIdentity,
    RecognitionResult,
)


class FakeVoiceprintProvider:
    """不联网的可编排声纹识别替身。"""

    def __init__(
        self,
        result: RecognitionResult,
        exception: Optional[Exception] = None,
    ) -> None:
        self.result = result
        self.exception = exception
        self.calls: List[Dict[str, Any]] = []

    async def identify_speaker(
        self,
        audio_data: bytes,
        session_id: str,
    ) -> RecognitionResult:
        self.calls.append(
            {"audio_data": audio_data, "session_id": session_id}
        )
        if self.exception is not None:
            raise self.exception
        return self.result

    @classmethod
    def recognized(
        cls,
        voiceprint_id: str,
        speaker_name: str,
        confidence: float,
    ) -> "FakeVoiceprintProvider":
        return cls(
            RecognitionResult(
                voiceprint_id=voiceprint_id,
                speaker_name=speaker_name,
                confidence=confidence,
                status=IdentityStatus.RECOGNIZED,
            )
        )

    @classmethod
    def unknown(cls) -> "FakeVoiceprintProvider":
        return cls(
            RecognitionResult(
                voiceprint_id=None,
                speaker_name=None,
                confidence=0.0,
                status=IdentityStatus.UNKNOWN_VOICEPRINT,
                error_code="unknown_voiceprint",
            )
        )

    @classmethod
    def low_confidence(
        cls,
        voiceprint_id: str,
        confidence: float,
    ) -> "FakeVoiceprintProvider":
        return cls(
            RecognitionResult(
                voiceprint_id=voiceprint_id,
                speaker_name=None,
                confidence=confidence,
                status=IdentityStatus.LOW_CONFIDENCE,
                error_code="confidence_below_threshold",
            )
        )

    @classmethod
    def timeout(cls) -> "FakeVoiceprintProvider":
        return cls(
            RecognitionResult(
                voiceprint_id=None,
                speaker_name=None,
                confidence=None,
                status=IdentityStatus.SERVICE_UNAVAILABLE,
                error_code="voiceprint_timeout",
            )
        )

    @classmethod
    def service_error(cls) -> "FakeVoiceprintProvider":
        return cls(
            RecognitionResult(
                voiceprint_id=None,
                speaker_name=None,
                confidence=None,
                status=IdentityStatus.SERVICE_UNAVAILABLE,
                error_code="voiceprint_service_error",
            )
        )


class FakeIdentityRepository:
    """仅在内存中维护声纹到人员的映射。"""

    def __init__(
        self,
        bindings: Optional[Dict[str, PersonIdentity]] = None,
    ) -> None:
        self._bindings = {
            (person.family_id, voiceprint_id): person
            for voiceprint_id, person in (bindings or {}).items()
        }
        self.find_calls: List[Dict[str, str]] = []

    def bind(self, voiceprint_id: str, person: PersonIdentity) -> None:
        self._bindings[(person.family_id, voiceprint_id)] = person

    def find_by_voiceprint_id(
        self,
        family_id: str,
        voiceprint_id: str,
    ) -> Optional[PersonIdentity]:
        self.find_calls.append(
            {"family_id": family_id, "voiceprint_id": voiceprint_id}
        )
        return self._bindings.get((family_id, voiceprint_id))


@dataclass(frozen=True)
class PowerMemCall:
    """一次测试用 PowerMem 调用记录。"""

    user_id: str
    payload: Any


class FakePowerMemRecorder:
    """只记录显式 user_id，不访问真实 PowerMem。"""

    def __init__(self) -> None:
        self.search_calls: List[PowerMemCall] = []
        self.profile_calls: List[PowerMemCall] = []
        self.add_calls: List[PowerMemCall] = []

    async def search(self, query: str, user_id: str) -> Dict[str, Any]:
        self.search_calls.append(PowerMemCall(user_id, query))
        return {"results": []}

    async def profile(self, user_id: str) -> Dict[str, Any]:
        self.profile_calls.append(PowerMemCall(user_id, None))
        return {}

    async def add(
        self,
        messages: Iterable[Dict[str, str]],
        user_id: str,
    ) -> Dict[str, Any]:
        self.add_calls.append(PowerMemCall(user_id, list(messages)))
        return {"results": []}

    @property
    def total_calls(self) -> int:
        return (
            len(self.search_calls)
            + len(self.profile_calls)
            + len(self.add_calls)
        )
