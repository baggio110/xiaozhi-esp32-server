"""家庭记忆独立配置模型与数据库路径解析。"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Optional, Union

from .errors import (
    FamilyMemoryConfigurationError,
    InvalidDatabasePathError,
)

PathValue = Union[str, os.PathLike]
DEFAULT_DATABASE_PATH = "data/family_identity.db"
_ALLOWED_SETTING_KEYS = frozenset(
    {"enabled", "family_id", "database_path"}
)


@dataclass(frozen=True)
class FamilyMemorySettings:
    """家庭记忆最小配置；默认关闭。"""

    enabled: bool = False
    family_id: Optional[str] = None
    database_path: str = DEFAULT_DATABASE_PATH

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise FamilyMemoryConfigurationError(
                "enabled 必须是布尔值"
            )
        if self.family_id is not None and not isinstance(
            self.family_id,
            str,
        ):
            raise FamilyMemoryConfigurationError(
                "family_id 必须是字符串或 None"
            )
        if self.enabled and (
            self.family_id is None or not self.family_id.strip()
        ):
            raise FamilyMemoryConfigurationError(
                "家庭记忆启用时 family_id 不能为空"
            )
        if (
            not isinstance(self.database_path, str)
            or not self.database_path.strip()
        ):
            raise FamilyMemoryConfigurationError(
                "database_path 必须是非空字符串"
            )
        if _is_absolute_path(self.database_path):
            raise InvalidDatabasePathError(
                "database_path 必须是项目内相对路径"
            )

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
    ) -> "FamilyMemorySettings":
        """从普通 mapping 创建严格的三字段配置。"""

        if not isinstance(values, Mapping):
            raise FamilyMemoryConfigurationError(
                "家庭记忆配置必须是 mapping"
            )
        unknown_keys = set(values) - _ALLOWED_SETTING_KEYS
        if unknown_keys:
            unknown_text = ", ".join(sorted(map(str, unknown_keys)))
            raise FamilyMemoryConfigurationError(
                f"家庭记忆配置包含未支持字段: {unknown_text}"
            )
        return cls(
            enabled=values.get("enabled", False),
            family_id=values.get("family_id"),
            database_path=values.get(
                "database_path",
                DEFAULT_DATABASE_PATH,
            ),
        )


def resolve_database_path(
    server_root: PathValue,
    database_path: str,
) -> Path:
    """将数据库相对路径安全解析到 xiaozhi-server 根目录内。"""

    root = _validate_server_root(server_root)
    if (
        not isinstance(database_path, str)
        or not database_path.strip()
    ):
        raise InvalidDatabasePathError(
            "database_path 必须是非空字符串"
        )

    relative_path = Path(database_path)
    if _is_absolute_path(database_path):
        raise InvalidDatabasePathError(
            "database_path 必须是相对于 server_root 的路径"
        )

    resolved_path = (root / relative_path).resolve(strict=False)
    try:
        resolved_path.relative_to(root)
    except ValueError as exc:
        raise InvalidDatabasePathError(
            "database_path 不得逃出 server_root"
        ) from exc

    if resolved_path == root:
        raise InvalidDatabasePathError(
            "database_path 必须指向数据库文件"
        )
    if resolved_path.exists() and resolved_path.is_dir():
        raise InvalidDatabasePathError(
            "database_path 不能指向目录"
        )
    return resolved_path


def _is_absolute_path(database_path: str) -> bool:
    return (
        Path(database_path).is_absolute()
        or PureWindowsPath(database_path).is_absolute()
        or PurePosixPath(database_path).is_absolute()
    )


def _validate_server_root(server_root: PathValue) -> Path:
    try:
        raw_root = os.fspath(server_root)
    except TypeError as exc:
        raise InvalidDatabasePathError(
            "server_root 必须是字符串或 PathLike"
        ) from exc
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise InvalidDatabasePathError("server_root 不能为空")

    root = Path(raw_root)
    if not root.is_absolute():
        raise InvalidDatabasePathError(
            "server_root 必须由调用方显式传入绝对路径"
        )
    resolved_root = root.resolve(strict=False)
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise InvalidDatabasePathError(
            "server_root 必须是已存在的目录"
        )
    return resolved_root
