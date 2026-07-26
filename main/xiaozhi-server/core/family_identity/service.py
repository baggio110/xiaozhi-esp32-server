"""家庭身份解析应用服务。"""

from typing import Optional

from .interfaces import IdentityRepository
from .models import IdentityDecision, RecognitionResult
from .policy import IdentityPolicy


class IdentityService:
    """连接身份 Repository 与 fail-closed 策略。"""

    def __init__(
        self,
        repository: IdentityRepository,
        *,
        min_confidence: float,
    ) -> None:
        self._policy = IdentityPolicy(repository, min_confidence)

    def resolve(
        self,
        family_id: str,
        recognition: Optional[RecognitionResult],
    ) -> IdentityDecision:
        """将原始声纹结果解析为稳定身份决策。"""

        return self._policy.decide(family_id, recognition)
