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

### 5) HTTP / HTTPS

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
