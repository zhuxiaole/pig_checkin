FROM python:3.14-alpine

WORKDIR /app

# 安装必要的系统运行时与构建工具 (时区、证书、编译库、Chromium)
RUN apk add --no-cache \
    tzdata \
    ca-certificates \
    chromium \
    build-base \
    libffi-dev

# 从官方镜像直接引入 uv 工具
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# 复制项目依赖描述文件，最大化利用 Docker 构建缓存
COPY pyproject.toml uv.lock ./

# 安装项目依赖（不含开发依赖，不预装项目自身）
RUN uv sync --frozen --no-dev --no-install-project

# 复制源码
COPY . .

# 完成项目安装
RUN uv sync --frozen --no-dev

# 配置运行时环境变量
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PIG_RELOAD=false

# 数据挂载点与服务端口
VOLUME ["/app/data"]
EXPOSE 8000

CMD ["pig-checkin"]
