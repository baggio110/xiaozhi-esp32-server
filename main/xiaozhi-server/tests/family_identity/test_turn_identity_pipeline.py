"""B3b 单轮身份上下文传递测试。"""

import ast
import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.family_identity import (
    FamilyMemoryRuntime,
    FamilyMemorySettings,
    IdentityDecision,
    IdentityStatus,
    PersonIdentity,
    RecognitionResult,
    TurnIdentityContext,
)

from .project_temp import ProjectTemporaryDirectory
from .test_asr_voiceprint_compatibility import (
    FakeVoiceprintProvider,
    load_isolated_asr_base,
)


SERVER_ROOT = Path(__file__).resolve().parents[2]
RECEIVE_AUDIO_PATH = (
    SERVER_ROOT / "core" / "handle" / "receiveAudioHandle.py"
)
WEBSOCKET_SERVER_PATH = SERVER_ROOT / "core" / "websocket_server.py"
CONNECTION_PATH = SERVER_ROOT / "core" / "connection.py"


class FakeLogger:
    """不输出日志的测试替身。"""

    def bind(self, **kwargs):
        return self

    def debug(self, *args, **kwargs) -> None:
        return None

    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        return None


class TurnIdentityAsrPipelineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = ProjectTemporaryDirectory()
        self.server_root = (
            Path(self.temporary_directory.name) / "xiaozhi-server"
        )
        self.server_root.mkdir()
        self.runtime = FamilyMemoryRuntime(
            FamilyMemorySettings(
                enabled=True,
                family_id="family_001",
            ),
            self.server_root,
        )
        repository = self.runtime.start()
        self.father = PersonIdentity(
            "family_001",
            "person_father",
            "爸爸",
        )
        self.mother = PersonIdentity(
            "family_001",
            "person_mother",
            "妈妈",
        )
        self.disabled = PersonIdentity(
            "family_001",
            "person_disabled",
            "停用成员",
            enabled=False,
        )
        for person, voiceprint_id in (
            (self.father, "voiceprint_father"),
            (self.mother, "voiceprint_mother"),
            (self.disabled, "voiceprint_disabled"),
        ):
            repository.save_person(person)
            repository.bind_voiceprint(
                person.family_id,
                person.person_id,
                voiceprint_id,
            )

        (
            self.module,
            self.chat_inputs,
            self.chat_contexts,
            self.reports,
        ) = load_isolated_asr_base()

        class TestAsrProvider(self.module.ASRProviderBase):
            async def speech_to_text_wrapper(
                provider_self,
                pcm_data,
                session_id,
            ):
                return "你好", None

            async def speech_to_text(
                provider_self,
                opus_data,
                session_id,
                artifacts=None,
            ):
                return "你好", None

        self.asr = TestAsrProvider()

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary_directory.cleanup()

    def connection(
        self,
        recognition,
        *,
        session_id="session_001",
        device_id="device_living_room",
        runtime=None,
    ):
        return types.SimpleNamespace(
            voiceprint_provider=FakeVoiceprintProvider(recognition),
            session_id=session_id,
            device_id=device_id,
            family_memory_runtime=runtime or self.runtime,
        )

    async def run_turn(self, recognition, **connection_kwargs):
        await self.asr.handle_voice_stop(
            self.connection(recognition, **connection_kwargs),
            [b"\x00\x00" * 100],
        )
        return self.chat_contexts[-1]

    async def test_success_creates_context_with_stable_identity(self):
        original_service = self.runtime._identity_service

        class CountingIdentityService:
            def __init__(self):
                self.calls = 0

            def resolve(self, family_id, recognition):
                self.calls += 1
                return original_service.resolve(family_id, recognition)

        counting_service = CountingIdentityService()
        self.runtime._identity_service = counting_service
        context = await self.run_turn(
            self.recognized(
                "voiceprint_father",
                "爸爸",
            )
        )

        self.assertEqual(1, counting_service.calls)
        self.assertIsInstance(context, TurnIdentityContext)
        self.assertEqual("session_001", context.session_id)
        self.assertEqual("device_living_room", context.device_id)
        self.assertTrue(context.turn_id)
        self.assertIsNotNone(context.created_at.tzinfo)
        self.assertEqual(
            IdentityStatus.RECOGNIZED,
            context.identity_decision.identity_status,
        )
        self.assertEqual(
            "family_001:person_father",
            context.identity_decision.memory_user_id,
        )

    async def test_failure_results_are_fixed_in_fail_closed_contexts(self):
        cases = (
            (
                self.recognized("voiceprint_unbound", "访客"),
                IdentityStatus.PERSON_NOT_BOUND,
            ),
            (
                RecognitionResult(
                    voiceprint_id="voiceprint_father",
                    speaker_name=None,
                    confidence=0.2,
                    status=IdentityStatus.LOW_CONFIDENCE,
                ),
                IdentityStatus.LOW_CONFIDENCE,
            ),
            (
                RecognitionResult(
                    voiceprint_id=None,
                    speaker_name=None,
                    confidence=None,
                    status=IdentityStatus.SERVICE_UNAVAILABLE,
                ),
                IdentityStatus.SERVICE_UNAVAILABLE,
            ),
            (None, IdentityStatus.INVALID_RESULT),
            (
                self.recognized(
                    "voiceprint_disabled",
                    "停用成员",
                ),
                IdentityStatus.PERSON_DISABLED,
            ),
        )

        for recognition, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                context = await self.run_turn(recognition)
                decision = context.identity_decision

                self.assertEqual(expected_status, decision.identity_status)
                self.assertFalse(decision.allow_memory_read)
                self.assertFalse(decision.allow_memory_write)
                self.assertIsNone(decision.memory_user_id)
                self.assertTrue(self.chat_inputs[-1])

    async def test_two_speakers_keep_independent_immutable_contexts(self):
        father_context = await self.run_turn(
            self.recognized("voiceprint_father", "爸爸")
        )
        mother_context = await self.run_turn(
            self.recognized("voiceprint_mother", "妈妈")
        )

        self.assertIsNot(father_context, mother_context)
        self.assertNotEqual(
            father_context.turn_id,
            mother_context.turn_id,
        )
        self.assertEqual(
            "family_001:person_father",
            father_context.identity_decision.memory_user_id,
        )
        self.assertEqual(
            "family_001:person_mother",
            mother_context.identity_decision.memory_user_id,
        )

    async def test_same_person_on_two_connections_uses_same_memory_id(self):
        recognition = self.recognized("voiceprint_father", "爸爸")

        living_room = await self.run_turn(
            recognition,
            session_id="session_living_room",
            device_id="device_living_room",
        )
        bedroom = await self.run_turn(
            recognition,
            session_id="session_bedroom",
            device_id="device_bedroom",
        )

        self.assertNotEqual(living_room.device_id, bedroom.device_id)
        self.assertEqual(
            living_room.identity_decision.memory_user_id,
            bedroom.identity_decision.memory_user_id,
        )

    async def test_disabled_runtime_skips_identity_resolution(self):
        disabled_runtime = FamilyMemoryRuntime(
            FamilyMemorySettings(),
            self.server_root,
        )
        disabled_runtime.start()

        context = await self.run_turn(
            self.recognized("voiceprint_father", "爸爸"),
            runtime=disabled_runtime,
        )

        self.assertIsNone(context)
        self.assertIsNone(disabled_runtime.repository)

    async def test_identity_service_exception_keeps_chat_running(self):
        class FailingIdentityService:
            def resolve(self, family_id, recognition):
                raise RuntimeError("test-only")

        self.runtime._identity_service = FailingIdentityService()

        context = await self.run_turn(
            self.recognized("voiceprint_father", "爸爸")
        )

        self.assertEqual(
            IdentityStatus.INVALID_RESULT,
            context.identity_decision.identity_status,
        )
        self.assertFalse(context.identity_decision.allow_memory_read)
        self.assertFalse(context.identity_decision.allow_memory_write)
        self.assertIsNone(context.identity_decision.memory_user_id)
        self.assertEqual(
            "你好",
            json.loads(self.chat_inputs[-1])["content"],
        )

    @staticmethod
    def recognized(voiceprint_id, speaker_name):
        return RecognitionResult(
            voiceprint_id=voiceprint_id,
            speaker_name=speaker_name,
            confidence=0.91,
            status=IdentityStatus.RECOGNIZED,
        )


class DelayedExecutor:
    """保存任务，直到测试显式执行。"""

    def __init__(self) -> None:
        self.tasks = []

    def submit(self, function, *args, **kwargs):
        self.tasks.append((function, args, kwargs))

    def run_next(self):
        function, args, kwargs = self.tasks.pop(0)
        return function(*args, **kwargs)


def load_isolated_receive_audio():
    """隔离加载 startToChat，不启动真实业务组件。"""

    async def no_op_async(*args, **kwargs):
        return None

    stubs = {
        "core.utils.util": types.SimpleNamespace(
            audio_to_data=no_op_async
        ),
        "core.handle.abortHandle": types.SimpleNamespace(
            handleAbortMessage=no_op_async
        ),
        "core.handle.intentHandler": types.SimpleNamespace(
            handle_user_intent=no_op_async
        ),
        "core.utils.output_counter": types.SimpleNamespace(
            check_device_output_limit=lambda *args, **kwargs: False
        ),
        "core.handle.sendAudioHandle": types.SimpleNamespace(
            send_stt_message=no_op_async,
            SentenceType=types.SimpleNamespace(LAST="last"),
        ),
    }
    module_name = "receive_audio_under_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        RECEIVE_AUDIO_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class StartToChatContextCaptureTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.module = load_isolated_receive_audio()
        self.executor = DelayedExecutor()
        self.chat_calls = []

        def chat(query, depth=0, *, turn_identity_context=None):
            self.chat_calls.append(
                (query, depth, turn_identity_context)
            )

        self.conn = types.SimpleNamespace(
            logger=FakeLogger(),
            introduced_speakers=set(),
            current_speaker=None,
            need_bind=False,
            max_output_size=0,
            client_is_speaking=False,
            client_listen_mode="auto",
            client_abort=False,
            executor=self.executor,
            chat=chat,
        )

    async def test_delayed_task_keeps_original_turn_context(self):
        context = self.context("person_father", "voiceprint_father")

        await self.module.startToChat(
            self.conn,
            '{"speaker":"爸爸","content":"你好"}',
            turn_identity_context=context,
        )
        self.conn.current_speaker = "妈妈"
        self.executor.run_next()

        self.assertIs(context, self.chat_calls[0][2])
        self.assertEqual(
            "family_001:person_father",
            self.chat_calls[0][2].identity_decision.memory_user_id,
        )

    async def test_existing_caller_without_context_remains_compatible(self):
        await self.module.startToChat(self.conn, "普通文本")
        self.executor.run_next()

        self.assertEqual(("普通文本", 0, None), self.chat_calls[0])

    @staticmethod
    def context(person_id, voiceprint_id):
        person = PersonIdentity("family_001", person_id, "测试成员")
        return TurnIdentityContext(
            session_id="session_001",
            turn_id="turn_001",
            device_id="device_001",
            identity_decision=IdentityDecision.recognized(
                person,
                voiceprint_id,
            ),
        )


def load_isolated_websocket_server(handler_calls):
    """隔离加载 WebSocketServer，验证 Runtime 构造链。"""

    class FakeConnectionHandler:
        def __init__(self, *args, **kwargs):
            handler_calls.append((args, kwargs))

        async def handle_connection(self, websocket):
            return None

    class FakeAuthManager:
        def __init__(self, **kwargs):
            return None

    stubs = {
        "websockets": types.SimpleNamespace(
            ServerConnection=object,
            serve=lambda *args, **kwargs: None,
        ),
        "config.logger": types.SimpleNamespace(
            setup_logging=lambda config=None: FakeLogger()
        ),
        "core.connection": types.SimpleNamespace(
            ConnectionHandler=FakeConnectionHandler
        ),
        "config.config_loader": types.SimpleNamespace(
            get_config_from_api_async=lambda config: config
        ),
        "core.auth": types.SimpleNamespace(
            AuthManager=FakeAuthManager,
            AuthenticationError=RuntimeError,
        ),
        "core.utils.modules_initialize": types.SimpleNamespace(
            initialize_modules=lambda *args, **kwargs: {}
        ),
        "core.utils.util": types.SimpleNamespace(
            check_vad_update=lambda *args, **kwargs: False,
            check_asr_update=lambda *args, **kwargs: False,
        ),
    }
    module_name = "websocket_server_under_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        WEBSOCKET_SERVER_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class RuntimeConstructorChainTest(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_passes_same_runtime_to_connection(self):
        handler_calls = []
        module = load_isolated_websocket_server(handler_calls)
        runtime = object()
        server = module.WebSocketServer(
            {
                "selected_module": [],
                "server": {
                    "auth": {},
                    "auth_key": "test-only",
                },
            },
            family_memory_runtime=runtime,
        )

        async def accept_auth(websocket):
            return None

        server._handle_auth = accept_auth
        websocket = types.SimpleNamespace(
            request=types.SimpleNamespace(
                headers={"device-id": "device_001"},
                path="/xiaozhi/v1/",
            ),
            close=self.async_no_op,
        )

        await server._handle_connection(websocket)

        self.assertEqual(1, len(handler_calls))
        self.assertIs(
            runtime,
            handler_calls[0][1]["family_memory_runtime"],
        )

    @staticmethod
    async def async_no_op():
        return None

    def test_connection_chat_context_parameter_defaults_to_none(self):
        tree = ast.parse(CONNECTION_PATH.read_text(encoding="utf-8"))
        chat = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "chat"
        )
        keyword_defaults = {
            argument.arg: default
            for argument, default in zip(
                chat.args.kwonlyargs,
                chat.args.kw_defaults,
            )
        }

        default = keyword_defaults["turn_identity_context"]
        self.assertIsInstance(default, ast.Constant)
        self.assertIsNone(default.value)
