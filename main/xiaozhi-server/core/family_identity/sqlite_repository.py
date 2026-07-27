"""基于 SQLite 的家庭身份映射仓库。"""

import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple, Union

from .errors import (
    IdentityRepositoryError,
    PersonNotFoundError,
    UnsupportedSchemaVersionError,
    VoiceprintAlreadyBoundError,
    VoiceprintNotFoundError,
    VoiceprintRevokedError,
)
from .models import (
    PersonIdentity,
    VoiceprintBinding,
)

PathValue = Union[str, os.PathLike]
SCHEMA_VERSION = 1


class SQLiteIdentityRepository:
    """使用短生命周期连接保存人员和声纹映射。"""

    def __init__(
        self,
        database_path: PathValue,
        *,
        timeout: float = 5.0,
        read_only: bool = False,
    ) -> None:
        self._database_path = self._validate_database_path(database_path)
        self._timeout = self._validate_timeout(timeout)
        if not isinstance(read_only, bool):
            raise TypeError("read_only 必须是布尔值")
        self._read_only = read_only
        if read_only:
            if not self._database_path.is_file():
                raise FileNotFoundError(
                    f"身份数据库不存在: {self._database_path}"
                )
            self._validate_supported_schema()
        else:
            self._initialize_schema()

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def read_only(self) -> bool:
        return self._read_only

    def save_person(self, person: PersonIdentity) -> None:
        """新增或更新家庭成员。"""

        self._ensure_writable()
        now = self._utc_now()
        with self._write_transaction() as connection:
            self._save_person(connection, person, now)

    def get_person(
        self,
        family_id: str,
        person_id: str,
    ) -> Optional[PersonIdentity]:
        """在家庭边界内按 person_id 查询成员。"""

        self._require_id(family_id, "family_id")
        self._require_id(person_id, "person_id")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT family_id, person_id, display_name, enabled
                FROM family_person
                WHERE family_id = ? AND person_id = ?
                """,
                (family_id, person_id),
            ).fetchone()
        return self._person_from_row(row)

    def list_persons(self, family_id: str) -> List[PersonIdentity]:
        """列出指定家庭的全部成员，不跨越家庭边界。"""

        self._require_id(family_id, "family_id")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT family_id, person_id, display_name, enabled
                FROM family_person
                WHERE family_id = ?
                ORDER BY person_id
                """,
                (family_id,),
            ).fetchall()
        return [self._person_from_row(row) for row in rows]

    def bind_voiceprint(
        self,
        family_id: str,
        person_id: str,
        voiceprint_id: str,
    ) -> None:
        """将未使用的声纹凭据绑定到指定人员。"""

        self._ensure_writable()
        self._require_mapping_ids(family_id, person_id, voiceprint_id)
        now = self._utc_now()

        with self._write_transaction() as connection:
            self._bind_voiceprint(
                connection,
                family_id,
                person_id,
                voiceprint_id,
                now,
            )

    def find_by_voiceprint_id(
        self,
        family_id: str,
        voiceprint_id: str,
    ) -> Optional[PersonIdentity]:
        """在家庭边界内解析未撤销的声纹凭据。"""

        self._require_id(family_id, "family_id")
        self._require_id(voiceprint_id, "voiceprint_id")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    person.family_id,
                    person.person_id,
                    person.display_name,
                    person.enabled
                FROM person_voiceprint AS voiceprint
                INNER JOIN family_person AS person
                    ON person.family_id = voiceprint.family_id
                    AND person.person_id = voiceprint.person_id
                WHERE voiceprint.family_id = ?
                    AND voiceprint.voiceprint_id = ?
                    AND voiceprint.revoked_at IS NULL
                """,
                (family_id, voiceprint_id),
            ).fetchone()
        return self._person_from_row(row)

    def get_voiceprint_binding(
        self,
        voiceprint_id: str,
    ) -> Optional[VoiceprintBinding]:
        """按全局唯一 voiceprint_id 查询绑定，包括撤销历史。"""

        self._require_id(voiceprint_id, "voiceprint_id")
        with self._connect() as connection:
            row = self._get_voiceprint_row(connection, voiceprint_id)
        return self._voiceprint_from_row(row)

    def list_voiceprints(
        self,
        family_id: str,
        person_id: Optional[str] = None,
    ) -> List[VoiceprintBinding]:
        """列出家庭内声纹绑定，可选限制到一个人员。"""

        self._require_id(family_id, "family_id")
        with self._connect() as connection:
            if person_id is None:
                rows = connection.execute(
                    """
                    SELECT voiceprint_id, family_id, person_id, revoked_at
                    FROM person_voiceprint
                    WHERE family_id = ?
                    ORDER BY person_id, created_at, voiceprint_id
                    """,
                    (family_id,),
                ).fetchall()
            else:
                self._require_id(person_id, "person_id")
                rows = connection.execute(
                    """
                    SELECT voiceprint_id, family_id, person_id, revoked_at
                    FROM person_voiceprint
                    WHERE family_id = ? AND person_id = ?
                    ORDER BY person_id, created_at, voiceprint_id
                    """,
                    (family_id, person_id),
                ).fetchall()
        return [self._voiceprint_from_row(row) for row in rows]

    def set_person_enabled(
        self,
        family_id: str,
        person_id: str,
        enabled: bool,
    ) -> None:
        """启用或禁用指定家庭成员。"""

        self._ensure_writable()
        self._require_id(family_id, "family_id")
        self._require_id(person_id, "person_id")
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是布尔值")

        with self._write_transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE family_person
                SET enabled = ?, updated_at = ?
                WHERE family_id = ? AND person_id = ?
                """,
                (
                    int(enabled),
                    self._utc_now(),
                    family_id,
                    person_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PersonNotFoundError(
                    f"家庭 {family_id} 中不存在人员 {person_id}"
                )

    def revoke_voiceprint(
        self,
        family_id: str,
        voiceprint_id: str,
    ) -> None:
        """明确撤销声纹凭据，保留不可复用的历史记录。"""

        self._ensure_writable()
        self._require_id(family_id, "family_id")
        self._require_id(voiceprint_id, "voiceprint_id")
        with self._write_transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE person_voiceprint
                SET revoked_at = ?
                WHERE family_id = ?
                    AND voiceprint_id = ?
                    AND revoked_at IS NULL
                """,
                (self._utc_now(), family_id, voiceprint_id),
            )
            if cursor.rowcount != 1:
                raise VoiceprintNotFoundError(
                    f"家庭 {family_id} 中不存在有效声纹 {voiceprint_id}"
                )

    def replace_voiceprint(
        self,
        family_id: str,
        person_id: str,
        old_voiceprint_id: str,
        new_voiceprint_id: str,
    ) -> None:
        """原子绑定新凭据并撤销旧凭据。"""

        self._ensure_writable()
        self._require_mapping_ids(
            family_id,
            person_id,
            old_voiceprint_id,
        )
        self._require_id(new_voiceprint_id, "new_voiceprint_id")
        if old_voiceprint_id == new_voiceprint_id:
            raise ValueError("新旧 voiceprint_id 不能相同")

        now = self._utc_now()
        with self._write_transaction() as connection:
            self._require_person(connection, family_id, person_id)
            old_binding = self._get_voiceprint_row(
                connection,
                old_voiceprint_id,
            )
            if (
                old_binding is None
                or old_binding["family_id"] != family_id
                or old_binding["person_id"] != person_id
                or old_binding["revoked_at"] is not None
            ):
                raise VoiceprintNotFoundError(
                    "旧声纹不存在、已撤销或不属于指定人员"
                )

            new_binding = self._get_voiceprint_row(
                connection,
                new_voiceprint_id,
            )
            if new_binding is not None:
                self._raise_existing_binding(
                    new_binding,
                    family_id,
                    person_id,
                    new_voiceprint_id,
                )
                raise VoiceprintAlreadyBoundError(
                    f"新声纹 {new_voiceprint_id} 已经绑定"
                )

            connection.execute(
                """
                INSERT INTO person_voiceprint (
                    voiceprint_id,
                    family_id,
                    person_id,
                    created_at,
                    revoked_at
                )
                VALUES (?, ?, ?, ?, NULL)
                """,
                (new_voiceprint_id, family_id, person_id, now),
            )
            connection.execute(
                """
                UPDATE person_voiceprint
                SET revoked_at = ?
                WHERE voiceprint_id = ?
                """,
                (now, old_voiceprint_id),
            )

    def apply_family_manifest(
        self,
        family_id: str,
        persons: Sequence[PersonIdentity],
        voiceprint_bindings: Sequence[Tuple[str, str]],
    ) -> None:
        """在一个事务内幂等应用人员和新增声纹绑定。"""

        self._ensure_writable()
        self._require_id(family_id, "family_id")
        checked_persons = tuple(persons)
        checked_bindings = tuple(voiceprint_bindings)
        for person in checked_persons:
            if not isinstance(person, PersonIdentity):
                raise TypeError("persons 只能包含 PersonIdentity")
            if person.family_id != family_id:
                raise ValueError("人员 family_id 与清单不一致")
        for person_id, voiceprint_id in checked_bindings:
            self._require_mapping_ids(
                family_id,
                person_id,
                voiceprint_id,
            )

        now = self._utc_now()
        with self._write_transaction() as connection:
            for person in checked_persons:
                self._save_person(connection, person, now)
            for person_id, voiceprint_id in checked_bindings:
                self._bind_voiceprint(
                    connection,
                    family_id,
                    person_id,
                    voiceprint_id,
                    now,
                )

    def inspect_schema(self) -> Dict[str, object]:
        """只读返回 schema 版本和表名。"""

        with self._connect() as connection:
            version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            tables = sorted(
                row[0]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                ).fetchall()
            )
        return {"user_version": version, "tables": tables}

    def _validate_supported_schema(self) -> None:
        schema = self.inspect_schema()
        current_version = schema["user_version"]
        if current_version > SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                "数据库结构版本 "
                f"{current_version} 高于当前支持版本 {SCHEMA_VERSION}"
            )

    def _initialize_schema(self) -> None:
        with self._write_transaction() as connection:
            current_version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            if current_version > SCHEMA_VERSION:
                raise UnsupportedSchemaVersionError(
                    "数据库结构版本 "
                    f"{current_version} 高于当前支持版本 {SCHEMA_VERSION}"
                )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS family_person (
                    family_id TEXT NOT NULL,
                    person_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL
                        CHECK (enabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (family_id, person_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS person_voiceprint (
                    voiceprint_id TEXT NOT NULL PRIMARY KEY,
                    family_id TEXT NOT NULL,
                    person_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    FOREIGN KEY (family_id, person_id)
                        REFERENCES family_person(family_id, person_id)
                        ON UPDATE CASCADE
                        ON DELETE RESTRICT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_person_voiceprint_family_person
                ON person_voiceprint(family_id, person_id)
                """
            )
            if current_version == 0:
                connection.execute("PRAGMA user_version = 1")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._read_only:
            database_uri = (
                self._database_path.resolve().as_uri()
                + "?mode=ro"
            )
            connection = sqlite3.connect(
                database_uri,
                timeout=self._timeout,
                uri=True,
            )
            connection.execute("PRAGMA query_only = ON")
        else:
            connection = sqlite3.connect(
                self._database_path,
                timeout=self._timeout,
            )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        self._ensure_writable()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _validate_database_path(database_path: PathValue) -> Path:
        try:
            raw_path = os.fspath(database_path)
        except TypeError as exc:
            raise TypeError("database_path 必须是字符串或 PathLike") from exc
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("database_path 不能为空")
        if raw_path == ":memory:":
            raise ValueError(
                "短连接 Repository 不支持 :memory:，测试请使用临时文件"
            )

        path = Path(raw_path)
        if path.exists() and path.is_dir():
            raise ValueError("database_path 不能指向目录")
        if not path.parent.exists():
            raise FileNotFoundError(
                f"数据库父目录不存在: {path.parent}"
            )
        return path

    @staticmethod
    def _validate_timeout(timeout: float) -> float:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout 必须是正的有限数值")
        return float(timeout)

    @staticmethod
    def _require_id(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} 不能为空")

    def _require_mapping_ids(
        self,
        family_id: str,
        person_id: str,
        voiceprint_id: str,
    ) -> None:
        self._require_id(family_id, "family_id")
        self._require_id(person_id, "person_id")
        self._require_id(voiceprint_id, "voiceprint_id")

    @staticmethod
    def _require_person(
        connection: sqlite3.Connection,
        family_id: str,
        person_id: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT 1
            FROM family_person
            WHERE family_id = ? AND person_id = ?
            """,
            (family_id, person_id),
        ).fetchone()
        if row is None:
            raise PersonNotFoundError(
                f"家庭 {family_id} 中不存在人员 {person_id}"
            )

    @staticmethod
    def _get_voiceprint_row(
        connection: sqlite3.Connection,
        voiceprint_id: str,
    ) -> Optional[sqlite3.Row]:
        return connection.execute(
            """
            SELECT voiceprint_id, family_id, person_id, revoked_at
            FROM person_voiceprint
            WHERE voiceprint_id = ?
            """,
            (voiceprint_id,),
        ).fetchone()

    @staticmethod
    def _save_person(
        connection: sqlite3.Connection,
        person: PersonIdentity,
        now: str,
    ) -> None:
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
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(family_id, person_id) DO UPDATE SET
                display_name = excluded.display_name,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                person.family_id,
                person.person_id,
                person.display_name,
                int(person.enabled),
                now,
                now,
            ),
        )

    def _bind_voiceprint(
        self,
        connection: sqlite3.Connection,
        family_id: str,
        person_id: str,
        voiceprint_id: str,
        now: str,
    ) -> None:
        self._require_person(connection, family_id, person_id)
        existing = self._get_voiceprint_row(connection, voiceprint_id)
        if existing is not None:
            self._raise_existing_binding(
                existing,
                family_id,
                person_id,
                voiceprint_id,
            )
            return

        connection.execute(
            """
            INSERT INTO person_voiceprint (
                voiceprint_id,
                family_id,
                person_id,
                created_at,
                revoked_at
            )
            VALUES (?, ?, ?, ?, NULL)
            """,
            (voiceprint_id, family_id, person_id, now),
        )

    @staticmethod
    def _raise_existing_binding(
        existing: sqlite3.Row,
        family_id: str,
        person_id: str,
        voiceprint_id: str,
    ) -> None:
        if existing["revoked_at"] is not None:
            raise VoiceprintRevokedError(
                f"声纹 {voiceprint_id} 已撤销，不能重新绑定"
            )
        if (
            existing["family_id"] != family_id
            or existing["person_id"] != person_id
        ):
            raise VoiceprintAlreadyBoundError(
                f"声纹 {voiceprint_id} 已绑定其他人员"
            )

    @staticmethod
    def _person_from_row(
        row: Optional[sqlite3.Row],
    ) -> Optional[PersonIdentity]:
        if row is None:
            return None
        return PersonIdentity(
            family_id=row["family_id"],
            person_id=row["person_id"],
            display_name=row["display_name"],
            enabled=bool(row["enabled"]),
        )

    @staticmethod
    def _voiceprint_from_row(
        row: Optional[sqlite3.Row],
    ) -> Optional[VoiceprintBinding]:
        if row is None:
            return None
        return VoiceprintBinding(
            voiceprint_id=row["voiceprint_id"],
            family_id=row["family_id"],
            person_id=row["person_id"],
            revoked_at=row["revoked_at"],
        )

    def _ensure_writable(self) -> None:
        if self._read_only:
            raise IdentityRepositoryError("只读 Repository 禁止写入")

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()
