"""B4a1 连接级短期 Dialogue 存储测试。"""

import ast
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from core.family_identity import (
    FamilySessionDialogueStore,
    IdentityDecision,
    IdentityStatus,
    PersonIdentity,
    TurnIdentityContext,
)


SERVER_ROOT = Path(__file__).resolve().parents[2]
STORE_PATH = (
    SERVER_ROOT
    / "core"
    / "family_identity"
    / "session_dialogues.py"
)


class FakeDialogue:
    """只用于验证对象身份和消息隔离。"""

    def __init__(self, sequence):
        self.sequence = sequence
        self.messages = []


class CountingDialogueFactory:
    """记录 Store 实际创建了多少个 Dialogue。"""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return FakeDialogue(self.calls)


class FamilySessionDialogueStoreTest(unittest.TestCase):
    def setUp(self):
        self.factory = CountingDialogueFactory()
        self.store = FamilySessionDialogueStore(self.factory)

    def test_none_context_returns_one_anonymous_dialogue(self):
        first = self.store.get_dialogue(None)
        second = self.store.get_dialogue(None)

        self.assertIs(first, second)
        self.assertEqual(1, self.factory.calls)
        self.assertNotIn(first, self.store._personal_dialogues.values())

    def test_all_failure_statuses_share_anonymous_dialogue(self):
        statuses = (
            IdentityStatus.UNKNOWN_VOICEPRINT,
            IdentityStatus.LOW_CONFIDENCE,
            IdentityStatus.SERVICE_UNAVAILABLE,
            IdentityStatus.PERSON_NOT_BOUND,
            IdentityStatus.PERSON_DISABLED,
            IdentityStatus.INVALID_RESULT,
        )

        dialogues = [
            self.store.get_dialogue(self.denied_context(status))
            for status in statuses
        ]

        self.assertTrue(
            all(dialogue is dialogues[0] for dialogue in dialogues)
        )
        self.assertEqual(1, self.factory.calls)
        self.assertEqual({}, self.store._personal_dialogues)

    def test_recognized_person_reuses_personal_dialogue(self):
        context = self.recognized_context(
            "person_father",
            "爸爸",
        )

        first = self.store.get_dialogue(context)
        second = self.store.get_dialogue(context)

        self.assertIs(first, second)
        self.assertEqual(1, self.factory.calls)

    def test_father_and_mother_use_different_dialogues(self):
        father = self.store.get_dialogue(
            self.recognized_context("person_father", "爸爸")
        )
        mother = self.store.get_dialogue(
            self.recognized_context("person_mother", "妈妈")
        )

        self.assertIsNot(father, mother)
        self.assertEqual(2, self.factory.calls)

    def test_same_display_name_with_different_ids_is_isolated(self):
        first = self.store.get_dialogue(
            self.recognized_context("person_first", "家人")
        )
        second = self.store.get_dialogue(
            self.recognized_context("person_second", "家人")
        )

        self.assertIsNot(first, second)

    def test_anonymous_dialogue_is_distinct_from_personal_dialogues(self):
        anonymous = self.store.get_dialogue(None)
        father = self.store.get_dialogue(
            self.recognized_context("person_father", "爸爸")
        )
        mother = self.store.get_dialogue(
            self.recognized_context("person_mother", "妈妈")
        )

        self.assertIsNot(anonymous, father)
        self.assertIsNot(anonymous, mother)
        self.assertIsNot(father, mother)
        self.assertNotIn(
            anonymous,
            self.store._personal_dialogues.values(),
        )

    def test_failed_turn_uses_anonymous_then_returns_to_personal(self):
        father_context = self.recognized_context(
            "person_father",
            "爸爸",
        )
        father = self.store.get_dialogue(father_context)
        father.messages.append("爸爸的历史")

        anonymous = self.store.get_dialogue(
            self.denied_context(IdentityStatus.LOW_CONFIDENCE)
        )
        anonymous.messages.append("匿名历史")
        father_again = self.store.get_dialogue(father_context)

        self.assertIs(father, father_again)
        self.assertIsNot(father, anonymous)
        self.assertEqual(["爸爸的历史"], father.messages)
        self.assertEqual(["匿名历史"], anonymous.messages)

    def test_invalid_recognized_memory_id_fails_closed_to_anonymous(self):
        anonymous = self.store.get_dialogue(None)
        invalid_values = (
            None,
            "",
            " ",
            "person_father",
            "family_001:person_other",
            123,
        )

        for invalid_value in invalid_values:
            with self.subTest(memory_user_id=invalid_value):
                context = types.SimpleNamespace(
                    session_id="session_001",
                    device_id="device_001",
                    current_speaker="爸爸",
                    identity_decision=types.SimpleNamespace(
                        identity_status=IdentityStatus.RECOGNIZED,
                        family_id="family_001",
                        person_id="person_father",
                        memory_user_id=invalid_value,
                        voiceprint_id="voiceprint_father",
                        display_name="爸爸",
                    ),
                )
                self.assertIs(
                    anonymous,
                    self.store.get_dialogue(context),
                )

        self.assertEqual({}, self.store._personal_dialogues)
        self.assertEqual(1, self.factory.calls)

    def test_two_store_instances_never_share_dialogues(self):
        context = self.recognized_context(
            "person_father",
            "爸爸",
        )
        other_store = FamilySessionDialogueStore(self.factory)

        first = self.store.get_dialogue(context)
        second = other_store.get_dialogue(context)

        self.assertIsNot(first, second)

    def test_clear_releases_personal_and_anonymous_dialogues(self):
        father_context = self.recognized_context(
            "person_father",
            "爸爸",
        )
        old_father = self.store.get_dialogue(father_context)
        old_anonymous = self.store.get_dialogue(None)

        self.store.clear()

        new_father = self.store.get_dialogue(father_context)
        new_anonymous = self.store.get_dialogue(None)
        self.assertIsNot(old_father, new_father)
        self.assertIsNot(old_anonymous, new_anonymous)
        self.assertEqual(4, self.factory.calls)

    def test_concurrent_first_personal_access_creates_once(self):
        context = self.recognized_context(
            "person_father",
            "爸爸",
        )
        dialogues = self.concurrent_get(context)

        self.assertTrue(
            all(dialogue is dialogues[0] for dialogue in dialogues)
        )
        self.assertEqual(1, self.factory.calls)

    def test_concurrent_first_anonymous_access_creates_once(self):
        dialogues = self.concurrent_get(None)

        self.assertTrue(
            all(dialogue is dialogues[0] for dialogue in dialogues)
        )
        self.assertEqual(1, self.factory.calls)

    def test_store_has_no_business_or_persistence_dependencies(self):
        tree = ast.parse(STORE_PATH.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )

        self.assertEqual(
            {"threading", "typing", "models"},
            imported_modules,
        )
        source = STORE_PATH.read_text(encoding="utf-8")
        for forbidden_name in (
            "ConnectionHandler",
            "IdentityService",
            "SQLite",
            "PowerMem",
            "FamilyMemoryRuntime",
            "open(",
            "Path(",
        ):
            with self.subTest(forbidden_name=forbidden_name):
                self.assertNotIn(forbidden_name, source)

    def concurrent_get(self, context):
        worker_count = 16
        barrier = Barrier(worker_count)

        def get_dialogue():
            barrier.wait()
            return self.store.get_dialogue(context)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(get_dialogue)
                for _ in range(worker_count)
            ]
            return [future.result() for future in futures]

    @staticmethod
    def recognized_context(person_id, display_name):
        person = PersonIdentity(
            "family_001",
            person_id,
            display_name,
        )
        return TurnIdentityContext(
            session_id="session_001",
            turn_id=f"turn_{person_id}",
            device_id="device_001",
            identity_decision=IdentityDecision.recognized(
                person,
                f"voiceprint_{person_id}",
            ),
        )

    @staticmethod
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
