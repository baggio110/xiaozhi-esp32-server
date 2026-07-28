"""家庭记忆内部管理操作的安全编排入口。"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.family_identity import (
    FamilyMemoryRuntime,
    FamilyMemorySettings,
    SQLiteIdentityRepository,
    resolve_database_path,
)
from tools.family_memory.admin import (
    list_family,
    person_rename,
    person_set_enabled,
    person_show,
    person_upsert,
    voiceprint_bind,
    voiceprint_list,
    voiceprint_replace,
    voiceprint_revoke,
)
from tools.family_memory.common import (
    ToolInputError,
    ToolOperationError,
    validate_manifest_identifier,
)
from tools.family_memory.preflight import run_preflight


PreflightRunner = Callable[..., Dict[str, Any]]


class FamilyMemoryAdminHandler:
    """只在通过服务端密钥验证后调用的家庭身份管理编排器。"""

    _SUPPORTED_OPERATIONS = frozenset(
        {
            "preflight",
            "person_list",
            "person_create",
            "person_show",
            "person_rename",
            "person_set_enabled",
            "voiceprint_list",
            "voiceprint_bind",
            "voiceprint_revoke",
            "voiceprint_replace",
        }
    )

    def __init__(
        self,
        server_root: Path,
        runtime: Optional[FamilyMemoryRuntime] = None,
        *,
        preflight_runner: PreflightRunner = run_preflight,
    ) -> None:
        self._server_root = Path(server_root).resolve(strict=False)
        self._runtime = runtime
        self._preflight_runner = preflight_runner

    def handle(
        self,
        operation: str,
        settings_values: Mapping[str, Any],
        payload: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """校验配置后执行一个明确的管理操作。"""

        if operation not in self._SUPPORTED_OPERATIONS:
            raise ToolInputError("不支持的家庭记忆管理操作")
        settings = self._parse_settings(settings_values)
        operation_payload = self._require_mapping(payload or {}, "payload")

        if operation == "preflight":
            return self._run_preflight(settings)

        family_id = self._require_family_id(settings)
        database_path = resolve_database_path(
            self._server_root,
            settings.database_path,
        )

        if operation == "person_list":
            return list_family(family_id, database_path)
        if operation == "person_create":
            person_id = self._required_text(operation_payload, "person_id")
            self._ensure_person_absent(
                database_path,
                family_id,
                person_id,
            )
            return person_upsert(
                family_id=family_id,
                person_id=person_id,
                display_name=self._required_text(
                    operation_payload,
                    "display_name",
                ),
                enabled=True,
                database_path=database_path,
                confirm_family_id=family_id,
            )
        if operation == "person_show":
            return person_show(
                family_id,
                self._required_text(operation_payload, "person_id"),
                database_path,
            )
        if operation == "person_rename":
            return person_rename(
                family_id=family_id,
                person_id=self._required_text(
                    operation_payload,
                    "person_id",
                ),
                display_name=self._required_text(
                    operation_payload,
                    "display_name",
                ),
                database_path=database_path,
                confirm_family_id=family_id,
            )
        if operation == "person_set_enabled":
            enabled = operation_payload.get("enabled")
            if not isinstance(enabled, bool):
                raise ToolInputError("enabled 必须是布尔值")
            return person_set_enabled(
                family_id=family_id,
                person_id=self._required_text(
                    operation_payload,
                    "person_id",
                ),
                enabled=enabled,
                database_path=database_path,
                confirm_family_id=family_id,
            )
        if operation == "voiceprint_list":
            person_id = operation_payload.get("person_id")
            if person_id is not None:
                person_id = self._required_text(
                    operation_payload,
                    "person_id",
                )
            return voiceprint_list(
                family_id,
                database_path,
                person_id=person_id,
            )
        if operation == "voiceprint_bind":
            return voiceprint_bind(
                family_id=family_id,
                person_id=self._required_text(
                    operation_payload,
                    "person_id",
                ),
                voiceprint_id=self._required_text(
                    operation_payload,
                    "voiceprint_id",
                ),
                database_path=database_path,
                confirm_family_id=family_id,
            )
        if operation == "voiceprint_revoke":
            return voiceprint_revoke(
                family_id=family_id,
                voiceprint_id=self._required_text(
                    operation_payload,
                    "voiceprint_id",
                ),
                database_path=database_path,
                confirm_family_id=family_id,
            )
        return voiceprint_replace(
            family_id=family_id,
            person_id=self._required_text(
                operation_payload,
                "person_id",
            ),
            old_voiceprint_id=self._required_text(
                operation_payload,
                "old_voiceprint_id",
            ),
            new_voiceprint_id=self._required_text(
                operation_payload,
                "new_voiceprint_id",
            ),
            database_path=database_path,
            confirm_family_id=family_id,
        )

    def _run_preflight(
        self,
        settings: FamilyMemorySettings,
    ) -> Dict[str, Any]:
        result = self._preflight_runner(
            enabled=settings.enabled,
            family_id=settings.family_id,
            database_path=settings.database_path,
            server_root=self._server_root,
        )
        family_id = settings.family_id
        member_count = 0
        active_voiceprint_count = 0
        if family_id:
            validate_manifest_identifier(family_id, "family_id")
            database_path = resolve_database_path(
                self._server_root,
                settings.database_path,
            )
            family = list_family(family_id, database_path)
            persons = family["persons"]
            member_count = len(persons)
            active_voiceprint_count = sum(
                len(person["active_voiceprint_ids"])
                for person in persons
            )

        response = dict(result)
        response["member_count"] = member_count
        response["active_voiceprint_count"] = active_voiceprint_count
        response["can_enable"] = bool(result.get("ok"))
        response["runtime"] = self._runtime_status(settings)
        return response

    def _runtime_status(
        self,
        desired: FamilyMemorySettings,
    ) -> Dict[str, Any]:
        runtime = self._runtime
        active = bool(runtime and runtime.is_active)
        desired_path = resolve_database_path(
            self._server_root,
            desired.database_path,
        )
        running_path = runtime.database_path if runtime else None
        restart_required = (
            active != desired.enabled
            or (
                desired.enabled
                and (
                    runtime is None
                    or runtime.family_id != desired.family_id
                    or running_path != desired_path
                )
            )
        )
        return {
            "active": active,
            "family_id": runtime.family_id if active else None,
            "database_path": str(running_path) if running_path else None,
            "restart_required": restart_required,
        }

    @staticmethod
    def _parse_settings(
        settings_values: Mapping[str, Any],
    ) -> FamilyMemorySettings:
        values = FamilyMemoryAdminHandler._require_mapping(
            settings_values,
            "settings",
        )
        return FamilyMemorySettings.from_mapping(values)

    @staticmethod
    def _require_family_id(settings: FamilyMemorySettings) -> str:
        family_id = settings.family_id
        if family_id is None:
            raise ToolInputError("family_id 不能为空")
        validate_manifest_identifier(family_id, "family_id")
        return family_id

    @staticmethod
    def _required_text(
        payload: Mapping[str, Any],
        field_name: str,
    ) -> str:
        value = payload.get(field_name)
        validate_manifest_identifier(value, field_name)
        return value

    @staticmethod
    def _require_mapping(
        value: Mapping[str, Any],
        field_name: str,
    ) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ToolInputError(f"{field_name} 必须是JSON对象")
        return value

    @staticmethod
    def _ensure_person_absent(
        database_path: Path,
        family_id: str,
        person_id: str,
    ) -> None:
        if not database_path.is_file():
            return
        repository = SQLiteIdentityRepository(
            database_path,
            read_only=True,
        )
        if repository.get_person(family_id, person_id) is not None:
            raise ToolOperationError("person_id已存在，请使用修改名称操作")
