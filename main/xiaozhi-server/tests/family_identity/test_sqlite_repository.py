"""SQLite 家庭身份仓库测试。"""

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from core.family_identity import (
    InvalidIdentityIdError,
    PersonIdentity,
    PersonNotFoundError,
    SCHEMA_VERSION,
    SQLiteIdentityRepository,
    UnsupportedSchemaVersionError,
    VoiceprintAlreadyBoundError,
    VoiceprintNotFoundError,
    VoiceprintRevokedError,
)

from .project_temp import ProjectTemporaryDirectory


class SQLiteIdentityRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = ProjectTemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "family_identity.db"
        )
        self.repository = SQLiteIdentityRepository(self.database_path)
        self.father = PersonIdentity(
            family_id="family_001",
            person_id="person_father",
            display_name="爸爸",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_missing_database_file_is_initialized_safely(self):
        self.assertTrue(self.database_path.exists())
        self.assertIsNone(
            self.repository.get_person("family_001", "person_missing")
        )

        with closing(sqlite3.connect(self.database_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                )
            }

        self.assertEqual(
            {"family_person", "person_voiceprint"},
            tables,
        )
        self.assertEqual(SCHEMA_VERSION, self.read_user_version())

    def test_mapping_persists_after_repository_restart(self):
        self.repository.save_person(self.father)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_father",
        )

        restarted_repository = SQLiteIdentityRepository(self.database_path)
        resolved = restarted_repository.find_by_voiceprint_id(
            "family_001",
            "voiceprint_father",
        )

        self.assertEqual(self.father, resolved)
        self.assertEqual(SCHEMA_VERSION, self.read_user_version())

    def test_same_voiceprint_cannot_bind_two_people(self):
        mother = PersonIdentity(
            "family_001",
            "person_mother",
            "妈妈",
        )
        self.repository.save_person(self.father)
        self.repository.save_person(mother)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_shared",
        )

        with self.assertRaises(VoiceprintAlreadyBoundError):
            self.repository.bind_voiceprint(
                "family_001",
                "person_mother",
                "voiceprint_shared",
            )

    def test_two_voiceprints_can_bind_same_person(self):
        self.repository.save_person(self.father)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_first",
        )
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_second",
        )

        first = self.repository.find_by_voiceprint_id(
            "family_001",
            "voiceprint_first",
        )
        second = self.repository.find_by_voiceprint_id(
            "family_001",
            "voiceprint_second",
        )

        self.assertEqual(first, second)
        self.assertEqual(
            "family_001:person_father",
            first.memory_user_id,
        )

    def test_replace_voiceprint_keeps_memory_user_id(self):
        self.repository.save_person(self.father)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_old",
        )
        original = self.repository.find_by_voiceprint_id(
            "family_001",
            "voiceprint_old",
        )

        self.repository.replace_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_old",
            "voiceprint_new",
        )

        replacement = self.repository.find_by_voiceprint_id(
            "family_001",
            "voiceprint_new",
        )
        self.assertIsNone(
            self.repository.find_by_voiceprint_id(
                "family_001",
                "voiceprint_old",
            )
        )
        self.assertEqual(
            original.memory_user_id,
            replacement.memory_user_id,
        )

    def test_revoked_voiceprint_no_longer_resolves(self):
        self.repository.save_person(self.father)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_old",
        )

        self.repository.revoke_voiceprint(
            "family_001",
            "voiceprint_old",
        )

        self.assertIsNone(
            self.repository.find_by_voiceprint_id(
                "family_001",
                "voiceprint_old",
            )
        )

    def test_revoked_voiceprint_cannot_be_rebound(self):
        self.repository.save_person(self.father)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_old",
        )
        self.repository.revoke_voiceprint(
            "family_001",
            "voiceprint_old",
        )

        with self.assertRaises(VoiceprintRevokedError):
            self.repository.bind_voiceprint(
                "family_001",
                "person_father",
                "voiceprint_old",
            )

    def test_voiceprint_query_is_scoped_by_family_id(self):
        other_father = PersonIdentity(
            "family_002",
            "person_father",
            "爸爸",
        )
        self.repository.save_person(self.father)
        self.repository.save_person(other_father)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_father",
        )

        self.assertIsNone(
            self.repository.find_by_voiceprint_id(
                "family_002",
                "voiceprint_father",
            )
        )
        with self.assertRaises(VoiceprintAlreadyBoundError):
            self.repository.bind_voiceprint(
                "family_002",
                "person_father",
                "voiceprint_father",
            )

    def test_same_display_name_does_not_merge_people(self):
        first = PersonIdentity("family_001", "person_001", "小明")
        second = PersonIdentity("family_001", "person_002", "小明")
        self.repository.save_person(first)
        self.repository.save_person(second)

        self.assertNotEqual(
            self.repository.get_person(
                "family_001",
                "person_001",
            ).memory_user_id,
            self.repository.get_person(
                "family_001",
                "person_002",
            ).memory_user_id,
        )

    def test_parameterized_sql_preserves_special_character_name(self):
        display_name = "小明'); DROP TABLE family_person; --"
        person = PersonIdentity(
            "family_001",
            "person_special",
            display_name,
        )

        self.repository.save_person(person)
        loaded = self.repository.get_person(
            "family_001",
            "person_special",
        )

        self.assertEqual(display_name, loaded.display_name)
        self.assertIsNone(
            self.repository.get_person(
                "family_001",
                "person_missing",
            )
        )

    def test_empty_identity_ids_are_rejected(self):
        with self.assertRaises(InvalidIdentityIdError):
            PersonIdentity("", "person_001", "成员")
        with self.assertRaises(InvalidIdentityIdError):
            PersonIdentity("family_001", "", "成员")

        self.repository.save_person(self.father)
        for family_id, person_id, voiceprint_id in (
            ("", "person_father", "voiceprint_001"),
            ("family_001", "", "voiceprint_001"),
            ("family_001", "person_father", ""),
        ):
            with self.subTest(
                family_id=family_id,
                person_id=person_id,
                voiceprint_id=voiceprint_id,
            ):
                with self.assertRaises(ValueError):
                    self.repository.bind_voiceprint(
                        family_id,
                        person_id,
                        voiceprint_id,
                    )

    def test_existing_database_initialization_is_idempotent(self):
        self.repository.save_person(self.father)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_father",
        )

        SQLiteIdentityRepository(self.database_path)
        SQLiteIdentityRepository(self.database_path)

        with closing(sqlite3.connect(self.database_path)) as connection:
            person_count = connection.execute(
                "SELECT COUNT(*) FROM family_person"
            ).fetchone()[0]
            voiceprint_count = connection.execute(
                "SELECT COUNT(*) FROM person_voiceprint"
            ).fetchone()[0]

        self.assertEqual(1, person_count)
        self.assertEqual(1, voiceprint_count)
        self.assertEqual(SCHEMA_VERSION, self.read_user_version())

    def test_future_schema_version_is_rejected_without_downgrade(self):
        future_database_path = (
            Path(self.temporary_directory.name) / "future.db"
        )
        future_version = 2
        with closing(sqlite3.connect(future_database_path)) as connection:
            connection.execute("PRAGMA user_version = 2")
            connection.commit()

        with self.assertRaises(UnsupportedSchemaVersionError):
            SQLiteIdentityRepository(future_database_path)

        with closing(sqlite3.connect(future_database_path)) as connection:
            persisted_version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            tables = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchall()

        self.assertEqual(future_version, persisted_version)
        self.assertEqual([], tables)

    def test_failed_initialization_rolls_back_partial_schema(self):
        broken_database_path = (
            Path(self.temporary_directory.name) / "broken.db"
        )
        with closing(sqlite3.connect(broken_database_path)) as connection:
            connection.execute(
                """
                CREATE VIEW person_voiceprint
                AS SELECT 'conflict' AS value
                """
            )
            connection.commit()

        with self.assertRaises(sqlite3.OperationalError):
            SQLiteIdentityRepository(broken_database_path)

        with closing(sqlite3.connect(broken_database_path)) as connection:
            objects = {
                (row[0], row[1])
                for row in connection.execute(
                    """
                    SELECT type, name
                    FROM sqlite_master
                    WHERE name IN ('family_person', 'person_voiceprint')
                    """
                )
            }
            persisted_version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]

        self.assertEqual(
            {("view", "person_voiceprint")},
            objects,
        )
        self.assertEqual(0, persisted_version)

    def test_person_can_be_disabled_and_enabled(self):
        self.repository.save_person(self.father)

        self.repository.set_person_enabled(
            "family_001",
            "person_father",
            False,
        )
        disabled = self.repository.get_person(
            "family_001",
            "person_father",
        )

        self.assertFalse(disabled.enabled)

        self.repository.set_person_enabled(
            "family_001",
            "person_father",
            True,
        )
        enabled = self.repository.get_person(
            "family_001",
            "person_father",
        )
        self.assertTrue(enabled.enabled)

    def test_binding_requires_existing_person_in_same_family(self):
        with self.assertRaises(PersonNotFoundError):
            self.repository.bind_voiceprint(
                "family_001",
                "person_missing",
                "voiceprint_missing",
            )

    def test_revoke_requires_active_voiceprint_in_same_family(self):
        self.repository.save_person(self.father)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_father",
        )

        with self.assertRaises(VoiceprintNotFoundError):
            self.repository.revoke_voiceprint(
                "family_002",
                "voiceprint_father",
            )

    def test_replace_voiceprint_failure_rolls_back_every_change(self):
        mother = PersonIdentity(
            "family_001",
            "person_mother",
            "妈妈",
        )
        self.repository.save_person(self.father)
        self.repository.save_person(mother)
        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_old",
        )
        self.repository.bind_voiceprint(
            "family_001",
            "person_mother",
            "voiceprint_occupied",
        )

        with self.assertRaises(VoiceprintAlreadyBoundError):
            self.repository.replace_voiceprint(
                "family_001",
                "person_father",
                "voiceprint_old",
                "voiceprint_occupied",
            )

        old_owner = self.repository.find_by_voiceprint_id(
            "family_001",
            "voiceprint_old",
        )
        occupied_owner = self.repository.find_by_voiceprint_id(
            "family_001",
            "voiceprint_occupied",
        )
        self.assertEqual("person_father", old_owner.person_id)
        self.assertEqual("person_mother", occupied_owner.person_id)

        self.repository.bind_voiceprint(
            "family_001",
            "person_father",
            "voiceprint_after_rollback",
        )
        self.assertEqual(
            "person_father",
            self.repository.find_by_voiceprint_id(
                "family_001",
                "voiceprint_after_rollback",
            ).person_id,
        )

    def read_user_version(self) -> int:
        with closing(sqlite3.connect(self.database_path)) as connection:
            return connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
