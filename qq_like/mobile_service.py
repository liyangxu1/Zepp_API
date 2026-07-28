"""手机端 QQ 互赞 API 的窄业务服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .mobile_store import MobileQQLikeStore, MobileQQLikeStoreError


class MobileQQLikeService:
    """手机执行节点的注册、心跳、任务领取和结果回传。"""

    def __init__(
        self,
        store: MobileQQLikeStore,
        *,
        enabled: bool = True,
        max_batch_size: int = 8,
        lease_seconds: int = 600,
        likes_per_target: int = 10,
    ) -> None:
        self.store = store
        self.enabled = bool(enabled)
        self.max_batch_size = max(1, min(int(max_batch_size), 50))
        self.lease_seconds = max(15, min(int(lease_seconds), 600))
        self.likes_per_target = max(1, min(int(likes_per_target), 10))

    @classmethod
    def from_path(
        cls,
        db_path: Path,
        *,
        enabled: bool,
        max_batch_size: int = 8,
        lease_seconds: int = 600,
        likes_per_target: int = 10,
    ) -> "MobileQQLikeService":
        return cls(
            MobileQQLikeStore(db_path),
            enabled=enabled,
            max_batch_size=max_batch_size,
            lease_seconds=lease_seconds,
            likes_per_target=likes_per_target,
        )

    def start(self) -> None:
        if self.enabled:
            self.store.init_schema()
            self.store.expire_leases()

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise MobileQQLikeStoreError("QQ 互赞工具尚未启用")

    @staticmethod
    def _public_account(account: Dict[str, object]) -> Dict[str, object]:
        return {
            "id": str(account["id"]),
            "qq_number": str(account["qq_number"]),
        }

    def register(
        self,
        *,
        qq_number: str,
        install_id: str,
        app_version: str,
        access_token: str = "",
    ) -> Dict[str, Any]:
        self._require_enabled()
        registered = self.store.register(
            qq_number,
            install_id=install_id,
            app_version=app_version,
            access_token=access_token,
        )
        payload: Dict[str, Any] = {
            "status": "success",
            "created": bool(registered["created"]),
            "rebound": bool(registered["rebound"]),
            "device": self._public_account(registered["account"]),
            "business_date": self.store.business_date(),
            "privacy": "服务器不接收或保存 QQ 登录凭证，只保存手机互赞令牌的 SHA-256 哈希。",
            "access_token": str(registered["access_token"] or access_token),
        }
        return payload

    def heartbeat(self, access_token: str) -> Dict[str, Any]:
        self._require_enabled()
        account = self.store.authenticate(access_token)
        account = self.store.heartbeat(str(account["id"]))
        self.store.expire_leases()
        return {
            "status": "success",
            "device": self._public_account(account),
            "business_date": self.store.business_date(),
            "tasks": self.store.daily_summary(str(account["id"])),
        }

    def lease(
        self,
        access_token: str,
        *,
        requested_limit: object = None,
    ) -> Dict[str, Any]:
        self._require_enabled()
        account = self.store.authenticate(access_token)
        try:
            limit = int(requested_limit) if requested_limit not in (None, "") else self.max_batch_size
        except (TypeError, ValueError):
            raise MobileQQLikeStoreError("任务批量大小必须是整数")
        limit = max(1, min(limit, self.max_batch_size))
        leased = self.store.lease_tasks(
            str(account["id"]),
            limit=limit,
            lease_seconds=self.lease_seconds,
            requested_times=self.likes_per_target,
        )
        return {
            "status": "success",
            "account_id": str(account["id"]),
            **leased,
        }

    def result(
        self,
        access_token: str,
        *,
        task_id: str,
        lease_token: str,
        outcome: str,
        idempotency_key: str,
        result_code: str = "",
        result_message: str = "",
    ) -> Dict[str, Any]:
        self._require_enabled()
        account = self.store.authenticate(access_token)
        task = self.store.record_result(
            str(account["id"]),
            task_id=task_id,
            lease_token=lease_token,
            outcome=outcome,
            idempotency_key=idempotency_key,
            result_code=result_code,
            result_message=result_message,
        )
        return {
            "status": "success",
            "task": task,
            "business_date": self.store.business_date(),
            "tasks": self.store.daily_summary(str(account["id"])),
        }

    def admin_overview(self) -> Dict[str, Any]:
        self._require_enabled()
        return {
            "status": "success",
            "mobile_enabled": True,
            **self.store.admin_overview(),
        }

    def admin_upsert_allowlist(
        self,
        *,
        qq_number: str,
        enabled: object = True,
        note: str = "",
    ) -> Dict[str, Any]:
        self._require_enabled()
        if isinstance(enabled, str):
            enabled_value = enabled.strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
        else:
            enabled_value = bool(enabled)
        item = self.store.upsert_allowlist(
            qq_number,
            enabled=enabled_value,
            note=note,
        )
        return {
            "status": "success",
            "allowlist_item": item,
            **self.store.admin_overview(),
        }

    def admin_account_action(
        self,
        *,
        qq_number: str,
        action: str,
    ) -> Dict[str, Any]:
        self._require_enabled()
        item = self.store.set_account_action(
            qq_number,
            action=action,
        )
        return {
            "status": "success",
            "account": item,
            **self.store.admin_overview(),
        }
