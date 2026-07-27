"""家庭成员映射管理及部署预检工具测试。"""

import io
import inspect
import json
import sqlite3
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.family_identity import (
    IdentityDecision,
    PersonIdentity,
    SQLiteIdentityRepository,
    VoiceprintAlreadyBoundError,
    build_memory_user_id,
)
from tools.family_memory import admin
from tools.family_memory.cli import build_parser, main
from tools.family_memory.common import (
    ToolInputError,
    resolve_cli_database_path,
)
from tools.family_memory.preflight import run_preflight

from .project_temp import ProjectTemporaryDirectory


class FamilyMemoryToolTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = ProjectTemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.data_directory = self.root / "data"
        self.data_directory.mkdir()
        self.database_path = (
            self.data_directory / "family_identity.db"
        )
        self.manifest_path = self.root / "family-members.json"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_manifest(self, members=None, **overrides):
        manifest = {
            "family_id": "home_001",
            "database_path": str(self.database_path),
            "members": members
            if members is not None
            else [
                {
                    "person_id": "grandfather_001",
                    "display_name": "爷爷",
                    "enabled": True,
                    "voiceprint_ids": ["official_voiceprint_001"],
                }
            ],
        }
        manifest.update(overrides)
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        return self.manifest_path

    def load_manifest(self, **overrides):
        self.write_manifest(**overrides)
        return admin.load_manifest(
            self.manifest_path,
            server_root=self.root,
        )


class ManifestAndPlanTest(FamilyMemoryToolTestCase):
    def test_valid_manifest_allows_chinese_display_name(self):
        manifest = self.load_manifest()

        self.assertEqual("home_001", manifest.family_id)
        self.assertEqual("爷爷", manifest.members[0].person.display_name)
        self.assertEqual(
            "home_001:grandfather_001",
            manifest.members[0].person.memory_user_id,
        )

    def test_unknown_manifest_or_member_field_fails(self):
        with self.subTest("root"):
            self.write_manifest(unexpected=True)
            with self.assertRaises(ToolInputError):
                admin.load_manifest(
                    self.manifest_path,
                    server_root=self.root,
                )

        with self.subTest("member"):
            members = [
                {
                    "person_id": "person_001",
                    "display_name": "成员",
                    "enabled": True,
                    "voiceprint_ids": [],
                    "secret": "not-allowed",
                }
            ]
            self.write_manifest(members=members)
            with self.assertRaises(ToolInputError):
                admin.load_manifest(
                    self.manifest_path,
                    server_root=self.root,
                )

    def test_missing_family_and_duplicate_ids_fail(self):
        with self.subTest("missing family"):
            self.write_manifest()
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            del raw["family_id"]
            self.manifest_path.write_text(
                json.dumps(raw),
                encoding="utf-8",
            )
            with self.assertRaises(Exception):
                admin.load_manifest(
                    self.manifest_path,
                    server_root=self.root,
                )

        duplicate_people = [
            {
                "person_id": "same",
                "display_name": "甲",
                "enabled": True,
                "voiceprint_ids": [],
            },
            {
                "person_id": "same",
                "display_name": "乙",
                "enabled": True,
                "voiceprint_ids": [],
            },
        ]
        with self.subTest("duplicate person"):
            self.write_manifest(members=duplicate_people)
            with self.assertRaises(ToolInputError):
                admin.load_manifest(
                    self.manifest_path,
                    server_root=self.root,
                )

        duplicate_voiceprints = [
            {
                "person_id": "one",
                "display_name": "同名",
                "enabled": True,
                "voiceprint_ids": ["shared"],
            },
            {
                "person_id": "two",
                "display_name": "同名",
                "enabled": True,
                "voiceprint_ids": ["shared"],
            },
        ]
        with self.subTest("duplicate voiceprint"):
            self.write_manifest(members=duplicate_voiceprints)
            with self.assertRaises(ToolInputError):
                admin.load_manifest(
                    self.manifest_path,
                    server_root=self.root,
                )

    def test_same_display_name_different_person_ids_is_allowed(self):
        members = [
            {
                "person_id": "child_001",
                "display_name": "孩子",
                "enabled": True,
                "voiceprint_ids": [],
            },
            {
                "person_id": "child_002",
                "display_name": "孩子",
                "enabled": True,
                "voiceprint_ids": [],
            },
        ]
        manifest = self.load_manifest(members=members)

        self.assertEqual(2, len(manifest.members))
        self.assertNotEqual(
            manifest.members[0].person.memory_user_id,
            manifest.members[1].person.memory_user_id,
        )

    def test_plan_missing_database_is_read_only(self):
        manifest = self.load_manifest()

        result = admin.plan_manifest(manifest)

        self.assertTrue(result["ok"])
        self.assertFalse(self.database_path.exists())
        self.assertEqual(1, len(result["persons"]["add"]))
        self.assertEqual(1, len(result["voiceprints"]["add"]))
        self.assertFalse(Path(str(self.database_path) + "-wal").exists())
        self.assertFalse(Path(str(self.database_path) + "-shm").exists())

    def test_plan_existing_database_does_not_modify_bytes(self):
        repository = SQLiteIdentityRepository(self.database_path)
        repository.save_person(
            PersonIdentity("home_001", "person_001", "旧名称", False)
        )
        original = self.database_path.read_bytes()
        members = [
            {
                "person_id": "person_001",
                "display_name": "新名称",
                "enabled": True,
                "voiceprint_ids": ["new_voiceprint"],
            }
        ]
        manifest = self.load_manifest(members=members)

        result = admin.plan_manifest(manifest)

        self.assertTrue(result["ok"])
        self.assertEqual(original, self.database_path.read_bytes())
        self.assertEqual(1, len(result["persons"]["rename"]))
        self.assertEqual(1, len(result["persons"]["enable"]))
        self.assertEqual(1, len(result["voiceprints"]["add"]))

    def test_read_only_repository_sees_latest_committed_data(self):
        SQLiteIdentityRepository(self.database_path)
        readonly = SQLiteIdentityRepository(
            self.database_path,
            read_only=True,
        )
        keeper = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                "wal",
                keeper.execute("PRAGMA journal_mode = WAL").fetchone()[0],
            )
            keeper.execute("BEGIN")
            keeper.execute("SELECT COUNT(*) FROM family_person").fetchone()
            writer = SQLiteIdentityRepository(self.database_path)
            writer.save_person(
                PersonIdentity("home_001", "person_001", "新成员")
            )
            writer.bind_voiceprint(
                "home_001",
                "person_001",
                "new_voiceprint",
            )

            self.assertTrue(
                Path(str(self.database_path) + "-wal").exists()
            )
            self.assertEqual(
                ["person_001"],
                [
                    person.person_id
                    for person in readonly.list_persons("home_001")
                ],
            )
            self.assertEqual(
                "person_001",
                readonly.get_voiceprint_binding(
                    "new_voiceprint"
                ).person_id,
            )
            keeper.rollback()
            keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            keeper.execute("PRAGMA journal_mode = DELETE")
        finally:
            keeper.close()
        self.assertFalse(Path(str(self.database_path) + "-wal").exists())
        self.assertFalse(Path(str(self.database_path) + "-shm").exists())

    def test_plan_reopens_database_and_sees_latest_commits(self):
        SQLiteIdentityRepository(self.database_path)
        manifest = self.load_manifest()
        first = admin.plan_manifest(manifest)
        writer = SQLiteIdentityRepository(self.database_path)
        writer.save_person(manifest.members[0].person)
        writer.bind_voiceprint(
            manifest.family_id,
            manifest.members[0].person.person_id,
            manifest.members[0].voiceprint_ids[0],
        )

        second = admin.plan_manifest(manifest)

        self.assertEqual(1, len(first["persons"]["add"]))
        self.assertEqual(1, len(first["voiceprints"]["add"]))
        self.assertEqual(1, len(second["persons"]["unchanged"]))
        self.assertEqual(1, len(second["voiceprints"]["unchanged"]))
        self.assertEqual([], second["persons"]["add"])
        self.assertEqual([], second["voiceprints"]["add"])

    def test_plan_reports_unchanged_and_global_conflict(self):
        repository = SQLiteIdentityRepository(self.database_path)
        person = PersonIdentity("home_001", "person_001", "成员")
        repository.save_person(person)
        repository.bind_voiceprint(
            "home_001",
            "person_001",
            "voiceprint_same",
        )
        repository.save_person(
            PersonIdentity("other_home", "other", "其他家庭成员")
        )
        repository.bind_voiceprint(
            "other_home",
            "other",
            "voiceprint_conflict",
        )
        members = [
            {
                "person_id": "person_001",
                "display_name": "成员",
                "enabled": True,
                "voiceprint_ids": [
                    "voiceprint_same",
                    "voiceprint_conflict",
                ],
            }
        ]
        manifest = self.load_manifest(members=members)

        result = admin.plan_manifest(manifest)

        self.assertFalse(result["ok"])
        self.assertEqual(1, len(result["persons"]["unchanged"]))
        self.assertEqual(1, len(result["voiceprints"]["unchanged"]))
        self.assertEqual(1, len(result["voiceprints"]["conflicts"]))
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("other_home", encoded)
        self.assertNotIn("其他家庭成员", encoded)
        self.assertNotIn('"other"', encoded)

    def test_manifest_rejects_control_characters_and_non_boolean(self):
        cases = [
            {
                "person_id": "person\n001",
                "display_name": "成员",
                "enabled": True,
                "voiceprint_ids": [],
            },
            {
                "person_id": "person_001",
                "display_name": "成员\t",
                "enabled": True,
                "voiceprint_ids": [],
            },
            {
                "person_id": "person_001",
                "display_name": "成员",
                "enabled": "true",
                "voiceprint_ids": [],
            },
        ]
        for member in cases:
            with self.subTest(member=member):
                self.write_manifest(members=[member])
                with self.assertRaises(Exception):
                    admin.load_manifest(
                        self.manifest_path,
                        server_root=self.root,
                    )


class ApplyTransactionTest(FamilyMemoryToolTestCase):
    def test_apply_requires_exact_confirmation_before_write(self):
        manifest = self.load_manifest()

        with self.assertRaises(ToolInputError):
            admin.apply_manifest(
                manifest,
                confirm_family_id="wrong_home",
            )

        self.assertFalse(self.database_path.exists())

    def test_apply_arbitrary_members_and_multiple_voiceprints(self):
        labels = (
            ("grandfather_001", "爷爷"),
            ("grandmother_001", "奶奶"),
            ("child_001", "孩子"),
            ("caregiver_001", "照护人员"),
            ("relative_001", "Alex"),
        )
        members = [
            {
                "person_id": person_id,
                "display_name": display_name,
                "enabled": True,
                "voiceprint_ids": (
                    [f"voice_{person_id}", f"voice2_{person_id}"]
                    if person_id == "caregiver_001"
                    else [f"voice_{person_id}"]
                ),
            }
            for person_id, display_name in labels
        ]
        manifest = self.load_manifest(members=members)

        result = admin.apply_manifest(
            manifest,
            confirm_family_id="home_001",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(5, result["final_person_count"])
        self.assertEqual(6, result["final_active_voiceprint_count"])
        repository = SQLiteIdentityRepository(
            self.database_path,
            read_only=True,
        )
        for person_id, _ in labels:
            self.assertEqual(
                f"home_001:{person_id}",
                repository.get_person(
                    "home_001",
                    person_id,
                ).memory_user_id,
            )

    def test_repeated_apply_is_idempotent(self):
        manifest = self.load_manifest()
        first = admin.apply_manifest(
            manifest,
            confirm_family_id="home_001",
        )
        second = admin.apply_manifest(
            manifest,
            confirm_family_id="home_001",
        )

        self.assertEqual(1, first["final_person_count"])
        self.assertEqual(1, second["final_person_count"])
        self.assertEqual(0, second["success"]["person_changes"])
        self.assertEqual(0, second["success"]["voiceprint_bindings"])
        self.assertIn("无需修改", second["human_lines"])

    def test_rename_and_enabled_change_keep_memory_user_id(self):
        manifest = self.load_manifest()
        admin.apply_manifest(
            manifest,
            confirm_family_id="home_001",
        )
        members = [
            {
                "person_id": "grandfather_001",
                "display_name": "新显示名称",
                "enabled": False,
                "voiceprint_ids": ["official_voiceprint_001"],
            }
        ]
        updated = self.load_manifest(members=members)

        plan = admin.plan_manifest(updated)
        admin.apply_manifest(
            updated,
            confirm_family_id="home_001",
        )

        self.assertEqual(1, len(plan["persons"]["rename"]))
        self.assertEqual(1, len(plan["persons"]["disable"]))
        person = SQLiteIdentityRepository(
            self.database_path,
            read_only=True,
        ).get_person("home_001", "grandfather_001")
        self.assertEqual("home_001:grandfather_001", person.memory_user_id)
        self.assertFalse(person.enabled)

    def test_conflict_blocks_apply_without_touching_other_family(self):
        repository = SQLiteIdentityRepository(self.database_path)
        repository.save_person(
            PersonIdentity("other_home", "other_person", "其他")
        )
        repository.bind_voiceprint(
            "other_home",
            "other_person",
            "occupied_voiceprint",
        )
        members = [
            {
                "person_id": "new_person",
                "display_name": "新成员",
                "enabled": True,
                "voiceprint_ids": ["occupied_voiceprint"],
            }
        ]
        manifest = self.load_manifest(members=members)

        plan = admin.plan_manifest(manifest)
        encoded_plan = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn("other_home", encoded_plan)
        self.assertNotIn("other_person", encoded_plan)
        self.assertNotIn("其他", encoded_plan)

        with self.assertRaises(Exception) as raised:
            admin.apply_manifest(
                manifest,
                confirm_family_id="home_001",
            )
        error_text = str(raised.exception)
        self.assertNotIn("other_home", error_text)
        self.assertNotIn("other_person", error_text)
        self.assertNotIn("其他", error_text)

        repository = SQLiteIdentityRepository(
            self.database_path,
            read_only=True,
        )
        self.assertIsNone(
            repository.get_person("home_001", "new_person")
        )
        self.assertEqual(
            "other_person",
            repository.get_voiceprint_binding(
                "occupied_voiceprint"
            ).person_id,
        )

    def test_repository_rolls_back_unexpected_mid_apply_exception(self):
        repository = SQLiteIdentityRepository(self.database_path)
        original_database = self.database_path.read_bytes()
        persons = [
            PersonIdentity("home_001", "person_001", "甲"),
            PersonIdentity("home_001", "person_002", "乙"),
        ]
        original_bind = repository._bind_voiceprint
        call_count = 0

        def fail_second(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("injected failure")
            return original_bind(*args, **kwargs)

        with mock.patch.object(
            repository,
            "_bind_voiceprint",
            side_effect=fail_second,
        ):
            with self.assertRaises(RuntimeError):
                repository.apply_family_manifest(
                    "home_001",
                    persons,
                    [
                        ("person_001", "voice_001"),
                        ("person_002", "voice_002"),
                    ],
                )

        readonly = SQLiteIdentityRepository(
            self.database_path,
            read_only=True,
        )
        self.assertEqual([], readonly.list_persons("home_001"))
        self.assertEqual([], readonly.list_voiceprints("home_001"))
        self.assertEqual(original_database, self.database_path.read_bytes())
        self.assertFalse(Path(str(self.database_path) + "-wal").exists())
        self.assertFalse(Path(str(self.database_path) + "-shm").exists())

    def test_apply_uses_one_outer_transaction_without_early_commit(self):
        repository = SQLiteIdentityRepository(self.database_path)
        original_save = repository._save_person
        observed_counts = []

        def observe_before_outer_commit(connection, person, now):
            original_save(connection, person, now)
            with closing(sqlite3.connect(self.database_path)) as observer:
                observed_counts.append(
                    observer.execute(
                        """
                        SELECT COUNT(*)
                        FROM family_person
                        WHERE family_id = ?
                        """,
                        ("home_001",),
                    ).fetchone()[0]
                )

        with mock.patch.object(
            repository,
            "_save_person",
            side_effect=observe_before_outer_commit,
        ):
            repository.apply_family_manifest(
                "home_001",
                [
                    PersonIdentity(
                        "home_001",
                        "person_001",
                        "成员",
                    )
                ],
                [],
            )

        self.assertEqual([0], observed_counts)
        self.assertEqual(
            1,
            len(repository.list_persons("home_001")),
        )
        apply_source = inspect.getsource(
            SQLiteIdentityRepository.apply_family_manifest
        )
        save_source = inspect.getsource(
            SQLiteIdentityRepository._save_person
        )
        bind_source = inspect.getsource(
            SQLiteIdentityRepository._bind_voiceprint
        )
        self.assertEqual(1, apply_source.count("_write_transaction()"))
        for helper_source in (save_source, bind_source):
            self.assertNotIn(".commit(", helper_source)
            self.assertNotIn("BEGIN ", helper_source)
            self.assertNotIn("sqlite3.connect", helper_source)

    def test_manifest_does_not_delete_omitted_people_or_bindings(self):
        repository = SQLiteIdentityRepository(self.database_path)
        repository.save_person(
            PersonIdentity("home_001", "preserved", "保留成员")
        )
        repository.bind_voiceprint(
            "home_001",
            "preserved",
            "preserved_voice",
        )
        manifest = self.load_manifest()

        admin.apply_manifest(
            manifest,
            confirm_family_id="home_001",
        )

        readonly = SQLiteIdentityRepository(
            self.database_path,
            read_only=True,
        )
        self.assertIsNotNone(
            readonly.get_person("home_001", "preserved")
        )
        self.assertTrue(
            readonly.get_voiceprint_binding("preserved_voice").active
        )


class PersonAndVoiceprintCommandTest(FamilyMemoryToolTestCase):
    def test_person_upsert_rename_disable_enable_show_and_list(self):
        result = admin.person_upsert(
            family_id="home_001",
            person_id="person_001",
            display_name="成员",
            enabled=True,
            database_path=self.database_path,
            confirm_family_id="home_001",
        )
        original_memory_id = result["person"]["memory_user_id"]

        renamed = admin.person_rename(
            family_id="home_001",
            person_id="person_001",
            display_name="中文新名称",
            database_path=self.database_path,
            confirm_family_id="home_001",
        )
        admin.person_set_enabled(
            family_id="home_001",
            person_id="person_001",
            enabled=False,
            database_path=self.database_path,
            confirm_family_id="home_001",
        )
        enabled = admin.person_set_enabled(
            family_id="home_001",
            person_id="person_001",
            enabled=True,
            database_path=self.database_path,
            confirm_family_id="home_001",
        )
        shown = admin.person_show(
            "home_001",
            "person_001",
            self.database_path,
        )
        listed = admin.list_family(
            "home_001",
            self.database_path,
        )

        self.assertEqual(
            original_memory_id,
            renamed["person"]["memory_user_id"],
        )
        self.assertTrue(enabled["person"]["enabled"])
        self.assertEqual("中文新名称", shown["persons"][0]["display_name"])
        self.assertEqual(1, len(listed["persons"]))

    def test_voiceprint_bind_is_idempotent_and_conflict_fails(self):
        repository = SQLiteIdentityRepository(self.database_path)
        repository.save_person(
            PersonIdentity("home_001", "person_001", "甲")
        )
        repository.save_person(
            PersonIdentity("home_001", "person_002", "乙")
        )
        for _ in range(2):
            admin.voiceprint_bind(
                family_id="home_001",
                person_id="person_001",
                voiceprint_id="official_voice",
                database_path=self.database_path,
                confirm_family_id="home_001",
            )

        with self.assertRaises(VoiceprintAlreadyBoundError):
            admin.voiceprint_bind(
                family_id="home_001",
                person_id="person_002",
                voiceprint_id="official_voice",
                database_path=self.database_path,
                confirm_family_id="home_001",
            )
        self.assertEqual(
            1,
            len(repository.list_voiceprints("home_001")),
        )

    def test_voiceprint_revoke_replace_and_history_list(self):
        repository = SQLiteIdentityRepository(self.database_path)
        repository.save_person(
            PersonIdentity("home_001", "person_001", "成员")
        )
        repository.bind_voiceprint(
            "home_001",
            "person_001",
            "voice_old",
        )
        replaced = admin.voiceprint_replace(
            family_id="home_001",
            person_id="person_001",
            old_voiceprint_id="voice_old",
            new_voiceprint_id="voice_new",
            database_path=self.database_path,
            confirm_family_id="home_001",
        )
        admin.voiceprint_revoke(
            family_id="home_001",
            voiceprint_id="voice_new",
            database_path=self.database_path,
            confirm_family_id="home_001",
        )
        listed = admin.voiceprint_list(
            "home_001",
            self.database_path,
            person_id="person_001",
        )

        self.assertEqual(
            "home_001:person_001",
            replaced["memory_user_id"],
        )
        self.assertEqual(2, len(listed["voiceprints"]))
        self.assertTrue(
            all(not item["active"] for item in listed["voiceprints"])
        )

    def test_one_person_can_have_multiple_globally_unique_voiceprints(self):
        repository = SQLiteIdentityRepository(self.database_path)
        repository.save_person(
            PersonIdentity("home_001", "person_001", "成员")
        )
        for voiceprint_id in ("voice_a", "voice_b", "voice_c"):
            repository.bind_voiceprint(
                "home_001",
                "person_001",
                voiceprint_id,
            )

        self.assertEqual(
            3,
            len(
                repository.list_voiceprints(
                    "home_001",
                    "person_001",
                )
            ),
        )

    def test_family_lists_and_json_do_not_leak_other_family(self):
        repository = SQLiteIdentityRepository(self.database_path)
        repository.save_person(
            PersonIdentity("family_A", "shared_person", "家庭 A 成员")
        )
        repository.save_person(
            PersonIdentity("family_B", "shared_person", "家庭 B 同号成员")
        )
        repository.save_person(
            PersonIdentity("family_B", "private_person_B", "家庭 B 私人成员")
        )
        repository.bind_voiceprint(
            "family_A",
            "shared_person",
            "voice_A",
        )
        repository.bind_voiceprint(
            "family_B",
            "shared_person",
            "voice_B_shared",
        )
        repository.bind_voiceprint(
            "family_B",
            "private_person_B",
            "voice_B_private",
        )

        family_result = admin.list_family(
            "family_A",
            self.database_path,
        )
        voiceprint_result = admin.voiceprint_list(
            "family_A",
            self.database_path,
        )
        encoded = json.dumps(
            {
                "family": family_result,
                "voiceprints": voiceprint_result,
            },
            ensure_ascii=False,
        )

        self.assertEqual(1, len(family_result["persons"]))
        self.assertEqual(
            "family_A:shared_person",
            family_result["persons"][0]["memory_user_id"],
        )
        self.assertEqual(
            ["voice_A"],
            [
                item["voiceprint_id"]
                for item in voiceprint_result["voiceprints"]
            ],
        )
        for private_value in (
            "family_B",
            "家庭 B 同号成员",
            "家庭 B 私人成员",
            "private_person_B",
            "voice_B_shared",
            "voice_B_private",
        ):
            self.assertNotIn(private_value, encoded)

    def test_cli_has_no_physical_delete_command(self):
        parser = build_parser()
        help_text = parser.format_help()

        self.assertNotIn("delete", help_text)
        with self.assertRaises(SystemExit):
            parser.parse_args(["person", "delete"])

    def test_tool_modules_do_not_reference_voiceprint_api_or_powermem_calls(self):
        tool_root = (
            Path(__file__).resolve().parents[2]
            / "tools"
            / "family_memory"
        )
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in tool_root.glob("*.py")
        )

        self.assertNotIn("voiceprint-api", text)
        self.assertNotIn("memory_client.search(", text)
        self.assertNotIn("memory_client.profile(", text)
        self.assertNotIn("memory_client.add(", text)


class PreflightTest(FamilyMemoryToolTestCase):
    @staticmethod
    def powermem_module(
        *,
        missing_class=None,
        missing_user_id=None,
    ):
        class AsyncMemory:
            async def search(self, query, user_id, limit=30):
                raise AssertionError("预检不得调用search")

            async def add(self, messages, user_id):
                raise AssertionError("预检不得调用add")

        class UserMemory:
            def search(self, query, user_id, limit=30):
                raise AssertionError("预检不得调用search")

            def profile(self, user_id):
                raise AssertionError("预检不得调用profile")

            def add(self, messages, user_id):
                raise AssertionError("预检不得调用add")

        module = SimpleNamespace(
            __file__="/fake/site-packages/powermem/__init__.py",
            AsyncMemory=AsyncMemory,
            UserMemory=UserMemory,
        )
        if missing_class is not None:
            delattr(module, missing_class)
        if missing_user_id is not None:
            class_name, method_name = missing_user_id

            def incompatible(self, query):
                raise AssertionError("不得调用不兼容接口")

            setattr(
                getattr(module, class_name),
                method_name,
                incompatible,
            )
        return module

    def run_fake_preflight(
        self,
        *,
        version="0.5.3",
        module=None,
        enabled=True,
        family_id="home_001",
        database_path=None,
    ):
        fake_module = module or self.powermem_module()
        return run_preflight(
            enabled=enabled,
            family_id=family_id,
            database_path=str(
                database_path or self.database_path
            ),
            server_root=self.root,
            python_version=(3, 10, 20),
            python_executable="/fake/python",
            metadata_version=lambda package: version,
            module_importer=lambda name: fake_module,
        )

    def test_powermem_053_and_all_explicit_user_ids_pass(self):
        result = self.run_fake_preflight()

        checks = {item["name"]: item for item in result["checks"]}
        self.assertTrue(result["ok"])
        self.assertEqual("PASS", checks["python.version"]["level"])
        self.assertEqual("PASS", checks["powermem.version"]["level"])
        for name in (
            "AsyncMemory.search",
            "AsyncMemory.add",
            "UserMemory.search",
            "UserMemory.profile",
            "UserMemory.add",
        ):
            self.assertEqual("PASS", checks[name]["level"])
        self.assertFalse(self.database_path.exists())

    def test_powermem_missing_or_below_minimum_fails(self):
        with self.subTest("not installed"):
            def missing(package):
                raise importlib_metadata_not_found()

            result = run_preflight(
                enabled=True,
                family_id="home_001",
                database_path=str(self.database_path),
                server_root=self.root,
                python_version=(3, 10, 20),
                python_executable="/fake/python",
                metadata_version=missing,
                module_importer=lambda name: self.powermem_module(),
            )
            self.assertFalse(result["ok"])

        with self.subTest("below minimum"):
            result = self.run_fake_preflight(version="0.2.9")
            self.assertFalse(result["ok"])

    def test_missing_class_or_user_id_fails_without_calling_sdk(self):
        cases = [
            self.powermem_module(missing_class="AsyncMemory"),
            self.powermem_module(missing_class="UserMemory"),
            self.powermem_module(
                missing_user_id=("AsyncMemory", "search")
            ),
            self.powermem_module(
                missing_user_id=("UserMemory", "profile")
            ),
            self.powermem_module(
                missing_user_id=("UserMemory", "add")
            ),
        ]
        for module in cases:
            with self.subTest(module=module):
                result = self.run_fake_preflight(module=module)
                self.assertFalse(result["ok"])

    def test_higher_powermem_warns_but_compatible_signatures_continue(self):
        result = self.run_fake_preflight(version="0.6.0")

        self.assertTrue(result["ok"])
        self.assertEqual("WARN", result["overall"])

    def test_enabled_requires_family_and_data_path(self):
        missing_family = self.run_fake_preflight(family_id=None)
        outside_database = (
            self.root / "outside" / "family_identity.db"
        )
        outside_database.parent.mkdir()
        outside = self.run_fake_preflight(
            database_path=outside_database
        )

        self.assertFalse(missing_family["ok"])
        self.assertFalse(outside["ok"])
        self.assertFalse(outside_database.exists())

    def test_disabled_outside_data_is_warning(self):
        outside_database = self.root / "family_identity.db"

        result = self.run_fake_preflight(
            enabled=False,
            database_path=outside_database,
        )

        self.assertTrue(result["ok"])
        self.assertEqual("WARN", result["overall"])

    def test_missing_database_warns_without_creating_any_file(self):
        before = set(self.root.rglob("*"))

        result = self.run_fake_preflight()

        after = set(self.root.rglob("*"))
        identity_check = next(
            item
            for item in result["checks"]
            if item["name"] == "identity_database"
        )
        self.assertEqual("WARN", identity_check["level"])
        self.assertEqual(before, after)

    def test_existing_database_is_checked_read_only(self):
        SQLiteIdentityRepository(self.database_path)
        original = self.database_path.read_bytes()

        result = self.run_fake_preflight()

        identity_check = next(
            item
            for item in result["checks"]
            if item["name"] == "identity_database"
        )
        self.assertEqual("PASS", identity_check["level"])
        self.assertEqual(original, self.database_path.read_bytes())
        self.assertFalse(Path(str(self.database_path) + "-wal").exists())
        self.assertFalse(Path(str(self.database_path) + "-shm").exists())

    def test_pass_warn_fail_skip_summary_and_json_output_are_stable(self):
        result = self.run_fake_preflight()
        encoded = json.dumps(result, sort_keys=True)

        self.assertEqual(
            {"PASS", "WARN", "FAIL", "SKIP"},
            set(result["summary"]),
        )
        self.assertIn('"checks"', encoded)
        self.assertIn('"overall"', encoded)


class BoundaryAndCliTest(FamilyMemoryToolTestCase):
    def test_128_members_have_no_fixed_limit(self):
        members = [
            {
                "person_id": f"person_{index:03d}",
                "display_name": "同名成员",
                "enabled": True,
                "voiceprint_ids": [],
            }
            for index in range(128)
        ]
        manifest = self.load_manifest(members=members)
        result = admin.apply_manifest(
            manifest,
            confirm_family_id="home_001",
        )

        self.assertEqual(128, result["final_person_count"])

    def test_path_traversal_and_wrong_database_filename_are_rejected(self):
        with self.assertRaises(Exception):
            resolve_cli_database_path(
                "../family_identity.db",
                server_root=self.root,
            )
        with self.assertRaises(Exception):
            resolve_cli_database_path(
                str(self.data_directory / "other.db"),
                server_root=self.root,
            )

    def test_cli_plan_json_is_machine_readable_and_read_only(self):
        self.write_manifest()
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "plan",
                    "--file",
                    str(self.manifest_path),
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["ok"])
        self.assertFalse(self.database_path.exists())

    def test_cli_apply_requires_confirmation_argument(self):
        parser = build_parser()
        self.write_manifest()

        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["apply", "--file", str(self.manifest_path)]
            )

    def test_list_missing_database_is_read_only(self):
        result = admin.list_family(
            "home_001",
            self.database_path,
        )

        self.assertEqual([], result["persons"])
        self.assertFalse(self.database_path.exists())

    def test_repository_read_only_mode_rejects_writes(self):
        SQLiteIdentityRepository(self.database_path)
        readonly = SQLiteIdentityRepository(
            self.database_path,
            read_only=True,
        )

        with self.assertRaises(Exception):
            readonly.save_person(
                PersonIdentity("home_001", "person", "成员")
            )

    def test_read_only_connection_uses_mode_ro_and_query_only(self):
        SQLiteIdentityRepository(self.database_path)
        readonly = SQLiteIdentityRepository(
            self.database_path,
            read_only=True,
        )

        with readonly._connect() as connection:
            self.assertEqual(
                1,
                connection.execute("PRAGMA query_only").fetchone()[0],
            )
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    """
                    INSERT INTO family_person (
                        family_id,
                        person_id,
                        display_name,
                        enabled,
                        created_at,
                        updated_at
                    )
                    VALUES ('x', 'y', 'z', 1, 'now', 'now')
                    """
                )
        production_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                Path(__file__).resolve().parents[2]
                / "core"
                / "family_identity",
                Path(__file__).resolve().parents[2]
                / "tools"
                / "family_memory",
            )
            for path in path.glob("*.py")
        )
        self.assertNotIn("immutable=1", production_sources)
        self.assertFalse(Path(str(self.database_path) + "-wal").exists())
        self.assertFalse(Path(str(self.database_path) + "-shm").exists())

    def test_core_identity_model_keeps_existing_runtime_semantics(self):
        family_id = " family:官方/2026 "
        person_id = " person_官方-01.2 "
        person = PersonIdentity(
            family_id,
            person_id,
            "  中文 显示名称  ",
        )

        self.assertEqual(
            f"{family_id}:{person_id}",
            build_memory_user_id(family_id, person_id),
        )
        self.assertEqual("  中文 显示名称  ", person.display_name)
        decision = IdentityDecision.recognized(
            person,
            "voice:official/2026_01-abc.def",
        )
        self.assertEqual(
            "voice:official/2026_01-abc.def",
            decision.voiceprint_id,
        )
        self.assertEqual(
            f"{family_id}:{person_id}",
            decision.memory_user_id,
        )

    def test_tool_validation_does_not_trim_stable_identifiers(self):
        members = [
            {
                "person_id": " person_001 ",
                "display_name": " 中文 名称 ",
                "enabled": True,
                "voiceprint_ids": [" voice_001 "],
            }
        ]

        manifest = self.load_manifest(members=members)

        self.assertEqual(
            " person_001 ",
            manifest.members[0].person.person_id,
        )
        self.assertEqual(
            " voice_001 ",
            manifest.members[0].voiceprint_ids[0],
        )
        self.assertEqual(
            "home_001: person_001 ",
            manifest.members[0].person.memory_user_id,
        )

    def test_repository_uses_only_existing_two_tables(self):
        SQLiteIdentityRepository(self.database_path)

        with closing(sqlite3.connect(self.database_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertEqual(
            {"family_person", "person_voiceprint"},
            tables,
        )


def importlib_metadata_not_found():
    from importlib.metadata import PackageNotFoundError

    return PackageNotFoundError("powermem")


if __name__ == "__main__":
    unittest.main()
