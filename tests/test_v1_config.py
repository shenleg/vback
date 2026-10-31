"""使用隔离目录验证 v1 配置文件的保存、解析和错误处理。"""

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "vback.sh"
FUNCTIONS = (
    "init_data_dir", "array_literal", "copy_array_var", "set_array_var",
    "task_var_name", "schedule_var_name", "task_exists", "task_get_scalar",
    "task_set_scalar", "task_get_array", "task_set_array", "schedule_get_scalar",
    "schedule_set_scalar", "default_task_name", "default_schedule_name",
    "create_schedule", "load_task_context", "save_current_task_context",
    "data_file_error", "validate_data_file", "decode_data_word", "load_data_file", "load_tasks_store",
    "load_schedules_store", "save_tasks_store", "save_schedules_store",
    "migrate_legacy_config", "ensure_task_store", "load_config", "save_config",
    "resolve_default_task_id", "sanitize_identifier", "generate_task_id", "create_task",
    "task_display_name", "task_dir_count", "task_prefix_display", "task_schedule_count",
    "edit_task_menu", "menu_tasks", "edit_backup_dirs", "edit_backup_options",
    "edit_exclude_patterns", "setup_wizard", "build_cron_command",
)


def shell_functions():
    """提取实际生产函数并返回 Shell 代码，缺少函数时抛出异常。"""
    script = SCRIPT.read_text()
    result = []
    for name in FUNCTIONS:
        start = script.index(f"{name}() {{")
        end = script.index("\n}\n", start) + 3
        result.append(script[start:end])
    return "\n".join(result)


class ConfigTests(unittest.TestCase):
    """覆盖真实配置读写函数及不可信文件的拒绝行为。"""

    @classmethod
    def setUpClass(cls):
        """读取待测试函数；脚本格式不符时抛出异常。"""
        cls.functions = shell_functions()

    def setUp(self):
        """创建隔离目录，避免访问真实用户配置。"""
        directory = tempfile.TemporaryDirectory(prefix="vback-config-")
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)

    def write_data(self, name, content, mode=0o600):
        """写入指定名称和权限的测试配置，返回路径；磁盘错误时抛出异常。"""
        path = self.data / name
        path.write_text(content)
        path.chmod(mode)
        return path

    def run_shell(self, body, *args, success=True):
        """在隔离配置目录执行函数和给定脚本，返回结果；状态不符时测试失败。"""
        setup = r'''
DATA_DIR="$1"; shift
CONFIG_FILE="$DATA_DIR/config"; TASKS_FILE="$DATA_DIR/tasks"
SCHEDULES_FILE="$DATA_DIR/schedules"; LOG_DIR="$DATA_DIR/logs"
log_error() { printf '%s\n' "$1" >&2; }
DEFAULT_EXCLUDE_PATTERNS=('*.log' '.git')
CLOUD_PROVIDER='' S3_ACCESS_KEY='' S3_SECRET_KEY='' S3_ENDPOINT='' S3_BUCKET='' S3_REGION=''
BACKUP_DIRS=() BACKUP_PREFIX='' MAX_BACKUPS=7 COMPRESS_BACKUP=true
COMPRESSION_LEVEL=6 SQLITE_SAFE_BACKUP=true SCHEDULE_CRON='0 3 * * *'
EXCLUDE_PATTERNS=("${DEFAULT_EXCLUDE_PATTERNS[@]}")
TASK_IDS=() SCHEDULE_IDS=() ACTIVE_TASK_ID='' DEFAULT_TASK_ID='' CURRENT_TASK_ID=''
CURRENT_LANG=en
'''
        result = subprocess.run(
            ["bash", "-c", self.functions + "\n" + setup + "\n" + body,
             "vback-test", str(self.data), *args],
            capture_output=True, text=True, check=False,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_save_load_round_trip(self):
        """多任务和多计划保存后可读回空串、引号、反斜杠、换行及字面命令替换。"""
        special = f"中文 space 'single' \"double\" \\ $ ` ; $(touch {self.data / 'never'})\nnext\x01end"
        result = self.run_shell(r'''
value="$1"
CLOUD_PROVIDER="$value"; S3_ACCESS_KEY="$value"; S3_SECRET_KEY=''; S3_ENDPOINT='trailing '
TASK_IDS=(task_a task_b); ACTIVE_TASK_ID=task_a; DEFAULT_TASK_ID=task_a
for id in "${TASK_IDS[@]}"; do
    task_set_scalar "$id" NAME "$value"
    task_set_scalar "$id" PREFIX "$value"
    task_set_scalar "$id" MAX_BACKUPS 7
    task_set_scalar "$id" COMPRESS true
    task_set_scalar "$id" COMPRESSION_LEVEL 6
    task_set_scalar "$id" SQLITE_SAFE true
    task_set_array "$id" DIRS "$value" '' "line
break"
    task_set_array "$id" EXCLUDES "$value" ''
done
SCHEDULE_IDS=(schedule_a schedule_b)
for id in "${SCHEDULE_IDS[@]}"; do
    schedule_set_scalar "$id" NAME "$value"
    schedule_set_scalar "$id" TASK task_a
    schedule_set_scalar "$id" CRON "$value"
done
save_config || exit 1
TASK_IDS=(); SCHEDULE_IDS=(); CLOUD_PROVIDER='wrong'; S3_ACCESS_KEY='wrong'
load_config || exit 2
[[ "$CLOUD_PROVIDER" == "$value" && "$S3_ACCESS_KEY" == "$value" && -z "$S3_SECRET_KEY" && "$S3_ENDPOINT" == 'trailing ' ]] || exit 3
[[ "${TASK_IDS[*]}" == 'task_a task_b' && "${SCHEDULE_IDS[*]}" == 'schedule_a schedule_b' ]] || exit 4
for id in "${TASK_IDS[@]}"; do
    [[ "$(task_get_scalar "$id" NAME)" == "$value" ]] || exit 5
    task_get_array "$id" DIRS dirs
    [[ ${#dirs[@]} == 3 && "${dirs[0]}" == "$value" && "${dirs[1]}" == '' && "${dirs[2]}" == $'line\nbreak' ]] || exit 6
    task_get_array "$id" EXCLUDES excludes
    [[ ${#excludes[@]} == 2 && "${excludes[0]}" == "$value" && "${excludes[1]}" == '' ]] || exit 7
done
for id in "${SCHEDULE_IDS[@]}"; do
    [[ "$(schedule_get_scalar "$id" NAME)" == "$value" && "$(schedule_get_scalar "$id" CRON)" == "$value" ]] || exit 8
done
''', special)
        self.assertEqual(result.stderr, "")
        self.assertFalse((self.data / "never").exists())

    def test_legacy_migration(self):
        """旧版单引号、双引号和空数组配置应迁移为任务及计划。"""
        self.write_data("config", """CLOUD_PROVIDER='中文'
BACKUP_DIRS=(
 '/data with space'
)
BACKUP_PREFIX="a\\\\b"
MAX_BACKUPS=3
COMPRESS_BACKUP=true
COMPRESSION_LEVEL=6
SQLITE_SAFE_BACKUP=true
EXCLUDE_PATTERNS=()
SCHEDULE_CRON='0 1 * * *'
""")
        self.run_shell(r'''
load_config || exit 1
[[ "$CLOUD_PROVIDER" == 中文 && "${TASK_IDS[*]}" == task_default && "${SCHEDULE_IDS[*]}" == schedule_default ]] || exit 2
[[ "$(task_get_scalar task_default PREFIX)" == 'a\b' ]] || exit 3
task_get_array task_default DIRS dirs
[[ ${#dirs[@]} == 1 && "${dirs[0]}" == '/data with space' ]] || exit 4
''')

    def test_unknown_fields_and_statements_rejected(self):
        """未知字段、命令和数组类型错误均拒绝并指出文件及原因。"""
        for content in ("UNKNOWN=1\n", "touch should_not_exist\n", "BACKUP_DIRS=not_an_array\n", "BACKUP_DIRS=(\nfoo\n"):
            with self.subTest(content=content):
                self.write_data("config", content)
                result = self.run_shell("load_config", success=False)
                self.assertIn(str(self.data / "config"), result.stderr)
                self.assertIn("Invalid data file", result.stderr)

    def test_arithmetic_and_boolean_fields_are_validated(self):
        """算术及开关字段必须在配置入口校验，不能把任意字面量交给 Shell 运算。"""
        for content in ("MAX_BACKUPS='a[1]'\n", "MAX_BACKUPS=-1\n", "MAX_BACKUPS=08\n",
                        "COMPRESSION_LEVEL=10\n", "SQLITE_SAFE_BACKUP=maybe\n"):
            with self.subTest(content=content):
                self.write_data("config", content)
                self.run_shell("load_config", success=False)

    def test_permissions_rejected(self):
        """配置、任务和计划文件权限不是 600 时加载失败。"""
        for name in ("config", "tasks", "schedules"):
            with self.subTest(name=name):
                path = self.write_data(name, "# test\n", 0o644)
                result = self.run_shell("load_config", success=False)
                self.assertIn(str(path), result.stderr)
                self.assertIn("permissions", result.stderr)
                path.chmod(0o600)

    def test_wrong_owner_rejected(self):
        """文件所有者不是当前用户时拒绝加载并指出原因。"""
        path = self.write_data("config", "# test\n")
        result = self.run_shell("id() { printf 99999999; }; load_config", success=False)
        self.assertIn(str(path), result.stderr)
        self.assertIn("owner", result.stderr)

    def test_command_substitution_never_executes(self):
        """字面命令替换作为数据保留，未引用命令替换必须拒绝。"""
        marker = self.data / "executed"
        self.write_data("config", f"S3_ACCESS_KEY='$(touch {marker})'\n")
        self.run_shell(r'''load_config || exit 1; [[ "$S3_ACCESS_KEY" == '$(touch '"$1"')' ]]''', str(marker))
        self.assertFalse(marker.exists())
        self.write_data("config", f"S3_ACCESS_KEY=$(touch {marker})\n")
        self.run_shell("load_config", success=False)
        self.assertFalse(marker.exists())

    def test_failure_stops_migration_and_following_loads(self):
        """任何已有文件加载失败后不能继续读取后续文件或迁移旧配置。"""
        self.write_data("config", "BACKUP_DIRS=(\n /legacy\n)\n")
        self.write_data("tasks", "TASK_IDS=(\n task_a\n)\nBAD=1\n")
        self.write_data("schedules", "SCHEDULE_IDS=(\n schedule_a\n)\n")
        self.run_shell(r'''
load_config && exit 1
[[ ${#SCHEDULE_IDS[@]} == 0 && "${TASK_IDS[*]}" == task_a ]] || exit 2
[[ ! -e "$DATA_DIR/unexpected" ]] || exit 3
''')
        self.write_data("tasks", "TASK_IDS=()\n")
        self.write_data("schedules", "BAD=1\n")
        self.run_shell(r'''
load_config && exit 1
[[ ${#TASK_IDS[@]} == 0 ]] || exit 2
''')

    def test_missing_files_are_first_start(self):
        """所有配置文件缺失时首次启动成功且不创建文件。"""
        self.run_shell('load_config')
        self.assertEqual(list(self.data.iterdir()), [])

    def test_save_failure_is_not_reported_as_success(self):
        """任一存储文件无法写入时，保存配置必须返回失败。"""
        for name in ("config", "tasks", "schedules"):
            with self.subTest(name=name):
                self.run_shell(r'''
case "$1" in config) CONFIG_FILE="$DATA_DIR" ;; tasks) TASKS_FILE="$DATA_DIR" ;; schedules) SCHEDULES_FILE="$DATA_DIR" ;; esac
save_config
''', name, success=False)

    def test_new_task_is_registered_and_editable(self):
        """回归：菜单创建任务必须保留父 Shell 状态，并立即引导编辑新任务。"""
        self.run_shell(r'''
declare -A L
show_header() { :; }; show_kv() { :; }; print_line() { :; }; menu_item() { :; }
success() { :; }; press_enter() { :; }
input_field() { printf -v "$3" '%s' 'New Task'; }
edit_task_menu() { printf '%s' "$1" > "$DATA_DIR/edited"; }
menu_tasks <<<'a
0'
[[ "${TASK_IDS[*]}" == task_new_task && "$ACTIVE_TASK_ID" == task_new_task ]] || exit 1
[[ "$(<"$DATA_DIR/edited")" == task_new_task ]] || exit 2
load_config || exit 3
[[ "$(task_get_scalar task_new_task NAME)" == 'New Task' ]] || exit 4
''')

    def test_setup_creates_default_task_in_parent_shell(self):
        """回归：首次向导创建默认任务不能在命令替换的子 Shell 中丢失任务定义。"""
        self.run_shell(r'''
declare -A L
clear() { :; }; print_box_top() { :; }; print_box_line() { :; }; print_box_bottom() { :; }
init_providers() { :; }; select_provider() { :; }; show_kv() { :; }; success() { :; }
get_provider_name() { :; }; get_dir_size() { printf 0; }; fmt_size() { printf 0; }
input_field() { :; }; confirm() { [[ "$1" != test-now ]]; }
L[test_connection_now]=test-now
S3_ACCESS_KEY=key S3_SECRET_KEY=secret S3_BUCKET=bucket
setup_wizard <<<'/tmp
'
task_exists "$DEFAULT_TASK_ID" || exit 1
load_config || exit 2
[[ ${#TASK_IDS[@]} == 1 && "${BACKUP_DIRS[*]}" == /tmp ]] || exit 3
''')

    def test_editors_save_changes_without_parent_save(self):
        """目录、排除规则和选项编辑结束后，配置重读仍保留修改。"""
        self.run_shell(r'''
declare -A L
show_header() { :; }; show_kv() { :; }; print_line() { :; }; menu_item() { :; }
success() { :; }; warn() { :; }; press_enter() { :; }; get_dir_size() { printf 0; }
fmt_size() { printf 0; }
create_task Test task_test >/dev/null
load_task_context task_test
edit_backup_dirs <<<'a
/tmp
0'
BACKUP_DIRS=()
load_config || exit 1
[[ "${BACKUP_DIRS[*]}" == /tmp ]] || exit 2
edit_exclude_patterns <<<'a
cache/
0'
EXCLUDE_PATTERNS=()
load_config || exit 3
[[ "${EXCLUDE_PATTERNS[2]}" == cache/ ]] || exit 4
input_field() { case "$3" in BACKUP_PREFIX) BACKUP_PREFIX=new-prefix ;; MAX_BACKUPS) MAX_BACKUPS=9 ;; esac; }
confirm() { return 1; }
edit_backup_options
load_config || exit 5
[[ "$BACKUP_PREFIX" == new-prefix && "$MAX_BACKUPS" == 9 && "$COMPRESS_BACKUP" == false && "$SQLITE_SAFE_BACKUP" == false ]] || exit 6
''')

    def test_parent_menu_does_not_reload_unsaved_context(self):
        """回归：父菜单循环不能重新加载上下文，覆盖子编辑器的修改。"""
        self.run_shell(r'''
declare -A L
show_header() { :; }; show_kv() { :; }; print_line() { :; }; menu_item() { :; }
success() { :; }; press_enter() { :; }
create_task Test task_test >/dev/null
edit_backup_options() { BACKUP_PREFIX=changed; }
edit_task_menu task_test <<<'3
0'
load_config || exit 1
[[ "$BACKUP_PREFIX" == changed ]] || exit 2
''')

    def test_cron_uses_current_bash_and_quotes_paths(self):
        """新 cron 命令使用当前 Bash 并保护空格路径，不依赖旧 shebang。"""
        self.run_shell(r'''
SCRIPT_PATH='/tmp/vback script.sh'; LOG_FILE='/tmp/backup log'
command_text="$(build_cron_command task_test schedule_test)"
expected="$(printf '%q %q' "$BASH" "$SCRIPT_PATH")"
[[ "$command_text" == "$expected backup --task-id task_test --scheduled --schedule-id schedule_test "* ]] || exit 1
''')

    def test_invalid_identifier_rejected(self):
        """任务和计划标识符不能携带后续动态变量访问中的 Shell 语法。"""
        for name, key in (("tasks", "TASK_IDS"), ("schedules", "SCHEDULE_IDS")):
            with self.subTest(name=name):
                path = self.write_data(name, f"{key}=(\n 'bad id'\n)\n")
                result = self.run_shell("load_config", success=False)
                self.assertIn("invalid identifier", result.stderr)
                path.write_text(f"{key}=()\n")


if __name__ == "__main__":
    unittest.main()
