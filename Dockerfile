# TogoAgent Dockerfile
# 基于 Ubuntu 24.04 LTS 构建
#
# 构建方式：
#   1. 确保 frontend 子模块已初始化：git submodule update --init --recursive
#   2. docker build -t togoagent:0.1.20 .
#   3. docker run -d -p 8080:8080 -v togoagent-data:/data togoagent:0.1.20

# ============================================
# Stage 1: 构建前端
# ============================================
FROM ubuntu:24.04 AS frontend-builder

# 安装 Node.js
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build/frontend

# 复制前端代码（需要在构建前执行 git submodule update --init --recursive）
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# ============================================
# Stage 2: 最终镜像
# ============================================
FROM ubuntu:24.04

LABEL maintainer="TogoAgent Team"
LABEL description="TogoAgent - Multi-Agent Chat Room Framework"
LABEL version="0.1.20"

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TOGOAGENT_HOME=/opt/togoagent \
    STORAGE_ROOT=/data

# 安装 Python 和运行依赖
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 创建应用目录和数据目录
RUN mkdir -p ${TOGOAGENT_HOME} ${STORAGE_ROOT}

WORKDIR ${TOGOAGENT_HOME}

# 复制后端源代码
COPY src/ ${TOGOAGENT_HOME}/src/

# 复制资源文件
COPY assets/ ${TOGOAGENT_HOME}/assets/
COPY requirements.txt ${TOGOAGENT_HOME}/requirements.txt

# 复制前端构建产物
COPY --from=frontend-builder /build/frontend/dist ${TOGOAGENT_HOME}/assets/frontend

# 创建 Python 虚拟环境并安装依赖
RUN python3 -m venv .venv \
    && .venv/bin/pip install --upgrade pip \
    && .venv/bin/pip install -r requirements.txt

# 创建默认配置文件
RUN mkdir -p ${STORAGE_ROOT} \
    && if [ ! -f ${STORAGE_ROOT}/setting.json ]; then \
        cp ${TOGOAGENT_HOME}/assets/config_template.json ${STORAGE_ROOT}/setting.json; \
    fi

# 暴露端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/system/status.json || exit 1

# 启动命令
WORKDIR ${TOGOAGENT_HOME}/src
CMD ["../.venv/bin/python3", "backend_main.py", "--config-dir", "/data"]