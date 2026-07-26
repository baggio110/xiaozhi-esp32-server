"""家庭身份模型与 memory_user_id 规则测试。"""

import unittest

from core.family_identity import (
    IdentityDecision,
    IdentityStatus,
    InvalidIdentityDecisionError,
    InvalidIdentityIdError,
    PersonIdentity,
    TurnIdentityContext,
    build_memory_user_id,
)


class MemoryUserIdModelTest(unittest.TestCase):
    def test_same_person_on_different_devices_has_same_memory_user_id(self):
        person = PersonIdentity(
            family_id="family_001",
            person_id="person_father",
            display_name="爸爸",
        )
        decision = IdentityDecision.recognized(person, "voiceprint_father")
        living_room_context = TurnIdentityContext(
            session_id="session_living_room",
            turn_id="turn_001",
            device_id="living-room-device",
            identity_decision=decision,
        )
        bedroom_context = TurnIdentityContext(
            session_id="session_bedroom",
            turn_id="turn_002",
            device_id="bedroom-device",
            identity_decision=decision,
        )

        self.assertNotEqual(
            living_room_context.device_id,
            bedroom_context.device_id,
        )
        self.assertEqual(
            living_room_context.identity_decision.memory_user_id,
            bedroom_context.identity_decision.memory_user_id,
        )
        self.assertEqual(
            "family_001:person_father",
            living_room_context.identity_decision.memory_user_id,
        )

    def test_father_and_mother_have_different_memory_user_ids(self):
        father = PersonIdentity("family_001", "person_father", "爸爸")
        mother = PersonIdentity("family_001", "person_mother", "妈妈")

        self.assertNotEqual(father.memory_user_id, mother.memory_user_id)

    def test_same_display_name_with_different_person_id_is_isolated(self):
        first = PersonIdentity("family_001", "person_001", "小明")
        second = PersonIdentity("family_001", "person_002", "小明")

        self.assertNotEqual(first.memory_user_id, second.memory_user_id)

    def test_replacing_voiceprint_keeps_memory_user_id(self):
        person = PersonIdentity("family_001", "person_father", "爸爸")

        old_decision = IdentityDecision.recognized(person, "voiceprint_old")
        new_decision = IdentityDecision.recognized(person, "voiceprint_new")

        self.assertNotEqual(
            old_decision.voiceprint_id,
            new_decision.voiceprint_id,
        )
        self.assertEqual(
            old_decision.memory_user_id,
            new_decision.memory_user_id,
        )

    def test_speaker_name_is_not_part_of_memory_user_id(self):
        before_rename = PersonIdentity("family_001", "person_001", "爸爸")
        after_rename = PersonIdentity("family_001", "person_001", "父亲")

        self.assertEqual(
            before_rename.memory_user_id,
            after_rename.memory_user_id,
        )

    def test_device_id_cannot_be_used_as_family_memory_key(self):
        person = PersonIdentity("family_001", "person_001", "爸爸")
        device_id = "living-room-device"

        self.assertEqual("family_001:person_001", person.memory_user_id)
        self.assertNotEqual(device_id, person.memory_user_id)

    def test_family_id_and_person_id_are_required(self):
        for family_id, person_id in (
            ("", "person_001"),
            ("family_001", ""),
            (" ", "person_001"),
            ("family_001", " "),
        ):
            with self.subTest(family_id=family_id, person_id=person_id):
                with self.assertRaises(InvalidIdentityIdError):
                    build_memory_user_id(family_id, person_id)

    def test_recognized_decision_rejects_noncanonical_memory_user_id(self):
        with self.assertRaises(InvalidIdentityDecisionError):
            IdentityDecision(
                family_id="family_001",
                person_id="person_001",
                voiceprint_id="voiceprint_001",
                display_name="爸爸",
                memory_user_id="living-room-device",
                identity_status=IdentityStatus.RECOGNIZED,
                allow_memory_read=True,
                allow_memory_write=True,
            )
