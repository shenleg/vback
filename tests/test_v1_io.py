"""隔离 S3 和恢复目录，验证上传校验、连接测试及归档安全。"""

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "vback.sh"


def load_functions(*names):
    """按名称提取生产函数并返回 Shell 代码；函数缺失时抛出异常。"""
    script = SCRIPT.read_text()
    functions = []
    for name in names:
        marker = f"{name}() {{"
        if marker not in script:
            raise AssertionError(f"生产函数不存在: {name}")
        start = script.index(marker)
        end = script.index("\n}\n", start) + 3
        functions.append(script[start:end])
    return "\n".join(functions)


class V1IOTests(unittest.TestCase):
    """通过真实归档工具和隔离的 S3 替身验证 I/O 行为。"""

    @classmethod
    def setUpClass(cls):
        """查找 Bash、tar 和 gzip；工具缺失或 Bash 版本过旧时跳过测试。"""
        cls.bash = shutil.which("bash")
        if not cls.bash or not shutil.which("tar") or not shutil.which("gzip"):
            raise unittest.SkipTest("需要 bash、tar 和 gzip")
        version = subprocess.run(
            [cls.bash, "-c", "printf '%s' ${BASH_VERSINFO[0]}"],
            capture_output=True, text=True, check=True,
        )
        if int(version.stdout) < 4:
            raise unittest.SkipTest("生产脚本需要 Bash 4 或更高版本")

    def setUp(self):
        """创建各测试专用的临时目录并注册清理；目录创建失败时抛出异常。"""
        directory = tempfile.TemporaryDirectory(prefix="vback-io-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = self.root / "calls"

    def run_shell(self, names, body, *args, stdin=None):
        """执行指定生产函数及测试脚本，返回进程结果；启动进程失败时抛出异常。"""
        setup = r'''
ROOT="$1"; shift
export ROOT
TMPDIR="$ROOT" TEMP_DIR="$ROOT/downloads"
mkdir -p "$TEMP_DIR"
S3_TOOL=aws S3_BUCKET=test-bucket BACKUP_PREFIX='backups/fail-error'
S3CMD_CFG="$ROOT/no-credentials" AWS_ENDPOINT=''
S3_UPLOAD_RETRIES=1 S3_UPLOAD_RETRY_DELAY=0 RUN_CONTEXT=scheduled
declare -A L
info() { :; }; warn() { printf 'WARN %s\n' "$*"; }
error() { printf 'ERROR %s\n' "$*"; }; success() { :; }
log_info() { printf 'INFO %s\n' "$*"; }; log_warn() { printf 'WARN %s\n' "$*"; }
log_error() { printf 'ERROR %s\n' "$*"; }; log_debug() { printf 'DEBUG %s\n' "$*"; }
aws() {
    printf 'aws %s\n' "$*" >> "$ROOT/calls"
    case "$1 $2" in
        's3api head-object') printf '"%s"\n' "$FAKE_ETAG" ;;
        's3api get-object')
            [[ "${FAKE_DOWNLOAD_FAIL:-0}" == 1 ]] && return 17
            local last="${!#}"
            cp "$ROOT/remote-sample" "$last" || return
            ;;
        's3 cp')
            if [[ "$3" == s3://* ]]; then
                cp "$ROOT/archive-source" "$4"
            else
                if [[ "${FAKE_UPLOAD_RC:-0}" != 0 ]]; then
                    printf 'upload stopped\n'
                    return "$FAKE_UPLOAD_RC"
                fi
                printf 'uploaded to error/fail path\n'
            fi
            ;;
        's3 rm') : ;;
        *) printf 'unexpected aws call\n' >&2; return 90 ;;
    esac
}
s3cmd() {
    printf 's3cmd %s\n' "$*" >> "$ROOT/calls"
    case " $* " in
        *' info '*) printf 'MD5 sum: %s\n' "$FAKE_ETAG" ;;
        *) printf 'unexpected s3cmd call\n' >&2; return 90 ;;
    esac
}
'''
        return subprocess.run(
            [self.bash, "-c", load_functions(*names) + "\n" + setup + "\n" + body,
             "vback-test", str(self.root), *(str(arg) for arg in args)],
            input=stdin, capture_output=True, text=True, check=False,
            env={**os.environ, "HOME": str(self.root), "AWS_CONFIG_FILE": str(self.root / "none"),
                 "AWS_SHARED_CREDENTIALS_FILE": str(self.root / "none")},
        )

    def calls(self):
        """读取假 S3 执行器记录并返回行列表；记录不存在时返回空列表。"""
        return self.log.read_text().splitlines() if self.log.exists() else []

    def archive(self, name, entries, compressed=False):
        """按指定类型和内容创建隔离归档并返回路径；归档写入失败时抛出异常。"""
        path = self.root / name
        with tarfile.open(path, "w:gz" if compressed else "w") as archive:
            for entry_name, kind, value in entries:
                item = tarfile.TarInfo(entry_name)
                if kind == "file":
                    data = value.encode()
                    item.size = len(data)
                    archive.addfile(item, io.BytesIO(data))
                else:
                    item.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                                 "fifo": tarfile.FIFOTYPE}[kind]
                    if kind != "fifo":
                        item.linkname = value
                    archive.addfile(item)
        return path

    def test_single_part_etag_match_and_mismatch(self):
        """普通 ETag 必须与本地 MD5 相同，且查询 key 只添加一次前缀。"""
        source = self.root / "data"
        source.write_bytes(b"single-part-content")
        md5 = hashlib.md5(source.read_bytes()).hexdigest()
        for etag, expected in ((md5, 0), ("0" * 32, 1)):
            with self.subTest(etag=etag):
                result = self.run_shell(
                    ("build_s3_path", "s3_verify_upload"),
                    'FAKE_ETAG="$2"; s3_verify_upload "$1" "app/archive.tar"', source, etag,
                )
                self.assertEqual(result.returncode == 0, expected == 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn("--key backups/fail-error/app/archive.tar", call)
            self.assertNotIn("backups/fail-error/backups/fail-error/", call)

    def test_multipart_aws_compares_downloaded_range_and_handles_errors(self):
        """分片 ETag 使用前 1 MiB 样本比较，并拒绝样本不一致或下载失败。"""
        source = self.root / "data"
        payload = b"A" * 1048576 + b"local suffix"
        source.write_bytes(payload)
        for sample, fail, expected in ((payload[:1048576], 0, True),
                                       (b"B" * 1048576, 0, False),
                                       (payload[:1048576], 1, False)):
            with self.subTest(fail=fail, matches=sample[:1] == b"A"):
                (self.root / "remote-sample").write_bytes(sample)
                self.log.write_text("")
                result = self.run_shell(
                    ("build_s3_path", "s3_verify_upload"),
                    'FAKE_ETAG="0123456789abcdef0123456789abcdef-2"; '
                    'FAKE_DOWNLOAD_FAIL="$2"; s3_verify_upload "$1" "app/data.tar"', source, fail,
                )
                self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)
                calls = self.calls()
                ranges = [call for call in calls if "s3api get-object" in call]
                self.assertEqual(len(ranges), 1, calls)
                self.assertIn("--range bytes=0-1048575", ranges[0])
                self.assertIn("--key backups/fail-error/app/data.tar", ranges[0])
                self.assertNotIn("backups/fail-error/backups/fail-error/", ranges[0])

    def test_multipart_s3cmd_explicitly_skips_verification(self):
        """s3cmd 遇到分片 ETag 时记录跳过校验，不将其误认为 MD5 不匹配。"""
        source = self.root / "data"
        source.write_bytes(b"data")
        result = self.run_shell(
            ("build_s3_path", "s3_verify_upload"),
            'S3_TOOL=s3cmd; FAKE_ETAG="abcdef-2"; s3_verify_upload "$1" "app/data.tar"', source,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout.lower(), r"skip")
        self.assertIn("s3://test-bucket/backups/fail-error/app/data.tar", "\n".join(self.calls()))

    def test_connection_test_uses_upload_status_and_same_unique_prefixed_key(self):
        """连接测试以上传状态判断结果，并删除两次测试各自上传的唯一对象。"""
        names = ("build_s3_path", "should_show_upload_progress", "s3_put", "s3_rm", "s3_test")
        for rc, expected in ((17, False), (0, True), (0, True)):
            with self.subTest(rc=rc, previous_calls=len(self.calls())):
                before = len(self.calls())
                result = self.run_shell(names, 'FAKE_UPLOAD_RC="$1"; s3_test', rc)
                self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)
                calls = self.calls()[before:]
                uploads = [line for line in calls if line.startswith("aws s3 cp ")]
                deletes = [line for line in calls if line.startswith("aws s3 rm ")]
                self.assertEqual(len(uploads), 1, calls)
                if expected:
                    self.assertEqual(len(deletes), 1, calls)
                    upload_key = next(word for word in uploads[0].split() if word.startswith("s3://"))
                    delete_key = next(word for word in deletes[0].split() if word.startswith("s3://"))
                    self.assertEqual(upload_key, delete_key)
                    self.assertTrue(upload_key.startswith("s3://test-bucket/backups/fail-error/"))
        successful_uploads = [line for line in self.calls() if line.startswith("aws s3 cp ")][-2:]
        keys = [next(word for word in line.split() if word.startswith("s3://")) for line in successful_uploads]
        self.assertNotEqual(keys[0], keys[1])

    def test_connection_test_calls_s3_put_and_uses_its_status(self):
        """连接测试必须调用 s3_put，并只根据它的状态判断上传是否成功。"""
        for rc, expected in ((23, False), (0, True)):
            with self.subTest(rc=rc):
                marker = self.root / "put-called"
                if marker.exists():
                    marker.unlink()
                result = self.run_shell(("build_s3_path", "s3_test"), r'''
s3_put() {
    printf '%s\n' "$2" > "$ROOT/put-called"
    if [[ "$1" == '' ]]; then return 99; fi
    if [[ "$FAKE_RC" != 0 ]]; then printf 'stopped\n'; return "$FAKE_RC"; fi
    printf 'successful error/fail path\n'
}
s3_rm() { :; }
FAKE_RC="$1"
s3_test
''', rc)
                self.assertTrue(marker.exists(), result.stdout + result.stderr)
                self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)

    def test_menu_restore_selects_second_backup(self):
        """菜单列出的第二条备份应传给 do_restore，而非丢失在管道子进程中。"""
        destination = self.root / "restore directory"
        result = self.run_shell(("menu_restore",), r'''
show_header() { :; }; press_enter() { :; }
prompt_task_selection() { printf task; }; resolve_default_task_id() { printf task; }
load_task_context() { :; }; validate_config() { :; }; check_s3_tool() { :; }
setup_s3_tool() { :; }; confirm() { return 0; }
s3_list() { printf '2026-01-01 12:00:00 100 first.tar\n2026-01-02 13:00:00 200 second.tar.gz\n'; }
do_restore() { printf '%s\n%s\n' "$1" "$2" > "$ROOT/selected"; }
BACKUP_DIRS=("$ROOT/app")
menu_restore
''', stdin=f"2\n{destination}\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "selected").exists(), result.stdout + result.stderr)
        self.assertEqual((self.root / "selected").read_text().splitlines(), ["app/second.tar.gz", str(destination)])

    def test_validate_archive_accepts_real_tar_and_gzip(self):
        """真实 tar 与 tar.gz 归档都能通过对应的验证模式。"""
        source = self.root / "input"
        source.mkdir()
        (source / "file.txt").write_text("restore data")
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                path = self.root / ("valid.tar.gz" if compressed else "valid.tar")
                subprocess.run(["tar", "-czf" if compressed else "-cf", str(path), "-C", str(source), "."], check=True)
                result = self.run_shell(("validate_restore_archive",),
                                        'validate_restore_archive "$1" "$2"', path, str(compressed).lower())
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validate_archive_rejects_unsafe_members_and_corruption(self):
        """拒绝损坏归档、绝对路径、目录穿越、链接和 FIFO。"""
        entries = (
            ("absolute", str(self.root / "outside"), "file", "bad"),
            ("traversal", "../outside", "file", "bad"),
            ("symlink", "link", "symlink", "../outside"),
            ("hardlink", "link", "hardlink", "target"),
            ("fifo", "pipe", "fifo", ""),
        )
        for compressed in (False, True):
            for label, name, kind, value in entries:
                with self.subTest(compressed=compressed, entry=label):
                    path = self.archive("unsafe.tar.gz" if compressed else "unsafe.tar", [(name, kind, value)], compressed)
                    result = self.run_shell(("validate_restore_archive",),
                                            'validate_restore_archive "$1" "$2"', path, str(compressed).lower())
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            path = self.root / ("broken.tar.gz" if compressed else "broken.tar")
            path.write_bytes(b"not a tar archive\x00\xff")
            result = self.run_shell(("validate_restore_archive",),
                                    'validate_restore_archive "$1" "$2"', path, str(compressed).lower())
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_do_restore_downloads_validates_and_extracts_real_archives(self):
        """从假 S3 下载的真实 tar 和 tar.gz 必须先验证，再成功恢复内容。"""
        source = self.root / "input"
        source.mkdir()
        (source / "file.txt").write_text("restored")
        names = ("build_s3_path", "validate_restore_archive", "do_restore")
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                archive_name = "backup.tar.gz" if compressed else "backup.tar"
                subprocess.run(["tar", "-czf" if compressed else "-cf", str(self.root / "archive-source"),
                                "-C", str(source), "."], check=True)
                destination = self.root / ("dest-gz" if compressed else "dest-tar")
                result = self.run_shell(names, r'''
# 用包装器记录验证发生在提取之前，实际校验仍由生产函数完成。
eval "$(declare -f validate_restore_archive | command sed '1s/validate_restore_archive/real_validate_restore_archive/')"
validate_restore_archive() { printf 'validate\n' >> "$ROOT/order"; real_validate_restore_archive "$@"; }
tar() { [[ "$1" == -x* ]] && printf 'extract\n' >> "$ROOT/order"; command tar "$@"; }
do_restore "$1" "$2"
''', "app/" + archive_name, destination)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((destination / "file.txt").read_text(), "restored")
                order = (self.root / "order").read_text().splitlines()
                self.assertLess(order.index("validate"), order.index("extract"))
                (self.root / "order").write_text("")

    def test_do_restore_rejects_traversal_before_extracting(self):
        """恢复恶意归档时必须先拒绝穿越路径，且不能调用提取或写入目标目录外。"""
        self.archive("archive-source", [("../escaped", "file", "unsafe")])
        destination = self.root / "destination"
        result = self.run_shell(("build_s3_path", "validate_restore_archive", "do_restore"), r'''
tar() { [[ "$1" == -x* ]] && printf 'extract\n' >> "$ROOT/order"; command tar "$@"; }
do_restore "$1" "$2"
''', "app/bad.tar", destination)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "escaped").exists())
        self.assertFalse((self.root / "order").exists(), "拒绝归档之前不应尝试提取")

    def test_s3_put_retries_and_preserves_final_status(self):
        """上传按次数重试，成功后停止；全部失败时保留最终非零状态。"""
        (self.root / "input").write_text("upload-content")
        for retries, succeeds, expected in ((3, True, True), (2, False, False)):
            with self.subTest(retries=retries, succeeds=succeeds):
                counter = self.root / "retry-count"
                counter.write_text("")
                result = self.run_shell(("build_s3_path", "should_show_upload_progress", "s3_put"), r'''
aws() {
    printf 'attempt\n' >> "$ROOT/retry-count"
    if [[ "$SUCCEEDS" == true && "$(wc -l < "$ROOT/retry-count")" -ge 3 ]]; then return 0; fi
    return 23
}
sleep() { :; }
S3_UPLOAD_RETRIES="$1"; SUCCEEDS="$2"
s3_put "$ROOT/input" app/data.tar false
''', retries, str(succeeds).lower())
                self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)
                self.assertEqual(len(counter.read_text().splitlines()), retries)

    def test_connection_cleanup_failure_is_visible(self):
        """测试对象无法删除时连接测试必须报告失败，而非显示成功。"""
        result = self.run_shell(("s3_test",), r'''
s3_put() { return 0; }
s3_rm() { return 23; }
s3_test
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot remove test object", result.stdout)

    def test_reset_refuses_system_home_and_symlink_aliases(self):
        """重置目录检查拒绝根目录、主目录及其符号链接，不执行实际删除。"""
        child = self.root / "owned-data"
        child.mkdir()
        alias = self.root / "root-alias"
        alias.symlink_to("/", target_is_directory=True)
        for path, expected in ((Path("/"), False), (self.root, False), (alias, False), (child, True)):
            with self.subTest(path=path):
                result = self.run_shell(("validate_reset_data_dir",),
                                        'DATA_DIR="$1"; validate_reset_data_dir', path)
                self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)
                self.assertTrue(child.exists())

    def test_update_keeps_original_on_download_or_syntax_failure(self):
        """下载返回非零或脚本语法错误时，只保留旧脚本而不覆盖它。"""
        original = self.root / "vback-copy.sh"
        content = "#!/bin/bash\nprintf 'original\\n'\n"
        for mode in ("download-failed", "syntax-error"):
            with self.subTest(mode=mode):
                original.write_text(content)
                result = self.run_shell(("compare_versions", "do_update"), r'''
show_kv() { :; }; confirm() { return 0; }
get_remote_version() { printf 1.4.2; }
curl() {
    if [[ "$MODE" == download-failed ]]; then printf 'partial'; printf 'network rejected\n' >&2; return 22; fi
    printf '#!/bin/bash\nif\n'
}
VERSION=1.4.0 SCRIPT_PATH="$1" RAW_SCRIPT_URL=https://storage.invalid/script
MODE="$2"
do_update
''', original, mode)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(original.read_text(), content)
                self.assertTrue(list(self.root.glob("vback-copy.sh.backup.*")))
                if mode == "download-failed":
                    self.assertIn("network rejected", result.stdout)
                    self.assertIn("exit 22", result.stdout)

    def test_validate_config_rejects_duplicate_source_basenames(self):
        """不同父目录下的同名来源应被拒绝，不同名称则允许。"""
        paths = [self.root / "one" / "app", self.root / "two" / "app", self.root / "two" / "other"]
        for path in paths:
            path.mkdir(parents=True)
        for second, expected in ((paths[1], False), (paths[2], True)):
            with self.subTest(second=second):
                result = self.run_shell(("validate_config",), r'''
S3_ACCESS_KEY=test S3_SECRET_KEY=test S3_BUCKET=test CURRENT_TASK_ID=task
BACKUP_DIRS=("$1" "$2")
validate_config task
''', paths[0], second)
                self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
