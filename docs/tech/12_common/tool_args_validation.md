# 工具参数校验：`null` 豁免的边界（#195 / #359）

> 本文是 `src/service/funcToolService/toolArgsValidation.py` 中 `_is_ignorable_null` 函数 docstring 的原文搬迁（2026-11-25，#359 压缩注释：函数头从 72 行压到 5 行）。
> 代码里只留「为什么不能放宽 / 放宽会看到什么」和本文指针；**表、实测读数、两次打回的历史都在这里**。

## 1. 分流按「schema 来源」，不按「字段是否必填」

| 路径 | 入口 | `null` 处置 |
| --- | --- | --- |
| 本地路径 | `func=`（schema 由本仓库生成器产出） | **一律严格**，不豁免任何 `null` |
| 外部路径 | `schema=`（外部来源，如 TSP 远端） | 单个非必填字段整体为 `null` ⇒ 忽略该 `ValidationError`；其余全拒 |

`validate_tool_args` 只在 **`schema=` 传入（外部来源）** 时调用 `_is_ignorable_null`。

## 2. 为什么本地路径不再豁免（#195 根因）

豁免成立的前提是「schema 里表达不出 `null` 的合法性」。这条前提在 #195 之前是真的：`python_type_to_json_schema` 把 `Optional[T]` 剥成 `T`，于是 `{"timezone": null}` 只能靠豁免放行。

但豁免面按**「是否必填」**切，而 `null` 合法性该按**「注解是否含 None」**切 —— 两条线不重合的地方就是 19 个注解不含 None 的字段（`create_task.description` / `.note` / `list_tasks.open_only` …）。它们本不该接受 `null`，却被一并放行，一路撞到 `NOT NULL constraint failed`，用户看到裸数据库错误。

⇒ 正确修法是让 schema 表达 nullability（#195 换成 TypeAdapter，`Optional[T]` → `type: [T, "null"]`），**豁免随之失去存在理由**。「把豁免面再收窄一点」是错方向：它治的是症状（哪一类错误被吃掉），不是病因（schema 不知道自己能不能 `null`）。

## 3. 豁免面必须恰好是「路径长度为 1 的字段」（H1 + H2，38 两轮打回）

下面三条**在启用范围内仍然必须遵守**（返工后本函数重新生效，只是范围收窄到 `schema=` 外部路径）。谁若把 `len(path) != 1` 放宽成 `>=1`，H1/H2 立刻回潮。

本函数只该豁免**一个具名参数整体为 `null`** 这一种情形。`absolute_path` 长度决定了「错在哪一层」，三者必须分开处理：

| `len(path)` | 含义 | 处置 |
| --- | --- | --- |
| `0` | **根节点**：整份参数不是对象（`function_args` 字面量就是 `null`）| **报错**（H1）|
| `1` | 单个参数整体为 `null`（`{"timezone": null}`）| 非必填 ⇒ 豁免；必填 ⇒ 报错 |
| `≥2` | **容器元素级** `null`（`{"tags": [null]}`、`{"a":{"b":null}}`）| **报错**（H2）|

### 3.1 H1：根节点漏网（38 首次打回）

只判 `instance is None` 时，根节点的 `type: object` 错误（`path=[]`、`instance=None`）落到 `return True` 被豁免 ⇒ `func(**None)` ⇒ 英文原话 + 无 `error_code`。实测修前 `get_tools(include_container_tools=True)` 的 **37 个工具喂 `'null'` 全漏**（这里写的是 37 个工具时的数字；现在是 49 个，见 `_drop_variadic_params` docstring 的实测行）。

### 3.2 H2：元素级漏网（38 二次打回）

修 H1 时只补了 `if not path`，**`path` 长度 ≥2 仍落到 `return True`**：`{"depends_on": [null]}`（`items: integer`）被豁免 ⇒ `int(None)` ⇒ 同一旧形态。实测修前 **10 个 array 型参数喂 `[None]` 全部放行**；**TSP 远端更严重** —— `call_tool` 真的被调用了，**非法参数已越过进程边界发给对端**。

两次都是同一个假设（「`null` 都可以放过」）的两半，所以这里一次性把豁免面收到**恰为长度 1**，而不是再逐个补 `len(path)==1` / `len(path)==2` 的分支。

## 4. 为什么不会误杀

（#195 前的实测，外部 schema 路径现在仍适用）

真实 37 个工具里 `object` 型参数只有 4 个（`save_agent.i18n` 等；这里写的是 37 个工具时的数字，现在工具数与每个工具的参数见 `_drop_variadic_params` docstring 的实测行），它们的 schema **都没有嵌套 `properties`**，`array` 也没有 `items.type == object` 的用例 ⇒ 本项目不存在「嵌套对象内的非必填字段传 `null` 且应被允许」的合法签名。

反向对照：`{"timezone": null}`、`{"depends_on": null}`（字段整体 `null`）仍放行。

⚠️ 这段结论只针对**本仓库工具**。外部 schema（`gtsp`）同样已实测：26 个「非必填但无 `null`」字段中 **14 个在 `gtsp` 本体上 ACCEPT `null`**，其中 `read_file.encoding` / `grep_search.path` 等是 `len(path)==1` 的典型形态 ⇒ 豁免面对它们是对的。

## 5. 外部 schema 前提的实测依据

豁免成立的前提「schema 里表达不出 `null` 的合法性」对外部 schema **仍然成立**：对端用的不是本仓库的生成器（实测 `gtsp` v0.4.19 的 11 个工具 / 39 个 property、`default` 0 个、schema 含 `null` 的 **0** 个，26 个「非必填但无 `null`」字段），本仓库无法保证它表达了 nullability。对它严格校验会把「对端本来就接受的合法写法」打死（`read_file{encoding: null}` / `grep_search{path: null}` 在 `gtsp` 本体上实测 ACCEPT），属**新误杀**。

## 6. `validate_tool_args` 的参数语义与失败返回

```python
validate_tool_args(tool_name, args, *, func=None, schema=None) -> dict | None
```

| 参数 | 语义 |
| --- | --- |
| `tool_name` | 工具名，仅用于日志与错误文案 |
| `args` | `json.loads(function_args)` 的结果（正常是 dict；模型给 `[]` / `5` 时也可能不是 dict，那本身就该被 schema 的 `type: object` 拦下） |
| `func` | 本地工具的可调用对象；给了就按身份缓存取 schema |
| `schema` | 已解析好的 schema（TSP 远端路径用）。**给了就不再用 `func`** |

返回值：通过返回 `None`；不通过返回结构化失败返回体 `{"success": False, "error_code": "invalid_tool_args", "message": <字段级>}`。schema 不可用时也返回 `None`（warning 已由取 schema 的函数记过）。

| 路径 | schema 来源 | `null` 豁免 |
| --- | --- | --- |
| 本地（`func=`）| 本仓库 `python_type_to_json_schema`（#195 起 TypeAdapter）| **不豁免**，严格 |
| 外部（`schema=`）| 调用方/对端传入（目前唯一调用方 `tspDriver._execute_tsp_tool`）| **保留** `_is_ignorable_null` |

外部 schema 的生成器不归本仓库管：实测 `gtsp` v0.4.19 的 39 个 property 里 **schema 含 `null` 的 0 个**，其中 26 个「非必填但无 `null`」中有 14 个在 `gtsp` 本体上真的 ACCEPT `null` ⇒ 对它严格会**误杀对端合法写法**（`read_file{encoding: null}`、`grep_search{path: null}` 基线放行、交付曾全拒，即 38 报的阻断项 B1）。

⚠️ **分流按来源，不按「字段是否必填」。** 后者是本单的病根：必填与否决定「键能否省略」，注解是否含 None 决定「值能否为 null」，两者正交。
