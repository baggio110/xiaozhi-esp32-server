"""家庭身份模块的边界接口。"""

from typing import Any, Dict, List, Optional, Protocol

from .models import IdentityDecision, PersonIdentity, RecognitionResult


class VoiceprintProvider(Protocol):
    """声纹识别端口。"""

    async def identify_speaker(
        self,
        audio_data: bytes,
        session_id: str,
    ) -> RecognitionResult:
        """返回结构化声纹结果，不得只返回显示名称。"""


class IdentityRepository(Protocol):
    """声纹凭据到稳定人员身份的只读查询端口。"""

    def find_by_voiceprint_id(
        self,
        family_id: str,
        voiceprint_id: str,
    ) -> Optional[PersonIdentity]:
        """在指定家庭边界内查询声纹凭据绑定的人员身份。"""


class IdentityResolver(Protocol):
    """原始识别结果到最终身份决策的解析端口。"""

    def decide(
        self,
        family_id: str,
        recognition: Optional[RecognitionResult],
    ) -> IdentityDecision:
        """生成 fail-closed 身份决策。"""


class PersonalMemoryGateway(Protocol):
    """使用显式 user_id 访问个人 PowerMem 的端口。"""

    async def search(self, query: str, user_id: str) -> Dict[str, Any]:
        """查询指定人员记忆。"""

    async def profile(self, user_id: str) -> Dict[str, Any]:
        """查询指定人员画像。"""

    async def add(
        self,
        messages: List[Dict[str, str]],
        user_id: str,
    ) -> Dict[str, Any]:
        """保存指定人员的单轮问答。"""
