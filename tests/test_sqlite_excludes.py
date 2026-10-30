"""使用真实 rsync 和 SQLite，验证安全备份不会绕过用户的排除规则。"""

import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "vback.sh"


def load_shell_function(name):
    """按名称读取生产函数，避免运行交互入口；返回 Shell 代码，函数缺失时抛出异常。"""
    script = SCRIPT_PATH.read_text()
    start = script.index(f"{name}() {{")
    end = script.index("\n}\n", start) + len("\n}\n")
    return script[start:end]


class SQLiteExcludeTests(unittest.TestCase):
    """验证排除规则与安全备份开关组合后的实际文件结果。"""

    @classmethod
    def setUpClass(cls):
        """检查测试依赖并读取生产函数；缺少工具时明确报错。"""
        for command in ("bash", "rsync", "sqlite3"):
            if shutil.which(command) is None:
                raise RuntimeError(f"运行测试需要安装 {command}")
        cls.backup_functions = "\n".join(
            load_shell_function(name)
            for name in ("backup_sqlite_db", "prepare_safe_copy")
        )

    def setUp(self):
        """创建隔离的源目录和备份目录，测试结束后自动清理临时文件。"""
        self.temp_dir = tempfile.TemporaryDirectory(prefix="vback-test-")
        self.addCleanup(self.temp_dir.cleanup)
        self.source = Path(self.temp_dir.name) / "source with spaces"
        self.destination = Path(self.temp_dir.name) / "backup"
        self.source.mkdir()

    def create_files(self, *paths):
        """按相对路径创建数据库或普通文件；数据库初始化与文件写入失败时抛出异常。"""
        for relative_path in paths:
            path = self.source / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix in (".db", ".sqlite"):
                with sqlite3.connect(path) as database:
                    database.execute("CREATE TABLE sample (value TEXT)")
                    database.execute("INSERT INTO sample VALUES ('backup-test')")
            else:
                path.write_text("backup-test")

    def prepare_copy(self, *patterns, safe_backup=True):
        """使用给定排除规则运行真实复制函数，返回结果文件集合；执行失败时测试失败。"""
        command = "set -o pipefail\n" + self.backup_functions + """
# 隔离界面和日志输出，不替换实际复制及数据库备份逻辑。
info() { :; }
warn() { printf '%s\\n' "$1" >&2; }
SQLITE_SAFE_BACKUP="$3"
EXCLUDE_PATTERNS=("${@:4}")
prepare_safe_copy "$1" "$2"
"""
        result = subprocess.run(
            [
                "bash", "-c", command, "vback-test",
                str(self.source), str(self.destination),
                "true" if safe_backup else "false", *patterns,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return {
            str(path.relative_to(self.destination))
            for path in self.destination.rglob("*")
            if path.is_file()
        }

    def test_excluded_directory_does_not_restore_databases(self):
        """回归：被排除目录中的两种数据库都不能被安全备份重新加入。"""
        self.create_files("cache/hidden.db", "cache/hidden.sqlite", "keep.db")
        self.assertEqual(self.prepare_copy("cache/"), {"keep.db"})

    def test_directory_name_matches_at_any_depth(self):
        """目录名称规则应同时排除根目录与深层目录中的数据库。"""
        self.create_files("cache/root.db", "nested/cache/deep.sqlite", "nested/keep.db")
        self.assertEqual(self.prepare_copy("cache/"), {"nested/keep.db"})

    def test_anchored_directory_only_matches_source_root(self):
        """开头的斜杠应以备份源目录为根，不应排除深层同名目录。"""
        self.create_files("cache/root.db", "nested/cache/deep.sqlite")
        self.assertEqual(self.prepare_copy("/cache/"), {"nested/cache/deep.sqlite"})

    def test_nested_directory_path(self):
        """指定目录路径只排除匹配路径中的数据库。"""
        self.create_files("storage/cache/hidden.db", "other/cache/keep.sqlite")
        self.assertEqual(self.prepare_copy("/storage/cache/"), {"other/cache/keep.sqlite"})

    def test_database_filename_and_wildcard_patterns(self):
        """数据库文件本身也必须遵守精确路径与通配符排除规则。"""
        self.create_files("data/skip.db", "data/cache-1.sqlite", "data/keep.db")
        self.assertEqual(
            self.prepare_copy("/data/skip.db", "cache-*.sqlite"),
            {"data/keep.db"},
        )

    def test_default_directory_patterns(self):
        """默认排除的依赖目录和 Git 目录不能因包含数据库而重新出现。"""
        self.create_files("node_modules/pkg/state.db", ".git/state.sqlite", "data/keep.db")
        self.assertEqual(self.prepare_copy("node_modules", ".git"), {"data/keep.db"})

    def test_kept_database_contents_and_special_paths(self):
        """未排除的数据库应保留内容，并支持空格、换行和反斜杠文件名。"""
        paths = ("data/with spaces.db", "data/line\nbreak.sqlite", "data/backslash\\#012.db")
        self.create_files(*paths)
        self.assertEqual(self.prepare_copy(), set(paths))
        for relative_path in paths:
            with sqlite3.connect(self.destination / relative_path) as database:
                self.assertEqual(database.execute("SELECT value FROM sample").fetchone(), ("backup-test",))

    def test_directory_only_rule_keeps_a_same_named_database_file(self):
        """末尾斜杠只匹配目录，不能把同名数据库文件也排除。"""
        self.create_files("cache.db", "nested/cache.db/hidden.sqlite")
        self.assertEqual(self.prepare_copy("cache.db/"), {"cache.db"})

    def test_recursive_wildcard_directory_rule(self):
        """递归通配符规则应继续按 rsync 的语义筛选深层目录。"""
        self.create_files("data/one/cache/a.db", "data/deep/two/cache/b.sqlite", "data/keep.db")
        self.assertEqual(self.prepare_copy("/data/**/cache/***"), {"data/keep.db"})

    def test_database_symlinks_are_not_snapshotted(self):
        """保持原有行为：安全备份只处理普通数据库文件，不跟随数据库符号链接。"""
        self.create_files("keep.db")
        (self.source / "alias.db").symlink_to("keep.db")
        self.assertEqual(self.prepare_copy(), {"keep.db"})

    def test_existing_backup_database_is_refreshed(self):
        """目标已存在相同大小和时间的数据库时，仍需执行安全备份。"""
        self.create_files("keep.db")
        self.destination.mkdir()
        shutil.copy2(self.source / "keep.db", self.destination / "keep.db")
        command = "set -o pipefail\n" + self.backup_functions + """
info() { :; }
warn() { :; }
# 记录安全备份确实被调用，而不是由普通 rsync 复制留下目标文件。
backup_sqlite_db() { printf 'snapshot\\n' > "$2"; }
SQLITE_SAFE_BACKUP=true
EXCLUDE_PATTERNS=()
prepare_safe_copy "$1" "$2"
"""
        subprocess.run(
            ["bash", "-c", command, "vback-test", str(self.source), str(self.destination)],
            check=True,
            capture_output=True,
        )
        self.assertEqual((self.destination / "keep.db").read_text(), "snapshot\n")

    def test_selection_failure_aborts_before_archiving(self):
        """数据库筛选失败时应停止备份，不能继续打包上传不完整的文件。"""
        marker = Path(self.temp_dir.name) / "unexpected-archive"
        command = (
            "set -o pipefail\n" + self.backup_functions
            + load_shell_function("backup_dir") + """
info() { :; }
error() { :; }
get_dir_size() { printf '0\\n'; }
count_files() { printf '0\\n'; }
fmt_size() { printf '0 B\\n'; }
rsync() { printf 'selection failed\\n' >&2; return 23; }
tar() { printf 'unexpected archive\\n' > "$ARCHIVE_MARKER"; return 1; }
TEMP_DIR="$2"
ARCHIVE_MARKER="$3"
SQLITE_SAFE_BACKUP=true
COMPRESS_BACKUP=true
COMPRESSION_LEVEL=1
EXCLUDE_PATTERNS=()
backup_dir "$1" test
"""
        )
        result = subprocess.run(
            [
                "bash", "-c", command, "vback-test", str(self.source),
                str(self.destination), str(marker),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("selection failed", result.stderr)
        self.assertFalse(marker.exists(), "数据库筛选失败后仍然尝试打包")

    def test_sqlite_sidecars_remain_excluded(self):
        """安全备份保留数据库，但不复制 WAL、共享内存及回滚日志文件。"""
        self.create_files("keep.db", "keep.db-wal", "keep.db-shm", "keep.db-journal", "notes.txt")
        self.assertEqual(self.prepare_copy(), {"keep.db", "notes.txt"})

    def test_disabled_safe_backup_still_respects_excludes(self):
        """关闭安全备份时，普通复制仍应遵守相同的目录排除规则。"""
        self.create_files("cache/hidden.db", "keep.db", "notes.txt")
        self.assertEqual(self.prepare_copy("cache/", safe_backup=False), {"keep.db", "notes.txt"})

    def test_excluding_all_databases_leaves_no_database_directories(self):
        """排除目录后，安全备份不应为其中的数据库重新创建空目录。"""
        self.create_files("cache/hidden.db", "notes.txt")
        self.assertEqual(self.prepare_copy("cache/"), {"notes.txt"})
        self.assertFalse((self.destination / "cache").exists())


if __name__ == "__main__":
    unittest.main()
