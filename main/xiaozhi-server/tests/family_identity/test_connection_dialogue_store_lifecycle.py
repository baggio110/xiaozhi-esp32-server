"""B4a2a ConnectionHandler 家庭 Dialogue Store 生命周期测试。"""

import ast
import asyncio
import copy
import queue
import threading
import types
import unittest
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import Mock

from core.family_identity import FamilySessionDialogueStore


SERVER_ROOT = Path(__file__).resolve().parents[2]
CONNECTION_PATH = SERVER_ROOT / "core" / "connection.py"


class FakeLogger:
    """记录关闭流程的错误，不输出真实日志。"""

    def __init__(self):
        self.errors = []

    def bind(self, **kwargs):
        return self

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def error(self, message):
        self.errors.append(str(message))


class FakePromptManager:
    """避免初始化真实提示词组件。"""

    def __init__(self, config, logger):
        self.config = config
        self.logger = logger


class CountingDialogue:
    """区分官方 Dialogue 创建与 Store 工厂惰性创建。"""

    instances = []

    def __init__(self):
        self.__class__.instances.append(self)
        self.dialogue = []


class FakeRuntime:
    """只公开 B4a2a 允许读取的 Runtime 活跃状态。"""

    def __init__(self, is_active):
        self.is_active = is_active

    def __getattr__(self, attribute):
        raise AssertionError(f"B4a2a 不得访问 Runtime.{attribute}")


def load_connection_lifecycle():
    """从生产源码加载构造与关闭方法，隔离外部业务依赖。"""

    tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
    connection_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ConnectionHandler"
    )
    methods = {
        node.name: node
        for node in connection_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"__init__", "close"}
    }
    module = ast.Module(
        body=[methods["__init__"], methods["close"]],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)

    namespace = {
        "Any": Any,
        "Dict": Dict,
        "Optional": Optional,
        "copy": copy,
        "uuid": uuid,
        "asyncio": asyncio,
        "threading": threading,
        "ThreadPoolExecutor": ThreadPoolExecutor,
        "queue": queue,
        "deque": deque,
        "setup_logging": FakeLogger,
        "PromptManager": FakePromptManager,
        "Dialogue": CountingDialogue,
        "FamilySessionDialogueStore": FamilySessionDialogueStore,
        "TAG": "test.connection",
    }
    exec(
        compile(module, str(CONNECTION_PATH), "exec"),
        namespace,
    )

    handler_class = type(
        "IsolatedConnectionHandler",
        (),
        {
            "__init__": namespace["__init__"],
            "close": namespace["close"],
            "clear_queues": lambda self: None,
        },
    )
    return handler_class


def connection_method_ast(method_name):
    """返回生产 ConnectionHandler 指定方法的 AST。"""

    tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
    connection_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ConnectionHandler"
    )
    return next(
        node
        for node in connection_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )


class ConnectionDialogueStoreLifecycleTest(
    unittest.IsolatedAsyncioTestCase
):
    """验证 Store 只接入 ConnectionHandler 生命周期。"""

    @classmethod
    def setUpClass(cls):
        cls.handler_class = load_connection_lifecycle()

    def setUp(self):
        CountingDialogue.instances = []

    def create_handler(self, is_active):
        return self.handler_class(
            {"exit_commands": []},
            None,
            None,
            None,
            object(),
            None,
            family_memory_runtime=FakeRuntime(is_active),
        )

    async def test_disabled_runtime_keeps_only_official_dialogue(self):
        handler = self.create_handler(False)

        self.assertIsNone(handler.family_session_dialogues)
        self.assertIsInstance(handler.dialogue, CountingDialogue)
        self.assertEqual(len(CountingDialogue.instances), 1)

        original_dialogue = handler.dialogue
        await handler.close()
        self.assertIs(handler.dialogue, original_dialogue)

    async def test_active_runtime_creates_one_store_without_children(self):
        handler = self.create_handler(True)

        store = handler.family_session_dialogues
        self.assertIsInstance(store, FamilySessionDialogueStore)
        self.assertIsInstance(handler.dialogue, CountingDialogue)
        self.assertIs(store._dialogue_factory, CountingDialogue)
        self.assertEqual(store._personal_dialogues, {})
        self.assertIsNone(store._anonymous_dialogue)
        self.assertEqual(len(CountingDialogue.instances), 1)
        self.assertIs(handler.family_session_dialogues, store)

        await handler.close()

    async def test_handlers_do_not_share_store_or_internal_state(self):
        first = self.create_handler(True)
        second = self.create_handler(True)

        self.assertIsNot(
            first.family_session_dialogues,
            second.family_session_dialogues,
        )
        self.assertIsNot(
            first.family_session_dialogues._personal_dialogues,
            second.family_session_dialogues._personal_dialogues,
        )
        self.assertEqual(len(CountingDialogue.instances), 2)

        await first.close()
        await second.close()

    async def test_normal_close_clears_store_exactly_once(self):
        handler = self.create_handler(True)
        store = handler.family_session_dialogues
        clear = Mock(wraps=store.clear)
        store.clear = clear

        await handler.close()

        clear.assert_called_once_with()
        self.assertIsNone(handler.family_session_dialogues)
        self.assertEqual(store._personal_dialogues, {})
        self.assertIsNone(store._anonymous_dialogue)

    async def test_repeated_close_is_safe_and_does_not_reclear(self):
        handler = self.create_handler(True)
        store = handler.family_session_dialogues
        clear = Mock(wraps=store.clear)
        store.clear = clear

        await handler.close()
        await handler.close()

        clear.assert_called_once_with()
        self.assertIsNone(handler.family_session_dialogues)

    async def test_store_clears_when_other_cleanup_fails(self):
        handler = self.create_handler(True)
        store = handler.family_session_dialogues
        clear = Mock(wraps=store.clear)
        store.clear = clear

        def fail_primary_cleanup():
            raise RuntimeError("primary cleanup failure")

        handler.clear_queues = fail_primary_cleanup
        await handler.close()

        clear.assert_called_once_with()
        self.assertTrue(
            any(
                "primary cleanup failure" in message
                for message in handler.logger.errors
            )
        )
        if handler.executor is not None:
            handler.executor.shutdown(wait=False)
            handler.executor = None

    async def test_clear_failure_does_not_escape_or_replace_cleanup_error(self):
        handler = self.create_handler(True)
        store = handler.family_session_dialogues

        def fail_primary_cleanup():
            raise RuntimeError("primary cleanup failure")

        def fail_store_clear():
            raise RuntimeError("store clear failure")

        handler.clear_queues = fail_primary_cleanup
        store.clear = Mock(side_effect=fail_store_clear)

        await handler.close()

        store.clear.assert_called_once_with()
        self.assertTrue(
            any(
                "primary cleanup failure" in message
                for message in handler.logger.errors
            )
        )
        self.assertFalse(
            any(
                "store clear failure" in message
                for message in handler.logger.errors
            )
        )
        if handler.executor is not None:
            handler.executor.shutdown(wait=False)
            handler.executor = None

    def test_chat_does_not_use_store_or_get_dialogue(self):
        chat = connection_method_ast("chat")
        attributes = {
            node.attr
            for node in ast.walk(chat)
            if isinstance(node, ast.Attribute)
        }

        self.assertIn("dialogue", attributes)
        self.assertNotIn("family_session_dialogues", attributes)
        self.assertNotIn("get_dialogue", attributes)

    def test_close_does_not_access_identity_memory_or_sqlite(self):
        close = connection_method_ast("close")
        attributes = {
            node.attr
            for node in ast.walk(close)
            if isinstance(node, ast.Attribute)
        }

        self.assertNotIn("memory", attributes)
        self.assertNotIn("identity_service", attributes)
        self.assertNotIn("repository", attributes)
        self.assertNotIn("get_dialogue", attributes)
