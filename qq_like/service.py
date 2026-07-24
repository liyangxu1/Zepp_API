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
    NapCatError,
    NapCatProtocolError,
    NapCatRuntimeBusy,
    NapCatSession,
)
from .store import QQLikeStore, QQLikeStoreError


LOGIN_LEASE_NAME = "qq-like-napcat-runtime"
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
    started_at: float
    expires_at: float


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
        time_factory: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.enabled = enabled
        self.login_timeout_seconds = max(60, min(int(login_timeout_seconds), 600))
        self.worker_interval_seconds = max(0.2, float(worker_interval_seconds))
        self.time_factory = time_factory
        self.sleep = sleep
        self._login_lock = threading.RLock()
        self._active_login: Optional[ActiveLogin] = None
        self._rate_lock = threading.Lock()
        self._rate_events: Dict[str, List[float]] = {}
        self._worker_wakeup = threading.Event()
        self._worker_stop = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

    @classmethod
    def from_paths(
        cls,
        *,
        db_path: Path,
        data_root: Path,
        enabled: bool,
        webui_port: int = 16199,
        onebot_port: int = 16100,
    ) -> "QQMutualLikeService":
        return cls(
            QQLikeStore(db_path),
            DockerNapCatRuntime(
                data_root,
                webui_port=webui_port,
                onebot_port=onebot_port,
            ),
            enabled=enabled,
        )

    def start(self) -> None:
        if not self.enabled:
            return
        self.store.init_schema()
        self.runtime.stop_all_managed()
        self.store.recover_interrupted_requests()
        if self._worker_thread and self._worker_thread.is_alive():
            return
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
        fingerprint = f"{remote_addr}|{user_agent[:120]}"
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
                session = self.runtime.start(contributor_id)
                webui = self.runtime.wait_for_webui(
                    session,
                    attempts=45,
                    interval_seconds=1,
                )
                webui.request_qr_code()
                now = self.time_factory()
                self._active_login = ActiveLogin(
                    contributor_id=contributor_id,
                    owner_id=owner_id,
                    session=session,
                    started_at=now,
                    expires_at=now + self.login_timeout_seconds,
                )
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

            return self._login_payload(
                contributor,
                created=created,
            )

    def read_login_qr(self, access_token: str) -> bytes:
        self._require_enabled()
        contributor = self.store.authenticate(access_token)
        with self._login_lock:
            login = self._require_active_login_locked(str(contributor["id"]))
            return self.runtime.read_qr_code_png(login.session)

    def refresh_login_qr(self, access_token: str) -> Dict[str, Any]:
        self._require_enabled()
        contributor = self.store.authenticate(access_token)
        with self._login_lock:
            login = self._require_active_login_locked(str(contributor["id"]))
            client = self.runtime.wait_for_webui(
                login.session,
                attempts=3,
                interval_seconds=0.5,
            )
            client.refresh_qr_code()
            client.request_qr_code()
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
                self._active_login is None
                or self._active_login.contributor_id != contributor_id
            ):
                return {
                    "status": "success",
                    "login_state": "not_started",
                    "contributor": self._public_contributor(contributor),
                }

            login = self._active_login
            self.store.acquire_runtime_lease(
                lease_name=LOGIN_LEASE_NAME,
                owner_id=login.owner_id,
                ttl_seconds=self.login_timeout_seconds + 30,
            )
            client = self.runtime.wait_for_webui(
                login.session,
                attempts=2,
                interval_seconds=0.5,
            )
            status = client.check_login_status()
            if bool(status.get("isLogin")):
                try:
                    login_info = self.runtime.onebot_client(
                        login.session
                    ).get_login_info()
                except NapCatError:
                    return {
                        "status": "success",
                        "login_state": "finalizing",
                        "contributor": self._public_contributor(contributor),
                    }
                qq_number = str(login_info["user_id"])
                try:
                    self.runtime.record_login(contributor_id, qq_number)
                    contributor = self.store.mark_contributor_active(
                        contributor_id,
                        qq_number,
                    )
                except QQLikeStoreError:
                    self._finish_login_locked(login, delete_session=True)
                    self.store.revoke_contributor(contributor_id)
                    raise
                self._finish_login_locked(login)
                self._worker_wakeup.set()
                return {
                    "status": "success",
                    "login_state": "active",
                    "dashboard": self._dashboard_for(contributor),
                }

            login_error = str(status.get("loginError") or "").strip()
            return {
                "status": "success",
                "login_state": "waiting_scan",
                "expires_in_seconds": max(
                    0,
                    int(login.expires_at - self.time_factory()),
                ),
                "login_error": login_error[:200],
                "contributor": self._public_contributor(contributor),
            }

    def dashboard(self, access_token: str) -> Dict[str, Any]:
        contributor = self.store.authenticate(access_token)
        return self._dashboard_for(contributor)

    def recover_access(self, contributor_id: str, recovery_code: str) -> Dict[str, str]:
        self._check_rate_limit(
            "recover",
            str(contributor_id or ""),
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
            "active_login_contributor_id": (
                self._active_login.contributor_id if self._active_login else ""
            ),
            "contributors": contributors,
            "requests": requests,
        }

    def worker_once(self) -> bool:
        if not self.enabled:
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
                try:
                    self.store.update_contributor_status(
                        source_id,
                        "offline",
                        error=message,
                    )
                except QQLikeStoreError:
                    pass
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
                    self._cleanup_expired_login_locked()
                did_work = self.worker_once()
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
        payload: Dict[str, Any] = {
            "status": "success",
            "login_state": "waiting_scan",
            "contributor": self._public_contributor(contributor),
            "qr_endpoint": "/api/tools/qq-like/login/qr",
            "expires_in_seconds": (
                max(0, int(login.expires_at - self.time_factory()))
                if login
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
            self._finish_login_locked(self._active_login)

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

    def _stop_runtime_safely(self, contributor_id: str) -> None:
        try:
            self.runtime.stop(contributor_id)
        except NapCatError as exc:
            print(f"停止 QQ 互赞 NapCat 失败: {exc}")

    def _delete_session_safely(self, contributor_id: str) -> None:
        try:
            self.runtime.delete_session(contributor_id)
        except NapCatError as exc:
            print(f"删除 QQ 互赞登录信息失败: {exc}")

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
            recent = [
                value
                for value in self._rate_events.get(key, [])
                if now - value < window_seconds
            ]
            if len(recent) >= limit:
                raise QQMutualLikeServiceError("操作过于频繁，请稍后再试")
            recent.append(now)
            self._rate_events[key] = recent

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
