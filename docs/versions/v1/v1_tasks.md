# V1: 双 Agent 单房间聊天 - 开发任务表

## 任务概览

将 V1 版本开发拆分为 7 个主要任务，按依赖关系排序。

---

## 任务列表

### 任务 1: 创建目录结构

**描述**: 创建 V1 版本所需的目录结构

**依赖**: 无

**子任务**:
- [ ] 创建 `config/` 目录
- [ ] 创建 `prompts/` 目录
- [ ] 创建 `src/core/` 目录
- [ ] 创建 `src/api/` 目录
- [ ] 创建 `logs/` 目录

**验收标准**:
- 所有目录创建成功
- 目录结构与技术文档中定义的一致

---

### 任务 2: 创建配置文件

**描述**: 创建 Agent 配置和 Prompt 文件

**依赖**: 任务 1

**子任务**:
- [ ] 创建 `config/agents_v1.json`
- [ ] 创建 `prompts/alice_system.md`
- [ ] 创建 `prompts/bob_system.md`

**验收标准**:
- `agents_v1.json` 包含两个 Agent 配置（Alice 和 Bob）
- 每个 Agent 有正确的 name、prompt_file 和 model 字段
- prompt 文件内容完整

---

### 任务 3: 实现 Message 数据类

**描述**: 在 `src/core/chat_room.py` 中实现 Message 数据类

**依赖**: 任务 1

**文件**: `src/core/chat_room.py`

**子任务**:
- [ ] 导入 dataclass 和 typing 模块
- [ ] 创建 Message dataclass（sender, content, timestamp）

**验收标准**:
- Message 类能正确序列化和反序列化
- 包含所有必需字段

---

### 任务 4: 实现 ChatRoom 类

**描述**: 实现 ChatRoom 核心功能

**依赖**: 任务 3

**文件**: `src/core/chat_room.py`

**子任务**:
- [ ] 实现 ChatRoom 类的 __init__ 方法
- [ ] 实现 add_message 方法
- [ ] 实现 get_context 方法（支持 max_messages 参数）
- [ ] 实现 format_log 方法

**验收标准**:
- 能创建聊天室实例
- 消息能正确添加并记录时间戳
- get_context 能返回最近 N 条消息的上下文字符串
- format_log 能格式化输出完整聊天记录

---

### 任务 5: 实现 Agent 类

**描述**: 实现 Agent 类及其响应生成逻辑

**依赖**: 任务 1

**文件**: `src/core/agent.py`

**子任务**:
- [ ] 创建 Agent 类的 __init__ 方法
- [ ] 实现 generate_response 异步方法
- [ ] 在方法中构建 API 请求消息数组
- [ ] 调用 API 客户端获取响应

**验收标准**:
- Agent 能正确初始化（name, system_prompt, model）
- generate_response 是异步函数
- 返回模型生成的回复文本

---

### 任务 6: 实现或复用 API 客户端

**描述**: 复用或调整现有的 API 客户端

**依赖**: 无

**文件**: `src/api/client.py`

**子任务**:
- [ ] 检查现有代码中的 API 调用逻辑
- [ ] 提取或创建 APIClient 类
- [ ] 实现 call_chat_completion 方法

**验收标准**:
- APIClient 能发起异步 HTTP 请求
- 支持 model 和 messages 参数
- 返回结构化的响应数据

---

### 任务 7: 实现主程序

**描述**: 实现主程序逻辑和调度

**依赖**: 任务 2, 任务 4, 任务 5, 任务 6

**文件**: `src/main.py`

**子任务**:
- [ ] 实现配置加载函数 load_config
- [ ] 实现 Prompt 加载函数 load_prompt
- [ ] 创建 ChatRoom 实例
- [ ] 创建 APIClient 实例
- [ ] 从配置创建 Agent 实例列表
- [ ] 添加初始话题到聊天室（如果有）
- [ ] 实现异步主循环和调度逻辑
- [ ] 处理异常并记录日志

**验收标准**:
- 程序能正确加载所有配置
- Agent 轮流对话
- 每轮对话都有日志输出
- 对话结束后输出完整聊天记录
- 程序能正常退出

---

## 任务依赖关系图

```
任务 1 (目录结构)
    ├─ 任务 2 (配置文件)
    ├─ 任务 3 (Message 类)
    │       └─ 任务 4 (ChatRoom 类)
    └─ 任务 5 (Agent 类)
    └─ 任务 6 (API 客户端)
            └─ 任务 7 (主程序) ← 需要任务 2, 4, 5, 6 都完成
```

---

## 开发顺序建议

**推荐顺序**: 任务 1 → 任务 2 → 任务 3 → 任务 4 → 任务 5 → 任务 6 → 任务 7

**并行开发机会**:
- 任务 3, 5, 6 可以并行开发（都只依赖任务 1）
- 任务 4 依赖任务 3
- 任务 7 需要等待所有其他任务完成

---

## 测试检查清单

- [ ] 配置文件格式正确且可解析
- [ ] Prompt 文件能正确读取
- [ ] Message 类实例化正常
- [ ] ChatRoom 能正确添加和获取消息
- [ ] Agent 能初始化并持有正确的配置
- [ ] API 客户端能成功调用 DashScope API
- [ ] 主程序能完整运行并输出日志
- [ ] Alice 表现出热情的性格特征
- [ ] Bob 表现出内敛工程师的性格特征
- [ ] 对话轮次符合配置
- [ ] 程序能正常退出

---

## 验收标准（最终）

- [ ] Alice 和 Bob 能完成配置的轮次对话
- [ ] 每个 Agent 表现出配置的性格特征
- [ ] 对话记录完整保存到日志输出
- [ ] 使用 asyncio 实现调度逻辑
- [ ] 程序能正常退出
