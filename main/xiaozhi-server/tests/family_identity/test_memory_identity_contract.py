"""身份决策到 PowerMem 显式 user_id 的契约测试。"""

import unittest

from core.family_identity import (
    IdentityDecision,
    IdentityStatus,
    MemoryAccessDeniedError,
    MemoryAccessPolicy,
    PersonIdentity,
)

from .fakes import FakePowerMemRecorder


class MemoryIdentityContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_recorder_captures_explicit_memory_user_id(self):
        person = PersonIdentity("family_001", "person_father", "爸爸")
        decision = IdentityDecision.recognized(
            person,
            "voiceprint_father",
        )
        recorder = FakePowerMemRecorder()

        read_user_id = MemoryAccessPolicy.user_id_for_read(decision)
        write_user_id = MemoryAccessPolicy.user_id_for_write(decision)
        await recorder.search("我喜欢什么？", user_id=read_user_id)
        await recorder.profile(user_id=read_user_id)
        await recorder.add(
            [
                {"role": "user", "content": "我喜欢茶"},
                {"role": "assistant", "content": "我记住了"},
            ],
            user_id=write_user_id,
        )

        self.assertEqual(
            ["family_001:person_father"],
            [call.user_id for call in recorder.search_calls],
        )
        self.assertEqual(
            ["family_001:person_father"],
            [call.user_id for call in recorder.profile_calls],
        )
        self.assertEqual(
            ["family_001:person_father"],
            [call.user_id for call in recorder.add_calls],
        )

    async def test_all_failure_statuses_produce_zero_powermem_calls(self):
        denied_statuses = (
            IdentityStatus.UNKNOWN_VOICEPRINT,
            IdentityStatus.LOW_CONFIDENCE,
            IdentityStatus.SERVICE_UNAVAILABLE,
            IdentityStatus.PERSON_NOT_BOUND,
            IdentityStatus.PERSON_DISABLED,
            IdentityStatus.INVALID_RESULT,
        )

        for status in denied_statuses:
            with self.subTest(status=status):
                recorder = FakePowerMemRecorder()
                decision = IdentityDecision.denied(
                    "family_001",
                    status,
                    failure_reason="test_denial",
                )

                with self.assertRaises(MemoryAccessDeniedError):
                    MemoryAccessPolicy.user_id_for_read(decision)
                with self.assertRaises(MemoryAccessDeniedError):
                    MemoryAccessPolicy.user_id_for_write(decision)

                self.assertEqual(0, recorder.total_calls)
