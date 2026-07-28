# 互赞助手（Android）

用户首页只展示 QQ 扫码登录、当前账号、今日互赞统计和任务记录。Termux、
NapCat、OneBot、端口、服务器地址与运行日志均不出现在用户主流程中。

QQ 登录使用原生二维码页：App 通过本机登录接口刷新二维码，读取本机二维码
图片并轮询登录结果，不再向用户展示 NapCat WebUI。

App 按 NapCat 官方 Android/Termux 路径完成：

1. 检查 Termux；
2. 通过 Termux 官方 `RUN_COMMAND` 接口执行 NapCat 官方安装脚本；
3. 在 Termux 的 `proot-distro` Debian 环境启动 NapCat；
4. 在 App 原生页面展示本机二维码并完成 QQ 登录；
5. 将 OneBot HTTP 服务配置为仅监听本机；
6. QQ 在线后自动向任务服务器注册并完成当天心跳；
7. 由用户在 App 前台明确点击后，按 8 条一批串行完成当天全部互赞任务。

第一版没有后台 Service、定时任务或开机常驻。离开 App 前台后会停止领取并
执行后续任务。构建 App 不会自动执行任何真实点赞。

`android-app/` 仍可独立构建为外接 Termux 的调试版本。单 APK 测试版已在
`/Users/liyangxu/data/workspace/github/termux-mutual-like-app` 中把本模块作为
库嵌入 Termux 官方源码，用户侧只安装一个 APK，主流程不会显示终端页面。

## 单 APK 使用条件

- Android 8.0 或更高版本；
- ARM64 设备；
- 保证至少约 3GB 可用空间；
- 首次点击扫码登录时保持网络连接，App 会执行 NapCat 官方安装流程。

不需要另装 Termux，也不需要授予跨 App 命令权限。

安装脚本从 `NapNeko/NapCat-Installer` 官方 GitHub raw 源下载。文档中的
`nclatest.znin.net` 镜像在部分网络下可能出现 TLS 握手失败，因此 App
会把 Termux 脚本内部的同一下载地址替换为官方 GitHub raw 地址；安装步骤
和其余下载地址保持不变。

## 独立调试模块构建

使用本机已有 Android SDK：

```bash
export ANDROID_HOME="/Users/liyangxu/data/deploy/android/sdk"
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
/Users/liyangxu/.gradle/wrapper/dists/gradle-8.14.3-all/10utluxaxniiv4wxiphsi49nj/gradle-8.14.3/bin/gradle \
  --offline \
  assembleDebug
```

正式构建固定使用 `https://openmemory.cloud:18080`。本机联调只允许通过构建参数
覆盖，不会在正式 APK 中保留模拟器地址：

```bash
gradle -PmutualLikeServerUrl=http://10.0.2.2:18081 assembleDebug
```

生成文件：

```text
app/build/outputs/apk/debug/app-debug.apk
```

单 APK 的构建方式、输入文件校验值和限制见
`/Users/liyangxu/data/workspace/github/termux-mutual-like-app/MUTUAL_LIKE_BUILD.md`。

## 官方依据

- NapCat Termux 安装：
  `https://napneko.github.io/guide/boot/Shell#napcat-termux-安卓-termux-部署-recommend`
- Termux RUN_COMMAND：
  `https://github.com/termux/termux-app/wiki/RUN_COMMAND-Intent`

QQ 登录数据位于 Termux 私有目录，不复制到本 App 或服务器。

## 本机 OneBot 配置

用户点击“配置本机 OneBot”后，App 会在 Termux 的 NapCat proot 内生成一个
随机 token，并把 OneBot 网络配置收敛为：

- HTTP Server：`127.0.0.1:3000`；
- `enableCors=false`；
- `enableWebsocket=false`；
- HTTP/SSE/WS 客户端和 WS 服务端全部禁用；
- `debug=false`。

配置同时写入 `onebot11.json`，如果能从本机 QQ 登录目录识别账号，也会写入
对应的 `onebot11_<QQ号>.json`。这会替换现有 OneBot 网络配置，并重启 NapCat
使其生效。

随机 OneBot token 在 proot 内生成，只保存于 Termux 配置目录和 App 私有
`SharedPreferences`。它不会显示在 App 运行日志，也不会发送到任务服务器。

App 必须先分别调用本机 `get_status` 和 `get_login_info`，确认 QQ 真实在线并
取得当前 QQ 号，才会请求任务服务器。

## 前台互赞执行规则

- QQ 在线后自动注册和心跳，未在白名单时只提示“当前账号未加入测试名单”；
- 用户必须点击“开始互赞”才会执行点赞；
- 生产地址必须是 HTTPS；Android 模拟器本机测试额外允许
  `127.0.0.1`、`10.0.2.2`、`10.0.3.2` 的 HTTP；
- 每次最多租约 8 条任务，当前批结果全部回报后才领取下一批；
- App 留在前台时持续领取到当天无任务；离开前台立即停止领取新任务；
- 目标 QQ 等于当前登录 QQ 时不调用 `send_like`，结果记为
  `skipped_self`；
- 每条任务在发包前同步写入 App 私有任务日志；
- `send_like` 请求体写出后发生超时、断连或响应解析异常时，结果记为
  `uncertain`，客户端绝不自动重试；
- 服务器重复下发同一 `task_id` 时，客户端只补报已保存结果，不会再次点赞；
- 暂时上报失败的结果会保留在本机，本批立即停止；下次用户主动同步时只补报
  结果，不重复调用 `send_like`。

App 不上传 QQ 会话、Cookie、扫码信息、NapCat 配置或 OneBot token。注册请求
只包含 QQ 号、随机安装 ID 和 App 版本；任务结果只包含完成该任务所需的
租约与归一化结果信息。

## 移动端任务协议

所有端点和 JSON 字段集中在 `MutualLikeProtocol.java` 与
`MutualLikeServerClient.java`。

### 注册

```http
POST /api/tools/qq-like/mobile/register
Content-Type: application/json

{
  "qq_number": "123456789",
  "install_id": "app-generated-uuid",
  "app_version": "0.1.0"
}
```

预期响应：

```json
{
  "status": "success",
  "device": {
    "id": "server-device-id",
    "qq_number": "123456789"
  },
  "access_token": "server-issued-token"
}
```

`access_token` 保存在 App 私有 `SharedPreferences`，并绑定服务器根地址，
供后续同步请求的 `Authorization: Bearer ...` 使用；它不会写入可见日志。

### 心跳与领取任务

```http
POST /api/tools/qq-like/mobile/heartbeat
Authorization: Bearer <access_token>
```

```http
POST /api/tools/qq-like/mobile/tasks/lease
Authorization: Bearer <access_token>
Content-Type: application/json

{"limit": 8}
```

租约响应中的任务：

```json
{
  "tasks": [
    {
      "id": "task-id",
      "target_qq": "987654321",
      "times": 10,
      "lease_token": "lease-token",
      "lease_expires_at": "2026-07-28T12:00:00Z"
    }
  ]
}
```

### 上报每条结果

```http
POST /api/tools/qq-like/mobile/tasks/result
Authorization: Bearer <access_token>
Idempotency-Key: <stable-attempt-uuid>
Content-Type: application/json

{
  "task_id": "task-id",
  "lease_token": "lease-token",
  "outcome": "succeeded",
  "result_code": "onebot_ok",
  "result_message": ""
}
```

`outcome` 当前可能为 `succeeded`、`failed` 或 `uncertain`；客户端排除自己的
任务会用 `failed` 加 `result_code=skipped_self` 回报。服务器应以
`Idempotency-Key` 和 `task_id` 对结果写入做幂等处理。租约过期后服务器会把
任务固定为 `uncertain`，不会重新下发或被迟到结果改写。

## 登录态恢复

Linux QQ 的持久化登录数据位于：

```text
/root/.config/QQ
```

App 启动时会读取下面目录中的本机账号标记，并按 NapCat 官方方式追加
`-q <QQ号>` 执行快速登录：

```text
/root/.config/QQ/nt_qq/global/nt_data/Login
```

NapCat 的 WebUI、OneBot 和插件配置位于：

```text
/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config
```

本地 Android 16 ARM64 虚拟机已验证：

1. 停止 NapCat/QQ 后，从完整备份恢复 `/root/.config/QQ` 可以免扫码登录；
2. 重启 Android 后，重新打开 Termux 并由 App 启动 NapCat，仍可免扫码登录；
3. 仅恢复文件但不传 `-q` 时，Linux QQ 会重新显示二维码。

不要只备份单个 token 或 MMKV 文件。QQ 登录态包含账号选择、设备绑定和多份
二进制状态，可靠方案是完整保存 `/root/.config/QQ`。备份等同账号凭证，应只放在
应用私有目录并设置为仅当前用户可读；主动执行 QQ“退出登录”、异地迁移、风控或
服务端会话过期后，旧备份仍可能失效。
