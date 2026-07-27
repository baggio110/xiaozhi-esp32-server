"""身份解析和 fail-closed 策略测试。"""

import unittest

from core.family_identity import (
    IdentityPolicy,
    IdentityStatus,
    PersonIdentity,
    RecognitionResult,
)

from .fakes import FakeIdentityRepository, FakeVoiceprintProvider


class IdentityPolicyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.father = PersonIdentity(
            family_id="family_001",
            person_id="person_father",
            display_name="爸爸",
        )
        self.repository = FakeIdentityRepository(
            {"voiceprint_father": self.father}
        )
        self.policy = IdentityPolicy(self.repository)

    async def test_fake_voiceprint_provider_supports_all_required_modes(self):
        providers = (
            FakeVoiceprintProvider.recognized(
                "voiceprint_father", "爸爸", 0.95
            ),
            FakeVoiceprintProvider.unknown(),
            FakeVoiceprintProvider.low_confidence(
                "voiceprint_father", 0.2
            ),
            FakeVoiceprintProvider.timeout(),
            FakeVoiceprintProvider.service_error(),
        )

        results = [
            await provider.identify_speaker(b"test-audio", "session_001")
            for provider in providers
        ]

        self.assertEqual(IdentityStatus.RECOGNIZED, results[0].status)
        self.assertEqual(IdentityStatus.UNKNOWN_VOICEPRINT, results[1].status)
        self.assertEqual(IdentityStatus.LOW_CONFIDENCE, results[2].status)
        self.assertEqual(IdentityStatus.SERVICE_UNAVAILABLE, results[3].status)
        self.assertEqual(IdentityStatus.SERVICE_UNAVAILABLE, results[4].status)

    def test_recognized_status_allows_memory_read_and_write(self):
        result = RecognitionResult(
            voiceprint_id="voiceprint_father",
            speaker_name="爸爸",
            confidence=0.95,
            status=IdentityStatus.RECOGNIZED,
        )

        decision = self.policy.decide("family_001", result)

        self.assertEqual(IdentityStatus.RECOGNIZED, decision.identity_status)
        self.assertTrue(decision.allow_memory_read)
        self.assertTrue(decision.allow_memory_write)
        self.assertEqual(
            "family_001:person_father",
            decision.memory_user_id,
        )

    def test_unknown_voiceprint_is_denied(self):
        result = RecognitionResult(
            voiceprint_id=None,
            speaker_name=None,
            confidence=0.0,
            status=IdentityStatus.UNKNOWN_VOICEPRINT,
        )

        self.assert_denied(
            self.policy.decide("family_001", result),
            IdentityStatus.UNKNOWN_VOICEPRINT,
        )

    def test_low_confidence_is_denied(self):
        result = RecognitionResult(
            voiceprint_id="voiceprint_father",
            speaker_name=None,
            confidence=0.35,
            status=IdentityStatus.LOW_CONFIDENCE,
        )

        self.assert_denied(
            self.policy.decide("family_001", result),
            IdentityStatus.LOW_CONFIDENCE,
        )
        self.assertEqual([], self.repository.find_calls)

    def test_recognized_status_is_not_reclassified_by_confidence(self):
        result = RecognitionResult(
            voiceprint_id="voiceprint_father",
            speaker_name="爸爸",
            confidence=0.35,
            status=IdentityStatus.RECOGNIZED,
        )

        decision = self.policy.decide("family_001", result)

        self.assertEqual(
            IdentityStatus.RECOGNIZED,
            decision.identity_status,
        )
        self.assertEqual(
            "family_001:person_father",
            decision.memory_user_id,
        )

    def test_recognized_status_with_invalid_confidence_is_denied(self):
        result = RecognitionResult(
            voiceprint_id="voiceprint_father",
            speaker_name="爸爸",
            confidence=None,
            status=IdentityStatus.RECOGNIZED,
        )

        self.assert_denied(
            self.policy.decide("family_001", result),
            IdentityStatus.INVALID_RESULT,
        )

    def test_service_unavailable_is_denied(self):
        result = RecognitionResult(
            voiceprint_id=None,
            speaker_name=None,
            confidence=None,
            status=IdentityStatus.SERVICE_UNAVAILABLE,
            error_code="voiceprint_timeout",
        )

        self.assert_denied(
            self.policy.decide("family_001", result),
            IdentityStatus.SERVICE_UNAVAILABLE,
        )

    def test_person_not_bound_is_denied(self):
        result = RecognitionResult(
            voiceprint_id="voiceprint_unbound",
            speaker_name="访客",
            confidence=0.95,
            status=IdentityStatus.RECOGNIZED,
        )

        self.assert_denied(
            self.policy.decide("family_001", result),
            IdentityStatus.PERSON_NOT_BOUND,
        )

    def test_person_disabled_is_denied(self):
        disabled_person = PersonIdentity(
            family_id="family_001",
            person_id="person_disabled",
            display_name="停用成员",
            enabled=False,
        )
        self.repository.bind("voiceprint_disabled", disabled_person)
        result = RecognitionResult(
            voiceprint_id="voiceprint_disabled",
            speaker_name="停用成员",
            confidence=0.95,
            status=IdentityStatus.RECOGNIZED,
        )

        self.assert_denied(
            self.policy.decide("family_001", result),
            IdentityStatus.PERSON_DISABLED,
        )

    def test_invalid_result_is_denied(self):
        self.assert_denied(
            self.policy.decide("family_001", None),
            IdentityStatus.INVALID_RESULT,
        )

    def test_invalid_status_type_is_denied(self):
        result = RecognitionResult(
            voiceprint_id="voiceprint_father",
            speaker_name="爸爸",
            confidence=0.95,
            status="recognized",
        )

        self.assert_denied(
            self.policy.decide("family_001", result),
            IdentityStatus.INVALID_RESULT,
        )

    def assert_denied(self, decision, expected_status):
        self.assertEqual(expected_status, decision.identity_status)
        self.assertFalse(decision.allow_memory_read)
        self.assertFalse(decision.allow_memory_write)
        self.assertIsNone(decision.memory_user_id)
