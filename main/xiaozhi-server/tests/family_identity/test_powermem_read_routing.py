"""B4b PowerMem 个人读取路由、画像缓存与匿名零读取测试。"""

import ast
import asyncio
import queue
import sys
import threading
import types
import unittest
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import patch


class ImportLogger:
    def bind(self, **kwargs):
        return self

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


logger_module = types.ModuleType("config.logger")
logger_module.setup_logging = lambda *args, **kwargs: ImportLogger()
sys.modules.setdefault("config.logger", logger_module)

from core.family_identity import (
    IdentityDecision,
    IdentityStatus,
    MemoryAccessPolicy,
    PersonIdentity,
    TurnIdentityContext,
    build_memory_user_id,
)
from core.family_identity.session_dialogues import FamilySessionDialogueStore
from core.providers.memory.powermem.powermem import (
    MemoryProvider as PowerMemProvider,
)
from core.utils.dialogue import Dialogue, Message


SERVER_ROOT = Path(__file__).resolve().parents[2]
CONNECTION_PATH = SERVER_ROOT / "core" / "connection.py"
OMITTED = object()


def load_connection_method(method_name, namespace):
    tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
    connection = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ConnectionHandler"
    )
    method = next(
        node
        for node in connection.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(CONNECTION_PATH), "exec"), namespace)
    return namespace[method_name]


CHAT = load_connection_method(
    "chat",
    {
        "asyncio": asyncio,
        "json": __import__("json"),
        "Optional": Optional,
        "uuid": uuid,
        "Message": Message,
        "TTSMessageDTO": lambda **kwargs: kwargs,
        "SentenceType": types.SimpleNamespace(
            FIRST="first",
            MIDDLE="middle",
            LAST="last",
        ),
        "ContentType": types.SimpleNamespace(
            ACTION="action",
            TEXT="text",
        ),
        "get_system_error_response": lambda config: "系统错误",
        "TAG": "test.connection",
    },
)
GET_MEMORY_USER_ID_FOR_TURN = load_connection_method(
    "get_memory_user_id_for_turn",
    {
        "TurnIdentityContext": TurnIdentityContext,
        "MemoryAccessPolicy": MemoryAccessPolicy,
        "build_memory_user_id": build_memory_user_id,
    },
)
APPEND_FAMILY_TURN_ASSISTANT_TEXT = load_connection_method(
    "_append_family_turn_assistant_text",
    {},
)
MARK_FAMILY_TURN_INCOMPLETE = load_connection_method(
    "_mark_family_turn_incomplete",
    {},
)
SAVE_FAMILY_TURN_MEMORY = load_connection_method(
    "_save_family_turn_memory",
    {
        "asyncio": asyncio,
        "Message": Message,
        "TAG": "test.connection",
    },
)


class ImmediateFuture:
    def __init__(self, coroutine):
        self._value = None
        self._error = None
        try:
            coroutine.send(None)
        except StopIteration as stop:
            self._value = stop.value
        except Exception as exc:
            self._error = exc
        else:
            coroutine.close()
            self._error = AssertionError(
                "连接路由替身协程不应发生异步挂起"
            )

    def result(self):
        if self._error is not None:
            raise self._error
        return self._value


class RecordingLogger:
    def __init__(self):
        self.errors = []

    def bind(self, **kwargs):
        return self

    def info(self, message):
        pass

    def debug(self, message):
        pass

    def error(self, message):
        self.errors.append(message)


class RecordingTTS:
    def __init__(self):
        self.tts_text_queue = queue.Queue()

    def store_tts_text(self, sentence_id, text):
        pass


class RecordingLLM:
    def __init__(self):
        self.dialogues = []

    def response(self, session_id, dialogue):
        self.dialogues.append(dialogue)
        return iter(["正常回答"])


class RecordingMemory:
    def __init__(self, *, fail=False):
        self.calls = []
        self.save_calls = []
        self.fail = fail

    async def query_memory(self, query, user_id=OMITTED):
        self.calls.append((query, user_id))
        if self.fail:
            raise RuntimeError("memory unavailable")
        identity = "legacy" if user_id is OMITTED else user_id
        return f"memory:{identity}"

    async def save_memory(self, messages, user_id=OMITTED):
        self.save_calls.append((messages, user_id))


class MemoryRoutingConnection:
    chat = CHAT
    _append_family_turn_assistant_text = staticmethod(
        APPEND_FAMILY_TURN_ASSISTANT_TEXT
    )
    _mark_family_turn_incomplete = staticmethod(
        MARK_FAMILY_TURN_INCOMPLETE
    )
    _save_family_turn_memory = SAVE_FAMILY_TURN_MEMORY
    get_memory_user_id_for_turn = staticmethod(
        GET_MEMORY_USER_ID_FOR_TURN
    )

    def __init__(self, *, family_enabled, memory):
        self.dialogue = Dialogue()
        self.dialogue.put(
            Message(role="system", content="<memory></memory>")
        )
        self.family_session_dialogues = (
            FamilySessionDialogueStore(
                lambda: self.dialogue.copy_static_context()
            )
            if family_enabled
            else None
        )
        self.logger = RecordingLogger()
        self.tts = RecordingTTS()
        self.llm = RecordingLLM()
        self.memory = memory
        self.intent_type = "nointent"
        self.session_id = "session_001"
        self.sentence_id = None
        self.current_speaker = None
        self.system_introduced_speakers = set()
        self.features = {"emoji": False}
        self.config = {"voiceprint": {}}
        self.client_abort = False
        self.loop = object()

    def get_dialogue_for_turn(self, turn_identity_context=None):
        if self.family_session_dialogues is None:
            return self.dialogue
        return self.family_session_dialogues.get_dialogue(
            turn_identity_context
        )

    def is_family_memory_active(self):
        return self.family_session_dialogues is not None


def recognized_context(person_id):
    return TurnIdentityContext(
        session_id="session_001",
        turn_id=f"turn_{person_id}",
        device_id="device_001",
        identity_decision=IdentityDecision.recognized(
            PersonIdentity(
                family_id="family_001",
                person_id=person_id,
                display_name=person_id,
            ),
            voiceprint_id=f"voiceprint_{person_id}",
        ),
    )


def denied_context(status):
    return TurnIdentityContext(
        session_id="session_001",
        turn_id=f"turn_{status.value}",
        device_id="device_001",
        identity_decision=IdentityDecision.denied(
            "family_001",
            status,
        ),
    )


def invalid_recognized_context():
    decision = object.__new__(IdentityDecision)
    values = {
        "family_id": "family_001",
        "person_id": "person_father",
        "voiceprint_id": "voiceprint_father",
        "display_name": "父亲",
        "memory_user_id": "device_001",
        "identity_status": IdentityStatus.RECOGNIZED,
        "allow_memory_read": True,
        "allow_memory_write": True,
        "failure_reason": None,
    }
    for name, value in values.items():
        object.__setattr__(decision, name, value)
    return TurnIdentityContext(
        session_id="session_001",
        turn_id="turn_invalid",
        device_id="device_001",
        identity_decision=decision,
    )


class ConnectionMemoryReadRoutingTest(unittest.TestCase):
    def run_chat(self, connection, query, context=None):
        with patch.object(
            asyncio,
            "run_coroutine_threadsafe",
            side_effect=lambda coroutine, loop: ImmediateFuture(coroutine),
        ):
            connection.chat(
                query,
                turn_identity_context=context,
            )

    def test_disabled_family_mode_keeps_legacy_query_signature(self):
        memory = RecordingMemory()
        connection = MemoryRoutingConnection(
            family_enabled=False,
            memory=memory,
        )

        self.run_chat(connection, "官方问题")

        self.assertEqual([("官方问题", OMITTED)], memory.calls)
        self.assertIn("memory:legacy", connection.llm.dialogues[0][0]["content"])

    def test_recognized_people_use_distinct_explicit_user_ids(self):
        memory = RecordingMemory()
        connection = MemoryRoutingConnection(
            family_enabled=True,
            memory=memory,
        )
        father = recognized_context("person_father")
        mother = recognized_context("person_mother")

        self.run_chat(connection, "父亲问题", father)
        connection.current_speaker = "母亲"
        self.run_chat(connection, "母亲问题", mother)
        connection.current_speaker = "陌生人"
        self.run_chat(connection, "父亲追问", father)

        self.assertEqual(
            [
                ("父亲问题", "family_001:person_father"),
                ("母亲问题", "family_001:person_mother"),
                ("父亲追问", "family_001:person_father"),
            ],
            memory.calls,
        )

    def test_all_denied_states_and_none_make_zero_private_reads(self):
        memory = RecordingMemory()
        connection = MemoryRoutingConnection(
            family_enabled=True,
            memory=memory,
        )

        self.run_chat(connection, "匿名问题", None)
        for status in IdentityStatus:
            if status is IdentityStatus.RECOGNIZED:
                continue
            self.run_chat(
                connection,
                f"{status.value}问题",
                denied_context(status),
            )

        self.assertEqual([], memory.calls)
        self.assertEqual(len(IdentityStatus), len(connection.llm.dialogues))

    def test_invalid_recognized_result_does_not_fallback_to_device(self):
        memory = RecordingMemory()
        connection = MemoryRoutingConnection(
            family_enabled=True,
            memory=memory,
        )

        self.run_chat(
            connection,
            "非法身份问题",
            invalid_recognized_context(),
        )

        self.assertEqual([], memory.calls)
        self.assertNotIn(
            "device_001",
            connection.llm.dialogues[0][0]["content"],
        )

    def test_anonymous_after_recognized_does_not_reuse_private_memory(self):
        memory = RecordingMemory()
        connection = MemoryRoutingConnection(
            family_enabled=True,
            memory=memory,
        )

        self.run_chat(
            connection,
            "父亲问题",
            recognized_context("person_father"),
        )
        self.run_chat(connection, "匿名问题", None)

        self.assertEqual(
            [("父亲问题", "family_001:person_father")],
            memory.calls,
        )
        anonymous_dialogue = connection.llm.dialogues[1]
        self.assertNotIn(
            "family_001:person_father",
            anonymous_dialogue[0]["content"],
        )

    def test_memory_is_injected_only_into_current_llm_copy(self):
        memory = RecordingMemory()
        connection = MemoryRoutingConnection(
            family_enabled=True,
            memory=memory,
        )
        father = recognized_context("person_father")

        self.run_chat(connection, "父亲问题", father)

        father_dialogue = connection.get_dialogue_for_turn(father)
        self.assertEqual(
            "<memory></memory>",
            father_dialogue.dialogue[0].content,
        )
        self.assertEqual(
            "<memory></memory>",
            connection.dialogue.dialogue[0].content,
        )
        self.assertIn(
            "memory:family_001:person_father",
            connection.llm.dialogues[0][0]["content"],
        )

    def test_query_exception_continues_without_memory_or_fallback(self):
        memory = RecordingMemory(fail=True)
        connection = MemoryRoutingConnection(
            family_enabled=True,
            memory=memory,
        )

        self.run_chat(
            connection,
            "父亲问题",
            recognized_context("person_father"),
        )

        self.assertEqual(
            [("父亲问题", "family_001:person_father")],
            memory.calls,
        )
        self.assertEqual(1, len(connection.llm.dialogues))
        self.assertEqual("<memory></memory>", connection.llm.dialogues[0][0]["content"])
        self.assertEqual(1, len(connection.logger.errors))


class RecordingUserMemoryClient:
    def __init__(
        self,
        profiles=None,
        memories=None,
        profile_errors=None,
        search_errors=None,
    ):
        self.profiles = profiles or {}
        self.memories = memories or {}
        self.profile_errors = set(profile_errors or ())
        self.search_errors = set(search_errors or ())
        self.profile_calls = []
        self.search_calls = []
        self._lock = threading.Lock()

    def profile(self, *, user_id):
        with self._lock:
            self.profile_calls.append(user_id)
        if user_id in self.profile_errors:
            raise RuntimeError("profile unavailable")
        content = self.profiles.get(user_id)
        return {"profile_content": content} if content else {}

    def search(self, *, query, user_id, limit):
        with self._lock:
            self.search_calls.append((query, user_id, limit))
        if user_id in self.search_errors:
            raise RuntimeError("search unavailable")
        content = self.memories.get(user_id)
        return {
            "results": (
                [{"memory": content, "updated_at": "2026-01-01T00:00:00"}]
                if content
                else []
            )
        }


class RecordingAsyncMemoryClient:
    def __init__(self):
        self.search_calls = []

    async def search(self, *, query, user_id, limit):
        await asyncio.sleep(0)
        self.search_calls.append((query, user_id, limit))
        return {"results": [{"memory": user_id}]}


def make_powermem_provider(client, *, profiles_enabled):
    provider = object.__new__(PowerMemProvider)
    provider.use_powermem = True
    provider.memory_client = client
    provider.enable_user_profile = profiles_enabled
    provider.role_id = "device_legacy"
    provider.last_profile_content = ""
    provider._profile_cache = {}
    provider._profile_cache_lock = threading.Lock()
    return provider


class PowerMemProviderReadIsolationTest(unittest.TestCase):
    def test_explicit_id_reaches_profile_and_search_without_mutating_role(self):
        client = RecordingUserMemoryClient(
            profiles={"family_001:person_father": "父亲画像"},
            memories={"family_001:person_father": "父亲记忆"},
        )
        provider = make_powermem_provider(client, profiles_enabled=True)

        result = asyncio.run(
            provider.query_memory(
                "喜欢什么",
                user_id="family_001:person_father",
            )
        )

        self.assertEqual(
            ["family_001:person_father"],
            client.profile_calls,
        )
        self.assertEqual(
            [("喜欢什么", "family_001:person_father", 30)],
            client.search_calls,
        )
        self.assertIn("父亲画像", result)
        self.assertIn("父亲记忆", result)
        self.assertEqual("device_legacy", provider.role_id)

    def test_profile_cache_is_keyed_by_explicit_user_id(self):
        father = "family_001:person_father"
        mother = "family_001:person_mother"
        client = RecordingUserMemoryClient(
            profiles={father: "父亲画像", mother: "母亲画像"},
        )
        provider = make_powermem_provider(client, profiles_enabled=True)

        father_first = asyncio.run(
            provider.get_user_profile(user_id=father)
        )
        mother_first = asyncio.run(
            provider.get_user_profile(user_id=mother)
        )
        father_again = asyncio.run(
            provider.get_user_profile(user_id=father)
        )

        self.assertEqual("父亲画像", father_first)
        self.assertEqual("母亲画像", mother_first)
        self.assertEqual("父亲画像", father_again)
        self.assertEqual([father, mother], client.profile_calls)
        self.assertEqual(
            {father: "父亲画像", mother: "母亲画像"},
            provider._profile_cache,
        )

    def test_father_and_mother_results_never_cross(self):
        father = "family_001:person_father"
        mother = "family_001:person_mother"
        client = RecordingUserMemoryClient(
            profiles={father: "父亲画像", mother: "母亲画像"},
            memories={father: "父亲记忆", mother: "母亲记忆"},
        )
        provider = make_powermem_provider(client, profiles_enabled=True)

        father_result = asyncio.run(
            provider.query_memory("父亲问题", user_id=father)
        )
        mother_result = asyncio.run(
            provider.query_memory("母亲问题", user_id=mother)
        )

        self.assertIn("父亲画像", father_result)
        self.assertIn("父亲记忆", father_result)
        self.assertNotIn("母亲", father_result)
        self.assertIn("母亲画像", mother_result)
        self.assertIn("母亲记忆", mother_result)
        self.assertNotIn("父亲", mother_result)

    def test_legacy_profile_and_role_id_behavior_remains_available(self):
        client = RecordingUserMemoryClient(
            memories={"device_legacy": "设备记忆"},
        )
        provider = make_powermem_provider(client, profiles_enabled=True)
        provider.last_profile_content = "官方缓存画像"

        result = asyncio.run(provider.query_memory("官方问题"))

        self.assertEqual([], client.profile_calls)
        self.assertEqual(
            [("官方问题", "device_legacy", 30)],
            client.search_calls,
        )
        self.assertIn("官方缓存画像", result)
        self.assertIn("设备记忆", result)

    def test_concurrent_explicit_queries_keep_their_own_user_ids(self):
        client = RecordingAsyncMemoryClient()
        provider = make_powermem_provider(client, profiles_enabled=False)
        father = "family_001:person_father"
        mother = "family_001:person_mother"

        async def query_both():
            return await asyncio.gather(
                provider.query_memory("父亲问题", user_id=father),
                provider.query_memory("母亲问题", user_id=mother),
            )

        results = asyncio.run(query_both())

        self.assertEqual(
            {
                ("父亲问题", father, 30),
                ("母亲问题", mother, 30),
            },
            set(client.search_calls),
        )
        self.assertIn(father, results[0])
        self.assertIn(mother, results[1])
        self.assertEqual("device_legacy", provider.role_id)

    def test_concurrent_profile_queries_use_separate_cache_keys(self):
        father = "family_001:person_father"
        mother = "family_001:person_mother"
        client = RecordingUserMemoryClient(
            profiles={father: "父亲画像", mother: "母亲画像"},
            memories={father: "父亲记忆", mother: "母亲记忆"},
        )
        provider = make_powermem_provider(client, profiles_enabled=True)

        async def query_both():
            return await asyncio.gather(
                provider.query_memory("父亲问题", user_id=father),
                provider.query_memory("母亲问题", user_id=mother),
            )

        results = asyncio.run(query_both())

        self.assertIn("父亲画像", results[0])
        self.assertNotIn("母亲画像", results[0])
        self.assertIn("母亲画像", results[1])
        self.assertNotIn("父亲画像", results[1])
        self.assertEqual(
            {father: "父亲画像", mother: "母亲画像"},
            provider._profile_cache,
        )

    def test_one_profile_failure_does_not_change_another_user_cache(self):
        father = "family_001:person_father"
        mother = "family_001:person_mother"
        client = RecordingUserMemoryClient(
            profiles={mother: "母亲画像"},
            profile_errors={father},
        )
        provider = make_powermem_provider(client, profiles_enabled=True)

        father_profile = asyncio.run(
            provider.get_user_profile(user_id=father)
        )
        mother_profile = asyncio.run(
            provider.get_user_profile(user_id=mother)
        )

        self.assertEqual("", father_profile)
        self.assertEqual("母亲画像", mother_profile)
        self.assertNotIn(father, provider._profile_cache)
        self.assertEqual("母亲画像", provider._profile_cache[mother])

    def test_empty_explicit_user_id_does_not_fallback_to_role_id(self):
        client = RecordingUserMemoryClient(
            profiles={"device_legacy": "设备画像"},
            memories={"device_legacy": "设备记忆"},
        )
        provider = make_powermem_provider(client, profiles_enabled=True)

        result = asyncio.run(
            provider.query_memory("非法身份问题", user_id="")
        )

        self.assertEqual("", result)
        self.assertEqual([], client.profile_calls)
        self.assertEqual([], client.search_calls)
        self.assertEqual({}, provider._profile_cache)

    def test_search_failure_returns_empty_without_other_user_fallback(self):
        father = "family_001:person_father"
        client = RecordingUserMemoryClient(
            search_errors={father},
            memories={"device_legacy": "设备记忆"},
        )
        provider = make_powermem_provider(client, profiles_enabled=True)

        result = asyncio.run(
            provider.query_memory("父亲问题", user_id=father)
        )

        self.assertEqual("", result)
        self.assertEqual(
            [("父亲问题", father, 30)],
            client.search_calls,
        )
        self.assertEqual("device_legacy", provider.role_id)


class PowerMemSourceContractTest(unittest.TestCase):
    def test_memory_base_and_powermem_query_keep_optional_user_id(self):
        base_path = (
            SERVER_ROOT / "core" / "providers" / "memory" / "base.py"
        )
        powermem_path = (
            SERVER_ROOT
            / "core"
            / "providers"
            / "memory"
            / "powermem"
            / "powermem.py"
        )
        for path in (base_path, powermem_path):
            with self.subTest(path=path):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                query = next(
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "query_memory"
                )
                self.assertEqual("user_id", query.args.args[-1].arg)
                self.assertIsInstance(
                    query.args.defaults[-1],
                    ast.Constant,
                )
                self.assertIsNone(query.args.defaults[-1].value)

    def test_connection_does_not_assign_memory_role_id(self):
        tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
        assignments = [
            target
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
            and target.attr == "role_id"
        ]
        self.assertEqual([], assignments)

    def test_save_memory_signature_is_backward_compatible(self):
        for path in (
            SERVER_ROOT / "core" / "providers" / "memory" / "base.py",
            SERVER_ROOT
            / "core"
            / "providers"
            / "memory"
            / "powermem"
            / "powermem.py",
        ):
            with self.subTest(path=path):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                method = next(
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "save_memory"
                )
                self.assertEqual("user_id", method.args.args[-1].arg)
                self.assertIsNone(method.args.defaults[-1].value)

    def test_reqlmm_recursion_preserves_original_turn_context(self):
        tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
        helper = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_handle_function_result"
        )
        recursive_calls = [
            node
            for node in ast.walk(helper)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "chat"
        ]
        self.assertTrue(recursive_calls)
        context_keywords = [
            keyword
            for call in recursive_calls
            for keyword in call.keywords
            if keyword.arg == "turn_identity_context"
        ]
        self.assertTrue(context_keywords)
        self.assertTrue(
            all(
                isinstance(keyword.value, ast.Name)
                and keyword.value.id == "turn_identity_context"
                for keyword in context_keywords
            )
        )

    def test_non_voice_entries_do_not_supply_person_context(self):
        paths = [
            (
                SERVER_ROOT
                / "core"
                / "handle"
                / "receiveAudioHandle.py"
            ),
            (
                SERVER_ROOT
                / "core"
                / "handle"
                / "textHandler"
                / "listenMessageHandler.py"
            ),
        ]
        for path in paths:
            with self.subTest(path=path):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                calls = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and (
                        (
                            isinstance(node.func, ast.Name)
                            and node.func.id == "startToChat"
                        )
                        or (
                            isinstance(node.func, ast.Attribute)
                            and node.func.attr == "chat"
                        )
                    )
                ]
                self.assertTrue(calls)
                for call in calls:
                    context_keywords = [
                        keyword
                        for keyword in call.keywords
                        if keyword.arg == "turn_identity_context"
                    ]
                    if context_keywords:
                        self.assertTrue(
                            all(
                                isinstance(keyword.value, ast.Name)
                                and keyword.value.id
                                == "turn_identity_context"
                                for keyword in context_keywords
                            )
                        )


if __name__ == "__main__":
    unittest.main()
