# Docker 部署指南

## 快速启动

### 使用 Docker Compose（推荐）

```bash
# 1. 初始化前端子模块
git submodule update --init --recursive

# 2. 启动服务
docker-compose up -d

# 3. 查看日志
docker-compose logs -f

# 4. 停止服务
docker-compose down
```

### 使用 Docker 命令

```bash
# 1. 初始化前端子模块
git submodule update --init --recursive

# 2. 构建镜像
docker build -t togospace:0.1.20 .

# 3. 启动容器
docker run -d \
  --name togospace \
  -p 8080:8080 \
  -v togospace-data:/data \
  togospace:0.1.20

# 4. 查看日志
docker logs -f togospace

# 5. 停止容器
docker stop togospace && docker rm togospace
```

## 配置

### 挂载自定义配置文件

```bash
# 创建配置文件
mkdir -p /path/to/config
cat > /path/to/config/setting.json << 'EOF'
{
  "language": "zh-CN",
  "default_llm_server": "qwen",
  "llm_services": [
    {
      "name": "qwen",
      "enable": true,
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "YOUR_API_KEY_HERE",
      "type": "openai-compatible",
      "model": "qwen-plus"
    }
  ]
}
EOF

# 使用自定义配置启动
docker run -d \
  --name togospace \
  -p 8080:8080 \
  -v /path/to/config:/data \
  togospace:0.1.20
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TZ` | 时区 | `Asia/Shanghai` |
| `STORAGE_ROOT` | 数据存储目录 | `/data` |

## 数据持久化

Docker 镜像使用 `/data` 作为数据存储目录，运行时会自动创建：

- `/data/setting.json` - 运行配置
- `/data/data/` - SQLite 数据库
- `/data/logs/` - 日志文件
- `/data/workspace/` - Agent 工作目录

建议使用 Docker Volume 持久化：

```bash
docker volume create togospace-data
docker run -d -p 8080:8080 -v togospace-data:/data togospace:0.1.20
```

## 访问服务

启动后访问：http://localhost:8080

- Web Console: http://localhost:8080/
- API 文档: 参考 `assets/setting.README.md`

## 健康检查

容器内置健康检查，可通过以下命令查看状态：

```bash
docker inspect --format='{{.State.Health.Status}}' togospace
```

## 构建参数

如需自定义构建，可使用以下参数：

```bash
docker build \
  --build-arg PYTHON_VERSION=3.12 \
  --build-arg NODE_VERSION=20 \
  -t togospace:custom .
```