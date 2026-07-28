"""手机端 QQ 互赞白名单、账号、每日有向任务和租约的数据层。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional


SHANGHAI_TZ = timezone(timedelta(hours=8))
QQ_NUMBER_MIN_LENGTH = 5
QQ_NUMBER_MAX_LENGTH = 12
MOBILE_TASK_STATUSES = {
    "queued",
    "leased",
    "succeeded",
    "failed",
    "uncertain",
}
MOBILE_FINAL_TASK_STATUSES = {"succeeded", "failed", "uncertain"}


class MobileQQLikeStoreError(ValueError):
    """手机互赞数据层可直接返回给客户端的业务错误。"""

    def __init__(
        self,
        message: str,
        *,
        http_status: int = 400,
        error_code: str = "mobile_like_invalid",
    ) -> None:
        super().__init__(message)
        self.http_status = int(http_status)
        self.error_code = str(error_code)


def _token_digest(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _normalize_qq_number(value: str) -> str:
    qq_number = str(value or "").strip()
    if not qq_number.isdigit():
        raise MobileQQLikeStoreError("QQ 号只能包含数字")
    if not QQ_NUMBER_MIN_LENGTH <= len(qq_number) <= QQ_NUMBER_MAX_LENGTH:
        raise MobileQQLikeStoreError(
            f"QQ 号长度必须在 {QQ_NUMBER_MIN_LENGTH}-{QQ_NUMBER_MAX_LENGTH} 位之间"
        )
    if qq_number.startswith("0"):
        raise MobileQQLikeStoreError("QQ 号不能以 0 开头")
    return qq_number


def _normalize_install_id(value: str) -> str:
    install_id = str(value or "").strip()
    if not install_id:
        raise MobileQQLikeStoreError("缺少安装 ID")
    if len(install_id) > 160:
        raise MobileQQLikeStoreError("安装 ID 过长")
    return install_id


class MobileQQLikeStore:
    """使用独立 SQLite 文件保存手机互赞调度状态。"""

    def __init__(
        self,
        db_path: Path,
        *,
        now_factory: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.now_factory = now_factory or (lambda: datetime.now(SHANGHAI_TZ))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _now(self) -> datetime:
        value = self.now_factory()
        if value.tzinfo is None:
            value = value.replace(tzinfo=SHANGHAI_TZ)
        return value.astimezone(SHANGHAI_TZ)

    @staticmethod
    def _time_text(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S")

    def _now_text(self) -> str:
        return self._time_text(self._now())

    def business_date(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    def init_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.chmod(0o700)
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS qq_like_mobile_allowlist (
                    qq_number TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (enabled IN (0, 1))
                );

                CREATE TABLE IF NOT EXISTS qq_like_mobile_accounts (
                    id TEXT PRIMARY KEY,
                    qq_number TEXT NOT NULL UNIQUE,
                    access_token_hash TEXT NOT NULL UNIQUE,
                    opted_in INTEGER NOT NULL DEFAULT 1,
                    install_id_hash TEXT NOT NULL DEFAULT '',
                    app_version TEXT NOT NULL DEFAULT '',
                    active_business_date TEXT NOT NULL DEFAULT '',
                    binding_reset_pending INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (opted_in IN (0, 1)),
                    CHECK (binding_reset_pending IN (0, 1))
                );

                CREATE TABLE IF NOT EXISTS qq_like_mobile_tasks (
                    id TEXT PRIMARY KEY,
                    source_account_id TEXT NOT NULL,
                    target_account_id TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_times INTEGER NOT NULL,
                    lease_id TEXT,
                    lease_expires_at TEXT,
                    result_code TEXT NOT NULL DEFAULT '',
                    result_message TEXT NOT NULL DEFAULT '',
                    result_idempotency_hash TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    leased_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY (source_account_id)
                        REFERENCES qq_like_mobile_accounts(id),
                    FOREIGN KEY (target_account_id)
                        REFERENCES qq_like_mobile_accounts(id),
                    UNIQUE (source_account_id, target_account_id, business_date),
                    CHECK (source_account_id != target_account_id),
                    CHECK (status IN (
                        'queued', 'leased', 'succeeded', 'failed', 'uncertain'
                    )),
                    CHECK (requested_times BETWEEN 1 AND 10)
                );

                CREATE INDEX IF NOT EXISTS idx_qq_like_mobile_task_lease
                ON qq_like_mobile_tasks(
                    source_account_id, business_date, status, created_at
                );

                CREATE INDEX IF NOT EXISTS idx_qq_like_mobile_lease_expiry
                ON qq_like_mobile_tasks(status, lease_expires_at);

                CREATE INDEX IF NOT EXISTS idx_qq_like_mobile_active
                ON qq_like_mobile_accounts(active_business_date, opted_in);

                CREATE UNIQUE INDEX IF NOT EXISTS uq_qq_like_mobile_result_key
                ON qq_like_mobile_tasks(result_idempotency_hash)
                WHERE result_idempotency_hash IS NOT NULL;
                """
            )
            # 兼容早期测试库，正式部署无需人工迁移。
            self._ensure_column(
                conn,
                "qq_like_mobile_accounts",
                "install_id_hash",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "qq_like_mobile_accounts",
                "app_version",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "qq_like_mobile_accounts",
                "active_business_date",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "qq_like_mobile_accounts",
                "binding_reset_pending",
                "INTEGER NOT NULL DEFAULT 0",
            )
        self.db_path.chmod(0o600)

    def upsert_allowlist(
        self,
        qq_number: str,
        *,
        enabled: bool = True,
        note: str = "",
    ) -> Dict[str, object]:
        self.init_schema()
        normalized_qq = _normalize_qq_number(qq_number)
        now = self._now_text()
        clean_note = str(note or "").strip()[:200]
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO qq_like_mobile_allowlist (
                    qq_number, enabled, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(qq_number) DO UPDATE SET
                    enabled = excluded.enabled,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (normalized_qq, int(bool(enabled)), clean_note, now, now),
            )
            conn.execute(
                """
                UPDATE qq_like_mobile_accounts
                SET opted_in = ?, updated_at = ?
                WHERE qq_number = ?
                """,
                (int(bool(enabled)), now, normalized_qq),
            )
            row = conn.execute(
                """
                SELECT *
                FROM qq_like_mobile_allowlist
                WHERE qq_number = ?
                """,
                (normalized_qq,),
            ).fetchone()
        return dict(row)

    def _allowlist_row_locked(
        self,
        conn: sqlite3.Connection,
        qq_number: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT *
            FROM qq_like_mobile_allowlist
            WHERE qq_number = ?
            """,
            (qq_number,),
        ).fetchone()
        if row is None:
            raise MobileQQLikeStoreError(
                "当前账号未加入测试名单",
                http_status=403,
                error_code="not_allowlisted",
            )
        if not row["enabled"]:
            raise MobileQQLikeStoreError(
                "当前账号已被停用",
                http_status=403,
                error_code="allowlist_disabled",
            )
        return row

    def register(
        self,
        qq_number: str,
        *,
        install_id: str,
        app_version: str,
        access_token: str = "",
    ) -> Dict[str, object]:
        """白名单 QQ 首次签发凭证，已有绑定必须携带原凭证。"""

        self.init_schema()
        normalized_qq = _normalize_qq_number(qq_number)
        normalized_install_id = _normalize_install_id(install_id)
        clean_version = str(app_version or "").strip()[:80]
        if not clean_version:
            raise MobileQQLikeStoreError("缺少 App 版本")
        now = self._now_text()
        new_token = secrets.token_urlsafe(32)
        account_id = f"qlm_{uuid.uuid4().hex}"
        install_id_hash = _token_digest(normalized_install_id)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._allowlist_row_locked(conn, normalized_qq)
            existing = conn.execute(
                """
                SELECT *
                FROM qq_like_mobile_accounts
                WHERE qq_number = ?
                """,
                (normalized_qq,),
            ).fetchone()
            if existing is not None:
                token_matches = bool(access_token) and hmac.compare_digest(
                    _token_digest(access_token),
                    str(existing["access_token_hash"]),
                )
                reset_pending = bool(existing["binding_reset_pending"])
                if not token_matches and not reset_pending:
                    raise MobileQQLikeStoreError(
                        "该 QQ 已绑定其他安装，请携带原任务凭证或由管理员重置绑定",
                        http_status=409,
                        error_code="binding_conflict",
                    )
                if reset_pending:
                    conn.execute(
                        """
                        UPDATE qq_like_mobile_accounts
                        SET access_token_hash = ?, install_id_hash = ?,
                            app_version = ?, opted_in = 1,
                            binding_reset_pending = 0, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            _token_digest(new_token),
                            install_id_hash,
                            clean_version,
                            now,
                            existing["id"],
                        ),
                    )
                    response_token = new_token
                    rebound = True
                else:
                    conn.execute(
                        """
                        UPDATE qq_like_mobile_accounts
                        SET install_id_hash = ?, app_version = ?,
                            opted_in = 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            install_id_hash,
                            clean_version,
                            now,
                            existing["id"],
                        ),
                    )
                    response_token = ""
                    rebound = False
                account = conn.execute(
                    "SELECT * FROM qq_like_mobile_accounts WHERE id = ?",
                    (existing["id"],),
                ).fetchone()
                return {
                    "account": dict(account),
                    "access_token": response_token,
                    "created": False,
                    "rebound": rebound,
                }

            conn.execute(
                """
                INSERT INTO qq_like_mobile_accounts (
                    id, qq_number, access_token_hash, opted_in,
                    install_id_hash, app_version, active_business_date,
                    binding_reset_pending, last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?, '', 0, ?, ?, ?)
                """,
                (
                    account_id,
                    normalized_qq,
                    _token_digest(new_token),
                    install_id_hash,
                    clean_version,
                    now,
                    now,
                    now,
                ),
            )
            account = conn.execute(
                "SELECT * FROM qq_like_mobile_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        return {
            "account": dict(account),
            "access_token": new_token,
            "created": True,
            "rebound": False,
        }

    def authenticate(self, access_token: str) -> Dict[str, object]:
        self.init_schema()
        if not str(access_token or "").strip():
            raise MobileQQLikeStoreError(
                "缺少手机互赞凭证",
                http_status=401,
                error_code="token_missing",
            )
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT account.*, allowlist.enabled AS allowlist_enabled
                FROM qq_like_mobile_accounts AS account
                LEFT JOIN qq_like_mobile_allowlist AS allowlist
                  ON allowlist.qq_number = account.qq_number
                WHERE account.access_token_hash = ?
                """,
                (_token_digest(access_token),),
            ).fetchone()
        if row is None:
            raise MobileQQLikeStoreError(
                "手机互赞凭证无效",
                http_status=401,
                error_code="token_invalid",
            )
        account = dict(row)
        if not account["opted_in"] or not account["allowlist_enabled"]:
            raise MobileQQLikeStoreError(
                "当前账号已被停用",
                http_status=403,
                error_code="account_disabled",
            )
        if account["binding_reset_pending"]:
            raise MobileQQLikeStoreError(
                "设备绑定已重置，请重新注册",
                http_status=401,
                error_code="binding_reset",
            )
        return account

    def heartbeat(self, account_id: str) -> Dict[str, object]:
        now = self._now_text()
        business_date = self.business_date()
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE qq_like_mobile_accounts
                SET active_business_date = ?, last_seen_at = ?, updated_at = ?
                WHERE id = ? AND opted_in = 1
                  AND binding_reset_pending = 0
                """,
                (business_date, now, now, account_id),
            )
            if cursor.rowcount != 1:
                raise MobileQQLikeStoreError(
                    "手机互赞账号不存在或未加入互赞",
                    http_status=403,
                    error_code="account_inactive",
                )
            row = conn.execute(
                "SELECT * FROM qq_like_mobile_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        return dict(row)

    def _expire_leases_locked(
        self,
        conn: sqlite3.Connection,
        *,
        now_text: str,
    ) -> int:
        cursor = conn.execute(
            """
            UPDATE qq_like_mobile_tasks
            SET status = 'uncertain',
                result_code = 'lease_expired',
                result_message = '手机执行租约已过期，结果未知且不会自动重发',
                finished_at = ?,
                updated_at = ?
            WHERE status = 'leased'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (now_text, now_text, now_text),
        )
        return int(cursor.rowcount)

    def expire_leases(self) -> int:
        self.init_schema()
        now_text = self._now_text()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._expire_leases_locked(conn, now_text=now_text)

    def _materialize_daily_tasks_locked(
        self,
        conn: sqlite3.Connection,
        *,
        source_account_id: str,
        business_date: str,
        requested_times: int,
        limit: int,
        now_text: str,
    ) -> int:
        queued_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM qq_like_mobile_tasks
                WHERE source_account_id = ?
                  AND business_date = ?
                  AND status = 'queued'
                """,
                (source_account_id, business_date),
            ).fetchone()[0]
        )
        remaining = max(0, int(limit) - queued_count)
        if remaining == 0:
            return 0
        targets = conn.execute(
            """
            SELECT target.id
            FROM qq_like_mobile_accounts AS target
            JOIN qq_like_mobile_allowlist AS allowlist
              ON allowlist.qq_number = target.qq_number
             AND allowlist.enabled = 1
            LEFT JOIN qq_like_mobile_tasks AS task
              ON task.source_account_id = ?
             AND task.target_account_id = target.id
             AND task.business_date = ?
            WHERE target.opted_in = 1
              AND target.binding_reset_pending = 0
              AND target.active_business_date = ?
              AND target.id != ?
              AND task.id IS NULL
            ORDER BY target.created_at ASC, target.id ASC
            LIMIT ?
            """,
            (
                source_account_id,
                business_date,
                business_date,
                source_account_id,
                remaining,
            ),
        ).fetchall()
        created = 0
        for target in targets:
            task_id = f"qlmt_{uuid.uuid4().hex}"
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO qq_like_mobile_tasks (
                    id, source_account_id, target_account_id,
                    business_date, status, requested_times,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    task_id,
                    source_account_id,
                    target["id"],
                    business_date,
                    requested_times,
                    now_text,
                    now_text,
                ),
            )
            created += int(cursor.rowcount)
        return created

    @staticmethod
    def _task_payload(row: sqlite3.Row) -> Dict[str, object]:
        return {
            "id": str(row["id"]),
            "target_qq": str(row["target_qq"]),
            "business_date": str(row["business_date"]),
            "times": int(row["requested_times"]),
            "lease_token": str(row["lease_id"] or ""),
            "lease_expires_at": str(row["lease_expires_at"] or ""),
            "status": str(row["status"]),
        }

    def lease_tasks(
        self,
        account_id: str,
        *,
        limit: int,
        lease_seconds: int,
        requested_times: int,
    ) -> Dict[str, object]:
        """原子领取一个小批次；同一来源同时最多有一个活动批次。"""

        self.init_schema()
        safe_limit = max(1, min(int(limit), 50))
        safe_lease_seconds = max(15, min(int(lease_seconds), 600))
        safe_requested_times = max(1, min(int(requested_times), 10))
        now = self._now()
        now_text = self._time_text(now)
        expires_at = self._time_text(now + timedelta(seconds=safe_lease_seconds))
        business_date = now.strftime("%Y-%m-%d")

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute(
                """
                SELECT id
                FROM qq_like_mobile_accounts
                WHERE id = ? AND opted_in = 1
                  AND binding_reset_pending = 0
                  AND active_business_date = ?
                """,
                (account_id, business_date),
            ).fetchone()
            if account is None:
                raise MobileQQLikeStoreError(
                    "请先确认本机 QQ 在线并完成今日心跳",
                    http_status=409,
                    error_code="heartbeat_required",
                )

            self._expire_leases_locked(conn, now_text=now_text)
            self._materialize_daily_tasks_locked(
                conn,
                source_account_id=account_id,
                business_date=business_date,
                requested_times=safe_requested_times,
                limit=safe_limit,
                now_text=now_text,
            )

            active_rows = conn.execute(
                """
                SELECT task.*, target.qq_number AS target_qq
                FROM qq_like_mobile_tasks AS task
                JOIN qq_like_mobile_accounts AS target
                  ON target.id = task.target_account_id
                WHERE task.source_account_id = ?
                  AND task.status = 'leased'
                ORDER BY task.created_at ASC, task.id ASC
                """,
                (account_id,),
            ).fetchall()
            if active_rows:
                return {
                    "business_date": business_date,
                    "lease_token": str(active_rows[0]["lease_id"] or ""),
                    "lease_expires_at": str(
                        active_rows[0]["lease_expires_at"] or ""
                    ),
                    "tasks": [self._task_payload(row) for row in active_rows],
                    "reused": True,
                }

            queued_rows = conn.execute(
                """
                SELECT task.id
                FROM qq_like_mobile_tasks AS task
                WHERE task.source_account_id = ?
                  AND task.business_date = ?
                  AND task.status = 'queued'
                ORDER BY task.created_at ASC, task.id ASC
                LIMIT ?
                """,
                (account_id, business_date, safe_limit),
            ).fetchall()
            if not queued_rows:
                return {
                    "business_date": business_date,
                    "lease_token": "",
                    "lease_expires_at": "",
                    "tasks": [],
                    "reused": False,
                    "summary": self._daily_summary_locked(
                        conn,
                        account_id=account_id,
                        business_date=business_date,
                    ),
                }

            lease_id = f"qlml_{uuid.uuid4().hex}"
            task_ids = [str(row["id"]) for row in queued_rows]
            placeholders = ", ".join("?" for _ in task_ids)
            cursor = conn.execute(
                f"""
                UPDATE qq_like_mobile_tasks
                SET status = 'leased', lease_id = ?, lease_expires_at = ?,
                    leased_at = ?, updated_at = ?
                WHERE id IN ({placeholders}) AND status = 'queued'
                """,
                (lease_id, expires_at, now_text, now_text, *task_ids),
            )
            if cursor.rowcount != len(task_ids):
                raise MobileQQLikeStoreError(
                    "任务领取冲突，请稍后重试",
                    http_status=409,
                    error_code="lease_conflict",
                )
            leased_rows = conn.execute(
                f"""
                SELECT task.*, target.qq_number AS target_qq
                FROM qq_like_mobile_tasks AS task
                JOIN qq_like_mobile_accounts AS target
                  ON target.id = task.target_account_id
                WHERE task.id IN ({placeholders})
                ORDER BY task.created_at ASC, task.id ASC
                """,
                tuple(task_ids),
            ).fetchall()
            return {
                "business_date": business_date,
                "lease_token": lease_id,
                "lease_expires_at": expires_at,
                "tasks": [self._task_payload(row) for row in leased_rows],
                "reused": False,
                "summary": self._daily_summary_locked(
                    conn,
                    account_id=account_id,
                    business_date=business_date,
                ),
            }

    def record_result(
        self,
        account_id: str,
        *,
        task_id: str,
        lease_token: str,
        outcome: str,
        idempotency_key: str,
        result_code: str = "",
        result_message: str = "",
    ) -> Dict[str, object]:
        """完成结果按任务幂等；过期任务保持 uncertain，不接受迟到覆盖。"""

        clean_task_id = str(task_id or "").strip()
        clean_lease_token = str(lease_token or "").strip()
        clean_outcome = str(outcome or "").strip().lower()
        clean_idempotency_key = str(idempotency_key or "").strip()
        if not clean_task_id:
            raise MobileQQLikeStoreError("缺少任务 ID")
        if not clean_lease_token:
            raise MobileQQLikeStoreError("缺少租约令牌")
        if not clean_idempotency_key:
            raise MobileQQLikeStoreError("缺少结果幂等请求 ID")
        if len(clean_idempotency_key) > 160:
            raise MobileQQLikeStoreError("结果幂等请求 ID 过长")
        if clean_outcome not in MOBILE_FINAL_TASK_STATUSES:
            raise MobileQQLikeStoreError(
                "结果状态必须是 succeeded、failed 或 uncertain"
            )

        self.init_schema()
        now_text = self._now_text()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._expire_leases_locked(conn, now_text=now_text)
            idempotency_hash = _token_digest(clean_idempotency_key)
            idempotent_row = conn.execute(
                """
                SELECT task.*, target.qq_number AS target_qq
                FROM qq_like_mobile_tasks AS task
                JOIN qq_like_mobile_accounts AS target
                  ON target.id = task.target_account_id
                WHERE task.result_idempotency_hash = ?
                """,
                (idempotency_hash,),
            ).fetchone()
            if idempotent_row is not None:
                if (
                    idempotent_row["id"] != clean_task_id
                    or idempotent_row["source_account_id"] != account_id
                ):
                    raise MobileQQLikeStoreError(
                        "结果幂等请求 ID 已被其他任务使用"
                    )
                payload = self._task_payload(idempotent_row)
                payload["idempotent"] = True
                payload["result_code"] = str(
                    idempotent_row["result_code"] or ""
                )
                payload["result_message"] = str(
                    idempotent_row["result_message"] or ""
                )
                return payload

            row = conn.execute(
                """
                SELECT task.*, target.qq_number AS target_qq
                FROM qq_like_mobile_tasks AS task
                JOIN qq_like_mobile_accounts AS target
                  ON target.id = task.target_account_id
                WHERE task.id = ? AND task.source_account_id = ?
                """,
                (clean_task_id, account_id),
            ).fetchone()
            if row is None:
                raise MobileQQLikeStoreError("互赞任务不存在")
            if row["status"] in MOBILE_FINAL_TASK_STATUSES:
                payload = self._task_payload(row)
                payload["idempotent"] = True
                payload["result_code"] = str(row["result_code"] or "")
                payload["result_message"] = str(row["result_message"] or "")
                return payload
            if row["status"] != "leased":
                raise MobileQQLikeStoreError("互赞任务当前不在执行中")
            if not hmac.compare_digest(
                str(row["lease_id"] or ""),
                clean_lease_token,
            ):
                raise MobileQQLikeStoreError("任务租约无效")

            conn.execute(
                """
                UPDATE qq_like_mobile_tasks
                SET status = ?, result_code = ?, result_message = ?,
                    result_idempotency_hash = ?,
                    finished_at = ?, updated_at = ?
                WHERE id = ? AND source_account_id = ?
                  AND status = 'leased' AND lease_id = ?
                """,
                (
                    clean_outcome,
                    str(result_code or "")[:120],
                    str(result_message or "")[:500],
                    idempotency_hash,
                    now_text,
                    now_text,
                    clean_task_id,
                    account_id,
                    clean_lease_token,
                ),
            )
            completed = conn.execute(
                """
                SELECT task.*, target.qq_number AS target_qq
                FROM qq_like_mobile_tasks AS task
                JOIN qq_like_mobile_accounts AS target
                  ON target.id = task.target_account_id
                WHERE task.id = ?
                """,
                (clean_task_id,),
            ).fetchone()
        payload = self._task_payload(completed)
        payload["idempotent"] = False
        payload["result_code"] = str(completed["result_code"] or "")
        payload["result_message"] = str(completed["result_message"] or "")
        return payload

    def _daily_summary_locked(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: str,
        business_date: str,
    ) -> Dict[str, int]:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM qq_like_mobile_tasks
            WHERE source_account_id = ? AND business_date = ?
            GROUP BY status
            """,
            (account_id, business_date),
        ).fetchall()
        counts = {status: 0 for status in MOBILE_TASK_STATUSES}
        for row in rows:
            counts[str(row["status"])] = int(row["count"])
        eligible_targets = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM qq_like_mobile_accounts AS target
                JOIN qq_like_mobile_allowlist AS allowlist
                  ON allowlist.qq_number = target.qq_number
                 AND allowlist.enabled = 1
                WHERE target.id != ?
                  AND target.opted_in = 1
                  AND target.binding_reset_pending = 0
                  AND target.active_business_date = ?
                """,
                (account_id, business_date),
            ).fetchone()[0]
        )
        materialized = sum(counts.values())
        counts["not_materialized"] = max(0, eligible_targets - materialized)
        counts["pending"] = (
            counts["queued"]
            + counts["leased"]
            + counts["not_materialized"]
        )
        counts["eligible_targets"] = eligible_targets
        counts["total"] = max(materialized, eligible_targets)
        return counts

    def daily_summary(self, account_id: str) -> Dict[str, int]:
        self.init_schema()
        business_date = self.business_date()
        with self._connection() as conn:
            return self._daily_summary_locked(
                conn,
                account_id=account_id,
                business_date=business_date,
            )

    def set_account_action(
        self,
        qq_number: str,
        *,
        action: str,
    ) -> Dict[str, object]:
        self.init_schema()
        normalized_qq = _normalize_qq_number(qq_number)
        clean_action = str(action or "").strip().lower()
        if clean_action not in {"enable", "disable", "reset_binding"}:
            raise MobileQQLikeStoreError("账号操作无效")
        now = self._now_text()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            allowlist = conn.execute(
                """
                SELECT *
                FROM qq_like_mobile_allowlist
                WHERE qq_number = ?
                """,
                (normalized_qq,),
            ).fetchone()
            if allowlist is None:
                raise MobileQQLikeStoreError("白名单中不存在该 QQ")
            if clean_action in {"enable", "disable"}:
                enabled = int(clean_action == "enable")
                conn.execute(
                    """
                    UPDATE qq_like_mobile_allowlist
                    SET enabled = ?, updated_at = ?
                    WHERE qq_number = ?
                    """,
                    (enabled, now, normalized_qq),
                )
                conn.execute(
                    """
                    UPDATE qq_like_mobile_accounts
                    SET opted_in = ?, updated_at = ?
                    WHERE qq_number = ?
                    """,
                    (enabled, now, normalized_qq),
                )
            else:
                revoked_hash = _token_digest(
                    f"revoked:{uuid.uuid4().hex}:{secrets.token_urlsafe(16)}"
                )
                conn.execute(
                    """
                    UPDATE qq_like_mobile_accounts
                    SET access_token_hash = ?, install_id_hash = '',
                        active_business_date = '',
                        binding_reset_pending = 1, updated_at = ?
                    WHERE qq_number = ?
                    """,
                    (revoked_hash, now, normalized_qq),
                )
            row = conn.execute(
                """
                SELECT allowlist.*, account.id AS account_id,
                       account.install_id_hash,
                       account.binding_reset_pending,
                       account.active_business_date,
                       account.last_seen_at,
                       account.app_version
                FROM qq_like_mobile_allowlist AS allowlist
                LEFT JOIN qq_like_mobile_accounts AS account
                  ON account.qq_number = allowlist.qq_number
                WHERE allowlist.qq_number = ?
                """,
                (normalized_qq,),
            ).fetchone()
        return dict(row)

    def admin_overview(self, *, task_limit: int = 300) -> Dict[str, object]:
        self.init_schema()
        self.expire_leases()
        business_date = self.business_date()
        safe_limit = max(1, min(int(task_limit), 1000))
        with self._connection() as conn:
            allowlist_rows = conn.execute(
                """
                SELECT allowlist.qq_number, allowlist.enabled, allowlist.note,
                       allowlist.created_at, allowlist.updated_at,
                       account.id AS account_id,
                       account.app_version,
                       account.active_business_date,
                       account.last_seen_at,
                       account.install_id_hash,
                       account.binding_reset_pending
                FROM qq_like_mobile_allowlist AS allowlist
                LEFT JOIN qq_like_mobile_accounts AS account
                  ON account.qq_number = allowlist.qq_number
                ORDER BY allowlist.created_at ASC, allowlist.qq_number ASC
                """
            ).fetchall()
            account_rows = conn.execute(
                """
                SELECT account.id, account.qq_number, account.app_version,
                       account.active_business_date, account.last_seen_at,
                       account.created_at, account.updated_at,
                       account.install_id_hash, account.binding_reset_pending,
                       account.opted_in, allowlist.enabled AS allowlist_enabled
                FROM qq_like_mobile_accounts AS account
                LEFT JOIN qq_like_mobile_allowlist AS allowlist
                  ON allowlist.qq_number = account.qq_number
                ORDER BY account.last_seen_at DESC, account.qq_number ASC
                """
            ).fetchall()
            task_rows = conn.execute(
                """
                SELECT task.id, source.qq_number AS source_qq,
                       target.qq_number AS target_qq,
                       task.business_date, task.status,
                       task.requested_times, task.lease_expires_at,
                       task.result_code, task.result_message,
                       task.created_at, task.leased_at, task.finished_at,
                       task.updated_at
                FROM qq_like_mobile_tasks AS task
                JOIN qq_like_mobile_accounts AS source
                  ON source.id = task.source_account_id
                JOIN qq_like_mobile_accounts AS target
                  ON target.id = task.target_account_id
                WHERE task.business_date = ?
                ORDER BY task.created_at DESC, task.id DESC
                LIMIT ?
                """,
                (business_date, safe_limit),
            ).fetchall()
            status_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM qq_like_mobile_tasks
                WHERE business_date = ?
                GROUP BY status
                """,
                (business_date,),
            ).fetchall()

        status_counts = {status: 0 for status in MOBILE_TASK_STATUSES}
        for row in status_rows:
            status_counts[str(row["status"])] = int(row["count"])
        allowlist_payload = []
        for row in allowlist_rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            item["device_bound"] = bool(item["install_id_hash"])
            item["binding_reset_pending"] = bool(
                item["binding_reset_pending"] or False
            )
            item.pop("install_id_hash", None)
            allowlist_payload.append(item)
        account_payload = []
        for row in account_rows:
            item = dict(row)
            item["active_today"] = (
                item["active_business_date"] == business_date
                and bool(item["opted_in"])
                and bool(item["allowlist_enabled"])
                and not bool(item["binding_reset_pending"])
            )
            item["device_bound"] = bool(item["install_id_hash"])
            item["binding_reset_pending"] = bool(
                item["binding_reset_pending"]
            )
            item["opted_in"] = bool(item["opted_in"])
            item["allowlist_enabled"] = bool(item["allowlist_enabled"])
            item.pop("install_id_hash", None)
            account_payload.append(item)
        summary = {
            **status_counts,
            "pending": status_counts["queued"] + status_counts["leased"],
            "abnormal": status_counts["failed"] + status_counts["uncertain"],
            "active_accounts": sum(
                1 for item in account_payload if item["active_today"]
            ),
            "allowlisted_accounts": sum(
                1 for item in allowlist_payload if item["enabled"]
            ),
        }
        return {
            "business_date": business_date,
            "summary": summary,
            "allowlist": allowlist_payload,
            "accounts": account_payload,
            "tasks": [dict(row) for row in task_rows],
        }

    def get_account(self, account_id: str) -> Optional[Dict[str, object]]:
        self.init_schema()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM qq_like_mobile_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_tasks(self, account_id: str) -> List[Dict[str, object]]:
        self.init_schema()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT task.*, target.qq_number AS target_qq
                FROM qq_like_mobile_tasks AS task
                JOIN qq_like_mobile_accounts AS target
                  ON target.id = task.target_account_id
                WHERE task.source_account_id = ?
                ORDER BY task.created_at ASC, task.id ASC
                """,
                (account_id,),
            ).fetchall()
        return [dict(row) for row in rows]
