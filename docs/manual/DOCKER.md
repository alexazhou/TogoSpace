# Docker 部署指南

## 快速启动

```bash
# 1. 初始化前端子模块
git submodule update --init --recursive

# 2. 构建镜像
docker build -t togospace:0.3.8 .

# 3. 启动容器
docker run -d \
  --name togospace \
  -p 7180:7180 \
  -v togospace-storage:/storage \
  togospace:0.3.8

# 4. 查看日志
docker logs -f togospace

# 5. 停止容器
docker stop togospace && docker rm togospace
```

## 配置

### 挂载自定义配置文件

```bash
docker run -d \
  --name togospace \
  -p 7180:7180 \
  -v /path/to/config:/storage \
  togospace:0.3.8
```

配置文件格式参考 `assets/docs/setting.README.md`。

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TOGO_PORT` | 监听端口 | `7180` |
| `TZ` | 时区 | `Asia/Shanghai` |

## 数据持久化

容器使用 `/storage` 作为数据存储目录，运行时会自动创建：

- `/storage/setting.json` - 运行配置
- `/storage/data/` - SQLite 数据库
- `/storage/logs/` - 日志文件
- `/storage/workspace/` - Agent 工作目录

建议使用 Docker Volume 持久化：

```bash
docker volume create togospace-storage
docker run -d -p 7180:7180 -v togospace-storage:/storage togospace:0.3.8
```

## 访问服务

启动后访问：http://localhost:7180

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
