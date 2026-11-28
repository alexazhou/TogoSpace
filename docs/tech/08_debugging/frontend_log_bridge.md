# 前端日志桥（#197 / #359 搬迁）

> 本文是 `src/util/frontendLog.py`（后端接收端）与 `frontend/src/utils/frontendLogBridge.ts`（前端发送端）两个模块头注释的原文搬迁（2026-11-25，#359 压缩注释：38 行 → 5 行、34 行 → 5 行）。
> 代码里只留「开关关着也会多出空文件 / 浏览器侧根本没有落库能力」这两条现象与本文指针。

## 1. 为什么需要

前端（桌面端 / 浏览器）运行时的 log 拿不到、无法落库，排查前端问题只能靠人肉看 devtools。本模块让后端把前端日志落成本地文件，事后可取。

两份文件的路径：

| 形态 | 前端组件 | 落盘文件 |
| --- | --- | --- |
| 桌面壳视角 | `desktopLog.ts`（仅桌面形态） | `{STORAGE_ROOT}/logs/desktop/desktop.log` |
| 后端收到的视角 | `frontendLogBridge.ts`（**桌面 + 浏览器都装**）→ ws → `frontendLog.py` | `{STORAGE_ROOT}/logs/backend/frontend/frontend.log` |

## 2. 与 `desktopLog.ts` 的分工（不重复、不互相替代）

- `desktopLog.ts`：**仅桌面形态**，`warn`/`error`/全局异常恒定转发到 Tauri 外壳，落到 `{STORAGE_ROOT}/logs/desktop/desktop.log`。它解决的是「外壳与后端启动时序」这类只在桌面端有的问题。
- 本模块：**桌面与浏览器都装**，把日志经 ws 送到**后端**，落到 `{STORAGE_ROOT}/logs/backend/frontend/frontend.log`。它解决的是 Operator 的原需求：「web 跑在浏览器里也要能转发」—— 浏览器形态没有 Tauri 外壳，`desktopLog.ts` 在那一侧直接 return，等于完全没有落库能力。

桌面形态下 `warn`/`error` 会同时出现在两个文件里。这是刻意的：`desktop.log` 记录「外壳视角看到了什么」，`frontend.log` 记录「后端收到了什么」，排查转发链路本身的问题时两者对比才有信息量。本模块**不修改 `desktopLog.ts` 的任何行为**。

## 3. 开关（两道，缺一不可）

1. **前端侧**（由 `setFrontendLogBridgeEnabled` 置位）：桌面来自 Rust 注入的 `window.tgconfig.frontendDebug`，浏览器来自 `/system/status.json` 的 `frontend_debug`。关着就连 ws 报文都不发（省流量）。
2. **后端侧 `TOGOSPACE_FRONTEND_DEBUG`**（与 Tauri 外壳注入 `window.tgconfig.frontendDebug` 用的是同一个变量）：**后端**据此决定是否真的写盘。`/system/status.json` 也把它暴露成 `frontend_debug` 字段，前端据此决定是否发送。

**为什么后端必须再判一次**：只靠前端自觉等于没有门控 —— 任何能连上 ws 的客户端都能往盘上写。后端这一道是真正的闸门，前端那道只是省流量，**不是**安全边界。

前端侧有三态（`pending` / `enabled` / `disabled`）：未知态只攒队列、不发报文，拿到权威答案后再决定 flush 还是丢弃。

## 4. 为什么不让启动早期的日志丢

ws 连上通常晚于首屏脚本执行，而排查白屏最需要的恰恰是最早那几行。所以未就绪时日志进环形队列（`MAX_QUEUE`），连接就绪后一次性 flush。

## 5. 写什么、不写什么

- `warn` / `error` **和** `log` 都接收（开关打开时全量才有排查价值），级别由前端报文带上来。
- 单条消息在**后端**再截断一次（`MAX_MESSAGE_CHARS`）：不信任客户端，防止一条超长日志把文件撑爆。
- 单条长度、总量上限由既有 `RotatingFileHandler`（100MB × 3）承担，不新造轮子。

## 6. 文件何时出现

**首条日志落盘时才创建**（`logUtil` 里该条目带 `lazy=True`，不在 `init_backend_logger` 阶段建文件）。原因很直接：`RotatingFileHandler` 一构造就建文件，若在启动时就挂上，开关关着的机器上也会凭空多出一个空 `frontend.log` —— 而交付标准要求「开关关闭时无该文件」。所以建文件这件事只能发生在「开关已确认为开 + 确实有一条要写」之后。

## 7. 时间戳

前端报文可以带 `ts`（浏览器侧 `Date.now()`），写盘时**两者都留**：后端收到的时间（writer 的 `asctime`）用来对齐后端其他日志，前端时间用来判断「是前端卡住了还是后端收晚了」—— 这类差异恰恰是排查白屏/卡顿的关键，只留一个就看不出来了。
