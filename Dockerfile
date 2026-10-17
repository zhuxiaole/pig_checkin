# 两阶段构建：uv 只在构建期用，不进最终镜像。
# 两阶段使用相同的 debian-slim 基础镜像，确保 glibc 与 CloakBrowser 原生二进制兼容。

# ---------- 构建阶段：把依赖装进 /app/.venv 并预装 CloakBrowser ----------
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

# 预下载 CloakBrowser Stealth Chromium 二进制，固化在镜像内，避免容器启动时动态下载
# 禁用后台自动更新检查守护线程，防止 QEMU (arm64 on x86) 跨架构仿真构建时主线程退出触发 SIGSEGV (139)
ENV CLOAKBROWSER_CACHE_DIR=/app/.cloakbrowser \
    CLOAKBROWSER_AUTO_UPDATE=false
RUN /app/.venv/bin/python -c "import cloakbrowser.download as d; d._maybe_trigger_update_check = lambda: None; d.ensure_binary()" || /app/.venv/bin/python -m cloakbrowser install || true

# ---------- 运行阶段：仅包含运行时系统库与产物 ----------
FROM python:3.14-slim-bookworm

WORKDIR /app

# 系统依赖：时区数据、CA 证书、中文字体、以及无头 Chromium 必需的运行时共享库
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    ca-certificates \
    fonts-wqy-zenhei \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIG_RELOAD=false \
    CLOAKBROWSER_CACHE_DIR=/app/.cloakbrowser \
    CLOAKBROWSER_AUTO_UPDATE=false

# 复制虚拟环境、源码及已预装的 CloakBrowser 二进制
COPY --from=builder /app /app

VOLUME ["/app/data"]
EXPOSE 8000

CMD ["pig-checkin"]
