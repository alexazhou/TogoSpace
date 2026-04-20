![ToGo Agent](image/togo_agent_team.png)

# ToGo Agent 🚀

[English](README.md) | [中文](README_CN.md)

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/framework-Tornado-orange.svg)](https://www.tornadoweb.org/)
[![UI](https://img.shields.io/badge/UI-Textual%20%2B%20Vue3-green.svg)](https://textual.textualize.io/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#)

**ToGo Agent** 是一款专为大语言模型（LLM）设计的**多智能体协作开源软件**。让多个 AI Agent 能够像人类团队一样自由交流、实时协作，共同攻克复杂任务。

> **关于名字的由来**：项目命名灵感源自 1925 年诺姆血清接力中的传奇雪橇犬 **Togo**。在那个极其恶劣的冬天，Togo 带领团队完成了整场接力中最长且最危险的一段航程。我们引用这个名字，旨在致敬那种无畏艰险、使命必达的协作精神，这也正是 ToGo Agent 想要赋予多智能体团队的核心特质。

---

## ✨ 核心特性

### 1. 真正的团队协作
多 Agent 在统一的群聊空间内自由发言、互相启发、补位配合，模拟真实人类团队的沟通模式，通过协作产生 1+1>2 的效果。

![Chat Interface](image/cn/chat.png)

### 2. 自由定义的 Agent 人格
你可以随心所欲地定义每个 Agent 的角色定位、专业技能与性格色彩。无论是严谨的代码审查专家，还是充满创意的产品策划，都能在你的定制下跃然纸上，打造专属的 AI 梦之队。

### 3. 告别繁琐的工作流编排
无需事先规划死板的流程图。得益于强大的调度逻辑，Agent 们能根据当前任务进展自主决定“下一步该谁上”，广泛适用于各种突发、多变的复杂任务场景。

### 4. 强大的多层级团队架构
支持多部门、多层级的组织架构管理。你可以像管理真实公司一样划分部门（Dept），应对海量 Agent 参与的超大型复杂工程任务。

![Team Editor](image/cn/team_editor.png)

### 5. 全程可视化的友好体验
告别冰冷的黑盒运行。配备现代化的 Web 前端，从团队角色配置到 Agent 的每一个思考步骤、每一条消息流向，全部实时可视化呈现，对人类用户极度友好。

### 6. 极致的跨平台兼容性
基于 Python 与 modern 前端技术构建，完美支持 macOS、Windows 与 Linux 操作系统，随时随地开启你的 AI 协作之旅。

---

## 🚀 快速开始

### 1. 快速体验 (推荐)
目前我们已提供 **macOS** 版本的 Release 包，下载即可快速体验。
- **下载地址**：[点击前往 Releases 页面下载](https://github.com/your-repo/togo-agent/releases)
- **使用说明**：下载并运行后，ToGo Agent 将常驻于你的系统状态栏。点击状态栏图标即可开启控制台、管理团队或执行任务。

![Status Bar Entry](image/cn/entry.png)

### 2. 开发者安装 (源码运行)
```bash
# 克隆仓库
git clone https://github.com/your-repo/togo-agent.git
cd togo-agent

# 安装依赖
pip install -r requirements.txt
```

### 2. 初始化与配置
```bash
# 执行数据库迁移
python src/db.py migrate

# (可选) 参考 assets/config_template.json 完善你的 LLM API 配置
```

### 3. 启动项目
```bash
# 1. 启动后端服务
./scripts/start_backend.sh

# 2. 启动 Web 控制台 (需进入 frontend 目录)
cd frontend && npm install && npm run dev

# 3. Coming Soon TUI 终端交互界面 (Coming Soon)
# ./scripts/start_tui.sh
```

---

## 📂 项目结构

- `src/`: 后端核心逻辑，包含 Agent 调度、驱动与持久化。
- `frontend/`: 基于 Vue 3 + TypeScript 的可视化控制台。
- `tui/`: Coming Soon 基于 Textual 的高性能终端交互界面 (Coming Soon)。

- `docs/`: 包含架构设计、调度逻辑、任务生命周期等深度文档。
- `assets/`: 预设的角色模板、团队配置与多语言支持。

---

## 🛠️ 故障排除 (Troubleshooting)

1. **进入设置页面**：后台设置页面的入口位于页面左上角的**齿轮图标**。
2. **模型重复发言**：如果出现模型重复发言的情况，可能是因为**思考模式 (Thought)** 未开启，请在**高级设置**中注入思考模式配置。
3. **Agent 调用失败或中止**：如果 Agent 调用 LLM 失败或因其他错误停止运行，可以点击页面左下角的 **Agent 卡片**，然后点击**重试**按钮。
4. **数据异常修复**：如果遇到数据异常导致无法运行，可以在后台设置页面点击**清除团队数据**来修复。

---

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源。
