<!-- 本文介绍 v1 Bash 脚本的安装、配置、备份、恢复及回归测试。 -->
<div align="center">

```
   ★       ★
██╗   ██╗██████╗  █████╗  ██████╗██╗  ██╗
██║   ██║██╔══██╗██╔══██╗██╔════╝██║ ██╔╝
██║   ██║██████╔╝███████║██║     █████╔╝
╚██╗ ██╔╝██╔══██╗██╔══██║██║     ██╔═██╗
 ╚████╔╝ ██████╔╝██║  ██║╚██████╗██║  ██╗
  ╚═══╝  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝
   ★       ★
```

### 更方便，更省心

**一款上手即用的服务器数据备份脚本**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Bash](https://img.shields.io/badge/Bash-4.2+-green.svg)](https://www.gnu.org/software/bash/)
[![GitHub stars](https://img.shields.io/github/stars/caigg188/vback?style=social)](https://github.com/caigg188/vback)

当前仓库：[shenleg/vback](https://github.com/shenleg/vback) · 上游：[caigg188/vback](https://github.com/caigg188/vback)

</div>

---

### 这是什么

`vback` v1 是一个单文件 Bash 备份脚本，用来把服务器上的目录打包后上传到 S3 兼容云存储。本分支维护 v1.4.2，继续使用 tar 全量备份和系统 cron。

它的目标一直很简单：

- 下载就能用
- 菜单式配置，不折腾
- 适合小中型项目做稳定的全量备份

#### 适合谁

- 需要把服务器目录定期备份到 S3 兼容存储的人
- 希望单脚本部署，不想引入复杂备份系统的人
- 需要在同一台机器上管理多套不同备份策略的人
- 需要为 SQLite 数据库做安全备份的人

#### 不适合谁

- 需要增量备份的人（目前仅支持全量备份）
- 需要备份编排和自动恢复的人（提供交互式恢复，不负责自动恢复编排）
- 需要数据库专用备份系统的人（如 MySQL/PostgreSQL 专业备份）
- 需要备份 TB 级数据的人（更适合中小规模目录）

### v1.4.2 修复

本分支移植上游 v1.4.1 / v1.4.2 的脚本修复，并保留任务创建、配置修改生效和 SQLite 排除规则的本地修复：

- **配置保存**：目录和排除规则子菜单支持手动保存，返回时自动保存；备份选项修改后立即保存；新任务创建后进入编辑菜单。
- **配置读取**：按字段白名单读取 `config`、`tasks`、`schedules`，不执行 Shell 内容；支持脚本原有的 `%q` 转义。文件必须属于运行用户且权限为 `600`，格式或权限错误时停止运行。
- **上传与连接测试**：修复 multipart ETag 被当作普通 MD5 的误报；连接测试按命令退出码判断，测试对象使用唯一名称并在相同 Prefix 下清理。
- **来源检查**：同一任务不允许两个来源使用相同 basename，避免归档和远端对象覆盖；厂商默认 Endpoint 会替换 Region 占位符。
- **SQLite 快照**：修复 `.backup` 调用，确保包含活动 WAL 中已提交的数据；排除规则同样适用于数据库，快照失败时停止打包上传，不退回普通文件复制。
- **恢复与重置**：修复恢复菜单丢失备份列表；解压前拒绝危险路径、链接和特殊文件；重置拒绝系统目录和用户主目录，且不清理其他进程的锁。
- **更新来源**：自动更新使用当前 fork，避免覆盖本分支的修复；下载和脚本语法错误会明确报告。
- **运行要求**：需要 Bash 4.2+，旧 Bash 在读取配置前报错退出；新生成的 cron 命令使用当前 Bash 的绝对路径。

### v1.4.0 新特性

在 v1.3.x 基础上，v1.4.0 重点强化了安全性、可靠性和跨平台兼容：

- **安全加固**：使用 `mktemp` 替代可预测的 PID 临时路径，移除 `eval echo` 命令注入风险，数据文件加载前进行安全校验，SQLite 备份路径安全引用，锁文件使用 `mkdir` 原子操作避免竞态条件
- **上传校验**：普通 ETag 与本地 MD5 比较；multipart 对象的校验方式见下方 FAQ
- **上传重试**：S3 上传失败自动重试（默认 3 次，间隔 5 秒），应对网络瞬断
- **恢复命令**：新增 `restore` 命令，支持交互式选择远端备份并下载解压到指定目录
- **macOS 兼容**：修复 `du -sb`、`stat -c`、`/proc`、`readlink -f`、`cp -a` 在 macOS 上的兼容问题
- **可靠性提升**：启用 `pipefail` 防止管道错误被吞掉，tar 部分失败时显示具体错误，rsync 不可用时发出明确警告，版本号比较支持非数字后缀（如 `-beta`）
- **代码优化**：提取 `setup_s3_tool()`、`get_file_size()`、`resolve_default_task_id()` 工具函数消除重复代码，配置文件写入使用 `printf %q` 安全引用

### v1.3.x 新特性

- **备份任务**：一个任务可包含多个待备份目录，并独立设置 `Prefix`、压缩、SQLite 安全备份、保留数量、排除规则。
- **多定时任务**：可以给不同备份任务分别配置不同的 cron 时间，例如同一天多次备份不同目录。
- **手动备份进度**：在交互终端执行备份时，会显示实时上传进度与速度；cron / 重定向日志场景默认静默。
- **兼容旧版本数据**：旧版 `~/.vback/config` 会自动映射成默认备份任务，原日志目录和原有配置可继续使用。

### 功能概览

- **单文件部署**：适合直接扔到服务器上使用
- **多任务备份**：一台机器可管理多套目录策略
- **多计划调度**：不同任务可不同频率执行
- **SQLite 安全备份**：减少活跃数据库文件直接拷贝的风险
- **S3 兼容多云**：缤纷云 S4 / Cloudflare R2 / AWS S3 / 阿里云 OSS / 七牛云 / Google Cloud / 自定义 S3
- **上传校验与重试**：普通 ETag 的 MD5 校验、AWS multipart 抽样校验和上传失败重试
- **恢复功能**：交互式选择远端备份并下载解压
- **双语界面**：中文 / English

### 截图

<details>
<summary>点击展开</summary>

<br>

<img src="imgs/ScreenShot_001.png" width="600" alt="主界面">

<br><br>

<img src="imgs/ScreenShot_003.png" width="600" alt="配置向导">

<br><br>

<img src="imgs/ScreenShot_004.png" width="600" alt="备份过程">

</details>

### 安装与依赖

#### 系统要求

- Linux / macOS
- Bash 4.2+
- 必需：`tar`、`gzip`
- 推荐：`curl`、`awk`、`crontab`
- 上传工具：`s3cmd` 或 `aws-cli`（至少需要一个）
- 推荐增强：`rsync`、`md5sum`/`md5`
- SQLite 安全备份必需：`sqlite3`（默认开启，缺少时数据库快照失败会停止备份）

#### 依赖检查

```bash
bash --version
tar --version
gzip --version
s3cmd --version   # 或 aws --version
```

#### 安装方式

方式一：本地运行（推荐快速体验）

```bash
curl -fsSL https://raw.githubusercontent.com/shenleg/vback/main/vback.sh -o vback.sh \
  && chmod +x vback.sh \
  && ./vback.sh
```

方式二：全局安装（推荐生产使用）

```bash
curl -fsSL https://raw.githubusercontent.com/shenleg/vback/main/vback.sh -o /usr/local/bin/vback \
  && chmod +x /usr/local/bin/vback \
  && vback

# 卸载
rm /usr/local/bin/vback
```

第一次运行会自动进入配置向导。

### 典型工作流

```bash
# 1. 首次配置
./vback.sh setup

# 2. 测试连接
./vback.sh test

# 3. 手动备份一次
./vback.sh backup --task web

# 4. 配置定时任务
./vback.sh install-cron --task web --cron "0 3 * * *"

# 5. 查看远端备份
./vback.sh status --task web

# 6. 恢复备份
./vback.sh restore --task web
```

### 快速开始

首次使用建议：

```bash
# 安装依赖
sudo apt install -y rsync sqlite3

# 下载脚本
curl -fsSL https://raw.githubusercontent.com/shenleg/vback/main/vback.sh -o /usr/local/bin/vback \
  && chmod +x /usr/local/bin/vback \
  && vback

# 运行配置向导
vback.sh setup

# 测试连接
vback.sh test

# 执行备份
vback.sh backup
```

### 核心概念

#### 1. 全局云配置

这一层配置的是 S3 连接信息：

- 云厂商
- Access Key / Secret Key
- Endpoint
- Bucket
- Region

#### 2. 备份任务

一个"备份任务"对应一组独立的备份策略，包含：

- 多个本地目录
- 一个云端目录前缀 `Prefix`
- 压缩开关与压缩级别
- SQLite 安全备份开关
- 备份保留数量
- 排除规则

可以理解为：旧版本里那一套"备份目录 + Prefix + 压缩设置"，在 `v1.3.x` 里被正式抽象成了一个任务。

#### 3. 定时任务

定时任务现在和备份任务解耦：

- 先创建备份任务
- 再给某个备份任务配置一个或多个 cron 表达式

这样就可以实现：

- 任务 A 每天凌晨备份
- 任务 B 每 6 小时备份一次
- 同一个任务一天内跑多次

### 使用方式

#### 交互模式

```bash
./vback.sh
```

推荐直接用菜单：

- `立即备份`
- `定时备份`
- `编辑配置 -> 备份任务`
- `编辑配置 -> S3 设置`

#### 排除规则（文件与文件夹）

在备份任务的「排除规则」菜单中添加或删除规则。选择「保存」或返回上一级时都会保存；每次添加一条规则，不需要输入引号。

规则使用相对于**待备份目录**的路径，不是系统绝对路径，也不是相对于脚本所在目录。例如，待备份目录为 `/data/app`：

| 想排除的内容 | 填写规则 |
|---|---|
| 所有层级中名为 `cache` 的文件夹 | `cache/` |
| 仅 `/data/app/cache` 文件夹 | `/cache/` |
| 仅 `/data/app/storage/cache` 文件夹 | `/storage/cache/` |
| 所有层级中的日志文件 | `*.log` |

规则说明：

- **末尾的 `/`** 表示只匹配文件夹，该文件夹及其内容都不备份；不加末尾的 `/` 时，同名文件和文件夹都会匹配。
- **开头的 `/`** 表示从待备份目录开始匹配，不代表系统根目录。因此，排除上例中的 `/data/app/cache` 应填写 `/cache/`，不要填写 `/data/app/cache/`。
- 一个任务包含多个待备份目录时，同一组规则会分别应用于每个目录。
- 开启 SQLite 安全备份后，排除规则同样适用于 `.db`、`.sqlite` 文件；被排除目录中的数据库不会重新加入备份。
- 推荐安装 `rsync`。未安装时使用 `tar` 复制，支持 `*.log`、`node_modules` 等不含 `/` 的名称规则；数据库只对筛选后保留的文件生成快照。含 `/` 的路径和目录规则需要 `rsync`，缺失时会明确报错并停止备份，不会忽略规则。

默认有 8 条规则：`*.log`、`*.tmp`、`node_modules`、`.git`、`__pycache__`、`*.pyc`、`.DS_Store`、`Thumbs.db`。可在「排除规则」菜单中恢复默认。

#### 命令行模式

```bash
# 打开菜单
./vback.sh

# 立即备份默认任务
./vback.sh backup

# 立即备份指定任务
./vback.sh backup --task web
./vback.sh backup --task-id task_web

# 查看指定任务的云端备份
./vback.sh status --task web

# 测试连接
./vback.sh test

# 同步已配置的所有定时任务到 crontab
./vback.sh install-cron

# 直接创建一个定时任务并同步
./vback.sh install-cron --task web --cron "0 */6 * * *" --schedule-name "web-6h"

# 移除当前安装到 crontab 的 vback 定时任务
./vback.sh remove-cron

# 查看当前配置
./vback.sh config

# 恢复备份（交互式选择）
./vback.sh restore --task web

# 重新进入配置向导
./vback.sh setup
```

#### 常用参数

```bash
# 详细输出
./vback.sh -v backup

# 指定配置目录中的 config 文件
./vback.sh -c /path/to/config backup

# 指定语言
./vback.sh --lang zh
./vback.sh --lang en

# 计划任务内部使用，通常不需要手动调用
./vback.sh backup --task web --scheduled
```

#### 上传重试配置

通过环境变量控制上传重试行为：

```bash
# 设置最大重试次数（默认 3）
S3_UPLOAD_RETRIES=5 ./vback.sh backup

# 设置重试间隔秒数（默认 5）
S3_UPLOAD_RETRY_DELAY=10 ./vback.sh backup
```

### 定时任务示例

```bash
# 每天 03:00
0 3 * * *

# 每 6 小时
0 */6 * * *

# 每天 09:30 / 14:30 / 21:30
30 9,14,21 * * *

# 每周日 02:00
0 2 * * 0
```

### 目录结构

`v1.3.x` 起，`~/.vback/` 目录通常如下：

```text
~/.vback/
├── config          # 全局配置 + 旧版兼容镜像字段
├── tasks           # 备份任务定义
├── schedules       # 定时任务定义
├── language        # 语言设置
└── logs/
    └── vback.log   # 运行日志
```

### 兼容旧版本

升级到 `v1.3.x` 时：

- 旧版 `config` 会自动迁移成一个默认备份任务
- 旧日志目录 `~/.vback/logs/` 不会被破坏
- 旧的 `backup / install-cron / remove-cron` 命令仍然可以继续使用
- 旧 cron 行在脚本更新后仍可继续执行；当你重新同步定时任务时，会切换到新的多任务模型

v1.4.2 继续读取原来的配置和日志，但不再执行配置中的 Shell 命令。支持脚本生成的 `%q` 格式及旧式引号字面量，不支持自定义 Shell 语句；错误会注明文件和行号。`config`、`tasks`、`schedules` 必须属于运行用户，权限为 `600`，可对实际存在的文件执行 `chmod 600` 修正。

### 恢复方式

`v1.4.0` 起支持交互式恢复：

```bash
# 交互式选择远端备份并恢复
./vback.sh restore --task web
```

也可手动恢复（下载对应归档并解压）：

```bash
# s3cmd 示例
s3cmd get s3://your-bucket/your-prefix/project_20260308_030000.tar.gz

# 解压
tar -xzf project_20260308_030000.tar.gz
```

**注意事项**：

- 备份包内目录结构与源目录 basename 对应
- 如果启用了 `Prefix`，需要从对应云端前缀下下载
- 恢复前建议先在临时目录解压检查
- 定期验证备份文件可下载、可解压
- 交互恢复拒绝绝对路径、`..` 路径、软链接、硬链接及设备/FIFO 等特殊文件；含这些项目的旧备份需要手工检查后选择其他恢复方式

### 常见问题

#### 1. 手动备份为什么有进度，cron 里没有？

这是设计行为：

- 手动交互终端：显示实时上传进度和速度
- cron / 重定向日志：默认关闭进度，避免日志被刷满

#### 2. 可以同时有多个定时任务吗？

可以。`v1.3.x` 已支持给同一个备份任务配置多个计划，也支持不同任务分别配置不同计划。

#### 3. 旧配置升级会不会丢？

不会。旧配置会自动映射成一个默认备份任务，并继续保留兼容字段。

#### 4. 支持增量备份吗？

暂不支持。目前仍是全量备份，定位是简单、稳定、可维护。

#### 5. `s3cmd` 和 `aws-cli` 优先使用哪个？

两者都可以，`vback` 会优先检测 `s3cmd`，其次使用 `aws-cli`。如果都没安装，首次运行时会提示安装 `s3cmd`。

建议：
- 国内环境优先 `s3cmd`，兼容性更好
- 已有 AWS 配置的环境可直接用 `aws-cli`

#### 6. cron 执行时为什么环境变量与手动运行不一致？

cron 默认使用 `/bin/sh` 和最小环境变量，可能导致：
- 脚本找不到 `bash` 或其他命令
- `PATH` 不包含某些工具路径

解决方法：
- 确保脚本 shebang 为 `#!/bin/bash`
- 在 cron 任务中指定完整路径：`/usr/local/bin/vback backup --scheduled`
- 或在 crontab 中设置 `PATH`：`PATH=/usr/local/bin:/usr/bin:/bin`

#### 7. 上传校验失败怎么办？

普通 ETag 会与本地文件 MD5 比较。multipart ETag 不是普通 MD5：使用 AWS CLI 时下载首个 1 MiB 抽样比较，样本下载失败或内容不同会判定失败；使用 `s3cmd` 时记录“跳过内容校验”，不会把分片 ETag 误判为损坏。抽样及跳过都不代表全文件校验。

校验失败时建议：
- 检查网络稳定性
- 增加重试次数：`S3_UPLOAD_RETRIES=5`
- 重新执行备份

#### 8. macOS 上有什么已知问题？

macOS 自带 `/bin/bash` 3.2，不满足要求。先运行 `brew install bash`，再使用新版 Bash 启动，例如 Apple Silicon 上：

```bash
/opt/homebrew/bin/bash ./vback.sh
```

定时任务应重新同步一次，新生成的命令会使用当前新版 Bash 的绝对路径，不依赖 cron 的 `PATH`。

### 限制与注意事项

#### 项目边界

- 目前仅支持全量备份，不支持增量备份
- 恢复功能为交互式下载解压，不提供自动恢复编排
- 不替代数据库专用备份系统（如 MySQL/PostgreSQL 专业工具）
- 适合小中型目录型备份场景，不建议用于 TB 级数据

#### 安全建议

- 不要把 `~/.vback/` 目录暴露给其他用户（已设置 700 权限）
- 建议给对象存储配置最小权限策略
- 首次启用前先手动执行一次 `backup` 和 `test`
- 定期验证备份可下载、可解压
- 定期检查日志文件 `~/.vback/logs/vback.log`
- v1.4.0 起临时文件使用 `mktemp` 生成不可预测路径，数据文件加载前进行安全校验

#### 性能建议

- 单个备份目录建议不超过 50GB
- 文件数量过多时，排除不必要的文件（如日志、缓存）
- 压缩级别 6 已能在速度和压缩率间取得较好平衡
- 建议在业务低峰期执行定时备份
- 安装 `rsync` 以支持排除规则和 SQLite 安全备份

### 系统要求

- Linux / macOS
- Bash 4.2+
- 必需：`tar`、`gzip`
- 推荐：`curl`、`awk`、`crontab`
- 推荐：`rsync`、`md5sum`/`md5`
- SQLite 安全备份需要 `sqlite3`
- 上传工具：`s3cmd` 或 `aws-cli`

### 回归测试

本地测试使用 Python 标准库、Bash 4.2+、`tar`、`gzip`、`rsync` 和 `sqlite3`。S3 调用使用本地替身，不访问真实存储、凭证或系统 crontab；测试只创建并清理专用临时目录。

```bash
python3 -I -m unittest discover -s tests -p 'test_*.py' -v
bash -n vback.sh
shellcheck --severity=error vback.sh
```

macOS 请先把新版 Bash 所在目录加入 `PATH`，例如 `export PATH="/opt/homebrew/bin:$PATH"`。

### 许可证

MIT
