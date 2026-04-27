# V19: HTTP API 鉴权 - 产品文档

## 目标

为后端 HTTP API 与 WebSocket 接口提供可选的鉴权能力，防止未授权访问。启用鉴权后，前端访问 API 时需要携带有效的访问 token，否则请求会被拒绝。

此前后端服务完全开放，任何能访问服务端口的人都可以调用 API、查看数据、操控 Agent。V19 之后，用户可通过配置文件开启鉴权，设置访问 token，前端在连接时自动携带 token，未授权的请求会被统一拦截并返回明确的错误提示。

---

## 功能特性

### 一、可选启用

- **开关配置**：鉴权功能通过 `setting.json` 中的 `auth.enabled` 字段控制，默认关闭。
- **向后兼容**：未配置鉴权字段或 `enabled: false` 时，行为与 V18 完全一致，所有 API 无需认证即可访问。
- **零侵入启动**：鉴权关闭时，相关检查逻辑完全跳过，不影响现有运行逻辑。

### 二、Token 认证

- **直接携带 token**：前端每次请求直接携带 token，无需登录换取临时凭证的中间步骤。
- **Token 存储**：token 存储在浏览器 localStorage 中，页面刷新后自动恢复，无需重复输入。
- **无状态验证**：后端直接验证 token 是否与配置文件中预设的 token 一致。

### 三、全接口覆盖

- **HTTP API 全覆盖**：启用鉴权后，除系统状态接口外的所有 HTTP API 都需要携带正确 token。
- **WebSocket 鉴权**：WebSocket 连接建立后，前端发送认证消息，后端验证后才订阅事件推送。
- **静态资源豁免**：Vite 代理的静态资源（`/`、`/assets/*`）不需要鉴权，仅 API 路径（`/*.json`、`/ws/*`）受保护。

### 四、前端自动认证

- **Token 输入弹窗**：前端检测到鉴权启用且无有效 token 时，自动弹出 token 输入界面。
- **自动携带**：用户输入 token 后，前端自动在所有 HTTP 请求头中携带 token，WebSocket 连接时发送认证消息。
- **Token 持久化**：token 存储在浏览器 localStorage 中，页面刷新后自动恢复，无需重复输入。
- **错误自动提示**：token 错误时，后端返回统一错误码，前端自动弹出输入界面重新认证。

### 五、统一错误响应

- **错误码标准化**：鉴权失败统一返回 HTTP 401 状态码，响应体包含 `error_code: "auth_required"` 或 `error_code: "auth_invalid"`。
- **错误描述友好**：响应体包含中文错误描述，前端可直接展示给用户。
- **WebSocket 断开提示**：WebSocket 鉴权失败时，连接立即关闭，前端通过错误事件感知并提示用户重新输入 token。

---

## 用户价值

### 1. 远程访问安全

当服务部署在可被外部访问的环境（如云服务器、局域网共享）时，鉴权能力防止陌生人随意访问数据、操控 Agent 或窃取配置信息。

### 2. 配置简单直观

单一 token 方案不需要理解复杂的用户-角色权限模型，只需在配置文件中设置一个 token 即可启用鉴权，适合个人或小团队快速上手。

### 3. 前端体验流畅

用户只需在首次访问或 token 错误时输入一次，日常使用过程中认证完全透明，不会打断正常操作流程。

---

## 核心概念

### 鉴权（Authentication）

验证请求来源是否拥有访问权限的过程。V19 采用固定 token 认证，用户输入 token 后，前端在每次请求中携带 token 即可通过验证。

### HTTP API 认证

token 通过 `Authorization: Bearer <token>` header 传递。后端检查 header 中的 token 是否与配置文件中预设的 token 一致。

### WebSocket 认证

WebSocket 连接建立后，前端发送一条包含 token 的认证消息 `{type: "auth", token: "xxx"}`。后端收到后验证 token，验证成功后才订阅事件推送。token 不在 URL 中传递，避免被日志记录。

---

## 效果演示

### 配置示例

```json
// setting.json
{
  "auth": {
    "enabled": true,
    "token": "my_access_token"
  }
}
```

### Token 输入界面

```text
┌────────────────────────────────────────────┐
│        TeamAgent - 请输入访问 Token         │
│                                            │
│   Token：[________________]               │
│                                            │
│           [确定]  [取消]                   │
│                                            │
│   提示：Token 可在 setting.json 中查看     │
└────────────────────────────────────────────┘
```

### 鉴权失败响应

```json
// HTTP 401
{
  "error_code": "auth_invalid",
  "error_desc": "Token 无效"
}
```

---

## 产品边界

### V19 包含

- 后端 HTTP API 鉴权检查（除系统状态接口外）
- WebSocket 连接鉴权检查（通过认证消息）
- 配置文件控制鉴权开关与 token
- 前端 token 输入界面与 token 自动携带
- 统一的鉴权错误响应格式

### V19 不包含

- 多用户与角色权限体系（仅单一 token）
- token 的加密存储（明文存储在配置文件）
- TUI 前端的鉴权支持（TUI 假定本地使用，不强制鉴权）
- 第三方 OAuth / LDAP 等外部认证集成

---

## 验收标准

### 配置与开关

- `setting.json` 中未配置 `auth` 字段时，所有 API 无需 token 可访问
- `auth.enabled: false` 时，所有 API 无需 token 可访问
- `auth.enabled: true` 时，除系统状态接口外所有 API 需要 token

### HTTP API 鉴权

- 携带正确 token 的请求正常响应
- 不携带 token 的请求返回 401 + `auth_required` 错误
- 携带错误 token 的请求返回 401 + `auth_invalid` 错误

### WebSocket 鉴权

- WebSocket 发送正确 token 的认证消息后正常接收事件
- WebSocket 未发送认证消息时连接被关闭
- WebSocket 发送错误 token 时连接被关闭

### 前端体验

- 鉴权启用时，前端自动弹出 token 输入界面
- 用户输入 token 后，后续请求自动携带 token
- token 错误后，前端自动弹出输入界面重新认证
- 鉴权关闭时，前端不显示输入界面，直接进入正常操作

### 错误处理

- 鉴权错误统一返回 401 状态码
- 错误响应包含 `error_code` 和 `error_desc` 字段
- 前端能正确解析并展示鉴权错误信息