# Service 依赖关系图

```mermaid
graph TD
    main --> route
    main --> scheduler_service
    main --> agent_service
    main --> room_service
    main --> llm_service
    main --> func_tool_service
    main --> message_bus

    route --> controller
    controller --> agent_service
    controller --> room_service
    controller --> scheduler_service
    controller --> ws_controller

    scheduler_service --> agent_service
    scheduler_service --> room_service
    scheduler_service -->|subscribe| message_bus

    agent_service --> llm_service
    agent_service --> room_service
    agent_service --> func_tool_service

    room_service -->|publish| message_bus
```

## 说明

| 模块层级 | 角色 | 依赖 |
|---------|------|------|
| `main` | 程序入口，初始化所有服务并启动 Tornado 服务器 | route / scheduler_service / service.* |
| `route / controller` | Web API 层，处理 HTTP 请求与 WebSocket 推送 | service.* |
| `scheduler_service` | 顶层调度，驱动所有 Agent 轮次 | agent_service / room_service / message_bus |
| `agent_service` | 管理 Agent 实例，执行一轮发言（含 tool call 循环） | llm_service / room_service / func_tool_service |
| `room_service` | 管理聊天室和轮次状态 | message_bus |
| `llm_service` | 封装 LLM API 调用 | 无 |
| `func_tool_service` | 管理和执行工具函数 | 无 |
| `message_bus` | 发布/订阅事件总线 | 无 |
```
