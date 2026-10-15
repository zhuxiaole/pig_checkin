# 支持签到站点列表

本文档记录 Pig Checkin 当前已内置支持的所有站点模板与认证配置。后续每新增一个网站将在此持续同步更新。

---

## 站点列表

| 站点名称 | 默认地址 | 模板标识 (`site_type`) | 架构 / 签到机制 | 认证方式 | Cloudflare 防盾 |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **国语视界** | `https://cnlang.org` | `cnlang` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |
| **观众** | `https://audiences.me` | `audiences` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **影迷乐园** | `https://www.ymlyhd.com` | `ymlyhd` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |
| **Anime 字幕论坛** | `https://bbs.acgrip.com` | `acgrip` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |
| **星陨阁** | `https://pt.xingyungept.org` | `xingyunge` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **NovaHD** | `https://pt.novahd.top` | `novahd` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **SSD 论坛** | `https://ssdforum.org` | `ssdforum` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |
| **PTShare 论坛** | `http://bbs.ptshare.org` | `ptshare` | Discuz! (`dsu_paulsign` 插件) | Cookies 认证 | ✔ 自动穿透 |
| **恩山无线论坛** | `https://www.right.com.cn/forum` | `right` | Discuz! (`erling_qd` 插件，AJAX + JSON) | Cookies 认证 | ✔ 自动穿透 |
| **多看聚影** | `https://duokan.club` | `duokan` | Discuz! (`zqlj_sign` 插件，令牌在 URL) | Cookies 认证 | ⚠ 需手动过一次人机验证 |
| **肉丝** | `https://rousi.pro` | `rousi` | PeerGo (`POST /api/points/attendance`) | 密钥认证 | - |
| **红豆饭** | `https://hdfans.org` | `hdfans` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **PT 劳改所** | `https://ptlgs.org` | `ptlgs` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **高清家园** | `https://hdhome.org` | `hdhome` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **熊猫 PT** | `https://pandapt.net` | `pandapt` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **PTHome** | `https://pthome.net` | `pthome` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **憨憨** | `https://hhanclub.net` | `hhanclub` | NexusPHP (`/attendance.php`) | Cookies 认证 | ✔ 自动穿透 |
| **人人字幕组** | `https://yysub.cc` | `yyets` | 原生 API (`/User/Login/ajaxLogin`) | 账号密码认证 | - |

---

## 站点模板配置

站点配置全部抽成 YAML 模板（`src/pig_checkin/site_templates.yaml`），代码只保留协议实现（`discuz` / `nexusphp` / `yysub` / `peergo`）。
模板随程序发布，**不依赖任何远程服务**。

### 启用与停用

后台「模板库」按钮打开两列视图：左侧是模板库中的全部模板（可按名称、标识或默认地址搜索），右侧是已启用模板。
点击「启用 / 停用」即可调整。

- **默认一个都不启用**：全新部署（或原数据被清空）时左列全部是「启用」、右列为空，需要自己挑；
  状态记录在数据库 `enabled_templates` 表，表为空即「没有启用任何模板」；
- 「添加网站」的模板下拉框只列出**已启用**的模板；
- **正被站点使用的模板不允许停用**：该项会显示 `使用中 N` 而不是「停用」按钮，接口也会拒绝并说明原因。
  需要停用就先把这些站点删掉或改用其它模板；
- 已有站点正在使用的模板会保留在编辑界面的下拉框里，即使它没有被启用，否则旧站点将无法编辑。

### 模板字段

| 字段 | 必填 | 用途 |
| :--- | :---: | :--- |
| `key` | ✔ | 唯一标识（`site_type`），`^[a-z0-9][a-z0-9_]{0,31}$` |
| `name` | ✔ | 站点名称，**只写名称，不要带网址**（如 `国语视界`） |
| `flow` | ✔ | 协议实现：`discuz` / `nexusphp` / `yysub` / `peergo` |
| `auth_type` | ✔ | `cookie` / `password` / `token`（密钥，单独一列加密存储） |
| `auth_label` | ✔ | 认证方式中文标签（UI 展示） |
| `default_domain` | ✔ | 默认域名，必须含 `http://` 或 `https://` |
| `cookie_placeholder` | ✔ | Cookie 输入框占位提示 |

**协议字段**：只写需要覆盖的项，未写的自动取协议默认值（默认值见 `templates.py` 的 `*_DEFAULTS`）。
约定 `*_patterns` 为按顺序尝试的正则数组（取首个命中，无捕获组时取整体匹配），`*_keywords` 为子串包含判定。

`discuz` 常用覆盖项：

| 字段 | 默认值 | 作用 |
| :--- | :--- | :--- |
| `sign_path` | `/plugin.php?id=dsu_paulsign:sign` | 签到页路径 |
| `submit_path` | `/plugin.php?id=dsu_paulsign:sign&operation=qiandao&infloat=1` | 提交签到路径，可用 `{formhash}` 占位（令牌在 URL 上的插件，如多看） |
| `submit_body` | `{"qdxq":"kx","qdmode":"1","fastreply":"0"}` | 随表单提交的固定字段 |
| `formhash_field` / `greeting_field` | `formhash` / `todaysay` | 动态字段名；`greeting_field` 留空则提交体里不带问候语 |
| `gate_keywords` / `gate_message` | 空 / 通用提示 | 命中站点自带的人机验证闸门时直接返回该提示（如多看） |
| `greetings` | 5 条中文祝福语 | 随机祝福语，支持 `{site}` 占位 |
| `sign_form_selectors` | `[["input","qdxq"],["form","qiandao"]]` | 判定「页面还有待提交的签到表单」 |
| `formhash_patterns` | 2 条正则 | 从 HTML 提取 formhash |
| `logged_in_patterns` / `logged_in_keywords` | `discuz_uid` 正则 / `action=logout` | 登录态判定 |
| `need_login_keywords` | 未登录提示文案 | 登录失效判定 |
| `user_patterns` | 4 条正则 | 提取用户名 |
| `already_signed_page_keywords` | 4 条已签到文案 | 仅在无签到表单时生效；**必须是页面独有的片段**，签到排行榜/统计里同样出现的字样会误判（如多看要用 `class="btna">今日已打`） |
| `success_keywords` / `already_signed_keywords` | 提交响应判定文案 | 判定签到结果 |
| `message_selectors` | `[["div","c"],["div","messagetext"]]` | 提取提示文本 |
| `encoding` | 空（自动识别） | 强制指定 `utf-8` / `gbk` / `gb2312` |

`nexusphp` 常用覆盖项：`sign_path`、`login_fail_keywords`、`login_required_keyword`、`user_patterns`、
`done_keywords`（已签到）、`verify_keywords`（需人机验证）、`success_keywords`、`bonus_patterns`（奖励数额）、
`card_desc_patterns`（卡片描述）、`award_patterns`（签到成功说明）、`encoding`。

`yysub` 常用覆盖项：`login_path`、`info_path`、`login_body`（值支持 `{username}` / `{password}` 占位）、
`login_fail_status`、`unauth_status`、`success_fields`（`a.b.c` 路径）、`ok_status`。

`peergo` 常用覆盖项（PeerGo 系站点，用个人 API Key 调它的自动化接口，不需要 Cookie）：

| 字段 | 默认值 | 作用 |
| :--- | :--- | :--- |
| `claim_path` | `/api/points/attendance` | 签到接口（`POST`，认证头 `Authorization: Bearer <密钥>`） |
| `claim_body` | `{}` | 随请求提交的 JSON 字段 |
| `claim_mode` | 空 | 留空用站点默认（`fixed`），可选 `fixed` / `random` |
| `already_claimed_codes` | `[1]` | 应答里代表「今天已签到」的 `code` |
| `unauth_codes` / `forbidden_codes` | `[401]` / `[403]` | 密钥失效 / 权限不足的 `code` |

> 密钥需要有 `attendance:claim` 权限（在站点账号设置里生成个人 API Key 时勾选），否则接口返回 403。

### 新增站点

在 `src/pig_checkin/site_templates.yaml` 的 `templates` 列表里加一项，填写上面的字段即可，**不需要**改 `checkin.py` —— 前提是站点能归入现有四种协议。

### 模板校验

模板库在程序启动时（首次读取）逐项校验，不通过的模板会被跳过并在日志中提示：

- `key` 格式、`flow` / `auth_type` 取值、`default_domain` 必须含协议；声明了 `sign_path` 的协议（`discuz` / `nexusphp`）必须有以 `/` 开头的 `sign_path`；
- 未知字段一律拒绝（防拼写错误）；
- 所有 `*_pattern` / `*_patterns` 必须能编译，且长度不超过 500 字符（压缩 ReDoS 空间）。

---

## 签到流程详解

系统把签到逻辑抽象为 **4 种协议**（`flow`），18 个站点只是同一协议的参数差异。

### 1. `discuz` — Discuz! 论坛签到（7 站）

适用：国语视界、影迷乐园、Anime 字幕论坛、SSD 论坛、PTShare 论坛（以上为 `dsu_paulsign` 插件），
恩山无线论坛（`erling_qd` 插件，AJAX + JSON 应答）、多看聚影（`zqlj_sign` 插件，令牌在 URL 上）。

| 步骤 | 请求 | 参数位置与内容 |
| :--- | :--- | :--- |
| ① 打开签到页 | `GET {domain}{sign_path}` | **Header**：`User-Agent`（固定 Chrome UA）、`Referer`（=签到页自身）、`Accept-Language: zh-CN`、`Upgrade-Insecure-Requests: 1`<br>**Cookie**：用户配置的完整 Cookie 串<br>**URL**：`sign_path` 可自带查询串（如 cnlang 的 `?mobile=no`） |
| ② 编码识别 | — | 按 `Content-Type` 与字节内容自动判定 `utf-8` / `gbk` / `gb2312` 后解码 |
| ③ 过盾（按需） | 唤起无痕浏览器 | 命中 Cloudflare 盾时由 `zendriver` 过盾，用新 Cookie 回写本次会话，页面内容替换为过盾后的 HTML |
| ③′ 站点自带人机验证闸门 | — | 页面命中 `gate_keywords`（如多看的 `dev8133_cloudflare`）时直接按 `gate_message` 报错：闸门票有过期时间，必须人工在浏览器里重过一次验证 |
| ④ 提取令牌 | 解析步骤①的 HTML | **优先**模板正则 `formhash_patterns`；取不到再兜底页面隐藏域 `<input name="formhash" value="...">`。（页面里常有登录/搜索表单的同名字段，故正则优先） |
| ⑤ 提交签到 | `POST {domain}{submit_path}` | **URL**：`submit_path` 支持 `{formhash}` 占位，令牌在 URL 上的插件（如多看 `?sign={formhash}`）用它<br>**Body**（`application/x-www-form-urlencoded`）：`submit_body` 的固定字段 + `formhash`；`greeting_field` 非空时附带随机祝福语，留空则不带（如恩山、多看）；GBK 站点按对应编码重新 urlencode |

**登录态判定**（步骤①之后立即执行）：命中 `discuz_uid = <数字>`（需大于 0）或页面存在 `action=logout` 视为已登录；命中 `need_login_keywords` 视为 Cookie 失效。

**是否签到成功**（按顺序判定）：

1. **无需重复签到**：仅当页面**不含**签到表单（`<input name="qdxq">` / `<form name="qiandao">` 均不存在）时，才认 `already_signed_page_keywords` → 成功。
   *（该约束用于避免误命中侧边栏“今日已有 N 人签到”这类站点统计文案；关键字同样必须是页面独有的片段——多看要用 `class="btna">今日已打`，恩山若用裸类名 `erqd-checkin-btn2` 会撞上自己 `<style>` 里的 CSS 规则名）*
2. **AJAX 插件应答是 JSON**：应答能解析成对象且含 `success` / `message` 字段（如恩山 `erling_qd`）时，按 `success` 布尔值判定，`message` 作为提示文本。
3. **提交成功**：响应含 `success_keywords`（`签到成功` / `恭喜你` / `获得` / `奖励` / `今日已签`）→ 成功。
4. **提交时已签到**：响应含 `already_signed_keywords`（`已经签到过了` / `今天已经签到`）→ 成功。
5. **其余**：判为失败，并把 `div.c` 或 `#messagetext` 中的提示文本回显到日志（只取第一行，Discuz 会把“请稍候，本站将自动跳转…”塞在同一个节点里）。

### 2. `nexusphp` — NexusPHP PT 站签到（9 站）

适用：观众、星陨阁、NovaHD、红豆饭、PT 劳改所、高清家园、熊猫 PT、PTHome、憨憨。签到路径均为 `/attendance.php`。

| 步骤 | 请求 | 参数位置与内容 |
| :--- | :--- | :--- |
| ① 打开签到页 | `GET {domain}{sign_path}` | **Header**：`User-Agent`、`Referer={domain}/index.php`、`Accept-Language: zh-CN,zh;q=0.9,en;q=0.8`、`Upgrade-Insecure-Requests: 1`<br>**Cookie**：用户配置的完整 Cookie 串 |
| ② 过盾（按需） | 唤起无痕浏览器 | 命中 Cloudflare 盾**或**页面出现 `attendance-card--verify`（卡片内 Turnstile）时，由 `zendriver` 完成人机验证。此处签到由站点前端 JS 自动完成，因此浏览器内轮询等待 `attendance-card--done`，再取回 Cookie 与 HTML |

**登录态判定**：出现 `login.php`、`class="login"` 或**缺少** `userdetails.php` → Cookie 失效。用户名取自正则 `userdetails.php?id=N ... <b>名字</b>`。

**是否签到成功**（按顺序判定）：

1. **已签到**：HTML 含 `done_keywords`（`attendance-card--done` / `今天已经签到` / `今日已签到` / `已经签到过了`）→ 成功；提示依次取 `bonus_patterns`（奖励数额）→ `card_desc_patterns`（卡片描述）→ `award_patterns`（签到说明）。
2. **仍需人机验证**：含 `verify_keywords`（`attendance-card--verify`）→ 失败（“需完成人机验证”）。
3. **签到成功**：含 `success_keywords`（`签到成功` / `这是您的第` / `已连续签到` / `本次签到获得`）→ 成功，并用 `award_patterns` 提取整句说明（兼容憨豆/魔力值/爆米花等不同奖励名）。
4. **其余**：判为失败，回显页面 `<title>` 以便排查模板是否过期。

### 3. `yysub` — yysub.cc 原生 JSON API（1 站）

适用：YYeTs 人人字幕组。该站无 Cloudflare 防盾。
| 步骤 | 请求 | 参数位置与内容 |
| :--- | :--- | :--- |
| ⓪ 凭据校验 | — | `username` 或 `cookies` 至少配置其一，否则直接返回失败 |
| ① 登录（可选，有账号密码时执行） | `POST {domain}/User/Login/ajaxLogin` | **Header**：`User-Agent`、`Referer={domain}/user/login`、`Origin={domain}`、`X-Requested-With: XMLHttpRequest`、`Accept: application/json, text/javascript, */*; q=0.01`<br>**Body**：`account`、`email`（均填账号）、`password`、`remember=1` |
| ② 查询用户信息 | `GET {domain}/user/login/getCurUserTopInfo` | 复用步骤①会话 Cookie；依赖上一步返回的登录态 |

**是否签到成功 / 登录成功**（依据返回 JSON）：

1. 步骤① 返回 `status` 属于 `{0, "0", 4001, "4001"}` → 登录失败（账号或密码错误）。
2. 步骤② 返回 `status` 属于 `{4001, "4001"}` → 未登录或会话已失效，失败。
3. 步骤② 存在 `data.usercount.cont_login` 或 `data.userinfo.point` → 成功，输出“连续登录 N 天，人人钻 N”。
4. 步骤② `status` 属于 `{1, 200, "1", "200"}` 或 `data` 中含 `uid` → 成功（仅有状态码，无明细）。
5. 其余 → 失败并回显 `info` / 状态码。

> 该站点的“签到”即每日登录行为本身（`cont_login` 连续登录天数递增即为打卡成功），无独立签到接口。

### 4. `peergo` — PeerGo 系站点（个人 API Key）（1 站）

适用：肉丝（rousi.pro）。**不需要 Cookie**：用账号设置里生成的个人 API Key（`pgk_` 开头）调 PeerGo 给
MoviePilot 等自动化工具准备的兼容接口。密钥必须有 `attendance:claim` 权限，否则接口返回 403。

| 步骤 | 请求 | 参数位置与内容 |
| :--- | :--- | :--- |
| ① 提交签到 | `POST {domain}{claim_path}` | **Header**：`Authorization: Bearer <密钥>`、`Accept: application/json`（源码也接受 `api-token` 头）<br>**Body**（JSON）：`claim_body`，`claim_mode` 非空时并入 `mode`（`fixed` / `random`，留空则由站点按默认 `fixed` 处理） |

**是否签到成功**（依据应答 JSON 的 `code`，与 HTTP 状态码一致）：

| `code` | 含义 | 处理 |
| :--- | :--- | :--- |
| `0` | 签到成功 | 成功，提示由 `data.reward` / `data.current_streak` / `data.total_days` 拼出（“获得 N 魔力，连续 N 天，累计 N 天”） |
| `1`（`already_claimed_codes`） | 今天已签到 | 成功（幂等） |
| `401`（`unauth_codes`） | 密钥无效或已撤销 | 失败，提示重新生成密钥 |
| `403`（`forbidden_codes`） | 密钥缺少 `attendance:claim` 权限 | 失败，提示补权限 |
| 其他 | — | 失败，回显 `message` |

### 通用机制

- **重试**：上述任一协议的失败结果都会按站点配置的 `max_retries` / `retry_interval` 重试；每次失败立即落库一条日志。
- **幂等**：四个协议都把“今日已签到”判定为**成功**，因此重复执行不会重复打卡。
- **浏览器兜底**：Cloudflare 盾（HTTP 403/503 或 `Just a moment...`）与页面内 Turnstile 都会自动唤起 `zendriver` 处理，过盾后新 Cookie（含 `cf_clearance`）会回写到本次 HTTP 会话。
  注意 `zendriver` 以无头模式运行，**站点自带的人机验证闸门**（如多看）在无头下连控件都不渲染，只能靠人工过验证后粘贴 Cookie；`gate_keywords` / `gate_message` 正是为这类站点准备的明确提示。

---

## Cookie 抓取注意事项

1. **不要退出登录**：在浏览器 F12 获取完 Cookie 后，**请勿在浏览器点击“退出登录”**，否则该 Cookie 会在服务端立即失效。
2. **尽量勾选“自动登录”**：登录各网站时勾选“下次自动登录”或“记住我”，可以使生成的 Cookie 有效期长达数月至一年。
3. **安全存储**：系统对存入的 Cookie、账号密码与 API 密钥均采用工业级 AES-128 / Fernet 对称加密存储，界面不回显明文（只显示「已填写 / 未填写」）。
