# V19: HTTP API 鉴权 - 技术文档

## 1. 方案概览

固定 token 认证方案：
- 配置文件设置 `auth.enabled` 和 `auth.token`
- HTTP API：请求头携带 `Authorization: Bearer <token>`
- WebSocket：连接后发送 `{type: "auth", token: "xxx"}` 认证消息

---

## 2. 配置模型

`setting.json` 新增 `auth` 字段：

```json
{
  "auth": {
    "enabled": true,
    "token": "my_access_token"
  }
}
```

默认 `enabled: false`，向后兼容。

---

## 3. HTTP API 鉴权

### 3.1 检查逻辑

`BaseHandler.prepare()` 中新增鉴权检查：

1. 检查 `auth.enabled`，未启用则跳过
2. 检查路径是否豁免（`/system/status.json` 豁免）
3. 从 `Authorization` header 获取 token（仅支持 header 方式）
4. 比对 token 是否与配置一致
5. 不一致则返回 401 + 错误码

### 3.2 错误响应

```json
// 401
{
  "error_code": "auth_required",  // 或 "auth_invalid"
  "error_desc": "请输入访问 Token"
}
```

---

## 4. WebSocket 鉴权

### 4.1 认证流程

1. 前端建立 WebSocket 连接
2. 前端发送认证消息 `{type: "auth", token: "xxx"}`
3. 后端收到消息后验证 token
4. 验证成功：订阅事件推送
5. 验证失败：关闭连接（code 1008）

### 4.2 为什么不在 URL 中传 token

URL 参数会被日志记录、浏览器历史保存，存在泄露风险。通过消息发送更安全。

---

## 5. 前端逻辑

### 5.1 Token 管理

- 存储在 `localStorage`（key: `teamagent_token`）
- 页面刷新后自动恢复

### 5.2 HTTP 请求

- 自动在 header 中携带 token
- 收到 401 时弹出 token 输入界面

### 5.3 WebSocket 连接

- 连接建立后发送认证消息
- 连接被关闭时（认证失败）弹出 token 输入界面

### 5.4 启动检测

调用 `/system/status.json` 检查 `auth_enabled` 字段：
- `true` 且无 token：弹出输入界面
- `false`：直接进入正常操作

---

## 6. 后端改动清单

| 模块 | 改动 |
|------|------|
| `configTypes.py` | 新增 `AuthConfig`，`SettingConfig` 新增 `auth` 字段 |
| `configUtil.py` | 保存配置时同步写入 `auth` |
| `baseController.py` | `prepare()` 新增鉴权检查逻辑 |
| `wsController.py` | `on_message()` 处理认证消息，认证后才订阅 |
| `systemController.py` | 响应新增 `auth_enabled` 字段 |

---

## 7. 前端改动清单

| 模块 | 改动 |
|------|------|
| `authStore.ts` | 新增：token 存取函数 |
| `api.ts` | `requestJson()` 自动携带 token，401 触发输入 |
| `runtimeStore.ts` | WebSocket 连接后发送认证消息 |
| `TokenDialog.vue` | 新增：token 输入界面 |
| `App.vue` | 启动时检测鉴权状态 |
| i18n 文件 | 新增 auth 相关文案 |

---

## 8. 测试要点

- 未启用鉴权：所有 API 正常访问
- 启用鉴权 + 无 token：401 `auth_required`
- 启用鉴权 + 错误 token：401 `auth_invalid`
- 启用鉴权 + 正确 token：正常响应
- `/system/status.json` 始终豁免
- WebSocket 认证成功才接收事件
- 前端 token 持久化，刷新后自动恢复