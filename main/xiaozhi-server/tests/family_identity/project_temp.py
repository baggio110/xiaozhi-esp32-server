"""项目目录内的测试临时目录。"""

import tempfile
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[2]
TEST_TEMP_ROOT = SERVER_ROOT / ".test_tmp"


class ProjectTemporaryDirectory:
    """在 xiaozhi-server/.test_tmp 下创建随机临时子目录。"""

    def __init__(self) -> None:
        TEST_TEMP_ROOT.mkdir(exist_ok=True)
        self._temporary_directory = tempfile.TemporaryDirectory(
            dir=TEST_TEMP_ROOT
        )
        self.name = self._temporary_directory.name
        self._cleaned = False

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._temporary_directory.cleanup()
        self._cleaned = True
        self._remove_empty_root()

    def __enter__(self) -> "ProjectTemporaryDirectory":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.cleanup()

    @staticmethod
    def _remove_empty_root() -> None:
        try:
            TEST_TEMP_ROOT.rmdir()
        except FileNotFoundError:
            return
        except OSError:
            if TEST_TEMP_ROOT.exists() and any(TEST_TEMP_ROOT.iterdir()):
                return
            raise
