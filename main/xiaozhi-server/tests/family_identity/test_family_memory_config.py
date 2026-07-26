"""家庭记忆配置和项目内数据库路径测试。"""

import unittest
from dataclasses import fields
from pathlib import Path

from core.family_identity import (
    DEFAULT_DATABASE_PATH,
    FamilyMemoryConfigurationError,
    FamilyMemorySettings,
    InvalidDatabasePathError,
    resolve_database_path,
)

from .project_temp import ProjectTemporaryDirectory


class FamilyMemorySettingsTest(unittest.TestCase):
    def test_default_settings_are_disabled(self):
        settings = FamilyMemorySettings()

        self.assertFalse(settings.enabled)

    def test_disabled_settings_allow_empty_family_id(self):
        for family_id in (None, "", " "):
            with self.subTest(family_id=family_id):
                settings = FamilyMemorySettings(
                    enabled=False,
                    family_id=family_id,
                )
                self.assertFalse(settings.enabled)

    def test_enabled_settings_reject_empty_family_id(self):
        for family_id in (None, "", " "):
            with self.subTest(family_id=family_id):
                with self.assertRaises(
                    FamilyMemoryConfigurationError
                ):
                    FamilyMemorySettings(
                        enabled=True,
                        family_id=family_id,
                    )

    def test_enabled_settings_accept_valid_family_id(self):
        settings = FamilyMemorySettings(
            enabled=True,
            family_id="family_001",
        )

        self.assertTrue(settings.enabled)
        self.assertEqual("family_001", settings.family_id)

    def test_default_database_path_is_project_relative(self):
        settings = FamilyMemorySettings()

        self.assertEqual(
            "data/family_identity.db",
            settings.database_path,
        )
        self.assertEqual(DEFAULT_DATABASE_PATH, settings.database_path)

    def test_settings_from_mapping_use_only_three_fields(self):
        settings = FamilyMemorySettings.from_mapping(
            {
                "enabled": True,
                "family_id": "family_001",
                "database_path": "state/family.db",
            }
        )

        self.assertEqual(
            {"enabled", "family_id", "database_path"},
            {item.name for item in fields(settings)},
        )
        self.assertEqual("state/family.db", settings.database_path)

    def test_settings_reject_unknown_fields(self):
        with self.assertRaises(FamilyMemoryConfigurationError):
            FamilyMemorySettings.from_mapping(
                {
                    "enabled": False,
                    "room_id": "living-room",
                }
            )

    def test_settings_reject_absolute_database_path_when_disabled(self):
        absolute_path = Path(__file__).resolve()

        with self.assertRaises(InvalidDatabasePathError):
            FamilyMemorySettings(
                enabled=False,
                database_path=str(absolute_path),
            )

    def test_settings_do_not_coerce_invalid_types(self):
        invalid_values = (
            {"enabled": "false"},
            {"family_id": 123},
            {"database_path": None},
        )

        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(
                    FamilyMemoryConfigurationError
                ):
                    FamilyMemorySettings.from_mapping(values)


class DatabasePathResolutionTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = ProjectTemporaryDirectory()
        self.server_root = (
            Path(self.temporary_directory.name) / "xiaozhi-server"
        )
        self.server_root.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_default_path_resolves_inside_server_root(self):
        resolved = resolve_database_path(
            self.server_root,
            DEFAULT_DATABASE_PATH,
        )

        self.assertEqual(
            self.server_root / "data" / "family_identity.db",
            resolved,
        )

    def test_valid_nested_relative_path_is_allowed(self):
        resolved = resolve_database_path(
            self.server_root,
            "state/identity/family.db",
        )

        self.assertEqual(
            self.server_root / "state" / "identity" / "family.db",
            resolved,
        )

    def test_absolute_database_path_is_rejected(self):
        absolute_path = (
            self.server_root / "outside" / "family.db"
        ).resolve()

        with self.assertRaises(InvalidDatabasePathError):
            resolve_database_path(self.server_root, str(absolute_path))

    def test_parent_traversal_outside_server_root_is_rejected(self):
        with self.assertRaises(InvalidDatabasePathError):
            resolve_database_path(
                self.server_root,
                "../outside/family.db",
            )

    def test_normalized_path_remaining_inside_root_is_allowed(self):
        resolved = resolve_database_path(
            self.server_root,
            "data/../state/family.db",
        )

        self.assertEqual(
            self.server_root / "state" / "family.db",
            resolved,
        )

    def test_path_resolution_creates_no_directory_or_database(self):
        resolved = resolve_database_path(
            self.server_root,
            "not-created/nested/family.db",
        )

        self.assertFalse(resolved.exists())
        self.assertFalse(resolved.parent.exists())

    def test_relative_server_root_is_rejected(self):
        with self.assertRaises(InvalidDatabasePathError):
            resolve_database_path(
                Path("relative-server-root"),
                DEFAULT_DATABASE_PATH,
            )
