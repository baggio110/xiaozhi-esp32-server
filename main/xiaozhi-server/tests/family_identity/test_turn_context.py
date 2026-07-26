"""不可变单轮身份上下文测试。"""

import unittest
from dataclasses import FrozenInstanceError

from core.family_identity import (
    IdentityDecision,
    PersonIdentity,
    TurnIdentityContext,
)


class TurnIdentityContextTest(unittest.TestCase):
    def test_context_is_not_affected_by_external_variable_changes(self):
        person = PersonIdentity("family_001", "person_father", "爸爸")
        decision = IdentityDecision.recognized(
            person,
            "voiceprint_father",
        )
        current_speaker = "爸爸"
        latest_decision = decision
        context = TurnIdentityContext(
            session_id="session_001",
            turn_id="turn_001",
            device_id="living-room-device",
            identity_decision=latest_decision,
        )

        current_speaker = "妈妈"
        latest_decision = IdentityDecision.recognized(
            PersonIdentity("family_001", "person_mother", "妈妈"),
            "voiceprint_mother",
        )

        self.assertEqual("妈妈", current_speaker)
        self.assertEqual(
            "person_father",
            context.identity_decision.person_id,
        )
        self.assertNotEqual(
            latest_decision.person_id,
            context.identity_decision.person_id,
        )

    def test_context_and_nested_decision_are_frozen(self):
        person = PersonIdentity("family_001", "person_father", "爸爸")
        context = TurnIdentityContext(
            session_id="session_001",
            turn_id="turn_001",
            device_id="living-room-device",
            identity_decision=IdentityDecision.recognized(
                person,
                "voiceprint_father",
            ),
        )

        with self.assertRaises(FrozenInstanceError):
            context.device_id = "bedroom-device"
        with self.assertRaises(FrozenInstanceError):
            context.identity_decision.person_id = "person_mother"
