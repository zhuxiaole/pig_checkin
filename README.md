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

启动后访问：[http://127.0.0.1:8000](http://127.0.0.1:8000)

- **默认账号**：`admin`
- **默认密码**：`admin123`
*(登录后可在右上角「账号设置」中修改用户名与密码)*

---

## 站点使用说明

### 添加 / 配置 `cnlang.org` (国语视界)
1. 登录 `https://cnlang.org`，按 `F12` 打开开发者工具，在网络面板复制当前请求完整的 `Cookie` 字符串。
2. 在管理面板点击 **「+ 添加新网站」** 或在现有站点上点击 **「编辑配置」**。
3. 将 Cookie 粘贴至 **Cookies 字符串** 输入框中，保存即可。
4. 可点击 **「执行签到」** 立即测试连通性，并在下方查看详细返回日志。

---

## 数据存储与维护

- **数据库路径**：`src/pig_checkin/checkin.db`（SQLite 单文件存储）。
- **重置密码命令**（若遗忘管理员密码）：
  ```bash
  uv run python -c "from pig_checkin.db import get_db, hash_password; get_db().execute('UPDATE users SET password = ? WHERE username = ?', (hash_password('新密码'), 'admin')).connection.commit(); print('重置成功')"
  ```
