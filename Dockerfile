# 两阶段构建：uv 只在构建期用，不进最终镜像。
# 两阶段使用相同的 debian-slim 基础镜像。

# ---------- 构建阶段：把依赖装进 /app/.venv ----------
FROM python:3.14-slim-bookworm AS builder

WORKDIR /app

# 引入官方 uv 包管理工具（仅本阶段使用）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# 缓存目录与目标可能跨文件系统，禁用硬链接
ENV UV_LINK_MODE=copy

# 先复制依赖声明文件，最大化利用构建缓存
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 复制源码并完成项目安装
COPY . .
RUN uv sync --frozen --no-dev

# ---------- 运行阶段：安装系统 chromium, xvfb, 字体与运行时 ----------
FROM python:3.14-slim-bookworm

WORKDIR /app

# 系统依赖：
# - tzdata: 支持 TZ 环境变量自定义时区
# - ca-certificates: HTTPS 证书链校验
# - chromium: 系统原生浏览器
# - xvfb: X 虚拟显存桌面（让浏览器在容器中以真实 1920x1080 有头模式渲染，秒过 Cloudflare Turnstile）
# - fonts-wqy-zenhei: 中文字体支持
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    ca-certificates \
    chromium \
    xvfb \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIG_RELOAD=false \
    PIG_BROWSER_PATH=/usr/bin/chromium

# 复制虚拟环境与源码
COPY --from=builder /app /app

VOLUME ["/app/data"]
EXPOSE 8000

CMD ["pig-checkin"]
