"""正式声纹 Provider 的结构化识别结果测试。"""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.family_identity import IdentityStatus


VOICEPRINT_PROVIDER_PATH = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "utils"
    / "voiceprint_provider.py"
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


class FakeFormData:
    """只记录字段、不编码网络请求的表单替身。"""

    def __init__(self) -> None:
        self.fields = []

    def add_field(self, name, value, **kwargs) -> None:
        self.fields.append((name, value, kwargs))


class FakeContentTypeError(ValueError):
    """模拟 aiohttp 非 JSON 响应异常。"""


class FakeResponse:
    """支持 async with 的 HTTP 响应替身。"""

    def __init__(
        self,
        *,
        status=200,
        payload=None,
        json_error=None,
        enter_error=None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.json_error = json_error
        self.enter_error = enter_error

    async def __aenter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeSession:
    """返回预设响应的 aiohttp ClientSession 替身。"""

    def __init__(self, response) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def post(self, *args, **kwargs):
        return self.response


def load_isolated_voiceprint_provider():
    """隔离加载 Provider，测试期间不依赖或访问真实网络组件。"""

    fake_aiohttp = types.SimpleNamespace(
        ClientSession=None,
        ClientTimeout=lambda **kwargs: kwargs,
        ContentTypeError=FakeContentTypeError,
        FormData=FakeFormData,
    )
    fake_requests = types.SimpleNamespace(
        exceptions=types.SimpleNamespace(
            ConnectTimeout=TimeoutError,
            ConnectionError=ConnectionError,
        ),
        get=lambda *args, **kwargs: None,
    )
    stubs = {
        "aiohttp": fake_aiohttp,
        "requests": fake_requests,
        "config.logger": types.SimpleNamespace(
            setup_logging=lambda: FakeLogger()
        ),
        "core.utils.cache.manager": types.SimpleNamespace(
            cache_manager=types.SimpleNamespace()
        ),
        "core.utils.cache.config": types.SimpleNamespace(
            CacheType=types.SimpleNamespace(VOICEPRINT_HEALTH="voiceprint")
        ),
    }
    module_name = "voiceprint_provider_under_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        VOICEPRINT_PROVIDER_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class VoiceprintRecognitionResultTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.module = load_isolated_voiceprint_provider()
        self.provider = self.module.VoiceprintProvider.__new__(
            self.module.VoiceprintProvider
        )
        self.provider.enabled = True
        self.provider.api_url = "https://voiceprint.invalid/identify"
        self.provider.api_key = "test-only"
        self.provider.speaker_ids = ["speaker_a", "speaker_b"]
        self.provider.speaker_map = {
            "speaker_a": {"name": "爸爸", "description": ""},
        }
        self.provider.similarity_threshold = 0.4

    async def identify(self, response):
        self.module.aiohttp.ClientSession = lambda **kwargs: FakeSession(
            response
        )
        return await self.provider.identify_speaker(
            b"test-wav",
            "session_001",
        )

    async def test_success_preserves_id_name_and_score(self):
        result = await self.identify(
            FakeResponse(
                payload={"speaker_id": "speaker_a", "score": 0.91}
            )
        )

        self.assertEqual(IdentityStatus.RECOGNIZED, result.status)
        self.assertEqual("speaker_a", result.voiceprint_id)
        self.assertEqual("爸爸", result.speaker_name)
        self.assertEqual(0.91, result.confidence)
        self.assertIsNone(result.error_code)

    async def test_score_below_threshold_is_low_confidence(self):
        result = await self.identify(
            FakeResponse(
                payload={"speaker_id": "speaker_a", "score": 0.39}
            )
        )

        self.assertEqual(IdentityStatus.LOW_CONFIDENCE, result.status)
        self.assertEqual("speaker_a", result.voiceprint_id)
        self.assertEqual(0.39, result.confidence)
        self.assertIsNone(result.speaker_name)

    async def test_empty_or_missing_speaker_id_is_unknown(self):
        for payload in (
            {"speaker_id": "", "score": 0.9},
            {"score": 0.9},
        ):
            with self.subTest(payload=payload):
                result = await self.identify(FakeResponse(payload=payload))

                self.assertEqual(
                    IdentityStatus.UNKNOWN_VOICEPRINT,
                    result.status,
                )
                self.assertIsNone(result.voiceprint_id)
                self.assertIsNone(result.speaker_name)
                self.assertEqual(0.9, result.confidence)

    async def test_unmapped_speaker_id_is_person_not_bound(self):
        result = await self.identify(
            FakeResponse(
                payload={"speaker_id": "speaker_b", "score": 0.9}
            )
        )

        self.assertEqual(IdentityStatus.PERSON_NOT_BOUND, result.status)
        self.assertEqual("speaker_b", result.voiceprint_id)
        self.assertEqual(0.9, result.confidence)
        self.assertIsNone(result.speaker_name)

    async def test_timeout_is_service_unavailable(self):
        result = await self.identify(
            FakeResponse(enter_error=asyncio.TimeoutError())
        )

        self.assertEqual(IdentityStatus.SERVICE_UNAVAILABLE, result.status)
        self.assertIsNone(result.voiceprint_id)
        self.assertIsNone(result.speaker_name)

    async def test_http_error_is_service_unavailable(self):
        result = await self.identify(FakeResponse(status=503))

        self.assertEqual(IdentityStatus.SERVICE_UNAVAILABLE, result.status)
        self.assertIsNone(result.voiceprint_id)
        self.assertIsNone(result.speaker_name)

    async def test_transport_error_is_service_unavailable(self):
        result = await self.identify(
            FakeResponse(enter_error=ConnectionError("test-only"))
        )

        self.assertEqual(IdentityStatus.SERVICE_UNAVAILABLE, result.status)
        self.assertIsNone(result.voiceprint_id)
        self.assertIsNone(result.speaker_name)

    async def test_non_json_response_is_invalid(self):
        result = await self.identify(
            FakeResponse(json_error=FakeContentTypeError())
        )

        self.assertEqual(IdentityStatus.INVALID_RESULT, result.status)
        self.assertIsNone(result.voiceprint_id)
        self.assertIsNone(result.speaker_name)

    async def test_non_object_json_response_is_invalid(self):
        result = await self.identify(FakeResponse(payload=[]))

        self.assertEqual(IdentityStatus.INVALID_RESULT, result.status)
        self.assertIsNone(result.voiceprint_id)
        self.assertIsNone(result.speaker_name)

    async def test_missing_or_invalid_score_is_invalid(self):
        for payload in (
            {"speaker_id": "speaker_a"},
            {"speaker_id": "speaker_a", "score": "0.9"},
            {"speaker_id": "speaker_a", "score": True},
        ):
            with self.subTest(payload=payload):
                result = await self.identify(FakeResponse(payload=payload))

                self.assertEqual(IdentityStatus.INVALID_RESULT, result.status)
                self.assertEqual("speaker_a", result.voiceprint_id)
                self.assertIsNone(result.speaker_name)
                self.assertIsNone(result.confidence)

    async def test_invalid_speaker_id_type_is_invalid(self):
        result = await self.identify(
            FakeResponse(payload={"speaker_id": 123, "score": 0.9})
        )

        self.assertEqual(IdentityStatus.INVALID_RESULT, result.status)
        self.assertIsNone(result.voiceprint_id)
        self.assertIsNone(result.speaker_name)

    async def test_disabled_provider_never_returns_a_display_name(self):
        self.provider.enabled = False

        result = await self.provider.identify_speaker(
            b"test-wav",
            "session_001",
        )

        self.assertEqual(IdentityStatus.SERVICE_UNAVAILABLE, result.status)
        self.assertIsNone(result.voiceprint_id)
        self.assertIsNone(result.speaker_name)
