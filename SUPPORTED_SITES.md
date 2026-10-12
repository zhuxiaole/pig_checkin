# 支持签到站点列表

本文档记录 Pig Checkin 当前已内置支持的所有站点模板与认证配置。后续每新增一个网站将在此持续同步更新。

---

## 站点列表

| 站点名称 | 默认地址 | 模板标识 (`site_type`) | 架构 / 签到机制 | 认证方式 | Cloudflare 防盾 |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **国语视界** | `https://cnlang.org` | `cnlang` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |
| **观众 PT** | `https://audiences.me` | `audiences` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **影迷乐园** | `https://www.ymlyhd.com` | `ymlyhd` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |
| **ACG 字幕论坛** | `https://bbs.acgrip.com` | `acgrip` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |
| **星运阁 PT** | `https://pt.xingyungept.org` | `xingyunge` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **NovaHD PT** | `https://pt.novahd.top` | `novahd` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |

---

## Cookie 抓取注意事项

1. **不要退出登录**：在浏览器 F12 获取完 Cookie 后，**请勿在浏览器点击“退出登录”**，否则该 Cookie 会在服务端立即失效。
2. **尽量勾选“自动登录”**：登录各网站时勾选“下次自动登录”或“记住我”，可以使生成的 Cookie 有效期长达数月至一年。
3. **安全存储**：系统对存入的 Cookie 和凭据均采用工业级 AES-128 / Fernet 对称加密存储，界面不回显明文。
