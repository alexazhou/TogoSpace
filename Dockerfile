# TogoSpace Dockerfile
# 基于 Ubuntu 24.04 LTS 构建
#
# 构建方式：
#   1. 确保 frontend 子模块已初始化：git submodule update --init --recursive
#   2. docker build -t togospace:0.3.8 .
#   3. docker run -d -p 7180:7180 -v togospace-storage:/storage togospace:0.3.8

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

LABEL maintainer="TogoSpace Team"
LABEL description="TogoSpace - Multi-Agent Chat Room Framework"
LABEL version="0.3.8"

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TOGOSPACE_HOME=/opt/togospace \
    STORAGE_ROOT=/storage \
    TOGOSPACE_RUN_ENV=docker

# 安装 Python、常用开发工具和运行依赖（含 GitHub CLI）
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    wget \
    ca-certificates \
    git \
    gh \
    openssh-client \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    file \
    imagemagick \
    jq \
    tree \
    htop \
    zip \
    unzip \
    rsync \
    sqlite3 \
    net-tools \
    netcat-openbsd \
    dnsutils \
    iproute2 \
    iputils-ping \
    vim \
    nano \
    tini \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 工具包（markitdown 全格式支持：PDF、Word、PPT、Excel、音频、YouTube 等）
RUN pip3 install --break-system-packages \
    markitdown[all] \
    && rm -rf /root/.cache/pip

# 创建应用目录和数据目录
RUN mkdir -p ${TOGOSPACE_HOME} ${STORAGE_ROOT}

WORKDIR ${TOGOSPACE_HOME}

# 复制后端源代码
COPY src/ ${TOGOSPACE_HOME}/src/

# 复制资源文件
COPY assets/ ${TOGOSPACE_HOME}/assets/
COPY requirements.txt ${TOGOSPACE_HOME}/requirements.txt

# 复制前端构建产物
COPY --from=frontend-builder /build/frontend/dist ${TOGOSPACE_HOME}/assets/frontend

# 创建 Python 虚拟环境并安装依赖
RUN python3 -m venv .venv \
    && .venv/bin/pip install --upgrade pip \
    && .venv/bin/pip install -r requirements.txt

# 创建默认配置文件
RUN mkdir -p ${STORAGE_ROOT} \
    && if [ ! -f ${STORAGE_ROOT}/setting.json ]; then \
        cp ${TOGOSPACE_HOME}/assets/config_template.json ${STORAGE_ROOT}/setting.json; \
    fi

# 暴露端口
EXPOSE 7180

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:7180/system/status.json || exit 1

# 启动命令（可通过 TOGO_PORT 环境变量覆盖端口）
ENV TOGO_PORT=7180
WORKDIR ${TOGOSPACE_HOME}/src
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/sh", "-c", "exec ../.venv/bin/python3 backend_main.py --config-dir /storage --port ${TOGO_PORT} --ignore-pid"]