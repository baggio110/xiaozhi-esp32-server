"""完整 B4a 短期 Dialogue 路由与静态模板测试。"""

import ast
import asyncio
import json
import queue
import types
import unittest
import uuid
from pathlib import Path
from typing import Optional

from core.family_identity import (
    FamilySessionDialogueStore,
    IdentityDecision,
    IdentityStatus,
    PersonIdentity,
    TurnIdentityContext,
)
from core.utils.dialogue import Dialogue, Message


SERVER_ROOT = Path(__file__).resolve().parents[2]
CONNECTION_PATH = SERVER_ROOT / "core" / "connection.py"
RECEIVE_AUDIO_PATH = (
    SERVER_ROOT / "core" / "handle" / "receiveAudioHandle.py"
)
INTENT_HANDLER_PATH = (
    SERVER_ROOT / "core" / "handle" / "intentHandler.py"
)
HELLO_HANDLER_PATH = (
    SERVER_ROOT / "core" / "handle" / "helloHandle.py"
)
LISTEN_HANDLER_PATH = (
    SERVER_ROOT
    / "core"
    / "handle"
    / "textHandler"
    / "listenMessageHandler.py"
)
INTENT_LLM_PATH = (
    SERVER_ROOT
    / "core"
    / "providers"
    / "intent"
    / "intent_llm"
    / "intent_llm.py"
)


def connection_class_ast():
    tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ConnectionHandler"
    )


def connection_method_ast(method_name):
    return next(
        node
        for node in connection_class_ast().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )


def load_routing_methods():
    method_names = {
        "_create_family_dialogue",
        "get_dialogue_for_turn",
    }
    methods = [
        node
        for node in connection_class_ast().body
        if isinstance(node, ast.FunctionDef)
        and node.name in method_names
    ]
    module = ast.Module(body=methods, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {}
    exec(compile(module, str(CONNECTION_PATH), "exec"), namespace)
    return (
        namespace["_create_family_dialogue"],
        namespace["get_dialogue_for_turn"],
    )


CREATE_FAMILY_DIALOGUE, GET_DIALOGUE_FOR_TURN = load_routing_methods()


def load_chat_method():
    chat = connection_method_ast("chat")
    module = ast.Module(body=[chat], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Optional": Optional,
        "uuid": uuid,
        "asyncio": asyncio,
        "json": json,
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
    }
    exec(compile(module, str(CONNECTION_PATH), "exec"), namespace)
    return namespace["chat"]


CHAT = load_chat_method()


class RoutingConnection:
    """只承载生产选择方法，不初始化真实 ConnectionHandler。"""

    _create_family_dialogue = CREATE_FAMILY_DIALOGUE
    get_dialogue_for_turn = GET_DIALOGUE_FOR_TURN

    def __init__(self, enabled):
        self.dialogue = Dialogue()
        self.family_session_dialogues = (
            FamilySessionDialogueStore(self._create_family_dialogue)
            if enabled
            else None
        )


class FakeLogger:
    def bind(self, **kwargs):
        return self

    def info(self, message):
        pass

    def debug(self, message):
        pass

    def error(self, message):
        raise AssertionError(message)


class FakeTTS:
    def __init__(self):
        self.tts_text_queue = queue.Queue()

    def store_tts_text(self, sentence_id, text):
        pass


class FakeLLM:
    def __init__(self):
        self.dialogues = []

    def response(self, session_id, dialogue):
        self.dialogues.append(dialogue)
        return iter(["测试答复"])


class ChatRoutingConnection(RoutingConnection):
    chat = CHAT

    def __init__(self, enabled):
        super().__init__(enabled)
        self.dialogue.put(Message(role="system", content="系统提示"))
        self.logger = FakeLogger()
        self.tts = FakeTTS()
        self.llm = FakeLLM()
        self.memory = None
        self.intent_type = "nointent"
        self.session_id = "session_001"
        self.sentence_id = None
        self.current_speaker = None
        self.system_introduced_speakers = set()
        self.features = {"emoji": False}
        self.config = {"voiceprint": {}}
        self.client_abort = False
        self.loop = None


def recognized_context(person_id, display_name):
    return TurnIdentityContext(
        session_id="session_001",
        turn_id=f"turn_{person_id}",
        device_id="device_001",
        identity_decision=IdentityDecision.recognized(
            PersonIdentity(
                "family_001",
                person_id,
                display_name,
            ),
            f"voiceprint_{person_id}",
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


class DialogueRoutingBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.father_context = recognized_context(
            "person_father",
            "爸爸",
        )
        self.mother_context = recognized_context(
            "person_mother",
            "妈妈",
        )

    def test_disabled_family_mode_always_returns_official_dialogue(self):
        connection = RoutingConnection(False)

        self.assertIs(
            connection.dialogue,
            connection.get_dialogue_for_turn(self.father_context),
        )
        self.assertIs(
            connection.dialogue,
            connection.get_dialogue_for_turn(None),
        )

    def test_father_reuses_dialogue_and_mother_is_isolated(self):
        connection = RoutingConnection(True)

        father = connection.get_dialogue_for_turn(self.father_context)
        father.put(Message(role="user", content="爸爸历史"))
        father_again = connection.get_dialogue_for_turn(
            self.father_context
        )
        mother = connection.get_dialogue_for_turn(self.mother_context)

        self.assertIs(father, father_again)
        self.assertIsNot(father, mother)
        self.assertEqual("爸爸历史", father.dialogue[-1].content)
        self.assertEqual([], mother.dialogue)

    def test_all_failure_states_and_none_share_anonymous_dialogue(self):
        connection = RoutingConnection(True)
        anonymous = connection.get_dialogue_for_turn(None)
        anonymous.put(Message(role="user", content="匿名连续历史"))

        failure_statuses = [
            status
            for status in IdentityStatus
            if status is not IdentityStatus.RECOGNIZED
        ]
        for status in failure_statuses:
            with self.subTest(status=status):
                selected = connection.get_dialogue_for_turn(
                    denied_context(status)
                )
                self.assertIs(anonymous, selected)
                self.assertEqual(
                    "匿名连续历史",
                    selected.dialogue[-1].content,
                )

    def test_father_anonymous_mother_histories_never_merge(self):
        connection = RoutingConnection(True)
        father = connection.get_dialogue_for_turn(self.father_context)
        anonymous = connection.get_dialogue_for_turn(None)
        mother = connection.get_dialogue_for_turn(self.mother_context)

        father.put(Message(role="user", content="爸爸"))
        anonymous.put(Message(role="user", content="匿名"))
        mother.put(Message(role="user", content="妈妈"))

        self.assertEqual(
            ["爸爸"],
            [message.content for message in father.dialogue],
        )
        self.assertEqual(
            ["匿名"],
            [message.content for message in anonymous.dialogue],
        )
        self.assertEqual(
            ["妈妈"],
            [message.content for message in mother.dialogue],
        )
        self.assertIs(
            father,
            connection.get_dialogue_for_turn(self.father_context),
        )

    def test_same_person_is_isolated_across_connections(self):
        living_room = RoutingConnection(True)
        bedroom = RoutingConnection(True)

        first = living_room.get_dialogue_for_turn(
            self.father_context
        )
        second = bedroom.get_dialogue_for_turn(
            self.father_context
        )

        self.assertIsNot(first, second)

    def test_static_template_is_deep_copied_without_dynamic_history(self):
        connection = RoutingConnection(True)
        system = Message(role="system", content="系统提示")
        fewshot = Message(
            role="assistant",
            tool_calls=[
                {
                    "id": "fewshot_001",
                    "function": {"name": "demo", "arguments": "{}"},
                }
            ],
            is_temporary=True,
        )
        connection.dialogue.put(system)
        connection.dialogue.put(fewshot)
        connection.dialogue.put(Message(role="user", content="真实用户历史"))
        connection.dialogue.put(
            Message(role="assistant", content="真实助手历史")
        )
        connection.dialogue.put(Message(role="tool", content="真实工具结果"))

        father = connection.get_dialogue_for_turn(self.father_context)
        mother = connection.get_dialogue_for_turn(self.mother_context)

        self.assertEqual(
            ["system", "assistant"],
            [message.role for message in father.dialogue],
        )
        self.assertTrue(father.dialogue[1].is_temporary)
        self.assertIsNot(father.dialogue[0], system)
        self.assertIsNot(father.dialogue[1], fewshot)
        self.assertIsNot(
            father.dialogue[1].tool_calls,
            mother.dialogue[1].tool_calls,
        )

        father.dialogue[0].content = "爸爸专属修改"
        father.dialogue[1].tool_calls[0]["function"]["name"] = "changed"
        self.assertEqual("系统提示", system.content)
        self.assertEqual(
            "demo",
            mother.dialogue[1].tool_calls[0]["function"]["name"],
        )

    def test_chat_reads_and_writes_only_selected_dialogue(self):
        connection = ChatRoutingConnection(True)

        connection.chat(
            "爸爸问题",
            turn_identity_context=self.father_context,
        )
        connection.current_speaker = "妈妈"
        connection.chat(
            "妈妈问题",
            turn_identity_context=self.mother_context,
        )
        connection.chat(
            "爸爸追问",
            turn_identity_context=self.father_context,
        )

        father = connection.get_dialogue_for_turn(self.father_context)
        mother = connection.get_dialogue_for_turn(self.mother_context)
        self.assertEqual(
            ["系统提示", "爸爸问题", "测试答复", "爸爸追问", "测试答复"],
            [message.content for message in father.dialogue],
        )
        self.assertEqual(
            ["系统提示", "妈妈问题", "测试答复"],
            [message.content for message in mother.dialogue],
        )
        self.assertEqual(
            ["系统提示"],
            [message.content for message in connection.dialogue.dialogue],
        )
        self.assertNotIn(
            "爸爸问题",
            [
                message["content"]
                for message in connection.llm.dialogues[1]
                if "content" in message
            ],
        )

    def test_chat_without_context_uses_anonymous_dialogue(self):
        connection = ChatRoutingConnection(True)

        connection.chat("匿名问题")

        anonymous = connection.get_dialogue_for_turn(None)
        self.assertEqual(
            ["系统提示", "匿名问题", "测试答复"],
            [message.content for message in anonymous.dialogue],
        )
        self.assertEqual(1, len(connection.dialogue.dialogue))

    def test_disabled_chat_keeps_official_dialogue_behavior(self):
        connection = ChatRoutingConnection(False)

        connection.chat("官方问题")

        self.assertEqual(
            ["系统提示", "官方问题", "测试答复"],
            [
                message.content
                for message in connection.dialogue.dialogue
            ],
        )


class DialogueRoutingSourceContractTest(unittest.TestCase):
    def test_chat_uses_one_local_active_dialogue(self):
        chat = connection_method_ast("chat")
        source_attributes = [
            node
            for node in ast.walk(chat)
            if isinstance(node, ast.Attribute)
        ]

        self.assertTrue(
            any(
                node.attr == "get_dialogue_for_turn"
                for node in source_attributes
            )
        )
        self.assertFalse(
            any(
                isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr == "dialogue"
                for node in source_attributes
            )
        )

    def test_tool_result_helper_uses_explicit_dialogue(self):
        helper = connection_method_ast("_handle_function_result")
        keyword_names = {
            argument.arg for argument in helper.args.kwonlyargs
        }
        self.assertIn("dialogue", keyword_names)
        self.assertFalse(
            any(
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr == "dialogue"
                for node in ast.walk(helper)
            )
        )

    def test_no_chat_path_reassigns_official_dialogue(self):
        chat = connection_method_ast("chat")
        self.assertFalse(
            any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == "dialogue"
                for node in ast.walk(chat)
                if isinstance(node, ast.Assign)
                for target in node.targets
            )
        )

    def test_intent_pipeline_receives_selected_dialogue(self):
        receive_tree = ast.parse(
            RECEIVE_AUDIO_PATH.read_text(encoding="utf-8")
        )
        start_to_chat = next(
            node
            for node in receive_tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "startToChat"
        )
        intent_call = next(
            node
            for node in ast.walk(start_to_chat)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "handle_user_intent"
        )
        dialogue_keyword = next(
            keyword
            for keyword in intent_call.keywords
            if keyword.arg == "dialogue"
        )

        self.assertIsInstance(dialogue_keyword.value, ast.Name)
        self.assertEqual(
            "active_dialogue",
            dialogue_keyword.value.id,
        )

    def test_direct_entries_no_longer_write_conn_dialogue(self):
        for path in [
            INTENT_HANDLER_PATH,
            HELLO_HANDLER_PATH,
            LISTEN_HANDLER_PATH,
            INTENT_LLM_PATH,
        ]:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("conn.dialogue", source)

    def test_anonymous_entries_select_none_context(self):
        for path in [HELLO_HANDLER_PATH, LISTEN_HANDLER_PATH]:
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                selectors = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get_dialogue_for_turn"
                ]
                self.assertTrue(selectors)
                self.assertTrue(
                    any(
                        len(call.args) == 1
                        and isinstance(call.args[0], ast.Constant)
                        and call.args[0].value is None
                        for call in selectors
                    )
                )

    def test_text_and_no_voice_chat_entries_have_no_person_context(self):
        receive_tree = ast.parse(
            RECEIVE_AUDIO_PATH.read_text(encoding="utf-8")
        )
        no_voice = next(
            node
            for node in receive_tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "no_voice_close_connect"
        )
        no_voice_chat = next(
            node
            for node in ast.walk(no_voice)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "startToChat"
        )
        self.assertFalse(
            any(
                keyword.arg == "turn_identity_context"
                for keyword in no_voice_chat.keywords
            )
        )

        listen_tree = ast.parse(
            LISTEN_HANDLER_PATH.read_text(encoding="utf-8")
        )
        text_chat_calls = [
            node
            for node in ast.walk(listen_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "startToChat"
        ]
        self.assertTrue(text_chat_calls)
        self.assertTrue(
            all(
                not any(
                    keyword.arg == "turn_identity_context"
                    for keyword in call.keywords
                )
                for call in text_chat_calls
            )
        )

    def test_intent_llm_cleans_selected_history_in_place(self):
        source = INTENT_LLM_PATH.read_text(encoding="utf-8")
        self.assertIn("dialogue_history[:] = clean_history", source)
        self.assertNotIn("conn.dialogue", source)
