"""家庭记忆 Runtime 生命周期测试。"""

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from core.family_identity import (
    SCHEMA_VERSION,
    FamilyMemoryRuntime,
    FamilyMemorySettings,
    PersonIdentity,
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
