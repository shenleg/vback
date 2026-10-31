"""通过真实 v1 命令入口和本地假 S3，验证完整备份、恢复及启动失败行为。"""

import os
import shlex
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "vback.sh"


class CLITests(unittest.TestCase):
    """隔离用户目录、S3 和 crontab，运行真实脚本入口。"""

    @classmethod
    def setUpClass(cls):
        """查找实际运行的 Bash；缺少工具时明确报错。"""
        cls.bash = shutil.which("bash")
        for tool in ("bash", "tar", "gzip", "rsync", "sqlite3"):
            if shutil.which(tool) is None:
                raise RuntimeError(f"运行测试需要安装 {tool}")

    def setUp(self):
        """创建隔离的配置、来源和假 S3 可执行文件；磁盘错误时抛出异常。"""
        directory = tempfile.TemporaryDirectory(prefix="vback-cli-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.source = self.root / "source with spaces" / "app"
        self.source.mkdir(parents=True)
        self.remote = self.root / "remote-object"
        self.calls = self.root / "calls"
        # 假 AWS 只复制测试归档；系统目录中不会放置此测试执行器。
        aws = self.bin / "aws"
        aws.write_text(r'''#!/bin/bash
set -e
printf '%s\n' "$*" >> "$FAKE_CALLS"
case "$1 $2" in
    's3 cp')
        if [[ "$3" == s3://* ]]; then
            cp "$FAKE_OBJECT" "$4"
        else
            cp "$3" "$FAKE_OBJECT"
            printf '%s\n' "$4" > "$FAKE_KEY"
            printf 'uploaded to fail/error path\n'
        fi
        ;;
    's3api head-object')
        if command -v md5sum >/dev/null; then
            md5sum "$FAKE_OBJECT" | cut -d ' ' -f 1
        else
            md5 -q "$FAKE_OBJECT"
        fi
        ;;
    's3 ls')
        if [[ -f "$FAKE_KEY" ]]; then
            key="$(cat "$FAKE_KEY")"
            printf '2026-10-31 03:00:00 1024 %s\n' "${key##*/}"
        fi
        ;;
    's3 rm') : ;;
    *) printf 'Unexpected fake AWS call: %s\n' "$*" >&2; exit 99 ;;
esac
''')
        aws.chmod(0o700)
        # 一旦测试误触 crontab 就失败，不允许读取或修改用户计划。
        crontab = self.bin / "crontab"
        crontab.write_text("#!/bin/sh\nprintf 'unexpected crontab call\\n' >&2\nexit 99\n")
        crontab.chmod(0o700)
        self.environment = {
            **os.environ,
            "HOME": str(self.root),
            "VBACK_DATA_DIR": str(self.data),
            "TMPDIR": str(self.root),
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "TERM": "dumb",
            "FAKE_OBJECT": str(self.remote),
            "FAKE_KEY": str(self.root / "remote-key"),
            "FAKE_CALLS": str(self.calls),
            "AWS_CONFIG_FILE": str(self.root / "no-config"),
            "AWS_SHARED_CREDENTIALS_FILE": str(self.root / "no-credentials"),
        }
        config = {
            "CLOUD_PROVIDER": "custom", "S3_ACCESS_KEY": "test-key", "S3_SECRET_KEY": "test-secret",
            "S3_ENDPOINT": "storage.invalid", "S3_BUCKET": "test-bucket", "S3_REGION": "test-region",
            "ACTIVE_TASK_ID": "task_web", "DEFAULT_TASK_ID": "task_web", "SCHEDULE_CRON": "",
        }
        self.write_private("config", "".join(f"{key}={shlex.quote(value)}\n" for key, value in config.items()))
        self.write_private("tasks", f"""TASK_IDS=(
 task_web
)
ACTIVE_TASK_ID=task_web
DEFAULT_TASK_ID=task_web
TASK_NAME_task_web='Web App'
TASK_PREFIX_task_web='backups/fail-error'
TASK_MAX_BACKUPS_task_web=7
TASK_COMPRESS_task_web=true
TASK_COMPRESSION_LEVEL_task_web=1
TASK_SQLITE_SAFE_task_web=true
TASK_DIRS_task_web=(
 {shlex.quote(str(self.source))}
)
TASK_EXCLUDES_task_web=(
 'cache/'
)
""")
        self.write_private("schedules", "SCHEDULE_IDS=()\n")

    def write_private(self, name, content):
        """写入属于当前用户的 600 配置，返回文件路径；磁盘错误时抛出异常。"""
        path = self.data / name
        path.write_text(content)
        path.chmod(0o600)
        return path

    def run_cli(self, *arguments, stdin=None, environment=None):
        """执行真实脚本入口，返回进程结果；进程超时或启动失败时抛出异常。"""
        return subprocess.run(
            [self.bash, str(SCRIPT), "--lang", "en", *arguments],
            input=stdin, capture_output=True, text=True, timeout=30,
            env=self.environment if environment is None else environment,
        )

    def test_backup_and_restore_active_sqlite_with_exclusions(self):
        """真实备份和恢复应保留 WAL 提交数据、排除数据库，并接受输出含 error 的成功上传。"""
        (self.source / "notes.txt").write_text("restore-content")
        cache = self.source / "cache"
        cache.mkdir()
        with sqlite3.connect(cache / "hidden.db") as excluded:
            excluded.execute("CREATE TABLE hidden (value TEXT)")
        database = sqlite3.connect(self.source / "active.db")
        self.addCleanup(database.close)
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE sample (value TEXT)")
        database.execute("INSERT INTO sample VALUES ('wal-committed')")
        database.commit()
        result = self.run_cli("backup", "--task", "task_web")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.remote.exists())
        with tarfile.open(self.remote) as archive:
            names = set(archive.getnames())
            self.assertIn("app/notes.txt", names)
            self.assertIn("app/active.db", names)
            self.assertFalse(any("cache" in name or name.endswith(("-wal", "-shm")) for name in names), names)
        destination = self.root / "restored files"
        result = self.run_cli("restore", "--task", "task_web", stdin=f"1\n{destination}\ny\n\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((destination / "app" / "notes.txt").read_text(), "restore-content")
        with sqlite3.connect(destination / "app" / "active.db") as restored:
            self.assertEqual(restored.execute("SELECT value FROM sample").fetchone(), ("wal-committed",))
        self.assertFalse((destination / "app" / "cache").exists())
        self.assertIn("s3api head-object", self.calls.read_text())

    def test_restore_invalid_archive_returns_failure(self):
        """命令行交互恢复拒绝损坏归档时必须向调用者返回失败。"""
        self.remote.write_bytes(b"invalid archive")
        (self.root / "remote-key").write_text("s3://test-bucket/backups/fail-error/app/broken.tar")
        destination = self.root / "invalid-restore"
        result = self.run_cli("restore", "--task", "task_web", stdin=f"1\n{destination}\ny\n\n")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(destination.exists())

    def test_config_does_not_require_s3_tools(self):
        """仅查看配置不应要求安装 S3 工具或触发安装提示。"""
        environment = {**self.environment, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
        result = self.run_cli("config", environment=environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Web App", result.stdout)
        self.assertIn(str(self.source), result.stdout)
        self.assertFalse(self.calls.exists())

    def test_invalid_config_stops_before_overwriting_data(self):
        """配置解析失败必须使真实入口退出，不能保存新配置覆盖错误文件。"""
        path = self.write_private("config", "UNKNOWN=1\n")
        tasks_before = (self.data / "tasks").read_bytes()
        result = self.run_cli("config")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unsupported field UNKNOWN", result.stderr)
        self.assertEqual(path.read_text(), "UNKNOWN=1\n")
        self.assertEqual((self.data / "tasks").read_bytes(), tasks_before)
        self.assertFalse(self.calls.exists())

    def test_unsafe_reset_stops_before_initialization(self):
        """危险重置目录应在初始化日志和访问 crontab 前被拒绝。"""
        result = self.run_cli("reset", environment={**self.environment, "VBACK_DATA_DIR": str(self.root)})
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Refusing to reset unsafe data directory", result.stderr)
        self.assertFalse((self.root / "logs").exists())
        self.assertTrue(self.data.exists())

    def test_legacy_system_bash_stops_before_creating_data(self):
        """旧系统 Bash 应在任何用户配置读写之前明确退出。"""
        system_bash = Path("/bin/bash")
        version = subprocess.run([str(system_bash), "-c", "printf '%s.%s' ${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"],
                                 capture_output=True, text=True, check=True)
        if tuple(map(int, version.stdout.split("."))) >= (4, 2):
            self.skipTest("系统 Bash 已满足要求")
        data = self.root / "not-created"
        result = subprocess.run([str(system_bash), str(SCRIPT), "--help"],
                                env={**self.environment, "VBACK_DATA_DIR": str(data)},
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Bash 4.2", result.stderr)
        self.assertFalse(data.exists())


if __name__ == "__main__":
    unittest.main()
