# `console` help 体积基线（#154 CC6 现役版）

`console` 的 help 文本是**派生**出来的（设计见
`docs/design/2026-11-20-console-tool-metadata/console_tool_metadata_design.md` §5），
不再手抄。所以「体积」也会随派生结构变，需要一个**可复跑的基线**来回答两个问题：

1. 这次改动让 help 变胖了还是变瘦了（模型上下文成本）；
2. 有没有哪条命令的 help 单独膨胀（长尾比总量更容易失控）。

本文件是 #317（P2 help 收拢）重量后的基线，取代 #154 CC6 里那份「16 工具 vs console」
的历史读数（那份是重构前的量法，已归档在 `shared/_archive_20261122/`，不可与本表相加）。

## 1. 量法（改数字前先读这一节）

| 项 | 口径 |
| --- | --- |
| 被测入口 | `service.funcToolService.consoleTool` 的 `HELP_SUMMARY` / `HELP_DETAIL` |
| 触发方式 | 两者都是 **PEP 562 惰性属性**（模块级 `__getattr__`），`import` 不派生，**读属性才派生**；每次读都会按当刻的工具声明重新拼 |
| 单位 | **UTF-8 字节**（`len(text.encode("utf-8"))`，含换行符） |
| summary | `HELP_SUMMARY` 一份整体字节数 + 行数（`len(text.splitlines())`） |
| detail | `HELP_DETAIL` 是 `{命令 key: 该命令的 help 正文}`：条目数、逐条字节、总和、最大 / 最小 |
| 不计入 | `help <resource>` 这类聚合视图（它由多条 detail 拼成，按 detail 逐条计已覆盖，不重复计总量）；`console` 工具 schema 本身的字节数（那是另一件事，见 #154 CC6 历史读数） |
| 时点 | detail 是「命令 key 全集」，包含容器命令（`container note`）与 `mcp` / `openapi` / `graphql` 子树 |

复跑：

```bash
cd <worktree>
PYTEST_XDIST_WORKER=gw99 env -u TOGOSPACE_DESKTOP \
  PYTHONPATH=src:<pydeps> .venv/bin/python3 -m pytest \
  tests/integration/test_funcToolService/test_help_snapshot_tmp.py -q -p no:randomly -p no:cacheprovider
# 产物 /tmp/help_after_317.json 的 volumes 段就是本文件的读数
```

（该夹具是 #317 的**临时**快照夹具，没提交；量具与产物归档在
`shared/console-p2-help-merge-317/`。要长期看守体积请另立项做成用例 —— 直接加断言会改
`tests/unit` 计数，超出 #317 边界。）

## 2. 基线读数（`0.6_dev` @ #317 收拢后）

| 指标 | #317 收拢前（`cbec90d9`） | #317 收拢后 | 变化 |
| --- | --- | --- | --- |
| `summary_bytes` | 1042 | **1042** | 0 |
| `summary_lines` | 14 | **14** | 0 |
| `detail_entries` | 28 | **28** | 0 |
| `detail_bytes_total` | 21085 | **20129** | −956（−4.53%） |
| `detail_bytes_max` | 2835（`task update`） | **2735**（`task update`） | -100 |
| `detail_bytes_min` | 144（`reload`） | **144**（`reload`） | 0 |

`summary` 一字未动是有意为之：本单只删「与 summary 重复」的 overlay 行，summary 自身
是全局规则的**正本**（`_build_help_summary`），不在收拢范围内。

## 3. 逐条明细（收拢后，按字节降序）

| 命令 key | 字节（#323 后） | 相对 #323 前 |
| --- | --- | --- |
| `task update` | 2735 | 2835 → 2735（-100） |
| `task create` | 1705 | — |
| `agent update` | 1559 | 1661 → 1559（-102） |
| `task ls` | 1527 | — |
| `dept update` | 1307 | 1407 → 1307（-100） |
| `agent create` | 1130 | — |
| `room update` | 1031 | 1131 → 1031（-100） |
| `dept create` | 922 | — |
| `dept rm` | 703 | 727 → 703（-24） |
| `role create` | 644 | — |
| `role update` | 637 | 734 → 637（-97） |
| `agent get` | 633 | 658 → 633（-25） |
| `room create` | 590 | — |
| `container note` | 574 | 746 → 574（-172） |
| `dept get` | 538 | 561 → 538（-23） |
| `room rm` | 506 | 530 → 506（-24） |
| `room get` | 454 | 477 → 454（-23） |
| `task get` | 382 | 405 → 382（-23） |
| `agent dm` | 346 | 382 → 346（-36） |
| `role ls` | 320 | — |
| `role rm` | 305 | 339 → 305（-34） |
| `agent wake` | 293 | 331 → 293（-38） |
| `dept ls` | 279 | — |
| `agent ls` | 252 | — |
| `role get` | 236 | 271 → 236（-35） |
| `room ls` | 195 | — |
| `time get` | 182 | — |
| `reload` | 144 | — |

**#323 之后的口径**：凡有参数的命令都换成了「位置参数：/ 选项：」两段，所以 28 条里 27 条字节都变了（唯一不变的是 `reload`，它没有参数）；没有位置参数、也没有隐藏参数的命令净变化为 0（段标题改名「参数：」→「选项：」恰好同长）。

## 4. 已知限制

- 体积与「实际 token」不是同一个量（token 分词依赖模型），本表只做**同口径前后对比**，
  不要跨口径引用比例。
- 读数依赖工具声明的**当刻快照**：新增工具 / 新增参数都会让 `detail_bytes_total` 变，
  所以这张表要跟版本走，别当成不变量。
