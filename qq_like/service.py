"""QQ 互赞的登录编排、公开数据投影和后台点赞执行。"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .napcat import (
    DockerNapCatRuntime,
    ManagedNapCatContainer,
    NapCatError,
    NapCatProtocolError,
    NapCatRuntimeBusy,
    NapCatSession,
    NapCatWebUICredentialError,
    NapCatWebUIRateLimitError,
)
from .store import QQLikeStore, QQLikeStoreError


LOGIN_LEASE_NAME = "qq-like-napcat-runtime"
LOGIN_ENDED_MESSAGE = "扫码任务已结束，需要重新扫码"
QR_PENDING_MESSAGE = "环境已启动，正在生成二维码"
WEBUI_RATE_LIMIT_RETRY_SECONDS = 60
RATE_LIMIT_MAX_KEYS = 4096
RATE_LIMIT_RETENTION_SECONDS = 3600
REQUEST_STATUS_LABELS = {
    "waiting_source": "等待可用账号",
    "assigned": "等待执行",
    "running": "正在点赞",
    "succeeded": "已完成",
    "failed": "执行失败",
    "uncertain": "结果待确认",
    "canceled": "已取消",
}
CONTRIBUTOR_STATUS_LABELS = {
    "pending_login": "等待扫码",
    "active": "贡献账号在线",
    "offline": "登录态失效",
    "paused": "已暂停",
    "revoked": "已停止贡献",
}


class QQMutualLikeServiceError(ValueError):
    """互赞服务可以直接展示给用户的业务错误。"""


@dataclass
class ActiveLogin:
    """当前唯一允许存在的扫码登录任务。"""

    contributor_id: str
    owner_id: str
    session: NapCatSession
    webui: Any
    started_at: float
    expires_at: float
    login_state: str = "waiting_scan"
    login_error: str = ""
    last_probe_at: float = 0.0
    next_probe_at: float = 0.0
    qr_revision: str = ""
    webui_reauth_attempted: bool = False
    last_logged_error: str = ""


def mask_qq_number(qq_number: str) -> str:
    value = str(qq_number or "").strip()
    if len(value) <= 4:
        return value or "未登录"
    prefix_length = min(3, max(1, len(value) - 4))
    return f"{value[:prefix_length]}{'*' * (len(value) - prefix_length - 3)}{value[-3:]}"


class QQMutualLikeService:
    """将 SQLite 调度和隔离 NapCat 驱动组合成窄业务接口。"""

    def __init__(
        self,
        store: QQLikeStore,
        runtime: DockerNapCatRuntime,
        *,
        enabled: bool = True,
        login_timeout_seconds: int = 300,
        worker_interval_seconds: float = 3.0,
        max_contributors: int = 20,
        pending_retention_hours: int = 24,
        time_factory: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.enabled = enabled
        self.login_timeout_seconds = max(60, min(int(login_timeout_seconds), 600))
        self.worker_interval_seconds = max(0.2, float(worker_interval_seconds))
        self.max_contributors = max(1, min(int(max_contributors), 200))
        self.pending_retention_hours = max(
            1,
            min(int(pending_retention_hours), 24 * 30),
        )
        self.time_factory = time_factory
        self.sleep = sleep
        self._login_lock = threading.RLock()
        self._active_login: Optional[ActiveLogin] = None
        self._rate_lock = threading.Lock()
        self._rate_events: Dict[str, List[float]] = {}
        self._rate_last_cleanup = 0.0
        self._worker_wakeup = threading.Event()
        self._worker_stop = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._runtime_retry_not_before = 0.0

    @classmethod
    def from_paths(
        cls,
        *,
        db_path: Path,
        data_root: Path,
        enabled: bool,
        webui_port: int = 16199,
        onebot_port: int = 16100,
        max_contributors: int = 20,
        pending_retention_hours: int = 24,
    ) -> "QQMutualLikeService":
        return cls(
            QQLikeStore(db_path),
            DockerNapCatRuntime(
                data_root,
                webui_port=webui_port,
                onebot_port=onebot_port,
            ),
            enabled=enabled,
            max_contributors=max_contributors,
            pending_retention_hours=pending_retention_hours,
        )

    def start(self) -> None:
        if not self.enabled:
            return
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self.store.init_schema()
        self._recover_managed_runtime()
        self._mark_orphaned_pending_logins()
        self._cleanup_stale_pending_contributors()
        self.store.recover_interrupted_requests()
        self._worker_stop.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="qq-like-worker",
            daemon=True,
        )
        self._worker_thread.start()

    def stop(self) -> None:
        self._worker_stop.set()
        self._worker_wakeup.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        if self.enabled:
            self.runtime.stop_all_managed()
        self._worker_thread = None

    def start_login(
        self,
        *,
        access_token: str = "",
        remote_addr: str = "",
        user_agent: str = "",
    ) -> Dict[str, Any]:
        self._require_enabled()
        fingerprint = str(remote_addr or "unknown").strip() or "unknown"
        self._check_rate_limit("login", fingerprint, limit=4, window_seconds=600)
        with self._login_lock:
            self._cleanup_expired_login_locked()
            if self._active_login is not None:
                if access_token:
                    contributor = self.store.authenticate(access_token)
                    if contributor["id"] == self._active_login.contributor_id:
                        return self._login_payload(contributor, created=None)
                raise QQMutualLikeServiceError("另一个 QQ 正在扫码登录，请稍后再试")

            created: Optional[Dict[str, str]] = None
            contributor: Dict[str, Any]
            if access_token:
                contributor = self.store.authenticate(access_token)
            else:
                self._cleanup_stale_pending_contributors()
                if (
                    self._count_capacity_contributors()
                    >= self.max_contributors
                ):
                    raise QQMutualLikeServiceError(
                        "贡献账号数量已达当前服务器上限，请联系管理员"
                    )
                created = self.store.create_contributor()
                contributor = self.store.get_contributor(created["contributor_id"]) or {}
            contributor_id = str(contributor.get("id") or "")
            if not contributor_id:
                raise QQMutualLikeServiceError("创建贡献账号失败")
            owner_id = f"login:{contributor_id}:{secrets.token_hex(6)}"
            if not self.store.acquire_runtime_lease(
                lease_name=LOGIN_LEASE_NAME,
                owner_id=owner_id,
                ttl_seconds=self.login_timeout_seconds + 30,
            ):
                if created:
                    self.store.revoke_contributor(contributor_id)
                raise QQMutualLikeServiceError("登录通道正在使用，请稍后再试")

            try:
                prepared_session = self.runtime.prepare_session(contributor_id)
                self.runtime.clear_qr_code(prepared_session)
                session = self.runtime.start(contributor_id)
                self._log_login_event(contributor_id, "容器已启动")
                webui = self.runtime.wait_for_webui(
                    session,
                    attempts=45,
                    interval_seconds=1,
                )
                self._log_login_event(contributor_id, "WebUI 已认证")
                webui.request_qr_code()
                qr_revision = ""
                qr_error = ""
                try:
                    _, qr_revision = self.runtime.wait_for_qr_code(
                        session,
                        attempts=30,
                        interval_seconds=0.1,
                    )
                    self._log_login_event(contributor_id, "二维码已就绪")
                except NapCatError:
                    qr_error = QR_PENDING_MESSAGE
                now = self.time_factory()
                self._active_login = ActiveLogin(
                    contributor_id=contributor_id,
                    owner_id=owner_id,
                    session=session,
                    webui=webui,
                    started_at=now,
                    expires_at=now + self.login_timeout_seconds,
                    login_error=qr_error,
                    qr_revision=qr_revision,
                )
                self.store.update_contributor_status(
                    contributor_id,
                    "pending_login",
                )
                contributor = self.store.get_contributor(contributor_id) or contributor
            except Exception:
                self._stop_runtime_safely(contributor_id)
                self.store.release_runtime_lease(
                    lease_name=LOGIN_LEASE_NAME,
                    owner_id=owner_id,
                )
                if created:
                    self.store.revoke_contributor(contributor_id)
                    self._delete_session_safely(contributor_id)
                raise

            self._worker_wakeup.set()
            return self._login_payload(
                contributor,
                created=created,
            )

    def read_login_qr(self, access_token: str) -> bytes:
        return self.read_login_qr_with_revision(access_token)[0]

    def read_login_qr_with_revision(
        self,
        access_token: str,
    ) -> tuple[bytes, str]:
        self._require_enabled()
        contributor = self.store.authenticate(access_token)
        with self._login_lock:
            login = self._require_active_login_locked(str(contributor["id"]))
            payload, revision = self.runtime.read_qr_code_png_with_revision(
                login.session
            )
            if revision != login.qr_revision:
                self._set_qr_revision_locked(login, revision)
            return payload, revision

    def refresh_login_qr(self, access_token: str) -> Dict[str, Any]:
        self._require_enabled()
        contributor = self.store.authenticate(access_token)
        with self._login_lock:
            login = self._require_active_login_locked(str(contributor["id"]))
            previous_revision = login.qr_revision
            self.runtime.clear_qr_code(login.session)
            login.webui.refresh_qr_code()
            login.webui.request_qr_code()
            try:
                _, revision = self.runtime.wait_for_qr_code(
                    login.session,
                    previous_revision=previous_revision,
                    attempts=30,
                    interval_seconds=0.1,
                )
                self._set_qr_revision_locked(login, revision)
                login.login_error = ""
            except NapCatError:
                login.qr_revision = ""
                login.login_error = QR_PENDING_MESSAGE
            now = self.time_factory()
            login.started_at = now
            login.expires_at = now + self.login_timeout_seconds
            login.login_state = "waiting_scan"
            login.next_probe_at = 0.0
            self._worker_wakeup.set()
            return self._login_payload(contributor, created=None)

    def poll_login(self, access_token: str) -> Dict[str, Any]:
        self._require_enabled()
        contributor = self.store.authenticate(access_token)
        contributor_id = str(contributor["id"])
        if contributor["status"] == "active":
            return {
                "status": "success",
                "login_state": "active",
                "dashboard": self.dashboard(access_token),
            }
        with self._login_lock:
            self._cleanup_expired_login_locked()
            if (
                self._active_login is not None
                and self._active_login.contributor_id == contributor_id
            ):
                return self._login_payload(contributor, created=None)
            contributor = self.store.get_contributor(contributor_id) or contributor
            return {
                "status": "success",
                "login_state": "not_started",
                "contributor": self._public_contributor(contributor),
            }

    def dashboard(self, access_token: str) -> Dict[str, Any]:
        contributor = self.store.authenticate(access_token)
        return self._dashboard_for(contributor)

    def recover_access(
        self,
        contributor_id: str,
        recovery_code: str,
        *,
        remote_addr: str = "",
    ) -> Dict[str, str]:
        self._check_rate_limit(
            "recover",
            (
                f"{str(remote_addr or 'unknown').strip() or 'unknown'}"
                f"|{str(contributor_id or '').strip()}"
            ),
            limit=5,
            window_seconds=600,
        )
        access_token = self.store.recover_access(contributor_id, recovery_code)
        return {
            "contributor_id": contributor_id,
            "access_token": access_token,
        }

    def revoke(self, access_token: str) -> Dict[str, Any]:
        contributor = self.store.authenticate(access_token)
        contributor_id = str(contributor["id"])
        with self._login_lock:
            if (
                self._active_login is not None
                and self._active_login.contributor_id == contributor_id
            ):
                self._finish_login_locked(
                    self._active_login,
                    delete_session=True,
                )
            else:
                self.runtime.stop(contributor_id)
        self.store.revoke_contributor(contributor_id)
        self._delete_session_safely(contributor_id)
        return {
            "status": "success",
            "message": "已停止贡献并删除本工具保存的 QQ 登录信息",
        }

    def submit_request(
        self,
        *,
        access_token: str,
        target_qq: str,
        idempotency_key: str,
        remote_addr: str = "",
    ) -> Dict[str, Any]:
        contributor = self.store.authenticate(access_token)
        scoped_key = self._scoped_idempotency_key(
            "contributor",
            str(contributor["id"]),
            idempotency_key,
        )
        request = self.store.create_request(
            contributor_id=str(contributor["id"]),
            target_qq=target_qq,
            idempotency_key=scoped_key,
            remote_addr=remote_addr,
        )
        self._worker_wakeup.set()
        return self._public_request(request)

    def submit_admin_request(
        self,
        *,
        target_qq: str,
        idempotency_key: str,
        remote_addr: str = "",
    ) -> Dict[str, Any]:
        request = self.store.create_request(
            admin=True,
            target_qq=target_qq,
            idempotency_key=self._scoped_idempotency_key(
                "admin",
                "admin",
                idempotency_key,
            ),
            remote_addr=remote_addr,
        )
        self._worker_wakeup.set()
        return self._public_request(request, admin=True)

    def admin_overview(self) -> Dict[str, Any]:
        contributors = [
            self._public_contributor(item, admin=True)
            for item in self.store.list_contributors()
        ]
        requests = [
            self._public_request(item, admin=True)
            for item in self.store.list_requests(limit=100)
        ]
        return {
            "status": "success",
            "enabled": self.enabled,
            "capacity": {
                "retained_contributors": self.store.count_retained_contributors(),
                "max_contributors": self.max_contributors,
            },
            "active_login_contributor_id": (
                self._active_login.contributor_id if self._active_login else ""
            ),
            "contributors": contributors,
            "requests": requests,
        }

    def worker_once(self) -> bool:
        if not self.enabled:
            return False
        if self.time_factory() < self._runtime_retry_not_before:
            return False
        self.store.assign_pending_requests()
        request = self.store.next_assigned_request()
        if request is None:
            return False
        request_id = str(request["id"])
        source_id = str(request["source_contributor_id"] or "")
        owner_id = f"request:{request_id}:{secrets.token_hex(6)}"
        if not source_id:
            return False
        if not self.store.acquire_runtime_lease(
            lease_name=LOGIN_LEASE_NAME,
            owner_id=owner_id,
            ttl_seconds=240,
        ):
            return False

        began = False
        session: Optional[NapCatSession] = None
        try:
            source = self.store.get_contributor(source_id)
            if source is None or source.get("status") != "active":
                self.store.release_assignment(
                    request_id,
                    source_id,
                    reason="来源账号当前不可用",
                )
                return True
            expected_qq = str(source.get("qq_number") or "")
            session = self.runtime.start(source_id)
            self._runtime_retry_not_before = 0.0
            client = self.runtime.onebot_client(session)
            login_info = self._wait_for_onebot_login(
                client,
                owner_id=owner_id,
                attempts=30,
            )
            actual_qq = str(login_info.get("user_id") or "")
            if actual_qq != expected_qq:
                raise NapCatProtocolError("登录 QQ 与贡献账号不一致")
            self.store.mark_contributor_active(source_id, actual_qq)
            began = self.store.begin_request(request_id, source_id)
            if not began:
                return True
            client.send_like(
                str(request["target_qq"]),
                times=int(request["requested_times"]),
            )
            self.store.finish_request(
                request_id,
                status="succeeded",
                result_code="ok",
                result_message=f"已执行 {request['requested_times']} 次点赞",
            )
            return True
        except Exception as exc:
            message = str(exc or "执行失败")[:500]
            if began:
                self.store.finish_request(
                    request_id,
                    status="uncertain",
                    result_code="execution_uncertain",
                    result_message=message,
                )
            else:
                if session is not None:
                    try:
                        self.store.update_contributor_status(
                            source_id,
                            "offline",
                            error=message,
                        )
                    except QQLikeStoreError:
                        pass
                else:
                    self._runtime_retry_not_before = self.time_factory() + 30
                self.store.release_assignment(
                    request_id,
                    source_id,
                    reason=message,
                )
            return True
        finally:
            if session is not None:
                self._stop_runtime_safely(source_id)
            self.store.release_runtime_lease(
                lease_name=LOGIN_LEASE_NAME,
                owner_id=owner_id,
            )

    def _worker_loop(self) -> None:
        while not self._worker_stop.is_set():
            try:
                with self._login_lock:
                    self._advance_active_login_locked()
                    login_running = self._active_login is not None
                did_work = False if login_running else self.worker_once()
            except Exception as exc:
                print(f"QQ 互赞 worker 异常: {exc}")
                did_work = False
            if did_work:
                continue
            self._worker_wakeup.wait(self.worker_interval_seconds)
            self._worker_wakeup.clear()

    def _wait_for_onebot_login(
        self,
        client: Any,
        *,
        owner_id: str,
        attempts: int,
    ) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for _ in range(max(1, attempts)):
            self.store.acquire_runtime_lease(
                lease_name=LOGIN_LEASE_NAME,
                owner_id=owner_id,
                ttl_seconds=240,
            )
            try:
                status = client.get_status()
                if status.get("online") is False or status.get("good") is False:
                    raise NapCatProtocolError("QQ 当前不在线")
                return client.get_login_info()
            except NapCatError as exc:
                last_error = exc
                self.sleep(2)
        raise NapCatProtocolError(
            f"QQ 登录状态检查超时: {last_error or '未知错误'}"
        )

    def _dashboard_for(self, contributor: Dict[str, Any]) -> Dict[str, Any]:
        contributor_id = str(contributor["id"])
        summary = self.store.contributor_daily_summary(contributor_id)
        requests = [
            self._public_request(item)
            for item in self.store.list_requests(
                contributor_id=contributor_id,
                limit=30,
            )
        ]
        return {
            "status": "success",
            "contributor": self._public_contributor(contributor),
            "quota": {
                "daily_total": 1,
                "daily_remaining": max(0, 1 - summary["request_used"]),
                "contributed_today": summary["source_used"],
                "queued_requests": summary["queued_requests"],
            },
            "requests": requests,
        }

    def _public_request(
        self,
        request: Dict[str, Any],
        *,
        admin: bool = False,
    ) -> Dict[str, Any]:
        source = None
        if request.get("source_contributor_id"):
            source = self.store.get_contributor(
                str(request["source_contributor_id"])
            )
        public = {
            "request_id": request["id"],
            "target_qq": request["target_qq"],
            "source_qq_masked": mask_qq_number(
                str((source or {}).get("qq_number") or "")
            ),
            "requested_times": request["requested_times"],
            "status": request["status"],
            "status_label": REQUEST_STATUS_LABELS.get(
                str(request["status"]),
                str(request["status"]),
            ),
            "result_message": request.get("result_message") or "",
            "created_at": request["created_at"],
            "started_at": request.get("started_at") or "",
            "finished_at": request.get("finished_at") or "",
        }
        if admin:
            public["requester_kind"] = request["requester_kind"]
            public["contributor_id"] = request.get("contributor_id") or ""
        return public

    def _public_contributor(
        self,
        contributor: Dict[str, Any],
        *,
        admin: bool = False,
    ) -> Dict[str, Any]:
        public = {
            "contributor_id": contributor["id"],
            "qq_masked": mask_qq_number(str(contributor.get("qq_number") or "")),
            "status": contributor["status"],
            "status_label": CONTRIBUTOR_STATUS_LABELS.get(
                str(contributor["status"]),
                str(contributor["status"]),
            ),
            "last_health_at": contributor.get("last_health_at") or "",
            "last_error": contributor.get("last_error") or "",
        }
        if admin:
            public["created_at"] = contributor.get("created_at") or ""
            public["updated_at"] = contributor.get("updated_at") or ""
        return public

    def _login_payload(
        self,
        contributor: Dict[str, Any],
        *,
        created: Optional[Dict[str, str]],
    ) -> Dict[str, Any]:
        login = self._active_login
        contributor_id = str(contributor.get("id") or "")
        owns_login = bool(
            login is not None and login.contributor_id == contributor_id
        )
        payload: Dict[str, Any] = {
            "status": "success",
            "login_state": login.login_state if owns_login else "not_started",
            "login_error": login.login_error if owns_login else "",
            "contributor": self._public_contributor(contributor),
            "qr_endpoint": "/api/tools/qq-like/login/qr",
            "qr_revision": login.qr_revision if owns_login else "",
            "expires_in_seconds": (
                max(0, int(login.expires_at - self.time_factory()))
                if owns_login
                else 0
            ),
        }
        if created:
            payload.update(
                {
                    "access_token": created["access_token"],
                    "recovery_code": created["recovery_code"],
                }
            )
        return payload

    def _require_active_login_locked(self, contributor_id: str) -> ActiveLogin:
        self._cleanup_expired_login_locked()
        if (
            self._active_login is None
            or self._active_login.contributor_id != contributor_id
        ):
            raise QQMutualLikeServiceError("当前没有进行中的扫码登录")
        return self._active_login

    def _cleanup_expired_login_locked(self) -> None:
        if (
            self._active_login is not None
            and self._active_login.expires_at <= self.time_factory()
        ):
            try:
                self.store.update_contributor_status(
                    self._active_login.contributor_id,
                    "pending_login",
                    error=LOGIN_ENDED_MESSAGE,
                )
            except QQLikeStoreError:
                pass
            self._log_login_event(
                self._active_login.contributor_id,
                "扫码任务已超时",
            )
            self._finish_login_locked(self._active_login)

    def _advance_active_login_locked(
        self,
        contributor_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        self._cleanup_expired_login_locked()
        login = self._active_login
        if login is None:
            return None
        if contributor_id and login.contributor_id != contributor_id:
            return None
        contributor = self.store.get_contributor(login.contributor_id)
        if contributor is None or contributor.get("status") == "revoked":
            self._finish_login_locked(login, delete_session=True)
            return None
        if not self.store.acquire_runtime_lease(
            lease_name=LOGIN_LEASE_NAME,
            owner_id=login.owner_id,
            ttl_seconds=self.login_timeout_seconds + 30,
        ):
            self._set_login_error_locked(login, "登录运行环境正在恢复")
            return self._login_payload(contributor, created=None)
        self._refresh_qr_revision_locked(login)
        now = self.time_factory()
        if login.next_probe_at > now:
            return self._login_payload(contributor, created=None)
        login.last_probe_at = now
        try:
            status = login.webui.check_login_status()
        except NapCatWebUIRateLimitError:
            login.next_probe_at = now + WEBUI_RATE_LIMIT_RETRY_SECONDS
            self._set_login_error_locked(
                login,
                "登录状态检查暂时受限，后台将在一分钟后自动重试",
            )
            return self._login_payload(contributor, created=None)
        except NapCatWebUICredentialError as exc:
            if login.webui_reauth_attempted:
                self._set_login_error_locked(login, str(exc))
                return self._login_payload(contributor, created=None)
            login.webui_reauth_attempted = True
            try:
                login.webui.login()
                status = login.webui.check_login_status()
            except NapCatWebUIRateLimitError:
                login.next_probe_at = now + WEBUI_RATE_LIMIT_RETRY_SECONDS
                self._set_login_error_locked(
                    login,
                    "登录状态检查暂时受限，后台将在一分钟后自动重试",
                )
                return self._login_payload(contributor, created=None)
            except NapCatError as reauth_exc:
                self._set_login_error_locked(login, str(reauth_exc))
                return self._login_payload(contributor, created=None)
        except NapCatError as exc:
            self._set_login_error_locked(login, str(exc))
            return self._login_payload(contributor, created=None)
        if not bool(status.get("isLogin")):
            login.login_state = "waiting_scan"
            login_error = str(status.get("loginError") or "").strip()[:200]
            if login_error:
                self._set_login_error_locked(login, login_error)
            elif login.qr_revision:
                login.login_error = ""
                login.last_logged_error = ""
            return self._login_payload(contributor, created=None)
        if login.login_state != "finalizing":
            login.login_state = "finalizing"
            login.login_error = ""
            login.last_logged_error = ""
            self._log_login_event(login.contributor_id, "扫码已确认")
        try:
            onebot = self.runtime.onebot_client(login.session)
            onebot_status = onebot.get_status()
            if not bool(onebot_status.get("online")) or not bool(
                onebot_status.get("good")
            ):
                raise NapCatProtocolError("QQ 当前尚未完全上线")
            login_info = onebot.get_login_info()
        except NapCatError as exc:
            self._set_login_error_locked(login, str(exc))
            return self._login_payload(contributor, created=None)
        qq_number = str(login_info.get("user_id") or "")
        try:
            self.runtime.record_login(login.contributor_id, qq_number)
            contributor = self.store.mark_contributor_active(
                login.contributor_id,
                qq_number,
            )
        except NapCatError as exc:
            self._set_login_error_locked(login, str(exc))
            return self._login_payload(contributor, created=None)
        except QQLikeStoreError:
            self._finish_login_locked(login, delete_session=True)
            self.store.revoke_contributor(login.contributor_id)
            raise
        self._log_login_event(login.contributor_id, "账号已激活")
        self._finish_login_locked(login)
        self._worker_wakeup.set()
        return {
            "status": "success",
            "login_state": "active",
            "dashboard": self._dashboard_for(contributor),
        }

    def _refresh_qr_revision_locked(self, login: ActiveLogin) -> None:
        try:
            _, revision = self.runtime.read_qr_code_png_with_revision(
                login.session
            )
        except NapCatError:
            return
        if revision != login.qr_revision:
            self._set_qr_revision_locked(login, revision)

    def _set_qr_revision_locked(
        self,
        login: ActiveLogin,
        revision: str,
    ) -> None:
        normalized_revision = str(revision or "").strip()
        if not normalized_revision or normalized_revision == login.qr_revision:
            return
        is_replacement = bool(login.qr_revision)
        login.qr_revision = normalized_revision
        if login.login_state == "waiting_scan":
            login.login_error = ""
            login.last_logged_error = ""
        self._log_login_event(
            login.contributor_id,
            "二维码已换新" if is_replacement else "二维码已就绪",
        )

    def _set_login_error_locked(
        self,
        login: ActiveLogin,
        detail: str,
    ) -> None:
        normalized_detail = str(detail or "")[:200]
        login.login_error = normalized_detail
        if normalized_detail and normalized_detail != login.last_logged_error:
            login.last_logged_error = normalized_detail
            self._log_login_event(
                login.contributor_id,
                "状态暂不可用",
            )

    def _recover_managed_runtime(self) -> None:
        lease = self.store.get_runtime_lease(LOGIN_LEASE_NAME)
        owner_id = str((lease or {}).get("owner_id") or "")
        containers = self.runtime.managed_containers()
        if not containers:
            owner_contributor_id = self._owner_login_contributor_id(owner_id)
            if owner_contributor_id:
                contributor = self.store.get_contributor(owner_contributor_id)
                if contributor and contributor.get("status") == "pending_login":
                    try:
                        self.store.update_contributor_status(
                            owner_contributor_id,
                            "pending_login",
                            error=LOGIN_ENDED_MESSAGE,
                        )
                    except QQLikeStoreError:
                        pass
            if owner_id:
                self.store.release_runtime_lease(
                    lease_name=LOGIN_LEASE_NAME,
                    owner_id=owner_id,
                )
            return
        contributor_id = ""
        delete_session_after_stop = False
        try:
            if len(containers) != 1:
                self._mark_recovery_failed(
                    containers,
                    "检测到多个遗留登录环境，需要重新扫码",
                    owner_id=owner_id,
                )
                return
            container = containers[0]
            contributor_id = self._resolve_recovery_contributor(
                container,
                owner_id,
            )
            contributor = (
                self.store.get_contributor(contributor_id)
                if contributor_id
                else None
            )
            if (
                contributor is None
                or contributor.get("status") != "pending_login"
                or (owner_id and not owner_id.startswith("login:"))
            ):
                return
            session = self.runtime.prepare_session(contributor_id)
            webui = self.runtime.wait_for_webui(
                session,
                attempts=2,
                interval_seconds=0.5,
            )
            if not bool(webui.check_login_status().get("isLogin")):
                raise NapCatProtocolError("扫码登录尚未确认")
            onebot = self.runtime.onebot_client(session)
            status = onebot.get_status()
            if not bool(status.get("online")) or not bool(status.get("good")):
                raise NapCatProtocolError("QQ 当前尚未完全上线")
            login_info = onebot.get_login_info()
            qq_number = str(login_info.get("user_id") or "")
            self.runtime.record_login(contributor_id, qq_number)
            self.store.mark_contributor_active(contributor_id, qq_number)
            self._log_login_event(contributor_id, "服务重启后补偿激活")
        except QQLikeStoreError as exc:
            if contributor_id:
                try:
                    self.store.revoke_contributor(contributor_id)
                    delete_session_after_stop = True
                except QQLikeStoreError:
                    pass
            print(f"恢复 QQ 互赞扫码账号失败: {exc}")
        except NapCatError as exc:
            if contributor_id:
                try:
                    self.store.update_contributor_status(
                        contributor_id,
                        "pending_login",
                        error=f"{LOGIN_ENDED_MESSAGE}：{exc}",
                    )
                except QQLikeStoreError:
                    pass
        finally:
            self.runtime.stop_all_managed()
            if delete_session_after_stop and contributor_id:
                self._delete_session_safely(contributor_id)
            if owner_id:
                self.store.release_runtime_lease(
                    lease_name=LOGIN_LEASE_NAME,
                    owner_id=owner_id,
                )

    def _resolve_recovery_contributor(
        self,
        container: ManagedNapCatContainer,
        owner_id: str,
    ) -> str:
        owner_contributor_id = self._owner_login_contributor_id(owner_id)
        if container.contributor_id:
            if (
                owner_contributor_id
                and container.contributor_id != owner_contributor_id
            ):
                return ""
            return container.contributor_id
        matches = [
            str(item.get("id") or "")
            for item in self.store.list_contributors(limit=500)
            if container.name
            == f"qq-like-napcat-{str(item.get('id') or '')[-12:]}"
        ]
        if len(matches) != 1:
            return ""
        if owner_contributor_id and matches[0] != owner_contributor_id:
            return ""
        return matches[0]

    @staticmethod
    def _owner_login_contributor_id(owner_id: str) -> str:
        owner_parts = owner_id.split(":")
        if len(owner_parts) >= 2 and owner_parts[0] == "login":
            return owner_parts[1]
        return ""

    def _mark_recovery_failed(
        self,
        containers: List[ManagedNapCatContainer],
        message: str,
        *,
        owner_id: str = "",
    ) -> None:
        contributor_ids = {
            item.contributor_id
            for item in containers
            if item.contributor_id
        }
        owner_contributor_id = self._owner_login_contributor_id(owner_id)
        if owner_contributor_id:
            contributor_ids.add(owner_contributor_id)
        for contributor_id in contributor_ids:
            contributor = self.store.get_contributor(contributor_id)
            if contributor and contributor.get("status") == "pending_login":
                try:
                    self.store.update_contributor_status(
                        contributor_id,
                        "pending_login",
                        error=message,
                    )
                except QQLikeStoreError:
                    pass

    def _finish_login_locked(
        self,
        login: ActiveLogin,
        *,
        delete_session: bool = False,
    ) -> None:
        self._stop_runtime_safely(login.contributor_id)
        self.store.release_runtime_lease(
            lease_name=LOGIN_LEASE_NAME,
            owner_id=login.owner_id,
        )
        if delete_session:
            self._delete_session_safely(login.contributor_id)
        if (
            self._active_login is not None
            and self._active_login.contributor_id == login.contributor_id
        ):
            self._active_login = None
        self._log_login_event(login.contributor_id, "运行环境已停止")

    def _stop_runtime_safely(self, contributor_id: str) -> None:
        try:
            self.runtime.stop(contributor_id)
        except NapCatError as exc:
            print(f"停止 QQ 互赞 NapCat 失败: {exc}")

    def _delete_session_safely(self, contributor_id: str) -> None:
        last_error: Optional[NapCatError] = None
        for attempt in range(5):
            try:
                self.runtime.delete_session(contributor_id)
                return
            except NapCatError as exc:
                last_error = exc
                if "仍在运行" not in str(exc) or attempt >= 4:
                    break
                self.sleep(0.2)
        print(f"删除 QQ 互赞登录信息失败: {last_error}")

    def _cleanup_stale_pending_contributors(self) -> int:
        cleaned = 0
        for contributor in self.store.list_stale_pending_contributors(
            older_than_hours=self.pending_retention_hours,
        ):
            contributor_id = str(contributor.get("id") or "")
            if not contributor_id:
                continue
            try:
                self.store.update_contributor_status(
                    contributor_id,
                    "pending_login",
                    error=LOGIN_ENDED_MESSAGE,
                )
            except QQLikeStoreError as exc:
                print(f"清理过期 QQ 互赞扫码账号失败: {exc}")
                continue
            cleaned += 1
        return cleaned

    def _mark_orphaned_pending_logins(self) -> int:
        marked = 0
        for contributor in self.store.list_contributors(limit=500):
            if contributor.get("status") != "pending_login":
                continue
            contributor_id = str(contributor.get("id") or "")
            if not contributor_id:
                continue
            if str(contributor.get("last_error") or "") == LOGIN_ENDED_MESSAGE:
                continue
            try:
                self.store.update_contributor_status(
                    contributor_id,
                    "pending_login",
                    error=LOGIN_ENDED_MESSAGE,
                )
            except QQLikeStoreError:
                continue
            marked += 1
        return marked

    def _count_capacity_contributors(self) -> int:
        return sum(
            1
            for contributor in self.store.list_contributors(limit=500)
            if contributor.get("status") != "revoked"
            and not (
                contributor.get("status") == "pending_login"
                and not contributor.get("qq_number")
                and str(contributor.get("last_error") or "")
                == LOGIN_ENDED_MESSAGE
            )
        )

    @staticmethod
    def _log_login_event(contributor_id: str, event: str) -> None:
        reference = str(contributor_id or "")[-8:] or "unknown"
        print(f"QQ 互赞登录[{reference}]: {event}")

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise QQMutualLikeServiceError("QQ 互赞工具尚未启用")

    def _check_rate_limit(
        self,
        action: str,
        fingerprint: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> None:
        now = self.time_factory()
        key = f"{action}:{fingerprint}"
        with self._rate_lock:
            self._prune_rate_events_locked(now, incoming_key=key)
            recent = [
                value
                for value in self._rate_events.get(key, [])
                if now - value < window_seconds
            ]
            if len(recent) >= limit:
                raise QQMutualLikeServiceError("操作过于频繁，请稍后再试")
            recent.append(now)
            self._rate_events[key] = recent

    def _prune_rate_events_locked(
        self,
        now: float,
        *,
        incoming_key: str,
    ) -> None:
        should_scan = (
            now - self._rate_last_cleanup >= 60
            or len(self._rate_events) >= RATE_LIMIT_MAX_KEYS
        )
        if should_scan:
            cutoff = now - RATE_LIMIT_RETENTION_SECONDS
            for key, values in list(self._rate_events.items()):
                retained = [value for value in values if value >= cutoff]
                if retained:
                    self._rate_events[key] = retained
                else:
                    self._rate_events.pop(key, None)
            self._rate_last_cleanup = now
        if (
            incoming_key not in self._rate_events
            and len(self._rate_events) >= RATE_LIMIT_MAX_KEYS
        ):
            oldest_key = min(
                self._rate_events,
                key=lambda item: max(self._rate_events[item] or [0.0]),
            )
            self._rate_events.pop(oldest_key, None)

    @staticmethod
    def _scoped_idempotency_key(
        requester_kind: str,
        requester_id: str,
        idempotency_key: str,
    ) -> str:
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            raise QQMutualLikeServiceError("缺少请求 ID")
        if len(clean_key) > 80:
            raise QQMutualLikeServiceError("请求 ID 过长")
        return f"{requester_kind}:{requester_id}:{clean_key}"
