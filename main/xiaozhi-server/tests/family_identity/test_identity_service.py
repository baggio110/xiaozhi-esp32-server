"""IdentityService 与 SQLite Repository 集成测试。"""

import unittest
from pathlib import Path

from core.family_identity import (
    IdentityService,
    IdentityStatus,
    PersonIdentity,
    RecognitionResult,
    SQLiteIdentityRepository,
)

from .fakes import FakeIdentityRepository
from .project_temp import ProjectTemporaryDirectory


class IdentityServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = ProjectTemporaryDirectory()
        database_path = (
            Path(self.temporary_directory.name) / "family_identity.db"
        )
        self.repository = SQLiteIdentityRepository(database_path)
        self.father = PersonIdentity(
            "family_001",
            "person_father",
            "爸爸",
        )
        self.repository.save_person(self.father)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_father",
        )
        self.service = IdentityService(self.repository)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_valid_enabled_person_is_recognized(self):
        decision = self.service.resolve(
            "family_001",
            self.recognized_result(),
        )

        self.assertEqual(
            IdentityStatus.RECOGNIZED,
            decision.identity_status,
        )
        self.assertEqual(
            "family_001:person_father",
            decision.memory_user_id,
        )
        self.assertTrue(decision.allow_memory_read)
        self.assertTrue(decision.allow_memory_write)

    def test_disabled_person_returns_person_disabled(self):
        self.repository.set_person_enabled(
            "family_001",
            "person_father",
            False,
        )

        decision = self.service.resolve(
            "family_001",
            self.recognized_result(),
        )

        self.assert_denied(decision, IdentityStatus.PERSON_DISABLED)

    def test_same_voiceprint_does_not_resolve_across_family(self):
        decision = self.service.resolve(
            "family_002",
            self.recognized_result(),
        )

        self.assert_denied(decision, IdentityStatus.PERSON_NOT_BOUND)

    def test_unbound_voiceprint_returns_person_not_bound(self):
        result = RecognitionResult(
            voiceprint_id="voiceprint_unbound",
            speaker_name="访客",
            confidence=0.95,
            status=IdentityStatus.RECOGNIZED,
        )

        decision = self.service.resolve("family_001", result)

        self.assert_denied(decision, IdentityStatus.PERSON_NOT_BOUND)

    def test_all_recognition_failure_states_remain_denied(self):
        failure_results = (
            (
                RecognitionResult(
                    voiceprint_id=None,
                    speaker_name=None,
                    confidence=0.0,
                    status=IdentityStatus.UNKNOWN_VOICEPRINT,
                ),
                IdentityStatus.UNKNOWN_VOICEPRINT,
            ),
            (
                RecognitionResult(
                    voiceprint_id="voiceprint_father",
                    speaker_name=None,
                    confidence=0.35,
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
                    error_code="voiceprint_timeout",
                ),
                IdentityStatus.SERVICE_UNAVAILABLE,
            ),
            (None, IdentityStatus.INVALID_RESULT),
        )

        for recognition, expected_status in failure_results:
            with self.subTest(expected_status=expected_status):
                decision = self.service.resolve(
                    "family_001",
                    recognition,
                )
                self.assert_denied(decision, expected_status)

    def test_service_unavailable_does_not_query_repository(self):
        repository = FakeIdentityRepository(
            {"voiceprint_father": self.father}
        )
        service = IdentityService(repository)
        result = RecognitionResult(
            voiceprint_id=None,
            speaker_name=None,
            confidence=None,
            status=IdentityStatus.SERVICE_UNAVAILABLE,
            error_code="voiceprint_service_error",
        )

        decision = service.resolve("family_001", result)

        self.assert_denied(
            decision,
            IdentityStatus.SERVICE_UNAVAILABLE,
        )
        self.assertEqual([], repository.find_calls)

    def test_provider_recognized_status_with_score_point_35_is_preserved(self):
        result = RecognitionResult(
            voiceprint_id="voiceprint_father",
            speaker_name="爸爸",
            confidence=0.35,
            status=IdentityStatus.RECOGNIZED,
        )

        decision = self.service.resolve("family_001", result)

        self.assertEqual(
            IdentityStatus.RECOGNIZED,
            decision.identity_status,
        )
        self.assertEqual(
            "family_001:person_father",
            decision.memory_user_id,
        )

    @staticmethod
    def recognized_result() -> RecognitionResult:
        return RecognitionResult(
            voiceprint_id="voiceprint_father",
            speaker_name="爸爸",
            confidence=0.95,
            status=IdentityStatus.RECOGNIZED,
        )

    def assert_denied(self, decision, expected_status):
        self.assertEqual(expected_status, decision.identity_status)
        self.assertFalse(decision.allow_memory_read)
        self.assertFalse(decision.allow_memory_write)
        self.assertIsNone(decision.memory_user_id)
