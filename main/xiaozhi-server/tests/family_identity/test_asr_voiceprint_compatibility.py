"""ASR 消费结构化声纹结果的兼容性测试。"""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.family_identity import IdentityStatus, RecognitionResult


ASR_BASE_PATH = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "providers"
    / "asr"
    / "base.py"
)


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


def load_isolated_asr_base():
    """隔离加载 ASR 基类，避免导入或启动外部业务组件。"""

    chat_inputs = []
    reports = []

    async def start_to_chat(conn, text):
        chat_inputs.append(text)

    def enqueue_report(conn, text, audio):
        reports.append((text, audio))

    stubs = {
        "config.logger": types.SimpleNamespace(
            setup_logging=lambda: FakeLogger()
        ),
        "core.handle.receiveAudioHandle": types.SimpleNamespace(
            handleAudioMessage=lambda *args, **kwargs: None,
            startToChat=start_to_chat,
        ),
        "core.handle.reportHandle": types.SimpleNamespace(
            enqueue_asr_report=enqueue_report
        ),
        "core.utils.util": types.SimpleNamespace(
            remove_punctuation_and_length=lambda text: (len(text), text)
        ),
    }
    module_name = "asr_base_under_test"
    spec = importlib.util.spec_from_file_location(module_name, ASR_BASE_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module, chat_inputs, reports


class FakeVoiceprintProvider:
    """返回预设 RecognitionResult 的声纹替身。"""

    def __init__(self, result) -> None:
        self.result = result

    async def identify_speaker(self, audio_data, session_id):
        return self.result


class AsrVoiceprintCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.module, self.chat_inputs, self.reports = (
            load_isolated_asr_base()
        )

        class TestAsrProvider(self.module.ASRProviderBase):
            def __init__(provider_self):
                provider_self.raw_text = "你好"

            async def speech_to_text_wrapper(
                provider_self,
                pcm_data,
                session_id,
            ):
                return provider_self.raw_text, None

            async def speech_to_text(
                provider_self,
                opus_data,
                session_id,
                artifacts=None,
            ):
                return provider_self.raw_text, None

        self.asr = TestAsrProvider()

    def connection(self, voiceprint_provider):
        return types.SimpleNamespace(
            voiceprint_provider=voiceprint_provider,
            session_id="session_001",
            device_id="device_must_not_become_speaker",
        )

    async def test_recognized_name_keeps_original_chat_json_shape(self):
        recognition = RecognitionResult(
            voiceprint_id="voiceprint_father",
            speaker_name="爸爸",
            confidence=0.91,
            status=IdentityStatus.RECOGNIZED,
        )

        await self.asr.handle_voice_stop(
            self.connection(FakeVoiceprintProvider(recognition)),
            [b"\x00\x00" * 100],
        )

        expected = json.dumps(
            {"speaker": "爸爸", "content": "你好"},
            ensure_ascii=False,
        )
        self.assertEqual([expected], self.chat_inputs)
        self.assertEqual(expected, self.reports[0][0])

    async def test_failure_status_never_becomes_speaker_name(self):
        failures = (
            RecognitionResult(
                voiceprint_id=None,
                speaker_name="未知说话人",
                confidence=0.91,
                status=IdentityStatus.UNKNOWN_VOICEPRINT,
                error_code="speaker_id_missing",
            ),
            RecognitionResult(
                voiceprint_id="voiceprint_unbound",
                speaker_name="voiceprint_unbound",
                confidence=0.91,
                status=IdentityStatus.PERSON_NOT_BOUND,
                error_code="speaker_not_bound",
            ),
            RecognitionResult(
                voiceprint_id="voiceprint_low",
                speaker_name="device_must_not_become_speaker",
                confidence=0.2,
                status=IdentityStatus.LOW_CONFIDENCE,
                error_code="confidence_below_threshold",
            ),
            RecognitionResult(
                voiceprint_id=None,
                speaker_name="voiceprint_timeout",
                confidence=None,
                status=IdentityStatus.SERVICE_UNAVAILABLE,
                error_code="voiceprint_timeout",
            ),
            RecognitionResult(
                voiceprint_id=None,
                speaker_name="invalid_response_json",
                confidence=None,
                status=IdentityStatus.INVALID_RESULT,
                error_code="invalid_response_json",
            ),
        )

        for recognition in failures:
            with self.subTest(status=recognition.status):
                self.chat_inputs.clear()
                self.reports.clear()

                await self.asr.handle_voice_stop(
                    self.connection(FakeVoiceprintProvider(recognition)),
                    [b"\x00\x00" * 100],
                )

                self.assertEqual(["你好"], self.chat_inputs)
                self.assertEqual("你好", self.reports[0][0])

    async def test_voiceprint_disabled_keeps_plain_text_chat(self):
        await self.asr.handle_voice_stop(
            self.connection(None),
            [b"\x00\x00" * 100],
        )

        self.assertEqual(["你好"], self.chat_inputs)
        self.assertEqual("你好", self.reports[0][0])

    async def test_dict_asr_result_keeps_original_structure(self):
        self.asr.raw_text = {
            "content": "你好",
            "language": "zh",
            "emotion": "neutral",
        }
        recognition = RecognitionResult(
            voiceprint_id="voiceprint_father",
            speaker_name="爸爸",
            confidence=0.91,
            status=IdentityStatus.RECOGNIZED,
        )

        await self.asr.handle_voice_stop(
            self.connection(FakeVoiceprintProvider(recognition)),
            [b"\x00\x00" * 100],
        )

        self.assertEqual(
            {
                "content": "你好",
                "language": "zh",
                "emotion": "neutral",
                "speaker": "爸爸",
            },
            json.loads(self.chat_inputs[0]),
        )
