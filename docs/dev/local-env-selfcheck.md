# 本地环境自查清单

本文档收录 **TogoSpace 本地开发/测试环境里会导致「假红」（看起来是代码坏了，其实是环境问题）的 5 类陷阱**。

每一条都按同一结构写：**症状 → 为什么容易误判 → 正确做法 → 一行自查命令**。
目标：新人 **30 秒内**能判断「我现在遇到的是不是这个坑」。

---

## 0. 读本文档前，先记住两条纪律

### 纪律 1：环境相关结论必须标注「哪个环境」

**不许裸说「不可用」，必须说清是哪台机器、哪个环境。**

同一件事在不同环境下结论可能相反，本仓已经有现成案例：

| 事实 | 本机（macOS arm64，`/usr/local` 为 x86_64 brew 前缀） | CI（ubuntu-latest） |
|---|---|---|
| `pytest --timeout=` | ❌ 不可用，报 `unrecognized arguments` | ✅ 可用（`.github/workflows/test.yml:39` 装插件、`:47`/`:51` 使用） |
| `/usr/local/bin/timeout` | ❌ x86_64 二进制，套在 arm64 venv 前会跑成 x86_64 | ✅ 原生可用 |

所以正确写法是：

> ✅ 「**本机** arm64 + `/usr/local` 为 x86_64 brew 前缀的环境下，`timeout`/`gtimeout` 不可用；CI（ubuntu）不受影响。」
> ❌ 「`timeout` 不能用。」

### 纪律 2：本文档里每条命令都必须是**本机实测跑通**的

未经实测的操作建议不得写入——本轮就发生过「凭印象建议用 `gtimeout`」，导致团队成员各自记错一次口径的事故（详见第 2 条）。

**如果你要往本文档加条目，请把命令跑一遍，连同输出一起贴进来。**

---

## 1. xdist worker 命名：只有 `gw<N>` 才偏移端口

### 症状

用 `pytest -n auto` 或自定义 worker 跑全量时，出现以下之一：

```
MockLLM 启动失败: [Errno 48] Address already in use
```
```
POST 消息返回 400 / WebSocket 连接被拒
```
```
多个 worker 抢同一个 18080 / 19876 端口
```

### 为什么容易误判

`tests/base.py:_get_worker_offset()` 的实现是这样的：

```python
def _get_worker_offset() -> int:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id and worker_id.startswith("gw"):
        try:
            return int(worker_id[2:])
        except ValueError:
            return 0
    return 0
```

也就是说：**只有 `gw` 前缀 + 纯数字后缀，才会产生端口偏移**。其余情况全部 **静默返回 0**，不会报任何错。

本机实测（直接调用 `tests/base.py` 里的真函数，不启动服务）：

```
PYTEST_XDIST_WORKER →  offset  backend  mock_llm
gw0                 →    0     18080    19876
gw3                 →    3     18083    19879   ✅ 只有 gw<N> 偏移
lg1                 →    0     18080    19876   ❌ 落回基端口
az7                 →    0     18080    19876   ❌ 落回基端口
pm1                 →    0     18080    19876   ❌ 落回基端口
gwX（后缀非数字）     →    0     18080    19876   ❌ int() 抛 ValueError 被吞掉，静默回 0
None（非 xdist）      →    0     18080    19876
```

复现命令（可粘贴）：

```bash
.venv/bin/python3 -c "
import sys, os
sys.path.insert(0,'tests'); sys.path.insert(0,'src')
from base import _get_worker_offset, _get_backend_port, _get_mock_llm_port
for name in ['gw0','gw3','lg1','az7','pm1','gwX',None]:
    if name is None: os.environ.pop('PYTEST_XDIST_WORKER', None)
    else: os.environ['PYTEST_XDIST_WORKER'] = name
    print(f'{str(name):6s} -> offset={_get_worker_offset():2d} backend={_get_backend_port()} mock_llm={_get_mock_llm_port()}')
"
```

> **比 `lg1` 更阴的是 `gwX`**：它长得像合规命名（有 `gw` 前缀），但后缀不是数字，`int()` 抛的 `ValueError` 被 `except` 吞掉后**静默回退到 0**，同样抢基端口且**不报错**。

顺带说明：`_get_test_db_path()` 用的是**任意** `PYTEST_XDIST_WORKER`（不要求 `gw` 前缀），所以 `lg1` 这类 worker 的 **DB 是隔离的、端口却不隔离**——一半隔离一半不隔离，更容易让人误判成"偶发"。

### 正确做法

跑全量一律使用唯一 `gwNN`，且人工指定时避开已被他人占用的编号：

```bash
# ✅ 推荐：让 xdist 自己分配
#    xdist 内部 worker id 固定为 'gw0','gw1',...（xdist/plugin.py:375 文档字符串），天然合规
.venv/bin/python3 -m pytest tests/unit tests/api tests/integration -n auto --dist loadscope

# ✅ 手动指定时必须是 gw + 数字，且用别人当前没用的编号
PYTEST_XDIST_WORKER=gw17 .venv/bin/python3 -m pytest tests/...
```

```bash
# ❌ 以下都会抢 18080/19876
PYTEST_XDIST_WORKER=lg1 ...
PYTEST_XDIST_WORKER=az7 ...
PYTEST_XDIST_WORKER=pm1 ...
```

### 一行自查命令

```bash
echo "$PYTEST_XDIST_WORKER" | grep -Eq '^gw[0-9]+$' && echo "OK（会偏移端口）" || echo "危险：不偏移端口，会抢 18080/19876"
```

端口占用态另行确认：

```bash
lsof -nP -iTCP:18080 -iTCP:19876 -sTCP:LISTEN
```

---

## 2. `timeout` 与 `gtimeout` **都**是 x86_64 二进制

### 症状

命令被套上 `timeout`/`gtimeout` 之后，子进程里的 Python 报编译型包架构不兼容：

```
ImportError: dlopen(.../pydantic_core/_pydantic_core.cpython-311-darwin.so, 0x0002):
  ... (mach-o file, but is an incompatible architecture (have 'arm64', need 'x86_64'))
```

`ormsgpack`、`pydantic_core` 这类带 `.so` 的包都会中招。

### 为什么容易误判

**三层面都会把人带偏：**

**① 报错方向容易看反。** 请记住这句话（本轮最值钱的一条经验）：

> `incompatible architecture (have 'arm64', need 'x86_64')`
> —— **`have` 是 `.so` 的架构**（`arm64`，**正确**），**`need` 是父进程要的架构**（`x86_64`，**错误**）。
> **不是 `.so` 坏了，是父进程架构错了。**

看不懂这一点，就会去「重装依赖 / 重建 venv」，白花半小时以上。

**② 网上到处是「用 `gtimeout` 代替」的建议，但本机不成立。**

实测（本机 arm64）：

```bash
$ uname -m
arm64
$ file -b /usr/local/bin/timeout
Mach-O 64-bit executable x86_64
$ file -b /usr/local/bin/gtimeout
Mach-O 64-bit executable x86_64        ← 同样不可用！

$ gtimeout 60 .venv/bin/python3 -c "import pydantic_core"
ImportError: ... (have 'arm64', need 'x86_64')      ← 一样炸

$ arch -arm64 gtimeout 5 echo hi
arch: posix_spawnp: /usr/local/bin/gtimeout: Bad CPU type in executable
```

根因不是「挑哪个名字」，而是 **`/usr/local` 这一整套 brew 前缀本身是 x86_64**：
`/usr/local/opt/coreutils/bin/gtimeout` 也是 x86_64；本机 arm64 的 brew 前缀是 `/opt/homebrew`，但里面**没有**装 coreutils 的 timeout。

**③ `pytest --timeout=` 在本地也不可用**，但 CI 里可用——这是「同一条命令两个环境结论相反」的典型案例：

```bash
$ .venv/bin/python3 -m pytest tests/unit -q --timeout=10
__main__.py: error: unrecognized arguments: --timeout=10
  inifile: /Volumes/PDATA/GitDB/TeamAgent/pytest.ini

$ .venv/bin/pip list | grep -i timeout
async-timeout  5.0.1          ← 只有这个，它不是 pytest 插件

$ sed -n '39p' .github/workflows/test.yml
        pip install -r requirements.txt pytest-timeout pytest-xdist     ← CI 有
```

补充：`pytest.ini` 里没有任何 `timeout` 配置项。pytest 内置的 `faulthandler_timeout` 是**另一回事**——按 `pytest --help` 的原文它只「在某个测试超过 TIMEOUT 秒时 dump 所有线程的 traceback」，**默认不终止进程**（要终止还需同时开 `faulthandler_exit_on_timeout`），而且本仓 `pytest.ini` 里两者都没配。**它不能当超时用。**

### 正确做法

| 别用 | 原因 |
|---|---|
| `timeout <cmd>` / `/usr/local/bin/timeout` | 本机 x86_64；父进程架构错 → 子进程跑成 x86_64 |
| `gtimeout <cmd>` / `/usr/local/bin/gtimeout` | **同上，也是 x86_64**（别照抄网上的"用 gtimeout"） |
| `arch -arm64 gtimeout …` | `Bad CPU type in executable`，x86_64 二进制在 arm64 下跑不起来 |
| `pytest --timeout=N`（**本地**） | 本地 venv 未装 `pytest-timeout`，会 `unrecognized arguments` |

| 用这个 | 说明 |
|---|---|
| `perl -e 'alarm N; exec @ARGV' <cmd>` | ✅ 系统自带 perl，arm64 干净，无任何依赖 |
| Python 侧 `subprocess.run(..., timeout=N)` | ✅ 不跨进程边界，天然不受架构问题影响 |
| `pytest --timeout=N`（**CI**） | ✅ CI 装了 `pytest-timeout` |

实测验证：

```bash
# ① perl alarm 真的会超时（不是只"能跑"）
$ time perl -e 'alarm 2; exec @ARGV' sleep 30
Alarm clock: 14
real 0m2.008s
$ echo $?
142

# ② perl 下子进程是 arm64，编译型包正常
$ perl -e 'alarm 60; exec @ARGV' .venv/bin/python3 -c "import platform,pydantic_core;print(platform.machine())"
arm64

# ③ subprocess.run(timeout=) 下子进程是 arm64
$ .venv/bin/python3 -c "
import subprocess
r = subprocess.run(['.venv/bin/python3','-c','import platform,pydantic_core;print(platform.machine())'],
                   capture_output=True, text=True, timeout=60)
print(r.returncode, r.stdout.strip())
"
0 arm64
```

### 一行自查命令

```bash
file -b $(which -a timeout gtimeout) | grep -q x86_64 && echo "危险：有 x86_64 的 timeout，别用它套 Python 命令" || echo "OK"
```

想连"实测通不通"一起验：

```bash
perl -e 'alarm 10; exec @ARGV' .venv/bin/python3 -c "import platform;print(platform.machine())"   # 应输出 arm64
```

---

## 3. `tests/config/setting.json` 会被改写：脏数据风险

> ⚠️ **本条与我拿到的初始描述有出入，以本节的实测结论为准**：改写**确实会发生**，但「之后同 worktree 跑 room/ws 用例**必假红**」这一因果，**我未能复现**。下面把「确认成立的」和「未成立的」分开写，避免下一个人照着一条错误的因果去排查。

### 症状

跑完测试后 `git status` 出现：

```
 M tests/config/setting.json
?? tests/config/setting.README.md        ← 只在「非 pytest 环境」下出现
```

打开 diff 会看到配置被**迁移并重新序列化**：

```diff
-      "enable": true,                              ← 被 exclude_defaults 抹掉
-    "vision": "", "advanced": ""                   ← 同上
-  "version": "v2"                                 → +"version": "v3"
+  "context_config": {}, "language": "zh-CN",
+  "development_mode": false, "auth": {}, "third_party_services": {}
```

### 为什么容易误判

**① 真危害不是「红」，是「脏」。** 这个 `M tests/config/setting.json` 极易被顺手 `git add -A` 带进提交——把本地环境的迁移结果提交上去，才是这个坑真正咬人的地方。

**② 改写链路是无条件的**（`src/util/configUtil/core.py`）：

```python
def update_setting(mutator: Callable[[SettingConfig], None]) -> None:
    mutator(_cached_app_config.setting)
    _save_setting_to_file()          # ← 无条件写回
```

`_save_setting_to_file()` 用 `model_dump(exclude_defaults=True)` 重序列化 + `os.replace()` 原子替换，所以：

- 与默认值相同的字段（`enable: true`、`vision: ""`、`advanced: ""`）会在 key 层面**消失**；
- `migrate_setting()` 会把 `version` 从 `v2` 提到 `v3` 并补 `context_config/language/...`；
- **inode 与 mtime 一定会变**（`os.replace` 换文件），**即使内容最终与原文件逐字节相同**——所以别用 mtime 判断"有没有被写过"。

**③ `?? setting.README.md` 的触发条件很窄，容易背锅到别处。** `_load_setting()` 里：

```python
if not _is_running_tests():                      # _is_running_tests() 只检查 PYTEST_CURRENT_TEST
    sync_file_if_changed("docs/setting.README.md", config_dir, "setting.README.md")
```

也就是说：**在 pytest 里跑不会产生它；用普通 Python 脚本/REPL 调 `configUtil.load(<config_dir>)` 就会产生它**。同一份代码，pytest 内与 pytest 外行为不同。

### 我实测到的边界（重要）

| 实验 | 结果 |
|---|---|
| 直接调 `configUtil.load('tests/config')` + `update_setting(...)`（非 pytest 环境） | ✅ 文件被改写（v2→v3，丢 `enable/vision/advanced`）+ 多出 `?? setting.README.md` |
| 改用 `PYTEST_CURRENT_TEST=dummy::test` 模拟 pytest 环境 | ✅ 文件仍被改写，但**不再**产生 `setting.README.md` |
| 正常跑 `pytest tests/integration/test_room_service`、`tests/api/test_ws_controller` 等 | ❌ **没有**出现假红（12 passed），`git status` 也**没有** `M setting.json` |
| 正常跑 6 个带 `use_custom_config = True` 的 API 测试目录（system / init / llm_service / auth / demo_mode / room） | ❌ 均未弄脏工作区 |
| 跑完后检查 `tests/api/test_system_controller/config/setting.json` 的 inode | ⚠️ 确实变了（说明**被写过**），但内容被 `teardown_class` 原样写回，`sha256` 不变 |

原因：带独立配置目录的测试类会在 `setup_class` 里把整份 `setting.json` 读进内存、`teardown_class` 里原样写回。两种等价写法都存在：

- `use_custom_config = True`（全仓 **7 处**，`tests/base.py` 据此推导 `<测试文件目录>/config`）；
- 直接显式传目录 `configUtil.load(_CONFIG_DIR, preset_dir=_CONFIG_DIR, force_reload=True)`（`tests/integration/test_agent_service`、`test_chat_flow`、`test_collaboration_task_flow` 等都是这种）。

示例（`tests/api/test_system_controller/test.py`）：

```python
@classmethod
def setup_class(cls) -> None:
    with open(_SETTING_PATH, "r", encoding="utf-8") as f:
        cls._original_setting = f.read()
    super().setup_class()

@classmethod
def teardown_class(cls) -> None:
    super().teardown_class()
    if cls._original_setting is not None:
        with open(_SETTING_PATH, "w", encoding="utf-8") as f:
            f.write(cls._original_setting)
```

**结论：改写必然发生，但多数测试类自带「读-存-写回」兜底，所以通常表现为「脏」而非「红」。** 缺少兜底的场景（新增测试类没写 setup/teardown、跑测试时被 Ctrl-C 打断导致 teardown 没执行、或手工脚本调用了 `update_setting`）才会留下脏文件。

### 正确做法

```bash
# ① 跑测试前后都过一遍（成本极低，习惯成自然）
git status --short

# ② 发现脏了就还原（注意：-u 的 README 也要清）
git checkout -- tests/config/setting.json
rm -f tests/config/setting.README.md

# ③ 提交前务必确认暂存区没有它
git diff --cached --stat | grep -E 'tests/.*config/setting' && echo "危险：配置被带进提交了" || echo "OK"
```

新增带后端、且会用到独立配置目录的测试类时，**照抄上面 `setup_class`/`teardown_class` 的读-存-写回模式**（`use_custom_config = True` 或显式 `configUtil.load(_CONFIG_DIR, ...)` 都算）。

### 一行自查命令

```bash
git status --short tests/ | grep -q 'config/setting' && echo "脏了：git checkout -- tests/config/setting.json" || echo "OK"
```

---

## 4. 先跑 mypy，再跑测试

### 症状

本地「测试通过了」，但 CI 上 `Run tests` 那段**根本没跑**；或者本地 `pytest` 输出里只有 mypy 的报错、看不到任何测试结果。

### 为什么容易误判

**① `mypy` 红会把测试整段 skip。** `.github/workflows/test.yml` 的 job 内步骤顺序是：

```yaml
- name: Run mypy              # test.yml:41
  run: bash scripts/run_mypy.sh

- name: Run tests (parallel)  # test.yml:45，紧跟其后
  run: PYTHONPATH=src python -m pytest tests/unit tests/api tests/integration -n auto --dist loadscope --timeout=60
```

GitHub Actions 默认「某 step 失败即中止后续 step」，`run_mypy.sh` 失败时 `exit 1`，所以 **tests 步骤直接不执行**。在 CI 页面上看起来像「测试没跑」，很容易被读成「测试通过」。

**② 成本极低，没有任何跳过的理由。** 实测（本机）：

```bash
$ time bash scripts/run_mypy.sh
Success: no issues found in 140 source files
mypy_ret_code: 0
real 3.28s
```

**140 个源文件只要 3.3 秒**，比全量测试快两个数量级。

### 正确做法

提交前固定顺序：

```bash
bash scripts/run_mypy.sh                  # 1. 类型（3~4 秒，红就停）
.venv/bin/python3 -m pytest <目标用例> -q   # 2. 目标测试
.venv/bin/python3 -m pytest tests/unit tests/api tests/integration -q -n auto --dist loadscope   # 3. 全量（必要时）
git diff --cached --stat                  # 4. 自检暂存内容
```

约定：**mypy 红一律不提交。**

### 一行自查命令

```bash
bash scripts/run_mypy.sh && echo "mypy OK，可以跑测试" || echo "mypy 红，先修类型，否则 CI 的测试会被整段 skip"
```

---

## 5. 隔离 worktree 纪律：`prune` 是全局操作

### 症状

`git worktree list` 里堆积 `/private/tmp/*` 僵尸条目；同伴核查仓库状态时把这些不明目录当成"谁留下的垃圾"报上来，浪费一轮沟通。

### 为什么容易误判

**`git worktree remove` 是精确的，`git worktree prune` 是全局的。**

`prune` 会清理**仓库里所有**失效的 worktree 记录——包括**别人**正在用的、只是临时不可见的条目（比如别人挂载/移动过路径）。本轮就有人踩过：`prune` 把同伴的验证目录记录一起清掉了。

### 正确做法

```bash
# ① 创建（detached，不产生分支）
git worktree add --detach /tmp/verify_xxx origin/dev

# ② 软链 venv（避免重复装依赖；软链不改仓库内容）
cd /tmp/verify_xxx && ln -s /Volumes/PDATA/GitDB/TeamAgent/.venv .venv
.venv/bin/python3 -m pytest tests/unit -q          # 实测可用

# ③ 精确清理自己创建的那一个（推荐）
cd /Volumes/PDATA/GitDB/TeamAgent
git worktree remove --force /tmp/verify_xxx

# ④ 想确认有没有残留时，用 dry-run，不落地
git worktree prune -n
```

规则：

- **只 `remove` 自己创建的条目**，`--force` 用于有未跟踪文件的情况；
- **不要习惯性 `prune`**；确实需要时先 `prune -n` 看它打算删什么；
- 用完立刻清理，别留到"下次一起清"——这正是僵尸条目的来源。

### 一行自查命令

```bash
git worktree list | grep -E '/tmp|/private/tmp' && echo "有临时 worktree 残留，确认是自己创建的后 git worktree remove --force <路径>" || echo "OK"
```

---

## 附录：一分钟速查

| # | 陷阱 | 快查 |
|---|---|---|
| 1 | worker 命名 | `echo "$PYTEST_XDIST_WORKER" \| grep -Eq '^gw[0-9]+$' \|\| echo 危险` |
| 2 | `timeout`/`gtimeout` 均 x86_64 | `file -b $(which -a timeout gtimeout)` 出现 `x86_64` 就别用 |
| 3 | `setting.json` 被改写 | `git status --short tests/ \| grep config/setting` |
| 4 | mypy 红导致测试被 skip | `bash scripts/run_mypy.sh && ...` |
| 5 | worktree 残留 | `git worktree list \| grep /tmp` |

**共通规律**：这 5 条都是「**环境把真红和假红混在一起**」。判断顺序建议固定为：
**先确认 worker 命名 → 再确认命令没被 x86_64 二进制包住 → 再看工作区是否脏 → 再看 mypy → 最后才怀疑代码。**

---

## 后续建议（不在本文档范围内，需另行开单）

本文档按约定**只写文档，不改任何代码/配置/CI**。以下三项若要根治，属代码层或 CI 层改动，需由主管评估后单独开单：

1. **`_get_worker_offset()` 的静默回退**：非 `gw<N>` 命名时建议打印一次 warning（至少 `-W` 或 `-s` 下可见），而不是无声回退到 0。
2. **`_is_running_tests()` 判据过窄**：只认 `PYTEST_CURRENT_TEST`，导致「pytest 内 vs 外」行为不一致（同一个 `configUtil.load` 一个会写 `setting.README.md`、一个不会）。可考虑改为同时接受 `PYTEST_XDIST_WORKER`、或显式 `TEAMAGENT_ENV=test`。
3. **`tests/config/setting.json` 的 `version: v2` 与代码 `migrate_setting()` 目标 `v3` 长期不一致**：每次加载都要跑一遍迁移，靠各测试类的 `teardown_class` 兜底写回。建议统一把仓库内测试配置升到 v3，消除「必然被写」这一前提。
