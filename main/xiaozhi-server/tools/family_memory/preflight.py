"""家庭记忆部署配置的纯只读预检。"""

import importlib
import importlib.metadata
import inspect
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.family_identity import (
    SCHEMA_VERSION,
    SQLiteIdentityRepository,
    build_memory_user_id,
)

from .common import (
    SERVER_ROOT,
    ensure_project_interpreter,
    has_basic_access,
    parse_bool,
    path_is_within,
    resolve_cli_database_path,
    validate_manifest_identifier,
)


MINIMUM_POWERMEM = (0, 3, 1)
VALIDATED_POWERMEM = (0, 5, 3)
EXPECTED_TABLES = frozenset({"family_person", "person_voiceprint"})
SYNOLOGY_CONTAINER_DATABASE = Path(
    "/opt/xiaozhi-esp32-server/data/family_identity.db"
)
SYNOLOGY_HOST_DATABASE = Path(
    "/volume1/docker/xiaozhi-server/data/family_identity.db"
)


@dataclass(frozen=True)
class Check:
    name: str
    level: str
    message: str
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "name": self.name,
            "level": self.level,
            "message": self.message,
        }
        if self.details is not None:
            result["details"] = self.details
        return result


def run_preflight(
    *,
    enabled: Any,
    family_id: Optional[str],
    database_path: str,
    server_root: Path = SERVER_ROOT,
    python_version: Optional[Sequence[int]] = None,
    python_executable: Optional[str] = None,
    metadata_version=None,
    module_importer=None,
) -> Dict[str, Any]:
    """执行预检；不实例化 PowerMem 客户端，也不创建文件。"""

    checks: List[Check] = []
    _check_python(
        checks,
        python_version=python_version,
        python_executable=python_executable,
    )
    _check_powermem(
        checks,
        metadata_version=metadata_version,
        module_importer=module_importer,
    )

    try:
        parsed_enabled = parse_bool(enabled)
        checks.append(
            Check("family_memory.enabled", "PASS", "enabled可解析为布尔值")
        )
    except Exception as exc:
        parsed_enabled = False
        checks.append(
            Check("family_memory.enabled", "FAIL", str(exc))
        )

    if family_id is None:
        family_error = "family_id缺失"
    else:
        try:
            validate_manifest_identifier(family_id, "family_id")
            build_memory_user_id(family_id, "preflight_probe")
            family_error = None
        except Exception as exc:
            family_error = str(exc)
    if family_error is None:
        checks.append(
            Check("family_memory.family_id", "PASS", f"家庭ID：{family_id}")
        )
        checks.append(
            Check(
                "memory_user_id.namespace",
                "WARN",
                "修改family_id会产生新的PowerMem用户命名空间",
            )
        )
    else:
        level = "FAIL" if parsed_enabled else "WARN"
        checks.append(
            Check("family_memory.family_id", level, family_error)
        )

    resolved_path = None
    try:
        resolved_path = resolve_cli_database_path(
            database_path,
            server_root=server_root,
        )
        checks.append(
            Check(
                "family_memory.database_path",
                "PASS",
                f"数据库路径：{resolved_path}",
            )
        )
    except Exception as exc:
        checks.append(
            Check("family_memory.database_path", "FAIL", str(exc))
        )

    if resolved_path is not None:
        _check_database_location(
            checks,
            resolved_path,
            server_root,
            parsed_enabled,
        )
        _check_database_file(checks, resolved_path)
        if resolved_path == SYNOLOGY_CONTAINER_DATABASE:
            checks.append(
                Check(
                    "synology.mapping",
                    "PASS",
                    "该宿主机路径仅适用于当前已核验的群晖部署",
                    {
                        "container_path": str(
                            SYNOLOGY_CONTAINER_DATABASE
                        ),
                        "host_path": str(SYNOLOGY_HOST_DATABASE),
                    },
                )
            )
        else:
            checks.append(
                Check(
                    "synology.mapping",
                    "SKIP",
                    "其他部署必须根据自己的volume映射确认宿主机路径",
                )
            )

    levels = [check.level for check in checks]
    overall = "FAIL" if "FAIL" in levels else (
        "WARN" if "WARN" in levels else "PASS"
    )
    result = {
        "ok": overall != "FAIL",
        "overall": overall,
        "checks": [check.to_dict() for check in checks],
        "summary": {
            level: levels.count(level)
            for level in ("PASS", "WARN", "FAIL", "SKIP")
        },
    }
    result["human_lines"] = [
        *[
            f"[{check.level}] {check.message}"
            for check in checks
        ],
        (
            "总体：可以继续初始化"
            if result["ok"]
            else "总体：预检失败，禁止继续初始化"
        ),
    ]
    return result


def _check_python(
    checks: List[Check],
    *,
    python_version: Optional[Sequence[int]],
    python_executable: Optional[str],
) -> None:
    version = tuple(
        python_version
        if python_version is not None
        else sys.version_info[:3]
    )
    executable = (
        python_executable
        if python_executable is not None
        else ensure_project_interpreter()
    )
    text = ".".join(str(part) for part in version)
    if version < (3, 10):
        level = "FAIL"
        message = f"Python {text}低于项目要求3.10"
    else:
        level = "PASS"
        message = f"Python {text}"
    checks.append(
        Check(
            "python.version",
            level,
            message,
            {"executable": executable},
        )
    )


def _check_powermem(
    checks: List[Check],
    *,
    metadata_version,
    module_importer,
) -> None:
    get_version = metadata_version or importlib.metadata.version
    importer = module_importer or importlib.import_module
    try:
        version_text = get_version("powermem")
        version = _parse_version(version_text)
    except Exception as exc:
        checks.append(
            Check("powermem.installation", "FAIL", f"PowerMem未安装: {exc}")
        )
        _skip_powermem_interfaces(checks, "PowerMem未安装")
        return

    if version < MINIMUM_POWERMEM:
        version_level = "FAIL"
        version_message = (
            f"PowerMem {version_text}低于最低版本0.3.1"
        )
    elif version > VALIDATED_POWERMEM:
        version_level = "WARN"
        version_message = (
            f"PowerMem {version_text}高于已验证0.5.3，需重新核验签名"
        )
    else:
        version_level = "PASS"
        version_message = f"PowerMem {version_text}"
    checks.append(
        Check("powermem.version", version_level, version_message)
    )

    try:
        module = importer("powermem")
    except Exception as exc:
        checks.append(
            Check("powermem.import", "FAIL", f"PowerMem导入失败: {exc}")
        )
        _skip_powermem_interfaces(checks, "PowerMem导入失败")
        return
    module_path = getattr(module, "__file__", None)
    checks.append(
        Check(
            "powermem.module",
            "PASS",
            f"PowerMem模块：{module_path}",
        )
    )

    requirements: Tuple[Tuple[str, str], ...] = (
        ("AsyncMemory", "search"),
        ("AsyncMemory", "add"),
        ("UserMemory", "search"),
        ("UserMemory", "profile"),
        ("UserMemory", "add"),
    )
    for class_name, method_name in requirements:
        check_name = f"{class_name}.{method_name}"
        client_class = getattr(module, class_name, None)
        method = getattr(client_class, method_name, None)
        if client_class is None or method is None:
            checks.append(
                Check(check_name, "FAIL", f"缺少{check_name}接口")
            )
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError) as exc:
            checks.append(
                Check(
                    check_name,
                    "FAIL",
                    f"无法检查{check_name}签名: {exc}",
                )
            )
            continue
        if "user_id" not in signature.parameters:
            checks.append(
                Check(
                    check_name,
                    "FAIL",
                    f"{check_name}缺少显式user_id参数",
                )
            )
        else:
            checks.append(
                Check(
                    check_name,
                    "PASS",
                    f"{check_name}支持user_id",
                    {"signature": str(signature)},
                )
            )


def _skip_powermem_interfaces(
    checks: List[Check],
    reason: str,
) -> None:
    for class_name, method_name in (
        ("AsyncMemory", "search"),
        ("AsyncMemory", "add"),
        ("UserMemory", "search"),
        ("UserMemory", "profile"),
        ("UserMemory", "add"),
    ):
        check_name = f"{class_name}.{method_name}"
        checks.append(
            Check(check_name, "SKIP", f"{check_name}跳过：{reason}")
        )


def _check_database_location(
    checks: List[Check],
    database_path: Path,
    server_root: Path,
    enabled: bool,
) -> None:
    data_root = server_root.resolve(strict=False) / "data"
    if path_is_within(database_path, data_root):
        checks.append(
            Check(
                "database.persistence",
                "PASS",
                "数据库路径位于server的data目录",
                {"data_directory": str(data_root)},
            )
        )
    else:
        level = "FAIL" if enabled else "WARN"
        checks.append(
            Check(
                "database.persistence",
                level,
                "数据库路径不在server的data目录",
                {"data_directory": str(data_root)},
            )
        )

    parent = database_path.parent
    if not parent.is_dir():
        checks.append(
            Check(
                "database.parent",
                "FAIL" if enabled else "WARN",
                f"数据库父目录不存在: {parent}",
            )
        )
    elif not has_basic_access(parent):
        checks.append(
            Check(
                "database.parent",
                "FAIL",
                f"当前进程无法基本访问数据库父目录: {parent}",
            )
        )
    else:
        checks.append(
            Check(
                "database.parent",
                "PASS",
                f"数据库父目录可访问: {parent}",
            )
        )


def _check_database_file(
    checks: List[Check],
    database_path: Path,
) -> None:
    if not database_path.exists():
        checks.append(
            Check(
                "identity_database",
                "WARN",
                "family_identity.db尚未初始化；首次apply或Runtime启动时创建",
            )
        )
        return
    if not database_path.is_file():
        checks.append(
            Check(
                "identity_database",
                "FAIL",
                "family_identity.db不是普通文件",
            )
        )
        return
    if not os.access(database_path, os.R_OK):
        checks.append(
            Check(
                "identity_database",
                "FAIL",
                "family_identity.db不可读",
            )
        )
        return

    try:
        repository = SQLiteIdentityRepository(
            database_path,
            read_only=True,
        )
        schema = repository.inspect_schema()
    except Exception as exc:
        checks.append(
            Check(
                "identity_database",
                "FAIL",
                f"无法只读打开身份数据库: {exc}",
            )
        )
        return

    missing_tables = EXPECTED_TABLES - set(schema["tables"])
    if schema["user_version"] != SCHEMA_VERSION or missing_tables:
        checks.append(
            Check(
                "identity_database",
                "FAIL",
                "身份数据库schema不符合当前版本",
                schema,
            )
        )
    else:
        checks.append(
            Check(
                "identity_database",
                "PASS",
                f"身份数据库schema版本：{SCHEMA_VERSION}",
                schema,
            )
        )


def _parse_version(value: str) -> Tuple[int, ...]:
    numeric = []
    for part in value.split("."):
        digits = ""
        for character in part:
            if character.isdigit():
                digits += character
            else:
                break
        if not digits:
            break
        numeric.append(int(digits))
    if not numeric:
        raise ValueError(f"无法解析PowerMem版本: {value}")
    while len(numeric) < 3:
        numeric.append(0)
    return tuple(numeric)
