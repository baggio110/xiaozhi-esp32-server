"""家庭记忆配置与服务生命周期的最小集成测试。"""

import asyncio
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.family_identity import (
    FamilyMemoryConfigurationError,
    InvalidDatabasePathError,
)

from .project_temp import ProjectTemporaryDirectory

APP_PATH = Path(__file__).resolve().parents[2] / "app.py"


class FakeLogger:
    """记录日志调用，不读取真实日志配置。"""

    def __init__(self) -> None:
        self.errors = []

    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        self.errors.append((args, kwargs))


def load_isolated_app():
    """用测试替身加载 app.py，避免导入或启动外部服务。"""

    logger = FakeLogger()

    async def idle_input():
        await asyncio.Future()

    async def load_config():
        return {}

    class IdleServer:
        def __init__(self, config):
            self.config = config

        async def start(self):
            await asyncio.Future()

    class FakeGcManager:
        async def start(self):
            return None

        async def stop(self):
            return None

    stubs = {
        "aioconsole": types.SimpleNamespace(ainput=idle_input),
        "config.settings": types.SimpleNamespace(load_config=load_config),
        "config.logger": types.SimpleNamespace(
            setup_logging=lambda config=None: logger
        ),
        "core.utils.util": types.SimpleNamespace(
            check_ffmpeg_installed=lambda: None,
            get_local_ip=lambda: "127.0.0.1",
            validate_mcp_endpoint=lambda endpoint: True,
        ),
        "core.http_server": types.SimpleNamespace(
            SimpleHttpServer=IdleServer
        ),
        "core.websocket_server": types.SimpleNamespace(
            WebSocketServer=IdleServer
        ),
        "core.utils.gc_manager": types.SimpleNamespace(
            get_gc_manager=lambda interval_seconds: FakeGcManager()
        ),
    }
    module_name = "family_memory_app_under_test"
    spec = importlib.util.spec_from_file_location(module_name, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module, logger


class FamilyMemoryAppConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = ProjectTemporaryDirectory()
        self.server_root = (
            Path(self.temporary_directory.name) / "xiaozhi-server"
        )
        self.server_root.mkdir()
        self.app, self.logger = load_isolated_app()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_missing_node_defaults_to_disabled(self):
        runtime = self.app.start_family_memory_runtime(
            {},
            self.server_root,
        )

        self.assertFalse(runtime.enabled)
        self.assertIsNone(runtime.repository)
        self.assertFalse((self.server_root / "data").exists())

    def test_explicit_disabled_config_has_no_database_side_effect(self):
        runtime = self.app.start_family_memory_runtime(
            {
                "family_memory": {
                    "enabled": False,
                    "family_id": "",
                    "database_path": "data/family_identity.db",
                }
            },
            self.server_root,
        )

        self.assertFalse(runtime.is_active)
        self.assertFalse((self.server_root / "data").exists())

    def test_enabled_empty_family_id_fails_and_logs(self):
        with self.assertRaises(FamilyMemoryConfigurationError):
            self.app.start_family_memory_runtime(
                {
                    "family_memory": {
                        "enabled": True,
                        "family_id": "",
                        "database_path": "data/family_identity.db",
                    }
                },
                self.server_root,
            )

        self.assertEqual(1, len(self.logger.errors))

    def test_enabled_valid_config_creates_database_under_server_root(self):
        runtime = self.app.start_family_memory_runtime(
            {
                "family_memory": {
                    "enabled": True,
                    "family_id": "family_001",
                    "database_path": "data/family_identity.db",
                }
            },
            self.server_root,
        )

        expected_path = (
            self.server_root / "data" / "family_identity.db"
        )
        self.assertEqual(expected_path, runtime.database_path)
        self.assertTrue(expected_path.exists())

    def test_absolute_database_path_fails_startup(self):
        absolute_path = self.server_root / "family_identity.db"

        with self.assertRaises(InvalidDatabasePathError):
            self.app.start_family_memory_runtime(
                {
                    "family_memory": {
                        "enabled": True,
                        "family_id": "family_001",
                        "database_path": str(absolute_path),
                    }
                },
                self.server_root,
            )

    def test_database_path_escape_fails_startup(self):
        with self.assertRaises(InvalidDatabasePathError):
            self.app.start_family_memory_runtime(
                {
                    "family_memory": {
                        "enabled": True,
                        "family_id": "family_001",
                        "database_path": "../family_identity.db",
                    }
                },
                self.server_root,
            )

    def test_config_parsing_ignores_environment_and_device_identity(self):
        config = {
            "device_id": "device_family",
            "agent_id": "agent_family",
        }

        with patch.dict(
            os.environ,
            {"FAMILY_ID": "environment_family"},
        ):
            runtime = self.app.start_family_memory_runtime(
                config,
                self.server_root,
            )

        self.assertFalse(runtime.enabled)
        self.assertIsNone(runtime.family_id)
        self.assertFalse((self.server_root / "data").exists())

    def test_close_keeps_enabled_database(self):
        runtime = self.app.start_family_memory_runtime(
            {
                "family_memory": {
                    "enabled": True,
                    "family_id": "family_001",
                    "database_path": "data/family_identity.db",
                }
            },
            self.server_root,
        )
        database_path = runtime.database_path

        runtime.close()

        self.assertTrue(database_path.exists())

    def test_repeated_start_reuses_repository(self):
        runtime = self.app.start_family_memory_runtime(
            {
                "family_memory": {
                    "enabled": True,
                    "family_id": "family_001",
                    "database_path": "data/family_identity.db",
                }
            },
            self.server_root,
        )
        first_repository = runtime.repository

        second_repository = runtime.start()

        self.assertIs(first_repository, second_repository)

    def test_runtime_start_does_not_read_voiceprint_threshold(self):
        class UnreadableVoiceprintConfig:
            def get(self, *args, **kwargs):
                raise AssertionError("Runtime 不得读取声纹阈值")

        runtime = self.app.start_family_memory_runtime(
            {
                "family_memory": {
                    "enabled": True,
                    "family_id": "family_001",
                    "database_path": "data/family_identity.db",
                },
                "voiceprint": UnreadableVoiceprintConfig(),
            },
            self.server_root,
        )

        self.assertTrue(runtime.is_active)
        self.assertFalse(hasattr(runtime, "_min_confidence"))


class FamilyMemoryAppLifecycleTest(unittest.TestCase):
    def test_runtime_is_passed_only_to_websocket_server_and_closed(self):
        app, _ = load_isolated_app()
        events = []
        server_arguments = []

        class FakeRuntime:
            def __init__(
                self,
                settings,
                server_root,
            ):
                events.append(
                    (
                        "runtime_init",
                        settings,
                        server_root,
                    )
                )

            def start(self):
                events.append(("runtime_start",))
                return None

            def close(self):
                events.append(("runtime_close",))

        class FakeServer:
            def __init__(self, *args, **kwargs):
                server_arguments.append((args, kwargs))

            async def start(self):
                await asyncio.Future()

        class FakeGcManager:
            async def start(self):
                events.append(("gc_start",))

            async def stop(self):
                events.append(("gc_stop",))

        async def load_config():
            return {
                "server": {
                    "auth_key": "test-auth-key",
                    "http_port": 8003,
                    "port": 8000,
                },
                "manager-api": {},
            }

        async def exit_immediately():
            return None

        app.FamilyMemoryRuntime = FakeRuntime
        app.WebSocketServer = FakeServer
        app.SimpleHttpServer = FakeServer
        app.get_gc_manager = (
            lambda interval_seconds: FakeGcManager()
        )
        app.load_config = load_config
        app.wait_for_exit = exit_immediately

        asyncio.run(app.main())

        event_names = [event[0] for event in events]
        runtime_init = next(
            event for event in events if event[0] == "runtime_init"
        )
        self.assertFalse(runtime_init[1].enabled)
        self.assertEqual(app.SERVER_ROOT, runtime_init[2])
        self.assertEqual(1, event_names.count("runtime_start"))
        self.assertEqual(1, event_names.count("runtime_close"))
        self.assertEqual(1, event_names.count("gc_start"))
        self.assertEqual(1, event_names.count("gc_stop"))
        self.assertEqual(2, len(server_arguments))
        websocket_arguments = server_arguments[0]
        http_arguments = server_arguments[1]
        self.assertEqual(1, len(websocket_arguments[0]))
        self.assertIsInstance(
            websocket_arguments[1]["family_memory_runtime"],
            FakeRuntime,
        )
        self.assertEqual((1, 0), (
            len(http_arguments[0]),
            len(http_arguments[1]),
        ))
