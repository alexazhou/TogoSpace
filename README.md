# Agent Team

一个基于 Tornado 和 Textual 的多智能体协作框架。

## 核心特性
- **四层架构**：Controller, Service, Model, Util。
- **混合界面**：支持 Textual TUI 和 Vue 3 Web Frontend。
- **持久化**：基于 SQLite 和迁移系统的状态持久化。
- **多智能体调度**：支持 Private (1v1) 和 Group (多 Agent) 房间。

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 初始化数据库
```bash
python src/db.py migrate
```

### 3. 启动后端
```bash
./scripts/start_backend.sh
```

### 4. 启动 TUI 界面
```bash
./scripts/start_tui.sh
```

## 开发规范
- 详见 `CLAUDE.md` 获取详细的开发指南和指令。
- 命名规范：服务层统一使用 `camelCase`。
- 运行测试：`pytest`。
- 类型检查：`./scripts/run_mypy.sh`。
