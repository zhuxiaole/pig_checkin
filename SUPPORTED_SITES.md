# 支持签到站点列表与配置指引

本文档记录 Pig Checkin 当前已内置支持的所有站点模板、认证要求及配置说明。后续每新增一个网站将在此文档持续同步更新。

---

## 站点总览

| 站点名称 | 默认地址 | 模板标识 (`site_type`) | 架构 / 签到机制 | 认证方式 | Cloudflare 防盾 |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **国语视界** | `https://cnlang.org` | `cnlang` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |
| **观众 PT** | `https://audiences.me` | `audiences` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **影迷乐园** | `https://www.ymlyhd.com` | `ymlyhd` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |
| **ACG 字幕论坛** | `https://bbs.acgrip.com` | `acgrip` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |

---

## 各站点详细配置指南

### 1. 国语视界 (`cnlang.org`)
- **站点地址**：`https://cnlang.org`
- **模板选择**：在添加网站界面选择 `cnlang.org (国语视界)`
- **签到机制**：访问 `/dsu_paulsign-sign.html?mobile=no`，提取页面 `formhash` 并自动携带随机心情问候提交签到表单。
- **获取 Cookie 步骤**：
  1. 浏览器登录 `https://cnlang.org`；
  2. 按 `F12` 打开开发者工具，切换到 **网络 (Network)** 标签页，刷新一次页面；
  3. 点击任意 `cnlang.org` 的请求，在 **请求标头 (Request Headers)** 中找到 `Cookie`，完整复制其值；
  4. 粘贴至系统的「Cookie 字符串」输入框中保存。

---

### 2. 观众 PT (`audiences.me`)
- **站点地址**：`https://audiences.me`
- **模板选择**：在添加网站界面选择 `audiences.me (观众 PT)`
- **签到机制**：自动路由访问 `/attendance.php`，解析签到状态、卡片描述及连续签到天数。
- **特别注意**：
  - 该站点通常开启了 Cloudflare 5秒盾 / Turnstile 防护；
  - 复制的 Cookie 必须包含 **`c_secure_uid`**、**`c_secure_pass`** 以及 Cloudflare 验证通过后的 **`cf_clearance`** 凭据；
  - 若签到过程中 `cf_clearance` 失效，后台将自动调度内置无痕浏览器（Playwright）进行后台静默破盾刷新。

---

### 3. 影迷乐园 (`ymlyhd.com`)
- **站点地址**：`https://www.ymlyhd.com`
- **模板选择**：在添加网站界面选择 `ymlyhd.com (影迷乐园)`
- **签到机制**：自动路由访问 `/plugin.php?id=dsu_paulsign:sign`，内置自动适配 GBK 字符集编码与 `formhash` 提取。
- **获取 Cookie 步骤**：
  1. 浏览器登录 `https://www.ymlyhd.com`；
  2. 按 `F12` 获取请求标头中的 `Cookie` 完整字符串（包含 `_saltkey`、`_auth` 等）；
  3. 粘贴至系统保存即可。

---

### 4. ACG 字幕论坛 (`bbs.acgrip.com`)
- **站点地址**：`https://bbs.acgrip.com`
- **模板选择**：在添加网站界面选择 `bbs.acgrip.com (ACG字幕论坛)`
- **签到机制**：自动访问 `/dsu_paulsign-sign.html`，通过全局 `discuz_uid` 精准判定会话有效性并自动完成打卡。
- **获取 Cookie 步骤**：
  1. 浏览器登录 `https://bbs.acgrip.com`；
  2. 按 `F12` 获取携带的完整 Cookie 字符串；
  3. 粘贴至系统保存即可。

---

## 通用 Cookie 获取小贴士

1. **不要退出登录**：在浏览器获取完 Cookie 后，**请勿在浏览器点击“退出登录”**，否则该 Cookie 会在服务端立即作废。
2. **尽量勾选“自动登录”**：登录各网站时勾选“下次自动登录”或“记住我”，可以使生成的 Cookie 有效期长达数月至一年。
3. **敏感信息安全**：所有存入系统的 Cookie 与凭据均通过 AES-128 / Fernet 对称加密保存在本地数据库中，界面不回显明文，保障私密安全。
