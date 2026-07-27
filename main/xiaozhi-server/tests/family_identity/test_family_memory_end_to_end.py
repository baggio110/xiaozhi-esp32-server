"""B7 无硬件双终端、多人员、匿名、并发与故障集成测试。"""

import ast
import asyncio
import json
import queue
import socket
import threading
import types
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from core.family_identity import (
    FamilySessionDialogueStore,
    IdentityDecision,
    IdentityService,
    IdentityStatus,
    MemoryAccessPolicy,
    PersonIdentity,
    RecognitionResult,
    TurnIdentityContext,
    build_memory_user_id,
)
from core.utils.dialogue import Dialogue, Message

from .fakes import FakeIdentityRepository, FakeVoiceprintProvider


SERVER_ROOT = Path(__file__).resolve().parents[2]
CONNECTION_PATH = SERVER_ROOT / "core" / "connection.py"
OMITTED = object()
REAL_THREAD = threading.Thread


def load_connection_method(method_name, namespace):
    """加载生产 ConnectionHandler 的单个方法，隔离外部服务初始化。"""

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


ACTION = types.SimpleNamespace(
    RESPONSE="response",
    NOTFOUND="notfound",
    ERROR="error",
    REQLLM="reqllm",
    RECORD="record",
)


@dataclass
class FakeActionResponse:
    action: str
    result: Optional[str] = None
    response: Optional[str] = None


def action_response(**kwargs):
    return FakeActionResponse(
        action=kwargs["action"],
        result=kwargs.get("result"),
        response=kwargs.get("response"),
    )


async def fake_generate_title(session_id):
    return None


COMMON_NAMESPACE = {
    "asyncio": asyncio,
    "json": json,
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
    "Action": ACTION,
    "ActionResponse": action_response,
    "DIRECT_ANSWER_TOOL": {"name": "direct_answer"},
    "enqueue_tool_report": lambda *args, **kwargs: None,
    "extract_json_from_string": lambda value: None,
    "get_system_error_response": lambda config: "系统错误",
    "TAG": "test.family-memory-e2e",
}

CHAT = load_connection_method("chat", dict(COMMON_NAMESPACE))
HANDLE_FUNCTION_RESULT = load_connection_method(
    "_handle_function_result",
    dict(COMMON_NAMESPACE),
)
APPEND_ASSISTANT_TEXT = load_connection_method(
    "_append_family_turn_assistant_text",
    {},
)
MARK_TURN_INCOMPLETE = load_connection_method(
    "_mark_family_turn_incomplete",
    {},
)
SAVE_FAMILY_TURN = load_connection_method(
    "_save_family_turn_memory",
    dict(COMMON_NAMESPACE),
)
GET_MEMORY_USER_ID = load_connection_method(
    "get_memory_user_id_for_turn",
    {
        "TurnIdentityContext": TurnIdentityContext,
        "MemoryAccessPolicy": MemoryAccessPolicy,
        "build_memory_user_id": build_memory_user_id,
    },
)
CREATE_FAMILY_DIALOGUE = load_connection_method(
    "_create_family_dialogue",
    {},
)
GET_DIALOGUE_FOR_TURN = load_connection_method(
    "get_dialogue_for_turn",
    {},
)
IS_FAMILY_MEMORY_ACTIVE = load_connection_method(
    "is_family_memory_active",
    {},
)
SAVE_AND_CLOSE = load_connection_method(
    "_save_and_close",
    {
        "asyncio": asyncio,
        "threading": threading,
        "generate_and_save_chat_title": fake_generate_title,
        "TAG": "test.family-memory-e2e",
    },
)


class LoopThread:
    """为生产同步 chat() 提供真实、受控的本地事件循环。"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.started = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name="family-memory-test-loop",
            daemon=True,
        )
        self.thread.start()
        if not self.started.wait(timeout=5):
            raise RuntimeError("测试事件循环启动超时")

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.started.set()
        self.loop.run_forever()

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


class FakeLogger:
    def __init__(self):
        self.errors = []

    def bind(self, **kwargs):
        return self

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        self.errors.append(str(message))


class FakeWebSocket:
    """只记录发送和关闭，不建立网络连接。"""

    def __init__(self, device_id):
        self.device_id = device_id
        self.sent = []
        self.closed = False
        self.close_calls = 0

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self):
        self.closed = True
        self.close_calls += 1


class FakeTTS:
    def __init__(self):
        self.tts_text_queue = queue.Queue()
        self.stored = []
        self.sentences = []

    def store_tts_text(self, sentence_id, text):
        self.stored.append((sentence_id, text))

    def tts_one_sentence(self, connection, content_type, content_detail):
        self.sentences.append(content_detail)


class FakeLLM:
    """根据输入和注入记忆返回确定性的自然语言流。"""

    def __init__(self):
        self.dialogues = []
        self.raise_queries = set()
        self.stream_fail_queries = set()
        self._lock = threading.Lock()

    def _record(self, dialogue):
        snapshot = [dict(message) for message in dialogue]
        with self._lock:
            self.dialogues.append(snapshot)
        return snapshot

    @staticmethod
    def _last_user(dialogue):
        return next(
            (
                message.get("content")
                for message in reversed(dialogue)
                if message.get("role") == "user"
            ),
            None,
        )

    @staticmethod
    def _answer(query, dialogue):
        system = next(
            (
                message.get("content", "")
                for message in dialogue
                if message.get("role") == "system"
            ),
            "",
        )
        history = "\n".join(
            message.get("content") or ""
            for message in dialogue
            if message.get("role") in {"user", "assistant"}
        )
        if query == "我平时喜欢喝什么？" and "龙井" in system:
            return "您平时喜欢喝龙井。"
        if query == "哪个适合晚上去？" and "西安有哪些景点？" in history:
            return "大唐不夜城适合晚上去。"
        return f"自然语言回答：{query}"

    def response(self, session_id, dialogue):
        snapshot = self._record(dialogue)
        query = self._last_user(snapshot)
        if query in self.raise_queries:
            raise RuntimeError("fake llm unavailable")
        answer = self._answer(query, snapshot)
        if query in self.stream_fail_queries:
            def broken_stream():
                yield answer[:4]
                raise RuntimeError("fake llm stream interrupted")

            return broken_stream()
        midpoint = max(1, len(answer) // 2)
        return iter((answer[:midpoint], answer[midpoint:]))

    def response_with_functions(self, session_id, dialogue, functions):
        snapshot = self._record(dialogue)
        query = self._last_user(snapshot)
        has_tool_result = any(
            message.get("role") == "tool"
            for message in snapshot
        )
        if has_tool_result:
            return iter((("工具后的最终自然语言回答", None),))
        if query == "直接回答":
            return iter(
                (
                    (
                        None,
                        [
                            {
                                "id": "direct_001",
                                "name": "direct_answer",
                                "arguments": json.dumps(
                                    {"response": "直接自然语言回答"},
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    ),
                )
            )
        return iter(
            (
                (
                    None,
                    [
                        {
                            "id": "tool_001",
                            "name": "fake_tool",
                            "arguments": "{}",
                        }
                    ],
                ),
            )
        )


@dataclass(frozen=True)
class MemoryCall:
    query: Optional[str]
    user_id: object
    effective_user_id: Optional[str]
    messages: tuple = ()


class FakeMemoryProvider:
    """共享的确定性内存替身，记录读、画像和写入身份。"""

    def __init__(self, role_id="official_device_role"):
        self.role_id = role_id
        self.query_calls = []
        self.profile_calls = []
        self.save_calls = []
        self.memories = {}
        self.profiles = {}
        self.query_failures = set()
        self.profile_failures = set()
        self.save_failures = set()
        self._lock = threading.Lock()

    def _effective_user_id(self, user_id):
        return self.role_id if user_id is OMITTED or user_id is None else user_id

    async def get_user_profile(self, user_id=OMITTED):
        effective = self._effective_user_id(user_id)
        with self._lock:
            self.profile_calls.append(
                MemoryCall(None, user_id, effective)
            )
        if effective in self.profile_failures:
            raise RuntimeError("fake profile unavailable")
        return self.profiles.get(effective, "")

    async def query_memory(self, query, user_id=OMITTED):
        effective = self._effective_user_id(user_id)
        with self._lock:
            self.query_calls.append(
                MemoryCall(query, user_id, effective)
            )
        await asyncio.sleep(0)
        if effective in self.query_failures:
            raise RuntimeError("fake memory read unavailable")
        try:
            profile = await self.get_user_profile(user_id=user_id)
        except RuntimeError:
            profile = ""
        with self._lock:
            saved = tuple(self.memories.get(effective, ()))
        parts = []
        if profile:
            parts.append(f"画像:{profile}")
        parts.extend(
            content
            for turn in saved
            for role, content in turn
            if role == "user"
        )
        return "\n".join(parts)

    async def save_memory(
        self,
        messages,
        session_id=None,
        user_id=OMITTED,
    ):
        effective = self._effective_user_id(user_id)
        payload = tuple(
            (message.role, message.content)
            for message in messages
        )
        with self._lock:
            self.save_calls.append(
                MemoryCall(None, user_id, effective, payload)
            )
        await asyncio.sleep(0)
        if effective in self.save_failures:
            raise RuntimeError("fake memory write unavailable")
        with self._lock:
            self.memories.setdefault(effective, []).append(payload)

    def counts(self):
        with self._lock:
            return (
                len(self.query_calls),
                len(self.profile_calls),
                len(self.save_calls),
            )


class FakeTool:
    """覆盖 RESPONSE、RECORD、REQLLM 和失败的工具替身。"""

    def __init__(self, mode=ACTION.REQLLM):
        self.mode = mode
        self.calls = []

    def get_functions(self):
        return [{"name": "fake_tool"}]

    async def handle_llm_function_call(
        self,
        connection,
        tool_call_data,
    ):
        await asyncio.sleep(0)
        self.calls.append(
            {
                "tool": dict(tool_call_data),
                "speaker_before": connection.current_speaker,
            }
        )
        connection.current_speaker = "中途变化的其他说话人"
        if self.mode == "raise":
            raise RuntimeError("fake tool failure")
        if self.mode == ACTION.RESPONSE:
            return FakeActionResponse(
                action=ACTION.RESPONSE,
                response="工具直接自然语言回答",
            )
        if self.mode == "direct_answer":
            return FakeActionResponse(
                action=ACTION.RESPONSE,
                response="工具直接回答",
            )
        if self.mode == ACTION.RECORD:
            return FakeActionResponse(
                action=ACTION.RECORD,
                result="工具内部记录",
                response="工具记录完成",
            )
        return FakeActionResponse(
            action=ACTION.REQLLM,
            result="工具内部原始结果",
        )


class SimulatedConnection:
    """装配生产路由方法的最小 ConnectionHandler 软件终端。"""

    chat = CHAT
    _handle_function_result = HANDLE_FUNCTION_RESULT
    _append_family_turn_assistant_text = staticmethod(
        APPEND_ASSISTANT_TEXT
    )
    _mark_family_turn_incomplete = staticmethod(
        MARK_TURN_INCOMPLETE
    )
    _save_family_turn_memory = SAVE_FAMILY_TURN
    get_memory_user_id_for_turn = staticmethod(GET_MEMORY_USER_ID)
    _create_family_dialogue = CREATE_FAMILY_DIALOGUE
    get_dialogue_for_turn = GET_DIALOGUE_FOR_TURN
    is_family_memory_active = IS_FAMILY_MEMORY_ACTIVE
    _save_and_close = SAVE_AND_CLOSE

    def __init__(
        self,
        device_id,
        loop,
        memory,
        *,
        family_enabled=True,
        llm=None,
    ):
        self.device_id = device_id
        self.session_id = f"session_{device_id}"
        self.loop = loop
        self.memory = memory
        self.llm = llm or FakeLLM()
        self.dialogue = Dialogue()
        self.dialogue.put(
            Message(
                role="system",
                content="系统提示\n<memory></memory>",
            )
        )
        self.dialogue.put(
            Message(
                role="assistant",
                content="静态示例",
                is_temporary=True,
            )
        )
        self.family_session_dialogues = (
            FamilySessionDialogueStore(self._create_family_dialogue)
            if family_enabled
            else None
        )
        self.logger = FakeLogger()
        self.tts = FakeTTS()
        self.intent_type = "nointent"
        self.func_handler = None
        self.sentence_id = None
        self.current_speaker = None
        self.system_introduced_speakers = set()
        self.features = {"emoji": False}
        self.config = {"voiceprint": {}, "tool_call_timeout": 5}
        self.client_abort = False
        self.close_calls = 0

    @staticmethod
    def _merge_tool_calls(target, source):
        target.extend(source)

    @staticmethod
    def _extract_direct_answer_response(arguments):
        try:
            return json.loads(arguments).get("response")
        except (TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _clean_response_garbage(text):
        return text

    def enable_tools(self, mode=ACTION.REQLLM):
        self.intent_type = "function_call"
        self.func_handler = FakeTool(mode)
        return self.func_handler

    async def close(self, websocket=None):
        self.close_calls += 1
        store = self.family_session_dialogues
        if store is not None:
            store.clear()
            self.family_session_dialogues = None
        if websocket is not None:
            await websocket.close()


class IdentityFixture:
    """把结构化声纹结果转换成真实 IdentityDecision。"""

    def __init__(self):
        self.persons = {
            "grandfather_001": PersonIdentity(
                "home_001", "grandfather_001", "爷爷"
            ),
            "grandmother_001": PersonIdentity(
                "home_001", "grandmother_001", "奶奶"
            ),
            "child_001": PersonIdentity(
                "home_001", "child_001", "孩子"
            ),
            "child_002": PersonIdentity(
                "home_001", "child_002", "孩子"
            ),
            "caregiver_001": PersonIdentity(
                "home_001", "caregiver_001", "照护人员"
            ),
            "relative_001": PersonIdentity(
                "home_001", "relative_001", "同名成员"
            ),
            "relative_002": PersonIdentity(
                "home_001", "relative_002", "同名成员"
            ),
            "disabled_001": PersonIdentity(
                "home_001",
                "disabled_001",
                "停用成员",
                enabled=False,
            ),
        }
        self.repository = FakeIdentityRepository(
            {
                f"voice_{person_id}": person
                for person_id, person in self.persons.items()
            }
        )
        self.service = IdentityService(self.repository)
        self._turn_counter = 0
        self._lock = threading.Lock()

    @staticmethod
    def recognized(person_id, speaker_name=None):
        return RecognitionResult(
            voiceprint_id=f"voice_{person_id}",
            speaker_name=speaker_name or person_id,
            confidence=0.99,
            status=IdentityStatus.RECOGNIZED,
        )

    def context(self, connection, recognition):
        provider = FakeVoiceprintProvider(recognition)
        recognized = asyncio.run(
            provider.identify_speaker(
                b"fake-audio",
                connection.session_id,
            )
        )
        decision = self.service.resolve("home_001", recognized)
        with self._lock:
            self._turn_counter += 1
            turn_id = f"turn_{self._turn_counter:04d}"
        return TurnIdentityContext(
            session_id=connection.session_id,
            turn_id=turn_id,
            device_id=connection.device_id,
            identity_decision=decision,
        )

    def person_context(self, connection, person_id, speaker_name=None):
        return self.context(
            connection,
            self.recognized(person_id, speaker_name),
        )

    def failure_context(self, connection, status):
        if status is IdentityStatus.PERSON_NOT_BOUND:
            recognition = RecognitionResult(
                voiceprint_id="voice_unbound",
                speaker_name="访客",
                confidence=0.99,
                status=IdentityStatus.RECOGNIZED,
            )
        elif status is IdentityStatus.PERSON_DISABLED:
            recognition = self.recognized("disabled_001")
        elif status is IdentityStatus.INVALID_RESULT:
            recognition = None
        else:
            recognition = RecognitionResult(
                voiceprint_id=(
                    "voice_unknown"
                    if status is IdentityStatus.LOW_CONFIDENCE
                    else None
                ),
                speaker_name=None,
                confidence=(
                    0.1
                    if status is IdentityStatus.LOW_CONFIDENCE
                    else None
                ),
                status=status,
                error_code=status.value,
            )
        return self.context(connection, recognition)

    @staticmethod
    def invalid_recognized_context(connection):
        decision = object.__new__(IdentityDecision)
        values = {
            "family_id": "home_001",
            "person_id": "grandfather_001",
            "voiceprint_id": "voice_grandfather_001",
            "display_name": "爷爷",
            "memory_user_id": connection.device_id,
            "identity_status": IdentityStatus.RECOGNIZED,
            "allow_memory_read": True,
            "allow_memory_write": True,
            "failure_reason": None,
        }
        for name, value in values.items():
            object.__setattr__(decision, name, value)
        return TurnIdentityContext(
            session_id=connection.session_id,
            turn_id="turn_invalid_recognized",
            device_id=connection.device_id,
            identity_decision=decision,
        )


class ImmediateThread:
    """同步执行断开守护任务，使断言可重复。"""

    targets = []

    def __init__(self, target, daemon):
        self.target = target
        self.daemon = daemon
        self.__class__.targets.append(target.__name__)

    def start(self):
        thread = REAL_THREAD(target=self.target, daemon=self.daemon)
        thread.start()
        thread.join(timeout=5)


class FamilyMemoryEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.network_guards = (
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("B7禁止真实网络连接"),
            ),
        )
        for guard in self.network_guards:
            guard.start()
        self.loop_thread = LoopThread()
        self.identity = IdentityFixture()
        self.memory = FakeMemoryProvider()
        self.living = self.connection("living_room_device")
        self.bedroom = self.connection("bedroom_device")

    def tearDown(self):
        self.loop_thread.close()
        for guard in reversed(self.network_guards):
            guard.stop()

    def connection(
        self,
        device_id,
        *,
        family_enabled=True,
        memory=None,
        llm=None,
    ):
        return SimulatedConnection(
            device_id,
            self.loop_thread.loop,
            memory or self.memory,
            family_enabled=family_enabled,
            llm=llm,
        )

    @staticmethod
    def run_chat(connection, query, context=None):
        return connection.chat(
            query,
            turn_identity_context=context,
        )

    def test_two_terminals_share_long_identity_not_short_dialogue(self):
        living_context = self.identity.person_context(
            self.living,
            "grandfather_001",
            "爷爷",
        )
        bedroom_context = self.identity.person_context(
            self.bedroom,
            "grandfather_001",
            "另一显示称呼",
        )

        self.assertEqual(
            "home_001:grandfather_001",
            living_context.identity_decision.memory_user_id,
        )
        self.assertEqual(
            living_context.identity_decision.memory_user_id,
            bedroom_context.identity_decision.memory_user_id,
        )
        self.assertIsNot(
            self.living.family_session_dialogues,
            self.bedroom.family_session_dialogues,
        )
        self.assertIsNot(
            self.living.get_dialogue_for_turn(living_context),
            self.bedroom.get_dialogue_for_turn(bedroom_context),
        )

        self.run_chat(
            self.living,
            "我喜欢喝龙井。",
            living_context,
        )
        self.run_chat(
            self.bedroom,
            "我平时喜欢喝什么？",
            bedroom_context,
        )

        expected = "home_001:grandfather_001"
        self.assertEqual(
            [expected, expected],
            [call.effective_user_id for call in self.memory.query_calls],
        )
        self.assertEqual(
            [expected, expected],
            [call.effective_user_id for call in self.memory.save_calls],
        )
        self.assertEqual(
            "您平时喜欢喝龙井。",
            self.memory.save_calls[-1].messages[-1][1],
        )
        self.assertNotIn(
            self.living.device_id,
            [call.effective_user_id for call in self.memory.query_calls],
        )
        self.assertNotIn(
            self.bedroom.device_id,
            [call.effective_user_id for call in self.memory.save_calls],
        )

    def test_many_people_alternate_without_name_or_history_crossover(self):
        people = (
            ("grandfather_001", "爷爷"),
            ("grandmother_001", "奶奶"),
            ("child_001", "孩子"),
            ("child_002", "孩子"),
            ("caregiver_001", "爷爷"),
            ("relative_001", "同名成员"),
            ("relative_002", "同名成员"),
        )
        dialogues = {}
        contexts = {}
        for index, (person_id, speaker_name) in enumerate(people):
            context = self.identity.person_context(
                self.living,
                person_id,
                speaker_name,
            )
            contexts[person_id] = context
            self.living.current_speaker = f"显示值-{index}"
            self.run_chat(
                self.living,
                f"{person_id}的问题",
                context,
            )
            dialogues[person_id] = self.living.get_dialogue_for_turn(
                context
            )

        self.assertEqual(len(people), len({id(value) for value in dialogues.values()}))
        self.assertIs(
            dialogues["grandfather_001"],
            self.living.get_dialogue_for_turn(
                contexts["grandfather_001"]
            ),
        )
        expected_ids = {
            f"home_001:{person_id}"
            for person_id, _ in people
        }
        self.assertEqual(
            expected_ids,
            {call.effective_user_id for call in self.memory.query_calls},
        )
        self.assertEqual(
            expected_ids,
            {call.effective_user_id for call in self.memory.save_calls},
        )
        anonymous = self.living.get_dialogue_for_turn(None)
        self.assertNotIn(
            id(anonymous),
            {id(value) for value in dialogues.values()},
        )

    def test_anonymous_context_is_connection_local_and_zero_memory(self):
        first_dialogue = self.living.get_dialogue_for_turn(None)
        self.run_chat(self.living, "西安有哪些景点？")
        self.run_chat(self.living, "哪个适合晚上去？")

        self.assertIs(
            first_dialogue,
            self.living.get_dialogue_for_turn(None),
        )
        self.assertEqual((0, 0, 0), self.memory.counts())
        second_llm_input = self.living.llm.dialogues[-1]
        self.assertTrue(
            any(
                message.get("content") == "西安有哪些景点？"
                for message in second_llm_input
            )
        )
        self.assertEqual(
            "大唐不夜城适合晚上去。",
            first_dialogue.dialogue[-1].content,
        )

        new_connection = self.connection("new_living_room_device")
        self.assertIsNot(
            first_dialogue,
            new_connection.get_dialogue_for_turn(None),
        )
        store = self.living.family_session_dialogues
        websocket = FakeWebSocket(self.living.device_id)
        asyncio.run(self.living.close(websocket))
        self.assertEqual({}, store._personal_dialogues)
        self.assertIsNone(store._anonymous_dialogue)
        self.assertTrue(websocket.closed)

    def test_all_failed_identities_use_anonymous_and_zero_private_io(self):
        anonymous = self.living.get_dialogue_for_turn(None)
        contexts = [None]
        contexts.extend(
            self.identity.failure_context(self.living, status)
            for status in (
                IdentityStatus.UNKNOWN_VOICEPRINT,
                IdentityStatus.LOW_CONFIDENCE,
                IdentityStatus.SERVICE_UNAVAILABLE,
                IdentityStatus.PERSON_NOT_BOUND,
                IdentityStatus.PERSON_DISABLED,
                IdentityStatus.INVALID_RESULT,
            )
        )
        contexts.append(
            self.identity.invalid_recognized_context(self.living)
        )

        for index, context in enumerate(contexts):
            self.run_chat(
                self.living,
                f"匿名或失败问题{index}",
                context,
            )
            self.assertIs(
                anonymous,
                self.living.get_dialogue_for_turn(context),
            )

        self.assertEqual((0, 0, 0), self.memory.counts())
        self.assertEqual(len(contexts), len(self.living.llm.dialogues))
        self.assertNotIn(
            self.living.device_id,
            str(self.memory.query_calls + self.memory.save_calls),
        )

    def test_other_people_and_anonymous_cannot_read_saved_memory(self):
        grandfather = self.identity.person_context(
            self.living,
            "grandfather_001",
        )
        grandmother = self.identity.person_context(
            self.bedroom,
            "grandmother_001",
        )
        self.run_chat(
            self.living,
            "我喜欢喝龙井。",
            grandfather,
        )
        self.run_chat(
            self.bedroom,
            "我平时喜欢喝什么？",
            grandmother,
        )
        calls_before_anonymous = self.memory.counts()
        self.run_chat(self.bedroom, "我平时喜欢喝什么？")

        self.assertEqual(
            "home_001:grandmother_001",
            self.memory.query_calls[-1].effective_user_id,
        )
        self.assertNotIn(
            "龙井",
            self.bedroom.llm.dialogues[-2][0]["content"],
        )
        self.assertEqual(calls_before_anonymous, self.memory.counts())

    def test_reqllm_keeps_original_turn_and_saves_only_final_answer(self):
        tool = self.living.enable_tools(ACTION.REQLLM)
        self.living.current_speaker = "爷爷"
        context = self.identity.person_context(
            self.living,
            "grandfather_001",
        )

        self.run_chat(self.living, "请调用工具", context)

        self.assertEqual(1, len(tool.calls))
        self.assertEqual(
            "中途变化的其他说话人",
            self.living.current_speaker,
        )
        self.assertEqual(1, len(self.memory.query_calls))
        self.assertEqual(1, len(self.memory.save_calls))
        saved = self.memory.save_calls[0]
        self.assertEqual(
            "home_001:grandfather_001",
            saved.effective_user_id,
        )
        self.assertEqual(
            (
                ("user", "请调用工具"),
                ("assistant", "工具后的最终自然语言回答"),
            ),
            saved.messages,
        )
        personal = self.living.get_dialogue_for_turn(context)
        self.assertIn(
            "tool",
            [message.role for message in personal.dialogue],
        )
        self.assertNotIn(
            "工具内部原始结果",
            [content for _, content in saved.messages],
        )

    def test_concurrent_terminals_and_store_creation_do_not_cross(self):
        grandfather = self.identity.person_context(
            self.living,
            "grandfather_001",
        )
        grandmother = self.identity.person_context(
            self.bedroom,
            "grandmother_001",
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda item: self.run_chat(*item),
                    (
                        (self.living, "爷爷并发问题", grandfather),
                        (self.bedroom, "奶奶并发问题", grandmother),
                    ),
                )
            )
        self.assertEqual([True, True], results)
        expected = {
            "home_001:grandfather_001",
            "home_001:grandmother_001",
        }
        self.assertEqual(
            expected,
            {call.effective_user_id for call in self.memory.query_calls},
        )
        self.assertEqual(
            expected,
            {call.effective_user_id for call in self.memory.save_calls},
        )
        self.assertEqual("official_device_role", self.memory.role_id)

        same_person_living = self.identity.person_context(
            self.living,
            "child_001",
        )
        same_person_bedroom = self.identity.person_context(
            self.bedroom,
            "child_001",
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(
                executor.map(
                    lambda item: self.run_chat(*item),
                    (
                        (self.living, "孩子客厅问题", same_person_living),
                        (self.bedroom, "孩子卧室问题", same_person_bedroom),
                    ),
                )
            )
        self.assertEqual(
            ["home_001:child_001", "home_001:child_001"],
            [
                call.effective_user_id
                for call in self.memory.query_calls[-2:]
            ],
        )

        with ThreadPoolExecutor(max_workers=16) as executor:
            personal = list(
                executor.map(
                    self.living.get_dialogue_for_turn,
                    [grandfather] * 32,
                )
            )
            anonymous = list(
                executor.map(
                    self.living.get_dialogue_for_turn,
                    [None] * 32,
                )
            )
        self.assertEqual(1, len({id(item) for item in personal}))
        self.assertEqual(1, len({id(item) for item in anonymous}))

    def test_concurrent_save_failure_does_not_affect_other_person(self):
        isolated_memory = FakeMemoryProvider()
        grandfather_id = "home_001:grandfather_001"
        grandmother_id = "home_001:grandmother_001"
        isolated_memory.save_failures.add(grandfather_id)
        living = self.connection(
            "failure_living_device",
            memory=isolated_memory,
        )
        bedroom = self.connection(
            "failure_bedroom_device",
            memory=isolated_memory,
        )
        grandfather = self.identity.person_context(
            living,
            "grandfather_001",
        )
        grandmother = self.identity.person_context(
            bedroom,
            "grandmother_001",
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda item: self.run_chat(*item),
                    (
                        (living, "爷爷保存失败", grandfather),
                        (bedroom, "奶奶保存成功", grandmother),
                    ),
                )
            )

        self.assertEqual([True, True], results)
        self.assertEqual(
            {grandfather_id, grandmother_id},
            {
                call.effective_user_id
                for call in isolated_memory.save_calls
            },
        )
        self.assertNotIn(
            grandfather_id,
            isolated_memory.memories,
        )
        self.assertIn(
            grandmother_id,
            isolated_memory.memories,
        )
        self.assertEqual(
            "official_device_role",
            isolated_memory.role_id,
        )

    def test_read_profile_save_and_tool_failures_remain_fail_closed(self):
        grandfather_id = "home_001:grandfather_001"
        grandmother_id = "home_001:grandmother_001"
        grandfather = self.identity.person_context(
            self.living,
            "grandfather_001",
        )
        grandmother = self.identity.person_context(
            self.bedroom,
            "grandmother_001",
        )
        self.memory.query_failures.add(grandfather_id)
        self.memory.profiles[grandmother_id] = "奶奶画像"
        self.run_chat(self.living, "读取失败仍回答", grandfather)
        self.run_chat(self.bedroom, "画像正常回答", grandmother)
        self.assertEqual(
            [grandfather_id, grandmother_id],
            [call.effective_user_id for call in self.memory.query_calls],
        )
        self.assertEqual(2, len(self.memory.save_calls))
        self.assertIsNot(
            self.living.dialogue,
            self.living.get_dialogue_for_turn(grandfather),
        )
        self.assertNotIn(
            "奶奶画像",
            str(self.living.llm.dialogues),
        )

        child_id = "home_001:child_001"
        child = self.identity.person_context(
            self.living,
            "child_001",
        )
        self.memory.profile_failures.add(child_id)
        self.run_chat(self.living, "画像失败仍回答", child)
        self.assertEqual(
            child_id,
            self.memory.profile_calls[-1].effective_user_id,
        )
        self.assertNotIn(
            "奶奶画像",
            str(self.living.llm.dialogues[-1]),
        )

        caregiver_id = "home_001:caregiver_001"
        caregiver = self.identity.person_context(
            self.living,
            "caregiver_001",
        )
        self.memory.save_failures.add(caregiver_id)
        self.run_chat(self.living, "保存失败仍回答", caregiver)
        caregiver_saves = [
            call
            for call in self.memory.save_calls
            if call.effective_user_id == caregiver_id
        ]
        self.assertEqual(1, len(caregiver_saves))
        self.assertEqual(
            "assistant",
            self.living.get_dialogue_for_turn(caregiver).dialogue[-1].role,
        )

        tool_connection = self.connection("tool_failure_device")
        tool_connection.enable_tools("raise")
        tool_context = self.identity.person_context(
            tool_connection,
            "grandfather_001",
        )
        self.run_chat(
            tool_connection,
            "工具失败问题",
            tool_context,
        )
        tool_saves = [
            call
            for call in self.memory.save_calls
            if call.messages
            and call.messages[0][1] == "工具失败问题"
        ]
        self.assertEqual(1, len(tool_saves))
        self.assertNotIn(
            "fake tool failure",
            str(tool_saves[0].messages),
        )

    def test_llm_exception_and_interrupted_turn_never_save(self):
        context = self.identity.person_context(
            self.living,
            "grandfather_001",
        )
        self.living.llm.raise_queries.add("LLM异常")
        self.living.llm.stream_fail_queries.add("流式中断")

        self.run_chat(self.living, "LLM异常", context)
        self.run_chat(self.living, "流式中断", context)

        interrupted = self.connection("interrupted_device")
        interrupted_context = self.identity.person_context(
            interrupted,
            "grandfather_001",
        )

        def interrupted_stream(session_id, dialogue):
            def chunks():
                yield "部分回答"
                interrupted.client_abort = True
                yield "不应完成"

            return chunks()

        interrupted.llm.response = interrupted_stream
        self.run_chat(
            interrupted,
            "用户主动中断",
            interrupted_context,
        )

        saved_queries = {
            call.messages[0][1]
            for call in self.memory.save_calls
            if call.messages
        }
        self.assertNotIn("LLM异常", saved_queries)
        self.assertNotIn("流式中断", saved_queries)
        self.assertNotIn("用户主动中断", saved_queries)

    def test_disconnect_and_official_mode_keep_their_separate_rules(self):
        context = self.identity.person_context(
            self.living,
            "grandfather_001",
        )
        self.run_chat(self.living, "家庭模式完整轮次", context)
        family_save_count = len(self.memory.save_calls)
        family_store = self.living.family_session_dialogues
        family_ws = FakeWebSocket(self.living.device_id)
        ImmediateThread.targets = []
        with patch.object(threading, "Thread", ImmediateThread):
            asyncio.run(self.living._save_and_close(family_ws))

        self.assertEqual(
            ["generate_title_task"],
            ImmediateThread.targets,
        )
        self.assertEqual(family_save_count, len(self.memory.save_calls))
        self.assertEqual({}, family_store._personal_dialogues)
        self.assertIsNone(family_store._anonymous_dialogue)
        self.assertTrue(family_ws.closed)

        official_memory = FakeMemoryProvider(
            role_id="bedroom_device"
        )
        official = self.connection(
            "bedroom_device",
            family_enabled=False,
            memory=official_memory,
        )
        self.assertIsNone(official.family_session_dialogues)
        self.assertIs(
            official.dialogue,
            official.get_dialogue_for_turn(None),
        )
        for query in (
            "官方文本入口",
            "官方问候入口",
            "官方语音入口",
        ):
            self.run_chat(official, query)
        official.enable_tools(ACTION.REQLLM)
        self.run_chat(official, "直接回答")
        self.run_chat(official, "请调用工具")
        self.assertTrue(
            all(
                call.effective_user_id == "bedroom_device"
                for call in official_memory.query_calls
            )
        )
        self.assertEqual([], official_memory.save_calls)

        official_ws = FakeWebSocket(official.device_id)
        ImmediateThread.targets = []
        with patch.object(threading, "Thread", ImmediateThread):
            asyncio.run(official._save_and_close(official_ws))
        self.assertEqual(
            ["generate_title_task", "save_memory_task"],
            ImmediateThread.targets,
        )
        self.assertEqual(1, len(official_memory.save_calls))
        self.assertEqual(
            "bedroom_device",
            official_memory.save_calls[0].effective_user_id,
        )
        self.assertTrue(official_ws.closed)


if __name__ == "__main__":
    unittest.main()
