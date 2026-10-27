# 本地环境自查清单

本文档收录 **TogoSpace 本地开发/测试环境里容易让人误判的 5 类陷阱**。其中 4 类会表现为「假红」（看起来是代码坏了，其实是环境问题）；**第 3 类不是假红，而是「不留神就误提交」的脏数据陷阱——危害其实更大，因为它静默发生、不报错**。

每一条都按同一结构写：**症状 → 为什么容易误判 → 正确做法 → 一行自查命令**。
目标：新人 **30 秒内**能判断「我现在遇到的是不是这个坑」。

> 与同目录其他文档的分工：
> - [execution_architecture.md](./execution_architecture.md)：测试体系架构、Mock 逻辑、配置加载机制（**讲"是什么"**）；
> - [unit_integration_boundary.md](./unit_integration_boundary.md)：单元/集成测试边界与全局状态污染（**讲"该放哪"**）；
> - 本文档：**讲"跑起来为什么红了、明明绿了为什么不算"**（环境层，全部是实操命令）。

> **本文档的证据分级**：每条结论都标注 **【已验证】**（本机跑过并附输出）或 **【代码推演】**（只读代码得出，未实跑）。第 3 条尤其注意：**全量套件下的行为未逐用例验证**。

---

## 0. 读本文档前，先记住三条纪律

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

#### 机制化入口：一行环境横幅（贴进 issue / 报告 / 群消息开头）

口头要求靠自觉，贴一行元数据靠机制。**任何涉及环境结论的汇报，开头先贴这行**：

```bash
printf "arch=%s | timeout=%s | gtimeout=%s | pytest-timeout=%s\n" \
  "$(uname -m)" \
  "$(file -b "$(which timeout)" 2>/dev/null | grep -o 'x86_64\|arm64' || echo none)" \
  "$(file -b "$(which gtimeout)" 2>/dev/null | grep -o 'x86_64\|arm64' || echo none)" \
  "$(.venv/bin/pip list 2>/dev/null | grep -qi '^pytest-timeout' && echo installed || echo missing)"
```

本机实测输出：

```
arch=arm64 | timeout=x86_64 | gtimeout=x86_64 | pytest-timeout=missing
```

一眼就能看出「这个结论是在什么环境下得出的」，从机制上堵住「本地无 / CI 有」这类混说。

> **若要贴 CI 侧结果，请实际在 CI 里跑这一行再贴**，不要凭推演写 CI 的输出——那正好违反纪律 3。（本机这一行是【已验证】，CI 侧本机无法实跑，标【未验证】。）

### 纪律 2：本文档里每条命令都必须是**本机实测跑通**的

未经实测的操作建议不得写入——本轮就发生过「凭印象建议用 `gtimeout`」，导致团队成员各自记错一次口径的事故（详见第 2 条）。

**如果你要往本文档加条目，请把命令跑一遍，连同输出一起贴进来。**

### 纪律 3：凡「会 / 不会影响 X」的断言，必须标注【已验证】或【代码推演】

这是纪律 1 的加强版，也是本轮踩得最疼的一课：

- 有人写「测试会改写配置，**之后跑 room/ws 必假红**」→ 实跑 12 passed，**断言不成立**（原话写进了任务单，见第 3 条）；
- 有人建议「用 `gtimeout` 代替」→ 实测同为 x86_64，**建议不成立**；
- 有人说「改了全局排序，**前端观感会变差**」→ 只到推演级别，接口返回顺序变了是已验证的，观感变差没人验。

**规矩**：写「不会影响 X」和写「会影响 X」一样要证据。做不到实跑就老老实实标【代码推演】，并写清推演到哪一步为止。

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

### ⚠️ 超时 ≠ 业务失败：两种超时方式的返回语义不同

这是第二条「假红」的来源：**脚本里写 `if rc != 0: fail`，会把超时误判成业务失败。**

| 方式 | 超时时发生了什么 | 怎么判断「这是超时」 |
|---|---|---|
| `perl -e 'alarm N; exec @ARGV'` | `alarm` 发 **SIGALRM(14)**，进程被默认动作杀掉 → **exit code = 142（128+14）** | `rc == 142` 即超时；shell 还会打印 `Alarm clock: 14` |
| Python `subprocess.run(..., timeout=N)` | **抛 `subprocess.TimeoutExpired` 异常**，不是返回码 | `try/except TimeoutExpired`；⚠️ **`TimeoutExpired` 没有 `.returncode` 属性**，别照抄 `e.returncode` |

实测：

```bash
$ perl -e 'alarm 2; exec @ARGV' .venv/bin/python3 -c "import time; time.sleep(10)"
Alarm clock: 14
$ echo $?
142                                        # 128 + 14(SIGALRM)

$ .venv/bin/python3 -c "
import subprocess
try:
    subprocess.run(['sleep','10'], timeout=2)
except subprocess.TimeoutExpired as e:
    print('timeout, returncode attr =', hasattr(e, 'returncode'))   # → False
"
timeout, returncode attr = False
```

**结论**：判断「超时」要看**信号/异常类型**，不要只看「非 0 即失败」。

### 一行自查命令

```bash
file -b $(which -a timeout gtimeout) | grep -q x86_64 && echo "危险：有 x86_64 的 timeout，别用它套 Python 命令" || echo "OK"
```

想连"实测通不通"一起验：

```bash
perl -e 'alarm 10; exec @ARGV' .venv/bin/python3 -c "import platform;print(platform.machine())"   # 应输出 arm64
```

---

## 3. `tests/config/setting.json` 被改写：脏数据 + 误提交

> **本文档的立论边界（先读这段）**：
> - 要防的是「**脏数据被误提交**」，**不是**「跑测试会假红」。
> - 早期曾有一条断言「测试改写 `setting.json` 后，再跑 room/ws 用例**必假红**」——**该断言已被实测推翻，本节不再采用**（复现见下表）。
> - **全量套件下的行为未逐用例验证**：我只跑了 room / ws / chat_flow 以及 6 个带独立配置目录的 api 子目录，结论是「这些组合下不脏」。**不能据此推断「全量一定不脏」。**

### 症状

跑完测试或脚本后 `git status` 出现：

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

**① 真危害不是「红」，是「脏」→「误提交」。** 这个 `M tests/config/setting.json` 极易被顺手 `git add -A` 带进提交——把本地环境的迁移结果提交上去，才是这个坑真正咬人的地方。**防线的重心因此是「提交前看清暂存区」，不是「排查假红」。**

**② diff 形态有欺骗性：看着像「清理冗余」，实际是配置被换成另一套形状。** `_save_setting_to_file()` 用 `model_dump(exclude_defaults=True)` + `os.replace()`，所以：

- 与默认值相同的字段（`enable: true`、`vision: ""`、`advanced: ""`）在 key 层面**整体消失**，看起来像"精简"；
- `migrate_setting()` 把 `version: v2` 提到 `v3`，并补上 `context_config` / `language` / `auth` / `third_party_services`；
- **inode 与 mtime 一定会变**（`os.replace` 换文件），**即使内容最终与原文件逐字节相同**——所以**别用 mtime 判断"有没有被写过"**（我实测过：inode 变、`sha256` 不变）。

**③ 改写链路是无条件的**（`src/util/configUtil/core.py:227-236` → `:239`）：

```python
def update_setting(mutator: Callable[[SettingConfig], None]) -> None:
    mutator(_cached_app_config.setting)
    _save_setting_to_file()          # ← 无条件写回
```

**④ `?? setting.README.md` 只在「非 pytest 环境」产生，容易背锅到别处。** `_load_setting()`（`core.py:22-24` / `:121-122`）：

```python
def _is_running_tests() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ     # ← 只查这一个变量
...
if not _is_running_tests():
    sync_file_if_changed("docs/setting.README.md", config_dir, "setting.README.md")
```

也就是说：**在 pytest 里不会产生它；用脚本/REPL 调 `configUtil.load(<config_dir>)` 就会产生它**。很多人是开 REPL 调配置的，一调就脏：

```bash
$ PYTHONPATH=src .venv/bin/python -c "from util import configUtil; configUtil.load('tests/config')"
$ git status --short
?? tests/config/setting.README.md      ← 凭空多出（实测复现）
```

> 注：`tests/api/test_system_controller/config/setting.README.md` 等若干目录下的同名文件是**入库文件**（`git ls-files` 可查），所以"生成 README"本身不是 bug；问题在于 `tests/config/` 下这个位置**没被跟踪**，于是变成脏文件。

### 实测边界（重要：这里区分「已验证」与「未验证」）

| 实验 | 结果 | 性质 |
|---|---|---|
| 直接调 `configUtil.load('tests/config')` + `update_setting(...)`（非 pytest 环境） | ✅ 文件被改写（v2→v3，丢 `enable/vision/advanced`）+ 多出 `?? setting.README.md` | 【已验证】 |
| 仅调 `configUtil.load('tests/config')`，不调 `update_setting`（非 pytest 环境） | ✅ 仍会多出 `?? setting.README.md`（只读也会脏） | 【已验证】 |
| 设 `PYTEST_CURRENT_TEST=dummy::test` 模拟 pytest 环境 | ✅ 文件仍被改写，但**不再**产生 `setting.README.md` | 【已验证】 |
| 跑 `pytest tests/integration/test_room_service/test_upsert_room.py` | 9 ~ 12 passed，`md5` 前后不变，`git status` 干净 | 【已验证】 |
| 跑 `pytest tests/api/test_ws_controller` | 3 passed，工作区干净 | 【已验证】 |
| 逐个跑 6 个带独立配置目录的 api 子目录（system / init / llm_service / auth / demo_mode / room） | 全部 passed，每次 `git status --short tests/` 为空 | 【已验证】 |
| 跑完后检查 `tests/api/test_system_controller/config/setting.json` 的 inode | ⚠️ inode 变了（说明**被写过**），但被 `teardown_class` 原样写回，`sha256` 不变 | 【已验证】 |
| **全量套件（`tests/unit tests/api tests/integration`）下是否一定不脏** | **未逐用例验证，请勿假设** | 【未验证】 |

**为什么默认路径下通常干净**：带独立配置目录的测试类会在 `setup_class` 里把整份 `setting.json` 读进内存、`teardown_class` 里原样写回。两种等价写法都存在：

- `use_custom_config = True`（`tests/base.py` 据此推导 `<测试文件目录>/config`）。**这里有三个数，含义不同，别混用**（均为实测）：

  | 数 | 含义 | 复现命令 |
  |---|---|---|
  | **8** | `tests/` 下出现过该属性的 `.py` 文件数（含 `tests/base.py` 的定义/说明处，即 7 个真实测试文件 + 1 个基类文件） | `grep -rl use_custom_config tests/ --include=*.py \| wc -l` |
  | **7** | 其中真实测试文件数（= 真实赋值语句行数）。⚠️ 直接 `grep -rn "use_custom_config = True"` 会得到 **9 行**：7 行真实赋值 + `tests/base.py:158`、`:437` 两处**注释/文档字符串**里的字样，**必须排除 `tests/base.py` 再数** | `grep -rn "use_custom_config = True" tests/ --include=*.py \| grep -v "tests/base.py:" \| wc -l` |
  | **16** | `grep -rl` **不加** `--include` 时命中数——多出的 8 个是 `__pycache__/*.pyc`，**纯噪声**，别拿它当"文件数" | `grep -rl use_custom_config tests/ \| wc -l` |

  这 7 个测试文件里，**6 个**所在目录带独立 `config/setting.json`（system / auth / demo_mode / init / room / llm_service，均在 `tests/api/` 下）；第 7 个是 `tests/real/simple_chat`，它虽有 `config/` 但走 `tests/real/` 单独一轮。
  **结论：本条适用范围应表述为「6 个带独立配置目录的 api 测试目录」，不要简写成「6 个目录用 use_custom_config」。**
- 直接显式传目录 `configUtil.load(_CONFIG_DIR, preset_dir=_CONFIG_DIR, force_reload=True)`（`tests/integration/test_agent_service`、`test_chat_flow`、`test_collaboration_task_flow` 等都是这种）。

示例（`tests/api/test_system_controller/test.py:22-32`）：

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

**结论：改写必然发生，但自带兜底的测试类会把内容写回，所以现象通常是「脏」而不是「红」。** 会留下脏文件的场景：新增测试类没写 setup/teardown、跑测试时 Ctrl-C 打断导致 teardown 未执行、或用脚本/REPL 直接调了 `configUtil.load` / `update_setting`。

### 正确做法

**重点是把「看清暂存区」当成提交前的固定动作，而不是去排查假红。**

```bash
# ① 跑测试前后都过一遍（成本极低，习惯成自然）
git status --short

# ② 发现脏了就还原（注意：未跟踪的 README 也要清）
git checkout -- tests/config/setting.json
rm -f tests/config/setting.README.md

# ③ 提交前务必确认暂存区没有它（这条是真正的防线）
git diff --cached --stat | grep -E 'tests/.*config/setting' && echo "危险：配置被带进提交了" || echo "OK"
```

新增带后端、且会用到独立配置目录的测试类时，**照抄上面 `setup_class`/`teardown_class` 的读-存-写回模式**（`use_custom_config = True` 或显式 `configUtil.load(_CONFIG_DIR, ...)` 都算）。

用 REPL / 一次性脚本调试配置相关逻辑时，**别指向 `tests/config/`**——用 `tmp_path` 或临时目录，否则会留下未跟踪的 `setting.README.md`：

```bash
# ✅ 指向临时目录
PYTHONPATH=src .venv/bin/python -c "
import tempfile, os
d = tempfile.mkdtemp()
from util import configUtil; configUtil.load(d)
print('ok', d)
"
```

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
| 3 | `setting.json` 被改写（**脏数据/误提交**，非假红） | `git diff --cached --stat \| grep 'config/setting'` |
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
4. **`_is_running_tests()` 顺带影响「写快照」类脚本**：既然它只认 `PYTEST_CURRENT_TEST`，那么任何在 pytest 之外跑的写配置/写快照脚本都会走"非测试"分支。若后续引入 CI 侧的护栏自检（如把 `--selftest` 挂进流水线），需注意这类脚本在 CI 里是"非 pytest 环境"，行为与本地 pytest 内不一致。
