# 两阶段构建：uv 只在构建期用，不进最终镜像（省约 35MB）。
# 两阶段必须用同一个基础镜像 —— venv/bin/python 是指向基础镜像解释器的符号链接。

# ---------- 构建阶段：把依赖装进 /app/.venv ----------
FROM python:3.14-alpine AS builder

WORKDIR /app

# 引入官方 uv 包管理工具（仅本阶段使用）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# 缓存目录与目标可能跨文件系统，禁用硬链接
ENV UV_LINK_MODE=copy

# 先复制依赖声明文件，最大化利用构建缓存
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 再复制源码并完成项目自身安装（editable，.pth 指向 /app/src）
COPY . .
RUN uv sync --frozen --no-dev

# ---------- 运行阶段：只带运行时需要的东西 ----------
FROM python:3.14-alpine

WORKDIR /app

# 系统依赖：
# - tzdata: 支持 TZ 环境变量自定义时区
# - ca-certificates: HTTPS 证书链校验
# - chromium: zendriver 原生无头浏览器
# - font-wqy-zenhei: 轻量中文字体支持 (相比 font-noto-cjk 省约 72MB)
RUN apk add --no-cache \
    tzdata \
    ca-certificates \
    chromium \
    font-wqy-zenhei \
    && find /usr/lib/chromium/locales/ -type f ! -name 'en-US.pak' ! -name 'zh-CN.pak' -delete

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIG_RELOAD=false \
    PIG_BROWSER_PATH=/usr/bin/chromium

# 只搬 venv 与源码；uv 的缓存不在 /app 下，本来就不会被带进来
COPY --from=builder /app /app

VOLUME ["/app/data"]
EXPOSE 8000

CMD ["pig-checkin"]
