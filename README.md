# Team Agent Frontend

基于 `Vue 3 + Vite + TypeScript` 的网页前端，功能对齐当前仓库内的 `tui/`：

- 房间列表与未读数
- Agent 状态面板
- 消息气泡展示
- 私聊房间发送消息
- WebSocket 实时消息与状态更新
- 断线自动重连

## 开发

```bash
npm install
npm run dev
```

默认通过 Vite 代理访问本地后端 `http://127.0.0.1:8180`。

如果需要直连其他后端地址，可以设置：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8180 npm run dev
```

## 构建

```bash
npm run build
```
