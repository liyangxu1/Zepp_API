#!/usr/bin/env python3
"""Python refactor of apgk/Zepp_API.

Usage:
  # CLI mode
  python app.py --user 13800138000 --pwd 123456 --step 20000

  # HTTP API mode
  python app.py --serve --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.cookiejar
import json
import mimetypes
import os
import posixpath
import random
import re
import secrets
import shutil
import sqlite3
import ssl
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from urllib.error import HTTPError, URLError
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, Optional, Tuple, Union
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qq_like import (
    NapCatError,
    QQLikeStoreError,
    QQMutualLikeService,
    QQMutualLikeServiceError,
)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_optional_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


DB_PATH = Path(__file__).with_name("tool_records.sqlite3")
STEP_MAX = 98800
SERVER_SOCKET_TIMEOUT_SECONDS = _env_int("ZEPP_SERVER_SOCKET_TIMEOUT_SECONDS", 20, 1, 300)
SERVER_PROTOCOL_PEEK_TIMEOUT_SECONDS = _env_int("ZEPP_SERVER_PROTOCOL_PEEK_TIMEOUT_SECONDS", 3, 1, 60)
SERVER_MAX_ACTIVE_CONNECTIONS = _env_int("ZEPP_SERVER_MAX_ACTIVE_CONNECTIONS", 100, 1, 1000)
NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


class ClientConnectionProbeError(OSError):
    pass


SHARED_DEVICE_STEP_DEFAULT = 20000
SHARED_DEVICE_SELF_BLOCKED_ACCOUNT_HASHES = {
    "418fc91ec857946d7179666f051d3553c035ceea3da1efcd74960cdb4aa71c86",
}
TOOL_API_KEY = os.environ.get("ZEPP_TOOL_API_KEY", "zepp-tool-default-key")
BAIDU_NETDISK_DEFAULT_SAVE_ROOT = os.environ.get("BAIDU_NETDISK_DEFAULT_SAVE_ROOT", "/apps/zepp-api-baidu-tmp").strip() or "/apps/zepp-api-baidu-tmp"
BAIDU_SERVER_DOWNLOAD_DEFAULT_ROOT = os.environ.get(
    "BAIDU_SERVER_DOWNLOAD_DEFAULT_ROOT",
    str(Path(__file__).with_name("baidu_share_downloads")),
).strip() or str(Path(__file__).with_name("baidu_share_downloads"))
BAIDU_SHARE_WORKER_ENABLED = os.environ.get("BAIDU_SHARE_WORKER_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
BAIDU_SHARE_WORKER_INTERVAL_SECONDS = _env_int("BAIDU_SHARE_WORKER_INTERVAL_SECONDS", 8, 1, 300)
BAIDU_SHARE_COMMAND_TIMEOUT_SECONDS = _env_int("BAIDU_SHARE_COMMAND_TIMEOUT_SECONDS", 1800, 30, 86400)
BAIDU_SHARE_PREFLIGHT_MAX_PAGES = _env_int("BAIDU_SHARE_PREFLIGHT_MAX_PAGES", 20, 1, 300)
BAIDU_SHARE_PREFLIGHT_TIMEOUT_SECONDS = _env_int("BAIDU_SHARE_PREFLIGHT_TIMEOUT_SECONDS", 45, 5, 180)
BAIDU_SHARE_FILE_RETENTION_HOURS = _env_int("BAIDU_SHARE_FILE_RETENTION_HOURS", 24, 1, 24 * 90)
BAIDU_SHARE_FILE_RETENTION_MINUTES = _env_optional_int(
    "BAIDU_SHARE_FILE_RETENTION_MINUTES",
    BAIDU_SHARE_FILE_RETENTION_HOURS * 60,
    1,
    24 * 90 * 60,
)
BAIDU_SHARE_STORAGE_MAX_GB = _env_int("BAIDU_SHARE_STORAGE_MAX_GB", 20, 1, 10000)
BAIDU_SHARE_STORAGE_MAX_BYTES = BAIDU_SHARE_STORAGE_MAX_GB * 1024 * 1024 * 1024
BAIDU_SHARE_CLEANUP_INTERVAL_SECONDS = _env_int("BAIDU_SHARE_CLEANUP_INTERVAL_SECONDS", 600, 60, 86400)
BAIDUPCS_GO_CONFIG_DIR = os.environ.get(
    "BAIDUPCS_GO_CONFIG_DIR",
    str(Path(__file__).with_name(".baidupcs")),
).strip() or str(Path(__file__).with_name(".baidupcs"))
BAIDUPCS_GO_BIN = os.environ.get("BAIDUPCS_GO_BIN", "").strip()
BAIDU_QR_LOGIN_TPL = os.environ.get("BAIDU_QR_LOGIN_TPL", "netdiskAccept").strip() or "netdiskAccept"
BAIDU_QR_LOGIN_USER_AGENT = os.environ.get(
    "BAIDU_QR_LOGIN_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
).strip()
BAIDU_QR_SIGN_RE = re.compile(r"^[0-9a-fA-F]{16,80}$")
BAIDU_SHARE_RAW_TEXT_MAX_LENGTH = 5000
BAIDU_SHARE_URL_RE = re.compile(
    r"(https?://pan\.baidu\.com/[^\s\"'<>]+|pan\.baidu\.com/[^\s\"'<>]+)",
    re.IGNORECASE,
)
BAIDU_SHARE_CODE_RE = re.compile(
    r"(?:提取码|提取碼|访问码|訪問碼|验证码|密[码碼]|pwd|code)[\s:：=为是-]*([A-Za-z0-9]{4})",
    re.IGNORECASE,
)
BAIDU_SHARE_STATUS_META = {
    "queued": {
        "label": "排队中",
        "detail": "任务已提交，等待服务器开始转存和下载。",
        "level": "info",
    },
    "worker_unavailable": {
        "label": "执行器不可用",
        "detail": "服务器还没有找到 BaiduPCS-Go，可安装或配置后自动继续。",
        "level": "warning",
    },
    "login_required": {
        "label": "需要登录",
        "detail": "服务器 BaiduPCS-Go 尚未登录百度网盘账号，登录后会继续处理。",
        "level": "warning",
    },
    "transferring": {
        "label": "转存中",
        "detail": "正在把分享文件保存到服务器登录的百度网盘账号。",
        "level": "running",
    },
    "transfer_failed": {
        "label": "转存失败",
        "detail": "分享链接转存到网盘失败，需要检查链接、提取码或网盘账号状态。",
        "level": "failed",
    },
    "downloading": {
        "label": "下载中",
        "detail": "文件已转存，正在从百度网盘下载到服务器。",
        "level": "running",
    },
    "completed": {
        "label": "已完成",
        "detail": "服务器已下载完成，后续可在页面提供本地下载入口。",
        "level": "success",
    },
    "partial_completed": {
        "label": "部分完成",
        "detail": "服务器已保留当前下载到的文件，可先打包下载 ZIP。",
        "level": "warning",
    },
    "failed": {
        "label": "失败",
        "detail": "任务执行失败，需要查看后台错误信息后重试。",
        "level": "failed",
    },
    "canceled": {
        "label": "已取消",
        "detail": "任务已取消，不会继续转存或下载。",
        "level": "warning",
    },
    "expired": {
        "label": "已过期",
        "detail": "任务或服务器文件已过期清理。",
        "level": "warning",
    },
}
ADMIN_PASSWORD = os.environ.get("ZEPP_ADMIN_PASSWORD", "").strip()
ADMIN_SESSION_COOKIE = "zepp_admin_session"
ADMIN_SESSION_TTL_SECONDS = 8 * 60 * 60
ADMIN_SESSIONS: Dict[str, dict] = {}
QQ_LIKE_ENABLED = os.environ.get("QQ_LIKE_ENABLED", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
QQ_LIKE_DATA_ROOT = Path(
    os.environ.get(
        "QQ_LIKE_DATA_ROOT",
        str(Path(__file__).with_name(".qq-like-data")),
    )
).expanduser()
QQ_LIKE_DB_PATH = Path(
    os.environ.get(
        "QQ_LIKE_DB_PATH",
        str(QQ_LIKE_DATA_ROOT / "qq_like.sqlite3"),
    )
).expanduser()
QQ_LIKE_WEBUI_PORT = _env_int("QQ_LIKE_WEBUI_PORT", 16199, 1024, 65535)
QQ_LIKE_ONEBOT_PORT = _env_int("QQ_LIKE_ONEBOT_PORT", 16100, 1024, 65535)
QQ_LIKE_MAX_CONTRIBUTORS = _env_int(
    "QQ_LIKE_MAX_CONTRIBUTORS",
    20,
    1,
    200,
)
QQ_LIKE_PENDING_RETENTION_HOURS = _env_int(
    "QQ_LIKE_PENDING_RETENTION_HOURS",
    24,
    1,
    24 * 30,
)
DEVICE_BIND_QR_ENV = os.environ.get("DEVICE_BIND_QR_PATH", "").strip()
DEVICE_BIND_QR_TOKEN_TTL_SECONDS = 120
DEVICE_BIND_QR_UNAVAILABLE_MESSAGE = "当前二维码有设备未解绑，暂时不能使用，请联系管理员。"
DEVICE_BIND_QR_DISTRIBUTION_PAUSED = os.environ.get("DEVICE_BIND_QR_DISTRIBUTION_PAUSED", "0").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
DEVICE_SHARE_UPLOAD_ENABLED = os.environ.get("DEVICE_SHARE_UPLOAD_ENABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
QQ_GROUP_NAME = "接待喵的小窝"
QQ_GROUP_NUMBER = "1084427315"
LEGACY_LOGIN_DEVICE_ID = "2C8B4939-0CCD-4E94-8CBA-CB8EA6E613A1"
LEGACY_LAST_DEVICE_ID = "DA932FFFFE8816E7"
DEVICE_BIND_QR_TOKENS: Dict[str, dict] = {}
DEVICE_BIND_QR_TOKEN_ISSUES: Dict[str, list] = {}
DEVICE_BIND_QR_RATE_LIMIT_WINDOW_SECONDS = 60
DEVICE_BIND_QR_RATE_LIMIT_COUNT = 12
DEVICE_SHARE_QR_MAX_BYTES = 2 * 1024 * 1024
SHARED_DEVICE_STEP_LOCK = threading.Lock()
PREFERRED_DEVICE_BINDINGS = {
    "1744731920@163.com": {
        "login_device_id": LEGACY_LOGIN_DEVICE_ID,
        "last_deviceid": LEGACY_LAST_DEVICE_ID,
        "data_did": LEGACY_LAST_DEVICE_ID,
        "device_type": "0",
        "device_model": "android_phone",
    }
}

try:
    DEVICE_BIND_QR_TOKEN_TTL_SECONDS = max(
        30,
        min(600, int(os.environ.get("DEVICE_BIND_QR_TOKEN_TTL_SECONDS", str(DEVICE_BIND_QR_TOKEN_TTL_SECONDS)))),
    )
except ValueError:
    DEVICE_BIND_QR_TOKEN_TTL_SECONDS = 120

try:
    SHARED_DEVICE_STEP_DEFAULT = max(
        1,
        min(STEP_MAX - 1, int(os.environ.get("SHARED_DEVICE_STEP_DEFAULT", str(SHARED_DEVICE_STEP_DEFAULT)))),
    )
except ValueError:
    SHARED_DEVICE_STEP_DEFAULT = 20000


def _asset_dir() -> Path:
    return Path(__file__).with_name("assets")


def _device_share_upload_dir() -> Path:
    configured = os.environ.get("DEVICE_SHARE_UPLOAD_DIR", "").strip()
    if configured:
        upload_dir = Path(configured).expanduser()
        if not upload_dir.is_absolute():
            upload_dir = Path(__file__).parent.joinpath(upload_dir)
        return upload_dir
    return Path(__file__).with_name("device_share_uploads")


def _resolve_static_qr_path() -> Optional[Path]:
    if DEVICE_BIND_QR_ENV:
        configured_path = Path(DEVICE_BIND_QR_ENV).expanduser()
        if not configured_path.is_absolute():
            configured_path = Path(__file__).parent.joinpath(configured_path)
        return configured_path if configured_path.exists() and configured_path.is_file() else None

    for name in (
        "device-bind-qr.jpg",
        "device-bind-qr.jpeg",
        "device-bind-qr.png",
        "device-bind-qr.webp",
    ):
        candidate = _asset_dir().joinpath(name)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _resolve_configured_qr_path() -> Optional[Path]:
    if DEVICE_BIND_QR_DISTRIBUTION_PAUSED:
        return None
    latest_share = get_latest_device_share()
    if latest_share:
        shared_qr = Path(str(latest_share.get("qr_path") or ""))
        if shared_qr.exists() and shared_qr.is_file():
            return shared_qr
    return _resolve_static_qr_path()


def device_bind_qr_status() -> dict:
    latest_share = get_latest_device_share()
    configured = _resolve_configured_qr_path() is not None
    shared_step = get_shared_device_step_state(latest_share) if latest_share else None
    return {
        "configured": configured,
        "paused": DEVICE_BIND_QR_DISTRIBUTION_PAUSED,
        "unavailable_message": DEVICE_BIND_QR_UNAVAILABLE_MESSAGE if DEVICE_BIND_QR_DISTRIBUTION_PAUSED else "",
        "expires_in_seconds": DEVICE_BIND_QR_TOKEN_TTL_SECONDS,
        "source": "device_share" if latest_share else ("static" if _resolve_static_qr_path() else "none"),
        "share_count": count_device_shares(),
        "latest_share": public_device_share(latest_share) if latest_share else None,
        "shared_step": shared_step,
        "upload_enabled": DEVICE_SHARE_UPLOAD_ENABLED,
        "hint": (
            DEVICE_BIND_QR_UNAVAILABLE_MESSAGE
            if DEVICE_BIND_QR_DISTRIBUTION_PAUSED
            else "系统默认共享账号由服务器后台配置；二维码也可通过 assets/device-bind-qr.jpg 作为兜底配置。"
        ),
    }


def guess_image_content_type(path: Path, data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _request_fingerprint(remote_addr: str, user_agent: str) -> str:
    return f"{remote_addr or ''}|{(user_agent or '')[:180]}"


def _cleanup_qr_tokens(now: Optional[float] = None) -> None:
    current = now or time.time()
    expired = [
        token
        for token, meta in DEVICE_BIND_QR_TOKENS.items()
        if float(meta.get("expires_at", 0)) <= current or int(meta.get("uses", 0)) >= 3
    ]
    for token in expired:
        DEVICE_BIND_QR_TOKENS.pop(token, None)


def issue_device_bind_qr_token(remote_addr: str, user_agent: str) -> dict:
    now = time.time()
    _cleanup_qr_tokens(now)
    fingerprint = _request_fingerprint(remote_addr, user_agent)
    recent_issues = [
        ts
        for ts in DEVICE_BIND_QR_TOKEN_ISSUES.get(fingerprint, [])
        if now - float(ts) < DEVICE_BIND_QR_RATE_LIMIT_WINDOW_SECONDS
    ]
    if len(recent_issues) >= DEVICE_BIND_QR_RATE_LIMIT_COUNT:
        DEVICE_BIND_QR_TOKEN_ISSUES[fingerprint] = recent_issues
        raise ValueError("二维码加载过于频繁，请稍后再试")
    recent_issues.append(now)
    DEVICE_BIND_QR_TOKEN_ISSUES[fingerprint] = recent_issues
    token = secrets.token_urlsafe(24)
    DEVICE_BIND_QR_TOKENS[token] = {
        "fingerprint": fingerprint,
        "expires_at": now + DEVICE_BIND_QR_TOKEN_TTL_SECONDS,
        "uses": 0,
    }
    return {
        "token": token,
        "expires_in_seconds": DEVICE_BIND_QR_TOKEN_TTL_SECONDS,
    }


def validate_device_bind_qr_token(token: str, remote_addr: str, user_agent: str) -> Tuple[bool, str]:
    _cleanup_qr_tokens()
    meta = DEVICE_BIND_QR_TOKENS.get(token or "")
    if not meta:
        return False, "二维码访问令牌无效或已过期"
    if meta.get("fingerprint") != _request_fingerprint(remote_addr, user_agent):
        return False, "二维码访问环境不匹配，请刷新页面后重试"
    if int(meta.get("uses", 0)) >= 3:
        DEVICE_BIND_QR_TOKENS.pop(token, None)
        return False, "二维码访问次数已用完，请重新加载"
    meta["uses"] = int(meta.get("uses", 0)) + 1
    return True, ""

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import padding as _padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception:  # pragma: no cover
    default_backend = None
    _padding = None
    Cipher = None
    algorithms = None
    modes = None


def normalize_device_account(account: str) -> str:
    value = (account or "").strip()
    return value.lower() if "@" in value else value


def normalize_login_account(account: str) -> str:
    value = (account or "").strip()
    if "@" in value:
        return value.lower()
    lower = value.lower()
    for domain in ("qq.com", "163.com", "126.com", "gmail.com", "proton.me", "outlook.com", "hotmail.com"):
        if lower.endswith(domain) and len(value) > len(domain):
            return f"{value[:-len(domain)]}@{domain}".lower()
    return value


def is_shared_device_self_blocked_account(account: str) -> bool:
    normalized = normalize_login_account(account)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest in SHARED_DEVICE_SELF_BLOCKED_ACCOUNT_HASHES


def random_hex_id(length: int = 16) -> str:
    alphabet = "0123456789ABCDEF"
    rng = random.SystemRandom()
    return "".join(rng.choice(alphabet) for _ in range(length))


def build_transient_device_binding(account: str = "") -> dict:
    last_deviceid = random_hex_id(16)
    return {
        "account": normalize_device_account(account),
        "login_device_id": str(uuid.uuid4()).upper(),
        "last_deviceid": last_deviceid,
        "data_did": last_deviceid,
        "device_type": "0",
        "device_model": "android_phone",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def init_device_binding_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS account_device_bindings (
                account TEXT PRIMARY KEY,
                login_device_id TEXT NOT NULL,
                last_deviceid TEXT NOT NULL,
                data_did TEXT NOT NULL,
                device_type TEXT NOT NULL DEFAULT '0',
                device_model TEXT NOT NULL DEFAULT 'android_phone',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def init_zepp_token_cache_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS zepp_token_cache (
                account TEXT PRIMARY KEY,
                login_device_id TEXT NOT NULL,
                login_token TEXT,
                app_token TEXT,
                user_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def get_cached_zepp_tokens(account: str) -> Optional[dict]:
    normalized = normalize_login_account(account)
    if not normalized:
        return None
    init_zepp_token_cache_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT account, login_device_id, login_token, app_token, user_id, created_at, updated_at
            FROM zepp_token_cache
            WHERE account = ?
            """,
            (normalized,),
        ).fetchone()
        return dict(row) if row else None


def save_zepp_tokens(
    *,
    account: str,
    login_device_id: str,
    login_token: str,
    app_token: str,
    user_id: str,
) -> None:
    normalized = normalize_login_account(account)
    if not normalized:
        return
    init_zepp_token_cache_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT created_at FROM zepp_token_cache WHERE account = ?",
            (normalized,),
        ).fetchone()
        created_at = row[0] if row else now
        conn.execute(
            """
            INSERT OR REPLACE INTO zepp_token_cache (
                account, login_device_id, login_token, app_token, user_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized,
                login_device_id,
                login_token or "",
                app_token or "",
                str(user_id or ""),
                created_at,
                now,
            ),
        )


def clear_cached_zepp_tokens(account: str) -> None:
    normalized = normalize_login_account(account)
    if not normalized:
        return
    init_zepp_token_cache_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM zepp_token_cache WHERE account = ?", (normalized,))


def get_or_create_account_device_binding(account: str) -> dict:
    normalized = normalize_login_account(account)
    if not normalized:
        return build_transient_device_binding("")

    preferred = PREFERRED_DEVICE_BINDINGS.get(normalized)
    if preferred:
        init_device_binding_db()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT created_at FROM account_device_bindings WHERE account = ?",
                (normalized,),
            ).fetchone()
            created_at = row["created_at"] if row else now
            conn.execute(
                """
                INSERT OR REPLACE INTO account_device_bindings (
                    account, login_device_id, last_deviceid, data_did,
                    device_type, device_model, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized,
                    preferred["login_device_id"],
                    preferred["last_deviceid"],
                    preferred["data_did"],
                    preferred["device_type"],
                    preferred["device_model"],
                    created_at,
                    now,
                ),
            )
        return {
            "account": normalized,
            "created_at": created_at,
            **preferred,
        }

    init_device_binding_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT account, login_device_id, last_deviceid, data_did, device_type, device_model, created_at
            FROM account_device_bindings
            WHERE account = ?
            """,
            (normalized,),
        ).fetchone()
        if row:
            return dict(row)

        binding = build_transient_device_binding(normalized)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            conn.execute(
                """
                INSERT INTO account_device_bindings (
                    account, login_device_id, last_deviceid, data_did,
                    device_type, device_model, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    binding["account"],
                    binding["login_device_id"],
                    binding["last_deviceid"],
                    binding["data_did"],
                    binding["device_type"],
                    binding["device_model"],
                    now,
                    now,
                ),
            )
            return binding
        except sqlite3.IntegrityError:
            row = conn.execute(
                """
                SELECT account, login_device_id, last_deviceid, data_did, device_type, device_model, created_at
                FROM account_device_bindings
                WHERE account = ?
                """,
                (normalized,),
            ).fetchone()
            return dict(row) if row else binding


def mask_device_identifier(value: str) -> str:
    text = str(value or "")
    if len(text) <= 10:
        return text
    return f"{text[:4]}****{text[-4:]}"


class MiMotionError(Exception):
    """Raised when Zepp API call failed for business reasons."""


class MiMotionRunner:
    KEY = "xeNtBVqzDc6tuNTh".encode("utf-8")
    IV = "MAAAYAAAAAAAAABg".encode("utf-8")
    APP_NAME = "com.xiaomi.hm.health"
    APP_VERSION = "6.14.0"
    USER_AGENT = "MiFit6.14.0 (2211133C; Android 15; Density/2.75)"
    DEVICE_ID = LEGACY_LOGIN_DEVICE_ID
    LAST_DEVICE_ID = LEGACY_LAST_DEVICE_ID

    def __init__(self, user: str, password: str):
        self.user = normalize_login_account(user)
        self.password = (password or "").strip()
        self.invalid = not (self.user and self.password)
        self.device_binding = get_or_create_account_device_binding(self.user)

    @property
    def login_device_id(self) -> str:
        return str(self.device_binding.get("login_device_id") or self.DEVICE_ID)

    @property
    def last_deviceid(self) -> str:
        return str(self.device_binding.get("last_deviceid") or self.LAST_DEVICE_ID)

    @property
    def data_did(self) -> str:
        return str(self.device_binding.get("data_did") or self.last_deviceid)

    @property
    def device_type(self) -> str:
        return str(self.device_binding.get("device_type") or "0")

    @staticmethod
    def desensitize_user_name(user: str) -> str:
        l = len(user)
        if l <= 8:
            left = max(l // 3, 1)
            return f"{user[:left]}***{user[-left:]}"
        return f"{user[:3]}****{user[-4:]}"

    def _encrypt_data(self, plain: str) -> bytes:
        data = plain.encode("utf-8")
        # PKCS7 padding for AES-128-CBC
        block_size = 16
        pad_len = block_size - (len(data) % block_size)
        if pad_len == 0:
            pad_len = block_size
        padded = data + bytes([pad_len] * pad_len)

        if Cipher is None:
            # fallback to openssl command-line (usually available on macOS/Linux)
            try:
                completed = subprocess.run(
                    [
                        "openssl",
                        "enc",
                        "-aes-128-cbc",
                        "-K",
                        self.KEY.hex(),
                        "-iv",
                        self.IV.hex(),
                        "-nosalt",
                        "-nopad",
                    ],
                    input=padded,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
            except FileNotFoundError as exc:  # pragma: no cover
                raise MiMotionError("加密失败：缺少 cryptography 包并且系统未安装 openssl") from exc

            if completed.returncode != 0:
                msg = completed.stderr.decode("utf-8", errors="ignore")
                raise MiMotionError(f"加密失败：{msg}")
            return completed.stdout

        # cryptography path
        padder = _padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        cipher = Cipher(algorithms.AES(self.KEY), modes.CBC(self.IV), backend=default_backend())
        encryptor = cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def _request(
        self,
        url: str,
        data: Optional[object] = None,
        app_token: Optional[str] = None,
        ekv: bool = False,
        follow_redirects: bool = True,
        extra_headers: Optional[Dict[str, str]] = None,
        method: Optional[str] = None,
    ) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.8",
            "Connection": "keep-alive",
            "app_name": self.APP_NAME,
            "appname": self.APP_NAME,
            "appplatform": "android_phone",
            "User-Agent": self.USER_AGENT,
        }
        if ekv:
            headers["x-hm-ekv"] = "1"
        if app_token:
            headers["apptoken"] = app_token
        if extra_headers:
            headers.update(extra_headers)

        body = None
        if data is not None:
            if isinstance(data, dict):
                body = urllib.parse.urlencode(data).encode("utf-8")
            else:
                body = bytes(data)
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        request = urllib.request.Request(url=url, data=body, method=method or ("POST" if body is not None else "GET"))
        for key, value in headers.items():
            request.add_header(key, value)

        context = ssl._create_unverified_context()
        try:
            if follow_redirects:
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
            else:
                class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        return None

                opener = urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=context),
                    NoRedirectHandler(),
                )

            with opener.open(request, timeout=10) as resp:
                raw_body = resp.read()
                status = getattr(resp, "status", 200)
                header_text = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())
                return {
                    "status": str(status),
                    "header": header_text,
                    "body": raw_body.decode("utf-8", errors="ignore"),
                }
        except HTTPError as exc:
            if not follow_redirects and 300 <= exc.code < 400:
                raw_body = exc.read()
                header_text = "\n".join(f"{k}: {v}" for k, v in exc.headers.items())
                return {
                    "status": str(exc.code),
                    "header": header_text,
                    "body": raw_body.decode("utf-8", errors="ignore"),
                }
            raise MiMotionError(f"HTTP请求失败: {exc}") from exc
        except Exception as exc:  # pragma: no cover
            raise MiMotionError(f"HTTP请求失败: {exc}") from exc

    def _get_access(self, username: str, password: str) -> str:
        if "@" not in username:
            username = "+86" + username
        url = "https://api-user.zepp.com/v2/registrations/tokens"
        data = {
            "emailOrPhone": username,
            "password": password,
            "state": "REDIRECTION",
            "client_id": "HuaMi",
            "country_code": "CN",
            "token": "access",
            "redirect_uri": "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html",
        }
        body = self._encrypt_data(urllib.parse.urlencode(data))
        response = self._request(url, data=body, app_token=None, ekv=True, follow_redirects=False)
        header = response["header"]
        body = response["body"]
        status = response["status"]

        detail = self._extract_response_detail(header, body, status)

        access_match = re.search(r"access=([^&;]+)", header)
        if access_match:
            return access_match.group(1)
        refresh_match = re.search(r"refresh=([^&;]+)", header)
        if refresh_match:
            return refresh_match.group(1)
        if "error=" in header or "error" in body.lower():
            raise MiMotionError(f"登录token接口请求失败：{detail}")
        raise MiMotionError(f"登录token接口请求失败：{detail}")

    @staticmethod
    def _extract_response_detail(header: str, body: str, status: Union[str, int]) -> str:
        snippets = []
        if status:
            snippets.append(f"HTTP {status}")
        if header:
            if "Location:" in header:
                loc = re.search(r"(?mi)^location:\s*(.*)$", header)
                if loc:
                    snippets.append(f"Location={loc.group(1).strip()}")
            if "error=" in header:
                err = re.search(r"(?mi)^.*error=([^&\\s]+)", header)
                if err:
                    snippets.append(f"header_error={err.group(1)}")
        if body:
            body_preview = body[:180].replace("\n", " ")
            snippets.append(f"body={body_preview}")
            if "error" in body.lower():
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        code = parsed.get("error")
                        if code:
                            snippets.append(f"json_error={code}")
                        msg = parsed.get("message") or parsed.get("error_description")
                        if msg:
                            snippets.append(f"json_message={msg}")
                except Exception:
                    pass
        return "；".join(snippets) if snippets else "无可用返回信息"

    def _client_login_huami(self, access: str) -> Tuple[str, str, str]:
        url = "https://account.huami.com/v2/client/login"
        headers = {
            "app_name": self.APP_NAME,
            "x-request-id": str(uuid.uuid4()),
            "accept-language": "zh-CN",
            "appname": self.APP_NAME,
            "cv": "50818_6.14.0",
            "v": "2.0",
            "appplatform": "android_phone",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        if "@" not in self.user:
            data = {
                "app_name": self.APP_NAME,
                "app_version": self.APP_VERSION,
                "code": access,
                "country_code": "CN",
                "device_id": self.login_device_id,
                "device_model": "phone",
                "grant_type": "access_token",
                "third_name": "huami_phone",
            }
        else:
            data = {
                "allow_registration=": "false",
                "app_name": self.APP_NAME,
                "app_version": self.APP_VERSION,
                "code": access,
                "country_code": "CN",
                "device_id": self.login_device_id,
                "device_model": "android_phone",
                "dn": "account.zepp.com,api-user.zepp.com,api-mifit.zepp.com,api-watch.zepp.com,app-analytics.zepp.com,api-analytics.huami.com,auth.zepp.com",
                "grant_type": "access_token",
                "lang": "zh_CN",
                "os_version": "1.5.0",
                "source": "com.xiaomi.hm.health:6.14.0:50818",
                "third_name": "email",
            }
        response = self._request(url, data=data, extra_headers=headers)
        try:
            result = json.loads(response["body"])
        except json.JSONDecodeError as exc:
            raise MiMotionError("客户端登录接口请求失败") from exc

        if isinstance(result, dict) and result.get("result") == "ok":
            token_info = result.get("token_info", {})
            return (
                str(token_info.get("login_token", "")),
                str(token_info.get("app_token", "")),
                str(token_info.get("user_id", "")),
            )

        raise MiMotionError(f"客户端登录失败: {response['body']}")

    def _grant_app_token(self, login_token: str) -> str:
        if not login_token:
            return ""
        url = (
            "https://account-cn.huami.com/v1/client/app_tokens?"
            + urllib.parse.urlencode(
                {
                    "app_name": self.APP_NAME,
                    "dn": "api-user.huami.com,api-mifit.huami.com,app-analytics.huami.com",
                    "login_token": login_token,
                }
            )
        )
        response = self._request(
            url,
            extra_headers={"User-Agent": "MiFit/5.3.0 (iPhone; iOS 14.7.1; Scale/3.00)"},
        )
        try:
            result = json.loads(response["body"])
        except json.JSONDecodeError as exc:
            raise MiMotionError("刷新 app_token 接口请求失败") from exc
        if isinstance(result, dict) and result.get("result") == "ok":
            return str(result.get("token_info", {}).get("app_token", ""))
        raise MiMotionError(f"刷新 app_token 失败: {response['body']}")

    def _legacy_client_login_zepp(self, access: str) -> Tuple[str, str]:
        url = "https://account.zepp.com/v2/client/login"
        data = {
            "app_name": self.APP_NAME,
            "app_version": self.APP_VERSION,
            "code": access,
            "country_code": "CN",
            "device_id": self.login_device_id,
            "device_model": "android_phone",
            "grant_type": "access_token",
            "third_name": "email" if "@" in self.user else "huami_phone",
            "dn": "account.zepp.com,api-user.zepp.com,api-mifit.zepp.com,api-watch.zepp.com,app-analytics.zepp.com,api-analytics.huami.com,auth.zepp.com",
            "source": "com.xiaomi.hm.health:6.14.0:50818",
            "lang": "zh",
        }
        response = self._request(url, data=data)
        try:
            result = json.loads(response["body"])
        except json.JSONDecodeError as exc:
            raise MiMotionError("登录接口请求失败") from exc

        if isinstance(result, dict) and result.get("result") == "ok":
            token_info = result.get("token_info", {})
            return str(token_info.get("app_token", "")), str(token_info.get("user_id", ""))

        raise MiMotionError(f"登录失败: {response['body']}")

    def login(self, force_refresh: bool = False) -> Tuple[str, str]:
        if self.invalid:
            return "", ""

        if force_refresh:
            clear_cached_zepp_tokens(self.user)

        cached = get_cached_zepp_tokens(self.user)
        if cached and cached.get("app_token") and cached.get("user_id"):
            return str(cached["app_token"]), str(cached["user_id"])

        if cached and cached.get("login_token") and cached.get("user_id"):
            app_token = self._grant_app_token(str(cached["login_token"]))
            if app_token:
                save_zepp_tokens(
                    account=self.user,
                    login_device_id=self.login_device_id,
                    login_token=str(cached["login_token"]),
                    app_token=app_token,
                    user_id=str(cached["user_id"]),
                )
                return app_token, str(cached["user_id"])

        access = self._get_access(self.user, self.password)
        login_token, app_token, userid = self._client_login_huami(access)
        if not app_token or not userid:
            app_token, userid = self._legacy_client_login_zepp(access)
            login_token = ""
        save_zepp_tokens(
            account=self.user,
            login_device_id=self.login_device_id,
            login_token=login_token,
            app_token=app_token,
            user_id=userid,
        )
        return app_token, userid

    @staticmethod
    def _build_payload_template(step: int) -> str:
        """
        Reuse original template in index.php to keep payload structure closer to upstream project.
        """
        php_file = Path(__file__).with_name("index.php")
        if not php_file.exists():
            raise FileNotFoundError("index.php 不存在，无法读取原始 payload 模板")

        text = php_file.read_text(encoding="utf-8", errors="ignore")
        # Match the PHP heredoc-like assignment for $json
        m = re.search(r"\$json\s*=\s*'(.*?)';\s*\$data\s*=", text, re.S)
        if not m:
            raise ValueError("未解析到 index.php 中的 data_json 模板")

        template = m.group(1)
        today = datetime.now().strftime("%Y-%m-%d")

        # Replace template placeholders used by php file
        template = template.replace("' . date('Y-m-d') . '", today)
        template = re.sub(r"'\s*\.\s*\$step\s*\.\s*'", str(step), template)

        return template

    @staticmethod
    def _build_payload_fallback(step: int) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        payload = [
            {
                "date": today,
                "tz": 32,
                "did": MiMotionRunner.LAST_DEVICE_ID,
                "source": 24,
                "type": 0,
                "data_hr": "UA",
                "data": [
                    {
                        "start": 0,
                        "stop": 1439,
                        "value": "UA",
                    }
                ],
                "summary": json.dumps(
                    {
                        "v": 6,
                        "stp": {"ttl": step},
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        ]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _payload_date(value: Optional[str] = None) -> str:
        if value and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
            return value.strip()
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _template_data_value() -> str:
        try:
            payload = json.loads(MiMotionRunner._build_payload_template(8000))
            return str(payload[0]["data"][0].get("value") or "UA")
        except Exception:
            return "UA"

    @staticmethod
    def _empty_sleep_summary(date_str: str) -> dict:
        try:
            day_start = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())
        except ValueError:
            day_start = int(datetime.now().timestamp())
        return {
            "st": day_start,
            "ed": day_start,
            "dp": 0,
            "lt": 0,
            "wk": 0,
            "usrSt": -1440,
            "usrEd": -1440,
            "wc": 0,
            "is": 0,
            "lb": 0,
            "to": 0,
            "dt": 0,
            "rhr": 0,
            "ss": 0,
        }

    @staticmethod
    def _distribute_int(total: int, weights: list) -> list:
        if not weights:
            return [total]
        raw = [total * weight / sum(weights) for weight in weights]
        values = [int(item) for item in raw]
        remainder = total - sum(values)
        order = sorted(range(len(raw)), key=lambda idx: raw[idx] - values[idx], reverse=True)
        for idx in order[:remainder]:
            values[idx] += 1
        return values

    @staticmethod
    def _build_realistic_stage(step: int, date_str: str) -> Tuple[list, dict]:
        rng = random.Random(f"{date_str}:{step}:{datetime.now().strftime('%H%M%S%f')}")
        stride_m = rng.uniform(0.48, 0.58)
        total_distance = max(1, int(round(step * stride_m)))
        total_cal = max(1, int(round(step * rng.uniform(0.026, 0.041))))

        if step < 8000:
            modes = [1, 3, 1, 3]
            weights = [0.20, 0.35, 0.18, 0.27]
        elif step < 20000:
            modes = [1, 3, 3, 1, 3, 3]
            weights = [0.12, 0.20, 0.18, 0.12, 0.18, 0.20]
        else:
            modes = [1, 3, 3, 4, 3, 1, 3, 4, 3]
            weights = [0.08, 0.14, 0.14, 0.13, 0.13, 0.08, 0.13, 0.10, 0.07]

        weights = [max(0.03, weight * rng.uniform(0.82, 1.18)) for weight in weights]
        stage_steps = MiMotionRunner._distribute_int(step, weights)
        stage_distance = MiMotionRunner._distribute_int(total_distance, stage_steps)
        stage_cal = MiMotionRunner._distribute_int(total_cal, stage_steps)

        start_min = rng.randint(360, 540)
        stages = []
        hr_bytes = bytearray([0xFF] * 1440)
        walk_minutes = 0
        run_minutes = 0
        run_distance = 0
        run_cal = 0

        for idx, seg_step in enumerate(stage_steps):
            mode = modes[idx]
            pace = 78 if mode == 1 else 112 if mode == 3 else 152
            pace = int(round(pace * rng.uniform(0.88, 1.14)))
            duration = max(3, int(round(seg_step / max(pace, 1))))
            duration = max(2, int(round(duration * rng.uniform(0.90, 1.12))))
            if start_min + duration > 1439:
                duration = max(1, 1439 - start_min)
            stop_min = min(1439, start_min + duration)
            stage = {
                "start": int(start_min),
                "stop": int(stop_min),
                "mode": int(mode),
                "dis": int(stage_distance[idx]),
                "cal": int(stage_cal[idx]),
                "step": int(seg_step),
            }
            stages.append(stage)

            base_hr = 82 if mode == 1 else 108 if mode == 3 else 138
            hr_offset = rng.randint(-5, 5)
            for minute in range(max(0, start_min), min(1440, stop_min + 1)):
                hr_bytes[minute] = max(60, min(178, base_hr + hr_offset + ((minute + idx) % 9) - 4))

            if mode == 4:
                run_minutes += max(0, stop_min - start_min + 1)
                run_distance += stage["dis"]
                run_cal += stage["cal"]
            else:
                walk_minutes += max(0, stop_min - start_min + 1)

            gap = rng.randint(8, 42)
            start_min = stop_min + gap
            if start_min >= 1439:
                break

        if stages and sum(item["step"] for item in stages) != step:
            stages[-1]["step"] += step - sum(item["step"] for item in stages)
        if stages and sum(item["dis"] for item in stages) != total_distance:
            stages[-1]["dis"] += total_distance - sum(item["dis"] for item in stages)
        if stages and sum(item["cal"] for item in stages) != total_cal:
            stages[-1]["cal"] += total_cal - sum(item["cal"] for item in stages)

        metrics = {
            "ttl": int(step),
            "dis": int(total_distance),
            "cal": int(total_cal),
            "wk": int(walk_minutes),
            "rn": int(run_minutes),
            "runDist": int(run_distance),
            "runCal": int(run_cal),
            "stage": stages,
            "data_hr": base64.b64encode(bytes(hr_bytes)).decode("ascii"),
        }
        return stages, metrics

    def _build_realistic_payload(self, step: int, payload_date: Optional[str] = None) -> str:
        date_str = MiMotionRunner._payload_date(payload_date)
        stages, metrics = MiMotionRunner._build_realistic_stage(step, date_str)
        summary = {
            "v": 6,
            "slp": MiMotionRunner._empty_sleep_summary(date_str),
            "stp": {
                "ttl": metrics["ttl"],
                "dis": metrics["dis"],
                "cal": metrics["cal"],
                "wk": metrics["wk"],
                "rn": metrics["rn"],
                "runDist": metrics["runDist"],
                "runCal": metrics["runCal"],
                "stage": stages,
            },
            "goal": 8000,
            "tz": "28800",
        }
        payload = [
            {
                "data_hr": metrics["data_hr"],
                "date": date_str,
                "data": [
                    {
                        "start": 0,
                        "stop": 1439,
                        "value": MiMotionRunner._template_data_value(),
                        "did": self.data_did,
                        "tz": 32,
                        "src": 24,
                    }
                ],
                "summary": json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                "source": 24,
                "type": 0,
            }
        ]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def build_payload_preview(step: int, payload_date: Optional[str] = None, account: str = "") -> dict:
        runner = MiMotionRunner(user=account, password="preview")
        data_json = runner._build_realistic_payload(step, payload_date)
        payload = json.loads(data_json)
        record = payload[0]
        summary = json.loads(record["summary"])
        stp = summary["stp"]
        stage = stp.get("stage", [])
        stage_step_sum = sum(item.get("step", 0) for item in stage)
        stage_distance_sum = sum(item.get("dis", 0) for item in stage)
        stage_cal_sum = sum(item.get("cal", 0) for item in stage)
        hr_len = len(base64.b64decode(record.get("data_hr", "") or b""))
        checks = [
            {
                "name": "stage_step_sum",
                "ok": stage_step_sum == step,
                "detail": f"stage.step 总和 {stage_step_sum}，目标 {step}",
            },
            {
                "name": "stage_distance_sum",
                "ok": abs(stage_distance_sum - stp["dis"]) <= max(2, int(stp["dis"] * 0.01)),
                "detail": f"stage.dis 总和 {stage_distance_sum}，summary.dis {stp['dis']}",
            },
            {
                "name": "stage_calorie_sum",
                "ok": abs(stage_cal_sum - stp["cal"]) <= max(2, int(stp["cal"] * 0.02)),
                "detail": f"stage.cal 总和 {stage_cal_sum}，summary.cal {stp['cal']}",
            },
            {
                "name": "data_hr_length",
                "ok": hr_len == 1440,
                "detail": f"data_hr 解码长度 {hr_len}",
            },
            {
                "name": "activity_calorie",
                "ok": stp["cal"] >= max(20, int(step * 0.015)),
                "detail": f"活动卡路里 {stp['cal']}",
            },
        ]
        return {
            "status": "success",
            "mode": "offline_payload_preview",
            "submitted": False,
            "date": record["date"],
            "step": step,
            "summary": {
                "ttl": stp["ttl"],
                "dis": stp["dis"],
                "cal": stp["cal"],
                "wk": stp["wk"],
                "rn": stp["rn"],
                "runDist": stp["runDist"],
                "runCal": stp["runCal"],
                "stage_count": len(stage),
                "data_hr_decoded_length": hr_len,
            },
            "device": {
                "bound_to_account": bool(normalize_device_account(account)),
                "account": MiMotionRunner.desensitize_user_name(account) if account else "",
                "login_device_id": mask_device_identifier(runner.login_device_id),
                "last_deviceid": mask_device_identifier(runner.last_deviceid),
                "data_did": mask_device_identifier(runner.data_did),
                "device_type": runner.device_type,
                "note": "同一账号会复用同一组模拟设备 ID；首次使用时随机生成并写入本地数据库。",
            },
            "checks": checks,
            "notes": [
                "这是离线模拟 payload 预览，不会请求 Zepp，也不会修改步数。",
                "data[0].value 暂时保留原模板，后续可用真实 App 样本替换。",
                "字段一致性提升不等于保证微信运动同步成功。",
            ],
            "data_json": data_json,
        }

    def _build_data_json(self, step: int) -> str:
        return self._build_payload_template(step)

    def login_and_post_step(self, step: int):
        if self.invalid:
            return "账号或密码配置有误", False
        if step > STEP_MAX:
            raise MiMotionError(f"步数超过上限（{STEP_MAX}）")

        token, userid = self.login()
        if not token or not userid:
            return "登录失败！", False

        data_json = self._build_data_json(step)

        def submit_with_token(app_token: str, submit_userid: str) -> dict:
            url = f"https://api-mifit-cn.huami.com/v1/data/band_data.json?&t={int(datetime.now().timestamp() * 1000)}&r={uuid.uuid4()}"
            post_data = {
                "data_json": data_json,
                "userid": submit_userid,
                "device_type": self.device_type,
                "last_sync_data_time": "1597306380",
                "last_deviceid": self.last_deviceid,
            }
            response = self._request(
                url,
                data=post_data,
                extra_headers={
                    "apptoken": app_token,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            return json.loads(response["body"])

        try:
            result = submit_with_token(token, userid)
        except MiMotionError as exc:
            if "401" not in str(exc) and "Unauthorized" not in str(exc):
                raise
            clear_cached_zepp_tokens(self.user)
            token, userid = self.login(force_refresh=True)
            if not token or not userid:
                raise MiMotionError("登录失败：缓存 token 失效，重新登录失败") from exc
            try:
                result = submit_with_token(token, userid)
            except json.JSONDecodeError as json_exc:
                raise MiMotionError("修改步数接口请求失败") from json_exc
        except json.JSONDecodeError as exc:
            raise MiMotionError("修改步数接口请求失败") from exc

        if isinstance(result, dict) and (result.get("code") == 1 or result.get("message") == "success"):
            return f"Zepp 接收成功（{step}）", True

        message = result.get("message") if isinstance(result, dict) else response["body"]
        raise MiMotionError(f"修改步数失败：{message}")

    def run(self, step: int):
        msg, success = self.login_and_post_step(step)
        return {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": self.desensitize_user_name(self.user),
            "step": step,
            "status": "success" if success else "failed",
            "message": msg,
        }


def init_tool_log_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_call_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                tool_id TEXT NOT NULL,
                account TEXT NOT NULL,
                account_masked TEXT NOT NULL,
                step INTEGER NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                remote_addr TEXT,
                debug INTEGER NOT NULL DEFAULT 0,
                response_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_call_records_created_at ON tool_call_records(created_at DESC)"
        )


def record_tool_call(
    *,
    tool_id: str,
    account: str,
    step: int,
    status: str,
    message: str,
    remote_addr: str,
    debug: bool,
    response: dict,
) -> None:
    init_tool_log_db()
    account_masked = MiMotionRunner.desensitize_user_name(account)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO tool_call_records (
                created_at, tool_id, account, account_masked, step, status,
                message, remote_addr, debug, response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                tool_id,
                account,
                account_masked,
                step,
                status,
                message,
                remote_addr,
                1 if debug else 0,
                json.dumps(response, ensure_ascii=False),
            ),
        )


def list_tool_logs(limit: int = 30) -> list:
    init_tool_log_db()
    safe_limit = max(1, min(int(limit or 30), 100))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, created_at, tool_id, account_masked, step, status, message, remote_addr, response_json
            FROM tool_call_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    records = []
    for row in rows:
        item = dict(row)
        response_payload = {}
        try:
            response_payload = json.loads(item.pop("response_json", "") or "{}")
        except json.JSONDecodeError:
            response_payload = {}
        tool_id = item.get("tool_id", "")
        item["tool_label"] = {
            "zepp-step": "自有设备步数修改",
            "shared-device-step": "共享账号扫码同步",
        }.get(tool_id, tool_id)
        requested_step = response_payload.get("requested_step")
        submitted_step = response_payload.get("submitted_step")
        item["requested_step"] = requested_step
        item["submitted_step"] = submitted_step
        if tool_id == "shared-device-step" and requested_step and submitted_step and requested_step != submitted_step:
            item["step_display"] = f"{requested_step} -> {submitted_step}"
        else:
            item["step_display"] = str(item.get("step", ""))
        if response_payload.get("sync_tip"):
            item["message"] = response_payload["sync_tip"]
        if item.get("status") != "success":
            item.update(classify_error(item.get("message", "")))
        records.append(item)
    return records


def init_device_share_step_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_share_step_state (
                share_id INTEGER PRIMARY KEY,
                current_step INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                source TEXT NOT NULL
            )
            """
        )


def _device_share_state_id(share: Optional[dict]) -> int:
    try:
        return int((share or {}).get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _safe_shared_step(value: object, fallback: int = SHARED_DEVICE_STEP_DEFAULT) -> int:
    try:
        step = int(value)
    except (TypeError, ValueError):
        step = fallback
    return max(1, min(STEP_MAX, step))


def _infer_shared_device_step_from_logs(share_id: int) -> Tuple[int, str]:
    init_tool_log_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT step, response_json
            FROM tool_call_records
            WHERE tool_id = 'shared-device-step' AND status = 'success'
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

    for row in rows:
        payload = {}
        try:
            payload = json.loads(row["response_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload_share_id = _device_share_state_id({"id": payload.get("device_share_id")})
        if share_id and payload_share_id and payload_share_id != share_id:
            continue
        submitted_step = payload.get("submitted_step") or payload.get("step") or row["step"]
        return _safe_shared_step(submitted_step), "log"
    return SHARED_DEVICE_STEP_DEFAULT, "default"


def get_shared_device_step_state(share: Optional[dict]) -> dict:
    share_id = _device_share_state_id(share)
    init_device_share_step_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT current_step, updated_at, source
            FROM device_share_step_state
            WHERE share_id = ?
            """,
            (share_id,),
        ).fetchone()
    if row:
        current_step = _safe_shared_step(row["current_step"])
        source = row["source"] or "state"
        updated_at = row["updated_at"]
    else:
        current_step, source = _infer_shared_device_step_from_logs(share_id)
        updated_at = None

    next_submit_step = current_step + 1 if current_step < STEP_MAX else None
    return {
        "share_id": share_id,
        "current_step": current_step,
        "next_submit_step": next_submit_step,
        "can_sync": next_submit_step is not None,
        "max_step": STEP_MAX,
        "source": source,
        "updated_at": updated_at,
    }


def update_shared_device_step_state(share: Optional[dict], current_step: int, source: str = "submit") -> dict:
    share_id = _device_share_state_id(share)
    safe_step = _safe_shared_step(current_step)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    init_device_share_step_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO device_share_step_state (share_id, current_step, updated_at, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(share_id) DO UPDATE SET
                current_step = excluded.current_step,
                updated_at = excluded.updated_at,
                source = excluded.source
            """,
            (share_id, safe_step, now, clean_message_text(source, 32) or "submit"),
        )
    return get_shared_device_step_state(share)


def init_device_share_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_share_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                account TEXT NOT NULL,
                account_masked TEXT NOT NULL,
                password TEXT NOT NULL,
                qr_path TEXT NOT NULL,
                qr_sha256 TEXT NOT NULL,
                qr_content_type TEXT NOT NULL,
                contributor_contact TEXT,
                contributor_contact_masked TEXT,
                note TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                remote_addr TEXT,
                user_agent TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_device_share_status_id ON device_share_records(status, id DESC)"
        )


def record_device_share(
    *,
    account: str,
    password: str,
    qr_path: Path,
    qr_sha256: str,
    qr_content_type: str,
    contributor_contact: str,
    note: str,
    remote_addr: str,
    user_agent: str,
) -> dict:
    init_device_share_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    account_clean = normalize_login_account(account)
    contact_clean = clean_message_text(contributor_contact or "", 120)
    note_clean = clean_message_text(note or "", 240)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO device_share_records (
                created_at, account, account_masked, password, qr_path, qr_sha256,
                qr_content_type, contributor_contact, contributor_contact_masked,
                note, status, remote_addr, user_agent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                now,
                account_clean,
                MiMotionRunner.desensitize_user_name(account_clean),
                password,
                str(qr_path),
                qr_sha256,
                qr_content_type,
                contact_clean,
                mask_contact(contact_clean),
                note_clean,
                remote_addr,
                (user_agent or "")[:300],
            ),
        )
        share_id = cursor.lastrowid
    return {
        "id": share_id,
        "created_at": now,
        "account_masked": MiMotionRunner.desensitize_user_name(account_clean),
        "qr_sha256": qr_sha256,
        "qr_content_type": qr_content_type,
        "contributor_contact_masked": mask_contact(contact_clean),
        "note": note_clean,
        "status": "active",
    }


def public_device_share(record: Optional[dict]) -> Optional[dict]:
    if not record:
        return None
    return {
        "id": record.get("id"),
        "created_at": record.get("created_at"),
        "account_masked": record.get("account_masked"),
        "qr_sha256": record.get("qr_sha256"),
        "qr_content_type": record.get("qr_content_type"),
        "contributor_contact_masked": record.get("contributor_contact_masked"),
        "note": record.get("note"),
        "status": record.get("status"),
    }


def get_latest_device_share() -> Optional[dict]:
    init_device_share_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, created_at, account, account_masked, password, qr_path, qr_sha256,
                   qr_content_type, contributor_contact_masked, note, status
            FROM device_share_records
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()
    for row in rows:
        item = dict(row)
        qr_path = Path(str(item.get("qr_path") or ""))
        if qr_path.exists() and qr_path.is_file():
            return item
    return None


def list_device_shares(limit: int = 10) -> list:
    init_device_share_db()
    safe_limit = max(1, min(int(limit or 10), 50))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, created_at, account_masked, qr_sha256, qr_content_type,
                   contributor_contact_masked, note, status
            FROM device_share_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [public_device_share(dict(row)) for row in rows]


def count_device_shares() -> int:
    init_device_share_db()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT qr_path FROM device_share_records WHERE status = 'active'").fetchall()
    return sum(1 for row in rows if Path(str(row[0] or "")).exists())


def image_extension_for_content_type(content_type: str) -> str:
    normalized = (content_type or "").lower()
    if normalized == "image/png":
        return ".png"
    if normalized == "image/webp":
        return ".webp"
    return ".jpg"


def mask_contact(value: str) -> str:
    contact = (value or "").strip()
    if not contact:
        return "未留联系方式"
    return MiMotionRunner.desensitize_user_name(contact)


def clean_message_text(value: str, max_len: int = 500) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", "", value or "")
    return text.strip()[:max_len]


def _strip_baidu_share_url_token(value: str) -> str:
    return (value or "").strip().strip(" \t\r\n\"'<>，。；;、,.)）]】")


def normalize_baidu_share_url(value: str) -> str:
    raw = _strip_baidu_share_url_token(value)
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    elif not re.match(r"^https?://", raw, flags=re.IGNORECASE):
        raw = f"https://{raw}"

    parsed = urllib.parse.urlparse(raw)
    if parsed.netloc.lower() != "pan.baidu.com":
        return ""
    if not (parsed.path.startswith("/s/") or parsed.path.startswith("/share/init")):
        return ""

    query = urllib.parse.parse_qs(parsed.query)
    normalized_query = urllib.parse.urlencode({k: v[-1] for k, v in query.items() if v})
    normalized = parsed._replace(
        scheme="https",
        netloc="pan.baidu.com",
        path=parsed.path.rstrip("/") or parsed.path,
        params="",
        query=normalized_query,
        fragment="",
    )
    return urllib.parse.urlunparse(normalized)


def extract_baidu_share_payload(raw_text: str) -> dict:
    text = clean_message_text(raw_text, BAIDU_SHARE_RAW_TEXT_MAX_LENGTH)
    if not text:
        return {
            "ok": False,
            "error": "请粘贴百度网盘分享文本",
            "need_resubmit": True,
        }

    share_urls = []
    for match in BAIDU_SHARE_URL_RE.finditer(text):
        share_url_candidate = normalize_baidu_share_url(match.group(1))
        if share_url_candidate and share_url_candidate not in share_urls:
            share_urls.append(share_url_candidate)

    if not share_urls:
        return {
            "ok": False,
            "error": "没有解析到有效的百度网盘分享链接，请重新提交。",
            "need_resubmit": True,
        }

    if len(share_urls) > 1:
        return {
            "ok": False,
            "error": "当前只支持一个百度网盘分享链接，请删除多余链接后重新提交。",
            "need_resubmit": True,
            "share_url_count": len(share_urls),
        }

    share_url = share_urls[0]
    parsed = urllib.parse.urlparse(share_url)
    query = urllib.parse.parse_qs(parsed.query)
    extraction_code = (query.get("pwd", [""])[-1] or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{4}", extraction_code):
        code_match = BAIDU_SHARE_CODE_RE.search(text)
        extraction_code = code_match.group(1).strip() if code_match else ""

    if not re.fullmatch(r"[A-Za-z0-9]{4}", extraction_code):
        return {
            "ok": False,
            "error": "已经解析到分享链接，但没有解析到 4 位提取码，请重新提交。",
            "need_resubmit": True,
            "share_url": share_url,
        }

    return {
        "ok": True,
        "share_url": share_url,
        "extraction_code": extraction_code,
        "raw_text": text,
    }


def normalize_netdisk_root(value: str) -> str:
    raw = clean_message_text(value or BAIDU_NETDISK_DEFAULT_SAVE_ROOT, 180).replace("\\", "/")
    raw = re.sub(r"/+", "/", raw).strip()
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return posixpath.normpath(raw) if raw != "/" else "/"


def normalize_server_download_root(value: str) -> str:
    raw = clean_message_text(value or BAIDU_SERVER_DOWNLOAD_DEFAULT_ROOT, 240)
    if not raw:
        raw = BAIDU_SERVER_DOWNLOAD_DEFAULT_ROOT
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(__file__).parent.joinpath(path)
    return str(path.resolve(strict=False))


def baidu_share_job_paths(job_id: str, netdisk_root: str, server_root: str) -> Tuple[str, str]:
    safe_job_id = re.sub(r"[^A-Za-z0-9_-]+", "", job_id) or secrets.token_hex(8)
    netdisk_path = posixpath.normpath(posixpath.join(normalize_netdisk_root(netdisk_root), safe_job_id))
    server_path = str(Path(normalize_server_download_root(server_root)).joinpath(safe_job_id).resolve(strict=False))
    return netdisk_path, server_path


def mask_baidu_share_url(value: str) -> str:
    url = value or ""
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc:
        return ""
    basename = posixpath.basename(parsed.path)
    if len(basename) > 8:
        masked_path = posixpath.join(posixpath.dirname(parsed.path), f"{basename[:5]}***{basename[-3:]}")
    else:
        masked_path = parsed.path
    masked = parsed._replace(path=masked_path, query="", fragment="")
    return urllib.parse.urlunparse(masked)


def mask_baidu_extract_code(value: str) -> str:
    text = value or ""
    if len(text) != 4:
        return "****"
    return f"{text[0]}**{text[-1]}"


def init_baidu_share_job_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS baidu_share_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                share_url TEXT NOT NULL,
                share_url_masked TEXT NOT NULL,
                extraction_code TEXT NOT NULL,
                extraction_code_masked TEXT NOT NULL,
                netdisk_save_path TEXT NOT NULL,
                server_save_path TEXT NOT NULL,
                remote_addr TEXT,
                user_agent TEXT,
                response_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_baidu_share_jobs_created_at ON baidu_share_jobs(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_baidu_share_jobs_status ON baidu_share_jobs(status)"
        )


def baidu_share_status_meta(status: str) -> dict:
    raw_status = clean_message_text(status or "queued", 64) or "queued"
    meta = BAIDU_SHARE_STATUS_META.get(raw_status)
    if not meta:
        return {
            "status": raw_status,
            "status_label": raw_status,
            "status_detail": "任务状态由后台执行器更新。",
            "status_level": "info",
        }
    return {
        "status": raw_status,
        "status_label": meta["label"],
        "status_detail": meta["detail"],
        "status_level": meta["level"],
    }


def public_baidu_share_job(row: dict) -> dict:
    status_meta = baidu_share_status_meta(str(row.get("status") or "queued"))
    worker_payload = parse_baidu_job_response_json(row)
    worker_message = clean_message_text(str(worker_payload.get("message") or ""), 300)
    file_stats = baidu_share_job_file_stats(str(row.get("server_save_path") or ""))
    archive_ready = status_meta["status"] in ("completed", "partial_completed") and int(file_stats["files"]) > 0
    return {
        "id": row.get("id"),
        "job_id": row.get("job_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        **status_meta,
        "share_url_masked": row.get("share_url_masked"),
        "extraction_code_masked": row.get("extraction_code_masked"),
        "netdisk_save_path": row.get("netdisk_save_path"),
        "server_save_path": row.get("server_save_path"),
        "archive_ready": archive_ready,
        "archive_name": f"{row.get('job_id')}.zip" if archive_ready else "",
        "server_files_count": file_stats["files"],
        "server_files_bytes": file_stats["bytes"],
        "server_files_size": human_file_size(int(file_stats["bytes"])),
        "expires_at": baidu_share_job_expires_at(row),
        "retention_minutes": BAIDU_SHARE_FILE_RETENTION_MINUTES,
        "retention_hours": round(BAIDU_SHARE_FILE_RETENTION_MINUTES / 60, 2),
        "retention_label": baidu_share_retention_label(),
        "remote_addr": row.get("remote_addr") or "",
        "worker_message": worker_message,
    }


def record_baidu_share_job(
    *,
    raw_text: str,
    share_url: str,
    extraction_code: str,
    netdisk_root: str,
    server_root: str,
    remote_addr: str,
    user_agent: str,
) -> dict:
    init_baidu_share_job_db()
    job_id = f"bdpan_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    netdisk_save_path, server_save_path = baidu_share_job_paths(job_id, netdisk_root, server_root)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "stage": "recorded",
        "message": "任务已记录，等待 BaiduPCS-Go worker 执行转存和下载。",
        "download_token": secrets.token_urlsafe(24),
    }
    row = {
        "job_id": job_id,
        "created_at": created_at,
        "updated_at": created_at,
        "status": "queued",
        "raw_text": clean_message_text(raw_text, BAIDU_SHARE_RAW_TEXT_MAX_LENGTH),
        "share_url": share_url,
        "share_url_masked": mask_baidu_share_url(share_url),
        "extraction_code": extraction_code,
        "extraction_code_masked": mask_baidu_extract_code(extraction_code),
        "netdisk_save_path": netdisk_save_path,
        "server_save_path": server_save_path,
        "remote_addr": remote_addr,
        "user_agent": clean_message_text(user_agent, 300),
        "response_json": json.dumps(payload, ensure_ascii=False),
    }
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO baidu_share_jobs (
                job_id, created_at, updated_at, status, raw_text, share_url,
                share_url_masked, extraction_code, extraction_code_masked,
                netdisk_save_path, server_save_path, remote_addr, user_agent,
                response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["job_id"],
                row["created_at"],
                row["updated_at"],
                row["status"],
                row["raw_text"],
                row["share_url"],
                row["share_url_masked"],
                row["extraction_code"],
                row["extraction_code_masked"],
                row["netdisk_save_path"],
                row["server_save_path"],
                row["remote_addr"],
                row["user_agent"],
                row["response_json"],
            ),
        )
        row["id"] = cursor.lastrowid
    return row


def parse_baidu_job_response_json(row: dict) -> dict:
    try:
        return json.loads(str(row.get("response_json") or "{}"))
    except json.JSONDecodeError:
        return {}


def get_baidu_share_job(job_id: str) -> Optional[dict]:
    safe_job_id = clean_message_text(job_id, 120)
    if not safe_job_id:
        return None
    init_baidu_share_job_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, job_id, created_at, updated_at, status, raw_text, share_url,
                   share_url_masked, extraction_code, extraction_code_masked,
                   netdisk_save_path, server_save_path, remote_addr, user_agent,
                   response_json
            FROM baidu_share_jobs
            WHERE job_id = ?
            """,
            (safe_job_id,),
        ).fetchone()
    return dict(row) if row else None


def baidu_share_job_download_token(row: dict) -> str:
    payload = parse_baidu_job_response_json(row)
    return str(payload.get("download_token") or "")


def baidu_share_job_has_files(server_save_path: str) -> bool:
    root = Path(server_save_path or "").expanduser()
    if not root.exists() or not root.is_dir():
        return False
    for item in root.rglob("*"):
        if item.is_symlink():
            continue
        if item.is_file():
            return True
    return False


def baidu_share_job_file_entries(server_save_path: str) -> list:
    root = Path(server_save_path or "").expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        return []
    entries = []
    for item in sorted(root.rglob("*")):
        if item.is_symlink() or not item.is_file():
            continue
        resolved = item.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        entries.append((resolved, resolved.relative_to(root).as_posix()))
    return entries


def create_baidu_share_zip(job: dict) -> Tuple[Path, int]:
    entries = baidu_share_job_file_entries(str(job.get("server_save_path") or ""))
    if not entries:
        raise FileNotFoundError("下载文件尚未生成")
    tmp = NamedTemporaryFile(prefix=f"{job.get('job_id', 'baidu-share')}-", suffix=".zip", delete=False)
    zip_path = Path(tmp.name)
    tmp.close()
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for source, archive_name in entries:
                zf.write(source, archive_name)
        return zip_path, zip_path.stat().st_size
    except Exception:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def parse_local_datetime(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def format_local_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def baidu_share_retention_label() -> str:
    minutes = BAIDU_SHARE_FILE_RETENTION_MINUTES
    if minutes % (24 * 60) == 0:
        return f"{minutes // (24 * 60)} 天"
    if minutes % 60 == 0:
        return f"{minutes // 60} 小时"
    if minutes < 60:
        return f"{minutes} 分钟"
    return f"{minutes // 60} 小时 {minutes % 60} 分钟"


def baidu_share_job_expires_at(row: dict) -> str:
    anchor = parse_local_datetime(str(row.get("updated_at") or "")) or parse_local_datetime(str(row.get("created_at") or "")) or datetime.now()
    return format_local_datetime(anchor + timedelta(minutes=BAIDU_SHARE_FILE_RETENTION_MINUTES))


def baidu_share_job_file_stats(server_save_path: str) -> dict:
    root = Path(server_save_path or "").expanduser()
    stats = {
        "exists": root.exists() and root.is_dir(),
        "files": 0,
        "bytes": 0,
        "latest_mtime": 0.0,
    }
    if not stats["exists"]:
        return stats
    for item in root.rglob("*"):
        if item.is_symlink() or not item.is_file():
            continue
        try:
            item_stat = item.stat()
        except OSError:
            continue
        stats["files"] += 1
        stats["bytes"] += item_stat.st_size
        stats["latest_mtime"] = max(stats["latest_mtime"], item_stat.st_mtime)
    return stats


def baidu_share_safe_delete_path(row: dict) -> Optional[Path]:
    job_id = clean_message_text(str(row.get("job_id") or ""), 120)
    if not job_id.startswith("bdpan_"):
        return None
    path = Path(str(row.get("server_save_path") or "")).expanduser().resolve(strict=False)
    if path.name != job_id:
        return None
    return path


def baidu_share_storage_rows(limit: Optional[int] = None) -> list:
    init_baidu_share_job_db()
    query_limit = ""
    params: Tuple[Union[int, str], ...] = ()
    if limit:
        query_limit = " LIMIT ?"
        params = (max(1, min(int(limit), 500)),)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT id, job_id, created_at, updated_at, status, share_url_masked,
                   extraction_code_masked, netdisk_save_path, server_save_path,
                   remote_addr, response_json
            FROM baidu_share_jobs
            ORDER BY id ASC
            {query_limit}
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def terminal_baidu_share_status(status: str) -> bool:
    return str(status or "") in {"completed", "partial_completed", "failed", "transfer_failed", "canceled", "expired"}


def baidu_share_download_root_usage() -> dict:
    root = Path(normalize_server_download_root("")).expanduser()
    usage_root = root if root.exists() else root.parent
    try:
        disk_usage = shutil.disk_usage(usage_root)
    except OSError:
        disk_usage = shutil.disk_usage(Path.cwd())
    return {
        "download_root": str(root),
        "disk_total_bytes": disk_usage.total,
        "disk_used_bytes": disk_usage.used,
        "disk_free_bytes": disk_usage.free,
        "disk_total_display": human_file_size(disk_usage.total),
        "disk_used_display": human_file_size(disk_usage.used),
        "disk_free_display": human_file_size(disk_usage.free),
    }


def baidu_share_storage_summary() -> dict:
    rows = baidu_share_storage_rows()
    now = datetime.now()
    total_bytes = 0
    total_files = 0
    active_jobs = 0
    expired_candidates = 0
    job_items = []
    for row in rows:
        stats = baidu_share_job_file_stats(str(row.get("server_save_path") or ""))
        total_bytes += int(stats["bytes"])
        total_files += int(stats["files"])
        status = str(row.get("status") or "")
        if not terminal_baidu_share_status(status):
            active_jobs += 1
        expires_at = baidu_share_job_expires_at(row)
        expires_dt = parse_local_datetime(expires_at)
        expired_by_time = bool(expires_dt and expires_dt <= now and stats["bytes"] > 0 and terminal_baidu_share_status(status))
        if expired_by_time:
            expired_candidates += 1
        job_items.append(
            {
                "job_id": row.get("job_id"),
                "status": status,
                "updated_at": row.get("updated_at"),
                "expires_at": expires_at,
                "files": stats["files"],
                "bytes": stats["bytes"],
                "size_display": human_file_size(int(stats["bytes"])),
                "expired_by_time": expired_by_time,
            }
        )
    usage = baidu_share_download_root_usage()
    return {
        "retention_minutes": BAIDU_SHARE_FILE_RETENTION_MINUTES,
        "retention_hours": round(BAIDU_SHARE_FILE_RETENTION_MINUTES / 60, 2),
        "retention_label": baidu_share_retention_label(),
        "storage_max_bytes": BAIDU_SHARE_STORAGE_MAX_BYTES,
        "storage_max_display": human_file_size(BAIDU_SHARE_STORAGE_MAX_BYTES),
        "cleanup_interval_seconds": BAIDU_SHARE_CLEANUP_INTERVAL_SECONDS,
        "job_count": len(rows),
        "active_jobs": active_jobs,
        "stored_files": total_files,
        "stored_bytes": total_bytes,
        "stored_size_display": human_file_size(total_bytes),
        "expired_candidates": expired_candidates,
        "over_storage_limit": total_bytes > BAIDU_SHARE_STORAGE_MAX_BYTES,
        "jobs": sorted(job_items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[:30],
        **usage,
    }


def mark_baidu_share_job_expired(row: dict, reason: str, stats: dict) -> None:
    message = f"服务器文件已清理：{reason}，释放 {human_file_size(int(stats.get('bytes') or 0))}。"
    update_baidu_share_job_status(str(row.get("job_id") or ""), "expired", message, "storage_cleanup")


def cleanup_baidu_share_storage(reason: str = "scheduled") -> dict:
    rows = baidu_share_storage_rows()
    now = datetime.now()
    candidates = []
    total_bytes = 0
    for row in rows:
        stats = baidu_share_job_file_stats(str(row.get("server_save_path") or ""))
        total_bytes += int(stats["bytes"])
        status = str(row.get("status") or "")
        if not terminal_baidu_share_status(status) or int(stats["bytes"]) <= 0:
            continue
        expires_at = parse_local_datetime(baidu_share_job_expires_at(row))
        updated_at = parse_local_datetime(str(row.get("updated_at") or "")) or parse_local_datetime(str(row.get("created_at") or "")) or now
        cleanup_reason = ""
        if expires_at and expires_at <= now:
            cleanup_reason = f"超过保留时间 {baidu_share_retention_label()}"
        candidates.append(
            {
                "row": row,
                "stats": stats,
                "updated_at": updated_at,
                "cleanup_reason": cleanup_reason,
            }
        )

    selected = [item for item in candidates if item["cleanup_reason"]]
    selected_ids = {str(item["row"].get("job_id") or "") for item in selected}
    remaining_bytes = total_bytes - sum(int(item["stats"]["bytes"]) for item in selected)
    if remaining_bytes > BAIDU_SHARE_STORAGE_MAX_BYTES:
        for item in sorted(candidates, key=lambda candidate: candidate["updated_at"]):
            job_id = str(item["row"].get("job_id") or "")
            if job_id in selected_ids:
                continue
            item["cleanup_reason"] = f"超过存储上限 {human_file_size(BAIDU_SHARE_STORAGE_MAX_BYTES)}"
            selected.append(item)
            selected_ids.add(job_id)
            remaining_bytes -= int(item["stats"]["bytes"])
            if remaining_bytes <= int(BAIDU_SHARE_STORAGE_MAX_BYTES * 0.9):
                break

    deleted = []
    errors = []
    for item in selected:
        row = item["row"]
        stats = item["stats"]
        delete_path = baidu_share_safe_delete_path(row)
        if not delete_path or not delete_path.exists():
            continue
        try:
            shutil.rmtree(delete_path)
            mark_baidu_share_job_expired(row, item["cleanup_reason"] or reason, stats)
            deleted.append(
                {
                    "job_id": row.get("job_id"),
                    "reason": item["cleanup_reason"] or reason,
                    "deleted_files": stats["files"],
                    "deleted_bytes": stats["bytes"],
                    "deleted_size_display": human_file_size(int(stats["bytes"])),
                }
            )
        except OSError as exc:
            errors.append({"job_id": row.get("job_id"), "error": clean_message_text(str(exc), 300)})
    return {
        "deleted": deleted,
        "deleted_count": len(deleted),
        "freed_bytes": sum(int(item.get("deleted_bytes") or 0) for item in deleted),
        "freed_size_display": human_file_size(sum(int(item.get("deleted_bytes") or 0) for item in deleted)),
        "errors": errors,
        "summary": baidu_share_storage_summary(),
    }


def update_baidu_share_job_status(job_id: str, status: str, message: str, stage: Optional[str] = None) -> None:
    safe_job_id = clean_message_text(job_id, 120)
    safe_status = clean_message_text(status, 64)
    safe_message = clean_message_text(message, 800)
    if not safe_job_id or not safe_status:
        return
    init_baidu_share_job_db()
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT response_json FROM baidu_share_jobs WHERE job_id = ?",
            (safe_job_id,),
        ).fetchone()
        if not row:
            return
        try:
            payload = json.loads(str(row["response_json"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        logs = payload.get("logs")
        if not isinstance(logs, list):
            logs = []
        log_item = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": safe_status,
            "message": safe_message,
        }
        if not logs or logs[-1].get("status") != safe_status or logs[-1].get("message") != safe_message:
            logs.append(log_item)
        payload["logs"] = logs[-50:]
        payload["stage"] = clean_message_text(stage or safe_status, 80)
        payload["message"] = safe_message
        payload["updated_by"] = "baidu_share_worker"
        conn.execute(
            """
            UPDATE baidu_share_jobs
            SET status = ?, updated_at = ?, response_json = ?
            WHERE job_id = ?
            """,
            (
                safe_status,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                json.dumps(payload, ensure_ascii=False),
                safe_job_id,
            ),
        )


def baidu_share_pending_job() -> Optional[dict]:
    init_baidu_share_job_db()
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, job_id, created_at, updated_at, status, raw_text, share_url,
                   share_url_masked, extraction_code, extraction_code_masked,
                   netdisk_save_path, server_save_path, remote_addr, user_agent,
                   response_json
            FROM baidu_share_jobs
            WHERE status IN ('queued', 'login_required', 'worker_unavailable')
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def baidupcs_binary_path() -> str:
    candidates = []
    if BAIDUPCS_GO_BIN:
        candidates.append(Path(BAIDUPCS_GO_BIN).expanduser())
    candidates.append(Path(__file__).with_name("bin").joinpath("BaiduPCS-Go"))
    candidates.append(Path.home().joinpath(".local", "bin", "BaiduPCS-Go"))
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which("BaiduPCS-Go")
    return found or ""


def baidupcs_env() -> dict:
    env = os.environ.copy()
    env["BAIDUPCS_GO_CONFIG_DIR"] = BAIDUPCS_GO_CONFIG_DIR
    Path(BAIDUPCS_GO_CONFIG_DIR).expanduser().mkdir(parents=True, exist_ok=True)
    return env


def baidupcs_login_ready() -> Tuple[bool, str]:
    config_path = Path(BAIDUPCS_GO_CONFIG_DIR).expanduser().joinpath("pcs_config.json")
    if not config_path.exists():
        return False, f"未找到 BaiduPCS-Go 配置：{config_path}"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"BaiduPCS-Go 配置读取失败：{exc}"
    active_uid = str(payload.get("baidu_active_uid") or "0")
    if active_uid == "0":
        return False, "BaiduPCS-Go 当前没有活动百度账号"
    users = payload.get("baidu_user_list") or []
    if not isinstance(users, list):
        return False, "BaiduPCS-Go 账号列表为空"
    for user in users:
        if str(user.get("uid") or "") != active_uid:
            continue
        bduss = str(user.get("bduss") or "")
        stoken = str(user.get("stoken") or "")
        if bduss and stoken:
            name = clean_message_text(str(user.get("name") or active_uid), 80)
            return True, f"当前百度网盘账号已登录：{name}"
        return False, "当前账号缺少结构化 BDUSS 或 STOKEN，请重新扫码或从百度网盘网页登录 Cookie 导入"
    return False, "BaiduPCS-Go 活动账号不在账号列表中"


def redact_baidu_worker_text(text: str, job: dict) -> str:
    value = str(text or "")
    for secret in (
        str(job.get("share_url") or ""),
        str(job.get("extraction_code") or ""),
    ):
        if secret:
            value = value.replace(secret, "***")
    value = re.sub(r"BDUSS=[^;\s]+", "BDUSS=***", value, flags=re.IGNORECASE)
    value = re.sub(r"STOKEN=[^;\s]+", "STOKEN=***", value, flags=re.IGNORECASE)
    value = re.sub(r"PANPSC=[^;\s]+", "PANPSC=***", value, flags=re.IGNORECASE)
    value = re.sub(r"\r+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return clean_message_text(value, 1200)


def summarize_baidupcs_output(result: subprocess.CompletedProcess, job: dict) -> str:
    output = "\n".join(part for part in [result.stdout, result.stderr] if part)
    output = redact_baidu_worker_text(output, job)
    if not output:
        output = "命令无输出"
    return output


def baidupcs_output_indicates_failure(output: str) -> bool:
    text = str(output or "")
    lowered = text.lower()
    if any(word in lowered for word in ("error", "panic", "failed")):
        return True
    if any(phrase in text for phrase in ("命令执行失败", "转存失败", "下载失败", "下载文件失败", "以下文件下载失败", "错误", "异常")):
        return True
    if "失败" not in text:
        return False
    zero_failure = re.search(r"(失败(?:数)?[：:\s]*0\b|0\s*(?:个)?失败)", text)
    return not bool(zero_failure)


def baidupcs_transfer_succeeded(output: str) -> bool:
    text = str(output or "")
    if any(phrase in text for phrase in ("文件重复", "已存在", "重复保存")):
        return True
    if baidupcs_output_indicates_failure(text):
        return False
    return any(phrase in text for phrase in ("成功", "转存完成", "保存成功", "已保存"))


def run_baidupcs_command(args: list, job: dict) -> subprocess.CompletedProcess:
    binary = baidupcs_binary_path()
    if not binary:
        raise FileNotFoundError("未找到 BaiduPCS-Go，请设置 BAIDUPCS_GO_BIN 或安装到 ~/.local/bin/BaiduPCS-Go")
    return subprocess.run(
        [binary, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=BAIDU_SHARE_COMMAND_TIMEOUT_SECONDS,
        check=False,
        env=baidupcs_env(),
    )


def redact_baidupcs_login_output(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"BDUSS=[^;\s]+", "BDUSS=***", value, flags=re.IGNORECASE)
    value = re.sub(r"STOKEN=[^;\s]+", "STOKEN=***", value, flags=re.IGNORECASE)
    value = re.sub(r"PTOKEN=[^;\s]+", "PTOKEN=***", value, flags=re.IGNORECASE)
    value = re.sub(r"PANPSC=[^;\s]+", "PANPSC=***", value, flags=re.IGNORECASE)
    value = re.sub(r"\r+", "\n", value)
    return clean_message_text(value, 1200)


def parse_cookie_header_values(cookies: str) -> dict:
    values = {}
    for part in str(cookies or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        clean_key = key.strip().upper()
        if clean_key in {"BDUSS", "STOKEN", "PTOKEN", "BAIDUID", "PANPSC"}:
            values[clean_key] = value.strip()
    return values


def parse_loose_json_payload(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        return {}
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip()
    if not raw.startswith("{"):
        match = re.search(r"\((\{.*\})\)\s*;?\s*$", raw, re.DOTALL)
        if match:
            raw = match.group(1)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def baidu_qr_request(url: str, timeout: int = 15) -> Tuple[dict, str]:
    req = urllib.request.Request(url, headers={"User-Agent": BAIDU_QR_LOGIN_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", "replace")
    return parse_loose_json_payload(text), text


def start_baidu_qr_login() -> dict:
    params = urllib.parse.urlencode({"lp": "pc", "tpl": BAIDU_QR_LOGIN_TPL})
    try:
        payload, _ = baidu_qr_request(f"https://passport.baidu.com/v2/api/getqrcode?{params}", timeout=15)
    except Exception as exc:
        return {"ok": False, "error": f"生成百度扫码二维码失败：{exc}"}
    if str(payload.get("errno")) != "0":
        return {"ok": False, "error": clean_message_text(str(payload.get("errmsg") or payload), 300)}
    sign = clean_message_text(str(payload.get("sign") or ""), 120)
    img_url = str(payload.get("imgurl") or "")
    if not BAIDU_QR_SIGN_RE.fullmatch(sign) or not img_url:
        return {"ok": False, "error": "百度扫码接口没有返回有效二维码。"}
    if img_url.startswith("//"):
        img_url = f"https:{img_url}"
    elif not img_url.startswith(("http://", "https://")):
        img_url = f"https://{img_url.lstrip('/')}"
    return {
        "ok": True,
        "sign": sign,
        "image_url": img_url,
        "proxy_image_url": f"/api/admin/baidu-share/qr/image?sign={urllib.parse.quote(sign)}",
        "prompt": clean_message_text(str(payload.get("prompt") or ""), 300),
    }


def fetch_baidu_qr_image(sign: str) -> Tuple[bytes, str]:
    clean_sign = clean_message_text(sign, 120)
    if not BAIDU_QR_SIGN_RE.fullmatch(clean_sign):
        raise ValueError("百度扫码 sign 无效，请重新生成二维码。")
    image_url = f"https://passport.baidu.com/v2/api/qrcode?{urllib.parse.urlencode({'sign': clean_sign, 'lp': 'pc'})}"
    req = urllib.request.Request(image_url, headers={"User-Agent": BAIDU_QR_LOGIN_USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        content_type = str(resp.headers.get("Content-Type") or "image/png").split(";")[0].strip().lower()
        image_bytes = resp.read(1024 * 512)
    if content_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        content_type = "image/png"
    if not image_bytes:
        raise ValueError("百度扫码二维码为空，请重新生成。")
    return image_bytes, content_type


def extract_channel_v(payload: dict) -> dict:
    channel_v = payload.get("channel_v") or payload.get("channelV") or {}
    if isinstance(channel_v, str):
        try:
            parsed = json.loads(channel_v)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return channel_v if isinstance(channel_v, dict) else {}


def extract_baidu_login_values(cookie_jar: http.cookiejar.CookieJar, body: str) -> dict:
    values = {}
    for cookie in cookie_jar:
        name = str(cookie.name or "").upper()
        domain = str(cookie.domain or "").lower()
        if name == "STOKEN" and "pan.baidu.com" in domain:
            values[name] = str(cookie.value or "")
        elif name in {"BDUSS", "BDUSS_BFESS", "STOKEN", "PTOKEN", "BAIDUID", "PANPSC"} and name not in values:
            values[name] = str(cookie.value or "")
    payload = parse_loose_json_payload(body)
    candidates = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
    session = payload.get("session")
    if isinstance(session, dict):
        candidates.append(session)
    for item in candidates:
        for key, value in item.items():
            key_upper = str(key).upper()
            if key_upper in {"BDUSS", "STOKEN", "PTOKEN", "BAIDUID", "PANPSC"} and value:
                values[key_upper] = str(value)
    for key in ("BDUSS", "STOKEN", "PTOKEN", "BAIDUID", "PANPSC"):
        pattern = re.compile(rf'"{key.lower()}"\s*:\s*"([^"]+)"', re.IGNORECASE)
        match = pattern.search(body)
        if match:
            values[key] = match.group(1)
    if "BDUSS" not in values and "BDUSS_BFESS" in values:
        values["BDUSS"] = values["BDUSS_BFESS"]
    return values


def make_baidu_cookie(name: str, value: str, domain: str) -> http.cookiejar.Cookie:
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def hydrate_baidu_pan_cookie_values(values: dict) -> dict:
    hydrated = dict(values)
    if not hydrated.get("BDUSS"):
        return hydrated
    cookie_jar = http.cookiejar.CookieJar()
    for key in ("BDUSS", "STOKEN", "PTOKEN", "BAIDUID"):
        value = str(hydrated.get(key) or "")
        if value:
            cookie_jar.set_cookie(make_baidu_cookie(key, value, ".baidu.com"))
    if hydrated.get("PANPSC"):
        cookie_jar.set_cookie(make_baidu_cookie("PANPSC", str(hydrated["PANPSC"]), ".pan.baidu.com"))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    for url in ("https://pan.baidu.com/", "https://pan.baidu.com/disk/main"):
        req = urllib.request.Request(url, headers={"User-Agent": BAIDU_QR_LOGIN_USER_AGENT})
        try:
            with opener.open(req, timeout=20) as resp:
                resp.read(4096)
        except Exception:
            continue
    for cookie in cookie_jar:
        name = str(cookie.name or "").upper()
        value = str(cookie.value or "")
        domain = str(cookie.domain or "").lower()
        if name == "STOKEN" and "pan.baidu.com" in domain and value:
            hydrated["STOKEN"] = value
        elif name in {"BAIDUID", "PANPSC", "PTOKEN"} and value:
            hydrated[name] = value
    return hydrated


def build_baidu_cookie_string(values: dict) -> str:
    parts = []
    for key in ("BDUSS", "STOKEN", "PTOKEN", "BAIDUID", "PANPSC"):
        value = str(values.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    return "; ".join(parts)


def finish_baidu_qr_login(qr_bduss: str) -> dict:
    clean_bduss = clean_message_text(qr_bduss, 3000)
    if not clean_bduss:
        return {"ok": False, "error": "百度扫码确认后没有返回登录凭证。"}
    params = urllib.parse.urlencode(
        {
            "bduss": clean_bduss,
            "tpl": BAIDU_QR_LOGIN_TPL,
            "qrcode": "1",
            "apiver": "v3",
            "loginVersion": "v4",
            "u": "https://pan.baidu.com/disk/home",
        }
    )
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    req = urllib.request.Request(
        f"https://passport.baidu.com/v3/login/main/qrbdusslogin?{params}",
        headers={"User-Agent": BAIDU_QR_LOGIN_USER_AGENT, "Referer": "https://pan.baidu.com/"},
    )
    try:
        with opener.open(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        return {"ok": False, "error": f"百度扫码凭证换取 Cookie 失败：{exc}"}
    values = hydrate_baidu_pan_cookie_values(extract_baidu_login_values(cookie_jar, body))
    cookie_string = build_baidu_cookie_string(values)
    if "BDUSS" not in values or "STOKEN" not in values:
        return {
            "ok": False,
            "qr_status": "token_incomplete",
            "error": "扫码已确认，但百度接口没有返回 BaiduPCS-Go 转存所需的完整 BDUSS/STOKEN，请改用 Cookie 导入。",
            "has_bduss": "BDUSS" in values,
            "has_stoken": "STOKEN" in values,
        }
    result = run_baidupcs_cookie_login(cookie_string)
    if not result.get("ok"):
        return {"ok": False, "qr_status": "import_failed", **result}
    return {"ok": True, "qr_status": "imported", **result}


def poll_baidu_qr_login(sign: str) -> dict:
    clean_sign = clean_message_text(sign, 120)
    if not BAIDU_QR_SIGN_RE.fullmatch(clean_sign):
        return {"ok": False, "error": "百度扫码 sign 无效，请重新生成二维码。"}
    params = urllib.parse.urlencode({"channel_id": clean_sign, "callback": ""})
    try:
        payload, _ = baidu_qr_request(f"https://passport.baidu.com/channel/unicast?{params}", timeout=12)
    except (TimeoutError, socket.timeout):
        return {"ok": True, "qr_status": "waiting", "message": "等待扫码。"}
    except URLError as exc:
        if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
            return {"ok": True, "qr_status": "waiting", "message": "等待扫码。"}
        return {"ok": False, "error": f"轮询百度扫码状态失败：{exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"轮询百度扫码状态失败：{exc}"}
    errno = str(payload.get("errno", ""))
    if errno != "0":
        message = clean_message_text(str(payload.get("errmsg") or payload.get("msg") or payload), 300)
        if errno in {"1", "2", "6"}:
            return {"ok": True, "qr_status": "waiting", "message": message or "等待扫码。"}
        if errno in {"3", "4", "5"}:
            return {"ok": False, "qr_status": "expired", "error": message or "二维码已过期，请重新生成。"}
        return {"ok": False, "qr_status": "failed", "error": message or "百度扫码状态异常。"}
    channel_v = extract_channel_v(payload)
    if str(channel_v.get("status") or "") in {"1", "2"}:
        return {"ok": True, "qr_status": "scanned", "message": "已扫码，请在手机端确认登录。"}
    qr_bduss = str(channel_v.get("v") or channel_v.get("bduss") or "")
    if not qr_bduss:
        return {"ok": True, "qr_status": "waiting", "message": "等待扫码确认。"}
    result = finish_baidu_qr_login(qr_bduss)
    if not result.get("ok"):
        return result
    return result


def baidu_share_confirmation_token(share_url: str, extraction_code: str) -> str:
    payload = f"{normalize_baidu_share_url(share_url)}|{extraction_code}|{datetime.now().strftime('%Y-%m-%d')}"
    return hmac.new(TOOL_API_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def human_file_size(size: int) -> str:
    value = float(max(0, int(size or 0)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.2f} TB"


def baidu_pan_cookie_opener() -> urllib.request.OpenerDirector:
    config_path = Path(BAIDUPCS_GO_CONFIG_DIR).expanduser().joinpath("pcs_config.json")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    active_uid = str(payload.get("baidu_active_uid") or "0")
    active_user = None
    for user in payload.get("baidu_user_list") or []:
        if str(user.get("uid") or "") == active_uid:
            active_user = user
            break
    if not active_user:
        raise ValueError("BaiduPCS-Go 尚未登录百度网盘账号")
    values = hydrate_baidu_pan_cookie_values(
        {
            "BDUSS": str(active_user.get("bduss") or ""),
            "STOKEN": str(active_user.get("stoken") or ""),
            "PTOKEN": str(active_user.get("ptoken") or ""),
        }
    )
    if not values.get("BDUSS") or not values.get("STOKEN"):
        raise ValueError("百度网盘登录态缺少 BDUSS 或网盘 STOKEN")
    cookie_jar = http.cookiejar.CookieJar()
    for key in ("BDUSS", "STOKEN", "PTOKEN", "BAIDUID", "PANPSC"):
        value = str(values.get(key) or "")
        if not value:
            continue
        domain = ".pan.baidu.com" if key in {"STOKEN", "PANPSC"} else ".baidu.com"
        cookie_jar.set_cookie(make_baidu_cookie(key, value, domain))
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))


def baidu_share_surl(share_url: str) -> str:
    path_item = urllib.parse.urlparse(share_url).path.rsplit("/", 1)[-1]
    return path_item[1:] if path_item.startswith("1") else path_item


def extract_locals_mset(html: str) -> dict:
    match = re.search(r"locals\.mset\((\{.*?\})\);", html, re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def fetch_baidu_share_preflight(share_url: str, extraction_code: str) -> dict:
    opener = baidu_pan_cookie_opener()
    safe_share_url = normalize_baidu_share_url(share_url)
    safe_code = clean_message_text(extraction_code, 16)
    surl = baidu_share_surl(safe_share_url)
    verify_url = "https://pan.baidu.com/share/verify?" + urllib.parse.urlencode(
        {
            "surl": surl,
            "t": str(int(time.time() * 1000)),
            "channel": "chunlei",
            "web": "1",
            "app_id": "250528",
            "bdstoken": "null",
            "clienttype": "0",
        }
    )
    req = urllib.request.Request(
        verify_url,
        data=urllib.parse.urlencode({"pwd": safe_code, "vcode": "", "vcode_str": ""}).encode("utf-8"),
        headers={
            "User-Agent": BAIDU_QR_LOGIN_USER_AGENT,
            "Referer": safe_share_url,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    with opener.open(req, timeout=20) as resp:
        verify_payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    if str(verify_payload.get("errno")) != "0":
        return {
            "ok": False,
            "error": "分享链接或提取码校验失败，暂不能确认文件大小。",
            "errno": verify_payload.get("errno"),
        }
    page_url = safe_share_url + ("&" if "?" in safe_share_url else "?") + "pwd=" + urllib.parse.quote(safe_code)
    with opener.open(urllib.request.Request(page_url, headers={"User-Agent": BAIDU_QR_LOGIN_USER_AGENT}), timeout=20) as resp:
        html = resp.read().decode("utf-8", "replace")
    locals_data = extract_locals_mset(html)
    root_items = locals_data.get("file_list") or []
    if not isinstance(root_items, list):
        root_items = []
    shareid = str(locals_data.get("shareid") or "")
    uk = str(locals_data.get("share_uk") or "")
    sekey = urllib.parse.unquote(str(verify_payload.get("randsk") or ""))
    queue = list(root_items)
    cursor = 0
    file_count = 0
    dir_count = 0
    total_bytes = 0
    pages = 0
    started_at = time.time()
    truncated = False
    root_names = [clean_message_text(str(item.get("server_filename") or ""), 80) for item in root_items[:5] if isinstance(item, dict)]
    while cursor < len(queue):
        if pages >= BAIDU_SHARE_PREFLIGHT_MAX_PAGES or time.time() - started_at > BAIDU_SHARE_PREFLIGHT_TIMEOUT_SECONDS:
            truncated = True
            break
        item = queue[cursor]
        cursor += 1
        if not isinstance(item, dict):
            continue
        if int(item.get("isdir") or 0):
            dir_count += 1
            if not shareid or not uk or not sekey:
                truncated = True
                continue
            dir_path = urllib.parse.unquote(str(item.get("path") or ""))
            page = 1
            while page <= 5:
                if pages >= BAIDU_SHARE_PREFLIGHT_MAX_PAGES or time.time() - started_at > BAIDU_SHARE_PREFLIGHT_TIMEOUT_SECONDS:
                    truncated = True
                    break
                params = {
                    "uk": uk,
                    "shareid": shareid,
                    "sekey": sekey,
                    "page": str(page),
                    "num": "100",
                    "dir": dir_path,
                    "root": "0",
                    "web": "1",
                    "app_id": "250528",
                    "channel": "chunlei",
                    "clienttype": "0",
                    "desc": "0",
                    "order": "name",
                }
                list_url = "https://pan.baidu.com/share/list?" + urllib.parse.urlencode(params)
                with opener.open(urllib.request.Request(list_url, headers={"User-Agent": BAIDU_QR_LOGIN_USER_AGENT, "Referer": safe_share_url}), timeout=20) as resp:
                    list_payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
                pages += 1
                if str(list_payload.get("errno")) != "0":
                    truncated = True
                    break
                children = list_payload.get("list") or []
                if not isinstance(children, list):
                    children = []
                queue.extend(children)
                if len(children) < 100:
                    break
                page += 1
        else:
            file_count += 1
            try:
                total_bytes += int(item.get("size") or 0)
            except (TypeError, ValueError):
                pass
    for item in queue[cursor:]:
        if isinstance(item, dict) and not int(item.get("isdir") or 0):
            file_count += 1
            try:
                total_bytes += int(item.get("size") or 0)
            except (TypeError, ValueError):
                pass
    return {
        "ok": True,
        "share_url_masked": mask_baidu_share_url(safe_share_url),
        "extraction_code_masked": mask_baidu_extract_code(safe_code),
        "root_names": root_names,
        "file_count": file_count,
        "dir_count": dir_count,
        "total_bytes": total_bytes,
        "total_size_display": human_file_size(total_bytes),
        "scanned_pages": pages,
        "truncated": truncated,
        "confirmation_token": baidu_share_confirmation_token(safe_share_url, safe_code),
    }


def run_baidupcs_cookie_login(cookies: str) -> dict:
    binary = baidupcs_binary_path()
    if not binary:
        return {
            "ok": False,
            "error": "未找到 BaiduPCS-Go，请设置 BAIDUPCS_GO_BIN 或安装到 ~/.local/bin/BaiduPCS-Go。",
        }
    clean_cookies = str(cookies or "").strip()
    cookie_values = parse_cookie_header_values(clean_cookies)
    if not cookie_values.get("BDUSS") or not cookie_values.get("STOKEN"):
        return {
            "ok": False,
            "error": "Cookie 必须包含 BDUSS 和 STOKEN；请在百度网盘网页版登录后复制完整 Cookie。",
        }
    login_args = [
        binary,
        "login",
        f"-bduss={cookie_values['BDUSS']}",
        f"-stoken={cookie_values['STOKEN']}",
    ]
    if cookie_values.get("PTOKEN"):
        login_args.append(f"-ptoken={cookie_values['PTOKEN']}")
    try:
        result = subprocess.run(
            login_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
            env=baidupcs_env(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "BaiduPCS-Go 登录命令超时。"}
    output = redact_baidupcs_login_output("\n".join(part for part in [result.stdout, result.stderr] if part))
    ready, login_message = baidupcs_login_ready()
    if result.returncode != 0 or not ready:
        return {
            "ok": False,
            "error": login_message,
            "output": output,
        }
    return {
        "ok": True,
        "message": login_message,
        "output": output,
    }


def baidu_share_worker_status() -> dict:
    binary = baidupcs_binary_path()
    ready, login_message = baidupcs_login_ready()
    return {
        "worker_enabled": BAIDU_SHARE_WORKER_ENABLED,
        "binary_found": bool(binary),
        "binary_path": binary,
        "config_dir": str(Path(BAIDUPCS_GO_CONFIG_DIR).expanduser()),
        "login_ready": ready,
        "login_message": login_message,
        "netdisk_default_root": normalize_netdisk_root(""),
        "server_default_root": normalize_server_download_root(""),
    }


def baidupcs_command_failed(result: subprocess.CompletedProcess, job: dict) -> str:
    output = summarize_baidupcs_output(result, job)
    if result.returncode != 0 or baidupcs_output_indicates_failure(output):
        return output
    return ""


def ensure_baidu_netdisk_tmp_dir(job: dict) -> Optional[str]:
    netdisk_path = normalize_netdisk_root(str(job.get("netdisk_save_path") or ""))
    netdisk_parent = posixpath.dirname(netdisk_path.rstrip("/")) or "/"
    for path_item in (netdisk_parent, netdisk_path):
        if path_item == "/":
            continue
        result = run_baidupcs_command(["mkdir", path_item], job)
        failed = baidupcs_command_failed(result, job)
        if failed and "已存在" not in failed and "already" not in failed.lower():
            return failed
    result = run_baidupcs_command(["cd", netdisk_path], job)
    failed = baidupcs_command_failed(result, job)
    return failed or None


def process_baidu_share_job(job: dict) -> None:
    job_id = str(job.get("job_id") or "")
    if not job_id:
        return
    binary = baidupcs_binary_path()
    if not binary:
        update_baidu_share_job_status(
            job_id,
            "worker_unavailable",
            "未找到 BaiduPCS-Go，请设置 BAIDUPCS_GO_BIN 或安装到 ~/.local/bin/BaiduPCS-Go。",
            "check_worker",
        )
        return
    ready, login_message = baidupcs_login_ready()
    if not ready:
        update_baidu_share_job_status(job_id, "login_required", login_message, "check_login")
        return

    update_baidu_share_job_status(job_id, "transferring", "正在准备百度网盘 tmp 目录。", "prepare_netdisk_dir")
    dir_error = ensure_baidu_netdisk_tmp_dir(job)
    if dir_error:
        update_baidu_share_job_status(job_id, "transfer_failed", f"准备网盘目录失败：{dir_error}", "prepare_netdisk_dir")
        return

    update_baidu_share_job_status(job_id, "transferring", "正在把分享文件转存到百度网盘 tmp 目录。", "transfer")
    transfer_result = run_baidupcs_command(
        ["transfer", str(job.get("share_url") or ""), str(job.get("extraction_code") or "")],
        job,
    )
    transfer_output = summarize_baidupcs_output(transfer_result, job)
    if transfer_result.returncode != 0 or not baidupcs_transfer_succeeded(transfer_output):
        update_baidu_share_job_status(job_id, "transfer_failed", f"转存失败：{transfer_output}", "transfer")
        return

    server_path = normalize_server_download_root(str(job.get("server_save_path") or ""))
    Path(server_path).mkdir(parents=True, exist_ok=True)
    update_baidu_share_job_status(job_id, "downloading", "转存成功，正在下载到服务器。", "download")
    download_result = run_baidupcs_command(
        ["download", "--saveto", server_path, str(job.get("netdisk_save_path") or "")],
        job,
    )
    download_output = summarize_baidupcs_output(download_result, job)
    if download_result.returncode != 0 or baidupcs_output_indicates_failure(download_output):
        update_baidu_share_job_status(job_id, "failed", f"下载失败：{download_output}", "download")
        return
    if not baidu_share_job_has_files(server_path):
        update_baidu_share_job_status(job_id, "failed", "下载命令结束，但服务器目录没有生成文件。", "download")
        return
    update_baidu_share_job_status(job_id, "completed", "下载完成，可以在页面下载 ZIP。", "completed")


def baidu_share_worker_once() -> bool:
    job = baidu_share_pending_job()
    if not job:
        return False
    try:
        process_baidu_share_job(job)
    except subprocess.TimeoutExpired:
        update_baidu_share_job_status(str(job.get("job_id") or ""), "failed", "BaiduPCS-Go 命令执行超时。", "timeout")
    except Exception as exc:
        update_baidu_share_job_status(str(job.get("job_id") or ""), "failed", f"worker 执行异常：{exc}", "worker_error")
    return True


def baidu_share_worker_loop() -> None:
    print("Baidu share worker started")
    last_cleanup_at = 0.0
    while True:
        now = time.time()
        if now - last_cleanup_at >= BAIDU_SHARE_CLEANUP_INTERVAL_SECONDS:
            try:
                cleanup_baidu_share_storage("scheduled")
            except Exception as exc:
                print(f"Baidu share storage cleanup failed: {exc}", file=sys.stderr)
            last_cleanup_at = now
        did_work = baidu_share_worker_once()
        time.sleep(1 if did_work else BAIDU_SHARE_WORKER_INTERVAL_SECONDS)


def list_baidu_share_jobs(limit: int = 20) -> list:
    init_baidu_share_job_db()
    safe_limit = max(1, min(int(limit or 20), 50))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, job_id, created_at, updated_at, status, share_url_masked,
                   extraction_code_masked, netdisk_save_path, server_save_path,
                   remote_addr, response_json
            FROM baidu_share_jobs
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [public_baidu_share_job(dict(row)) for row in rows]


def current_token_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def generate_daily_token() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def init_daily_token_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_verification_tokens (
                token_date TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def get_or_create_daily_token(token_date: Optional[str] = None) -> dict:
    init_daily_token_db()
    safe_date = token_date or current_token_date()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT token_date, token, created_at, updated_at
            FROM daily_verification_tokens
            WHERE token_date = ?
            """,
            (safe_date,),
        ).fetchone()
        if row:
            return dict(row)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        token = generate_daily_token()
        conn.execute(
            """
            INSERT INTO daily_verification_tokens (token_date, token, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (safe_date, token, now, now),
        )
    return {
        "token_date": safe_date,
        "token": token,
        "created_at": now,
        "updated_at": now,
    }


def rotate_daily_token(token_date: Optional[str] = None) -> dict:
    init_daily_token_db()
    safe_date = token_date or current_token_date()
    existing = get_or_create_daily_token(safe_date)
    token = generate_daily_token()
    while token == existing.get("token"):
        token = generate_daily_token()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    created_at = str(existing.get("created_at") or now)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO daily_verification_tokens (token_date, token, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(token_date) DO UPDATE SET
                token = excluded.token,
                updated_at = excluded.updated_at
            """,
            (safe_date, token, created_at, now),
        )
    return {
        "token_date": safe_date,
        "token": token,
        "created_at": created_at,
        "updated_at": now,
    }


def request_verification_token(params: Dict[str, str]) -> str:
    return (
        params.get("verification_token", "")
        or params.get("daily_token", "")
        or params.get("captcha", "")
    ).strip()


def validate_daily_token(params: Dict[str, str]) -> bool:
    provided = request_verification_token(params)
    if not re.fullmatch(r"\d{6}", provided or ""):
        return False
    current = str(get_or_create_daily_token().get("token") or "")
    return hmac.compare_digest(provided, current)


def cleanup_admin_sessions(now: Optional[float] = None) -> None:
    current = now or time.time()
    expired = [
        token
        for token, meta in ADMIN_SESSIONS.items()
        if float(meta.get("expires_at", 0)) <= current
    ]
    for token in expired:
        ADMIN_SESSIONS.pop(token, None)


def create_admin_session() -> str:
    cleanup_admin_sessions()
    token = secrets.token_urlsafe(32)
    ADMIN_SESSIONS[token] = {
        "created_at": time.time(),
        "expires_at": time.time() + ADMIN_SESSION_TTL_SECONDS,
    }
    return token


def is_valid_admin_session(token: str) -> bool:
    cleanup_admin_sessions()
    meta = ADMIN_SESSIONS.get(token or "")
    if not meta:
        return False
    meta["expires_at"] = time.time() + ADMIN_SESSION_TTL_SECONDS
    return True


def init_message_board_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message_board_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                tool_id TEXT NOT NULL,
                contact TEXT NOT NULL,
                contact_masked TEXT NOT NULL,
                content TEXT NOT NULL,
                remote_addr TEXT,
                user_agent TEXT,
                status TEXT NOT NULL DEFAULT 'new'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_message_board_created_at ON message_board_records(created_at DESC)"
        )


def record_board_message(
    *,
    tool_id: str,
    contact: str,
    content: str,
    remote_addr: str,
    user_agent: str,
) -> dict:
    init_message_board_db()
    safe_tool_id = clean_message_text(tool_id or "zepp-step", 64) or "zepp-step"
    safe_contact = clean_message_text(contact or "", 120)
    safe_content = clean_message_text(content, 500)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    contact_masked = mask_contact(safe_contact)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO message_board_records (
                created_at, tool_id, contact, contact_masked, content,
                remote_addr, user_agent, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                safe_tool_id,
                safe_contact,
                contact_masked,
                safe_content,
                remote_addr,
                user_agent,
                "new",
            ),
        )
        message_id = cursor.lastrowid
    return {
        "id": message_id,
        "created_at": created_at,
        "tool_id": safe_tool_id,
        "contact_masked": contact_masked,
        "content": safe_content,
        "status": "new",
    }


def list_board_messages(limit: int = 20) -> list:
    init_message_board_db()
    safe_limit = max(1, min(int(limit or 20), 50))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, created_at, tool_id, contact_masked, content, status
            FROM message_board_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def classify_error(message: str) -> dict:
    text = message or ""
    lower = text.lower()

    if "header_error=401" in text or "error=401" in text:
        return {
            "error_type": "invalid_credentials",
            "user_tip": "账号或密码错误，或该账号不是 Zepp Life 邮箱/手机号密码登录。",
            "action_tip": "请先在 Zepp Life App 用同一账号和密码登录；确认无误后再提交，避免连续重试。",
        }

    if "429" in text or "too many" in lower or "rate" in lower or "频繁" in text:
        return {
            "error_type": "rate_limited",
            "user_tip": "请求过于频繁，Zepp 暂时限制了登录或提交。",
            "action_tip": "暂停 10-30 分钟后再试；不要连续快速点击提交。",
        }

    if "步数超过上限" in text or "step_limit_exceeded" in lower:
        return {
            "error_type": "step_limit_exceeded",
            "user_tip": f"步数超过微信运动常见上限 {STEP_MAX}。",
            "action_tip": f"请填写 1-{STEP_MAX} 之间的步数；建议日常使用 8000-25000。",
        }

    if '"error_code":"0106"' in text or "error_code\":\"0106" in text:
        return {
            "error_type": "login_verification",
            "user_tip": "Zepp 登录验证失败，可能需要先在 App 完成安全验证。",
            "action_tip": "打开 Zepp Life App 手动登录并完成验证，再回到工具提交。",
        }

    if '"error_code":"0117"' in text or "error_code\":\"0117" in text:
        return {
            "error_type": "account_login_mode",
            "user_tip": "账号登录方式不匹配，常见于第三方登录或未完成邮箱验证。",
            "action_tip": "请使用 Zepp Life 直接注册的邮箱/手机号密码登录，或先完成邮箱验证。",
        }

    if "http请求失败" in lower or "timed out" in lower or "timeout" in lower:
        return {
            "error_type": "network_error",
            "user_tip": "服务器访问 Zepp 接口失败，可能是网络超时或上游暂时不可用。",
            "action_tip": "稍后重试；如果持续出现，请检查服务器网络出口。",
        }

    if "修改步数失败" in text:
        return {
            "error_type": "step_submit_failed",
            "user_tip": "登录成功，但步数提交接口返回失败。",
            "action_tip": "换一个更合理的步数再试，例如 8000-25000；也可以稍后重试。",
        }

    return {
        "error_type": "unknown_error",
        "user_tip": "提交失败，暂时无法判断具体类型。",
        "action_tip": "查看详细报错；如果多次出现，请先确认账号可在 Zepp Life App 正常登录。",
    }


def _simple_page_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>轻工具箱</title>
  <link rel="icon" href="/assets/logo.svg" />
  <style>
    :root {
      --bg: #f7fafc;
      --panel: #ffffff;
      --panel-soft: #fbfdff;
      --line: #e2e8f0;
      --text: #0f172a;
      --muted: #64748b;
      --soft: #f1f5f9;
      --primary: #0f766e;
      --primary-strong: #0f5f59;
      --primary-soft: #ecfeff;
      --blue: #2563eb;
      --success: #0f8f5f;
      --danger: #b91c1c;
      --danger-soft: #fff1f2;
      --danger-line: #fecdd3;
      --shadow: 0 16px 40px rgba(15, 23, 42, 0.07);
      --shadow-soft: 0 8px 18px rgba(15, 23, 42, 0.045);
    }

    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    body {
      margin: 0;
      color: var(--text);
      background:
        linear-gradient(180deg, #eefafa 0, rgba(247, 250, 252, 0) 280px),
        linear-gradient(90deg, rgba(15, 118, 110, 0.035) 1px, transparent 1px),
        linear-gradient(180deg, rgba(15, 118, 110, 0.035) 1px, transparent 1px),
        var(--bg);
      background-size: auto, 28px 28px, 28px 28px, auto;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      font-size: 14px;
    }

    .layout {
      display: grid;
      grid-template-columns: 248px minmax(0, 1fr);
      min-height: 100vh;
    }

    .sidebar {
      position: sticky;
      top: 0;
      height: 100vh;
      border-right: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.88);
      backdrop-filter: blur(16px);
      padding: 18px 14px;
      overflow-y: auto;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 11px;
      margin-bottom: 18px;
      padding: 4px 8px;
    }

    .brand-logo {
      width: 38px;
      height: 38px;
      border-radius: 10px;
      box-shadow: var(--shadow-soft);
      flex: none;
    }

    .brand-title { font-size: 18px; font-weight: 800; }
    .brand-subtitle { color: var(--muted); font-size: 12px; margin-top: 2px; }

    .side-section-title {
      padding: 14px 10px 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }

    .category {
      width: 100%;
      border: 0;
      background: transparent;
      color: var(--text);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px;
      border-radius: 8px;
      cursor: pointer;
      text-align: left;
      font-size: 14px;
    }

    .category:hover,
    .category.active {
      background: var(--primary-soft);
      color: #0f5f59;
    }

    .category-count {
      color: var(--muted);
      font-size: 12px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 1px 7px;
      background: var(--panel);
    }

    .main { min-width: 0; }

    .topbar {
      position: sticky;
      top: 0;
      z-index: 2;
      height: 70px;
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 0 28px;
      border-bottom: 1px solid var(--line);
      background: rgba(247, 250, 252, 0.82);
      backdrop-filter: blur(12px);
    }

    .search-wrap {
      max-width: 560px;
      width: 100%;
      position: relative;
    }

    .search-wrap input {
      width: 100%;
      height: 42px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 96px 0 38px;
      background: var(--panel);
      color: var(--text);
      outline: none;
      font-size: 14px;
    }

    .search-wrap input:focus {
      border-color: #93c5fd;
      box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.14);
    }

    .search-icon {
      position: absolute;
      left: 13px;
      top: 11px;
      color: var(--muted);
    }

    .kbd {
      position: absolute;
      right: 10px;
      top: 9px;
      color: var(--muted);
      border: 1px solid var(--line);
      background: var(--soft);
      border-radius: 6px;
      padding: 3px 8px;
      font-size: 12px;
    }

    .top-note {
      color: var(--muted);
      white-space: nowrap;
      margin-left: auto;
    }

    .content {
      padding: 24px 28px 38px;
      display: grid;
      gap: 22px;
    }

    .content > * {
      min-width: 0;
    }

    .section-head {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
    }

    h1 {
      margin: 0;
      font-size: 24px;
      line-height: 1.2;
      letter-spacing: 0;
    }

    .section-desc {
      margin: 8px 0 0;
      color: var(--muted);
    }

    .stats {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    .stat {
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.82);
      border-radius: 8px;
      padding: 9px 12px;
      min-width: 96px;
      box-shadow: var(--shadow-soft);
    }

    .stat strong {
      display: block;
      font-size: 18px;
    }

    .stat span {
      color: var(--muted);
      font-size: 12px;
    }

    .tools-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
      gap: 12px;
    }

    .tool-card {
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.92);
      border-radius: 8px;
      padding: 14px;
      cursor: pointer;
      min-height: 114px;
      transition: border-color 0.16s ease, transform 0.16s ease, box-shadow 0.16s ease;
    }

    .tool-card:hover {
      border-color: #99f6e4;
      transform: translateY(-1px);
      box-shadow: var(--shadow);
    }

    .tool-card.active {
      border-color: #2dd4bf;
      background: var(--primary-soft);
    }

    .tool-card.selected {
      border-color: #0f766e;
      box-shadow: 0 0 0 2px rgba(15, 118, 110, 0.12);
    }

    .tool-card.external {
      background: #fbfbfa;
      border-color: #d7d7d2;
    }

    .tool-card.external:hover {
      border-color: #17191c;
    }

    .tool-title {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      font-weight: 750;
      margin-bottom: 8px;
    }

    .tool-title-main {
      min-width: 0;
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }

    .tool-name {
      min-width: 0;
      overflow-wrap: anywhere;
    }

    .tool-icon {
      width: 28px;
      height: 28px;
      flex: none;
    }

    .keyrun-mark {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 3px;
      padding: 2px;
    }

    .keyrun-mark span {
      border-radius: 2px;
      background: #030405;
    }

    .keyrun-mark span:nth-child(4) {
      border-radius: 50%;
      background: linear-gradient(135deg, #f25a3d 0%, #e936a7 45%, #3d7dff 100%);
    }

    .badge {
      color: var(--primary);
      background: #ccfbf1;
      border-radius: 999px;
      padding: 1px 7px;
      font-size: 12px;
      height: 21px;
      flex: none;
    }

    .tool-desc {
      color: var(--muted);
      line-height: 1.55;
      margin: 0;
    }

    .tool-purpose {
      display: inline-flex;
      align-items: center;
      width: fit-content;
      max-width: 100%;
      margin: 0 0 8px;
      padding: 4px 8px;
      border: 1px solid #17191c;
      border-radius: 6px;
      background: #ffffff;
      color: #17191c;
      font-size: 12px;
      font-weight: 850;
      line-height: 1.35;
    }

    .workspace {
      display: grid;
      grid-template-columns: minmax(320px, 440px) minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }

    .tool-workspace {
      display: none;
    }

    .tool-workspace.active {
      display: grid;
    }

    .panel {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.94);
      border-radius: 8px;
      padding: 18px;
      box-shadow: var(--shadow);
    }

    .panel h2 {
      margin: 0 0 6px;
      font-size: 20px;
      letter-spacing: 0;
    }

    .panel p {
      margin: 0 0 16px;
      color: var(--muted);
      line-height: 1.6;
    }

    .guide {
      border: 1px solid #ccfbf1;
      background: #f8fffd;
      border-radius: 8px;
      padding: 12px;
      margin: 0 0 16px;
    }

    .tutorial-details {
      margin-top: 12px;
    }

    .tutorial-details summary {
      cursor: pointer;
      list-style: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      color: var(--text);
      font-weight: 900;
    }

    .tutorial-details summary::-webkit-details-marker {
      display: none;
    }

    .tutorial-details summary::after {
      content: "展开教程";
      color: var(--primary);
      border: 1px solid #99f6e4;
      background: #ecfeff;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      white-space: nowrap;
    }

    .tutorial-details[open] summary::after {
      content: "收起教程";
    }

    .tutorial-details .tutorial-steps {
      margin-top: 12px;
    }

    .tool-notice {
      margin: 10px 0 14px;
      border: 1px solid #bae6fd;
      background: #f0f9ff;
      color: #075985;
      border-radius: 8px;
      padding: 10px 12px;
      line-height: 1.6;
      font-size: 14px;
      font-weight: 700;
    }

    .flow-switch {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      border: 1px solid var(--line);
      background: #f8fafc;
      border-radius: 8px;
      padding: 5px;
      margin: 0 0 16px;
    }

    .flow-tab {
      min-height: 42px;
      border: 0;
      border-radius: 7px;
      background: transparent;
      color: var(--muted);
      font-weight: 850;
      cursor: pointer;
    }

    .flow-tab.active {
      background: #fff;
      color: var(--primary);
      box-shadow: var(--shadow-soft);
    }

    .method-panel { display: none; }
    .method-panel.active { display: block; }

    .flow-summary {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      padding: 11px 12px;
      margin-bottom: 14px;
      color: var(--muted);
      line-height: 1.6;
    }

    .flow-summary strong {
      color: var(--text);
    }

    .qr-workspace {
      display: grid;
      gap: 14px;
    }

    .qr-frame {
      position: relative;
      display: grid;
      place-items: center;
      min-height: 280px;
      border: 1px dashed #94a3b8;
      border-radius: 8px;
      background:
        linear-gradient(45deg, rgba(15, 118, 110, 0.045) 25%, transparent 25%),
        linear-gradient(-45deg, rgba(15, 118, 110, 0.045) 25%, transparent 25%),
        #ffffff;
      background-size: 18px 18px;
      overflow: hidden;
      user-select: none;
    }

    .qr-shield {
      display: grid;
      place-items: center;
      min-height: 220px;
      width: min(100%, 300px);
      padding: 20px;
      text-align: center;
      color: var(--muted);
      line-height: 1.55;
    }

    .qr-shield.qr-warning {
      width: min(calc(100% - 28px), 320px);
      min-height: 188px;
      border: 1px solid var(--danger-line);
      border-radius: 8px;
      background: rgba(255, 241, 242, 0.96);
      color: var(--danger);
      font-size: 15px;
      font-weight: 900;
      box-shadow: 0 8px 18px rgba(185, 28, 28, 0.08);
    }

    .device-qr {
      width: min(100%, 300px);
      aspect-ratio: 1 / 1;
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 8px;
      pointer-events: none;
      -webkit-user-drag: none;
    }

    .qr-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    .qr-status {
      min-height: 20px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 750;
      line-height: 1.5;
    }

    .qr-status.success { color: var(--success); }
    .qr-status.failed { color: var(--danger); }
    .qr-status.warning { color: var(--danger); }

    .qr-confirm-panel {
      display: grid;
      gap: 10px;
      border: 1px solid var(--danger-line);
      border-radius: 8px;
      background: var(--danger-soft);
      padding: 12px;
    }

    .qr-confirm-panel[hidden] {
      display: none;
    }

    .qr-confirm-check {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      color: var(--danger);
      font-size: 13px;
      font-weight: 800;
      line-height: 1.55;
    }

    .qr-confirm-check input {
      margin-top: 3px;
      flex: 0 0 auto;
      accent-color: var(--danger);
    }

    .danger-text {
      color: var(--danger);
      font-weight: 900;
    }

    .qr-rules {
      margin: 0;
      padding-left: 20px;
      color: var(--muted);
      line-height: 1.65;
    }

    .qr-tutorial {
      border: 1px solid #bfdbfe;
      background: #f8fbff;
      border-radius: 8px;
      padding: 12px;
      line-height: 1.65;
    }

    .qr-tutorial-title {
      color: var(--text);
      font-weight: 900;
      margin-bottom: 8px;
    }

    .qr-tutorial ol {
      margin: 0;
      padding-left: 20px;
      color: var(--muted);
    }

    .qr-tutorial li {
      margin: 5px 0;
    }

    .qr-tutorial strong {
      color: var(--danger);
    }

    .share-box {
      margin-top: 16px;
      border-top: 1px solid var(--line);
      padding-top: 16px;
    }

    .share-box h3 {
      margin: 0 0 6px;
      font-size: 16px;
      color: var(--text);
    }

    .share-box p {
      margin-bottom: 12px;
    }

    .share-upload-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }

    .share-upload-grid .wide {
      grid-column: 1 / -1;
    }

    input[type="file"] {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      background: #fff;
      color: var(--muted);
    }

    .share-status {
      min-height: 20px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 750;
      line-height: 1.5;
    }

    .share-status.success { color: var(--success); }
    .share-status.failed { color: var(--danger); }

    .share-meta {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 8px;
      padding: 10px 12px;
      color: var(--muted);
      line-height: 1.6;
      font-size: 13px;
    }

    .share-meta strong {
      color: var(--text);
    }

    .baidu-share-workspace {
      scroll-margin-top: 78px;
    }

    .qq-like-workspace {
      grid-template-columns: minmax(0, 1fr);
      scroll-margin-top: 78px;
      min-width: 0;
      width: 100%;
    }

    .qq-like-shell {
      width: 100%;
      max-width: 1040px;
      min-width: 0;
      display: grid;
      gap: 14px;
    }

    .qq-like-shell > * {
      min-width: 0;
      max-width: 100%;
    }

    .qq-like-titlebar {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
    }

    .qq-like-titlebar p {
      margin-bottom: 0;
    }

    .qq-like-account {
      display: grid;
      gap: 14px;
      border: 1px solid #99f6e4;
      border-radius: 8px;
      background: #f8fffd;
      padding: 14px 16px;
    }

    .qq-like-account-head,
    .qq-like-record-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .qq-like-account-state {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      color: var(--success);
      font-weight: 900;
    }

    .qq-like-account-state::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: currentColor;
      box-shadow: 0 0 0 4px rgba(15, 143, 95, 0.1);
    }

    .qq-like-account-state.pending {
      color: #b45309;
    }

    .qq-like-account-state.offline {
      color: var(--danger);
    }

    .qq-like-account-number {
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
      font-size: 20px;
      font-weight: 900;
      font-variant-numeric: tabular-nums;
    }

    .qq-like-account-number span {
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }

    .qq-like-link {
      border: 0;
      background: transparent;
      color: var(--primary);
      padding: 0;
      min-height: auto;
      font-weight: 850;
      cursor: pointer;
    }

    .qq-like-quota-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }

    .qq-like-quota {
      min-width: 0;
      padding: 2px 16px;
      text-align: center;
      border-right: 1px solid var(--line);
    }

    .qq-like-quota:first-child {
      padding-left: 0;
    }

    .qq-like-quota:last-child {
      padding-right: 0;
      border-right: 0;
    }

    .qq-like-quota span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }

    .qq-like-quota strong {
      display: block;
      margin-top: 5px;
      color: var(--primary);
      font-size: 22px;
      font-variant-numeric: tabular-nums;
    }

    .qq-like-form-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 220px;
      gap: 12px;
      align-items: end;
    }

    .qq-like-form-grid .form-row {
      margin: 0;
    }

    .qq-like-form-grid .primary {
      width: 100%;
      min-height: 42px;
    }

    .qq-like-task {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      margin-top: 8px;
    }

    .qq-like-task-step {
      position: relative;
      min-width: 0;
      padding: 0 8px;
      text-align: center;
      color: var(--muted);
    }

    .qq-like-task-step::before {
      content: "";
      position: absolute;
      top: 15px;
      left: 0;
      width: 50%;
      height: 2px;
      background: var(--line);
      transform: translateX(-50%);
    }

    .qq-like-task-step:first-child::before {
      display: none;
    }

    .qq-like-task-step.done::before,
    .qq-like-task-step.active::before {
      background: #2dd4bf;
    }

    .qq-like-task-node {
      position: relative;
      z-index: 1;
      width: 30px;
      height: 30px;
      margin: 0 auto 7px;
      display: grid;
      place-items: center;
      border: 1px solid #cbd5e1;
      border-radius: 50%;
      background: #fff;
      color: var(--muted);
      font-weight: 900;
    }

    .qq-like-task-step.done .qq-like-task-node,
    .qq-like-task-step.active .qq-like-task-node {
      border-color: var(--primary);
      background: var(--primary);
      color: #fff;
    }

    .qq-like-task-step strong {
      display: block;
      color: var(--text);
      font-size: 13px;
    }

    .qq-like-task-step.active strong {
      color: var(--primary);
    }

    .qq-like-task-step span {
      display: block;
      margin-top: 3px;
      font-size: 11px;
      line-height: 1.4;
    }

    .qq-like-table-wrap {
      min-width: 0;
      max-width: 100%;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
    }

    .qq-like-table {
      width: 100%;
      min-width: 640px;
      border-collapse: collapse;
      font-size: 13px;
    }

    .qq-like-table th,
    .qq-like-table td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      white-space: nowrap;
    }

    .qq-like-table th {
      color: var(--muted);
      background: #f8fafc;
      font-size: 12px;
    }

    .qq-like-table tr:last-child td {
      border-bottom: 0;
    }

    .qq-like-login-grid {
      display: grid;
      grid-template-columns: 220px minmax(0, 1fr);
      gap: 16px;
      align-items: center;
      margin-top: 12px;
    }

    .qq-like-qr {
      width: 220px;
      height: 220px;
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 8px;
    }

    .qq-like-login-copy {
      color: var(--muted);
      line-height: 1.7;
    }

    .qq-like-recovery {
      margin-top: 12px;
      border: 1px solid #fde68a;
      border-radius: 8px;
      background: #fffbeb;
      padding: 12px;
      color: #92400e;
      line-height: 1.6;
    }

    .qq-like-recovery code {
      display: block;
      margin: 8px 0;
      color: #78350f;
      font-size: 18px;
      font-weight: 900;
      letter-spacing: 0.06em;
      overflow-wrap: anywhere;
    }

    .qq-like-manage {
      margin-top: 12px;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }

    .qq-like-manage summary {
      cursor: pointer;
      color: var(--primary);
      font-weight: 850;
    }

    .qq-like-manage-body {
      display: grid;
      gap: 12px;
      margin-top: 12px;
    }

    .qq-like-safety {
      display: flex;
      gap: 10px;
      align-items: flex-start;
      border: 1px solid #a7f3d0;
      border-radius: 8px;
      background: #f0fdf4;
      padding: 12px 14px;
      color: #166534;
      line-height: 1.6;
      font-size: 13px;
    }

    .qq-like-safety strong {
      flex: none;
    }

    .baidu-share-form textarea {
      min-height: 132px;
      resize: vertical;
    }

    .path-preview {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }

    .path-preview code {
      display: block;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
      padding: 8px 10px;
      color: #334155;
      overflow-wrap: anywhere;
      font-size: 12px;
      line-height: 1.45;
    }

    .job-list {
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }

    .job-list-title {
      margin-top: 16px;
      color: var(--text);
      font-size: 14px;
      font-weight: 900;
    }

    .job-item {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 10px 12px;
      display: grid;
      gap: 6px;
    }

    .job-item-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }

    .job-item-main {
      color: var(--text);
      font-weight: 800;
      overflow-wrap: anywhere;
    }

    .job-item-state {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }

    .job-item-state strong {
      color: var(--text);
    }

    .job-download {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
      margin-top: 2px;
    }

    .download-link,
    .copy-download-link {
      display: inline-flex;
      align-items: center;
      min-height: 32px;
      border: 0;
      border-radius: 8px;
      padding: 0 10px;
      background: #0f766e;
      color: #fff;
      font-size: 12px;
      font-weight: 900;
      text-decoration: none;
      cursor: pointer;
      font-family: inherit;
    }

    .copy-download-link {
      background: #374151;
    }

    .download-note {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }

    .job-item-path {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .shared-step-card {
      display: grid;
      gap: 8px;
      border: 1px solid #bfdbfe;
      background: #f8fbff;
      border-radius: 8px;
      padding: 12px;
    }

    .shared-step-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .shared-step-head span {
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
    }

    .shared-step-head strong {
      color: var(--primary);
      font-size: 24px;
      line-height: 1;
      font-weight: 900;
      font-variant-numeric: tabular-nums;
    }

    .shared-step-hint {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }

    .shared-step-hint strong {
      color: var(--text);
      font-weight: 900;
    }

    .guide-title {
      font-weight: 800;
      margin-bottom: 8px;
    }

    .tutorial-steps {
      display: grid;
      gap: 12px;
    }

    .tutorial-step {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 10px;
      padding: 12px;
      line-height: 1.65;
    }

    .tutorial-step h3 {
      margin: 0 0 8px;
      font-size: 15px;
      color: var(--text);
    }

    .tutorial-step ol,
    .tutorial-step ul {
      margin: 0;
      padding-left: 20px;
      color: var(--muted);
    }

    .tutorial-step li {
      margin: 4px 0;
    }

    .tutorial-step strong {
      color: var(--text);
      font-weight: 800;
    }

    .tutorial-url {
      display: inline-flex;
      margin-top: 6px;
      padding: 3px 8px;
      border-radius: 999px;
      background: #ecfeff;
      color: #0f766e;
      font-weight: 800;
      font-size: 12px;
    }

    .guide-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .guide-item {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 9px 10px;
      line-height: 1.45;
    }

    .guide-item strong {
      display: block;
      margin-bottom: 3px;
      font-size: 13px;
    }

    .guide-item span {
      color: var(--muted);
      font-size: 13px;
    }

    .guide-source {
      display: inline-block;
      margin-top: 9px;
      color: var(--primary);
      text-decoration: none;
      font-size: 13px;
    }

    .app-guide {
      margin-top: 12px;
      display: grid;
      gap: 10px;
    }

    .app-guide-title {
      font-weight: 800;
      font-size: 13px;
      color: var(--text);
    }

    .app-choice-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .app-choice-card {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 10px;
      padding: 10px;
      display: grid;
      gap: 10px;
    }

    .app-choice-card.recommended {
      border-color: #7dd3fc;
      background: #f0f9ff;
    }

    .app-choice-main {
      display: flex;
      gap: 10px;
      align-items: center;
    }

    .app-logo {
      width: 48px;
      height: 48px;
      border-radius: 14px;
      object-fit: cover;
      border: 1px solid var(--line);
      background: #fff;
      flex: none;
    }

    .app-name {
      font-weight: 900;
    }

    .app-desc {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      margin-top: 2px;
    }

    .app-badge {
      display: inline-flex;
      width: fit-content;
      align-items: center;
      border-radius: 999px;
      padding: 2px 7px;
      color: #0369a1;
      background: #e0f2fe;
      font-size: 12px;
      font-weight: 800;
    }

    .app-original {
      display: block;
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
      background: #fff;
    }

    .app-original img {
      display: block;
      width: 100%;
      max-height: 210px;
      object-fit: cover;
      object-position: top;
    }

    .field-hint {
      color: var(--muted);
      font-size: 12px;
      margin-top: 6px;
      line-height: 1.5;
    }

    .preview-note {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    .form-row { margin-bottom: 14px; }
    label {
      display: block;
      color: #374151;
      font-weight: 650;
      margin-bottom: 7px;
    }

    input[type="text"],
    input[type="password"],
    input[type="number"] {
      width: 100%;
      height: 42px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 11px;
      background: #fff;
      outline: none;
      font-size: 14px;
    }

    input:focus {
      border-color: #93c5fd;
      box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.14);
    }

    .inline-option {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-weight: 500;
    }

    .actions {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 16px;
    }

    button.primary {
      height: 40px;
      border: 0;
      border-radius: 8px;
      padding: 0 16px;
      background: linear-gradient(135deg, var(--primary), #0ea5e9);
      color: #fff;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 8px 16px rgba(14, 165, 233, 0.18);
    }

    button.primary:disabled {
      opacity: 0.65;
      cursor: not-allowed;
    }

    button.ghost {
      height: 40px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 14px;
      background: #ffffff;
      color: var(--text);
      cursor: pointer;
    }

    button.ghost:hover {
      border-color: #99f6e4;
      background: #f8fffd;
    }

    .result-box {
      min-height: 290px;
      border: 1px solid var(--line);
      background: #fbfdff;
      color: #334155;
      border-radius: 8px;
      padding: 14px;
      overflow: auto;
      white-space: pre-wrap;
      font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }

    .result-status {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      border-radius: 999px;
      padding: 4px 9px;
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 10px;
    }

    .status-idle { color: var(--muted); background: var(--soft); }
    .status-success { color: var(--success); background: #dcfce7; }
    .status-failed { color: var(--danger); background: #ffedd5; }

    .tip-box {
      display: none;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px 12px;
      margin-bottom: 12px;
      line-height: 1.55;
    }

    .tip-box.show { display: block; }
    .tip-box.success { border-color: #bbf7d0; background: #f0fdf4; color: #166534; }
    .tip-box.failed { border-color: #fed7aa; background: #fff7ed; color: #9a3412; }
    .tip-box strong { display: block; margin-bottom: 4px; }
    .tip-box span { display: block; color: inherit; opacity: 0.88; }
    .tip-box .tip-action { margin-top: 10px; }

    .log-head {
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 12px;
      margin-bottom: 12px;
    }

    .log-table {
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
    }

    .log-table table {
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
      background: #fff;
    }

    .log-table th,
    .log-table td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 13px;
    }

    .log-table th {
      background: #f8fafc;
      color: #374151;
      font-weight: 800;
    }

    .log-table tr:last-child td { border-bottom: 0; }

    .log-message {
      max-width: 360px;
      color: var(--muted);
      word-break: break-word;
    }

    .status-pill {
      display: inline-flex;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 800;
    }

    .status-pill.success { color: var(--success); background: #dcfce7; }
    .status-pill.failed { color: var(--danger); background: #ffedd5; }
    .status-pill.info { color: #0369a1; background: #e0f2fe; }
    .status-pill.running { color: #7c3aed; background: #ede9fe; }
    .status-pill.warning { color: #92400e; background: #fef3c7; }

    .support-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }

    .community-card {
      overflow: hidden;
      position: relative;
    }

    .community-card::before {
      content: "";
      position: absolute;
      inset: 0;
      background:
        radial-gradient(circle at 15% 12%, rgba(45, 212, 191, 0.18), transparent 28%),
        radial-gradient(circle at 88% 20%, rgba(96, 165, 250, 0.18), transparent 30%);
      pointer-events: none;
    }

    .community-inner {
      position: relative;
      display: grid;
      gap: 14px;
    }

    .community-top .community-inner {
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 18px;
    }

    .community-main {
      display: grid;
      gap: 12px;
      min-width: 0;
    }

    .scroll-tip {
      display: inline-flex;
      width: fit-content;
      align-items: center;
      border: 1px solid #99f6e4;
      border-radius: 999px;
      background: #ecfeff;
      color: var(--primary-strong);
      padding: 5px 10px;
      font-size: 13px;
      font-weight: 900;
      text-decoration: none;
    }

    .community-top button.ghost {
      justify-self: start;
    }

    .group-meta {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .group-avatar {
      width: 58px;
      height: 58px;
      border-radius: 20px;
      display: grid;
      place-items: center;
      color: #0f766e;
      font-weight: 900;
      background: linear-gradient(135deg, #ccfbf1, #dbeafe);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.8);
      flex: none;
    }

    .group-name {
      font-weight: 900;
      font-size: 18px;
    }

    .group-number {
      color: var(--muted);
      margin-top: 4px;
      font-size: 14px;
    }

    .qq-qr {
      width: min(100%, 320px);
      border-radius: 18px;
      border: 1px solid var(--line);
      background: #fff;
      justify-self: center;
      box-shadow: var(--shadow-soft);
    }

    .community-top .qq-qr {
      width: min(34vw, 220px);
      min-width: 150px;
      max-height: 220px;
      object-fit: contain;
      border-radius: 12px;
    }

    .message-form {
      display: grid;
      gap: 12px;
    }

    .message-form textarea {
      min-height: 110px;
      resize: vertical;
    }

    .form-row select,
    .form-row textarea {
      width: 100%;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 11px 12px;
      font: inherit;
      color: var(--text);
      background: #fff;
      outline: none;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }

    .form-row select:focus,
    .form-row textarea:focus {
      border-color: #2dd4bf;
      box-shadow: 0 0 0 4px rgba(45, 212, 191, 0.16);
    }

    .message-status {
      min-height: 20px;
      color: var(--muted);
      font-weight: 700;
      font-size: 13px;
    }

    .message-status.success { color: var(--success); }
    .message-status.failed { color: var(--danger); }

    .copy-toast {
      position: fixed;
      left: 50%;
      bottom: 24px;
      z-index: 80;
      min-width: min(88vw, 260px);
      max-width: calc(100vw - 32px);
      transform: translate(-50%, 18px);
      opacity: 0;
      pointer-events: none;
      border-radius: 999px;
      padding: 12px 16px;
      background: #14532d;
      color: #fff;
      box-shadow: 0 18px 48px rgba(15, 23, 42, 0.22);
      font-weight: 900;
      text-align: center;
      transition: opacity 0.18s ease, transform 0.18s ease;
    }

    .copy-toast.show {
      opacity: 1;
      transform: translate(-50%, 0);
    }

    .copy-toast.failed {
      background: #9a3412;
    }

    .message-list {
      display: grid;
      gap: 10px;
      margin-top: 16px;
    }

    .message-item {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      background: var(--panel-soft);
    }

    .message-item-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }

    .message-item-body {
      color: var(--text);
      line-height: 1.65;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .empty-card {
      border: 1px dashed #cbd5e1;
      border-radius: 8px;
      padding: 18px;
      background: rgba(255,255,255,0.6);
      color: var(--muted);
      line-height: 1.6;
    }

    .mobile-menu { display: none; }

    @media (max-width: 920px) {
      .layout { grid-template-columns: 1fr; }
      .sidebar {
        position: static;
        height: auto;
        border-right: 0;
        border-bottom: 1px solid var(--line);
        padding: 14px 16px;
      }
      .sidebar .brand { margin-bottom: 0; }
      .sidebar .side-section-title,
      .sidebar .category { display: none; }
      .topbar { padding: 12px 16px; height: auto; flex-wrap: wrap; }
      .top-note { display: none; }
      .content { padding: 18px 16px 30px; }
      .workspace { grid-template-columns: 1fr; }
      .support-grid { grid-template-columns: 1fr; }
      .section-head { align-items: start; flex-direction: column; }
      .guide-grid { grid-template-columns: 1fr; }
      .app-choice-grid { grid-template-columns: 1fr; }
      .community-top .community-inner { grid-template-columns: 1fr; }
      .community-top .qq-qr {
        width: min(100%, 220px);
        max-height: 220px;
        justify-self: start;
      }
      .qq-like-titlebar,
      .qq-like-account-head,
      .qq-like-record-head {
        align-items: flex-start;
      }
      .qq-like-form-grid,
      .qq-like-login-grid {
        grid-template-columns: 1fr;
      }
      .qq-like-qr {
        width: min(100%, 220px);
        height: auto;
        aspect-ratio: 1 / 1;
        justify-self: center;
      }
      .qq-like-task {
        grid-template-columns: 1fr;
        gap: 10px;
      }
      .qq-like-task-step {
        display: grid;
        grid-template-columns: 30px minmax(0, 1fr);
        grid-template-rows: auto auto;
        column-gap: 10px;
        text-align: left;
        padding: 0;
      }
      .qq-like-task-step::before {
        top: -10px;
        left: 14px;
        width: 2px;
        height: 10px;
        transform: none;
      }
      .qq-like-task-node {
        grid-row: 1 / span 2;
        margin: 0;
      }
      .qq-like-task-step span {
        margin-top: 1px;
      }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        <img class="brand-logo" src="/assets/logo.svg" alt="轻工具箱 logo" />
        <div>
          <div class="brand-title">轻工具箱</div>
          <div class="brand-subtitle">本地优先的在线工具站</div>
        </div>
      </div>
      <div class="side-section-title">工具分类</div>
      <button class="category active" data-category="all"><span>全部工具</span><span class="category-count">10</span></button>
      <button class="category" data-category="life"><span>生活工具</span><span class="category-count">1</span></button>
      <button class="category" data-category="file"><span>文件工具</span><span class="category-count">1</span></button>
      <button class="category" data-category="qq"><span>QQ 工具</span><span class="category-count">1</span></button>
      <button class="category" data-category="promotion"><span>推广工具</span><span class="category-count">1</span></button>
      <button class="category" data-category="dev"><span>开发工具</span><span class="category-count">3</span></button>
      <button class="category" data-category="text"><span>文本工具</span><span class="category-count">1</span></button>
      <button class="category" data-category="image"><span>图片工具</span><span class="category-count">1</span></button>
      <button class="category" data-category="office"><span>办公工具</span><span class="category-count">1</span></button>
      <div class="side-section-title">当前工具</div>
      <button class="category active" data-tool-shortcut="zepp-step"><span>微信步数修改</span><span class="category-count">可用</span></button>
      <button class="category" data-tool-shortcut="baidu-share"><span>网盘中转下载</span><span class="category-count">记录中</span></button>
      <button class="category" data-tool-shortcut="qq-like"><span>QQ 互赞</span><span class="category-count">可用</span></button>
    </aside>

    <main class="main">
      <div class="topbar">
        <div class="search-wrap">
          <span class="search-icon">⌕</span>
          <input id="searchInput" type="text" placeholder="搜索工具，例如：步数、KeyRun、JSON..." />
          <span class="kbd">Ctrl K</span>
        </div>
        <div class="top-note">工具导航 · 本地服务 · 可持续扩展</div>
      </div>

      <div class="content">
        <section class="section-head">
          <div>
            <h1>工具导航</h1>
            <p class="section-desc">先把已跑通的微信步数修改放进工具站框架，也支持跳转到独立工具页面和人工对接服务。</p>
          </div>
          <div class="stats">
            <div class="stat"><strong>4</strong><span>自助工具</span></div>
            <div class="stat"><strong>1</strong><span>人工对接</span></div>
            <div class="stat"><strong>5</strong><span>预留位置</span></div>
          </div>
        </section>

        <section class="panel community-card community-top">
          <div class="community-inner">
            <div class="community-main">
              <div>
                <h2>QQ 交流群</h2>
                <p>工具使用不成功、同步失败或想加新工具，可以先进群反馈。</p>
              </div>
              <div class="group-meta">
                <div class="group-avatar">喵</div>
                <div>
                  <div class="group-name">__QQ_GROUP_NAME__</div>
                  <div class="group-number">QQ 群号：<span id="groupNumber">__QQ_GROUP_NUMBER__</span></div>
                </div>
              </div>
              <a class="scroll-tip" href="#zeppStepPanel">使用工具请向下滑动</a>
              <button class="ghost" type="button" id="copyGroupNumber">复制 QQ 群号</button>
            </div>
            <img class="qq-qr" src="/assets/qq-group.jpg" alt="__QQ_GROUP_NAME__ QQ 群二维码" />
          </div>
        </section>

        <section class="tools-grid" id="toolsGrid"></section>

        <section class="workspace tool-workspace active" id="zeppStepPanel" data-tool-workspace="zepp-step">
          <div class="panel" id="toolPanel">
            <h2>微信步数修改</h2>
            <p>有设备的账号继续走 Zepp Life 提交；没有设备的用户走扫码绑定处理，避免把两种链路混在一起。</p>
            <div class="tool-notice">微信运动同步通常需要 Zepp / Zepp Life 账号已绑定有效手环或手表设备；如果 Zepp 显示成功但微信运动不同步，请优先检查设备绑定。没有设备的用户切换到“没有设备，扫码绑定”。</div>
            <div class="flow-switch" role="tablist" aria-label="选择步数修改方式">
              <button class="flow-tab active" type="button" data-flow="self" role="tab" aria-selected="true">我有设备，自己修改</button>
              <button class="flow-tab" type="button" data-flow="qr" role="tab" aria-selected="false">没有设备，扫码绑定</button>
            </div>
            <section class="method-panel active" data-flow-panel="self">
              <div class="flow-summary"><strong>适用情况：</strong>你自己的 Zepp / Zepp Life 账号已经绑定有效手环或手表，并且已经在 App 里完成微信运动第三方授权。</div>
            <details class="guide tutorial-details">
              <summary>使用教程：注册、绑定微信、提交和排查</summary>
              <div class="tutorial-steps">
                <section class="tutorial-step">
                  <h3>一、下载并注册 Zepp Life 账号（必做）</h3>
                  <ol>
                    <li>手机应用商店搜索 <strong>Zepp Life（原小米运动）</strong>，下载安装。</li>
                    <li>打开 App 后点击注册，<strong>不要使用微信 / QQ / 小米账号快捷登录</strong>。</li>
                    <li>推荐选择邮箱注册，例如 QQ 邮箱或 163 邮箱，填写邮箱和密码后完成注册并登录。</li>
                    <li>注册后性别、身高、体重等信息可按提示填写；注册阶段可以先跳过绑定设备。</li>
                    <li>如果需要同步到微信运动，账号通常还需要绑定有效手环或手表设备；没有设备请加入下方 QQ 交流群联系群主。</li>
                  </ol>
                </section>
                <section class="tutorial-step">
                  <h3>二、绑定微信运动（同步步数到微信）</h3>
                  <ol>
                    <li>Zepp Life 底部点击 <strong>我的</strong>，找到 <strong>第三方接入</strong>。</li>
                    <li>选择 <strong>微信</strong> 后点击绑定，App 会生成二维码。</li>
                    <li>使用微信扫一扫该二维码，关注 Amazfit 华米公众号，完成授权绑定。</li>
                    <li>微信同步不只看第三方授权，还受设备绑定状态影响；Zepp 已更新但微信不变时，请检查账号是否绑定了有效设备。</li>
                    <li>绑定成功后微信运动可同步 Zepp Life 步数；不要取关公众号，否则可能解绑。</li>
                  </ol>
                </section>
                <section class="tutorial-step">
                  <h3>三、网页刷步数操作</h3>
                  <ol>
                    <li>手机浏览器打开本站地址：<span class="tutorial-url">openmemory.cloud:18080</span></li>
                    <li>账号填写注册的 Zepp Life 邮箱 / 手机号。</li>
                    <li>密码填写 Zepp Life 登录密码。</li>
                    <li>步数建议填写 <strong>8000-25000</strong>，过高容易异常。</li>
                    <li>点击提交修改，提示成功后等待 1-3 分钟同步。</li>
                  </ol>
                </section>
                <section class="tutorial-step">
                  <h3>四、查看结果</h3>
                  <ol>
                    <li>打开 Zepp Life，进入运动页面，确认今日步数已更新。</li>
                    <li>打开微信，进入微信运动，下拉刷新，确认步数同步到排行榜。</li>
                  </ol>
                </section>
                <section class="tutorial-step">
                  <h3>五、常见问题</h3>
                  <ul>
                    <li><strong>提交失败：</strong>核对账号密码，确认不是第三方快捷登录账号，也可以换浏览器重试。</li>
                    <li><strong>微信不同步：</strong>先确认 Zepp / Zepp Life 是否已绑定有效手环或手表设备，再重新绑定微信或在微信运动设置中重新授权；没有设备请加入下方 QQ 交流群联系群主。</li>
                    <li><strong>异常提示：</strong>降低步数数值，建议不超过 25000，并避免频繁提交。</li>
                  </ul>
                </section>
              </div>
              <div class="app-guide">
                <div class="app-guide-title">可用 App：两个都可以用，推荐使用蓝色 Zepp。</div>
                <div class="app-choice-grid">
                  <div class="app-choice-card">
                    <div class="app-choice-main">
                      <img class="app-logo" src="/assets/tutorial/zepp-life-logo.jpg" alt="Zepp Life app logo" />
                      <div>
                        <div class="app-name">Zepp Life</div>
                        <div class="app-desc">橙色图标，可用于账号登录和微信运动绑定。</div>
                      </div>
                    </div>
                    <a class="app-original" href="/assets/tutorial/zepp-life-original.jpg" target="_blank" rel="noreferrer">
                      <img src="/assets/tutorial/zepp-life-original.jpg" alt="Zepp Life 应用商店原图" />
                    </a>
                  </div>
                  <div class="app-choice-card recommended">
                    <div class="app-choice-main">
                      <img class="app-logo" src="/assets/tutorial/zepp-logo.jpg" alt="Zepp app logo" />
                      <div>
                        <div class="app-name">Zepp</div>
                        <div class="app-badge">推荐</div>
                        <div class="app-desc">蓝色图标，官方 Zepp，优先推荐下载安装到这个 App。</div>
                      </div>
                    </div>
                    <a class="app-original" href="/assets/tutorial/zepp-original.jpg" target="_blank" rel="noreferrer">
                      <img src="/assets/tutorial/zepp-original.jpg" alt="Zepp 应用商店原图" />
                    </a>
                  </div>
                </div>
              </div>
            </details>
            <form id="stepForm" novalidate>
              <div class="form-row">
                <label>账号（手机号或邮箱）</label>
                <input name="user" type="text" required autocomplete="username" placeholder="Zepp Life 账号" />
              </div>
              <div class="form-row">
                <label>密码</label>
                <input name="pwd" type="password" required autocomplete="current-password" placeholder="Zepp Life 密码" />
              </div>
              <div class="form-row">
                <label>步数</label>
                <input name="step" type="number" required min="1" max="98800" step="1" placeholder="20000" />
                <div class="field-hint">优先用 8000-25000 测试；最高限制 98800；只能增加步数，不能把已同步的步数降下来。</div>
              </div>
              <div class="form-row">
                <label>今日验证码</label>
                <input name="verification_token" id="verificationToken" type="text" required inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" placeholder="6 位数字验证码" />
                <div class="field-hint">验证码每日更新，请加入下方 QQ 交流群获取；预览模拟数据不需要验证码。</div>
              </div>
              <label class="inline-option">
                <input id="debugMode" type="checkbox" checked />
                显示详细报错
              </label>
              <div class="actions">
                <button class="primary" type="submit" id="submitBtn">提交修改</button>
                <button class="ghost" type="button" id="previewPayload">预览模拟数据</button>
                <button class="ghost" type="button" id="fillExample">随机步数</button>
              </div>
              <div class="preview-note">“预览模拟数据”只生成 payload，不登录 Zepp，不会修改步数；填写账号后会按账号绑定同一组模拟设备 ID。</div>
            </form>
            </section>
            <section class="method-panel" data-flow-panel="qr">
              <div class="flow-summary"><strong>适用情况：</strong>账号没有可用设备，或 Zepp 已接收但微信运动一直不同步。扫码流程用于处理设备绑定数据源，二维码不会直接写在页面 HTML 里。</div>
              <div class="qr-workspace">
                <div class="qr-tutorial">
                  <div class="qr-tutorial-title">无设备扫码同步流程</div>
                  <ol>
                    <li>先点击 <strong>显示二维码</strong> 获取临时二维码，用微信扫码进入服务号页面。</li>
                    <li>按微信页面提示关注服务号；关注完成后回到本站，确认下方展示的系统二维码当前步数。</li>
                    <li>点击 <strong>我已关注，开始同步</strong>。后台会使用系统默认共享账号提交“当前系统步数 + 1”，刷新一次新的步数数据，让微信运动重新拉取同步状态。</li>
                    <li>提交完成后查看微信运动是否同步成功；如果已经同步成功，<strong class="danger-text">必须取关刚才关注的华米服务号</strong>，否则后续可能无法继续修改或同步步数，再刷新微信运动确认排行榜步数。</li>
                  </ol>
                </div>
                <div class="share-meta" id="deviceShareMeta">正在检查系统默认共享账号...</div>
                <div class="qr-frame" id="qrFrame">
                  <div class="qr-shield" id="qrShield">点击下方按钮后临时加载二维码；二维码图片不走静态资源直链，短时间后自动失效。</div>
                  <img class="device-qr" id="deviceQrImage" alt="扫码绑定设备二维码" hidden draggable="false" />
                </div>
                <div class="qr-actions">
                  <button class="primary" type="button" id="loadDeviceQr">显示二维码</button>
                  <button class="ghost" type="button" id="refreshDeviceQr">刷新二维码</button>
                </div>
                <div class="qr-confirm-panel" id="qrConfirmPanel" hidden>
                  <label class="qr-confirm-check">
                    <input id="qrUnbindConfirm" type="checkbox" />
                    <span>我已确认：扫码完成并同步成功后，一定会取消绑定/取关华米服务号；否则后续可能无法继续修改或同步步数。</span>
                  </label>
                  <button class="primary" type="button" id="confirmLoadDeviceQr" disabled>同意并显示二维码</button>
                </div>
                <div class="qr-status" id="deviceQrStatus">正在检查二维码配置...</div>
                <ul class="qr-rules">
                  <li>请在微信内打开或用微信扫码，根据页面提示完成绑定。</li>
                  <li>当日步数只能增加，不能降低；微信运动通常取更高的数据源。</li>
                  <li>二维码会短期有效，失效后点击刷新二维码重新加载。</li>
                </ul>
                <div class="shared-step-card">
                  <div class="shared-step-head">
                    <span>当前系统二维码步数</span>
                    <strong id="sharedCurrentStep">加载中</strong>
                  </div>
                  <div class="shared-step-hint" id="sharedNextStepHint">正在读取后台共享账号步数状态...</div>
                </div>
                <form id="sharedStepForm">
                  <div class="form-row">
                    <label>今日验证码</label>
                    <input name="verification_token" id="sharedVerificationToken" type="text" required inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" placeholder="6 位数字验证码" />
                    <div class="field-hint">扫码同步也需要当天验证码，请加入下方 QQ 交流群获取；验证码错误不会提交系统共享账号。</div>
                  </div>
                  <div class="actions">
                    <button class="primary" type="submit" id="sharedStepSubmit">我已关注，开始同步</button>
                  </div>
                  <div class="share-status" id="sharedStepStatus"></div>
                </form>
              </div>
            </section>
          </div>

          <div class="panel">
            <h2>响应结果</h2>
            <p>这里显示本地接口返回的 JSON；Zepp 接收成功不等于微信运动已同步，需再刷新 Zepp / 微信运动确认。若 Zepp 已变但微信不变，优先检查账号是否绑定有效设备。</p>
            <div id="resultStatus" class="result-status status-idle">等待提交</div>
            <div id="resultTip" class="tip-box"></div>
            <pre id="result" class="result-box">尚未提交</pre>
          </div>
        </section>

        <section class="workspace baidu-share-workspace tool-workspace" id="baiduSharePanel" data-tool-workspace="baidu-share">
          <div class="panel">
            <h2>百度网盘中转下载</h2>
            <p>粘贴单个百度网盘分享文本，系统自动解析链接和提取码，后台会用已登录的 BaiduPCS-Go 账号转存并下载到服务器。</p>
            <div class="tool-notice">百度网盘登录态由管理员在 /admin 后台维护；用户提交任务需要填写当天验证码。服务器下载文件当前保留 __BAIDU_SHARE_RETENTION_LABEL__，过期会自动清理。</div>
            <form class="baidu-share-form" id="baiduShareForm">
              <div class="form-row">
                <label>分享文本</label>
                <textarea name="raw_text" id="baiduRawText" required maxlength="5000" placeholder="直接粘贴百度网盘分享内容，例如：链接：https://pan.baidu.com/s/1xxxx 提取码：abcd"></textarea>
                <div class="field-hint">当前只支持一个百度网盘链接；可从整段文字里自动提取 pan.baidu.com 链接和 4 位提取码，多个链接会要求重新提交。</div>
              </div>
              <div class="form-row">
                <label>网盘保存根目录</label>
                <input name="netdisk_save_root" id="baiduNetdiskRoot" type="text" placeholder="/apps/zepp-api-baidu-tmp" />
                <div class="field-hint">实际任务会在这个专用 tmp 根目录下按 job_id 建独立目录，避免污染你的其他网盘文件。</div>
              </div>
              <div class="form-row">
                <label>服务器下载根目录</label>
                <input name="server_download_root" id="baiduServerRoot" type="text" placeholder="服务器本地下载目录" />
                <div class="field-hint">worker 会把网盘文件下载到该根目录下的 job_id 子目录，再由网页打包成 ZIP 下载。</div>
              </div>
              <div class="form-row">
                <label>今日验证码</label>
                <input name="verification_token" id="baiduVerificationToken" type="text" required inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" placeholder="6 位数字验证码" />
                <div class="field-hint">百度网盘中转下载也使用管理后台的当天验证码；验证码错误不会提交任务。</div>
              </div>
              <div class="actions">
                <button class="primary" type="submit" id="baiduSubmit">提交任务</button>
                <button class="ghost" type="button" id="baiduParseBtn">重新解析</button>
                <button class="ghost" type="button" id="refreshBaiduJobs">刷新记录</button>
              </div>
              <div class="share-status" id="baiduShareStatus">等待粘贴分享文本。</div>
            </form>
          </div>

          <div class="panel">
            <h2>解析结果</h2>
            <p>这里只展示解析预览和最近任务记录；完整分享链接与提取码只保存在服务器 SQLite 里供 worker 使用。</p>
            <div class="share-meta" id="baiduParsePreview">尚未解析。</div>
            <div class="path-preview" id="baiduPathPreview"></div>
            <div class="job-list-title">提交记录（脱敏）</div>
            <div class="job-list" id="baiduJobsList">
              <div class="empty-card">暂无百度网盘任务。</div>
            </div>
          </div>
        </section>

        <section class="workspace qq-like-workspace tool-workspace" id="qqLikePanel" data-tool-workspace="qq-like">
          <div class="qq-like-shell">
            <section class="panel">
              <div class="qq-like-titlebar">
                <div>
                  <h2>QQ 互赞</h2>
                  <p>贡献一个可用 QQ 登录态，即可获得每日 1 次互赞资格；每次固定执行 10 次点赞。</p>
                </div>
                <button class="qq-like-link" type="button" id="qqLikeReload">刷新状态</button>
              </div>

              <div id="qqLikeGuest">
                <div class="tool-notice">只有贡献 QQ 登录态的普通用户才能发起互赞。系统只调用 OneBot 点赞接口，不读取聊天记录、联系人或群消息。</div>
                <div class="actions">
                  <button class="primary" type="button" id="qqLikeStartLogin">贡献 QQ 并获取资格</button>
                  <button class="ghost" type="button" id="qqLikeShowRecovery">已有贡献账号，找回管理权</button>
                </div>
              </div>

              <div id="qqLikeLogin" hidden>
                <div class="qq-like-login-grid">
                  <img class="qq-like-qr" id="qqLikeQrImage" alt="QQ 登录二维码" />
                  <div class="qq-like-login-copy">
                    <strong>使用手机 QQ 扫码登录</strong>
                    <p id="qqLikeLoginStatus">二维码准备中，请稍候。</p>
                    <div class="actions">
                      <button class="ghost" type="button" id="qqLikeRefreshQr">刷新二维码</button>
                      <button class="ghost" type="button" id="qqLikeCancelLocal">稍后再扫</button>
                    </div>
                  </div>
                </div>
              </div>

              <div class="qq-like-recovery" id="qqLikeRecoveryNotice" hidden>
                <strong>请立即保存恢复码</strong>
                <code id="qqLikeRecoveryCode"></code>
                恢复码只在首次创建时展示一次。它可以在更换浏览器后找回账号管理权，请勿发给他人。
              </div>

              <div class="qq-like-account" id="qqLikeAccount" hidden>
                <div class="qq-like-account-head">
                  <div>
                    <div class="qq-like-account-state" id="qqLikeAccountState">贡献账号在线</div>
                    <div class="qq-like-account-number">
                      <strong id="qqLikeAccountNumber">未登录</strong>
                      <span id="qqLikeAccountHealth">等待状态同步</span>
                    </div>
                  </div>
                  <button class="qq-like-link" type="button" id="qqLikeManageToggle">管理账号</button>
                </div>
                <div class="qq-like-quota-grid">
                  <div class="qq-like-quota">
                    <span>今日名额</span>
                    <strong id="qqLikeDailyTotal">1 个</strong>
                  </div>
                  <div class="qq-like-quota">
                    <span>剩余名额</span>
                    <strong id="qqLikeDailyRemaining">0 个</strong>
                  </div>
                  <div class="qq-like-quota">
                    <span>排队任务</span>
                    <strong id="qqLikeQueuedRequests">0 个</strong>
                  </div>
                </div>
              </div>

              <details class="qq-like-manage" id="qqLikeManage">
                <summary>账号管理与凭证恢复</summary>
                <div class="qq-like-manage-body">
                  <form id="qqLikeRecoveryForm">
                    <div class="form-row">
                      <label>贡献账号 ID</label>
                      <input id="qqLikeContributorId" name="contributor_id" type="text" maxlength="40" autocomplete="off" placeholder="qlc_..." />
                    </div>
                    <div class="form-row">
                      <label>恢复码</label>
                      <input id="qqLikeRecoveryInput" name="recovery_code" type="text" maxlength="20" autocomplete="off" placeholder="XXXX-XXXX-XXXX" />
                    </div>
                    <div class="actions">
                      <button class="primary" type="submit" id="qqLikeRecover">找回管理权</button>
                      <button class="ghost" type="button" id="qqLikeForgetLocal">仅清除本机凭证</button>
                      <button class="ghost" type="button" id="qqLikeRevoke">停止贡献并删除登录信息</button>
                    </div>
                  </form>
                </div>
              </details>
              <div class="share-status" id="qqLikeGlobalStatus">正在检查本机是否保存了贡献凭证。</div>
            </section>

            <section class="panel">
              <h2>发起今日互赞</h2>
              <p>系统会自动选择另一台当天仍有额度的贡献账号执行，同一来源账号每天只执行一次互赞任务。</p>
              <form id="qqLikeRequestForm">
                <div class="qq-like-form-grid">
                  <div class="form-row">
                    <label>目标 QQ 号</label>
                    <input id="qqLikeTarget" name="target_qq" type="text" inputmode="numeric" pattern="[1-9][0-9]{4,11}" maxlength="12" autocomplete="off" placeholder="请输入要点赞的 QQ 号" />
                  </div>
                  <button class="primary" type="submit" id="qqLikeSubmit" disabled>加入互赞队列</button>
                </div>
                <div class="field-hint">固定执行 10 次点赞；任务结果不明确时不会自动重复发送，避免对同一目标重复点赞。</div>
              </form>
            </section>

            <section class="panel">
              <h2>当前任务状态</h2>
              <p id="qqLikeTaskSummary">尚未提交今日互赞任务。</p>
              <div class="qq-like-task" id="qqLikeTaskSteps">
                <div class="qq-like-task-step" data-qq-like-step="submitted">
                  <div class="qq-like-task-node">1</div>
                  <strong>已提交</strong>
                  <span>—</span>
                </div>
                <div class="qq-like-task-step" data-qq-like-step="assigned">
                  <div class="qq-like-task-node">2</div>
                  <strong>等待可用账号</strong>
                  <span>—</span>
                </div>
                <div class="qq-like-task-step" data-qq-like-step="running">
                  <div class="qq-like-task-node">3</div>
                  <strong>执行点赞</strong>
                  <span>—</span>
                </div>
                <div class="qq-like-task-step" data-qq-like-step="finished">
                  <div class="qq-like-task-node">4</div>
                  <strong>完成</strong>
                  <span>—</span>
                </div>
              </div>
            </section>

            <section class="panel">
              <div class="qq-like-record-head">
                <div>
                  <h2>今日记录</h2>
                  <p>只展示当前贡献账号发起的任务，来源 QQ 会脱敏。</p>
                </div>
              </div>
              <div class="qq-like-table-wrap">
                <table class="qq-like-table">
                  <thead>
                    <tr>
                      <th>目标 QQ</th>
                      <th>来源账号</th>
                      <th>次数</th>
                      <th>状态</th>
                      <th>时间</th>
                    </tr>
                  </thead>
                  <tbody id="qqLikeRecords">
                    <tr><td colspan="5">暂无互赞记录</td></tr>
                  </tbody>
                </table>
              </div>
            </section>

            <div class="qq-like-safety">
              <strong>安全说明</strong>
              <span>扫码贡献即表示允许系统仅使用该登录态执行 QQ 点赞。服务不会接入消息事件，也不会读取聊天、联系人或群数据；可随时在账号管理中停止贡献并删除本工具保存的登录信息。</span>
            </div>
          </div>
        </section>

        <section class="workspace tool-workspace" id="reservedToolPanel" data-tool-workspace="reserved">
          <div class="panel">
            <h2 id="reservedToolTitle">工具详情</h2>
            <p id="reservedToolDesc">这个工具位已预留，后续可以接入后端接口或纯前端逻辑。</p>
            <div class="tool-notice" id="reservedToolNotice">当前工具暂未开放自助提交。</div>
            <div class="actions">
              <button class="ghost" type="button" id="reservedCopyGroupNumber">复制 QQ 群号</button>
            </div>
          </div>

          <div class="panel">
            <h2>当前状态</h2>
            <p>这里只显示当前选中的工具说明，不再混放其他工具表单。</p>
            <div id="reservedToolStatus" class="result-status status-idle">待接入</div>
            <pre id="reservedToolResult" class="result-box">请选择工具查看详情。</pre>
          </div>
        </section>

        <section class="support-grid">
          <div class="panel">
            <h2>留言反馈</h2>
            <p>如果某个工具不成功，可以留下工具名称、现象和联系方式。留言展示会脱敏，数据库保留完整联系方式用于排查。</p>
            <form class="message-form" id="messageForm">
              <div class="form-row">
                <label>相关工具</label>
                <select name="tool_id">
                  <option value="zepp-step">微信步数修改</option>
                  <option value="baidu-share">百度网盘中转下载</option>
                  <option value="qq-like">QQ 互赞</option>
                  <option value="douyin-growth">抖音点赞涨粉</option>
                  <option value="other">其他工具 / 新功能建议</option>
                </select>
              </div>
              <div class="form-row">
                <label>联系方式（选填）</label>
                <input name="contact" type="text" placeholder="QQ / 邮箱 / 手机号，展示时会脱敏" />
              </div>
              <div class="form-row">
                <label>留言内容</label>
                <textarea name="content" required maxlength="500" placeholder="例如：微信步数修改提交成功，但微信运动没有同步；账号是邮箱登录，步数 20000。"></textarea>
              </div>
              <div class="actions">
                <button class="primary" type="submit" id="messageSubmit">提交留言</button>
                <button class="ghost" type="button" id="refreshMessages">刷新留言</button>
              </div>
              <div class="message-status" id="messageStatus"></div>
            </form>
            <div class="message-list" id="messageList">
              <div class="empty-card">暂无留言。</div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="log-head">
            <div>
              <h2>调用记录</h2>
              <p>日志列表只展示脱敏账号；数据库保存完整账号、步数和响应用于排查，不保存密码。</p>
            </div>
            <button class="ghost" type="button" id="refreshLogs">刷新记录</button>
          </div>
          <div class="log-table">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>工具</th>
                  <th>账号</th>
                  <th>步数</th>
                  <th>状态</th>
                  <th>来源</th>
                  <th>消息</th>
                </tr>
              </thead>
              <tbody id="logRows">
                <tr><td colspan="7">暂无记录</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  </div>
  <div class="copy-toast" id="copyToast" role="status" aria-live="polite" aria-atomic="true"></div>

  <script>
    const tools = [
      {
        id: 'zepp-step',
        category: 'life',
        title: '微信步数修改',
        badge: '可用',
        desc: '有设备走 Zepp Life 提交；无设备走扫码绑定处理。',
        active: true,
      },
      {
        id: 'baidu-share',
        category: 'file',
        title: '百度网盘中转下载',
        badge: '队列下载',
        purpose: '分享链接解析、自动转存、服务器 ZIP 下载',
        desc: '粘贴单个百度网盘分享文本，自动提取链接和提取码，完成后打包成一个 ZIP 下载。',
        active: true,
      },
      {
        id: 'qq-like',
        category: 'qq',
        title: 'QQ 互赞',
        badge: '可用',
        purpose: '贡献一个 QQ，获得每日互赞资格',
        desc: '使用独立 QQ 登录态互助点赞；普通用户需先贡献账号，每次固定执行 10 次点赞。',
        active: true,
      },
      {
        id: 'douyin-growth',
        category: 'promotion',
        title: '抖音点赞涨粉',
        badge: '人工对接',
        purpose: '短视频平台点赞推流、涨粉推流咨询',
        desc: '面向抖音等短视频平台，当前暂未接入官方接口；需要推广请加入 QQ 群联系人工处理。',
        active: true,
      },
      {
        id: 'keyrun',
        category: 'dev',
        title: 'KeyRun JetBrains',
        badge: '外链',
        purpose: '一键激活 JetBrains 全家桶',
        desc: '提供 Windows、macOS、Linux 三个平台的激活脚本入口，覆盖常见 JetBrains IDE 产品。',
        icon: 'keyrun',
        url: 'https://liyangxu1.github.io/keyrun/',
      },
      {
        id: 'json-format',
        category: 'dev',
        title: 'JSON 格式化',
        badge: '待接入',
        desc: '预留工具位，后续可接入 JSON 校验、压缩和格式化。',
      },
      {
        id: 'hash',
        category: 'dev',
        title: '文件 Hash',
        badge: '待接入',
        desc: '预留工具位，后续可做 MD5、SHA256、本地文件摘要。',
      },
      {
        id: 'text-extract',
        category: 'text',
        title: '文本提取',
        badge: '待接入',
        desc: '预留工具位，后续可提取邮箱、手机号、链接等内容。',
      },
      {
        id: 'image-compress',
        category: 'image',
        title: '图片压缩',
        badge: '待接入',
        desc: '预留工具位，后续可接入图片压缩、格式转换。',
      },
      {
        id: 'pdf-tools',
        category: 'office',
        title: 'PDF 工具',
        badge: '待接入',
        desc: '预留工具位，后续可做合并、拆分、转图片等功能。',
      },
    ]

    const grid = document.getElementById('toolsGrid')
    const form = document.getElementById('stepForm')
    const result = document.getElementById('result')
    const resultStatus = document.getElementById('resultStatus')
    const resultTip = document.getElementById('resultTip')
    const searchInput = document.getElementById('searchInput')
    const submitBtn = document.getElementById('submitBtn')
    const previewPayload = document.getElementById('previewPayload')
    const fillExample = document.getElementById('fillExample')
    const refreshLogs = document.getElementById('refreshLogs')
    const logRows = document.getElementById('logRows')
    const messageForm = document.getElementById('messageForm')
    const messageSubmit = document.getElementById('messageSubmit')
    const messageStatus = document.getElementById('messageStatus')
    const messageList = document.getElementById('messageList')
    const refreshMessages = document.getElementById('refreshMessages')
    const copyGroupNumber = document.getElementById('copyGroupNumber')
    const copyToast = document.getElementById('copyToast')
    const flowTabs = document.querySelectorAll('[data-flow]')
    const flowPanels = document.querySelectorAll('[data-flow-panel]')
    const verificationToken = document.getElementById('verificationToken')
    const sharedVerificationToken = document.getElementById('sharedVerificationToken')
    const baiduVerificationToken = document.getElementById('baiduVerificationToken')
    const loadDeviceQr = document.getElementById('loadDeviceQr')
    const refreshDeviceQr = document.getElementById('refreshDeviceQr')
    const deviceQrImage = document.getElementById('deviceQrImage')
    const deviceQrStatus = document.getElementById('deviceQrStatus')
    const qrShield = document.getElementById('qrShield')
    const qrConfirmPanel = document.getElementById('qrConfirmPanel')
    const qrUnbindConfirm = document.getElementById('qrUnbindConfirm')
    const confirmLoadDeviceQr = document.getElementById('confirmLoadDeviceQr')
    const deviceShareMeta = document.getElementById('deviceShareMeta')
    const sharedStepForm = document.getElementById('sharedStepForm')
    const sharedStepSubmit = document.getElementById('sharedStepSubmit')
    const sharedStepStatus = document.getElementById('sharedStepStatus')
    const sharedCurrentStep = document.getElementById('sharedCurrentStep')
    const sharedNextStepHint = document.getElementById('sharedNextStepHint')
    const baiduSharePanel = document.getElementById('baiduSharePanel')
    const baiduShareForm = document.getElementById('baiduShareForm')
    const baiduRawText = document.getElementById('baiduRawText')
    const baiduNetdiskRoot = document.getElementById('baiduNetdiskRoot')
    const baiduServerRoot = document.getElementById('baiduServerRoot')
    const baiduSubmit = document.getElementById('baiduSubmit')
    const baiduParseBtn = document.getElementById('baiduParseBtn')
    const refreshBaiduJobs = document.getElementById('refreshBaiduJobs')
    const baiduShareStatus = document.getElementById('baiduShareStatus')
    const baiduParsePreview = document.getElementById('baiduParsePreview')
    const baiduPathPreview = document.getElementById('baiduPathPreview')
    const baiduJobsList = document.getElementById('baiduJobsList')
    const qqLikePanel = document.getElementById('qqLikePanel')
    const qqLikeGuest = document.getElementById('qqLikeGuest')
    const qqLikeLogin = document.getElementById('qqLikeLogin')
    const qqLikeAccount = document.getElementById('qqLikeAccount')
    const qqLikeStartLogin = document.getElementById('qqLikeStartLogin')
    const qqLikeShowRecovery = document.getElementById('qqLikeShowRecovery')
    const qqLikeReload = document.getElementById('qqLikeReload')
    const qqLikeQrImage = document.getElementById('qqLikeQrImage')
    const qqLikeRefreshQr = document.getElementById('qqLikeRefreshQr')
    const qqLikeCancelLocal = document.getElementById('qqLikeCancelLocal')
    const qqLikeLoginStatus = document.getElementById('qqLikeLoginStatus')
    const qqLikeRecoveryNotice = document.getElementById('qqLikeRecoveryNotice')
    const qqLikeRecoveryCode = document.getElementById('qqLikeRecoveryCode')
    const qqLikeAccountState = document.getElementById('qqLikeAccountState')
    const qqLikeAccountNumber = document.getElementById('qqLikeAccountNumber')
    const qqLikeAccountHealth = document.getElementById('qqLikeAccountHealth')
    const qqLikeDailyTotal = document.getElementById('qqLikeDailyTotal')
    const qqLikeDailyRemaining = document.getElementById('qqLikeDailyRemaining')
    const qqLikeQueuedRequests = document.getElementById('qqLikeQueuedRequests')
    const qqLikeManageToggle = document.getElementById('qqLikeManageToggle')
    const qqLikeManage = document.getElementById('qqLikeManage')
    const qqLikeRecoveryForm = document.getElementById('qqLikeRecoveryForm')
    const qqLikeContributorId = document.getElementById('qqLikeContributorId')
    const qqLikeRecoveryInput = document.getElementById('qqLikeRecoveryInput')
    const qqLikeRecover = document.getElementById('qqLikeRecover')
    const qqLikeForgetLocal = document.getElementById('qqLikeForgetLocal')
    const qqLikeRevoke = document.getElementById('qqLikeRevoke')
    const qqLikeGlobalStatus = document.getElementById('qqLikeGlobalStatus')
    const qqLikeRequestForm = document.getElementById('qqLikeRequestForm')
    const qqLikeTarget = document.getElementById('qqLikeTarget')
    const qqLikeSubmit = document.getElementById('qqLikeSubmit')
    const qqLikeTaskSummary = document.getElementById('qqLikeTaskSummary')
    const qqLikeTaskSteps = document.querySelectorAll('[data-qq-like-step]')
    const qqLikeRecords = document.getElementById('qqLikeRecords')
    const toolWorkspaces = document.querySelectorAll('[data-tool-workspace]')
    const zeppStepPanel = document.getElementById('zeppStepPanel')
    const reservedToolPanel = document.getElementById('reservedToolPanel')
    const reservedToolTitle = document.getElementById('reservedToolTitle')
    const reservedToolDesc = document.getElementById('reservedToolDesc')
    const reservedToolNotice = document.getElementById('reservedToolNotice')
    const reservedToolStatus = document.getElementById('reservedToolStatus')
    const reservedToolResult = document.getElementById('reservedToolResult')
    const reservedCopyGroupNumber = document.getElementById('reservedCopyGroupNumber')
    const stepMax = 98800
    const toolApiKey = __ZEPP_TOOL_API_KEY__
    const qqGroupName = __QQ_GROUP_NAME_JSON__
    const qqGroupNumber = __QQ_GROUP_NUMBER_JSON__
    const baiduNetdiskDefaultRoot = __BAIDU_NETDISK_DEFAULT_SAVE_ROOT_JSON__
    const baiduServerDefaultRoot = __BAIDU_SERVER_DOWNLOAD_DEFAULT_ROOT_JSON__
    const sharedSelfBlockedAccounts = new Set(['3313696759@proton.me'])
    let currentCategory = 'all'
    let currentToolId = 'zepp-step'
    let qrConfigured = false
    let qrPaused = false
    let qrUnavailableMessage = ''
    let sharedDeviceCount = 0
    let sharedCurrentStepValue = null
    let sharedCanSync = false
    let qrConfirmTimer = null
    let baiduParseTimer = null
    let qqLikeAccessToken = ''
    let qqLikeLoginTimer = null
    let qqLikeDashboardTimer = null
    let qqLikeQrObjectUrl = ''
    let qqLikeDashboardData = null

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;')
    }

    let copyToastTimer = null
    function showCopyToast(message, state = 'success') {
      window.clearTimeout(copyToastTimer)
      copyToast.textContent = message
      copyToast.className = `copy-toast show ${state === 'failed' ? 'failed' : ''}`.trim()
      copyToastTimer = window.setTimeout(() => {
        copyToast.className = 'copy-toast'
      }, 2200)
    }

    async function copyTextToClipboard(text) {
      if (navigator.clipboard && window.isSecureContext) {
        try {
          await navigator.clipboard.writeText(text)
          return
        } catch {}
      }
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.left = '0'
      textarea.style.top = '0'
      textarea.style.width = '1px'
      textarea.style.height = '1px'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      textarea.setSelectionRange(0, textarea.value.length)
      const ok = document.execCommand('copy')
      textarea.remove()
      if (!ok) throw new Error('copy failed')
    }

    function normalizeLoginAccount(value) {
      const raw = String(value || '').trim()
      if (raw.includes('@')) return raw.toLowerCase()
      const lower = raw.toLowerCase()
      const domains = ['qq.com', '163.com', '126.com', 'gmail.com', 'proton.me', 'outlook.com', 'hotmail.com']
      for (const domain of domains) {
        if (lower.endsWith(domain) && raw.length > domain.length) {
          return `${raw.slice(0, -domain.length)}@${domain}`.toLowerCase()
        }
      }
      return raw
    }

    function isSharedSelfBlockedAccount(value) {
      return sharedSelfBlockedAccounts.has(normalizeLoginAccount(value))
    }

    function isValidVerificationToken(value) {
      return /^[0-9]{6}$/.test(String(value || '').trim())
    }

    function syncVerificationToken(source, ...targets) {
      const value = source.value.replace(/[^0-9]/g, '').slice(0, 6)
      source.value = value
      targets.forEach((target) => {
        if (target && target !== source) target.value = value
      })
    }

    verificationToken.addEventListener('input', () => syncVerificationToken(verificationToken, sharedVerificationToken, baiduVerificationToken))
    sharedVerificationToken.addEventListener('input', () => syncVerificationToken(sharedVerificationToken, verificationToken, baiduVerificationToken))
    baiduVerificationToken.addEventListener('input', () => syncVerificationToken(baiduVerificationToken, verificationToken, sharedVerificationToken))

    function showVerificationTokenError(target = result) {
      resultStatus.className = 'result-status status-failed'
      resultStatus.textContent = '验证码错误'
      resultTip.className = 'tip-box show failed'
      resultTip.innerHTML = '<strong>今日验证码不正确</strong><span>请加入页面下方 QQ 交流群获取当天 6 位数字验证码后再提交。</span>'
      target.textContent = '请填写当天 6 位数字验证码；验证码请加入下方 QQ 交流群获取。'
    }

    function showSharedSelfBlockedResult() {
      const payload = {
        status: 'failed',
        error: '该账号为共享账号，不支持自定义步数',
        user_tip: '该账号为共享账号，不支持自定义步数',
        action_tip: '请切换到“没有设备，扫码绑定”流程使用系统共享二维码同步。',
        blocked_shared_account: true,
      }
      resultStatus.className = 'result-status status-failed'
      resultStatus.textContent = '提交失败'
      result.textContent = JSON.stringify(payload, null, 2)
      showResultTip(payload)
      return payload
    }

    function showResultTip(json) {
      resultTip.className = 'tip-box'
      resultTip.innerHTML = ''
      if (!json || !json.status) return

      if (json.status === 'success') {
        resultTip.className = 'tip-box show success'
        resultTip.innerHTML = '<strong>Zepp 接收成功</strong><span>这代表 Zepp 接口已接收数据；微信运动是否同步还要看绑定状态、步数是否递增和同步延迟。</span>'
        return
      }

      const title = json.user_tip || '提交失败'
      const action = json.action_tip || '请检查账号、密码和 Zepp Life 绑定状态后再试。'
      resultTip.className = 'tip-box show failed'
      resultTip.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(action)}</span>`
    }

    function setActiveTool(toolId, scroll = true) {
      if (toolId !== 'qq-like') {
        stopQQLikePolling()
      }
      const workspaceId = ['zepp-step', 'baidu-share', 'qq-like'].includes(toolId) ? toolId : 'reserved'
      currentToolId = toolId
      let activePanel = null
      toolWorkspaces.forEach((panel) => {
        const active = panel.getAttribute('data-tool-workspace') === workspaceId
        panel.classList.toggle('active', active)
        if (active) activePanel = panel
      })
      document.querySelectorAll('[data-tool-shortcut]').forEach((button) => {
        button.classList.toggle('active', button.getAttribute('data-tool-shortcut') === toolId)
      })
      document.querySelectorAll('[data-tool]').forEach((card) => {
        card.classList.toggle('selected', card.getAttribute('data-tool') === toolId)
      })
      if (scroll && activePanel) {
        activePanel.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    }

    function showZeppStepPanel() {
      setActiveTool('zepp-step')
    }

    function showReservedToolPanel(tool, options = {}) {
      const selected = tool || { id: 'reserved', title: '预留工具', desc: '这个工具位已预留，后续可以接入后端接口或纯前端逻辑。' }
      reservedToolTitle.textContent = selected.title
      reservedToolDesc.textContent = selected.desc || '这个工具位已预留。'
      reservedToolNotice.textContent = options.notice || '当前工具暂未开放自助提交，如需使用或接入请加入 QQ 群反馈。'
      reservedToolStatus.className = `result-status ${options.statusClass || 'status-idle'}`.trim()
      reservedToolStatus.textContent = options.status || (selected.badge || '待接入')
      reservedToolResult.textContent = (options.lines || [
        '工具状态',
        `- 当前工具：${selected.title}`,
        `- 当前状态：${selected.badge || '待接入'}`,
        '- 说明：该工具位只展示当前选中工具的说明，不显示其他工具表单',
      ]).join('\\n')
      setActiveTool(selected.id || 'reserved')
    }

    function showDouyinGrowthIntro() {
      const tool = tools.find((item) => item.id === 'douyin-growth')
      showReservedToolPanel(tool, {
        status: '人工对接',
        statusClass: 'status-idle',
        notice: `当前未接入抖音官方接口，暂不支持页面自助下单；如需点赞推流、涨粉推流或短视频推广方案，请加入 ${qqGroupName} QQ 群联系。`,
        lines: [
          '服务说明',
          '- 适用平台：抖音等短视频平台',
          '- 支持方向：点赞推流、涨粉推流、短视频推广方案咨询',
          '- 当前状态：官方接口待接入，暂不支持页面自助下单或自动执行推广',
          `- 联系方式：加入 QQ 群 ${qqGroupNumber}，说明账号、作品链接和推广目标后人工沟通`,
        ],
      })
    }

    function showBaiduSharePanel() {
      setActiveTool('baidu-share')
      loadBaiduJobs()
    }

    function showQQLikePanel() {
      setActiveTool('qq-like')
      loadQQLikeDashboard()
    }

    const qqLikeAccessTokenKey = 'qqLikeAccessToken:v1'
    const qqLikeContributorIdKey = 'qqLikeContributorId:v1'

    function readQQLikeLocalValue(key) {
      try {
        return window.localStorage.getItem(key) || ''
      } catch {
        return ''
      }
    }

    function writeQQLikeLocalValue(key, value) {
      try {
        if (value) {
          window.localStorage.setItem(key, value)
        } else {
          window.localStorage.removeItem(key)
        }
      } catch {}
    }

    function setQQLikeAccess(accessToken, contributorId = '') {
      qqLikeAccessToken = String(accessToken || '').trim()
      writeQQLikeLocalValue(qqLikeAccessTokenKey, qqLikeAccessToken)
      if (contributorId) {
        writeQQLikeLocalValue(qqLikeContributorIdKey, contributorId)
        qqLikeContributorId.value = contributorId
      }
    }

    function clearQQLikeQrImage() {
      if (qqLikeQrObjectUrl) {
        URL.revokeObjectURL(qqLikeQrObjectUrl)
        qqLikeQrObjectUrl = ''
      }
      qqLikeQrImage.removeAttribute('src')
    }

    function stopQQLikePolling() {
      if (qqLikeLoginTimer) {
        window.clearTimeout(qqLikeLoginTimer)
        qqLikeLoginTimer = null
      }
      if (qqLikeDashboardTimer) {
        window.clearTimeout(qqLikeDashboardTimer)
        qqLikeDashboardTimer = null
      }
    }

    function scheduleQQLikeDashboard() {
      if (currentToolId !== 'qq-like' || !qqLikeAccessToken) return
      if (qqLikeDashboardTimer) window.clearTimeout(qqLikeDashboardTimer)
      qqLikeDashboardTimer = window.setTimeout(loadQQLikeDashboard, 8000)
    }

    function qqLikeRequestHeaders(json = false) {
      const headers = {}
      if (json) headers['Content-Type'] = 'application/json'
      if (qqLikeAccessToken) headers['X-QQ-Like-Token'] = qqLikeAccessToken
      return headers
    }

    async function qqLikeJsonRequest(path, options = {}) {
      const requestOptions = {
        cache: 'no-store',
        ...options,
        headers: {
          ...qqLikeRequestHeaders(Boolean(options.json)),
          ...(options.headers || {}),
        },
      }
      delete requestOptions.json
      const response = await fetch(path, requestOptions)
      const data = await response.json().catch(() => ({}))
      if (!response.ok || data.status === 'failed') {
        const error = new Error(data.error || `请求失败（HTTP ${response.status}）`)
        error.status = response.status
        throw error
      }
      return data
    }

    function renderQQLikeGuest(message = '') {
      qqLikeDashboardData = null
      qqLikeGuest.hidden = false
      qqLikeLogin.hidden = true
      qqLikeAccount.hidden = true
      qqLikeSubmit.disabled = true
      renderQQLikeRecords([])
      renderQQLikeTask(null)
      if (message) {
        setShareStatus(qqLikeGlobalStatus, message, 'failed')
      } else {
        setShareStatus(qqLikeGlobalStatus, '尚未绑定贡献账号。')
      }
    }

    function qqlikeStatusClass(status) {
      if (status === 'active') return ''
      if (status === 'pending_login') return 'pending'
      return 'offline'
    }

    function renderQQLikeDashboard(data) {
      const contributor = data?.contributor || {}
      const quota = data?.quota || {}
      const requests = Array.isArray(data?.requests) ? data.requests : []
      qqLikeDashboardData = data
      qqLikeGuest.hidden = true
      qqLikeLogin.hidden = true
      qqLikeAccount.hidden = false
      qqLikeAccountState.className = `qq-like-account-state ${qqlikeStatusClass(contributor.status)}`.trim()
      qqLikeAccountState.textContent = contributor.status_label || '贡献账号状态未知'
      qqLikeAccountNumber.textContent = contributor.qq_masked || '未登录'
      qqLikeAccountHealth.textContent = contributor.last_health_at
        ? `最近检查 ${contributor.last_health_at}`
        : (contributor.last_error || '等待首次状态检查')
      const dailyRemaining = Number(quota.daily_remaining || 0)
      qqLikeDailyTotal.textContent = `${Number(quota.daily_total || 1)} 个`
      qqLikeDailyRemaining.textContent = `${dailyRemaining} 个`
      qqLikeQueuedRequests.textContent = `${Number(quota.queued_requests || 0)} 个`
      qqLikeSubmit.disabled = contributor.status !== 'active' || dailyRemaining < 1
      const contributorId = contributor.contributor_id || ''
      if (contributorId) {
        writeQQLikeLocalValue(qqLikeContributorIdKey, contributorId)
        qqLikeContributorId.value = contributorId
      }
      renderQQLikeRecords(requests)
      renderQQLikeTask(requests[0] || null)
      const message = contributor.status === 'active'
        ? (
          dailyRemaining > 0
            ? '贡献账号可用，可以发起今日互赞。'
            : '今日互赞名额已使用，明日自动恢复。'
        )
        : (contributor.last_error || '贡献账号当前不可用，请重新扫码恢复登录。')
      setShareStatus(
        qqLikeGlobalStatus,
        message,
        contributor.status === 'active' ? 'success' : 'failed',
      )
    }

    function qqlikeRequestLevel(status) {
      if (status === 'succeeded') return 'success'
      if (['failed', 'uncertain', 'canceled'].includes(status)) return 'failed'
      return ''
    }

    function renderQQLikeRecords(requests) {
      qqLikeRecords.innerHTML = requests.length ? requests.map((item) => `
        <tr>
          <td>${escapeHtml(item.target_qq || '-')}</td>
          <td>${escapeHtml(item.source_qq_masked || '等待分配')}</td>
          <td>${Number(item.requested_times || 10)}</td>
          <td><span class="status-pill ${qqlikeRequestLevel(item.status)}">${escapeHtml(item.status_label || item.status || '-')}</span></td>
          <td>${escapeHtml(item.created_at || '-')}</td>
        </tr>
      `).join('') : '<tr><td colspan="5">暂无互赞记录</td></tr>'
    }

    function renderQQLikeTask(request) {
      const status = request?.status || ''
      const stages = ['submitted', 'assigned', 'running', 'finished']
      const progressByStatus = {
        waiting_source: 1,
        assigned: 2,
        running: 3,
        succeeded: 4,
        failed: 4,
        uncertain: 4,
        canceled: 4,
      }
      const progress = progressByStatus[status] || 0
      qqLikeTaskSteps.forEach((step, index) => {
        step.classList.toggle('done', progress > index + 1 || (progress === 4 && status === 'succeeded'))
        step.classList.toggle('active', progress === index + 1 && status !== 'succeeded')
        const detail = step.querySelector('span')
        if (!request) {
          detail.textContent = '—'
          return
        }
        if (stages[index] === 'submitted') detail.textContent = request.created_at || '已记录'
        if (stages[index] === 'assigned') detail.textContent = request.source_qq_masked || '等待其他账号'
        if (stages[index] === 'running') detail.textContent = request.started_at || (status === 'running' ? '执行中' : '—')
        if (stages[index] === 'finished') detail.textContent = request.finished_at || '—'
      })
      if (!request) {
        qqLikeTaskSummary.textContent = '尚未提交今日互赞任务。'
        return
      }
      qqLikeTaskSummary.textContent = `目标 ${request.target_qq}：${request.status_label || request.status}${request.result_message ? `，${request.result_message}` : ''}`
    }

    async function loadQQLikeQr() {
      clearQQLikeQrImage()
      const response = await fetch('/api/tools/qq-like/login/qr', {
        cache: 'no-store',
        headers: qqLikeRequestHeaders(false),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error(data.error || '二维码加载失败')
      }
      const blob = await response.blob()
      qqLikeQrObjectUrl = URL.createObjectURL(blob)
      qqLikeQrImage.src = qqLikeQrObjectUrl
    }

    function scheduleQQLikeLoginPoll() {
      if (qqLikeLoginTimer) window.clearTimeout(qqLikeLoginTimer)
      qqLikeLoginTimer = window.setTimeout(pollQQLikeLogin, 2500)
    }

    async function pollQQLikeLogin() {
      if (!qqLikeAccessToken || qqLikeLogin.hidden) return
      try {
        const data = await qqLikeJsonRequest('/api/tools/qq-like/login/status')
        if (data.login_state === 'active' && data.dashboard) {
          stopQQLikePolling()
          clearQQLikeQrImage()
          renderQQLikeDashboard(data.dashboard)
          scheduleQQLikeDashboard()
          return
        }
        if (data.login_state === 'finalizing') {
          qqLikeLoginStatus.textContent = '扫码已确认，正在校验 QQ 登录状态。'
        } else if (data.login_state === 'waiting_scan') {
          qqLikeLoginStatus.textContent = `等待扫码确认，二维码约 ${Number(data.expires_in_seconds || 0)} 秒后失效。`
        } else {
          qqLikeLoginStatus.textContent = '当前扫码任务已结束，可以重新发起登录。'
          return
        }
        scheduleQQLikeLoginPoll()
      } catch (error) {
        qqLikeLoginStatus.textContent = `状态检查失败：${error.message}`
        scheduleQQLikeLoginPoll()
      }
    }

    async function startQQLikeLogin() {
      qqLikeStartLogin.disabled = true
      setShareStatus(qqLikeGlobalStatus, '正在启动独立 QQ 登录环境。')
      try {
        const data = await qqLikeJsonRequest('/api/tools/qq-like/login/start', {
          method: 'POST',
          json: true,
          body: '{}',
        })
        if (data.access_token) {
          setQQLikeAccess(data.access_token, data.contributor?.contributor_id || '')
        }
        if (data.recovery_code) {
          qqLikeRecoveryCode.textContent = data.recovery_code
          qqLikeRecoveryNotice.hidden = false
        }
        qqLikeGuest.hidden = true
        qqLikeAccount.hidden = true
        qqLikeLogin.hidden = false
        qqLikeLoginStatus.textContent = `等待扫码确认，二维码约 ${Number(data.expires_in_seconds || 0)} 秒后失效。`
        await loadQQLikeQr()
        setShareStatus(qqLikeGlobalStatus, '二维码已生成，请使用手机 QQ 扫码。', 'success')
        scheduleQQLikeLoginPoll()
      } catch (error) {
        setShareStatus(qqLikeGlobalStatus, `启动登录失败：${error.message}`, 'failed')
      } finally {
        qqLikeStartLogin.disabled = false
      }
    }

    async function loadQQLikeDashboard() {
      if (!qqLikeAccessToken) {
        qqLikeAccessToken = readQQLikeLocalValue(qqLikeAccessTokenKey)
      }
      if (!qqLikeContributorId.value) {
        qqLikeContributorId.value = readQQLikeLocalValue(qqLikeContributorIdKey)
      }
      if (!qqLikeAccessToken) {
        renderQQLikeGuest()
        return
      }
      try {
        const data = await qqLikeJsonRequest('/api/tools/qq-like/dashboard')
        renderQQLikeDashboard(data)
        scheduleQQLikeDashboard()
      } catch (error) {
        if (error.status === 401) {
          setQQLikeAccess('')
          renderQQLikeGuest('本机贡献凭证已失效，请使用贡献账号 ID 和恢复码找回管理权。')
          qqLikeManage.open = true
          return
        }
        setShareStatus(qqLikeGlobalStatus, `状态加载失败：${error.message}`, 'failed')
        scheduleQQLikeDashboard()
      }
    }

    qqLikeStartLogin.addEventListener('click', startQQLikeLogin)
    qqLikeReload.addEventListener('click', loadQQLikeDashboard)
    qqLikeShowRecovery.addEventListener('click', () => {
      qqLikeManage.open = true
      qqLikeRecoveryInput.focus()
    })
    qqLikeManageToggle.addEventListener('click', () => {
      qqLikeManage.open = !qqLikeManage.open
    })
    qqLikeRefreshQr.addEventListener('click', async () => {
      qqLikeRefreshQr.disabled = true
      try {
        const data = await qqLikeJsonRequest('/api/tools/qq-like/login/refresh', {
          method: 'POST',
          json: true,
          body: '{}',
        })
        await loadQQLikeQr()
        qqLikeLoginStatus.textContent = `二维码已刷新，约 ${Number(data.expires_in_seconds || 0)} 秒后失效。`
        scheduleQQLikeLoginPoll()
      } catch (error) {
        qqLikeLoginStatus.textContent = `刷新失败：${error.message}`
      } finally {
        qqLikeRefreshQr.disabled = false
      }
    })
    qqLikeCancelLocal.addEventListener('click', () => {
      if (qqLikeLoginTimer) window.clearTimeout(qqLikeLoginTimer)
      qqLikeLoginTimer = null
      clearQQLikeQrImage()
      qqLikeLogin.hidden = true
      qqLikeGuest.hidden = false
      setShareStatus(qqLikeGlobalStatus, '扫码任务仍会在服务器超时后自动关闭，可随时重新打开。')
    })

    qqLikeRecoveryForm.addEventListener('submit', async (event) => {
      event.preventDefault()
      const contributorId = qqLikeContributorId.value.trim()
      const recoveryCode = qqLikeRecoveryInput.value.trim().toUpperCase()
      if (!contributorId || !recoveryCode) {
        setShareStatus(qqLikeGlobalStatus, '请填写贡献账号 ID 和恢复码。', 'failed')
        return
      }
      qqLikeRecover.disabled = true
      try {
        const data = await qqLikeJsonRequest('/api/tools/qq-like/access/recover', {
          method: 'POST',
          json: true,
          body: JSON.stringify({
            contributor_id: contributorId,
            recovery_code: recoveryCode,
          }),
        })
        setQQLikeAccess(data.access_token, data.contributor_id)
        qqLikeRecoveryInput.value = ''
        qqLikeManage.open = false
        await loadQQLikeDashboard()
      } catch (error) {
        setShareStatus(qqLikeGlobalStatus, `找回失败：${error.message}`, 'failed')
      } finally {
        qqLikeRecover.disabled = false
      }
    })

    qqLikeForgetLocal.addEventListener('click', () => {
      stopQQLikePolling()
      clearQQLikeQrImage()
      setQQLikeAccess('')
      renderQQLikeGuest('已清除本机访问凭证，服务器登录态和贡献资格未删除。')
    })

    qqLikeRevoke.addEventListener('click', async () => {
      if (!qqLikeAccessToken) {
        setShareStatus(qqLikeGlobalStatus, '本机没有可用凭证，无法停止该贡献账号。', 'failed')
        return
      }
      if (!window.confirm('停止后将删除本工具保存的 QQ 登录信息，且不能继续使用该贡献资格。确认继续吗？')) return
      qqLikeRevoke.disabled = true
      try {
        const data = await qqLikeJsonRequest('/api/tools/qq-like/revoke', {
          method: 'POST',
          json: true,
          body: '{}',
        })
        stopQQLikePolling()
        clearQQLikeQrImage()
        setQQLikeAccess('')
        writeQQLikeLocalValue(qqLikeContributorIdKey, '')
        qqLikeContributorId.value = ''
        qqLikeManage.open = false
        renderQQLikeGuest(data.message || '已停止贡献并删除登录信息。')
      } catch (error) {
        setShareStatus(qqLikeGlobalStatus, `停止贡献失败：${error.message}`, 'failed')
      } finally {
        qqLikeRevoke.disabled = false
      }
    })

    qqLikeTarget.addEventListener('input', () => {
      qqLikeTarget.value = qqLikeTarget.value.replace(/[^0-9]/g, '').slice(0, 12)
    })

    qqLikeRequestForm.addEventListener('submit', async (event) => {
      event.preventDefault()
      const targetQQ = qqLikeTarget.value.trim()
      if (!/^[1-9][0-9]{4,11}$/.test(targetQQ)) {
        setShareStatus(qqLikeGlobalStatus, '请输入 5-12 位、且不以 0 开头的 QQ 号。', 'failed')
        return
      }
      if (!qqLikeAccessToken || qqLikeSubmit.disabled) {
        setShareStatus(qqLikeGlobalStatus, '当前没有可用互赞资格。', 'failed')
        return
      }
      const requestId = window.crypto?.randomUUID
        ? window.crypto.randomUUID()
        : `web-${Date.now()}-${Math.random().toString(16).slice(2)}`
      qqLikeSubmit.disabled = true
      setShareStatus(qqLikeGlobalStatus, '正在提交互赞任务。')
      try {
        const data = await qqLikeJsonRequest('/api/tools/qq-like/requests', {
          method: 'POST',
          json: true,
          headers: { 'Idempotency-Key': requestId },
          body: JSON.stringify({ target_qq: targetQQ }),
        })
        qqLikeTarget.value = ''
        renderQQLikeTask(data.request)
        setShareStatus(qqLikeGlobalStatus, '互赞任务已进入队列。', 'success')
        await loadQQLikeDashboard()
      } catch (error) {
        setShareStatus(qqLikeGlobalStatus, `提交失败：${error.message}`, 'failed')
        await loadQQLikeDashboard()
      }
    })

    reservedCopyGroupNumber?.addEventListener('click', async () => {
      try {
        await copyTextToClipboard(qqGroupNumber)
        reservedCopyGroupNumber.textContent = '已复制'
        showCopyToast(`QQ群号 ${qqGroupNumber} 已复制`)
        setTimeout(() => { reservedCopyGroupNumber.textContent = '复制 QQ 群号' }, 1500)
      } catch {
        reservedCopyGroupNumber.textContent = qqGroupNumber
        showCopyToast('复制失败，请手动复制群号', 'failed')
      }
    })

    function setBaiduStatus(message, state = '') {
      baiduShareStatus.className = `share-status ${state}`.trim()
      baiduShareStatus.textContent = message
    }

    function renderBaiduParsePreview(data) {
      const parsed = data?.parsed
      if (!parsed) {
        delete baiduParsePreview.dataset.parsed
        baiduParsePreview.textContent = '尚未解析。'
        baiduPathPreview.innerHTML = ''
        return
      }
      baiduParsePreview.dataset.parsed = JSON.stringify(data)
      baiduParsePreview.innerHTML = `
        <strong>已解析到分享信息</strong><br />
        链接：${escapeHtml(parsed.share_url_masked || parsed.share_url)}<br />
        提取码：${escapeHtml(parsed.extraction_code_masked || '****')}
      `
      baiduPathPreview.innerHTML = `
        <code>网盘保存根目录：${escapeHtml(baiduNetdiskRoot.value || baiduNetdiskDefaultRoot)}</code>
        <code>服务器下载根目录：${escapeHtml(baiduServerRoot.value || baiduServerDefaultRoot)}</code>
      `
    }

    async function parseBaiduShareText(showEmpty = false) {
      const rawText = baiduRawText.value.trim()
      if (!rawText) {
        if (showEmpty) setBaiduStatus('请先粘贴百度网盘分享文本。', 'failed')
        renderBaiduParsePreview(null)
        return null
      }
      try {
        const resp = await fetch('/api/tools/baidu-share/parse', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ raw_text: rawText })
        })
        const data = await resp.json()
        if (!resp.ok || data.status !== 'success') {
          renderBaiduParsePreview(null)
          setBaiduStatus(data.error || '没有解析到有效分享信息，请重新提交。', 'failed')
          return null
        }
        renderBaiduParsePreview(data)
        setBaiduStatus('已自动解析到百度网盘链接和提取码，可以提交任务。', 'success')
        return data
      } catch (err) {
        setBaiduStatus(`解析失败：${err}`, 'failed')
        return null
      }
    }

    function scheduleBaiduParse() {
      window.clearTimeout(baiduParseTimer)
      baiduParseTimer = window.setTimeout(() => {
        if (baiduRawText.value.trim().length >= 12) {
          parseBaiduShareText(false)
        } else {
          renderBaiduParsePreview(null)
          setBaiduStatus('等待粘贴分享文本。')
        }
      }, 420)
    }

    function baiduJobTokenKey(jobId) {
      return `baiduShareJobToken:${jobId}`
    }

    function saveBaiduJobToken(jobId, token) {
      if (!jobId || !token) return
      try {
        window.localStorage.setItem(baiduJobTokenKey(jobId), token)
      } catch {}
    }

    function getBaiduJobToken(jobId) {
      if (!jobId) return ''
      try {
        return window.localStorage.getItem(baiduJobTokenKey(jobId)) || ''
      } catch {
        return ''
      }
    }

    function renderBaiduDownloadAction(job) {
      if (job.status !== 'completed' && job.status !== 'partial_completed') {
        return '<div class="job-download"><span class="download-note">完成后这里会显示 ZIP 下载按钮。</span></div>'
      }
      if (!job.archive_ready) {
        return '<div class="job-download"><span class="download-note">服务器文件还未准备好打包。</span></div>'
      }
      const token = getBaiduJobToken(job.job_id)
      if (!token) {
        return '<div class="job-download"><span class="download-note">缺少本机下载凭证，请使用提交该任务的浏览器下载。</span></div>'
      }
      const href = `/api/tools/baidu-share/jobs/${encodeURIComponent(job.job_id)}/download.zip?token=${encodeURIComponent(token)}`
      const name = job.archive_name || `${job.job_id}.zip`
      return `<div class="job-download">
        <a class="download-link" href="${href}" target="_blank" rel="noopener" download="${escapeHtml(name)}" data-download-url="${href}">下载 ZIP</a>
        <button class="copy-download-link" type="button" data-download-url="${href}">复制链接</button>
        <span class="download-note">${escapeHtml(name)}</span>
      </div>`
    }

    function baiduPreflightConfirmMessage(preflight) {
      const roots = Array.isArray(preflight?.root_names) && preflight.root_names.length
        ? preflight.root_names.join('、')
        : '未命名分享内容'
      const limitHint = preflight?.truncated
        ? '目录较大，当前统计结果可能只是下限，实际转存和下载会更多。'
        : '统计已完成。'
      return [
        `将转存：${roots}`,
        `已统计文件：${Number(preflight?.file_count || 0)} 个`,
        `已统计目录：${Number(preflight?.dir_count || 0)} 个`,
        `已统计大小：${preflight?.total_size_display || '未知'}`,
        limitHint,
        '确认后才会开始转存到百度网盘并下载到服务器。'
      ].join('\\n')
    }

    async function preflightBaiduShareJob(verificationValue) {
      setBaiduStatus('正在统计分享文件大小，目录较大时会慢一些...', '')
      resultStatus.className = 'result-status status-idle'
      resultStatus.textContent = '大小预检中'
      result.textContent = '正在请求 /api/tools/baidu-share/preflight ...'
      const resp = await fetch('/api/tools/baidu-share/preflight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          raw_text: baiduRawText.value.trim(),
          verification_token: verificationValue,
          api_key: toolApiKey,
        })
      })
      const data = await resp.json()
      result.textContent = JSON.stringify(data, null, 2)
      if (!resp.ok || data.status !== 'success') {
        throw new Error(data.error || '分享文件大小预检失败')
      }
      const confirmed = window.confirm(baiduPreflightConfirmMessage(data.preflight || {}))
      if (!confirmed) {
        setBaiduStatus('已取消提交，未开始转存。', '')
        resultStatus.className = 'result-status status-idle'
        resultStatus.textContent = '已取消'
        return ''
      }
      return data.confirmation_token || data.preflight?.confirmation_token || ''
    }

    function renderBaiduJobs(jobs) {
      baiduJobsList.innerHTML = jobs.length ? jobs.map((job) => `
        <article class="job-item">
          <div class="job-item-head">
            <span>${escapeHtml(job.created_at)} · ${escapeHtml(job.job_id)}</span>
            <span class="status-pill ${escapeHtml(job.status_level || 'info')}" title="原始状态：${escapeHtml(job.status || '-')}">${escapeHtml(job.status_label || job.status || '排队中')}</span>
          </div>
          <div class="job-item-main">${escapeHtml(job.share_url_masked || '-')} · 提取码 ${escapeHtml(job.extraction_code_masked || '****')}</div>
          <div class="job-item-state">当前状态：<strong>${escapeHtml(job.status_label || job.status || '排队中')}</strong><span>${escapeHtml(job.worker_message || job.status_detail || '等待后台更新任务状态。')}</span></div>
          <div class="job-item-path">网盘：${escapeHtml(job.netdisk_save_path || '-')}</div>
          <div class="job-item-path">服务器：${escapeHtml(job.server_save_path || '-')}</div>
          <div class="job-item-path">已保存：${escapeHtml(job.server_files_size || '0 B')} · ${Number(job.server_files_count || 0)} 个文件</div>
          <div class="job-item-path">保留到：${escapeHtml(job.expires_at || '-')}（${escapeHtml(job.retention_label || '-')}策略）</div>
          ${renderBaiduDownloadAction(job)}
        </article>
      `).join('') : '<div class="empty-card">暂无百度网盘任务。</div>'
    }

    async function loadBaiduJobs() {
      try {
        const resp = await fetch('/api/tools/baidu-share/jobs?limit=12')
        const data = await resp.json()
        renderBaiduJobs(data.jobs || [])
      } catch (err) {
        baiduJobsList.innerHTML = `<div class="empty-card">任务记录加载失败：${escapeHtml(err)}</div>`
      }
    }

    function renderTools() {
      const keyword = searchInput.value.trim().toLowerCase()
      const visible = tools.filter((tool) => {
        const categoryMatched = currentCategory === 'all' || tool.category === currentCategory
        const keywordMatched = !keyword || `${tool.title} ${tool.purpose || ''} ${tool.desc}`.toLowerCase().includes(keyword)
        return categoryMatched && keywordMatched
      })

      grid.innerHTML = visible.map((tool) => `
        <article class="tool-card ${tool.active ? 'active' : ''} ${tool.url ? 'external' : ''}" data-tool="${tool.id}">
          <div class="tool-title">
            <span class="tool-title-main">
              ${tool.icon === 'keyrun' ? '<span class="tool-icon keyrun-mark" aria-hidden="true"><span></span><span></span><span></span><span></span></span>' : ''}
              <span class="tool-name">${tool.title}</span>
            </span>
            <span class="badge">${tool.badge}</span>
          </div>
          ${tool.purpose ? `<div class="tool-purpose">${tool.purpose}</div>` : ''}
          <p class="tool-desc">${tool.desc}</p>
        </article>
      `).join('') || '<div class="empty-card">没有匹配的工具，换个关键词试试。</div>'

      document.querySelectorAll('[data-tool]').forEach((card) => {
        card.addEventListener('click', () => {
          const toolId = card.getAttribute('data-tool')
          const tool = tools.find((item) => item.id === toolId)
          if (tool?.url) {
            window.location.href = tool.url
            return
          }
          if (toolId === 'baidu-share') {
            showBaiduSharePanel()
            return
          }
          if (toolId === 'qq-like') {
            showQQLikePanel()
            return
          }
          if (toolId === 'douyin-growth') {
            showDouyinGrowthIntro()
            return
          }
          if (toolId !== 'zepp-step') {
            showReservedToolPanel(tool)
            return
          }
          showZeppStepPanel()
        })
      })
      setActiveTool(currentToolId, false)
    }

    document.querySelectorAll('[data-category]').forEach((button) => {
      button.addEventListener('click', () => {
        currentCategory = button.getAttribute('data-category')
        document.querySelectorAll('[data-category]').forEach((item) => item.classList.remove('active'))
        button.classList.add('active')
        renderTools()
      })
    })

    document.querySelectorAll('[data-tool-shortcut]').forEach((button) => {
      button.addEventListener('click', () => {
        const toolId = button.getAttribute('data-tool-shortcut')
        if (toolId === 'baidu-share') {
          showBaiduSharePanel()
          return
        }
        if (toolId === 'qq-like') {
          showQQLikePanel()
          return
        }
        showZeppStepPanel()
      })
    })

    searchInput.addEventListener('input', renderTools)
    document.addEventListener('keydown', (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        searchInput.focus()
      }
    })

    function setFlow(flow) {
      flowTabs.forEach((tab) => {
        const active = tab.getAttribute('data-flow') === flow
        tab.classList.toggle('active', active)
        tab.setAttribute('aria-selected', active ? 'true' : 'false')
      })
      flowPanels.forEach((panel) => {
        panel.classList.toggle('active', panel.getAttribute('data-flow-panel') === flow)
      })
      if (flow === 'qr') {
        loadDeviceQrStatus()
      }
    }

    flowTabs.forEach((tab) => {
      tab.addEventListener('click', () => setFlow(tab.getAttribute('data-flow')))
    })

    function setQrStatus(message, state = '') {
      deviceQrStatus.className = `qr-status ${state}`.trim()
      deviceQrStatus.textContent = message
    }

    function setShareStatus(element, message, state = '') {
      element.className = `share-status ${state}`.trim()
      element.textContent = message
    }

    function formatStep(value) {
      if (!Number.isFinite(Number(value))) return '未加载'
      return Number(value).toLocaleString('zh-CN')
    }

    function updateSharedSubmitState() {
      sharedStepSubmit.disabled = !qrConfigured || qrPaused || sharedDeviceCount < 1 || !sharedCanSync
    }

    function renderSharedStepState(state) {
      const currentStep = Number(state?.current_step)
      const nextStep = Number(state?.next_submit_step)
      sharedCanSync = Boolean(state?.can_sync) && Number.isFinite(currentStep)
      if (!Number.isFinite(currentStep)) {
        sharedCurrentStepValue = null
        sharedCurrentStep.textContent = '未加载'
        sharedNextStepHint.textContent = '系统默认共享账号步数状态尚未加载，请稍后刷新。'
        updateSharedSubmitState()
        return
      }

      sharedCurrentStepValue = currentStep
      sharedCurrentStep.textContent = formatStep(currentStep)
      if (sharedCanSync && Number.isFinite(nextStep)) {
        sharedNextStepHint.innerHTML = `系统提供的二维码使用后台固定步数，用户不能手动填写；点击开始同步后，后台会自动提交 <strong>${formatStep(nextStep)}</strong> 来刷新同步状态。`
      } else {
        sharedNextStepHint.textContent = `当前系统二维码步数已达到上限 ${formatStep(state?.max_step || stepMax)}，暂时不能继续自动 +1 同步。`
      }
      updateSharedSubmitState()
    }

    function resetQrConfirmPanel() {
      if (qrConfirmTimer) {
        clearTimeout(qrConfirmTimer)
        qrConfirmTimer = null
      }
      qrConfirmPanel.hidden = true
      qrUnbindConfirm.checked = false
      confirmLoadDeviceQr.disabled = true
      qrShield.classList.remove('qr-warning')
    }

    function renderDeviceShareMeta(data) {
      if (data?.paused) {
        deviceShareMeta.innerHTML = escapeHtml(data.unavailable_message || '当前二维码暂时不能使用，请联系管理员。')
        return
      }
      if (!data?.share_count) {
        deviceShareMeta.innerHTML = '系统默认共享账号未就绪。请联系管理员在服务器后台配置后再使用扫码同步。'
        return
      }
      deviceShareMeta.innerHTML = `
        系统默认共享账号已就绪。账号和密码由后台固定维护，前台不可查看、上传或切换；
        扫码关注后只需要点击开始同步，步数由后台按当前系统二维码步数自动 +1。
      `
    }

    async function loadDeviceQrStatus() {
      try {
        const resp = await fetch('/api/device-bind/status', { cache: 'no-store' })
        const data = await resp.json()
        qrConfigured = Boolean(data.configured)
        qrPaused = Boolean(data.paused)
        qrUnavailableMessage = data.unavailable_message || data.hint || '当前二维码暂时不能使用，请联系管理员。'
        sharedDeviceCount = Number(data.share_count || 0)
        renderDeviceShareMeta(data)
        renderSharedStepState(data.shared_step)
        loadDeviceQr.disabled = !qrConfigured && !qrPaused
        refreshDeviceQr.disabled = !qrConfigured && !qrPaused
        updateSharedSubmitState()
        if (qrPaused) {
          deviceQrImage.hidden = true
          deviceQrImage.removeAttribute('src')
          qrShield.hidden = false
          resetQrConfirmPanel()
          qrShield.classList.add('qr-warning')
          qrShield.textContent = qrUnavailableMessage
          setQrStatus(qrUnavailableMessage, 'failed')
          setShareStatus(sharedStepStatus, '当前二维码暂时不能使用，无法开始扫码同步。', 'failed')
          return
        }
        if (qrConfigured && sharedDeviceCount < 1) {
          setShareStatus(sharedStepStatus, '二维码可以扫码；系统默认共享账号尚未配置，暂时不能开始同步。', '')
        } else if (qrConfigured) {
          setShareStatus(sharedStepStatus, '系统默认共享账号已就绪。扫码关注后可点击开始同步。', 'success')
        }
        if (qrConfigured) {
          setQrStatus(`二维码已配置。点击显示后会生成约 ${data.expires_in_seconds || 120} 秒有效的临时访问令牌。`, 'success')
        } else {
          setQrStatus(data.hint || '二维码尚未配置。', 'failed')
        }
      } catch (err) {
        qrConfigured = false
        qrPaused = false
        qrUnavailableMessage = ''
        sharedDeviceCount = 0
        loadDeviceQr.disabled = true
        refreshDeviceQr.disabled = true
        sharedCanSync = false
        updateSharedSubmitState()
        setQrStatus(`二维码配置检查失败：${err}`, 'failed')
        deviceShareMeta.textContent = `系统默认共享账号状态加载失败：${err}`
      }
    }

    function showQrUnavailableMessage() {
      deviceQrImage.hidden = true
      deviceQrImage.removeAttribute('src')
      qrShield.hidden = false
      resetQrConfirmPanel()
      qrShield.classList.add('qr-warning')
      qrShield.textContent = qrUnavailableMessage || '当前二维码有设备未解绑，暂时不能使用，请联系管理员。'
      setQrStatus(qrShield.textContent, 'failed')
    }

    async function startDeviceQrConfirmFlow() {
      if (qrPaused) {
        showQrUnavailableMessage()
        return
      }
      if (!qrConfigured) {
        await loadDeviceQrStatus()
      }
      if (qrPaused) {
        showQrUnavailableMessage()
        return
      }
      if (!qrConfigured) return

      resetQrConfirmPanel()
      loadDeviceQr.disabled = true
      refreshDeviceQr.disabled = true
      deviceQrImage.hidden = true
      deviceQrImage.removeAttribute('src')
      qrShield.hidden = false
      qrShield.classList.add('qr-warning')
      setQrStatus('请先确认取消绑定提醒。', 'warning')
      const warnings = [
        '提示 1/3：一定要记得取消绑定/取关华米服务号。',
        '提示 2/3：一定要记得取消绑定/取关华米服务号。',
        '提示 3/3：一定要记得取消绑定/取关华米服务号。',
      ]
      let index = 0
      const showNextWarning = () => {
        qrShield.textContent = warnings[index]
        index += 1
        if (index < warnings.length) {
          qrConfirmTimer = setTimeout(showNextWarning, 900)
          return
        }
        qrConfirmTimer = setTimeout(() => {
          qrConfirmTimer = null
          qrShield.classList.remove('qr-warning')
          qrShield.textContent = '请勾选确认后再显示二维码。'
          qrConfirmPanel.hidden = false
          setQrStatus('勾选确认后才会生成二维码。', 'warning')
          loadDeviceQr.disabled = !qrConfigured && !qrPaused
          refreshDeviceQr.disabled = !qrConfigured && !qrPaused
        }, 900)
      }
      showNextWarning()
    }

    async function showDeviceQr() {
      if (!qrUnbindConfirm.checked) {
        setQrStatus('请先勾选确认取消绑定提醒。', 'failed')
        return
      }
      resetQrConfirmPanel()
      loadDeviceQr.disabled = true
      refreshDeviceQr.disabled = true
      deviceQrImage.hidden = true
      deviceQrImage.removeAttribute('src')
      qrShield.hidden = false
      qrShield.classList.remove('qr-warning')
      qrShield.textContent = '正在生成临时二维码访问令牌...'
      setQrStatus('正在加载二维码...', '')
      try {
        const resp = await fetch('/api/device-bind/qr-token', { cache: 'no-store' })
        const data = await resp.json()
        if (!resp.ok || !data.token) {
          throw new Error(data.error || '二维码令牌获取失败')
        }
        const url = `/api/device-bind/qr?token=${encodeURIComponent(data.token)}&v=${Date.now()}`
        await new Promise((resolve, reject) => {
          deviceQrImage.onload = resolve
          deviceQrImage.onerror = () => reject(new Error('二维码图片加载失败'))
          deviceQrImage.src = url
        })
        qrShield.hidden = true
        deviceQrImage.hidden = false
        setQrStatus(`二维码已显示，约 ${data.expires_in_seconds || 120} 秒后失效。`, 'success')
      } catch (err) {
        qrShield.hidden = false
        qrShield.textContent = '二维码加载失败，请刷新后重试。'
        setQrStatus(`二维码加载失败：${err}`, 'failed')
      } finally {
        loadDeviceQr.disabled = !qrConfigured && !qrPaused
        refreshDeviceQr.disabled = !qrConfigured && !qrPaused
      }
    }

    qrUnbindConfirm.addEventListener('change', () => {
      confirmLoadDeviceQr.disabled = !qrUnbindConfirm.checked
    })
    confirmLoadDeviceQr.addEventListener('click', showDeviceQr)
    loadDeviceQr.addEventListener('click', startDeviceQrConfirmFlow)
    refreshDeviceQr.addEventListener('click', startDeviceQrConfirmFlow)

    sharedStepForm.addEventListener('submit', async (event) => {
      event.preventDefault()
      if (!sharedCanSync || !Number.isFinite(Number(sharedCurrentStepValue))) {
        setShareStatus(sharedStepStatus, '当前系统二维码步数尚未就绪，暂时不能开始同步。', 'failed')
        return
      }
      const token = sharedVerificationToken.value.trim()
      if (!isValidVerificationToken(token)) {
        showVerificationTokenError(result)
        setShareStatus(sharedStepStatus, '请先填写当天 6 位数字验证码；验证码请加入下方 QQ 交流群获取。', 'failed')
        return
      }
      sharedStepSubmit.disabled = true
      setShareStatus(sharedStepStatus, `正在使用系统默认共享账号提交 ${formatStep(sharedCurrentStepValue + 1)}，用于刷新同步状态...`, '')
      resultStatus.className = 'result-status status-idle'
      resultStatus.textContent = '共享提交中'
      result.textContent = '正在请求 /api/device-share/step ...'
      resultTip.className = 'tip-box'
      resultTip.innerHTML = ''
      try {
        const resp = await fetch('/api/device-share/step', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            debug: document.getElementById('debugMode').checked,
            api_key: toolApiKey,
            verification_token: token,
          })
        })
        const data = await resp.json()
        result.textContent = JSON.stringify(data, null, 2)
        resultStatus.className = `result-status ${data.status === 'success' ? 'status-success' : 'status-failed'}`
        resultStatus.textContent = data.status === 'success' ? '系统账号提交成功' : '系统账号提交失败'
        showResultTip(data)
        if (data.shared_step) {
          renderSharedStepState(data.shared_step)
        }
        setShareStatus(sharedStepStatus, data.status === 'success' ? `同步请求已提交，当前系统二维码步数已更新为 ${formatStep(data.submitted_step)}。请查看微信运动是否同步成功；成功后务必取关刚才关注的华米服务号，不取关后续可能无法继续修改或同步步数。` : (data.user_tip || data.error || '同步失败'), data.status === 'success' ? 'success' : 'failed')
        loadLogs()
      } catch (err) {
        result.textContent = `共享同步失败: ${err}`
        resultStatus.className = 'result-status status-failed'
        resultStatus.textContent = '共享同步失败'
        setShareStatus(sharedStepStatus, `同步失败：${err}`, 'failed')
      } finally {
        updateSharedSubmitState()
      }
    })

    fillExample.addEventListener('click', () => {
      const min = 20000
      const max = 50000
      form.elements.step.value = String(Math.floor(Math.random() * (max - min + 1)) + min)
    })

    function validateStepOnly() {
      const stepValue = Number(form.elements.step.value)
      if (!Number.isInteger(stepValue) || stepValue < 1 || stepValue > stepMax) {
        resultStatus.className = 'result-status status-failed'
        resultStatus.textContent = '参数错误'
        result.textContent = `步数必须在 1-${stepMax} 之间`
        resultTip.className = 'tip-box show failed'
        resultTip.innerHTML = `<strong>步数不合法</strong><span>请填写 1-${stepMax} 之间的整数。</span>`
        return null
      }
      return stepValue
    }

    previewPayload.addEventListener('click', async () => {
      const stepValue = validateStepOnly()
      if (!stepValue) return
      previewPayload.disabled = true
      resultStatus.className = 'result-status status-idle'
      resultStatus.textContent = '生成中'
      resultTip.className = 'tip-box'
      resultTip.innerHTML = ''
      result.textContent = '正在离线生成模拟 payload ...'
      try {
        const resp = await fetch('/api/payload-preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({
            step: String(stepValue),
            user: form.elements.user.value.trim(),
          })
        })
        const text = await resp.text()
        const json = JSON.parse(text)
        result.textContent = JSON.stringify(json, null, 2)
        resultStatus.className = resp.ok ? 'result-status status-success' : 'result-status status-failed'
        resultStatus.textContent = resp.ok ? '离线预览已生成' : '预览失败'
        if (resp.ok) {
          const failedChecks = (json.checks || []).filter((item) => !item.ok)
          resultTip.className = `tip-box show ${failedChecks.length ? 'failed' : 'success'}`
          resultTip.innerHTML = failedChecks.length
            ? `<strong>一致性检查有风险</strong><span>${escapeHtml(failedChecks.map((item) => item.detail).join('；'))}</span>`
            : '<strong>离线预览通过</strong><span>payload 字段已按步数联动生成；这个操作没有请求 Zepp。</span>'
        } else {
          showResultTip(json)
        }
      } catch (err) {
        result.textContent = `预览失败: ${err}`
        resultStatus.className = 'result-status status-failed'
        resultStatus.textContent = '预览失败'
        resultTip.className = 'tip-box show failed'
        resultTip.innerHTML = `<strong>预览失败</strong><span>${escapeHtml(err)}</span>`
      } finally {
        previewPayload.disabled = false
      }
    })

    async function loadLogs() {
      try {
        const resp = await fetch('/api/logs?limit=30')
        const data = await resp.json()
        const records = data.records || []
        logRows.innerHTML = records.length ? records.map((row) => `
          <tr>
            <td>${escapeHtml(row.created_at)}</td>
            <td>${escapeHtml(row.tool_label || row.tool_id)}</td>
            <td>${escapeHtml(row.account_masked)}</td>
            <td>${escapeHtml(row.step_display || row.step)}</td>
            <td><span class="status-pill ${row.status === 'success' ? 'success' : 'failed'}">${escapeHtml(row.status)}</span></td>
            <td>${escapeHtml(row.remote_addr || '-')}</td>
            <td class="log-message">${escapeHtml(row.user_tip || row.message)}</td>
          </tr>
        `).join('') : '<tr><td colspan="7">暂无记录</td></tr>'
      } catch (err) {
        logRows.innerHTML = `<tr><td colspan="7">日志加载失败：${escapeHtml(err)}</td></tr>`
      }
    }

    refreshLogs.addEventListener('click', loadLogs)

    async function loadMessages() {
      try {
        const resp = await fetch('/api/messages?limit=20')
        const data = await resp.json()
        const messages = data.messages || []
        messageList.innerHTML = messages.length ? messages.map((item) => `
          <article class="message-item">
            <div class="message-item-head">
              <span>${escapeHtml(item.created_at)} · ${escapeHtml(item.tool_id)}</span>
              <span>${escapeHtml(item.contact_masked || '未留联系方式')}</span>
            </div>
            <div class="message-item-body">${escapeHtml(item.content)}</div>
          </article>
        `).join('') : '<div class="empty-card">暂无留言。</div>'
      } catch (err) {
        messageList.innerHTML = `<div class="empty-card">留言加载失败：${escapeHtml(err)}</div>`
      }
    }

    refreshMessages.addEventListener('click', loadMessages)

    baiduNetdiskRoot.value = baiduNetdiskDefaultRoot
    baiduServerRoot.value = baiduServerDefaultRoot
    baiduRawText.addEventListener('input', scheduleBaiduParse)
    baiduRawText.addEventListener('paste', () => window.setTimeout(scheduleBaiduParse, 0))
    baiduNetdiskRoot.addEventListener('input', () => renderBaiduParsePreview(baiduParsePreview.dataset.parsed ? JSON.parse(baiduParsePreview.dataset.parsed) : null))
    baiduServerRoot.addEventListener('input', () => renderBaiduParsePreview(baiduParsePreview.dataset.parsed ? JSON.parse(baiduParsePreview.dataset.parsed) : null))
    baiduParseBtn.addEventListener('click', () => parseBaiduShareText(true))
    refreshBaiduJobs.addEventListener('click', loadBaiduJobs)
    baiduJobsList.addEventListener('click', async (event) => {
      const copyButton = event.target.closest('.copy-download-link')
      if (copyButton) {
        const rawUrl = copyButton.getAttribute('data-download-url') || ''
        if (!rawUrl) return
        try {
          await copyTextToClipboard(new URL(rawUrl, window.location.origin).href)
          copyButton.textContent = '已复制'
          setBaiduStatus('下载链接已复制；如果当前内置浏览器无法下载，请粘贴到 Chrome 或 Safari 打开。', 'success')
          window.setTimeout(() => { copyButton.textContent = '复制链接' }, 1500)
        } catch {
          setBaiduStatus('复制失败，请右键下载按钮复制链接，或在系统浏览器打开当前页面。', 'failed')
        }
        return
      }
      const downloadLink = event.target.closest('.download-link')
      if (downloadLink) {
        setBaiduStatus('已触发 ZIP 下载；如果当前内置浏览器没有反应，请点“复制链接”后在 Chrome 或 Safari 打开。', 'success')
      }
    })

    baiduShareForm.addEventListener('submit', async (e) => {
      e.preventDefault()
      const verificationValue = baiduVerificationToken.value.trim()
      if (!isValidVerificationToken(verificationValue)) {
        showVerificationTokenError(result)
        setBaiduStatus('请先填写当天 6 位数字验证码。', 'failed')
        return
      }
      const parsed = await parseBaiduShareText(true)
      if (!parsed) return
      baiduSubmit.disabled = true
      setBaiduStatus('正在准备提交百度网盘任务...', '')
      resultStatus.className = 'result-status status-idle'
      resultStatus.textContent = '准备中'
      resultTip.className = 'tip-box'
      resultTip.innerHTML = ''
      try {
        const sizeConfirmToken = await preflightBaiduShareJob(verificationValue)
        if (!sizeConfirmToken) return
        setBaiduStatus('已确认大小，正在记录百度网盘任务...', '')
        resultStatus.textContent = '记录中'
        result.textContent = '正在请求 /api/tools/baidu-share ...'
        const resp = await fetch('/api/tools/baidu-share', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            raw_text: baiduRawText.value.trim(),
            netdisk_save_root: baiduNetdiskRoot.value.trim(),
            server_download_root: baiduServerRoot.value.trim(),
            verification_token: verificationValue,
            size_confirm_token: sizeConfirmToken,
            api_key: toolApiKey,
          })
        })
        const data = await resp.json()
        result.textContent = JSON.stringify(data, null, 2)
        if (!resp.ok || data.status !== 'success') {
          throw new Error(data.error || '任务记录失败')
        }
        resultStatus.className = 'result-status status-success'
        resultStatus.textContent = '任务已记录'
        resultTip.className = 'tip-box show success'
        resultTip.innerHTML = '<strong>任务已进入队列</strong><span>worker 会自动处理；如果显示“需要登录”，请联系管理员在后台导入百度网盘 Cookie。</span>'
        setBaiduStatus(`任务已记录：${data.job?.job_id || ''}，当前状态：${data.job?.status_label || '排队中'}`, 'success')
        saveBaiduJobToken(data.job?.job_id, data.download_token)
        if (data.job) {
          baiduPathPreview.innerHTML = `
            <code>网盘任务目录：${escapeHtml(data.job.netdisk_save_path || '-')}</code>
            <code>服务器任务目录：${escapeHtml(data.job.server_save_path || '-')}</code>
          `
        }
        loadBaiduJobs()
      } catch (err) {
        resultStatus.className = 'result-status status-failed'
        resultStatus.textContent = '记录失败'
        resultTip.className = 'tip-box show failed'
        resultTip.innerHTML = `<strong>任务记录失败</strong><span>${escapeHtml(err)}</span>`
        setBaiduStatus(`任务记录失败：${err}`, 'failed')
      } finally {
        baiduSubmit.disabled = false
      }
    })

    copyGroupNumber.addEventListener('click', async () => {
      const groupNumber = document.getElementById('groupNumber').textContent.trim()
      try {
        await copyTextToClipboard(groupNumber)
        copyGroupNumber.textContent = '已复制'
        showCopyToast(`QQ群号 ${groupNumber} 已复制`)
        setTimeout(() => { copyGroupNumber.textContent = '复制 QQ 群号' }, 1500)
      } catch {
        copyGroupNumber.textContent = groupNumber
        showCopyToast('复制失败，请手动复制群号', 'failed')
      }
    })

    messageForm.addEventListener('submit', async (e) => {
      e.preventDefault()
      const fd = new FormData(messageForm)
      const payload = {
        tool_id: fd.get('tool_id')?.toString().trim(),
        contact: fd.get('contact')?.toString().trim(),
        content: fd.get('content')?.toString().trim(),
      }
      if (!payload.content) {
        messageStatus.className = 'message-status failed'
        messageStatus.textContent = '请填写留言内容'
        return
      }
      messageSubmit.disabled = true
      messageStatus.className = 'message-status'
      messageStatus.textContent = '正在提交留言...'
      try {
        const resp = await fetch('/api/messages', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams(payload)
        })
        const data = await resp.json()
        if (!resp.ok || data.status !== 'success') {
          throw new Error(data.error || '提交失败')
        }
        messageStatus.className = 'message-status success'
        messageStatus.textContent = '留言已提交'
        messageForm.reset()
        loadMessages()
      } catch (err) {
        messageStatus.className = 'message-status failed'
        messageStatus.textContent = `留言提交失败：${err}`
      } finally {
        messageSubmit.disabled = false
      }
    })

    form.addEventListener('submit', async (e) => {
      e.preventDefault()
      const fd = new FormData(form)
      const payload = {
        user: fd.get('user')?.toString().trim(),
        pwd: fd.get('pwd')?.toString().trim(),
        step: fd.get('step')?.toString().trim(),
        verification_token: fd.get('verification_token')?.toString().trim(),
      }
      if (!isValidVerificationToken(payload.verification_token)) {
        showVerificationTokenError()
        return
      }
      if (isSharedSelfBlockedAccount(payload.user)) {
        showSharedSelfBlockedResult()
        return
      }
      if (!payload.user || !payload.pwd || !payload.step) {
        result.textContent = '请填写完整参数'
        return
      }
      const stepValue = Number(payload.step)
      if (!Number.isInteger(stepValue) || stepValue < 1 || stepValue > stepMax) {
        resultStatus.className = 'result-status status-failed'
        resultStatus.textContent = '提交失败'
        result.textContent = `步数必须在 1-${stepMax} 之间`
        showResultTip({
          status: 'failed',
          user_tip: `步数超过微信运动常见上限 ${stepMax}`,
          action_tip: `请填写 1-${stepMax} 之间的步数；建议日常使用 8000-25000。`
        })
        return
      }
      submitBtn.disabled = true
      resultStatus.className = 'result-status status-idle'
      resultStatus.textContent = '提交中'
      resultTip.className = 'tip-box'
      resultTip.innerHTML = ''
      result.textContent = '正在请求 /api/tools/zepp-step ...'
      try {
        const resp = await fetch('/api/tools/zepp-step', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            account: payload.user,
            password: payload.pwd,
            step: Number(payload.step),
            debug: document.getElementById('debugMode').checked,
            api_key: toolApiKey,
            verification_token: payload.verification_token
          })
        })
        const text = await resp.text()
        try {
          const json = JSON.parse(text)
          result.textContent = JSON.stringify(json, null, 2)
          resultStatus.className = `result-status ${json.status === 'success' ? 'status-success' : 'status-failed'}`
              resultStatus.textContent = json.status === 'success' ? 'Zepp 接收成功' : '提交失败'
          showResultTip(json)
        } catch {
          result.textContent = text
          resultStatus.className = resp.ok ? 'result-status status-success' : 'result-status status-failed'
          resultStatus.textContent = resp.ok ? '请求完成' : '请求失败'
        }
      } catch (err) {
        result.textContent = `请求失败: ${err}`
        resultStatus.className = 'result-status status-failed'
        resultStatus.textContent = '请求失败'
        resultTip.className = 'tip-box show failed'
        resultTip.innerHTML = `<strong>请求失败</strong><span>${escapeHtml(err)}</span>`
      } finally {
        submitBtn.disabled = false
        loadLogs()
      }
    })

    renderTools()
    loadDeviceQrStatus()
    loadLogs()
    loadMessages()
    loadBaiduJobs()
    window.setInterval(loadBaiduJobs, 5000)
  </script>
</body>
</html>
""".replace("__ZEPP_TOOL_API_KEY__", json.dumps(TOOL_API_KEY)).replace(
    "__QQ_GROUP_NAME_JSON__", json.dumps(QQ_GROUP_NAME)
).replace(
    "__QQ_GROUP_NUMBER_JSON__", json.dumps(QQ_GROUP_NUMBER)
).replace(
    "__BAIDU_NETDISK_DEFAULT_SAVE_ROOT_JSON__", json.dumps(normalize_netdisk_root(""))
).replace(
    "__BAIDU_SERVER_DOWNLOAD_DEFAULT_ROOT_JSON__", json.dumps(normalize_server_download_root(""))
).replace(
    "__BAIDU_SHARE_RETENTION_LABEL__", baidu_share_retention_label()
).replace(
    "__QQ_GROUP_NAME__", QQ_GROUP_NAME
).replace(
    "__QQ_GROUP_NUMBER__", QQ_GROUP_NUMBER
)


def _admin_page_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>轻工具箱管理后台</title>
  <style>
    :root {
      --bg: #f7fafc;
      --panel: #ffffff;
      --line: #e2e8f0;
      --text: #0f172a;
      --muted: #64748b;
      --primary: #0f766e;
      --danger: #b91c1c;
      --success: #0f8f5f;
      --shadow: 0 16px 40px rgba(15, 23, 42, 0.08);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background: linear-gradient(180deg, #eefafa 0, var(--bg) 320px);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      display: grid;
      place-items: center;
      padding: 24px;
    }

    .shell {
      width: min(760px, 100%);
      display: grid;
      gap: 16px;
    }

    .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.96);
      box-shadow: var(--shadow);
      padding: 22px;
    }

    h1, h2 { margin: 0; letter-spacing: 0; }
    h1 { font-size: 24px; }
    h2 { font-size: 18px; }
    hr { border: 0; border-top: 1px solid var(--line); margin: 22px 0; }
    p { margin: 8px 0 0; color: var(--muted); line-height: 1.6; }
    label { display: block; margin: 16px 0 7px; font-weight: 800; }
    input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 12px;
      font: inherit;
    }

    input { height: 42px; }
    textarea {
      min-height: 120px;
      padding: 10px 12px;
      resize: vertical;
      line-height: 1.5;
    }

    button, .button-link {
      min-height: 40px;
      border: 0;
      border-radius: 8px;
      padding: 0 14px;
      background: var(--primary);
      color: #fff;
      font: inherit;
      font-weight: 850;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
    }

    button.ghost, .button-link.ghost {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
    }

    .actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 16px;
    }

    .token-box {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: center;
      margin-top: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
      padding: 16px;
    }

    .token-value {
      font-size: clamp(34px, 8vw, 56px);
      font-weight: 950;
      letter-spacing: 0.12em;
      font-variant-numeric: tabular-nums;
    }

    .meta {
      margin-top: 12px;
      color: var(--muted);
      line-height: 1.7;
    }

    .status {
      min-height: 22px;
      margin-top: 12px;
      font-weight: 800;
    }

    .status.success { color: var(--success); }
    .status.failed { color: var(--danger); }
    .hidden { display: none !important; }

    .qr-login-box {
      display: grid;
      grid-template-columns: 170px minmax(0, 1fr);
      gap: 16px;
      align-items: center;
      margin-top: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
      padding: 14px;
    }

    .qr-login-box img {
      width: 160px;
      height: 160px;
      border-radius: 8px;
      background: #fff;
      border: 1px solid var(--line);
    }

    .qr-login-box strong {
      display: block;
      margin-bottom: 6px;
    }

    .storage-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }

    .storage-row {
      display: grid;
      grid-template-columns: 1.2fr 0.7fr 0.8fr;
      gap: 10px;
      padding: 10px 0;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
    }

    .storage-row strong {
      color: var(--text);
      font-weight: 800;
      overflow-wrap: anywhere;
    }

    .copy-toast {
      position: fixed;
      left: 50%;
      bottom: 24px;
      z-index: 80;
      min-width: min(88vw, 260px);
      max-width: calc(100vw - 32px);
      transform: translate(-50%, 18px);
      opacity: 0;
      pointer-events: none;
      border-radius: 999px;
      padding: 12px 16px;
      background: #14532d;
      color: #fff;
      box-shadow: 0 18px 48px rgba(15, 23, 42, 0.22);
      font-weight: 900;
      text-align: center;
      transition: opacity 0.18s ease, transform 0.18s ease;
    }

    .copy-toast.show {
      opacity: 1;
      transform: translate(-50%, 0);
    }

    .copy-toast.failed {
      background: #9a3412;
    }

    @media (max-width: 560px) {
      body { padding: 14px; }
      .panel { padding: 18px; }
      .token-box { grid-template-columns: 1fr; }
      .qr-login-box { grid-template-columns: 1fr; }
      .storage-row { grid-template-columns: 1fr; }
      .actions button, .actions .button-link { width: 100%; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="panel">
      <h1>轻工具箱管理后台</h1>
      <p>用于查看和刷新当天 6 位验证码。用户提交步数前必须填写当天验证码。</p>
    </section>

    <section class="panel" id="loginPanel">
      <h2>管理员登录</h2>
      <form id="loginForm">
        <label>后台密码</label>
        <input id="password" type="password" autocomplete="current-password" placeholder="输入 ZEPP_ADMIN_PASSWORD" />
        <div class="actions">
          <button type="submit" id="loginBtn">登录</button>
        </div>
      </form>
      <div class="status" id="loginStatus"></div>
    </section>

    <section class="panel hidden" id="dashboardPanel">
      <h2>今日验证码</h2>
      <p>每天按服务器日期自动生成；点击刷新后，旧验证码立即失效。复制后可发到 QQ 群聊，用户按当天验证码提交。</p>
      <div class="token-box">
        <div>
          <div class="token-value" id="tokenValue">------</div>
          <div class="meta" id="tokenMeta">正在加载...</div>
        </div>
        <button type="button" id="copyToken">复制验证码</button>
      </div>
      <div class="actions">
        <button type="button" id="rotateToken">刷新今日验证码</button>
        <button class="ghost" type="button" id="reloadToken">重新加载</button>
        <button class="ghost" type="button" id="logoutBtn">退出登录</button>
      </div>
      <div class="status" id="dashboardStatus"></div>
      <hr />
      <h2>百度网盘登录态</h2>
      <p>网盘中转下载使用同一个后台验证码；BaiduPCS-Go 登录态只允许管理员在这里维护。优先使用后台扫码登录；如果百度没有返回完整 STOKEN，再复制完整 Cookie 导入。</p>
      <div class="meta" id="baiduAdminMeta">正在检查 BaiduPCS-Go 状态...</div>
      <div class="qr-login-box hidden" id="baiduQrBox">
        <img id="baiduQrImage" alt="百度网盘扫码登录二维码" />
        <div>
          <strong>百度网盘扫码登录</strong>
          <p id="baiduQrText">请使用百度网盘 App 或百度 App 扫码，并在手机端确认登录。</p>
          <div class="status" id="baiduQrStatus"></div>
        </div>
      </div>
      <label>百度网盘网页登录 Cookie</label>
      <textarea id="adminBaiduCookie" maxlength="12000" placeholder="BDUSS=...; STOKEN=...; PANPSC=..."></textarea>
      <div class="actions">
        <button type="button" id="startBaiduQrLogin">生成扫码登录二维码</button>
        <a class="button-link ghost" href="https://pan.baidu.com/" target="_blank" rel="noopener noreferrer">打开百度网盘网页版</a>
        <button type="button" id="importBaiduCookie">导入百度网盘 Cookie</button>
        <button class="ghost" type="button" id="reloadBaiduWorker">刷新网盘登录态</button>
      </div>
      <div class="status" id="baiduAdminStatus"></div>
      <hr />
      <h2>网盘文件存储</h2>
      <p>服务器下载文件会按保留时间自动清理；空间超过上限时优先清理最旧的终态任务，不会删除正在下载的任务。</p>
      <div class="meta" id="baiduStorageMeta">正在加载存储状态...</div>
      <div class="actions">
        <button class="ghost" type="button" id="reloadBaiduStorage">刷新存储状态</button>
        <button type="button" id="cleanupBaiduStorage">立即清理过期文件</button>
      </div>
      <div class="status" id="baiduStorageStatus"></div>
      <div class="storage-list" id="baiduStorageJobs"></div>
    </section>
  </main>
  <div class="copy-toast" id="copyToast" role="status" aria-live="polite" aria-atomic="true"></div>

  <script>
    const loginPanel = document.getElementById('loginPanel')
    const dashboardPanel = document.getElementById('dashboardPanel')
    const loginForm = document.getElementById('loginForm')
    const loginStatus = document.getElementById('loginStatus')
    const dashboardStatus = document.getElementById('dashboardStatus')
    const tokenValue = document.getElementById('tokenValue')
    const tokenMeta = document.getElementById('tokenMeta')
    const loginBtn = document.getElementById('loginBtn')
    const rotateToken = document.getElementById('rotateToken')
    const reloadToken = document.getElementById('reloadToken')
    const copyToken = document.getElementById('copyToken')
    const logoutBtn = document.getElementById('logoutBtn')
    const copyToast = document.getElementById('copyToast')
    const baiduAdminMeta = document.getElementById('baiduAdminMeta')
    const baiduAdminStatus = document.getElementById('baiduAdminStatus')
    const adminBaiduCookie = document.getElementById('adminBaiduCookie')
    const importBaiduCookie = document.getElementById('importBaiduCookie')
    const reloadBaiduWorker = document.getElementById('reloadBaiduWorker')
    const startBaiduQrLogin = document.getElementById('startBaiduQrLogin')
    const baiduQrBox = document.getElementById('baiduQrBox')
    const baiduQrImage = document.getElementById('baiduQrImage')
    const baiduQrText = document.getElementById('baiduQrText')
    const baiduQrStatus = document.getElementById('baiduQrStatus')
    const baiduStorageMeta = document.getElementById('baiduStorageMeta')
    const reloadBaiduStorage = document.getElementById('reloadBaiduStorage')
    const cleanupBaiduStorage = document.getElementById('cleanupBaiduStorage')
    const baiduStorageStatus = document.getElementById('baiduStorageStatus')
    const baiduStorageJobs = document.getElementById('baiduStorageJobs')
    let baiduQrSign = ''
    let baiduQrPolling = false

    function setStatus(element, text, state = '') {
      element.className = `status ${state}`.trim()
      element.textContent = text
    }

    let copyToastTimer = null
    function showCopyToast(message, state = 'success') {
      window.clearTimeout(copyToastTimer)
      copyToast.textContent = message
      copyToast.className = `copy-toast show ${state === 'failed' ? 'failed' : ''}`.trim()
      copyToastTimer = window.setTimeout(() => {
        copyToast.className = 'copy-toast'
      }, 2200)
    }

    async function copyTextToClipboard(text) {
      if (navigator.clipboard && window.isSecureContext) {
        try {
          await navigator.clipboard.writeText(text)
          return
        } catch {}
      }
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.left = '0'
      textarea.style.top = '0'
      textarea.style.width = '1px'
      textarea.style.height = '1px'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      textarea.setSelectionRange(0, textarea.value.length)
      const ok = document.execCommand('copy')
      textarea.remove()
      if (!ok) throw new Error('copy failed')
    }

    function showLogin() {
      loginPanel.classList.remove('hidden')
      dashboardPanel.classList.add('hidden')
    }

    function showDashboard() {
      loginPanel.classList.add('hidden')
      dashboardPanel.classList.remove('hidden')
    }

    function renderToken(data) {
      tokenValue.textContent = data.token || '------'
      tokenMeta.textContent = `日期：${data.token_date || '-'}；创建：${data.created_at || '-'}；更新：${data.updated_at || '-'}`
    }

    async function loadToken() {
      setStatus(dashboardStatus, '正在加载验证码...')
      const resp = await fetch('/api/admin/daily-token', { cache: 'no-store' })
      if (resp.status === 401) {
        showLogin()
        setStatus(loginStatus, '请先登录后台。')
        return
      }
      const data = await resp.json()
      if (!resp.ok) throw new Error(data.error || '加载失败')
      renderToken(data)
      showDashboard()
      setStatus(dashboardStatus, '验证码已加载。', 'success')
    }

    function renderBaiduWorkerStatus(data) {
      const binary = data.binary_found ? '已找到' : '未找到'
      const login = data.login_ready ? '已登录' : '未登录'
      baiduAdminMeta.textContent = `执行器：${binary}；登录态：${login}；配置目录：${data.config_dir || '-'}；网盘 tmp：${data.netdisk_default_root || '-'}`
      if (!data.binary_found) {
        setStatus(baiduAdminStatus, '未找到 BaiduPCS-Go，请先安装执行器或配置 BAIDUPCS_GO_BIN。', 'failed')
      } else if (!data.login_ready) {
        setStatus(baiduAdminStatus, data.login_message || '百度网盘账号未登录。', 'failed')
      } else {
        setStatus(baiduAdminStatus, data.login_message || '百度网盘账号已登录。', 'success')
        stopBaiduQrPolling()
      }
    }

    async function loadBaiduWorkerStatus() {
      const resp = await fetch('/api/admin/baidu-share/worker', { cache: 'no-store' })
      if (resp.status === 401) {
        showLogin()
        return
      }
      const data = await resp.json()
      if (!resp.ok || data.status !== 'success') throw new Error(data.error || '网盘登录态加载失败')
      renderBaiduWorkerStatus(data)
    }

    function renderBaiduStorage(data) {
      const storage = data.storage || data.summary || data
      baiduStorageMeta.textContent = [
        `保留：${storage.retention_label || '-'}`,
        `任务文件：${storage.stored_size_display || '0 B'} / ${storage.storage_max_display || '-'}`,
        `磁盘剩余：${storage.disk_free_display || '-'}`,
        `过期候选：${storage.expired_candidates || 0}`,
      ].join('；')
      if (storage.over_storage_limit) {
        setStatus(baiduStorageStatus, '当前任务文件超过存储上限，下一次清理会优先删除最旧终态任务。', 'failed')
      } else {
        setStatus(baiduStorageStatus, '存储状态正常。', 'success')
      }
      const jobs = Array.isArray(storage.jobs) ? storage.jobs.slice(0, 8) : []
      baiduStorageJobs.innerHTML = jobs.length ? jobs.map((job) => `
        <div class="storage-row">
          <strong>${job.job_id || '-'}</strong>
          <span>${job.size_display || '0 B'} · ${job.files || 0} 个文件</span>
          <span>过期：${job.expires_at || '-'}</span>
        </div>
      `).join('') : '<div class="meta">暂无服务器下载文件。</div>'
    }

    async function loadBaiduStorageStatus() {
      const resp = await fetch('/api/admin/baidu-share/storage', { cache: 'no-store' })
      if (resp.status === 401) {
        showLogin()
        return
      }
      const data = await resp.json()
      if (!resp.ok || data.status !== 'success') throw new Error(data.error || '存储状态加载失败')
      renderBaiduStorage(data)
    }

    async function cleanupBaiduStorageNow() {
      cleanupBaiduStorage.disabled = true
      setStatus(baiduStorageStatus, '正在清理过期或超限文件...')
      try {
        const resp = await fetch('/api/admin/baidu-share/storage/cleanup', { method: 'POST', cache: 'no-store' })
        const data = await resp.json()
        if (!resp.ok || data.status !== 'success') throw new Error(data.error || '清理失败')
        renderBaiduStorage(data)
        setStatus(baiduStorageStatus, `清理完成，删除 ${data.deleted_count || 0} 个任务，释放 ${data.freed_size_display || '0 B'}。`, 'success')
      } catch (err) {
        setStatus(baiduStorageStatus, `清理失败：${err.message || err}`, 'failed')
      } finally {
        cleanupBaiduStorage.disabled = false
      }
    }

    async function importBaiduCookieToWorker() {
      const cookies = adminBaiduCookie.value.trim()
      if (!cookies) {
        setStatus(baiduAdminStatus, '请先粘贴百度网盘网页登录 Cookie。', 'failed')
        return
      }
      importBaiduCookie.disabled = true
      setStatus(baiduAdminStatus, '正在导入百度网盘 Cookie...')
      try {
        const resp = await fetch('/api/admin/baidu-share/login-cookie', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cookies })
        })
        const data = await resp.json()
        if (!resp.ok || data.status !== 'success') throw new Error(data.error || '导入失败')
        adminBaiduCookie.value = ''
        renderBaiduWorkerStatus(data)
      } catch (err) {
        setStatus(baiduAdminStatus, `导入失败：${err.message || err}`, 'failed')
      } finally {
        importBaiduCookie.disabled = false
      }
    }

    function stopBaiduQrPolling() {
      baiduQrPolling = false
      baiduQrSign = ''
      startBaiduQrLogin.disabled = false
    }

    function shouldContinueBaiduQrPoll(data) {
      return data.qr_status === 'waiting' || data.qr_status === 'scanned'
    }

    async function pollBaiduQrLogin() {
      if (!baiduQrPolling || !baiduQrSign) return
      try {
        const resp = await fetch(`/api/admin/baidu-share/qr/status?sign=${encodeURIComponent(baiduQrSign)}`, { cache: 'no-store' })
        const data = await resp.json()
        if (!resp.ok || data.status !== 'success') throw new Error(data.error || '扫码登录失败')
        if (data.qr_status === 'imported') {
          baiduQrText.textContent = '扫码登录成功，BaiduPCS-Go 登录态已写入。'
          setStatus(baiduQrStatus, data.message || '扫码登录成功。', 'success')
          renderBaiduWorkerStatus(data)
          stopBaiduQrPolling()
          return
        }
        if (data.qr_status === 'scanned') {
          setStatus(baiduQrStatus, data.message || '已扫码，请在手机端确认登录。')
        } else {
          setStatus(baiduQrStatus, data.message || '等待扫码。')
        }
        if (shouldContinueBaiduQrPoll(data)) {
          window.setTimeout(pollBaiduQrLogin, 800)
        } else {
          stopBaiduQrPolling()
        }
      } catch (err) {
        setStatus(baiduQrStatus, `扫码登录失败：${err.message || err}`, 'failed')
        await loadBaiduWorkerStatus().catch(() => {})
        stopBaiduQrPolling()
      }
    }

    async function startBaiduQrLoginFlow() {
      stopBaiduQrPolling()
      startBaiduQrLogin.disabled = true
      setStatus(baiduQrStatus, '正在生成百度网盘扫码二维码...')
      baiduQrBox.classList.remove('hidden')
      try {
        const resp = await fetch('/api/admin/baidu-share/qr/start', { method: 'POST', cache: 'no-store' })
        const data = await resp.json()
        if (!resp.ok || data.status !== 'success') throw new Error(data.error || '二维码生成失败')
        baiduQrSign = data.sign
        baiduQrPolling = true
        baiduQrImage.src = data.proxy_image_url || data.image_url
        baiduQrText.textContent = '请使用百度网盘 App 或百度 App 扫码，并在手机端确认登录。'
        setStatus(baiduQrStatus, '等待扫码。')
        pollBaiduQrLogin()
      } catch (err) {
        setStatus(baiduQrStatus, `二维码生成失败：${err.message || err}`, 'failed')
        stopBaiduQrPolling()
      }
    }

    loginForm.addEventListener('submit', async (event) => {
      event.preventDefault()
      loginBtn.disabled = true
      setStatus(loginStatus, '正在登录...')
      try {
        const resp = await fetch('/api/admin/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: document.getElementById('password').value })
        })
        const data = await resp.json()
        if (!resp.ok || data.status !== 'success') throw new Error(data.error || '登录失败')
        document.getElementById('password').value = ''
        setStatus(loginStatus, '')
        await loadToken()
        loadBaiduWorkerStatus().catch((err) => setStatus(baiduAdminStatus, `加载失败：${err.message || err}`, 'failed'))
        loadBaiduStorageStatus().catch((err) => setStatus(baiduStorageStatus, `加载失败：${err.message || err}`, 'failed'))
      } catch (err) {
        setStatus(loginStatus, `登录失败：${err.message || err}`, 'failed')
      } finally {
        loginBtn.disabled = false
      }
    })

    rotateToken.addEventListener('click', async () => {
      rotateToken.disabled = true
      setStatus(dashboardStatus, '正在刷新验证码...')
      try {
        const resp = await fetch('/api/admin/daily-token/rotate', { method: 'POST' })
        const data = await resp.json()
        if (!resp.ok) throw new Error(data.error || '刷新失败')
        renderToken(data)
        setStatus(dashboardStatus, '今日验证码已刷新，旧验证码已失效。', 'success')
      } catch (err) {
        setStatus(dashboardStatus, `刷新失败：${err.message || err}`, 'failed')
      } finally {
        rotateToken.disabled = false
      }
    })

    reloadToken.addEventListener('click', () => {
      loadToken().catch((err) => setStatus(dashboardStatus, `加载失败：${err.message || err}`, 'failed'))
    })

    reloadBaiduWorker.addEventListener('click', () => {
      loadBaiduWorkerStatus().catch((err) => setStatus(baiduAdminStatus, `加载失败：${err.message || err}`, 'failed'))
    })

    reloadBaiduStorage.addEventListener('click', () => {
      loadBaiduStorageStatus().catch((err) => setStatus(baiduStorageStatus, `加载失败：${err.message || err}`, 'failed'))
    })

    cleanupBaiduStorage.addEventListener('click', cleanupBaiduStorageNow)

    importBaiduCookie.addEventListener('click', importBaiduCookieToWorker)
    startBaiduQrLogin.addEventListener('click', startBaiduQrLoginFlow)

    copyToken.addEventListener('click', async () => {
      try {
        await copyTextToClipboard(tokenValue.textContent.trim())
        setStatus(dashboardStatus, '验证码已复制。', 'success')
        showCopyToast('今日验证码已复制')
        copyToken.textContent = '已复制'
        setTimeout(() => { copyToken.textContent = '复制验证码' }, 1500)
      } catch {
        setStatus(dashboardStatus, `当前验证码：${tokenValue.textContent.trim()}`)
        showCopyToast('复制失败，请手动复制验证码', 'failed')
      }
    })

    logoutBtn.addEventListener('click', async () => {
      await fetch('/api/admin/logout', { method: 'POST' })
      showLogin()
      setStatus(loginStatus, '已退出登录。')
    })

    loadToken()
      .then(() => loadBaiduWorkerStatus().catch((err) => setStatus(baiduAdminStatus, `加载失败：${err.message || err}`, 'failed')))
      .then(() => loadBaiduStorageStatus().catch((err) => setStatus(baiduStorageStatus, `加载失败：${err.message || err}`, 'failed')))
      .catch(() => showLogin())
  </script>
</body>
</html>
"""


def _run_cli(user: str, pwd: str, step: int) -> None:
    result = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user": MiMotionRunner.desensitize_user_name(user),
        "step": step,
        "status": "failed",
        "message": "",
    }

    runner = MiMotionRunner(user=user, password=pwd)
    try:
        result = runner.run(step)
    except Exception as exc:  # pragma: no cover
        result["message"] = str(exc)
    else:
        result["status"] = "success"
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _run_http_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    ssl_cert: Optional[str] = None,
    ssl_key: Optional[str] = None,
) -> None:
    qq_like_service = QQMutualLikeService.from_paths(
        db_path=QQ_LIKE_DB_PATH,
        data_root=QQ_LIKE_DATA_ROOT,
        enabled=QQ_LIKE_ENABLED,
        webui_port=QQ_LIKE_WEBUI_PORT,
        onebot_port=QQ_LIKE_ONEBOT_PORT,
        max_contributors=QQ_LIKE_MAX_CONTRIBUTORS,
        pending_retention_hours=QQ_LIKE_PENDING_RETENTION_HOURS,
    )
    qq_like_runtime_state = {"startup_error": ""}

    class ZeppThreadingHTTPServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        request_queue_size = 50
        ssl_context: Optional[ssl.SSLContext] = None

        def __init__(self, *args, **kwargs):
            self.active_connections = threading.BoundedSemaphore(SERVER_MAX_ACTIVE_CONNECTIONS)
            super().__init__(*args, **kwargs)

        def get_request(self):
            client_socket, client_address = self.socket.accept()
            client_socket.settimeout(SERVER_SOCKET_TIMEOUT_SECONDS)
            return client_socket, client_address

        def process_request(self, request, client_address):
            if not self.active_connections.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except Exception:
                self.active_connections.release()
                self.shutdown_request(request)
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.active_connections.release()

        def handle_error(self, request, client_address):
            exc = sys.exc_info()[1]
            if isinstance(exc, (ClientConnectionProbeError, TimeoutError, ConnectionError, ssl.SSLError)):
                return
            super().handle_error(request, client_address)

    class StepHandler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            ssl_context = getattr(self.server, "ssl_context", None)
            if ssl_context is not None:
                self.request.settimeout(SERVER_PROTOCOL_PEEK_TIMEOUT_SECONDS)
                try:
                    prefix = self.request.recv(1, socket.MSG_PEEK)
                    if not prefix:
                        self.request.close()
                        raise ClientConnectionProbeError("客户端在发送请求前已断开")
                    # TLS 握手的第一字节是 0x16；普通 HTTP 请求保持原始 socket。
                    if prefix == b"\x16":
                        self.request = ssl_context.wrap_socket(self.request, server_side=True)
                except OSError:
                    self.request.close()
                    raise
                self.request.settimeout(SERVER_SOCKET_TIMEOUT_SECONDS)
            super().setup()
            self.request.settimeout(SERVER_SOCKET_TIMEOUT_SECONDS)

        def _json_response(self, payload: dict, status: int = 200, headers: Optional[Dict[str, str]] = None) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _html_response(self, html: str, status: int = 200, headers: Optional[Dict[str, str]] = None) -> None:
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _binary_response(
            self,
            data: bytes,
            *,
            content_type: str,
            status: int = 200,
            headers: Optional[Dict[str, str]] = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _is_https_request(self) -> bool:
            return isinstance(self.request, ssl.SSLSocket)

        def _is_local_request(self) -> bool:
            remote_addr = self.client_address[0] if self.client_address else ""
            return remote_addr in ("127.0.0.1", "::1", "localhost")

        def _admin_access_allowed(self) -> bool:
            return True

        def _cookie_value(self, name: str) -> str:
            cookie_header = self.headers.get("Cookie", "")
            if not cookie_header:
                return ""
            cookie = SimpleCookie()
            try:
                cookie.load(cookie_header)
            except Exception:
                return ""
            morsel = cookie.get(name)
            return morsel.value if morsel else ""

        def _admin_authenticated(self) -> bool:
            if not self._admin_access_allowed():
                return False
            return is_valid_admin_session(self._cookie_value(ADMIN_SESSION_COOKIE))

        def _admin_cookie_header(self, session_token: str) -> str:
            parts = [
                f"{ADMIN_SESSION_COOKIE}={session_token}",
                "Path=/",
                "HttpOnly",
                "SameSite=Lax",
                f"Max-Age={ADMIN_SESSION_TTL_SECONDS}",
            ]
            if self._is_https_request():
                parts.append("Secure")
            return "; ".join(parts)

        def _admin_clear_cookie_header(self) -> str:
            return (
                f"{ADMIN_SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; "
                "Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
            )

        def _admin_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
            headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Robots-Tag": "noindex, nofollow, noarchive",
            }
            headers.update(extra or {})
            return headers

        def _admin_forbidden(self) -> None:
            self._json_response(
                {
                    "status": "failed",
                    "error": "管理后台当前不可访问",
                },
                status=403,
                headers=self._admin_headers(),
            )

        def _admin_require_auth(self) -> bool:
            if not self._admin_access_allowed():
                self._admin_forbidden()
                return False
            if not self._admin_authenticated():
                self._json_response(
                    {
                        "status": "failed",
                        "error": "请先登录管理后台",
                    },
                    status=401,
                    headers=self._admin_headers(),
                )
                return False
            return True

        def _handle_admin_login(self, params: Dict[str, str]) -> None:
            if self.command != "POST":
                self._json_response({"status": "failed", "error": "只支持 POST 登录"}, status=405)
                return
            if not self._admin_access_allowed():
                self._admin_forbidden()
                return
            if not ADMIN_PASSWORD:
                self._json_response(
                    {
                        "status": "failed",
                        "error": "管理后台尚未配置密码",
                    },
                    status=503,
                    headers=self._admin_headers(),
                )
                return
            provided = params.get("password", "")
            if not hmac.compare_digest(provided, ADMIN_PASSWORD):
                self._json_response(
                    {
                        "status": "failed",
                        "error": "密码错误",
                    },
                    status=401,
                    headers=self._admin_headers(),
                )
                return
            session_token = create_admin_session()
            self._json_response(
                {
                    "status": "success",
                    "expires_in_seconds": ADMIN_SESSION_TTL_SECONDS,
                },
                headers=self._admin_headers({"Set-Cookie": self._admin_cookie_header(session_token)}),
            )

        def _handle_admin_logout(self) -> None:
            token = self._cookie_value(ADMIN_SESSION_COOKIE)
            if token:
                ADMIN_SESSIONS.pop(token, None)
            self._json_response(
                {"status": "success"},
                headers=self._admin_headers({"Set-Cookie": self._admin_clear_cookie_header()}),
            )

        def _handle_admin_daily_token(self) -> None:
            if self.command != "GET":
                self._json_response({"status": "failed", "error": "只支持 GET"}, status=405)
                return
            if not self._admin_require_auth():
                return
            self._json_response(get_or_create_daily_token(), headers=self._admin_headers())

        def _handle_admin_rotate_daily_token(self) -> None:
            if self.command != "POST":
                self._json_response({"status": "failed", "error": "只支持 POST"}, status=405)
                return
            if not self._admin_require_auth():
                return
            self._json_response(rotate_daily_token(), headers=self._admin_headers())

        def _handle_admin_qq_like_overview(self) -> None:
            if self.command != "GET":
                self._json_response(
                    {"status": "failed", "error": "只支持 GET"},
                    status=405,
                    headers=self._admin_headers(),
                )
                return
            if not self._admin_require_auth():
                return
            if not self._qq_like_available():
                return
            self._json_response(
                qq_like_service.admin_overview(),
                headers=self._admin_headers(),
            )

        def _handle_admin_qq_like_request(self, params: Dict[str, str]) -> None:
            if self.command != "POST":
                self._json_response(
                    {"status": "failed", "error": "只支持 POST"},
                    status=405,
                    headers=self._admin_headers(),
                )
                return
            if not self._admin_require_auth():
                return
            if not self._qq_like_available():
                return
            try:
                request_key = (
                    (self.headers.get("Idempotency-Key") or "").strip()
                    or params.get("request_id", "")
                )
                request = qq_like_service.submit_admin_request(
                    target_qq=params.get("target_qq", ""),
                    idempotency_key=request_key,
                    remote_addr=self.client_address[0] if self.client_address else "",
                )
            except (
                NapCatError,
                QQLikeStoreError,
                QQMutualLikeServiceError,
            ) as exc:
                self._qq_like_error(exc)
                return
            self._json_response(
                {"status": "success", "request": request},
                headers=self._admin_headers(),
            )

        def _admin_page_response(self) -> None:
            if not self._admin_access_allowed():
                self._html_response(
                    "<!doctype html><meta charset='utf-8'><title>禁止访问</title><h1>管理后台当前不可访问</h1>",
                    status=403,
                    headers=self._admin_headers(),
                )
                return
            self._html_response(_admin_page_html(), headers=self._admin_headers())

        def _ensure_valid_verification_token(self, params: Dict[str, str]) -> bool:
            if validate_daily_token(params):
                return True
            self._json_response(
                {
                    "status": "failed",
                    "error": "今日验证码缺失或错误",
                    "error_type": "daily_token_invalid",
                    "user_tip": "今日验证码不正确或已过期。",
                    "action_tip": "请加入页面下方 QQ 交流群获取当天 6 位数字验证码后再提交。",
                },
                status=403,
            )
            return False

        def _qq_like_headers(self) -> Dict[str, str]:
            return {
                "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Robots-Tag": "noindex, nofollow, noarchive",
                "Referrer-Policy": "same-origin",
            }

        def _qq_like_access_token(self) -> str:
            explicit = (self.headers.get("X-QQ-Like-Token") or "").strip()
            if explicit:
                return explicit
            authorization = (self.headers.get("Authorization") or "").strip()
            if authorization.lower().startswith("bearer "):
                return authorization[7:].strip()
            return ""

        def _qq_like_available(self) -> bool:
            if not QQ_LIKE_ENABLED:
                self._json_response(
                    {
                        "status": "failed",
                        "error": "QQ 互赞工具尚未启用",
                    },
                    status=503,
                    headers=self._qq_like_headers(),
                )
                return False
            startup_error = qq_like_runtime_state.get("startup_error", "")
            if startup_error:
                self._json_response(
                    {
                        "status": "failed",
                        "error": "QQ 互赞执行器暂不可用",
                    },
                    status=503,
                    headers=self._qq_like_headers(),
                )
                return False
            return True

        def _qq_like_error(self, exc: Exception) -> None:
            message = str(exc or "QQ 互赞请求失败")
            status = 400
            if "凭证" in message:
                status = 401
            elif "正在" in message or "稍后" in message:
                status = 409
            elif isinstance(exc, NapCatError):
                status = 503
            self._json_response(
                {
                    "status": "failed",
                    "error": message[:300],
                },
                status=status,
                headers=self._qq_like_headers(),
            )

        def _handle_qq_like(self, path: str, params: Dict[str, str]) -> bool:
            if not path.startswith("/api/tools/qq-like"):
                return False
            if not self._qq_like_available():
                return True
            headers = self._qq_like_headers()
            access_token = self._qq_like_access_token()
            remote_addr = self.client_address[0] if self.client_address else ""
            user_agent = self.headers.get("User-Agent", "")
            try:
                if path == "/api/tools/qq-like/login/start":
                    if self.command != "POST":
                        self._json_response(
                            {"status": "failed", "error": "只支持 POST"},
                            status=405,
                            headers=headers,
                        )
                        return True
                    payload = qq_like_service.start_login(
                        access_token=access_token,
                        remote_addr=remote_addr,
                        user_agent=user_agent,
                    )
                    self._json_response(payload, headers=headers)
                    return True

                if path == "/api/tools/qq-like/login/status":
                    if self.command != "GET":
                        self._json_response(
                            {"status": "failed", "error": "只支持 GET"},
                            status=405,
                            headers=headers,
                        )
                        return True
                    self._json_response(
                        qq_like_service.poll_login(access_token),
                        headers=headers,
                    )
                    return True

                if path == "/api/tools/qq-like/login/qr":
                    if self.command != "GET":
                        self._json_response(
                            {"status": "failed", "error": "只支持 GET"},
                            status=405,
                            headers=headers,
                        )
                        return True
                    self._binary_response(
                        qq_like_service.read_login_qr(access_token),
                        content_type="image/png",
                        headers=headers,
                    )
                    return True

                if path == "/api/tools/qq-like/login/refresh":
                    if self.command != "POST":
                        self._json_response(
                            {"status": "failed", "error": "只支持 POST"},
                            status=405,
                            headers=headers,
                        )
                        return True
                    self._json_response(
                        qq_like_service.refresh_login_qr(access_token),
                        headers=headers,
                    )
                    return True

                if path == "/api/tools/qq-like/dashboard":
                    if self.command != "GET":
                        self._json_response(
                            {"status": "failed", "error": "只支持 GET"},
                            status=405,
                            headers=headers,
                        )
                        return True
                    self._json_response(
                        qq_like_service.dashboard(access_token),
                        headers=headers,
                    )
                    return True

                if path == "/api/tools/qq-like/requests":
                    if self.command == "GET":
                        self._json_response(
                            qq_like_service.dashboard(access_token),
                            headers=headers,
                        )
                        return True
                    if self.command != "POST":
                        self._json_response(
                            {"status": "failed", "error": "只支持 GET 或 POST"},
                            status=405,
                            headers=headers,
                        )
                        return True
                    request_key = (
                        (self.headers.get("Idempotency-Key") or "").strip()
                        or params.get("request_id", "")
                    )
                    request = qq_like_service.submit_request(
                        access_token=access_token,
                        target_qq=params.get("target_qq", ""),
                        idempotency_key=request_key,
                        remote_addr=remote_addr,
                    )
                    self._json_response(
                        {"status": "success", "request": request},
                        headers=headers,
                    )
                    return True

                if path == "/api/tools/qq-like/revoke":
                    if self.command != "POST":
                        self._json_response(
                            {"status": "failed", "error": "只支持 POST"},
                            status=405,
                            headers=headers,
                        )
                        return True
                    self._json_response(
                        qq_like_service.revoke(access_token),
                        headers=headers,
                    )
                    return True

                if path == "/api/tools/qq-like/access/recover":
                    if self.command != "POST":
                        self._json_response(
                            {"status": "failed", "error": "只支持 POST"},
                            status=405,
                            headers=headers,
                        )
                        return True
                    payload = qq_like_service.recover_access(
                        params.get("contributor_id", ""),
                        params.get("recovery_code", ""),
                        remote_addr=remote_addr,
                    )
                    self._json_response(
                        {"status": "success", **payload},
                        headers=headers,
                    )
                    return True

                self._json_response(
                    {"status": "failed", "error": "未知 QQ 互赞接口"},
                    status=404,
                    headers=headers,
                )
                return True
            except (
                NapCatError,
                QQLikeStoreError,
                QQMutualLikeServiceError,
            ) as exc:
                self._qq_like_error(exc)
                return True

        def _asset_response(self, path: str) -> bool:
            asset_map = {
                "/assets/logo.svg": "logo.svg",
                "/favicon.svg": "logo.svg",
                "/assets/qq-group.svg": "qq-group.svg",
                "/assets/qq-group.jpg": "qq-group.jpg",
                "/assets/tutorial/zepp-life-logo.jpg": "tutorial/zepp-life-logo.jpg",
                "/assets/tutorial/zepp-logo.jpg": "tutorial/zepp-logo.jpg",
                "/assets/tutorial/zepp-life-original.jpg": "tutorial/zepp-life-original.jpg",
                "/assets/tutorial/zepp-original.jpg": "tutorial/zepp-original.jpg",
            }
            asset_name = asset_map.get(path)
            if not asset_name:
                return False

            asset_path = _asset_dir().joinpath(asset_name)
            if not asset_path.exists():
                self._json_response({"error": "asset not found"}, status=404)
                return True

            data = asset_path.read_bytes()
            self.send_response(200)
            if asset_name.endswith(".jpg") or asset_name.endswith(".jpeg"):
                self.send_header("Content-Type", "image/jpeg")
            else:
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True

        def _device_bind_qr_response(self, params: Dict[str, str]) -> None:
            headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Robots-Tag": "noindex, nofollow, noarchive",
                "Referrer-Policy": "same-origin",
            }
            if DEVICE_BIND_QR_DISTRIBUTION_PAUSED:
                self._json_response(
                    {
                        "status": "failed",
                        "error": DEVICE_BIND_QR_UNAVAILABLE_MESSAGE,
                        "paused": True,
                    },
                    status=409,
                    headers=headers,
                )
                return

            token = params.get("token", "")
            remote_addr = self.client_address[0] if self.client_address else ""
            user_agent = self.headers.get("User-Agent", "")
            ok, reason = validate_device_bind_qr_token(token, remote_addr, user_agent)
            if not ok:
                self._json_response({"status": "failed", "error": reason}, status=403, headers=headers)
                return

            qr_path = _resolve_configured_qr_path()
            if not qr_path:
                self._json_response({"status": "failed", "error": "二维码尚未配置"}, status=404, headers=headers)
                return

            data = qr_path.read_bytes()
            content_type = guess_image_content_type(qr_path, data)
            self.send_response(200)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", 'inline; filename="device-bind-qr"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _parse_multipart_form(self) -> Tuple[Dict[str, str], Dict[str, dict]]:
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype.lower():
                raise ValueError("请求必须使用 multipart/form-data")
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                raise ValueError("请求体为空")
            if length > DEVICE_SHARE_QR_MAX_BYTES + 64 * 1024:
                raise ValueError("上传内容过大")

            body = self.rfile.read(length)
            raw = f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
            message = BytesParser(policy=email_policy).parsebytes(raw)
            if not message.is_multipart():
                raise ValueError("无法解析上传表单")

            fields: Dict[str, str] = {}
            files: Dict[str, dict] = {}
            for part in message.iter_parts():
                if part.get_content_disposition() != "form-data":
                    continue
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                payload = part.get_payload(decode=True) or b""
                filename = part.get_filename()
                if filename:
                    files[str(name)] = {
                        "filename": filename,
                        "content": payload,
                        "content_type": part.get_content_type(),
                    }
                else:
                    fields[str(name)] = payload.decode("utf-8", errors="ignore").strip()
            return fields, files

        def _handle_device_share_upload(self) -> None:
            headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Robots-Tag": "noindex, nofollow, noarchive",
            }
            if not DEVICE_SHARE_UPLOAD_ENABLED:
                self._json_response(
                    {
                        "status": "failed",
                        "error": "共享账号由系统默认配置，前台不允许上传或替换",
                        "action_tip": "如需更换默认共享账号，请由管理员在服务器后台处理。",
                    },
                    status=403,
                    headers=headers,
                )
                return
            if self.command != "POST":
                self._json_response({"status": "failed", "error": "只支持 POST 上传"}, status=405, headers=headers)
                return
            try:
                fields, files = self._parse_multipart_form()
            except ValueError as exc:
                self._json_response({"status": "failed", "error": str(exc)}, status=400, headers=headers)
                return

            account = fields.get("account", "") or fields.get("user", "")
            password = fields.get("password", "") or fields.get("pwd", "")
            qr_file = files.get("qr") or files.get("qr_file") or files.get("file")
            if not account or not password or not qr_file:
                self._json_response(
                    {"status": "failed", "error": "必须提交共享 Zepp 账号、密码和二维码图片"},
                    status=400,
                    headers=headers,
                )
                return

            content = qr_file.get("content") or b""
            if not content:
                self._json_response({"status": "failed", "error": "二维码图片为空"}, status=400, headers=headers)
                return
            if len(content) > DEVICE_SHARE_QR_MAX_BYTES:
                self._json_response({"status": "failed", "error": "二维码图片不能超过 2MB"}, status=413, headers=headers)
                return

            detected_type = guess_image_content_type(Path(str(qr_file.get("filename") or "qr.jpg")), content)
            if detected_type not in ("image/jpeg", "image/png", "image/webp"):
                self._json_response(
                    {"status": "failed", "error": "只支持 JPG、PNG 或 WebP 二维码图片"},
                    status=400,
                    headers=headers,
                )
                return

            upload_dir = _device_share_upload_dir()
            upload_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(content).hexdigest()
            filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(6)}{image_extension_for_content_type(detected_type)}"
            qr_path = upload_dir.joinpath(filename)
            qr_path.write_bytes(content)
            try:
                os.chmod(qr_path, 0o600)
            except OSError:
                pass

            share = record_device_share(
                account=account,
                password=password,
                qr_path=qr_path,
                qr_sha256=digest,
                qr_content_type=detected_type,
                contributor_contact=fields.get("contact", ""),
                note=fields.get("note", ""),
                remote_addr=self.client_address[0] if self.client_address else "",
                user_agent=self.headers.get("User-Agent", ""),
            )
            self._json_response(
                {
                    "status": "success",
                    "share": share,
                    "privacy": "账号密码只保存在服务器本地 SQLite，用于共享设备账号提交步数；接口不会返回密码。",
                },
                headers=headers,
            )

        def _collect_params(self) -> Tuple[Dict[str, str], str]:
            parsed = urllib.parse.urlparse(self.path)
            params = dict(urllib.parse.parse_qs(parsed.query))
            merged = {k: (v[-1] if isinstance(v, list) and v else v) for k, v in params.items()}
            path = parsed.path or "/"

            if self.command == "POST":
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length).decode("utf-8", errors="ignore") if length else ""
                if body:
                    ctype = (self.headers.get("Content-Type") or "").lower()
                    if "application/json" in ctype:
                        merged_body = json.loads(body)
                    else:
                        merged_body = urllib.parse.parse_qs(body)
                        merged_body = {k: (v[-1] if v else "") for k, v in merged_body.items()}
                    merged.update(merged_body)
            return {str(k): str(v) for k, v in merged.items()}, path

        def _request_api_key(self, params: Dict[str, str]) -> str:
            auth = (self.headers.get("Authorization") or "").strip()
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
            return (
                (self.headers.get("X-Api-Key") or "").strip()
                or params.get("api_key", "").strip()
                or params.get("key", "").strip()
            )

        def _is_authorized(self, params: Dict[str, str]) -> bool:
            return self._request_api_key(params) == TOOL_API_KEY

        @staticmethod
        def _debug_enabled(params: Dict[str, str]) -> bool:
            return params.get("debug", "").strip().lower() in ("1", "true", "yes", "on")

        def _parse_baidu_share_text(self, params: Dict[str, str]) -> None:
            raw_text = params.get("raw_text", "") or params.get("text", "") or params.get("content", "")
            parsed = extract_baidu_share_payload(raw_text)
            if not parsed.get("ok"):
                self._json_response(
                    {
                        "status": "failed",
                        **parsed,
                        "defaults": {
                            "netdisk_save_root": normalize_netdisk_root(""),
                            "server_download_root": normalize_server_download_root(""),
                        },
                    },
                    status=400,
                )
                return

            share_url = str(parsed.get("share_url") or "")
            extraction_code = str(parsed.get("extraction_code") or "")
            self._json_response(
                {
                    "status": "success",
                    "parsed": {
                        "share_url": share_url,
                        "share_url_masked": mask_baidu_share_url(share_url),
                        "extraction_code": extraction_code,
                        "extraction_code_masked": mask_baidu_extract_code(extraction_code),
                    },
                    "defaults": {
                        "netdisk_save_root": normalize_netdisk_root(""),
                        "server_download_root": normalize_server_download_root(""),
                    },
                }
            )

        def _preflight_baidu_share_job(self, params: Dict[str, str]) -> None:
            if self.command != "POST":
                self._json_response({"status": "failed", "error": "请使用 POST 预检百度网盘任务"}, status=405)
                return

            if not self._is_authorized(params):
                self._json_response(
                    {
                        "status": "failed",
                        "error": "未授权，必须提供有效 api_key 或 X-Api-Key",
                    },
                    status=401,
                )
                return

            if not self._ensure_valid_verification_token(params):
                return

            raw_text = params.get("raw_text", "") or params.get("text", "") or params.get("content", "")
            parsed = extract_baidu_share_payload(raw_text)
            if not parsed.get("ok"):
                self._json_response({"status": "failed", **parsed}, status=400)
                return

            share_url = str(parsed.get("share_url") or "")
            extraction_code = str(parsed.get("extraction_code") or "")
            try:
                preflight = fetch_baidu_share_preflight(share_url, extraction_code)
            except Exception as exc:
                self._json_response(
                    {
                        "status": "failed",
                        "error": f"分享文件大小预检失败：{clean_message_text(str(exc), 300)}",
                        "requires_size_confirmation": True,
                    },
                    status=502,
                )
                return

            if not preflight.get("ok"):
                self._json_response(
                    {
                        "status": "failed",
                        "error": preflight.get("error") or "分享文件大小预检失败",
                        "preflight": preflight,
                        "requires_size_confirmation": True,
                    },
                    status=400,
                )
                return

            self._json_response(
                {
                    "status": "success",
                    "message": "分享文件大小预检完成，请确认后再提交转存任务。",
                    "preflight": preflight,
                    "confirmation_token": preflight.get("confirmation_token", ""),
                    "parsed": {
                        "share_url_masked": mask_baidu_share_url(share_url),
                        "extraction_code_masked": mask_baidu_extract_code(extraction_code),
                    },
                }
            )

        def _handle_admin_baidu_share_worker_status(self) -> None:
            if not self._admin_require_auth():
                return
            self._json_response({"status": "success", **baidu_share_worker_status()}, headers=self._admin_headers())

        def _handle_admin_baidu_share_storage_status(self) -> None:
            if self.command != "GET":
                self._json_response(
                    {"status": "failed", "error": "请使用 GET 查看百度网盘存储状态"},
                    status=405,
                    headers=self._admin_headers(),
                )
                return
            if not self._admin_require_auth():
                return
            self._json_response({"status": "success", "storage": baidu_share_storage_summary()}, headers=self._admin_headers())

        def _handle_admin_baidu_share_storage_cleanup(self) -> None:
            if self.command != "POST":
                self._json_response(
                    {"status": "failed", "error": "请使用 POST 清理百度网盘服务器文件"},
                    status=405,
                    headers=self._admin_headers(),
                )
                return
            if not self._admin_require_auth():
                return
            result = cleanup_baidu_share_storage("管理员手动清理")
            self._json_response({"status": "success", **result}, headers=self._admin_headers())

        def _handle_admin_baidu_share_cookie_login(self, params: Dict[str, str]) -> None:
            if self.command != "POST":
                self._json_response(
                    {"status": "failed", "error": "请使用 POST 导入百度网盘 Cookie"},
                    status=405,
                    headers=self._admin_headers(),
                )
                return

            if not self._admin_require_auth():
                return

            cookies = params.get("cookies", "") or params.get("cookie", "")
            result = run_baidupcs_cookie_login(cookies)
            if not result.get("ok"):
                self._json_response(
                    {"status": "failed", **result, **baidu_share_worker_status()},
                    status=400,
                    headers=self._admin_headers(),
                )
                return
            self._json_response({"status": "success", **result, **baidu_share_worker_status()}, headers=self._admin_headers())

        def _handle_admin_baidu_share_qr_start(self) -> None:
            if self.command != "POST":
                self._json_response(
                    {"status": "failed", "error": "请使用 POST 生成百度网盘扫码二维码"},
                    status=405,
                    headers=self._admin_headers(),
                )
                return

            if not self._admin_require_auth():
                return

            result = start_baidu_qr_login()
            if not result.get("ok"):
                self._json_response(
                    {"status": "failed", **result, **baidu_share_worker_status()},
                    status=502,
                    headers=self._admin_headers(),
                )
                return
            self._json_response({"status": "success", **result, **baidu_share_worker_status()}, headers=self._admin_headers())

        def _handle_admin_baidu_share_qr_image(self, params: Dict[str, str]) -> None:
            if not self._admin_require_auth():
                return

            try:
                image_bytes, content_type = fetch_baidu_qr_image(params.get("sign", ""))
            except Exception as exc:
                self._json_response(
                    {"status": "failed", "error": clean_message_text(str(exc), 300)},
                    status=400,
                    headers=self._admin_headers(),
                )
                return
            self.send_response(200)
            headers = self._admin_headers(
                {
                    "Content-Type": content_type,
                    "Content-Length": str(len(image_bytes)),
                }
            )
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(image_bytes)

        def _handle_admin_baidu_share_qr_status(self, params: Dict[str, str]) -> None:
            if not self._admin_require_auth():
                return

            result = poll_baidu_qr_login(params.get("sign", ""))
            http_status = 200 if result.get("ok") else 400
            if result.get("qr_status") == "failed":
                http_status = 502
            self._json_response(
                {"status": "success" if result.get("ok") else "failed", **result, **baidu_share_worker_status()},
                status=http_status,
                headers=self._admin_headers(),
            )

        def _submit_baidu_share_job(self, params: Dict[str, str]) -> None:
            if self.command != "POST":
                self._json_response({"status": "failed", "error": "请使用 POST 提交百度网盘任务"}, status=405)
                return

            if not self._is_authorized(params):
                self._json_response(
                    {
                        "status": "failed",
                        "error": "未授权，必须提供有效 api_key 或 X-Api-Key",
                    },
                    status=401,
                )
                return

            if not self._ensure_valid_verification_token(params):
                return

            raw_text = params.get("raw_text", "") or params.get("text", "") or params.get("content", "")
            parsed = extract_baidu_share_payload(raw_text)
            if not parsed.get("ok"):
                self._json_response({"status": "failed", **parsed}, status=400)
                return

            share_url = str(parsed.get("share_url") or "")
            extraction_code = str(parsed.get("extraction_code") or "")
            confirm_token = params.get("size_confirm_token", "") or params.get("confirmation_token", "")
            expected_confirm_token = baidu_share_confirmation_token(share_url, extraction_code)
            if not confirm_token or not hmac.compare_digest(confirm_token, expected_confirm_token):
                self._json_response(
                    {
                        "status": "failed",
                        "error": "提交前必须先确认分享文件大小。",
                        "requires_size_confirmation": True,
                        "preflight_endpoint": "/api/tools/baidu-share/preflight",
                    },
                    status=409,
                )
                return

            job = record_baidu_share_job(
                raw_text=str(parsed.get("raw_text") or raw_text),
                share_url=share_url,
                extraction_code=extraction_code,
                netdisk_root=params.get("netdisk_save_root", "") or params.get("netdisk_save_path", ""),
                server_root=params.get("server_download_root", "") or params.get("server_save_path", ""),
                remote_addr=self.client_address[0] if self.client_address else "",
                user_agent=self.headers.get("User-Agent", ""),
            )
            public_job = public_baidu_share_job(job)
            download_token = baidu_share_job_download_token(job)
            self._json_response(
                {
                    "status": "success",
                    "message": "百度网盘分享任务已进入队列，worker 会按该记录执行转存和下载。",
                    "job": public_job,
                    "download_token": download_token,
                    "parsed": {
                        "share_url": job["share_url"],
                        "share_url_masked": job["share_url_masked"],
                        "extraction_code": job["extraction_code"],
                        "extraction_code_masked": job["extraction_code_masked"],
                    },
                    "next_step": "等待 worker 调用 BaiduPCS-Go：先转存到 netdisk_save_path，再 download --saveto 到 server_save_path。",
                }
            )

        def _download_baidu_share_zip(self, job_id: str, params: Dict[str, str]) -> None:
            if self.command != "GET":
                self._json_response({"status": "failed", "error": "请使用 GET 下载 ZIP"}, status=405)
                return

            job = get_baidu_share_job(job_id)
            if not job:
                self._json_response({"status": "failed", "error": "任务不存在"}, status=404)
                return

            token = params.get("token", "").strip()
            expected_token = baidu_share_job_download_token(job)
            if not expected_token or not hmac.compare_digest(token, expected_token):
                self._json_response({"status": "failed", "error": "下载凭证无效或已缺失"}, status=403)
                return

            status = str(job.get("status") or "")
            if status not in ("completed", "partial_completed"):
                meta = baidu_share_status_meta(status)
                self._json_response(
                    {
                        "status": "failed",
                        "error": "任务尚未完成，暂不能下载 ZIP",
                        "job_status": meta["status"],
                        "job_status_label": meta["status_label"],
                        "job_status_detail": meta["status_detail"],
                    },
                    status=409,
                )
                return

            zip_path = None
            try:
                zip_path, zip_size = create_baidu_share_zip(job)
                filename = f"{job.get('job_id') or 'baidu-share'}.zip"
                safe_filename = urllib.parse.quote(filename)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{safe_filename}")
                self.send_header("Content-Length", str(zip_size))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, private, max-age=0")
                self.end_headers()
                with zip_path.open("rb") as fh:
                    while True:
                        chunk = fh.read(1024 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except FileNotFoundError as exc:
                self._json_response({"status": "failed", "error": str(exc)}, status=404)
            except BrokenPipeError:
                return
            finally:
                if zip_path:
                    try:
                        zip_path.unlink(missing_ok=True)
                    except OSError:
                        pass

        def _submit_zepp_step(self, params: Dict[str, str]) -> None:
            if not self._ensure_valid_verification_token(params):
                return

            user = params.get("account", "") or params.get("user", "")
            pwd = params.get("password", "") or params.get("pwd", "")
            debug_mode = self._debug_enabled(params)
            try:
                step = int(params.get("step", "0"))
            except ValueError:
                step = 0

            if user and is_shared_device_self_blocked_account(user):
                result = {
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "user": MiMotionRunner.desensitize_user_name(user),
                    "step": step if step > 0 else None,
                    "status": "failed",
                    "message": "该账号为共享账号，不支持自定义步数",
                    "error": "该账号为共享账号，不支持自定义步数",
                    "user_tip": "该账号为共享账号，不支持自定义步数",
                    "action_tip": "请切换到“没有设备，扫码绑定”流程使用系统共享二维码同步。",
                    "blocked_shared_account": True,
                }
                self._json_response(result, status=403)
                return

            if not user or not pwd or step <= 0:
                self._json_response(
                    {
                        "status": "failed",
                        "error": "参数不完整，必须提供 account/password/step 或 user/pwd/step",
                    },
                    status=400,
                )
                return

            if step > STEP_MAX:
                result = {
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "user": MiMotionRunner.desensitize_user_name(user),
                    "step": step,
                    "status": "failed",
                    "message": f"步数超过上限（{STEP_MAX}）",
                }
                result.update(classify_error(result["message"]))
                record_tool_call(
                    tool_id="zepp-step",
                    account=user,
                    step=step,
                    status=result["status"],
                    message=result["message"],
                    remote_addr=self.client_address[0] if self.client_address else "",
                    debug=debug_mode,
                    response=result,
                )
                self._json_response(result, status=400)
                return

            runner = MiMotionRunner(user=user, password=pwd)
            result = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user": MiMotionRunner.desensitize_user_name(user),
                "step": step,
                "status": "failed",
                "message": "",
            }
            try:
                msg, success = runner.login_and_post_step(step)
                result["status"] = "success" if success else "failed"
                result["message"] = msg
                record_tool_call(
                    tool_id="zepp-step",
                    account=user,
                    step=step,
                    status=result["status"],
                    message=result["message"],
                    remote_addr=self.client_address[0] if self.client_address else "",
                    debug=debug_mode,
                    response=result,
                )
                self._json_response(result)
            except Exception as exc:
                result["message"] = str(exc)
                if debug_mode:
                    cause = str(exc)
                    if "登录token接口请求失败" in cause:
                        result["suggestion"] = "建议检查：账号密码、是否触发验证码/反爬策略、是否有风控风控页面重定向。"
                    if "HTTP请求失败" in cause:
                        result["suggestion"] = "建议检查网络：DNS、HTTPS 访问、代理/出口 IP"
                result.update(classify_error(result["message"]))
                record_tool_call(
                    tool_id="zepp-step",
                    account=user,
                    step=step,
                    status=result["status"],
                    message=result["message"],
                    remote_addr=self.client_address[0] if self.client_address else "",
                    debug=debug_mode,
                    response=result,
                )
                self._json_response(result, status=500)

        def _submit_shared_device_step(self, params: Dict[str, str]) -> None:
            if not self._is_authorized(params):
                self._json_response(
                    {
                        "status": "failed",
                        "error": "未授权，必须提供有效 api_key 或 X-Api-Key",
                    },
                    status=401,
                )
                return

            if not self._ensure_valid_verification_token(params):
                return

            if DEVICE_BIND_QR_DISTRIBUTION_PAUSED:
                self._json_response(
                    {
                        "status": "failed",
                        "error": DEVICE_BIND_QR_UNAVAILABLE_MESSAGE,
                        "user_tip": DEVICE_BIND_QR_UNAVAILABLE_MESSAGE,
                        "action_tip": "请联系管理员处理当前二维码设备解绑后再使用扫码同步。",
                        "paused": True,
                    },
                    status=409,
                )
                return

            share = get_latest_device_share()
            if not share:
                self._json_response(
                    {
                        "status": "failed",
                        "error": "当前没有可用的系统默认共享账号",
                        "user_tip": "系统默认共享账号尚未配置。",
                        "action_tip": "请联系管理员在服务器后台配置默认共享账号和二维码。",
                    },
                    status=404,
                )
                return

            debug_mode = self._debug_enabled(params)
            account = str(share.get("account") or "")
            password = str(share.get("password") or "")

            with SHARED_DEVICE_STEP_LOCK:
                step_state = get_shared_device_step_state(share)
                current_step = _safe_shared_step(step_state.get("current_step"))
                submit_step = current_step + 1
                if submit_step > STEP_MAX:
                    result = {
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "user": share.get("account_masked", ""),
                        "device_share_id": share.get("id"),
                        "current_step": current_step,
                        "requested_step": current_step,
                        "submitted_step": submit_step,
                        "step": current_step,
                        "status": "failed",
                        "message": f"系统二维码当前步数已达到上限（{STEP_MAX}），暂时不能继续自动 +1 同步。",
                        "shared_step": step_state,
                    }
                    result.update(classify_error(result["message"]))
                    result["action_tip"] = "请联系管理员重置或更换系统默认共享二维码账号。"
                    self._json_response(result, status=400)
                    return

                runner = MiMotionRunner(user=account, password=password)
                result = {
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "user": MiMotionRunner.desensitize_user_name(account),
                    "device_share_id": share.get("id"),
                    "current_step": current_step,
                    "requested_step": current_step,
                    "submitted_step": submit_step,
                    "sync_bump": True,
                    "step": submit_step,
                    "status": "failed",
                    "message": "",
                    "shared_step": step_state,
                }
                try:
                    msg, success = runner.login_and_post_step(submit_step)
                    result["status"] = "success" if success else "failed"
                    result["message"] = msg
                    if success:
                        result["shared_step"] = update_shared_device_step_state(share, submit_step)
                        result["sync_tip"] = f"系统默认共享账号已按当前系统步数 {current_step} 自动 +1，实际提交 {submit_step}，用于刷新微信运动同步状态。请查看微信运动是否同步成功；成功后请取关华米服务号，不取关后续可能无法继续修改或同步步数。"
                    record_tool_call(
                        tool_id="shared-device-step",
                        account=account,
                        step=submit_step,
                        status=result["status"],
                        message=result["message"],
                        remote_addr=self.client_address[0] if self.client_address else "",
                        debug=debug_mode,
                        response=result,
                    )
                    self._json_response(result)
                except Exception as exc:
                    result["message"] = str(exc)
                    if debug_mode:
                        result["suggestion"] = "系统默认共享账号提交失败时，请检查后台账号密码是否仍可登录、设备绑定是否还有效。"
                    result.update(classify_error(result["message"]))
                    record_tool_call(
                        tool_id="shared-device-step",
                        account=account,
                        step=submit_step,
                        status=result["status"],
                        message=result["message"],
                        remote_addr=self.client_address[0] if self.client_address else "",
                        debug=debug_mode,
                        response=result,
                    )
                    self._json_response(result, status=500)

        def _handle_step(self) -> None:
            parsed_path = urllib.parse.urlparse(self.path).path or "/"
            if parsed_path == "/api/device-share/upload":
                self._handle_device_share_upload()
                return

            params, path = self._collect_params()
            if self._asset_response(path):
                return

            no_store_headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Robots-Tag": "noindex, nofollow, noarchive",
            }

            if path == "/admin":
                self._admin_page_response()
                return

            if path == "/api/admin/login":
                self._handle_admin_login(params)
                return

            if path == "/api/admin/logout":
                self._handle_admin_logout()
                return

            if path == "/api/admin/daily-token":
                self._handle_admin_daily_token()
                return

            if path == "/api/admin/daily-token/rotate":
                self._handle_admin_rotate_daily_token()
                return

            if path == "/api/admin/qq-like":
                self._handle_admin_qq_like_overview()
                return

            if path == "/api/admin/qq-like/requests":
                self._handle_admin_qq_like_request(params)
                return

            if path == "/api/admin/baidu-share/worker":
                self._handle_admin_baidu_share_worker_status()
                return

            if path == "/api/admin/baidu-share/storage":
                self._handle_admin_baidu_share_storage_status()
                return

            if path == "/api/admin/baidu-share/storage/cleanup":
                self._handle_admin_baidu_share_storage_cleanup()
                return

            if path == "/api/admin/baidu-share/login-cookie":
                self._handle_admin_baidu_share_cookie_login(params)
                return

            if path == "/api/admin/baidu-share/qr/start":
                self._handle_admin_baidu_share_qr_start()
                return

            if path == "/api/admin/baidu-share/qr/status":
                self._handle_admin_baidu_share_qr_status(params)
                return

            if path == "/api/admin/baidu-share/qr/image":
                self._handle_admin_baidu_share_qr_image(params)
                return

            if self._handle_qq_like(path, params):
                return

            if path == "/api/device-bind/status":
                self._json_response(device_bind_qr_status(), headers=no_store_headers)
                return

            if path == "/api/device-bind/qr-token":
                if DEVICE_BIND_QR_DISTRIBUTION_PAUSED:
                    self._json_response(
                        {
                            "status": "failed",
                            "error": DEVICE_BIND_QR_UNAVAILABLE_MESSAGE,
                            **device_bind_qr_status(),
                        },
                        status=409,
                        headers=no_store_headers,
                    )
                    return
                if not _resolve_configured_qr_path():
                    self._json_response(
                        {
                            "status": "failed",
                            "error": "二维码尚未配置",
                            **device_bind_qr_status(),
                        },
                        status=404,
                        headers=no_store_headers,
                    )
                    return
                remote_addr = self.client_address[0] if self.client_address else ""
                try:
                    token_payload = issue_device_bind_qr_token(remote_addr, self.headers.get("User-Agent", ""))
                except ValueError as exc:
                    self._json_response(
                        {"status": "failed", "error": str(exc)},
                        status=429,
                        headers=no_store_headers,
                    )
                    return
                self._json_response({"status": "success", **token_payload}, headers=no_store_headers)
                return

            if path == "/api/device-bind/qr":
                self._device_bind_qr_response(params)
                return

            if path == "/api/device-share/list":
                try:
                    limit = int(params.get("limit", "10"))
                except ValueError:
                    limit = 10
                self._json_response(
                    {
                        "status": "success",
                        "shares": list_device_shares(limit),
                        "latest_share": public_device_share(get_latest_device_share()),
                    },
                    headers=no_store_headers,
                )
                return

            if path == "/api/device-share/step":
                self._submit_shared_device_step(params)
                return

            if path == "/api/logs":
                try:
                    limit = int(params.get("limit", "30"))
                except ValueError:
                    limit = 30
                self._json_response(
                    {
                        "records": list_tool_logs(limit),
                        "privacy": "日志接口只返回脱敏账号；数据库保存完整账号、步数和响应用于排查，不保存密码。",
                    }
                )
                return

            if path == "/api/messages":
                if self.command == "GET":
                    try:
                        limit = int(params.get("limit", "20"))
                    except ValueError:
                        limit = 20
                    self._json_response({"messages": list_board_messages(limit)})
                    return

                content = clean_message_text(params.get("content", ""))
                if not content:
                    self._json_response({"status": "failed", "error": "留言内容不能为空"}, status=400)
                    return

                item = record_board_message(
                    tool_id=params.get("tool_id", "zepp-step"),
                    contact=params.get("contact", ""),
                    content=content,
                    remote_addr=self.client_address[0] if self.client_address else "",
                    user_agent=self.headers.get("User-Agent", ""),
                )
                self._json_response({"status": "success", "message": item})
                return

            if path == "/api/tools/baidu-share/parse":
                self._parse_baidu_share_text(params)
                return

            if path == "/api/tools/baidu-share/preflight":
                self._preflight_baidu_share_job(params)
                return

            baidu_zip_match = re.fullmatch(r"/api/tools/baidu-share/jobs/([^/]+)/download\.zip", path)
            if baidu_zip_match:
                self._download_baidu_share_zip(urllib.parse.unquote(baidu_zip_match.group(1)), params)
                return

            if path == "/api/tools/baidu-share/jobs":
                try:
                    limit = int(params.get("limit", "20"))
                except ValueError:
                    limit = 20
                self._json_response({"status": "success", "jobs": list_baidu_share_jobs(limit)}, headers=no_store_headers)
                return

            if path == "/api/tools/baidu-share":
                self._submit_baidu_share_job(params)
                return

            if path == "/api/payload-preview":
                try:
                    step = int(params.get("step", "0"))
                except ValueError:
                    step = 0
                if step <= 0:
                    self._json_response(
                        {"status": "failed", "error": "必须提供合法 step"},
                        status=400,
                    )
                    return
                if step > STEP_MAX:
                    result = {
                        "status": "failed",
                        "message": f"步数超过上限（{STEP_MAX}）",
                    }
                    result.update(classify_error(result["message"]))
                    self._json_response(result, status=400)
                    return
                self._json_response(MiMotionRunner.build_payload_preview(step, params.get("date"), params.get("user", "")))
                return

            if path in ("", "/") or path == "/index.html":
                if params.get("user") and params.get("pwd") and params.get("step"):
                    pass
                else:
                    html = _simple_page_html()
                    data = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, private, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

            if path == "/api/tools/zepp-step":
                if not self._is_authorized(params):
                    self._json_response(
                        {
                            "status": "failed",
                            "error": "未授权，必须提供有效 api_key 或 X-Api-Key",
                        },
                        status=401,
                    )
                    return
                self._submit_zepp_step(params)
                return

            if path == "/api/step" or path in ("", "/") or path == "/index.html":
                self._submit_zepp_step(params)
                return

            self._json_response({"error": "未知路径"}, status=404)

        def do_GET(self):
            self._handle_step()

        def do_POST(self):
            self._handle_step()

        def log_message(self, fmt, *args):  # pragma: no cover
            return

    httpd = ZeppThreadingHTTPServer((host, port), StepHandler)
    scheme = "http"
    if ssl_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=ssl_cert, keyfile=ssl_key)
        httpd.ssl_context = context
        scheme = "http+https"
    init_tool_log_db()
    init_baidu_share_job_db()
    init_device_binding_db()
    init_zepp_token_cache_db()
    get_or_create_daily_token()
    if QQ_LIKE_ENABLED:
        try:
            qq_like_service.start()
        except Exception as exc:
            qq_like_runtime_state["startup_error"] = str(exc)[:300]
            qq_like_service.enabled = False
            print(f"QQ 互赞执行器启动失败，原有工具继续运行: {exc}")
    if BAIDU_SHARE_WORKER_ENABLED:
        threading.Thread(target=baidu_share_worker_loop, name="baidu-share-worker", daemon=True).start()
    print(f"Serving on {scheme}://{host}:{port}")
    try:
        httpd.serve_forever()
    finally:
        qq_like_service.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zepp_API Python 重构版")
    parser.add_argument("--user", help="账号（手机号或邮箱）")
    parser.add_argument("--pwd", help="账号密码")
    parser.add_argument("--step", type=int, help="步数")
    parser.add_argument("--serve", action="store_true", help="启动 HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1", help="服务监听地址")
    parser.add_argument("--port", type=int, default=8000, help="服务监听端口")
    parser.add_argument("--ssl-cert", help="HTTPS 证书文件路径；不传则启动 HTTP")
    parser.add_argument("--ssl-key", help="HTTPS 私钥文件路径；证书包含私钥时可不传")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.serve or not all([args.user, args.pwd, args.step]):
        _run_http_server(host=args.host, port=args.port, ssl_cert=args.ssl_cert, ssl_key=args.ssl_key)
        return

    _run_cli(user=args.user, pwd=args.pwd, step=args.step)


if __name__ == "__main__":
    main()
