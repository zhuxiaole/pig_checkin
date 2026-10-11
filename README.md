# Pig Checkin 自动签到 Web 服务

基于 Python + FastAPI 构建的多站点自动签到服务，支持 Web 后台管理、多种定时调度策略、失败自动重试、以及通过 `invisible_playwright` 无头浏览器绕过 Cloudflare 等机器人反爬验证。

---

## 特性

- 🌐 **Web 可视化管理**：内置登录鉴权与会话管理，支持查看签到状态、配置站点、实时手动触发与查看历史日志。
- 🛡️ **反爬与安全防护**：
  - 遇到 Cloudflare 质询时自动唤起 `invisible_playwright` 绕过检测并更新 Cookies。
  - 用户密码采用 Salted SHA-256 + 恒定时间防时序攻击比对安全存储。
- ⏱️ **灵活定时调度**：
  - 支持**每日随机时间**（默认）与**每日固定时间**。
  - 下次执行时刻精准持久化至 SQLite 数据库中，任务成功后自动顺延。
  - 内置固定容量线程池（`ThreadPoolExecutor`）与任务防重锁，避免高并发爆内存。
- 🔄 **自动重试机制**：支持自定义失败重试次数及重试间隔（秒）。
- 🧩 **站点模板绑定**：每个站点模板绑定固定的认证方式（例如 `cnlang.org` 固定使用 Cookies），简化配置体验。

---

## 快速开始

### 1. 环境准备
项目使用 [uv](https://github.com/astral-sh/uv) 进行依赖与环境管理。

```bash
# 安装 uv (如果尚未安装)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. 安装依赖并启动

```bash
# 同步安装依赖
uv sync

# 启动 Web 服务
uv run pig-checkin
```

### 3. Docker 部署（推荐）

通过 GitHub Actions 自动构建的轻量化镜像（基于 Python 3.14 Alpine）：

```bash
docker run -d \
  --name pig-checkin \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -e TZ="Asia/Shanghai" \
  -e SECRET_KEY="your-custom-strong-secret-key-123456" \
  --restart unless-stopped \
  ghcr.io/<github-username>/pig_checkin:latest
```

或使用 `docker-compose.yml`：
```yaml
services:
  pig-checkin:
    image: ghcr.io/<github-username>/pig_checkin:latest
    container_name: pig-checkin
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
      - SECRET_KEY=your-custom-strong-secret-key-123456
```

启动后访问：[http://127.0.0.1:8000](http://127.0.0.1:8000)

- **默认账号**：`admin`
- **默认密码**：`admin123`
*(登录后可在右上角「账号设置」中修改用户名与密码)*

### 4. Docker 环境变量说明

镜像底层已预置 `tzdata` 时区数据库与完整 Python 运行环境，支持以下环境变量灵活定制：

| 环境变量名 | 默认值 | 必填/建议 | 功能说明 |
| :--- | :--- | :---: | :--- |
| **`TZ`** | `Asia/Shanghai` | **推荐配置** | 容器系统时区。依赖预置的 `tzdata`，确保签到日志时间戳、每日定时触发时刻与您所在地区时间精准一致（例如 `Asia/Shanghai`、`UTC`、`America/New_York`）。 |
| **`SECRET_KEY`** | *(内置默认密钥)* | **强烈建议修改** | 站点凭据（Cookies、密码）对称加密根密钥（Fernet / AES-128-CBC + HMAC-SHA256）。生产与公网部署务必修改为高强度随机字符串。 |
| **`PIG_RELOAD`** | `false` | 可选 | Uvicorn 服务热重载开关。生产容器环境保持 `false`；本地调试代码若需热加载可设为 `true`。 |

---

## 安全配置与密钥设置（强烈建议）

> ⚠️ **重要安全提醒**：
> 数据库中存储的站点 **Cookies**、**用户名** 与 **密码** 均采用工业级 **Fernet (AES-128-CBC + HMAC-SHA256)** 对称加密存储。
> 系统默认会读取环境变量 `SECRET_KEY` 作为加密根密钥。**为了保护您的站点账号凭据安全，强烈建议您在生产或公网环境中部署时，务必自定义设置 `SECRET_KEY` 环境变量**！若未配置，系统将回退使用代码内置默认密钥。

### 设置环境变量运行方式：

- **Linux / macOS**:
  ```bash
  export SECRET_KEY="your-custom-strong-secret-key-123456"
  uv run pig-checkin
  ```

- **Windows PowerShell**:
  ```powershell
  $env:SECRET_KEY="your-custom-strong-secret-key-123456"
  uv run pig-checkin
  ```

- **Docker / systemd**:
  在环境变量列表或 `.env` 中加入：
  ```env
  SECRET_KEY=your-custom-strong-secret-key-123456
  ```

---

## 站点使用说明

### 添加 / 配置 `cnlang.org` (国语视界)
1. 登录 `https://cnlang.org`，按 `F12` 打开开发者工具，在网络面板复制当前请求完整的 `Cookie` 字符串。
2. 在管理面板点击 **「+ 添加新网站」**（模板选 `cnlang.org`）或在现有站点上点击 **「编辑配置」**。
3. 将 Cookie 粘贴至 **Cookies 字符串** 输入框中，保存即可。
4. 可点击 **「执行签到」** 立即测试连通性，并在下方查看详细返回日志。

### 添加 / 配置 `audiences.me` (观众 PT)
1. 浏览器访问并登录 `https://audiences.me`，按 `F12` 在网络面板获取携带的完整 Cookie（确保包含 `c_secure_uid`、`c_secure_pass` 及 Cloudflare 的 `cf_clearance` 凭据）。
2. 在管理面板点击 **「+ 添加新网站」**，站点模板选择 **audiences.me (观众 PT)**。
3. 粘贴完整 Cookies 并保存。签到地址将自动路由至 `/attendance.php`，若遇到 Cloudflare 质询拦截将自动调度 `invisible_playwright` 进行处理。

---

## 数据存储与维护

- **数据库路径**：`data/checkin.db`（当前目录下 SQLite 单文件存储）。
- **重置密码命令**（若遗忘管理员密码）：
  ```bash
  uv run python -c "from pig_checkin.db import get_db, hash_password; get_db().execute('UPDATE users SET password = ? WHERE username = ?', (hash_password('新密码'), 'admin')).connection.commit(); print('重置成功')"
  ```
