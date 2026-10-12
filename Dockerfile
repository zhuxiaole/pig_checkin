FROM python:3.14-alpine

WORKDIR /app

# 1. 系统基础依赖与运行时支持：
# - tzdata: 支持 TZ 环境变量自定义时区
# - ca-certificates: HTTPS 证书链校验
# - chromium: zendriver 原生无头浏览器
# - font-noto-cjk: 网页渲染与验证码中文字体支持
RUN apk add --no-cache \
    tzdata \
    ca-certificates \
    chromium \
    font-noto-cjk

# 2. 引入官方 uv 包管理工具
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# 3. 环境变量优化：启用字节码预编译与独立复制模式
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PIG_RELOAD=false

# 4. 复制依赖声明文件，最大化利用 Docker 镜像层缓存
COPY pyproject.toml uv.lock README.md ./

# 5. 预装第三方依赖（不含开发依赖，不预装项目自身）
RUN uv sync --frozen --no-dev --no-install-project && uv cache clean

# 6. 复制应用源代码并完成项目自身可执行脚本安装
COPY . .
RUN uv sync --frozen --no-dev && uv cache clean

# 7. 数据持久化挂载卷与端口暴露
VOLUME ["/app/data"]
EXPOSE 8000

# 8. 启动应用
CMD ["pig-checkin"]
