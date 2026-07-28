# Zepp_API (Python重构版)

这是 `apgk/Zepp_API` 的 Python 重构版本。保留了原项目的核心调用链：

1. 接收 `user` / `pwd` / `step`
2. 登录 Zepp 接口
3. 提交步数数据

## 文件

- `app.py`：完整 Python 实现（CLI + 简易 HTTP 服务）
- `index.php`：保留原项目代码作为对照（未删除）
- `requirements.txt`：依赖说明

## 运行方式

### 1) CLI 一次性执行

```bash
python app.py --user 13800138000 --pwd 123456 --step 20000
```

### 2) HTTP 服务模式

```bash
python app.py --serve --host 0.0.0.0 --port 8000
```

请求示例：

```bash
curl "http://127.0.0.1:8000/?user=13800138000&pwd=123456&step=20000"
```

或 `POST`：

```bash
curl -X POST -d "user=13800138000&pwd=123456&step=20000" http://127.0.0.1:8000/
```

### 3) 简单页面模式

启动服务后直接打开：

```bash
http://127.0.0.1:8000/
```

页面会提交到 `POST /api/step`，返回 JSON 展示结果。

### 4) 标准 JSON 接口

页面默认改为提交到：

```text
POST /api/tools/zepp-step
Content-Type: application/json
```

请求体：

```json
{
  "account": "13800138000",
  "password": "123456",
  "step": 20000,
  "debug": false,
  "api_key": "zepp-tool-default-key"
}
```

也支持把鉴权 key 放到请求头：

```text
X-Api-Key: zepp-tool-default-key
Authorization: Bearer zepp-tool-default-key
```

默认 key 可通过环境变量覆盖：

```bash
ZEPP_TOOL_API_KEY="your-key" python app.py --serve --host 0.0.0.0 --port 8000
```

旧接口 `POST /api/step` 仍保留，用于兼容已有表单或脚本。

### 5) 百度网盘中转下载

页面已接入“百度网盘中转下载”入口。当前版本只支持一个分享链接，提交后后台 worker 会调用 BaiduPCS-Go，把分享文件先转存到登录账号的百度网盘专用 tmp 目录，再下载到服务器本地目录，最后由网页打包成 ZIP 给用户下载。

worker 依赖 BaiduPCS-Go 二进制和登录态：

```bash
# 二进制可以放在 ~/.local/bin/BaiduPCS-Go，也可以用环境变量指定
BAIDUPCS_GO_BIN="/path/to/BaiduPCS-Go"

# 独立配置目录会保存百度网盘 Cookie/BDUSS/STOKEN，请勿提交到 Git
BAIDUPCS_GO_CONFIG_DIR="$(pwd)/.baidupcs"
```

BaiduPCS-Go 没有原生二维码登录命令。管理后台额外接了百度 Passport 扫码入口：管理员先进入 `/admin`，打开“百度网盘登录态”区域，点击“生成扫码登录二维码”，页面会通过后台代理显示二维码；用百度网盘 App 或百度 App 扫码确认。如果百度接口没有返回 BaiduPCS-Go 转存所需的完整 `BDUSS/STOKEN`，再点击“打开百度网盘网页版”，从浏览器 Network 请求里复制完整 Cookie，并粘贴到管理后台导入。前台用户不能导入或查看 Cookie。

也可以用命令行导入：

```bash
BAIDUPCS_GO_BIN="/path/to/BaiduPCS-Go"
BAIDUPCS_GO_CONFIG_DIR="$(pwd)/.baidupcs" \
"$BAIDUPCS_GO_BIN" login -cookies="BDUSS=...; STOKEN=...; PANPSC=..."
```

导入后可检查：

```bash
BAIDUPCS_GO_CONFIG_DIR="$(pwd)/.baidupcs" "$BAIDUPCS_GO_BIN" who
```

自动解析接口：

```text
POST /api/tools/baidu-share/parse
```

提交前大小预检接口：

```text
POST /api/tools/baidu-share/preflight
Content-Type: application/json
```

预检会校验分享链接和提取码，并尽量统计分享文件数量、目录数量和总大小；大型目录可能只返回当前统计下限，并带 `truncated: true`。接口返回的 `confirmation_token` 需要随正式提交一起传入，避免用户没确认大小就开始转存。

任务记录接口：

```text
POST /api/tools/baidu-share
Content-Type: application/json
```

请求体：

```json
{
  "raw_text": "链接：https://pan.baidu.com/s/1xxxx 提取码：abcd",
  "netdisk_save_root": "/apps/zepp-api-baidu-tmp",
  "server_download_root": "/srv/baidu-downloads",
  "verification_token": "123456",
  "size_confirm_token": "<preflight 返回的 confirmation_token>",
  "api_key": "zepp-tool-default-key"
}
```

百度网盘任务和步数提交共用 `/admin` 管理后台生成的当天 6 位验证码。提交成功后接口会返回 `download_token`。前端会把该 token 存在提交用户当前浏览器的 `localStorage` 中；任务完成后用它请求 ZIP 下载，公开日志不会展示完整链接、提取码或下载凭证。

最近记录接口：

```text
GET /api/tools/baidu-share/jobs?limit=20
```

任务记录会返回脱敏后的链接、提取码和状态字段。当前页面会展示这些状态：

```text
queued          排队中
worker_unavailable 执行器不可用
login_required  需要登录
transferring    转存中
downloading     下载中
completed       已完成
partial_completed 部分完成
failed          失败
transfer_failed 转存失败
canceled        已取消
expired         已过期
```

任务完成后的 ZIP 下载接口：

```text
GET /api/tools/baidu-share/jobs/{job_id}/download.zip?token=<download_token>
```

下载接口只在任务状态为 `completed` 或 `partial_completed`、服务器任务目录存在文件、且 token 正确时返回 ZIP。多文件会统一打包成一个 ZIP 发送给用户。Codex 内置浏览器不支持文件下载；本地调试时如果点击无反应，可以复制下载链接到 Chrome 或 Safari 打开。

服务器文件默认保留 24 小时；线上可用 `BAIDU_SHARE_FILE_RETENTION_MINUTES="30"` 改成 30 分钟。过期后后台会把任务状态标记为 `expired` 并删除对应 `job_id` 下载目录。管理后台 `/admin` 的“网盘文件存储”区域可查看当前占用、磁盘剩余、过期候选任务，也可以手动触发清理。清理只处理终态任务，不会删除排队、转存中或下载中的任务。

管理员存储接口：

```text
GET  /api/admin/baidu-share/storage
POST /api/admin/baidu-share/storage/cleanup
```

默认目录可通过环境变量覆盖：

```bash
BAIDU_NETDISK_DEFAULT_SAVE_ROOT="/apps/zepp-api-baidu-tmp"
BAIDU_SERVER_DOWNLOAD_DEFAULT_ROOT="/srv/baidu-downloads"
BAIDU_SHARE_WORKER_ENABLED="1"
BAIDU_SHARE_FILE_RETENTION_HOURS="24"
BAIDU_SHARE_FILE_RETENTION_MINUTES="30"
BAIDU_SHARE_STORAGE_MAX_GB="20"
BAIDU_SHARE_CLEANUP_INTERVAL_SECONDS="600"
```

### 6) QQ 互赞工具

移动互赞模式下，工具网只维护测试白名单、当天活跃池、任务租约和结果记录。
QQ、NapCat、登录态、OneBot token 与 `send_like` 全部留在用户手机。服务器不
启动 QQ、NapCat 或 Docker。

线上配置：

```bash
QQ_LIKE_ENABLED="0"
QQ_LIKE_MOBILE_ENABLED="1"
QQ_LIKE_DATA_ROOT="/data/qq-like-tool"
QQ_LIKE_MOBILE_DB_PATH="/data/qq-like-tool/qq_like_mobile.sqlite3"
QQ_LIKE_MOBILE_MAX_BATCH_SIZE="8"
QQ_LIKE_MOBILE_LEASE_SECONDS="600"
QQ_LIKE_MOBILE_LIKES_PER_TARGET="10"
```

运行约束：

- 只有后台启用的白名单 QQ 才能注册；
- QQ 在线确认和心跳成功后才进入北京时间当天活跃池；
- 同一来源、目标和业务日期只有一条有向任务；
- 每次最多租约 8 条，租约 600 秒；
- 失败或结果不明均为终态，不自动重新派发；
- 重置设备绑定会立即撤销旧任务凭证，下次注册签发新凭证；
- SQLite 只保存任务凭证、安装 ID 和结果幂等键的 SHA-256 哈希。

管理员接口：

```text
GET  /api/admin/qq-like/mobile/overview
POST /api/admin/qq-like/mobile/allowlist
POST /api/admin/qq-like/mobile/accounts/action
```

`/admin` 可以维护白名单、查看当天活跃账号和任务明细、停用账号及重置设备
绑定。完整 QQ 号只在已认证后台返回。

#### Android 登录助手

`android-app/` 的用户界面只展示 QQ 扫码登录、当前账号、今日互赞和任务
记录。NapCat、Termux、OneBot、端口、服务器地址和运行日志不会出现在用户
主流程中。扫码页使用原生二维码界面，不再展示 NapCat WebUI。QQ 在线后 App
自动注册和心跳；用户主动点击“开始互赞”后，App 分批领取当天全部任务，并通过只监听
`127.0.0.1:3000` 的本机 OneBot 严格串行执行 `send_like`。

第一版仅支持前台手动同步，不包含后台 Service、定时任务或开机常驻。App
不会把 QQ 会话、Cookie、扫码信息、NapCat 配置或 OneBot token 上传到服务器。
构建、协议和本机幂等规则见 `android-app/README.md`。

移动端接口：

```text
POST /api/tools/qq-like/mobile/register
POST /api/tools/qq-like/mobile/heartbeat
POST /api/tools/qq-like/mobile/tasks/lease
POST /api/tools/qq-like/mobile/tasks/result
```

服务器只保存移动端访问令牌和结果幂等键的 SHA-256 哈希，不保存其明文。
租约过期会直接标记为 `uncertain`，不会自动重新下发同一任务。

### 7) HTTP / HTTPS

默认启动 HTTP：

```bash
python app.py --serve --host 0.0.0.0 --port 8000
```

如果需要 Python 进程直接提供 HTTPS，需要传入证书和私钥：

```bash
python app.py --serve --host 0.0.0.0 --port 8443 --ssl-cert /path/fullchain.pem --ssl-key /path/privkey.pem
```

实际线上更推荐使用 Nginx/Caddy 提供 HTTPS，反向代理到本服务的 HTTP 端口。前端使用相对路径 `/api/tools/zepp-step`，所以页面通过 HTTP 打开就走 HTTP，通过 HTTPS 打开就走 HTTPS。

## 说明

- 加密部分优先使用 `cryptography`，缺失时回退到本机 `openssl`
- 输出同样是 JSON，包含时间戳、脱敏后的用户、状态和结果信息
- 项目仅做工程重构演示，实际接口行为受官方接口变更影响

## 在 conda 环境启动建议（示例）

```bash
# 进入项目
cd /Users/liyangxu/data/workspace/github/zepp-api-python

# 创建并激活环境（示例）
conda create -n zepp-api python=3.11 -y
conda activate zepp-api
pip install -r requirements.txt

# 启动服务
python app.py --serve --host 127.0.0.1 --port 8000
```
