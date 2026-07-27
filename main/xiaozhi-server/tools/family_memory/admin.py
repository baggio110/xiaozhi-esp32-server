"""家庭成员及官方声纹 ID 本地映射管理。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.family_identity import (
    PersonIdentity,
    SCHEMA_VERSION,
    SQLiteIdentityRepository,
    VoiceprintBinding,
    build_memory_user_id,
)

from .common import (
    SERVER_ROOT,
    ToolInputError,
    ToolOperationError,
    require_existing_parent,
    resolve_cli_database_path,
    validate_manifest_display_name,
    validate_manifest_identifier,
)


_MANIFEST_KEYS = frozenset({"family_id", "database_path", "members"})
_MEMBER_KEYS = frozenset(
    {"person_id", "display_name", "enabled", "voiceprint_ids"}
)
_EXPECTED_TABLES = frozenset({"family_person", "person_voiceprint"})


@dataclass(frozen=True)
class MemberSpec:
    person: PersonIdentity
    voiceprint_ids: Tuple[str, ...]


@dataclass(frozen=True)
class FamilyManifest:
    family_id: str
    database_path: Path
    members: Tuple[MemberSpec, ...]


def load_manifest(
    manifest_path: Path,
    *,
    database_path_override: Optional[str] = None,
    server_root: Path = SERVER_ROOT,
) -> FamilyManifest:
    """读取并严格校验 JSON 清单。"""

    try:
        with Path(manifest_path).open("r", encoding="utf-8") as manifest_file:
            raw = json.load(manifest_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolInputError(f"无法读取JSON清单: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ToolInputError("清单根节点必须是JSON对象")
    unknown_keys = set(raw) - _MANIFEST_KEYS
    if unknown_keys:
        raise ToolInputError(
            "清单包含未知字段: " + ", ".join(sorted(unknown_keys))
        )

    family_id = raw.get("family_id")
    validate_manifest_identifier(family_id, "family_id")
    raw_database_path = (
        database_path_override
        if database_path_override is not None
        else raw.get("database_path")
    )
    if raw_database_path is None:
        raise ToolInputError(
            "database_path 必须存在于清单或命令行参数中"
        )
    database_path = resolve_cli_database_path(
        raw_database_path,
        server_root=server_root,
    )

    raw_members = raw.get("members")
    if not isinstance(raw_members, list):
        raise ToolInputError("members 必须是列表")

    members: List[MemberSpec] = []
    person_ids = set()
    voiceprint_ids = set()
    for index, raw_member in enumerate(raw_members):
        location = f"members[{index}]"
        if not isinstance(raw_member, Mapping):
            raise ToolInputError(f"{location} 必须是JSON对象")
        unknown_member_keys = set(raw_member) - _MEMBER_KEYS
        if unknown_member_keys:
            raise ToolInputError(
                f"{location} 包含未知字段: "
                + ", ".join(sorted(unknown_member_keys))
            )
        missing_keys = {
            "person_id",
            "display_name",
            "enabled",
        } - set(raw_member)
        if missing_keys:
            raise ToolInputError(
                f"{location} 缺少字段: "
                + ", ".join(sorted(missing_keys))
            )

        enabled = raw_member["enabled"]
        if not isinstance(enabled, bool):
            raise ToolInputError(f"{location}.enabled 必须是布尔值")
        validate_manifest_identifier(
            raw_member["person_id"],
            f"{location}.person_id",
        )
        validate_manifest_display_name(raw_member["display_name"])
        person = PersonIdentity(
            family_id=family_id,
            person_id=raw_member["person_id"],
            display_name=raw_member["display_name"],
            enabled=enabled,
        )
        if person.person_id in person_ids:
            raise ToolInputError(
                f"同一家庭存在重复person_id: {person.person_id}"
            )
        person_ids.add(person.person_id)

        raw_voiceprints = raw_member.get("voiceprint_ids", [])
        if not isinstance(raw_voiceprints, list):
            raise ToolInputError(
                f"{location}.voiceprint_ids 必须是列表"
            )
        checked_voiceprints: List[str] = []
        for voiceprint_index, voiceprint_id in enumerate(raw_voiceprints):
            validate_manifest_identifier(
                voiceprint_id,
                f"{location}.voiceprint_ids[{voiceprint_index}]",
            )
            if voiceprint_id in voiceprint_ids:
                raise ToolInputError(
                    f"清单存在重复voiceprint_id: {voiceprint_id}"
                )
            voiceprint_ids.add(voiceprint_id)
            checked_voiceprints.append(voiceprint_id)
        members.append(
            MemberSpec(person, tuple(checked_voiceprints))
        )

    return FamilyManifest(
        family_id=family_id,
        database_path=database_path,
        members=tuple(members),
    )


def plan_manifest(manifest: FamilyManifest) -> Dict[str, Any]:
    """只读计算清单变化；数据库不存在时也不会创建。"""

    plan = {
        "ok": True,
        "family_id": manifest.family_id,
        "database_path": str(manifest.database_path),
        "database_exists": manifest.database_path.is_file(),
        "persons": {
            "add": [],
            "unchanged": [],
            "rename": [],
            "enable": [],
            "disable": [],
        },
        "voiceprints": {
            "add": [],
            "unchanged": [],
            "conflicts": [],
        },
        "members": [],
        "errors": [],
    }

    repository = None
    if manifest.database_path.exists():
        if not manifest.database_path.is_file():
            plan["errors"].append("database_path不是普通文件")
        else:
            try:
                repository = SQLiteIdentityRepository(
                    manifest.database_path,
                    read_only=True,
                )
                schema = repository.inspect_schema()
                if schema["user_version"] != SCHEMA_VERSION:
                    plan["errors"].append(
                        "数据库schema版本不是当前支持版本"
                    )
                missing_tables = _EXPECTED_TABLES - set(schema["tables"])
                if missing_tables:
                    plan["errors"].append(
                        "数据库缺少表: "
                        + ", ".join(sorted(missing_tables))
                    )
            except Exception as exc:
                plan["errors"].append(
                    f"无法只读检查身份数据库: {exc}"
                )

    for member in manifest.members:
        person = member.person
        person_summary = {
            "person_id": person.person_id,
            "display_name": person.display_name,
            "enabled": person.enabled,
            "memory_user_id": person.memory_user_id,
            "voiceprint_ids": list(member.voiceprint_ids),
        }
        plan["members"].append(person_summary)

        existing_person = None
        if repository is not None and not plan["errors"]:
            try:
                existing_person = repository.get_person(
                    manifest.family_id,
                    person.person_id,
                )
            except Exception as exc:
                plan["errors"].append(
                    f"读取人员 {person.person_id} 失败: {exc}"
                )

        if existing_person is None:
            plan["persons"]["add"].append(person_summary)
        else:
            person_changed = False
            if existing_person.display_name != person.display_name:
                plan["persons"]["rename"].append(
                    {
                        "person_id": person.person_id,
                        "from": existing_person.display_name,
                        "to": person.display_name,
                        "memory_user_id": person.memory_user_id,
                    }
                )
                person_changed = True
            if existing_person.enabled != person.enabled:
                category = "enable" if person.enabled else "disable"
                plan["persons"][category].append(person_summary)
                person_changed = True
            if not person_changed:
                plan["persons"]["unchanged"].append(person_summary)

        for voiceprint_id in member.voiceprint_ids:
            binding = None
            if repository is not None and not plan["errors"]:
                try:
                    binding = repository.get_voiceprint_binding(
                        voiceprint_id
                    )
                except Exception as exc:
                    plan["errors"].append(
                        f"读取声纹 {voiceprint_id} 失败: {exc}"
                    )
            binding_summary = {
                "voiceprint_id": voiceprint_id,
                "person_id": person.person_id,
                "memory_user_id": person.memory_user_id,
            }
            if binding is None:
                plan["voiceprints"]["add"].append(binding_summary)
            elif (
                binding.family_id == manifest.family_id
                and binding.person_id == person.person_id
                and binding.active
            ):
                plan["voiceprints"]["unchanged"].append(
                    binding_summary
                )
            else:
                conflict = dict(binding_summary)
                conflict.update(
                    {
                        "reason": "voiceprint_id已被占用",
                        "revoked": not binding.active,
                    }
                )
                plan["voiceprints"]["conflicts"].append(conflict)

    if plan["voiceprints"]["conflicts"]:
        plan["errors"].append("存在voiceprint_id绑定冲突")
    plan["ok"] = not plan["errors"]
    plan["human_lines"] = _plan_human_lines(plan)
    return plan


def apply_manifest(
    manifest: FamilyManifest,
    *,
    confirm_family_id: str,
) -> Dict[str, Any]:
    """重新计划并以单事务应用清单。"""

    _require_confirmation(manifest.family_id, confirm_family_id)
    plan = plan_manifest(manifest)
    if not plan["ok"]:
        raise ToolOperationError(
            "plan存在阻塞错误，禁止apply: "
            + "; ".join(plan["errors"])
        )
    require_existing_parent(manifest.database_path)

    repository = SQLiteIdentityRepository(manifest.database_path)
    persons = [member.person for member in manifest.members]
    bindings = [
        (member.person.person_id, voiceprint_id)
        for member in manifest.members
        for voiceprint_id in member.voiceprint_ids
    ]
    repository.apply_family_manifest(
        manifest.family_id,
        persons,
        bindings,
    )

    final_persons = repository.list_persons(manifest.family_id)
    final_voiceprints = repository.list_voiceprints(manifest.family_id)
    changed_persons = sum(
        len(plan["persons"][category])
        for category in ("add", "rename", "enable", "disable")
    )
    added_voiceprints = len(plan["voiceprints"]["add"])
    skipped = (
        len(plan["persons"]["unchanged"])
        + len(plan["voiceprints"]["unchanged"])
    )
    result = {
        "ok": True,
        "family_id": manifest.family_id,
        "database_path": str(manifest.database_path),
        "success": {
            "person_changes": changed_persons,
            "voiceprint_bindings": added_voiceprints,
        },
        "skipped": skipped,
        "failed": [],
        "final_person_count": len(final_persons),
        "final_active_voiceprint_count": sum(
            binding.active for binding in final_voiceprints
        ),
    }
    if changed_persons == 0 and added_voiceprints == 0:
        status_line = "无需修改"
    else:
        status_line = "清单已原子应用"
    result["human_lines"] = [
        f"数据库：{manifest.database_path}",
        f"家庭：{manifest.family_id}",
        status_line,
        f"最终人员数量：{result['final_person_count']}",
        "最终有效声纹绑定数量："
        f"{result['final_active_voiceprint_count']}",
    ]
    return result


def list_family(
    family_id: str,
    database_path: Path,
) -> Dict[str, Any]:
    """只读列出指定家庭的人员和全部声纹历史。"""

    validate_manifest_identifier(family_id, "family_id")
    if not database_path.is_file():
        result = {
            "ok": True,
            "family_id": family_id,
            "database_path": str(database_path),
            "persons": [],
        }
        result["human_lines"] = [
            f"数据库：{database_path}",
            "身份数据库尚未初始化，人员数量：0",
        ]
        return result

    repository = SQLiteIdentityRepository(database_path, read_only=True)
    persons = repository.list_persons(family_id)
    bindings = repository.list_voiceprints(family_id)
    by_person: Dict[str, List[VoiceprintBinding]] = {}
    for binding in bindings:
        by_person.setdefault(binding.person_id, []).append(binding)

    output_persons = []
    for person in persons:
        person_bindings = by_person.get(person.person_id, [])
        active = [
            binding.voiceprint_id
            for binding in person_bindings
            if binding.active
        ]
        revoked = [
            binding.voiceprint_id
            for binding in person_bindings
            if not binding.active
        ]
        output_persons.append(
            {
                "person_id": person.person_id,
                "display_name": person.display_name,
                "enabled": person.enabled,
                "memory_user_id": person.memory_user_id,
                "active_voiceprint_ids": active,
                "revoked_voiceprint_ids": revoked,
                "voiceprint_count": len(person_bindings),
            }
        )
    result = {
        "ok": True,
        "family_id": family_id,
        "database_path": str(database_path),
        "persons": output_persons,
    }
    lines = [f"数据库：{database_path}", f"家庭：{family_id}"]
    for person in output_persons:
        lines.append(
            f"- {person['person_id']} | {person['display_name']} | "
            f"enabled={str(person['enabled']).lower()} | "
            f"{person['memory_user_id']} | "
            f"有效声纹={person['active_voiceprint_ids']} | "
            f"已撤销={person['revoked_voiceprint_ids']}"
        )
    lines.append(f"人员数量：{len(output_persons)}")
    result["human_lines"] = lines
    return result


def person_upsert(
    *,
    family_id: str,
    person_id: str,
    display_name: str,
    enabled: bool,
    database_path: Path,
    confirm_family_id: str,
) -> Dict[str, Any]:
    _require_confirmation(family_id, confirm_family_id)
    validate_manifest_identifier(person_id, "person_id")
    validate_manifest_display_name(display_name)
    require_existing_parent(database_path)
    person = PersonIdentity(
        family_id,
        person_id,
        display_name,
        enabled,
    )
    repository = SQLiteIdentityRepository(database_path)
    repository.save_person(person)
    return _person_result("人员已新增或更新", person, database_path)


def person_rename(
    *,
    family_id: str,
    person_id: str,
    display_name: str,
    database_path: Path,
    confirm_family_id: str,
) -> Dict[str, Any]:
    _require_confirmation(family_id, confirm_family_id)
    validate_manifest_identifier(person_id, "person_id")
    validate_manifest_display_name(display_name)
    repository = _writable_existing_repository(database_path)
    existing = repository.get_person(family_id, person_id)
    if existing is None:
        raise ToolOperationError(f"人员不存在: {person_id}")
    renamed = PersonIdentity(
        family_id,
        person_id,
        display_name,
        existing.enabled,
    )
    repository.save_person(renamed)
    return _person_result("显示名称已修改", renamed, database_path)


def person_set_enabled(
    *,
    family_id: str,
    person_id: str,
    enabled: bool,
    database_path: Path,
    confirm_family_id: str,
) -> Dict[str, Any]:
    _require_confirmation(family_id, confirm_family_id)
    repository = _writable_existing_repository(database_path)
    repository.set_person_enabled(family_id, person_id, enabled)
    person = repository.get_person(family_id, person_id)
    message = "人员已启用" if enabled else "人员已停用"
    return _person_result(message, person, database_path)


def person_show(
    family_id: str,
    person_id: str,
    database_path: Path,
) -> Dict[str, Any]:
    validate_manifest_identifier(family_id, "family_id")
    validate_manifest_identifier(person_id, "person_id")
    if not database_path.is_file():
        raise ToolOperationError("身份数据库尚未初始化")
    repository = SQLiteIdentityRepository(database_path, read_only=True)
    person = repository.get_person(family_id, person_id)
    if person is None:
        raise ToolOperationError(f"人员不存在: {person_id}")
    result = list_family(family_id, database_path)
    result["persons"] = [
        item
        for item in result["persons"]
        if item["person_id"] == person_id
    ]
    result["human_lines"] = [
        f"数据库：{database_path}",
        f"人员：{person.person_id}",
        f"显示名称：{person.display_name}",
        f"enabled={str(person.enabled).lower()}",
        f"memory_user_id={person.memory_user_id}",
    ]
    return result


def voiceprint_bind(
    *,
    family_id: str,
    person_id: str,
    voiceprint_id: str,
    database_path: Path,
    confirm_family_id: str,
) -> Dict[str, Any]:
    _require_confirmation(family_id, confirm_family_id)
    validate_manifest_identifier(person_id, "person_id")
    validate_manifest_identifier(voiceprint_id, "voiceprint_id")
    repository = _writable_existing_repository(database_path)
    repository.bind_voiceprint(family_id, person_id, voiceprint_id)
    return _voiceprint_result(
        "声纹绑定已确认",
        family_id,
        person_id,
        voiceprint_id,
        database_path,
    )


def voiceprint_revoke(
    *,
    family_id: str,
    voiceprint_id: str,
    database_path: Path,
    confirm_family_id: str,
) -> Dict[str, Any]:
    _require_confirmation(family_id, confirm_family_id)
    validate_manifest_identifier(voiceprint_id, "voiceprint_id")
    repository = _writable_existing_repository(database_path)
    binding = repository.get_voiceprint_binding(voiceprint_id)
    if binding is None or binding.family_id != family_id:
        raise ToolOperationError("指定家庭不存在该声纹绑定")
    repository.revoke_voiceprint(family_id, voiceprint_id)
    return _voiceprint_result(
        "声纹绑定已撤销",
        family_id,
        binding.person_id,
        voiceprint_id,
        database_path,
    )


def voiceprint_replace(
    *,
    family_id: str,
    person_id: str,
    old_voiceprint_id: str,
    new_voiceprint_id: str,
    database_path: Path,
    confirm_family_id: str,
) -> Dict[str, Any]:
    _require_confirmation(family_id, confirm_family_id)
    validate_manifest_identifier(person_id, "person_id")
    validate_manifest_identifier(
        old_voiceprint_id,
        "old_voiceprint_id",
    )
    validate_manifest_identifier(
        new_voiceprint_id,
        "new_voiceprint_id",
    )
    repository = _writable_existing_repository(database_path)
    repository.replace_voiceprint(
        family_id,
        person_id,
        old_voiceprint_id,
        new_voiceprint_id,
    )
    result = _voiceprint_result(
        "声纹绑定已原子替换",
        family_id,
        person_id,
        new_voiceprint_id,
        database_path,
    )
    result["old_voiceprint_id"] = old_voiceprint_id
    return result


def voiceprint_list(
    family_id: str,
    database_path: Path,
    *,
    person_id: Optional[str] = None,
) -> Dict[str, Any]:
    validate_manifest_identifier(family_id, "family_id")
    if person_id is not None:
        validate_manifest_identifier(person_id, "person_id")
    if not database_path.is_file():
        result = {
            "ok": True,
            "family_id": family_id,
            "database_path": str(database_path),
            "voiceprints": [],
            "human_lines": [
                f"数据库：{database_path}",
                "身份数据库尚未初始化，声纹绑定数量：0",
            ],
        }
        return result
    repository = SQLiteIdentityRepository(database_path, read_only=True)
    bindings = repository.list_voiceprints(family_id, person_id)
    items = [
        {
            "voiceprint_id": binding.voiceprint_id,
            "person_id": binding.person_id,
            "active": binding.active,
            "revoked_at": binding.revoked_at,
        }
        for binding in bindings
    ]
    return {
        "ok": True,
        "family_id": family_id,
        "database_path": str(database_path),
        "voiceprints": items,
        "human_lines": [
            f"数据库：{database_path}",
            *[
                f"- {item['voiceprint_id']} -> {item['person_id']} | "
                f"active={str(item['active']).lower()}"
                for item in items
            ],
            f"声纹绑定数量：{len(items)}",
        ],
    }


def _require_confirmation(
    family_id: str,
    confirm_family_id: str,
) -> None:
    validate_manifest_identifier(family_id, "family_id")
    if confirm_family_id != family_id:
        raise ToolInputError(
            "confirm-family-id必须与family_id精确一致"
        )


def _writable_existing_repository(
    database_path: Path,
) -> SQLiteIdentityRepository:
    if not database_path.is_file():
        raise ToolOperationError("身份数据库尚未初始化")
    return SQLiteIdentityRepository(database_path)


def _person_result(
    message: str,
    person: PersonIdentity,
    database_path: Path,
) -> Dict[str, Any]:
    return {
        "ok": True,
        "database_path": str(database_path),
        "person": {
            "family_id": person.family_id,
            "person_id": person.person_id,
            "display_name": person.display_name,
            "enabled": person.enabled,
            "memory_user_id": person.memory_user_id,
        },
        "human_lines": [
            f"数据库：{database_path}",
            message,
            f"memory_user_id={person.memory_user_id}",
        ],
    }


def _voiceprint_result(
    message: str,
    family_id: str,
    person_id: str,
    voiceprint_id: str,
    database_path: Path,
) -> Dict[str, Any]:
    return {
        "ok": True,
        "database_path": str(database_path),
        "family_id": family_id,
        "person_id": person_id,
        "voiceprint_id": voiceprint_id,
        "memory_user_id": build_memory_user_id(
            family_id,
            person_id,
        ),
        "human_lines": [
            f"数据库：{database_path}",
            message,
            f"{voiceprint_id} -> {family_id}:{person_id}",
        ],
    }


def _plan_human_lines(plan: Mapping[str, Any]) -> List[str]:
    lines = [
        f"数据库：{plan['database_path']}",
        f"家庭：{plan['family_id']}",
        f"新增人员：{len(plan['persons']['add'])}",
        f"无变化人员：{len(plan['persons']['unchanged'])}",
        f"改名人员：{len(plan['persons']['rename'])}",
        f"启用人员：{len(plan['persons']['enable'])}",
        f"停用人员：{len(plan['persons']['disable'])}",
        f"新增声纹：{len(plan['voiceprints']['add'])}",
        f"无变化声纹：{len(plan['voiceprints']['unchanged'])}",
        f"冲突声纹：{len(plan['voiceprints']['conflicts'])}",
    ]
    for member in plan["members"]:
        lines.append(
            f"- {member['person_id']} -> {member['memory_user_id']}"
        )
    for error in plan["errors"]:
        lines.append(f"[阻塞] {error}")
    lines.append("可以apply" if plan["ok"] else "禁止apply")
    return lines
