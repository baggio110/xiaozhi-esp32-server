"""家庭记忆管理工具统一命令行入口。"""

import argparse
from pathlib import Path
from typing import Optional, Sequence

from . import admin
from .common import (
    SERVER_ROOT,
    command_error_result,
    emit_result,
    parse_bool,
    resolve_cli_database_path,
)
from .preflight import run_preflight


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.family_memory.cli",
        description="家庭成员与官方声纹ID映射管理及部署预检",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("--family-id")
    preflight_parser.add_argument("--database-path", required=True)
    preflight_parser.add_argument("--enabled", required=True)
    _add_json(preflight_parser)

    list_parser = commands.add_parser("list")
    list_parser.add_argument(
        "resource",
        nargs="?",
        default="persons",
        choices=("persons",),
    )
    _add_family_and_database(list_parser)
    _add_json(list_parser)

    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--file", required=True)
    plan_parser.add_argument("--database-path")
    _add_json(plan_parser)

    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--file", required=True)
    apply_parser.add_argument("--database-path")
    apply_parser.add_argument("--confirm-family-id", required=True)
    _add_json(apply_parser)

    person_parser = commands.add_parser("person")
    person_commands = person_parser.add_subparsers(
        dest="person_command",
        required=True,
    )
    upsert = person_commands.add_parser("upsert")
    _add_family_and_database(upsert)
    upsert.add_argument("--person-id", required=True)
    upsert.add_argument("--display-name", required=True)
    upsert.add_argument("--enabled", required=True)
    _add_confirmation(upsert)
    _add_json(upsert)

    rename = person_commands.add_parser("rename")
    _add_family_and_database(rename)
    rename.add_argument("--person-id", required=True)
    rename.add_argument("--display-name", required=True)
    _add_confirmation(rename)
    _add_json(rename)

    for command_name in ("enable", "disable"):
        command_parser = person_commands.add_parser(command_name)
        _add_family_and_database(command_parser)
        command_parser.add_argument("--person-id", required=True)
        _add_confirmation(command_parser)
        _add_json(command_parser)

    show = person_commands.add_parser("show")
    _add_family_and_database(show)
    show.add_argument("--person-id", required=True)
    _add_json(show)

    voiceprint_parser = commands.add_parser("voiceprint")
    voiceprint_commands = voiceprint_parser.add_subparsers(
        dest="voiceprint_command",
        required=True,
    )

    bind = voiceprint_commands.add_parser("bind")
    _add_family_and_database(bind)
    bind.add_argument("--person-id", required=True)
    bind.add_argument("--voiceprint-id", required=True)
    _add_confirmation(bind)
    _add_json(bind)

    revoke = voiceprint_commands.add_parser("revoke")
    _add_family_and_database(revoke)
    revoke.add_argument("--voiceprint-id", required=True)
    _add_confirmation(revoke)
    _add_json(revoke)

    replace = voiceprint_commands.add_parser("replace")
    _add_family_and_database(replace)
    replace.add_argument("--person-id", required=True)
    replace.add_argument("--old-voiceprint-id", required=True)
    replace.add_argument("--new-voiceprint-id", required=True)
    _add_confirmation(replace)
    _add_json(replace)

    voiceprint_list = voiceprint_commands.add_parser("list")
    _add_family_and_database(voiceprint_list)
    voiceprint_list.add_argument("--person-id")
    _add_json(voiceprint_list)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = getattr(args, "json", False)
    try:
        result = _dispatch(args)
    except Exception as exc:
        emit_result(command_error_result(str(exc)), as_json=as_json)
        return 1
    emit_result(result, as_json=as_json)
    return 0 if result.get("ok", False) else 1


def _dispatch(args) -> dict:
    if args.command == "preflight":
        return run_preflight(
            enabled=args.enabled,
            family_id=args.family_id,
            database_path=args.database_path,
        )
    if args.command == "plan":
        manifest = admin.load_manifest(
            Path(args.file),
            database_path_override=args.database_path,
        )
        return admin.plan_manifest(manifest)
    if args.command == "apply":
        manifest = admin.load_manifest(
            Path(args.file),
            database_path_override=args.database_path,
        )
        return admin.apply_manifest(
            manifest,
            confirm_family_id=args.confirm_family_id,
        )

    database_path = resolve_cli_database_path(
        args.database_path,
        server_root=SERVER_ROOT,
    )
    if args.command == "list":
        return admin.list_family(args.family_id, database_path)

    if args.command == "person":
        if args.person_command == "upsert":
            return admin.person_upsert(
                family_id=args.family_id,
                person_id=args.person_id,
                display_name=args.display_name,
                enabled=parse_bool(args.enabled),
                database_path=database_path,
                confirm_family_id=args.confirm_family_id,
            )
        if args.person_command == "rename":
            return admin.person_rename(
                family_id=args.family_id,
                person_id=args.person_id,
                display_name=args.display_name,
                database_path=database_path,
                confirm_family_id=args.confirm_family_id,
            )
        if args.person_command in ("enable", "disable"):
            return admin.person_set_enabled(
                family_id=args.family_id,
                person_id=args.person_id,
                enabled=args.person_command == "enable",
                database_path=database_path,
                confirm_family_id=args.confirm_family_id,
            )
        return admin.person_show(
            args.family_id,
            args.person_id,
            database_path,
        )

    if args.voiceprint_command == "bind":
        return admin.voiceprint_bind(
            family_id=args.family_id,
            person_id=args.person_id,
            voiceprint_id=args.voiceprint_id,
            database_path=database_path,
            confirm_family_id=args.confirm_family_id,
        )
    if args.voiceprint_command == "revoke":
        return admin.voiceprint_revoke(
            family_id=args.family_id,
            voiceprint_id=args.voiceprint_id,
            database_path=database_path,
            confirm_family_id=args.confirm_family_id,
        )
    if args.voiceprint_command == "replace":
        return admin.voiceprint_replace(
            family_id=args.family_id,
            person_id=args.person_id,
            old_voiceprint_id=args.old_voiceprint_id,
            new_voiceprint_id=args.new_voiceprint_id,
            database_path=database_path,
            confirm_family_id=args.confirm_family_id,
        )
    return admin.voiceprint_list(
        args.family_id,
        database_path,
        person_id=args.person_id,
    )


def _add_family_and_database(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--family-id", required=True)
    parser.add_argument("--database-path", required=True)


def _add_confirmation(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirm-family-id", required=True)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


if __name__ == "__main__":
    raise SystemExit(main())
