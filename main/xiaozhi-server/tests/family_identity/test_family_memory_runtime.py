"""家庭记忆 Runtime 生命周期测试。"""

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from core.family_identity import (
    SCHEMA_VERSION,
    FamilyMemoryRuntime,
    FamilyMemorySettings,
    IdentityStatus,
    PersonIdentity,
    RecognitionResult,
    SQLiteIdentityRepository,
)

from .project_temp import ProjectTemporaryDirectory


class FamilyMemoryRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = ProjectTemporaryDirectory()
        self.server_root = (
            Path(self.temporary_directory.name) / "xiaozhi-server"
        )
        self.server_root.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_disabled_runtime_has_no_database_side_effects(self):
        data_directory = self.server_root / "data"
        database_path = data_directory / "family_identity.db"
        runtime = FamilyMemoryRuntime(
            FamilyMemorySettings(),
            self.server_root,
        )

        repository = runtime.start()

        self.assertIsNone(repository)
        self.assertIsNone(runtime.repository)
        self.assertIsNone(runtime.database_path)
        self.assertIsNone(runtime.family_id)
        self.assertFalse(runtime.enabled)
        self.assertTrue(runtime.is_started)
        self.assertFalse(runtime.is_active)
        self.assertFalse(data_directory.exists())
        self.assertFalse(database_path.exists())

    def test_disabled_runtime_start_is_idempotent(self):
        runtime = FamilyMemoryRuntime(
            FamilyMemorySettings(),
            self.server_root,
        )

        first = runtime.start()
        second = runtime.start()

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertFalse((self.server_root / "data").exists())

    def test_disabled_runtime_does_not_create_missing_server_root(self):
        missing_server_root = (
            Path(self.temporary_directory.name) / "missing-server-root"
        )
        runtime = FamilyMemoryRuntime(
            FamilyMemorySettings(),
            missing_server_root,
        )

        self.assertIsNone(runtime.start())
        self.assertFalse(missing_server_root.exists())

    def test_enabled_runtime_creates_repository_and_schema(self):
        runtime = self.enabled_runtime()

        repository = runtime.start()

        self.assertIsInstance(repository, SQLiteIdentityRepository)
        self.assertIs(repository, runtime.repository)
        self.assertTrue(runtime.enabled)
        self.assertTrue(runtime.is_started)
        self.assertTrue(runtime.is_active)
        self.assertFalse(hasattr(runtime, "_min_confidence"))
        self.assertEqual(
            self.server_root / "data" / "family_identity.db",
            runtime.database_path,
        )
        self.assertTrue(runtime.database_path.exists())
        self.assertEqual(
            SCHEMA_VERSION,
            self.read_user_version(runtime.database_path),
        )

    def test_enabled_runtime_exposes_configured_family_id_only(self):
        runtime = self.enabled_runtime()

        runtime.start()

        self.assertEqual("family_001", runtime.family_id)
        self.assertFalse(hasattr(runtime, "person_id"))
        self.assertFalse(hasattr(runtime, "memory_user_id"))
        self.assertIsNone(
            runtime.repository.get_person(
                "family_001",
                "person_missing",
            )
        )

    def test_enabled_runtime_start_returns_same_repository(self):
        runtime = self.enabled_runtime()

        first = runtime.start()
        second = runtime.start()

        self.assertIs(first, second)
        self.assertIs(first, runtime.repository)

    def test_close_keeps_database_and_clears_runtime_references(self):
        runtime = self.enabled_runtime()
        runtime.start()
        database_path = runtime.database_path

        runtime.close()

        self.assertTrue(database_path.exists())
        self.assertFalse(runtime.is_started)
        self.assertFalse(runtime.is_active)
        self.assertIsNone(runtime.repository)
        self.assertIsNone(runtime.database_path)
        self.assertIsNone(runtime.family_id)

    def test_restart_preserves_repository_data(self):
        runtime = self.enabled_runtime()
        repository = runtime.start()
        father = PersonIdentity(
            "family_001",
            "person_father",
            "爸爸",
        )
        repository.save_person(father)
        repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_father",
        )

        runtime.close()
        restarted_repository = runtime.start()

        self.assertIsNot(repository, restarted_repository)
        self.assertEqual(
            father,
            restarted_repository.find_by_voiceprint_id(
                "family_001",
                "voiceprint_father",
            ),
        )

    def test_custom_internal_database_path_is_supported(self):
        runtime = FamilyMemoryRuntime(
            FamilyMemorySettings(
                enabled=True,
                family_id="family_001",
                database_path="state/identity/family.db",
            ),
            self.server_root,
        )

        runtime.start()

        self.assertEqual(
            self.server_root / "state" / "identity" / "family.db",
            runtime.database_path,
        )
        self.assertTrue(runtime.database_path.exists())

    def test_enabled_runtime_resolves_with_one_identity_service_call(self):
        runtime = self.enabled_runtime()
        repository = runtime.start()
        father = PersonIdentity(
            "family_001",
            "person_father",
            "爸爸",
        )
        repository.save_person(father)
        repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_father",
        )
        original_service = runtime._identity_service
        self.assertIs(
            runtime.repository,
            original_service._policy._repository,
        )

        class CountingIdentityService:
            def __init__(self):
                self.calls = 0

            def resolve(self, family_id, recognition):
                self.calls += 1
                return original_service.resolve(family_id, recognition)

        counting_service = CountingIdentityService()
        runtime._identity_service = counting_service

        decision = runtime.resolve_identity(
            RecognitionResult(
                voiceprint_id="voiceprint_father",
                speaker_name="爸爸",
                confidence=0.91,
                status=IdentityStatus.RECOGNIZED,
            )
        )

        self.assertEqual(1, counting_service.calls)
        self.assertEqual(IdentityStatus.RECOGNIZED, decision.identity_status)
        self.assertEqual(
            "family_001:person_father",
            decision.memory_user_id,
        )

    def test_identity_service_exception_is_fail_closed(self):
        runtime = self.enabled_runtime()
        runtime.start()

        class FailingIdentityService:
            def resolve(self, family_id, recognition):
                raise RuntimeError("test-only")

        runtime._identity_service = FailingIdentityService()

        decision = runtime.resolve_identity(
            RecognitionResult(
                voiceprint_id="voiceprint_father",
                speaker_name="爸爸",
                confidence=0.91,
                status=IdentityStatus.RECOGNIZED,
            )
        )

        self.assertEqual(
            IdentityStatus.INVALID_RESULT,
            decision.identity_status,
        )
        self.assertFalse(decision.allow_memory_read)
        self.assertFalse(decision.allow_memory_write)
        self.assertIsNone(decision.memory_user_id)
        self.assertEqual(
            "identity_resolution_error",
            decision.failure_reason,
        )

    def test_disabled_runtime_does_not_resolve_identity(self):
        runtime = FamilyMemoryRuntime(
            FamilyMemorySettings(),
            self.server_root,
        )
        runtime.start()

        with self.assertRaises(RuntimeError):
            runtime.resolve_identity(None)

        self.assertIsNone(runtime._identity_service)
        self.assertIsNone(runtime.repository)
        self.assertFalse((self.server_root / "data").exists())

    def enabled_runtime(self) -> FamilyMemoryRuntime:
        return FamilyMemoryRuntime(
            FamilyMemorySettings(
                enabled=True,
                family_id="family_001",
            ),
            self.server_root,
        )

    @staticmethod
    def read_user_version(database_path: Path) -> int:
        with closing(sqlite3.connect(database_path)) as connection:
            return connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
