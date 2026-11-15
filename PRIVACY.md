# Privacy Policy / 隐私政策

Togobot（TogoSpace）是一款**本地运行**的桌面/自托管应用：你的数据默认只存在你自己的机器上。

This document is intentionally bilingual so that both users and code-signing reviewers can read the exact same text.

- 适用范围 / Scope: the Togobot desktop application (Tauri shell + bundled Python backend), the web console it serves, and the Docker/self-hosted deployment of the same code in this repository.
- 最后更新 / Last updated: 2026-11-15

## 0. 摘要 / Summary

**不收集、不上报用户数据；启动时会向 GitHub 查询版本更新（不携带用户身份信息），可在设置中关闭。**

**We do not collect or upload user data. At startup the app asks GitHub for the latest release version — no identity information is sent — and this can be disabled in Settings.**

下面是这句话的代码依据与边界，逐条可核。

---

## 1. 我们不做什么 / What we do NOT do

代码事实（可自行 grep 核对）：

- **没有遥测、没有统计、没有崩溃上报，也不收集、不上报用户数据**。仓库里不存在任何 analytics / telemetry 依赖或上报代码：
  `grep -rn "telemetry\|analytics\|sentry\|bugsnag\|mixpanel\|amplitude\|posthog" src/ api/ frontend/src/` 返回空。
- **没有账号体系**，不收集邮箱、手机号、设备指纹、安装 ID。
- **不上传你的对话、文件、任务、Agent 配置**。它们存在本机
  （`~/.togospace/` 下的 sqlite 数据库与工作区目录，见 `src/appPaths.py:56-67`）。
- **没有自动下载/自动执行远程内容**：仓库内 `src/` 无 `download`/`urlretrieve` 之类的自动下载路径。

## 2. 会发生的对外网络请求 / Outbound network requests

应用只有以下四类出站请求，全部可指认到代码位置：

| # | 触发方式 | 目标 | 发送内容 | 代码位置 |
|---|---|---|---|---|
| 1 | 启动时自动检查更新（**可在设置里关闭**，配置项 `auto_check_update`，默认开启） | `https://api.github.com/repos/alexazhou/TogoSpace/releases/latest`（GitHub Releases API） | 仅一个固定 `User-Agent: Togobot-UpdateChecker`；不带任何用户数据、标识或统计 | `src/service/updateService.py:15`、`:89-98`；默认值 `src/util/configUtil/configTypes.py:266`；开关接口 `src/controller/systemController.py:92-96` |
| 2 | 你**配置了**某个 LLM 服务商并使用对话/任务功能 | 你填的 `base_url`（`llm_providers` 默认为**空列表**，未配置就不发任何请求） | 你发给该服务商的对话内容、上下文与你的 API Key | `src/util/configUtil/configTypes.py:258`（`llm_providers` 默认空）、`src/util/llmApiUtil/client.py:269`（流式）、`:340`（非流式） |
| 3 | Agent 使用「网页抓取」工具时 | 它要抓取的那个网址 | 你/Agent 指定的 URL 与常规 HTTP 头 | `src/service/funcToolService/webFetchTool.py:464` |
| 4 | 你在设置里填入**自己的**第三方搜索 API Key 并主动测试/使用 | `https://api.deepseek.com/...` 或 `https://api.xiaomimimo.com/...` | 你的搜索 query 与你的 API Key | `src/service/thirdPartyService/deepseekService.py:18`、`xiaomiMimoService.py:18`，调用方 `src/controller/thirdPartyController.py:82`、`:100` |

说明：

- 第 2–4 类都是**你主动配置或主动触发**的，请求直达你选的服务商；我们不经过任何中转服务器，也没有我们的服务器可中转。
- 第 1 类若你不需要，可在应用「设置 → 检查更新」关闭，或在 `setting.json` 里把 `auto_check_update` 设为 `false`。

## 3. 你的数据存在哪 / Where your data lives

- 数据库：`~/.togospace/data/data.db`（sqlite）；配置：`~/.togospace/setting.json`；工作区与产出：`~/.togospace/workspace/...`（见 `src/appPaths.py:56-67`）。
- 卸载应用**不会**自动删除上述目录；要彻底清除请手动删除 `~/.togospace/`。
- 日志写在本地文件，不上传。

## 4. 第三方组件 / Third-party components

应用内嵌的第三方开源组件遵循各自许可证；仓库 `docs/thirdparty_doc/` 与依赖清单（`requirements.txt`、`frontend/package.json`、`tauri/src-tauri/Cargo.toml`）可查。这些组件不在后台向外发送数据；上面第 2–4 类请求是应用功能本身产生的。

## 5. 儿童 / Children

本应用不是面向儿童的产品，也不会有意收集儿童信息。

## 6. 变更与联系 / Changes and contact

本政策若变更会更新本文件顶部日期。问题请开 issue：<https://github.com/alexazhou/TogoSpace/issues>。
