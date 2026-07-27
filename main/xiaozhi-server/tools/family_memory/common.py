"""家庭记忆命令行工具的公共输入与路径规则。"""

import json
import os
import sys
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict

from core.family_identity import (
    InvalidDatabasePathError,
    resolve_database_path,
)


SERVER_ROOT = Path(__file__).resolve().parents[2]
DATABASE_FILENAME = "family_identity.db"


class ToolInputError(ValueError):
    """命令输入不符合安全约束。"""


class ToolOperationError(RuntimeError):
    """命令无法安全完成。"""


def parse_bool(value: Any, field_name: str = "enabled") -> bool:
    """严格解析布尔值，只接受布尔对象或 true/false 文本。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ToolInputError(f"{field_name} 必须是 true 或 false")


def validate_manifest_identifier(value: Any, field_name: str) -> None:
    """对人工清单和CLI输入执行比核心模型更严格的边界校验。"""

    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"{field_name} 不能为空")
    if any(
        unicodedata.category(character) == "Cc"
        for character in value
    ):
        raise ToolInputError(f"{field_name} 不能包含控制字符")


def validate_manifest_display_name(value: Any) -> None:
    """校验显示名称；允许中文、合理空格和重复名称。"""

    validate_manifest_identifier(value, "display_name")


def resolve_cli_database_path(
    database_path: str,
    *,
    server_root: Path = SERVER_ROOT,
) -> Path:
    """解析工具数据库路径；相对路径完全复用服务端规则。"""

    if not isinstance(database_path, str) or not database_path.strip():
        raise ToolInputError("database_path 必须是非空字符串")
    if any(
        unicodedata.category(character) == "Cc"
        for character in database_path
    ):
        raise ToolInputError("database_path 不能包含控制字符")

    path = Path(database_path)
    foreign_absolute = (
        PureWindowsPath(database_path).is_absolute()
        or PurePosixPath(database_path).is_absolute()
    )
    if path.is_absolute():
        resolved = path.resolve(strict=False)
    elif foreign_absolute:
        raise InvalidDatabasePathError(
            "绝对路径格式与当前操作系统不兼容"
        )
    else:
        resolved = resolve_database_path(server_root, database_path)

    if resolved.name != DATABASE_FILENAME:
        raise InvalidDatabasePathError(
            f"工具只允许操作名为 {DATABASE_FILENAME} 的身份数据库"
        )
    return resolved


def require_existing_parent(database_path: Path) -> None:
    """写命令只接受已经存在的父目录。"""

    if not database_path.parent.is_dir():
        raise ToolOperationError(
            f"数据库父目录不存在: {database_path.parent}"
        )


def emit_result(result: Dict[str, Any], *, as_json: bool) -> None:
    """输出机器可读 JSON 或简洁中文摘要。"""

    if as_json:
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return

    for line in result.get("human_lines", []):
        print(line)


def command_error_result(message: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "errors": [message],
        "human_lines": [f"[失败] {message}"],
    }


def ensure_project_interpreter() -> str:
    """返回当前解释器绝对路径，不修改 PATH 或环境变量。"""

    return str(Path(sys.executable).resolve(strict=False))


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(
            parent.resolve(strict=False)
        )
        return True
    except ValueError:
        return False


def has_basic_access(path: Path) -> bool:
    """只用权限位进行只读判断，不创建探测文件。"""

    return os.access(path, os.R_OK | os.W_OK | os.X_OK)
