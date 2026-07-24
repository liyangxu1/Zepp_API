"""QQ 互赞专用的隔离 NapCat 运行时和最小 OneBot 客户端。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


NAPCAT_IMAGE = (
    "mlikiowa/napcat-docker@"
    "sha256:29ef959c1e59318d280891b08523f8e2258bbab7717992969811e45f30f81ceb"
)
MANAGED_LABEL_KEY = "com.litianyi.qq-like"
MANAGED_LABEL_VALUE = "napcat-worker"
MANAGED_NETWORK_NAME = "qq-like-isolated"
MANAGED_NETWORK_LABEL_VALUE = "isolated-network"
CONTRIBUTOR_ID_RE = re.compile(r"^qlc_[0-9a-f]{32}$")
QQ_NUMBER_RE = re.compile(r"^[1-9][0-9]{4,11}$")
ALLOWED_ONEBOT_ACTIONS = {
    "get_login_info",
    "get_status",
    "send_like",
    "bot_exit",
}


class NapCatError(RuntimeError):
    """NapCat 驱动的可预期错误。"""


class NapCatRuntimeBusy(NapCatError):
    """服务器已有另一个互赞登录或点赞运行时。"""


class NapCatProtocolError(NapCatError):
    """NapCat 返回了无法安全处理的响应。"""


class JsonTransport(Protocol):
    """JSON HTTP 传输协议，便于在测试中替换。"""

    def post_json(
        self,
        path: str,
        payload: Dict[str, Any],
        *,
        bearer_token: str = "",
    ) -> Dict[str, Any]:
        """提交 JSON 并返回对象。"""


class CommandRunner(Protocol):
    """外部命令执行协议，避免运行时接收任意 Shell。"""

    def run(self, args: List[str], *, timeout: int = 60) -> subprocess.CompletedProcess:
        """执行已构造好的参数数组。"""


class UrllibJsonTransport:
    """使用标准库访问仅绑定本机的 NapCat HTTP 接口。"""

    def __init__(self, base_url: str, *, timeout: int = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def post_json(
        self,
        path: str,
        payload: Dict[str, Any],
        *,
        bearer_token: str = "",
    ) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise NapCatProtocolError(
                f"NapCat HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise NapCatProtocolError(f"NapCat 连接失败: {exc.reason}") from exc
        except OSError as exc:
            raise NapCatProtocolError(f"NapCat 连接失败: {exc}") from exc
        try:
            decoded = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NapCatProtocolError("NapCat 返回的不是有效 JSON") from exc
        if not isinstance(decoded, dict):
            raise NapCatProtocolError("NapCat 返回结构不是对象")
        return decoded


class SubprocessCommandRunner:
    """只执行由驱动内部构造的 Docker 参数。"""

    def run(self, args: List[str], *, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )


@dataclass(frozen=True)
class NapCatSession:
    """一个贡献账号对应的私有运行目录和密钥。"""

    contributor_id: str
    root: Path
    webui_token: str
    onebot_token: str
    webui_port: int
    onebot_port: int
    qq_number: str = ""

    @property
    def container_name(self) -> str:
        return f"qq-like-napcat-{self.contributor_id[-12:]}"

    @property
    def qr_code_path(self) -> Path:
        return self.root / "cache" / "qrcode.png"


def _require_contributor_id(contributor_id: str) -> str:
    normalized = str(contributor_id or "").strip()
    if not CONTRIBUTOR_ID_RE.fullmatch(normalized):
        raise NapCatError("贡献账号 ID 格式错误")
    return normalized


def _require_qq_number(qq_number: str) -> str:
    normalized = str(qq_number or "").strip()
    if not QQ_NUMBER_RE.fullmatch(normalized):
        raise NapCatError("QQ 号格式错误")
    return normalized


def _secure_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


class NapCatWebUIClient:
    """仅处理登录二维码和登录状态，不暴露 WebUI 其他能力。"""

    def __init__(self, transport: JsonTransport, webui_token: str) -> None:
        self.transport = transport
        self.webui_token = webui_token
        self._credential = ""

    def login(self) -> str:
        password_hash = hashlib.sha256(
            f"{self.webui_token}.napcat".encode("utf-8")
        ).hexdigest()
        response = self.transport.post_json(
            "/api/auth/login",
            {"hash": password_hash},
        )
        data = self._require_webui_success(response)
        credential = str(data.get("Credential") or "")
        if not credential:
            raise NapCatProtocolError("NapCat WebUI 未返回登录凭证")
        self._credential = credential
        return credential

    def request_qr_code(self) -> str:
        data = self._authenticated_post("/api/QQLogin/GetQQLoginQrcode")
        qr_code_url = str(data.get("qrcode") or "").strip()
        if not qr_code_url.startswith("https://"):
            raise NapCatProtocolError("NapCat 未返回安全二维码地址")
        return qr_code_url

    def refresh_qr_code(self) -> Dict[str, Any]:
        return self._authenticated_post("/api/QQLogin/RefreshQRcode")

    def check_login_status(self) -> Dict[str, Any]:
        return self._authenticated_post("/api/QQLogin/CheckLoginStatus")

    def _authenticated_post(self, path: str) -> Dict[str, Any]:
        if not self._credential:
            self.login()
        response = self.transport.post_json(
            path,
            {},
            bearer_token=self._credential,
        )
        return self._require_webui_success(response)

    @staticmethod
    def _require_webui_success(response: Dict[str, Any]) -> Dict[str, Any]:
        if response.get("code") != 0:
            message = str(response.get("message") or "NapCat WebUI 请求失败")
            raise NapCatProtocolError(message)
        data = response.get("data")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise NapCatProtocolError("NapCat WebUI data 不是对象")
        return data


class NapCatOneBotClient:
    """只开放状态、登录信息、点赞和退出四个 OneBot 动作。"""

    def __init__(self, transport: JsonTransport, onebot_token: str) -> None:
        self.transport = transport
        self.onebot_token = onebot_token

    def get_status(self) -> Dict[str, Any]:
        return self._call("get_status", {})

    def get_login_info(self) -> Dict[str, Any]:
        data = self._call("get_login_info", {})
        user_id = str(data.get("user_id") or "").strip()
        if not QQ_NUMBER_RE.fullmatch(user_id):
            raise NapCatProtocolError("NapCat 未返回有效登录 QQ")
        return data

    def send_like(self, target_qq: str, *, times: int = 10) -> Dict[str, Any]:
        qq_number = _require_qq_number(target_qq)
        normalized_times = int(times)
        if not 1 <= normalized_times <= 10:
            raise NapCatError("单次点赞次数必须在 1-10 次之间")
        return self._call(
            "send_like",
            {
                "user_id": qq_number,
                "times": normalized_times,
            },
        )

    def bot_exit(self) -> Dict[str, Any]:
        return self._call("bot_exit", {})

    def _call(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if action not in ALLOWED_ONEBOT_ACTIONS:
            raise NapCatError("禁止调用未授权的 OneBot 动作")
        response = self.transport.post_json(
            f"/{action}",
            payload,
            bearer_token=self.onebot_token,
        )
        if response.get("status") != "ok" or int(response.get("retcode", -1)) != 0:
            message = str(
                response.get("message")
                or response.get("wording")
                or f"{action} 执行失败"
            )
            raise NapCatProtocolError(message)
        data = response.get("data")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise NapCatProtocolError("OneBot data 不是对象")
        return data


class DockerNapCatRuntime:
    """管理互赞专用的单个隔离 NapCat 容器。"""

    def __init__(
        self,
        data_root: Path,
        *,
        webui_port: int = 16199,
        onebot_port: int = 16100,
        image: str = NAPCAT_IMAGE,
        runner: Optional[CommandRunner] = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.webui_port = int(webui_port)
        self.onebot_port = int(onebot_port)
        self.image = image
        self.runner = runner or SubprocessCommandRunner()
        if self.image != NAPCAT_IMAGE:
            raise NapCatError("NapCat 镜像必须使用已验证的固定摘要")
        for port in (self.webui_port, self.onebot_port):
            if not 1024 <= port <= 65535:
                raise NapCatError("NapCat 本机端口范围错误")

    def prepare_session(self, contributor_id: str) -> NapCatSession:
        normalized_id = _require_contributor_id(contributor_id)
        session_root = self.data_root / "sessions" / normalized_id
        secret_path = session_root / "private.json"
        for child_name in ("qq", "config", "logs", "cache"):
            child = session_root / child_name
            child.mkdir(parents=True, exist_ok=True)
            os.chmod(child, 0o700)
        os.chmod(session_root, 0o700)

        private_data: Dict[str, Any] = {}
        if secret_path.exists():
            try:
                loaded = json.loads(secret_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise NapCatError("NapCat 私有会话文件损坏") from exc
            if not isinstance(loaded, dict):
                raise NapCatError("NapCat 私有会话文件格式错误")
            private_data = loaded

        webui_token = str(private_data.get("webui_token") or secrets.token_urlsafe(32))
        onebot_token = str(private_data.get("onebot_token") or secrets.token_urlsafe(32))
        qq_number = str(private_data.get("qq_number") or "")
        if qq_number:
            _require_qq_number(qq_number)
        _secure_write_json(
            secret_path,
            {
                "webui_token": webui_token,
                "onebot_token": onebot_token,
                "qq_number": qq_number,
            },
        )
        self._write_onebot_config(session_root / "config" / "onebot11.json", onebot_token)
        return NapCatSession(
            contributor_id=normalized_id,
            root=session_root,
            webui_token=webui_token,
            onebot_token=onebot_token,
            webui_port=self.webui_port,
            onebot_port=self.onebot_port,
            qq_number=qq_number,
        )

    def record_login(self, contributor_id: str, qq_number: str) -> NapCatSession:
        session = self.prepare_session(contributor_id)
        normalized_qq = _require_qq_number(qq_number)
        _secure_write_json(
            session.root / "private.json",
            {
                "webui_token": session.webui_token,
                "onebot_token": session.onebot_token,
                "qq_number": normalized_qq,
            },
        )
        return self.prepare_session(contributor_id)

    def start(self, contributor_id: str) -> NapCatSession:
        session = self.prepare_session(contributor_id)
        managed_names = self._managed_container_names()
        if managed_names:
            if managed_names == [session.container_name]:
                return session
            raise NapCatRuntimeBusy("另一个 QQ 正在登录或执行点赞，请稍后再试")
        self._ensure_isolated_network()

        command = [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--pull",
            "never",
            "--name",
            session.container_name,
            "--label",
            f"{MANAGED_LABEL_KEY}={MANAGED_LABEL_VALUE}",
            "--network",
            MANAGED_NETWORK_NAME,
            "--memory",
            "512m",
            "--cpus",
            "0.75",
            "--pids-limit",
            "160",
            "--security-opt",
            "no-new-privileges:true",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=2",
            "-e",
            f"WEBUI_TOKEN={session.webui_token}",
            "-v",
            f"{session.root / 'qq'}:/app/.config/QQ",
            "-v",
            f"{session.root / 'config'}:/app/napcat/config",
            "-v",
            f"{session.root / 'logs'}:/app/napcat/logs",
            "-v",
            f"{session.root / 'cache'}:/app/napcat/cache",
            "-p",
            f"127.0.0.1:{session.webui_port}:6099",
            "-p",
            f"127.0.0.1:{session.onebot_port}:3000",
        ]
        if session.qq_number:
            command.extend(["-e", f"ACCOUNT={session.qq_number}"])
        command.append(self.image)
        completed = self.runner.run(command, timeout=90)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:500]
            raise NapCatError(f"启动 NapCat 失败: {detail or 'Docker 返回错误'}")
        return session

    def stop(self, contributor_id: str) -> None:
        session = self.prepare_session(contributor_id)
        managed_names = self._managed_container_names()
        if session.container_name not in managed_names:
            return
        completed = self.runner.run(
            ["docker", "stop", "--time", "20", session.container_name],
            timeout=40,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:500]
            raise NapCatError(f"停止 NapCat 失败: {detail or 'Docker 返回错误'}")

    def stop_all_managed(self) -> int:
        stopped = 0
        for container_name in self._managed_container_names():
            completed = self.runner.run(
                ["docker", "stop", "--time", "20", container_name],
                timeout=40,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()[:500]
                raise NapCatError(
                    f"停止遗留 NapCat 失败: {detail or 'Docker 返回错误'}"
                )
            stopped += 1
        return stopped

    def delete_session(self, contributor_id: str) -> None:
        session = self.prepare_session(contributor_id)
        if session.container_name in self._managed_container_names():
            raise NapCatError("贡献账号仍在运行，不能删除登录信息")
        if session.root.exists():
            shutil.rmtree(session.root)

    def wait_for_webui(
        self,
        session: NapCatSession,
        *,
        attempts: int = 30,
        interval_seconds: float = 1.0,
    ) -> NapCatWebUIClient:
        client = NapCatWebUIClient(
            UrllibJsonTransport(f"http://127.0.0.1:{session.webui_port}"),
            session.webui_token,
        )
        last_error: Optional[Exception] = None
        for _ in range(max(1, attempts)):
            try:
                client.login()
                return client
            except NapCatProtocolError as exc:
                last_error = exc
                time.sleep(max(0.0, interval_seconds))
        raise NapCatError(f"NapCat WebUI 启动超时: {last_error or '未知错误'}")

    def onebot_client(self, session: NapCatSession) -> NapCatOneBotClient:
        return NapCatOneBotClient(
            UrllibJsonTransport(f"http://127.0.0.1:{session.onebot_port}"),
            session.onebot_token,
        )

    def read_qr_code_png(self, session: NapCatSession) -> bytes:
        try:
            payload = session.qr_code_path.read_bytes()
        except OSError as exc:
            raise NapCatError("登录二维码文件尚未生成") from exc
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise NapCatError("登录二维码文件不是有效 PNG")
        return payload

    def _managed_container_names(self) -> List[str]:
        completed = self.runner.run(
            [
                "docker",
                "ps",
                "--all",
                "--filter",
                f"label={MANAGED_LABEL_KEY}={MANAGED_LABEL_VALUE}",
                "--format",
                "{{.Names}}",
            ],
            timeout=15,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:500]
            raise NapCatError(f"读取 NapCat 容器状态失败: {detail or 'Docker 返回错误'}")
        return sorted(
            name.strip()
            for name in (completed.stdout or "").splitlines()
            if name.strip()
        )

    def _ensure_isolated_network(self) -> None:
        format_expression = (
            f'{{{{.Name}}}}|{{{{.Label "{MANAGED_LABEL_KEY}"}}}}'
        )
        completed = self.runner.run(
            [
                "docker",
                "network",
                "ls",
                "--filter",
                f"name=^{MANAGED_NETWORK_NAME}$",
                "--format",
                format_expression,
            ],
            timeout=15,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:500]
            raise NapCatError(
                f"读取 QQ 互赞隔离网络失败: {detail or 'Docker 返回错误'}"
            )
        rows = [
            row.strip()
            for row in (completed.stdout or "").splitlines()
            if row.strip()
        ]
        if rows:
            try:
                network_name, label_value = rows[0].split("|", 1)
            except ValueError as exc:
                raise NapCatError("QQ 互赞隔离网络返回格式错误") from exc
            if (
                network_name != MANAGED_NETWORK_NAME
                or label_value != MANAGED_NETWORK_LABEL_VALUE
            ):
                raise NapCatError("同名 Docker 网络不属于 QQ 互赞工具")
            return

        created = self.runner.run(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--label",
                f"{MANAGED_LABEL_KEY}={MANAGED_NETWORK_LABEL_VALUE}",
                MANAGED_NETWORK_NAME,
            ],
            timeout=30,
        )
        if created.returncode != 0:
            detail = (created.stderr or created.stdout or "").strip()[:500]
            raise NapCatError(
                f"创建 QQ 互赞隔离网络失败: {detail or 'Docker 返回错误'}"
            )

    @staticmethod
    def _write_onebot_config(path: Path, token: str) -> None:
        _secure_write_json(
            path,
            {
                "network": {
                    "httpServers": [
                        {
                            "name": "qq-like-http",
                            "enable": True,
                            "port": 3000,
                            "host": "0.0.0.0",
                            "enableCors": False,
                            "enableWebsocket": False,
                            "messagePostFormat": "array",
                            "token": token,
                            "debug": False,
                        }
                    ],
                    "httpClients": [],
                    "websocketServers": [],
                    "websocketClients": [],
                },
                "musicSignUrl": "",
                "enableLocalFile2Url": False,
                "parseMultMsg": False,
            },
        )
