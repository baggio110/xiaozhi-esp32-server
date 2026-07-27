"""家庭记忆 Repository 生命周期管理。"""

from pathlib import Path
from typing import Optional

from .config import (
    FamilyMemorySettings,
    PathValue,
    resolve_database_path,
)
from .models import (
    IdentityDecision,
    IdentityStatus,
    RecognitionResult,
)
from .service import IdentityService
from .sqlite_repository import SQLiteIdentityRepository


class FamilyMemoryRuntime:
    """按配置启停一个受管理的 SQLiteIdentityRepository。"""

    def __init__(
        self,
        settings: FamilyMemorySettings,
        server_root: PathValue,
    ) -> None:
        if not isinstance(settings, FamilyMemorySettings):
            raise TypeError("settings 必须是 FamilyMemorySettings")
        self._settings = settings
        self._server_root = server_root
        self._started = False
        self._repository: Optional[SQLiteIdentityRepository] = None
        self._identity_service: Optional[IdentityService] = None
        self._database_path: Optional[Path] = None

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def is_active(self) -> bool:
        return (
            self._started
            and self._repository is not None
            and self._identity_service is not None
        )

    @property
    def family_id(self) -> Optional[str]:
        if not self.is_active:
            return None
        return self._settings.family_id

    @property
    def database_path(self) -> Optional[Path]:
        return self._database_path

    @property
    def repository(self) -> Optional[SQLiteIdentityRepository]:
        return self._repository

    def start(self) -> Optional[SQLiteIdentityRepository]:
        """幂等启动；重复调用返回同一个 Repository。"""

        if self._started:
            return self._repository

        if not self._settings.enabled:
            self._started = True
            return None

        database_path = resolve_database_path(
            self._server_root,
            self._settings.database_path,
        )
        database_path.parent.mkdir(parents=True, exist_ok=True)
        repository = SQLiteIdentityRepository(database_path)
        identity_service = IdentityService(repository)

        self._database_path = database_path
        self._repository = repository
        self._identity_service = identity_service
        self._started = True
        return repository

    def resolve_identity(
        self,
        recognition: Optional[RecognitionResult],
    ) -> IdentityDecision:
        """使用当前 Runtime 的家庭边界解析一次声纹结果。"""

        family_id = self.family_id
        identity_service = self._identity_service
        if family_id is None or identity_service is None:
            raise RuntimeError("家庭身份 Runtime 尚未启用")

        try:
            return identity_service.resolve(family_id, recognition)
        except Exception:
            return IdentityDecision.denied(
                family_id,
                IdentityStatus.INVALID_RESULT,
                failure_reason="identity_resolution_error",
            )

    def close(self) -> None:
        """停止 Runtime；短连接 Repository 无需伪造 close 操作。"""

        self._repository = None
        self._identity_service = None
        self._database_path = None
        self._started = False
