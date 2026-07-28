"""家庭记忆内部管理编排器回归测试。"""

import ast
import asyncio
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.api.family_memory_handler import FamilyMemoryAdminHandler
from core.family_identity import (
    SQLiteIdentityRepository,
    VoiceprintAlreadyBoundError,
)
from tools.family_memory.common import ToolInputError, ToolOperationError

from .project_temp import ProjectTemporaryDirectory


SERVER_MESSAGE_HANDLER = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "handle"
    / "textHandler"
    / "serverMessageHandler.py"
)


def load_server_message_handler():
    """隔离未参与本测试的可选设备MCP音频依赖。"""

    module_name = "core.providers.tools.device_mcp"
    stub = types.ModuleType(module_name)

    async def handle_mcp_message(*_args, **_kwargs):
        return None

    stub.handle_mcp_message = handle_mcp_message
    with patch.dict(sys.modules, {module_name: stub}):
        module = importlib.import_module(
            "core.handle.textHandler.serverMessageHandler"
        )
    return module.ServerTextMessageHandler


def passing_preflight(**_kwargs):
    return {
        "ok": True,
        "overall": "PASS",
        "checks": [],
        "summary": {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0},
        "human_lines": ["总体：可以继续初始化"],
    }


class FamilyMemoryAdminHandlerTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = ProjectTemporaryDirectory()
        self.server_root = Path(self.temporary_directory.name)
        (self.server_root / "data").mkdir()
        self.database_path = (
            self.server_root / "data" / "family_identity.db"
        )
        self.handler = FamilyMemoryAdminHandler(
            self.server_root,
            preflight_runner=passing_preflight,
        )
        self.settings = {
            "enabled": False,
            "family_id": "family_a",
            "database_path": "data/family_identity.db",
        }

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_preflight_is_read_only_and_reports_empty_counts(self):
        result = self.handler.handle("preflight", self.settings)

        self.assertTrue(result["can_enable"])
        self.assertEqual(0, result["member_count"])
        self.assertEqual(0, result["active_voiceprint_count"])
        self.assertFalse(self.database_path.exists())

    def test_create_arbitrary_people_with_chinese_duplicate_names(self):
        first = self.handler.handle(
            "person_create",
            self.settings,
            {"person_id": "person_a", "display_name": "小明"},
        )
        second = self.handler.handle(
            "person_create",
            self.settings,
            {"person_id": "person_b", "display_name": "小明"},
        )
        listed = self.handler.handle("person_list", self.settings)

        self.assertEqual("family_a:person_a", first["person"]["memory_user_id"])
        self.assertEqual("family_a:person_b", second["person"]["memory_user_id"])
        self.assertEqual(
            {"person_a", "person_b"},
            {person["person_id"] for person in listed["persons"]},
        )

    def test_rename_and_enable_state_keep_memory_user_id(self):
        self._create_person("person_a", "原名称")

        renamed = self.handler.handle(
            "person_rename",
            self.settings,
            {"person_id": "person_a", "display_name": "新 名称"},
        )
        disabled = self.handler.handle(
            "person_set_enabled",
            self.settings,
            {"person_id": "person_a", "enabled": False},
        )

        self.assertEqual(
            "family_a:person_a",
            renamed["person"]["memory_user_id"],
        )
        self.assertEqual(
            "family_a:person_a",
            disabled["person"]["memory_user_id"],
        )
        self.assertFalse(disabled["person"]["enabled"])

    def test_create_rejects_existing_person_id_without_renaming(self):
        self._create_person("person_a", "原名称")

        with self.assertRaises(ToolOperationError):
            self._create_person("person_a", "覆盖名称")

        shown = self.handler.handle(
            "person_show",
            self.settings,
            {"person_id": "person_a"},
        )
        self.assertEqual("原名称", shown["persons"][0]["display_name"])

    def test_bind_revoke_and_replace_use_existing_repository_operations(self):
        self._create_person("person_a", "成员")
        self.handler.handle(
            "voiceprint_bind",
            self.settings,
            {"person_id": "person_a", "voiceprint_id": "voice_old"},
        )
        replaced = self.handler.handle(
            "voiceprint_replace",
            self.settings,
            {
                "person_id": "person_a",
                "old_voiceprint_id": "voice_old",
                "new_voiceprint_id": "voice_new",
            },
        )
        self.handler.handle(
            "voiceprint_revoke",
            self.settings,
            {"voiceprint_id": "voice_new"},
        )
        listed = self.handler.handle("voiceprint_list", self.settings)

        self.assertEqual("voice_old", replaced["old_voiceprint_id"])
        self.assertEqual(
            [
                ("voice_old", False),
                ("voice_new", False),
            ],
            [
                (item["voiceprint_id"], item["active"])
                for item in listed["voiceprints"]
            ],
        )

    def test_cross_family_list_is_scoped_and_conflict_hides_owner(self):
        repository = SQLiteIdentityRepository(self.database_path)
        other_settings = dict(self.settings, family_id="family_b")
        other_handler = self.handler
        other_handler.handle(
            "person_create",
            other_settings,
            {"person_id": "same_id", "display_name": "其他家庭私密名称"},
        )
        other_handler.handle(
            "voiceprint_bind",
            other_settings,
            {"person_id": "same_id", "voiceprint_id": "occupied_voice"},
        )
        self._create_person("same_id", "本家庭成员")

        listed = self.handler.handle("person_list", self.settings)
        with self.assertRaises(VoiceprintAlreadyBoundError) as raised:
            self.handler.handle(
                "voiceprint_bind",
                self.settings,
                {
                    "person_id": "same_id",
                    "voiceprint_id": "occupied_voice",
                },
            )

        self.assertEqual(["本家庭成员"], [
            person["display_name"] for person in listed["persons"]
        ])
        self.assertNotIn("family_b", str(raised.exception))
        self.assertNotIn("其他家庭私密名称", str(raised.exception))
        binding = repository.get_voiceprint_binding("occupied_voice")
        self.assertEqual("family_b", binding.family_id)

    def test_invalid_path_and_unknown_operation_fail_before_database_write(self):
        invalid = dict(
            self.settings,
            database_path="../data/family_identity.db",
        )

        with self.assertRaises(ValueError):
            self.handler.handle("preflight", invalid)
        with self.assertRaises(ToolInputError):
            self.handler.handle("physical_delete", self.settings)

        self.assertFalse(self.database_path.exists())

    def test_person_write_requires_configured_family_id(self):
        missing_family = dict(self.settings, family_id=None)

        with self.assertRaises(ToolInputError):
            self.handler.handle(
                "person_create",
                missing_family,
                {"person_id": "person_a", "display_name": "成员"},
            )

        self.assertFalse(self.database_path.exists())

    def _create_person(self, person_id, display_name):
        return self.handler.handle(
            "person_create",
            self.settings,
            {"person_id": person_id, "display_name": display_name},
        )


class FamilyMemoryServerMessageSecurityContractTest(unittest.TestCase):
    class _WebSocket:
        def __init__(self):
            self.messages = []

        async def send(self, message):
            self.messages.append(json.loads(message))

    class _AdminHandler:
        def __init__(self):
            self.calls = 0

        def handle(self, operation, settings, payload):
            self.calls += 1
            return {"operation": operation}

    def test_ordinary_device_is_rejected_even_with_correct_secret(self):
        websocket = self._WebSocket()
        admin = self._AdminHandler()
        connection = SimpleNamespace(
            read_config_from_api=True,
            family_memory_admin_authorized=False,
            config={"manager-api": {"secret": "server-secret"}},
            websocket=websocket,
            server=SimpleNamespace(family_memory_admin=admin),
        )

        handler_class = load_server_message_handler()
        asyncio.run(handler_class().handle(
            connection,
            {
                "action": "family_memory",
                "content": {
                    "secret": "server-secret",
                    "request_id": "request-1",
                    "operation": "preflight",
                    "settings": {},
                    "payload": {},
                },
            },
        ))

        self.assertEqual(0, admin.calls)
        self.assertEqual("fail", websocket.messages[0]["status"])
        self.assertNotIn("server-secret", json.dumps(websocket.messages))

    def test_internal_admin_connection_still_requires_correct_secret(self):
        websocket = self._WebSocket()
        admin = self._AdminHandler()
        connection = SimpleNamespace(
            read_config_from_api=True,
            family_memory_admin_authorized=True,
            config={"manager-api": {"secret": "server-secret"}},
            websocket=websocket,
            server=SimpleNamespace(family_memory_admin=admin),
        )

        handler_class = load_server_message_handler()
        asyncio.run(handler_class().handle(
            connection,
            {
                "action": "family_memory",
                "content": {
                    "secret": "wrong-secret",
                    "request_id": "request-2",
                    "operation": "preflight",
                },
            },
        ))

        self.assertEqual(0, admin.calls)
        self.assertEqual("error", websocket.messages[0]["status"])
        self.assertNotIn("server-secret", json.dumps(websocket.messages))

    def test_family_admin_dispatch_occurs_after_server_secret_validation(self):
        source = SERVER_MESSAGE_HANDLER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        handle = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "handle"
        )
        secret_compare_line = next(
            node.lineno
            for node in ast.walk(handle)
            if isinstance(node, ast.Compare)
            and "post_secret" in ast.unparse(node)
        )
        family_dispatch_line = next(
            node.lineno
            for node in ast.walk(handle)
            if isinstance(node, ast.Await)
            and "_handle_family_memory" in ast.unparse(node)
        )

        self.assertLess(secret_compare_line, family_dispatch_line)
        self.assertIn("INVALID_REQUEST", source)
        self.assertIn("OPERATION_FAILED", source)
        self.assertEqual(4, source.count('"status": "fail"'))


if __name__ == "__main__":
    unittest.main()
