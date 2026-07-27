"""B4c PowerMem 逐轮个人写入与断开保存兼容测试。"""

import ast
import asyncio
import json
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
    FamilySessionDialogueStore,
    IdentityDecision,
    IdentityStatus,
    MemoryAccessPolicy,
    PersonIdentity,
    TurnIdentityContext,
    build_memory_user_id,
)
from core.providers.memory.powermem.powermem import (
    MemoryProvider as PowerMemProvider,
)
from core.utils.dialogue import Dialogue, Message


SERVER_ROOT = Path(__file__).resolve().parents[2]
CONNECTION_PATH = SERVER_ROOT / "core" / "connection.py"
OMITTED = object()
REAL_THREAD = threading.Thread


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


ACTION = types.SimpleNamespace(
    RESPONSE="response",
    NOTFOUND="notfound",
    ERROR="error",
    REQLLM="reqllm",
    RECORD="record",
)
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
    "ActionResponse": lambda **kwargs: types.SimpleNamespace(**kwargs),
    "DIRECT_ANSWER_TOOL": {"name": "direct_answer"},
    "enqueue_tool_report": lambda *args, **kwargs: None,
    "get_system_error_response": lambda config: "系统错误",
    "TAG": "test.connection",
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
SAVE_AND_CLOSE = load_connection_method(
    "_save_and_close",
    {
        "asyncio": asyncio,
        "threading": threading,
        "generate_and_save_chat_title": lambda session_id: None,
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
                "测试替身协程不应发生异步挂起"
            )

    def result(self, timeout=None):
        if self._error is not None:
            raise self._error
        return self._value


class RecordingLogger:
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
        self.errors.append(message)


class RecordingTTS:
    def __init__(self):
        self.tts_text_queue = queue.Queue()
        self.sentences = []

    def store_tts_text(self, sentence_id, text):
        pass

    def tts_one_sentence(self, connection, content_type, content_detail):
        self.sentences.append(content_detail)


class StreamingLLM:
    def __init__(self, chunks):
        self.chunks = chunks
        self.dialogues = []

    def response(self, session_id, dialogue):
        self.dialogues.append(dialogue)
        if callable(self.chunks):
            return self.chunks()
        return iter(self.chunks)


class RecordingMemory:
    def __init__(self, *, save_error=None):
        self.query_calls = []
        self.save_calls = []
        self.save_error = save_error

    async def query_memory(self, query, user_id=OMITTED):
        self.query_calls.append((query, user_id))
        return ""

    async def save_memory(
        self,
        messages,
        session_id=None,
        user_id=OMITTED,
    ):
        self.save_calls.append((messages, session_id, user_id))
        if self.save_error is not None:
            raise self.save_error


class TestConnection:
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

    def __init__(
        self,
        *,
        family_enabled=True,
        memory=None,
        llm=None,
    ):
        self.dialogue = Dialogue()
        self.dialogue.put(
            Message(role="system", content="静态系统提示")
        )
        self.family_session_dialogues = (
            FamilySessionDialogueStore(
                lambda: self.dialogue.copy_static_context()
            )
            if family_enabled
            else None
        )
        self.memory = memory
        self.llm = llm or StreamingLLM(["默认回答"])
        self.logger = RecordingLogger()
        self.tts = RecordingTTS()
        self.intent_type = "nointent"
        self.session_id = "session_001"
        self.sentence_id = None
        self.current_speaker = None
        self.system_introduced_speakers = set()
        self.features = {"emoji": False}
        self.config = {"voiceprint": {}, "tool_call_timeout": 30}
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

    @staticmethod
    def _merge_tool_calls(target, source):
        target.extend(source)


def recognized_context(person_id):
    return arbitrary_context(
        "family_001",
        person_id,
        person_id,
    )


def arbitrary_context(family_id, person_id, display_name):
    return TurnIdentityContext(
        session_id="session_001",
        turn_id=f"turn_{person_id}",
        device_id="device_001",
        identity_decision=IdentityDecision.recognized(
            PersonIdentity(
                family_id=family_id,
                person_id=person_id,
                display_name=display_name,
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


def write_denied_recognized_context():
    decision = object.__new__(IdentityDecision)
    values = {
        "family_id": "family_001",
        "person_id": "person_father",
        "voiceprint_id": "voiceprint_father",
        "display_name": "父亲",
        "memory_user_id": "family_001:person_father",
        "identity_status": IdentityStatus.RECOGNIZED,
        "allow_memory_read": True,
        "allow_memory_write": False,
        "failure_reason": None,
    }
    for name, value in values.items():
        object.__setattr__(decision, name, value)
    return TurnIdentityContext(
        session_id="session_001",
        turn_id="turn_write_denied",
        device_id="device_001",
        identity_decision=decision,
    )


def turn_state(user_content, *assistant_parts):
    return {
        "user_content": user_content,
        "assistant_parts": list(assistant_parts),
        "incomplete": False,
        "save_attempted": False,
    }


class ArbitraryFamilyMemberRegressionTest(unittest.TestCase):
    def run_chat(self, connection, query, context):
        with patch.object(
            asyncio,
            "run_coroutine_threadsafe",
            side_effect=lambda coroutine, loop: ImmediateFuture(coroutine),
        ):
            connection.chat(
                query,
                turn_identity_context=context,
            )

    def assert_person_routes_independently(
        self,
        person_id,
        display_name,
    ):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        context = arbitrary_context(
            "home_001",
            person_id,
            display_name,
        )

        self.run_chat(connection, f"{display_name}的问题", context)

        expected_user_id = f"home_001:{person_id}"
        self.assertEqual(
            [(f"{display_name}的问题", expected_user_id)],
            memory.query_calls,
        )
        self.assertEqual(
            [expected_user_id],
            [call[2] for call in memory.save_calls],
        )
        return connection, context

    def test_grandfather_has_independent_read_and_write_route(self):
        self.assert_person_routes_independently(
            "grandfather_001",
            "爷爷",
        )

    def test_child_has_independent_read_and_write_route(self):
        self.assert_person_routes_independently(
            "child_001",
            "孩子",
        )

    def test_two_children_with_same_name_use_different_memories(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        contexts = [
            arbitrary_context(
                "home_001",
                person_id,
                "孩子",
            )
            for person_id in ("child_001", "child_002")
        ]

        for index, context in enumerate(contexts, start=1):
            self.run_chat(connection, f"第{index}个问题", context)

        self.assertEqual(
            [
                "home_001:child_001",
                "home_001:child_002",
            ],
            [call[1] for call in memory.query_calls],
        )
        self.assertEqual(
            [
                "home_001:child_001",
                "home_001:child_002",
            ],
            [call[2] for call in memory.save_calls],
        )
        self.assertIsNot(
            connection.get_dialogue_for_turn(contexts[0]),
            connection.get_dialogue_for_turn(contexts[1]),
        )

    def test_arbitrary_legal_person_id_uses_complete_common_flow(self):
        connection, context = self.assert_person_routes_independently(
            "relative-2026_A.17",
            "家庭成员",
        )
        dialogue = connection.get_dialogue_for_turn(context)
        self.assertEqual(
            ["user", "assistant"],
            [message.role for message in dialogue.dialogue[-2:]],
        )

    def test_dialogue_store_has_no_fixed_member_count_limit(self):
        connection = TestConnection(memory=RecordingMemory())
        dialogues = {
            id(
                connection.get_dialogue_for_turn(
                    arbitrary_context(
                        "home_001",
                        f"member_{index:03d}",
                        f"成员{index}",
                    )
                )
            )
            for index in range(128)
        }
        self.assertEqual(128, len(dialogues))

    def test_display_and_speaker_names_do_not_build_memory_user_id(self):
        first = arbitrary_context(
            "home_001",
            "caregiver_001",
            "爷爷",
        )
        renamed = arbitrary_context(
            "home_001",
            "caregiver_001",
            "照护人员",
        )
        self.assertEqual(
            "home_001:caregiver_001",
            first.identity_decision.memory_user_id,
        )
        self.assertEqual(
            first.identity_decision.memory_user_id,
            renamed.identity_decision.memory_user_id,
        )

    def test_same_display_name_with_different_ids_is_isolated(self):
        contexts = [
            arbitrary_context(
                "home_001",
                person_id,
                "同名成员",
            )
            for person_id in ("relative_001", "relative_002")
        ]
        self.assertEqual(
            {
                "home_001:relative_001",
                "home_001:relative_002",
            },
            {
                context.identity_decision.memory_user_id
                for context in contexts
            },
        )

    def test_all_relationship_labels_use_the_same_identity_model(self):
        labels = (
            ("grandfather_001", "爷爷"),
            ("grandmother_001", "奶奶"),
            ("child_001", "孩子"),
            ("relative_001", "亲戚"),
            ("caregiver_001", "照护人员"),
            ("member_001", "Alex"),
        )
        self.assertEqual(
            {
                f"home_001:{person_id}"
                for person_id, _ in labels
            },
            {
                arbitrary_context(
                    "home_001",
                    person_id,
                    display_name,
                ).identity_decision.memory_user_id
                for person_id, display_name in labels
            },
        )


class ConnectionTurnWritePolicyTest(unittest.TestCase):
    def save_state(self, connection, state, context):
        with patch.object(
            asyncio,
            "run_coroutine_threadsafe",
            side_effect=lambda coroutine, loop: ImmediateFuture(coroutine),
        ):
            connection._save_family_turn_memory(state, context)

    def test_father_turn_saves_exact_two_messages_once(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)

        self.save_state(
            connection,
            turn_state("父亲问题", "父亲", "回答"),
            recognized_context("person_father"),
        )

        self.assertEqual(1, len(memory.save_calls))
        messages, session_id, user_id = memory.save_calls[0]
        self.assertIsNone(session_id)
        self.assertEqual(
            "family_001:person_father",
            user_id,
        )
        self.assertEqual(
            [("user", "父亲问题"), ("assistant", "父亲回答")],
            [(message.role, message.content) for message in messages],
        )

    def test_mother_turn_uses_mother_memory_user_id(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        self.save_state(
            connection,
            turn_state("母亲问题", "母亲回答"),
            recognized_context("person_mother"),
        )
        self.assertEqual(
            "family_001:person_mother",
            memory.save_calls[0][2],
        )

    def test_father_and_mother_turns_never_cross(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        for person_id, question, answer in (
            ("person_father", "父亲问题", "父亲回答"),
            ("person_mother", "母亲问题", "母亲回答"),
        ):
            self.save_state(
                connection,
                turn_state(question, answer),
                recognized_context(person_id),
            )
        self.assertEqual(
            [
                (
                    "family_001:person_father",
                    ["父亲问题", "父亲回答"],
                ),
                (
                    "family_001:person_mother",
                    ["母亲问题", "母亲回答"],
                ),
            ],
            [
                (
                    call[2],
                    [message.content for message in call[0]],
                )
                for call in memory.save_calls
            ],
        )

    def test_father_anonymous_mother_only_saves_people(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        for state, context in (
            (
                turn_state("父亲问题", "父亲回答"),
                recognized_context("person_father"),
            ),
            (turn_state("匿名问题", "匿名回答"), None),
            (
                turn_state("母亲问题", "母亲回答"),
                recognized_context("person_mother"),
            ),
        ):
            self.save_state(connection, state, context)
        self.assertEqual(
            [
                "family_001:person_father",
                "family_001:person_mother",
            ],
            [call[2] for call in memory.save_calls],
        )

    def test_all_failure_statuses_make_zero_writes(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        for status in IdentityStatus:
            if status is IdentityStatus.RECOGNIZED:
                continue
            with self.subTest(status=status):
                self.save_state(
                    connection,
                    turn_state("问题", "回答"),
                    denied_context(status),
                )
        self.assertEqual([], memory.save_calls)

    def test_none_context_makes_zero_writes(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        self.save_state(
            connection,
            turn_state("匿名问题", "匿名回答"),
            None,
        )
        self.assertEqual([], memory.save_calls)

    def test_invalid_recognized_context_never_falls_back_to_device(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        self.save_state(
            connection,
            turn_state("问题", "回答"),
            invalid_recognized_context(),
        )
        self.assertEqual([], memory.save_calls)

    def test_recognized_without_write_permission_makes_zero_writes(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        self.save_state(
            connection,
            turn_state("问题", "回答"),
            write_denied_recognized_context(),
        )
        self.assertEqual([], memory.save_calls)

    def test_empty_user_input_is_not_saved(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        self.save_state(
            connection,
            turn_state("  ", "回答"),
            recognized_context("person_father"),
        )
        self.assertEqual([], memory.save_calls)

    def test_empty_assistant_answer_is_not_saved(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        self.save_state(
            connection,
            turn_state("问题", "", "  "),
            recognized_context("person_father"),
        )
        self.assertEqual([], memory.save_calls)

    def test_incomplete_turn_is_not_saved(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        state = turn_state("问题", "部分回答")
        state["incomplete"] = True
        self.save_state(
            connection,
            state,
            recognized_context("person_father"),
        )
        self.assertEqual([], memory.save_calls)

    def test_save_failure_does_not_retry_or_fallback(self):
        memory = RecordingMemory(
            save_error=RuntimeError("PowerMem unavailable")
        )
        connection = TestConnection(memory=memory)
        state = turn_state("问题", "已生成回答")
        self.save_state(
            connection,
            state,
            recognized_context("person_father"),
        )
        self.save_state(
            connection,
            state,
            recognized_context("person_father"),
        )
        self.assertEqual(1, len(memory.save_calls))
        self.assertEqual(
            "family_001:person_father",
            memory.save_calls[0][2],
        )
        self.assertEqual(1, len(connection.logger.errors))

    def test_current_speaker_change_does_not_change_turn_identity(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        context = recognized_context("person_father")
        connection.current_speaker = "妈妈"
        self.save_state(
            connection,
            turn_state("父亲问题", "回答"),
            context,
        )
        self.assertEqual(
            "family_001:person_father",
            memory.save_calls[0][2],
        )

    def test_saved_content_excludes_dialogue_history_and_injections(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        connection.dialogue.put(
            Message(role="system", content="memory和profile注入")
        )
        connection.dialogue.put(
            Message(role="tool", content="工具原始载荷")
        )
        connection.dialogue.put(
            Message(role="assistant", content="其他历史")
        )
        self.save_state(
            connection,
            turn_state("本轮问题", "本轮回答"),
            recognized_context("person_father"),
        )
        saved = memory.save_calls[0][0]
        self.assertEqual(
            ["本轮问题", "本轮回答"],
            [message.content for message in saved],
        )

    def test_save_attempt_guard_limits_turn_to_one_write(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        state = turn_state("问题", "回答")
        context = recognized_context("person_father")
        self.save_state(connection, state, context)
        self.save_state(connection, state, context)
        self.assertEqual(1, len(memory.save_calls))

    def test_non_voice_entry_contexts_make_zero_private_writes(self):
        for entry in ("文本", "问候", "无语音提示", "来电"):
            with self.subTest(entry=entry):
                memory = RecordingMemory()
                connection = TestConnection(memory=memory)
                self.save_state(
                    connection,
                    turn_state(f"{entry}输入", "正常回答"),
                    None,
                )
                self.assertEqual([], memory.save_calls)


class ChatTurnCompletionTest(unittest.TestCase):
    def run_chat(self, connection, query, context=None):
        with patch.object(
            asyncio,
            "run_coroutine_threadsafe",
            side_effect=lambda coroutine, loop: ImmediateFuture(coroutine),
        ):
            return connection.chat(
                query,
                turn_identity_context=context,
            )

    def test_streamed_answer_is_joined_in_order_and_saved_once(self):
        memory = RecordingMemory()
        connection = TestConnection(
            memory=memory,
            llm=StreamingLLM(["第一段", "第二段", "第三段"]),
        )
        self.run_chat(
            connection,
            "用户问题",
            recognized_context("person_father"),
        )
        self.assertEqual(1, len(memory.save_calls))
        self.assertEqual(
            "第一段第二段第三段",
            memory.save_calls[0][0][1].content,
        )

    def test_client_abort_does_not_save_partial_answer(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)

        def interrupted_stream():
            yield "部分回答"
            connection.client_abort = True
            yield "不会处理"

        connection.llm = StreamingLLM(interrupted_stream)
        self.run_chat(
            connection,
            "问题",
            recognized_context("person_father"),
        )
        self.assertEqual([], memory.save_calls)

    def test_stream_exception_does_not_save_partial_answer(self):
        memory = RecordingMemory()

        def broken_stream():
            yield "部分回答"
            raise RuntimeError("stream failed")

        connection = TestConnection(
            memory=memory,
            llm=StreamingLLM(broken_stream),
        )
        self.run_chat(
            connection,
            "问题",
            recognized_context("person_father"),
        )
        self.assertEqual([], memory.save_calls)

    def test_none_context_chat_answers_but_never_saves(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        result = self.run_chat(connection, "匿名问题", None)
        self.assertTrue(result)
        self.assertEqual([], memory.save_calls)

    def test_family_disabled_chat_keeps_disconnect_only_save_model(self):
        memory = RecordingMemory()
        connection = TestConnection(
            family_enabled=False,
            memory=memory,
        )
        self.run_chat(connection, "官方问题")
        self.assertEqual([], memory.save_calls)

    def test_reqllm_recursion_uses_one_state_and_one_final_save(self):
        memory = RecordingMemory()
        connection = TestConnection(memory=memory)
        connection.intent_type = "function_call"
        connection.current_speaker = "爸爸"

        class ToolLLM:
            def __init__(self):
                self.calls = 0

            def response_with_functions(
                llm_self,
                session_id,
                dialogue,
                functions,
            ):
                llm_self.calls += 1
                if llm_self.calls == 1:
                    return iter(
                        [
                            (
                                None,
                                [
                                    {
                                        "id": "tool_001",
                                        "name": "lookup",
                                        "arguments": "{}",
                                    }
                                ],
                            )
                        ]
                    )
                return iter([("工具后的最终回答", None)])

        class ToolHandler:
            def get_functions(handler_self):
                return [{"name": "lookup"}]

            async def handle_llm_function_call(
                handler_self,
                conn,
                tool_call_data,
            ):
                conn.current_speaker = "妈妈"
                return types.SimpleNamespace(
                    action=ACTION.REQLLM,
                    result="内部工具结果",
                    response=None,
                )

        connection.llm = ToolLLM()
        connection.func_handler = ToolHandler()
        context = recognized_context("person_father")
        self.run_chat(connection, "查一下", context)

        self.assertEqual(1, len(memory.save_calls))
        messages, _, user_id = memory.save_calls[0]
        self.assertEqual(
            "family_001:person_father",
            user_id,
        )
        self.assertEqual(
            ["查一下", "工具后的最终回答"],
            [message.content for message in messages],
        )
        self.assertEqual(
            ["user", "assistant"],
            [message.role for message in messages],
        )


class DisconnectSaveCompatibilityTest(unittest.TestCase):
    class Store:
        def __init__(self):
            self.clear_calls = 0

        def clear(self):
            self.clear_calls += 1

    class Connection:
        _save_and_close = SAVE_AND_CLOSE

        def __init__(self, *, family_enabled):
            self.memory = RecordingMemory()
            self.dialogue = types.SimpleNamespace(
                dialogue=[
                    Message(role="user", content="历史问题"),
                    Message(role="assistant", content="历史回答"),
                ]
            )
            self.session_id = "session_001"
            self.logger = RecordingLogger()
            self.store = (
                DisconnectSaveCompatibilityTest.Store()
                if family_enabled
                else None
            )
            self.close_calls = 0

        def is_family_memory_active(self):
            return self.store is not None

        async def close(self, ws):
            self.close_calls += 1
            if self.store is not None:
                self.store.clear()

    class ImmediateThread:
        targets = []

        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon
            self.__class__.targets.append(target.__name__)

        def start(self):
            thread = REAL_THREAD(
                target=self.target,
                daemon=self.daemon,
            )
            thread.start()
            thread.join()

    def setUp(self):
        self.ImmediateThread.targets = []

    def test_family_mode_skips_whole_dialogue_memory_thread(self):
        connection = self.Connection(family_enabled=True)
        with patch.object(threading, "Thread", self.ImmediateThread):
            asyncio.run(connection._save_and_close(object()))
        self.assertEqual(["generate_title_task"], self.ImmediateThread.targets)
        self.assertEqual([], connection.memory.save_calls)
        self.assertEqual(1, connection.close_calls)
        self.assertEqual(1, connection.store.clear_calls)

    def test_disabled_mode_keeps_official_daemon_and_arguments(self):
        connection = self.Connection(family_enabled=False)
        with patch.object(threading, "Thread", self.ImmediateThread):
            asyncio.run(connection._save_and_close(object()))
        self.assertEqual(
            ["generate_title_task", "save_memory_task"],
            self.ImmediateThread.targets,
        )
        self.assertEqual(1, len(connection.memory.save_calls))
        messages, session_id, user_id = connection.memory.save_calls[0]
        self.assertIs(connection.dialogue.dialogue, messages)
        self.assertEqual("session_001", session_id)
        self.assertIs(OMITTED, user_id)
        self.assertEqual(1, connection.close_calls)


def make_powermem_provider(client, *, profiles_enabled=False):
    provider = object.__new__(PowerMemProvider)
    provider.config = {}
    provider.role_id = "device_legacy"
    provider.llm = None
    provider.use_powermem = True
    provider.memory_client = client
    provider.enable_user_profile = profiles_enabled
    provider.last_profile_content = ""
    provider._profile_cache = {}
    provider._profile_cache_lock = threading.Lock()
    return provider


class AsyncAddClient:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error

    async def add(self, **kwargs):
        await asyncio.sleep(0)
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"results": []}


class SyncAddClient:
    def __init__(self):
        self.calls = []

    def add(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "profile_extracted": True,
            "profile_content": f"画像:{kwargs['user_id']}",
        }


class PowerMemWriteInterfaceTest(unittest.TestCase):
    @staticmethod
    def messages():
        return [
            Message(role="user", content="问题"),
            Message(role="assistant", content="回答"),
        ]

    def test_legacy_save_uses_role_id(self):
        client = AsyncAddClient()
        provider = make_powermem_provider(client)
        asyncio.run(provider.save_memory(self.messages()))
        self.assertEqual("device_legacy", client.calls[0]["user_id"])

    def test_explicit_save_uses_user_id_without_mutating_role_id(self):
        client = AsyncAddClient()
        provider = make_powermem_provider(client)
        asyncio.run(
            provider.save_memory(
                self.messages(),
                user_id="family_001:person_father",
            )
        )
        self.assertEqual(
            "family_001:person_father",
            client.calls[0]["user_id"],
        )
        self.assertEqual("device_legacy", provider.role_id)

    def test_explicit_empty_user_id_never_falls_back(self):
        client = AsyncAddClient()
        provider = make_powermem_provider(client)
        asyncio.run(
            provider.save_memory(self.messages(), user_id="")
        )
        self.assertEqual([], client.calls)

    def test_user_memory_add_gets_explicit_user_and_partitioned_cache(self):
        client = SyncAddClient()
        provider = make_powermem_provider(
            client,
            profiles_enabled=True,
        )
        user_id = "family_001:person_father"
        asyncio.run(
            provider.save_memory(self.messages(), user_id=user_id)
        )
        self.assertEqual(user_id, client.calls[0]["user_id"])
        self.assertEqual(f"画像:{user_id}", provider._profile_cache[user_id])
        self.assertEqual("", provider.last_profile_content)

    def test_concurrent_people_keep_per_call_user_ids(self):
        client = AsyncAddClient()
        provider = make_powermem_provider(client)

        async def save_both():
            await asyncio.gather(
                provider.save_memory(
                    self.messages(),
                    user_id="family_001:person_father",
                ),
                provider.save_memory(
                    self.messages(),
                    user_id="family_001:person_mother",
                ),
            )

        asyncio.run(save_both())
        self.assertEqual(
            {
                "family_001:person_father",
                "family_001:person_mother",
            },
            {call["user_id"] for call in client.calls},
        )
        self.assertEqual("device_legacy", provider.role_id)

    def test_sdk_exception_is_contained_without_identity_fallback(self):
        client = AsyncAddClient(error=RuntimeError("SDK failed"))
        provider = make_powermem_provider(client)
        asyncio.run(
            provider.save_memory(
                self.messages(),
                user_id="family_001:person_father",
            )
        )
        self.assertEqual(1, len(client.calls))
        self.assertEqual(
            "family_001:person_father",
            client.calls[0]["user_id"],
        )


class WriteSourceContractTest(unittest.TestCase):
    def test_no_connection_role_id_assignment_or_private_user_field(self):
        tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
        role_assignments = [
            target
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
            and target.attr == "role_id"
        ]
        self.assertEqual([], role_assignments)
        self.assertNotIn(
            "current_memory_user_id",
            CONNECTION_PATH.read_text(encoding="utf-8"),
        )

    def test_turn_save_constructs_only_user_and_assistant_messages(self):
        tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
        helper = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_save_family_turn_memory"
        )
        roles = [
            keyword.value.value
            for node in ast.walk(helper)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Message"
            for keyword in node.keywords
            if keyword.arg == "role"
            and isinstance(keyword.value, ast.Constant)
        ]
        self.assertEqual(["user", "assistant"], roles)

    def test_reqllm_recursion_passes_same_turn_save_state(self):
        tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
        helper = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_handle_function_result"
        )
        recursive_call = next(
            node
            for node in ast.walk(helper)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "chat"
        )
        state_keyword = next(
            keyword
            for keyword in recursive_call.keywords
            if keyword.arg == "_turn_memory_state"
        )
        self.assertIsInstance(state_keyword.value, ast.Name)
        self.assertEqual(
            "turn_memory_state",
            state_keyword.value.id,
        )

    def test_family_disconnect_condition_guards_only_memory_thread(self):
        tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
        helper = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_save_and_close"
        )
        source = ast.unparse(helper)
        self.assertIn(
            "self.memory and (not self.is_family_memory_active())",
            source,
        )
        self.assertIn("await self.close(ws)", source)


if __name__ == "__main__":
    unittest.main()
