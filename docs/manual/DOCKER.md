# Docker 部署指南

## 快速启动

```bash
# 1. 拉取镜像
docker pull ghcr.io/alexazhou/togospace:latest

# 2. 创建数据目录
mkdir -p ~/togospace-data

# 3. 启动容器
docker run -d \
  --name togospace \
  -p 7180:7180 \
  -v ~/togospace-data:/storage \
  ghcr.io/alexazhou/togospace:latest
```

启动后访问：http://localhost:7180

## 数据目录

容器内使用 `/storage` 存储所有运行数据。Docker 容器的文件系统是临时的，`-v ~/togospace-data:/storage` 将本地目录挂载到容器内，保证数据在容器删除后不丢失。

目录结构：

```
~/togospace-data/
├── setting.json        # 运行配置
├── data/               # SQLite 数据库
├── logs/               # 日志文件
└── workspace/          # Agent 工作目录
```

首次启动时，容器会自动生成默认配置。如需自定义，可在启动前将 `setting.json` 放入目录：

```bash
cp /path/to/your/setting.json ~/togospace-data/setting.json
```

配置文件格式参考 `assets/docs/setting.README.md`。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TOGO_PORT` | 监听端口 | `7180` |
| `TZ` | 时区 | `Asia/Shanghai` |

## 常用操作

```bash
# 查看日志
docker logs -f togospace

# 查看健康状态
docker inspect --format='{{.State.Health.Status}}' togospace

# 停止并删除容器
docker stop togospace && docker rm togospace
```

## 更新镜像

```bash
# 1. 拉取最新镜像
docker pull ghcr.io/alexazhou/togospace:latest

# 2. 停止并删除旧容器
docker stop togospace && docker rm togospace

# 3. 用相同参数重新启动（数据目录不变，数据不会丢失）
docker run -d \
  --name togospace \
  -p 7180:7180 \
  -v ~/togospace-data:/storage \
  ghcr.io/alexazhou/togospace:latest
```

## 从源码构建

如果需要从源码构建镜像：

```bash
# 1. 初始化前端子模块
git submodule update --init --recursive

# 2. 构建镜像
docker build -t togospace:latest .

# 3. 启动容器
docker run -d \
  --name togospace \
  -p 7180:7180 \
  -v ~/togospace-data:/storage \
  togospace:latest
```

自定义构建参数：

```bash
docker build \
  --build-arg PYTHON_VERSION=3.12 \
  --build-arg NODE_VERSION=20 \
  -t togospace:custom .
```
