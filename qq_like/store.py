"""QQ 互赞贡献账号、任务和公平调度的数据层。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import string
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional


SHANGHAI_TZ = timezone(timedelta(hours=8))
QQ_NUMBER_MIN_LENGTH = 5
QQ_NUMBER_MAX_LENGTH = 12
DEFAULT_LIKES_PER_REQUEST = 10
RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PBKDF2_ITERATIONS = 210_000

CONTRIBUTOR_STATUSES = {
    "pending_login",
    "active",
    "offline",
    "paused",
    "revoked",
}
REQUEST_STATUSES = {
    "waiting_source",
    "assigned",
    "running",
    "succeeded",
    "failed",
    "uncertain",
    "canceled",
}


class QQLikeStoreError(ValueError):
    """QQ 互赞数据层的可预期业务错误。"""


def _access_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_recovery_code(code: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        code.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return f"{PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_recovery_code(code: str, encoded: str) -> bool:
    try:
        raw_iterations, raw_salt, expected = encoded.split("$", 2)
        iterations = int(raw_iterations)
        salt = bytes.fromhex(raw_salt)
        expected_digest = bytes.fromhex(expected)
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        code.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected_digest)


def _new_recovery_code() -> str:
    raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(12))
    return "-".join(raw[index : index + 4] for index in range(0, 12, 4))


def _normalize_qq_number(value: str) -> str:
    qq_number = str(value or "").strip()
    if not qq_number.isdigit():
        raise QQLikeStoreError("QQ 号只能包含数字")
    if not QQ_NUMBER_MIN_LENGTH <= len(qq_number) <= QQ_NUMBER_MAX_LENGTH:
        raise QQLikeStoreError(
            f"QQ 号长度必须在 {QQ_NUMBER_MIN_LENGTH}-{QQ_NUMBER_MAX_LENGTH} 位之间"
        )
    if qq_number.startswith("0"):
        raise QQLikeStoreError("QQ 号不能以 0 开头")
    return qq_number


class QQLikeStore:
    """使用 SQLite 持久化互赞资格、任务和每日来源占用。"""

    def __init__(
        self,
        db_path: Path,
        *,
        now_factory: Optional[Callable[[], datetime]] = None,
        likes_per_request: int = DEFAULT_LIKES_PER_REQUEST,
    ) -> None:
        self.db_path = Path(db_path)
        self.now_factory = now_factory or (lambda: datetime.now(SHANGHAI_TZ))
        self.likes_per_request = max(1, min(int(likes_per_request), 10))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _now(self) -> datetime:
        value = self.now_factory()
        if value.tzinfo is None:
            value = value.replace(tzinfo=SHANGHAI_TZ)
        return value.astimezone(SHANGHAI_TZ)

    def _now_text(self) -> str:
        return self._now().strftime("%Y-%m-%d %H:%M:%S")

    def business_date(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    def init_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.chmod(0o700)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS qq_like_contributors (
                    id TEXT PRIMARY KEY,
                    qq_number TEXT,
                    status TEXT NOT NULL,
                    access_token_hash TEXT NOT NULL UNIQUE,
                    recovery_hash TEXT NOT NULL,
                    session_ref TEXT NOT NULL UNIQUE,
                    last_health_at TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT,
                    CHECK (status IN (
                        'pending_login', 'active', 'offline', 'paused', 'revoked'
                    ))
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_qq_like_active_qq
                ON qq_like_contributors(qq_number)
                WHERE qq_number IS NOT NULL AND status != 'revoked';

                CREATE TABLE IF NOT EXISTS qq_like_requests (
                    id TEXT PRIMARY KEY,
                    requester_kind TEXT NOT NULL,
                    contributor_id TEXT,
                    target_qq TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_contributor_id TEXT,
                    requested_times INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    result_code TEXT NOT NULL DEFAULT '',
                    result_message TEXT NOT NULL DEFAULT '',
                    remote_addr TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY (contributor_id)
                        REFERENCES qq_like_contributors(id),
                    FOREIGN KEY (source_contributor_id)
                        REFERENCES qq_like_contributors(id),
                    CHECK (requester_kind IN ('contributor', 'admin')),
                    CHECK (status IN (
                        'waiting_source', 'assigned', 'running', 'succeeded',
                        'failed', 'uncertain', 'canceled'
                    )),
                    CHECK (requested_times BETWEEN 1 AND 10)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_qq_like_daily_request
                ON qq_like_requests(contributor_id, business_date)
                WHERE requester_kind = 'contributor' AND status != 'canceled';

                CREATE INDEX IF NOT EXISTS idx_qq_like_requests_queue
                ON qq_like_requests(status, business_date, created_at);

                CREATE TABLE IF NOT EXISTS qq_like_source_usage (
                    source_contributor_id TEXT NOT NULL,
                    target_qq TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source_contributor_id, business_date),
                    UNIQUE (source_contributor_id, target_qq, business_date),
                    FOREIGN KEY (source_contributor_id)
                        REFERENCES qq_like_contributors(id),
                    FOREIGN KEY (request_id)
                        REFERENCES qq_like_requests(id)
                );

                CREATE TABLE IF NOT EXISTS qq_like_runtime_leases (
                    lease_name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
        self.db_path.chmod(0o600)

    def create_contributor(self) -> Dict[str, str]:
        self.init_schema()
        contributor_id = f"qlc_{uuid.uuid4().hex}"
        access_token = secrets.token_urlsafe(32)
        recovery_code = _new_recovery_code()
        now = self._now_text()
        session_ref = f"contributor-{contributor_id}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO qq_like_contributors (
                    id, status, access_token_hash, recovery_hash, session_ref,
                    created_at, updated_at
                ) VALUES (?, 'pending_login', ?, ?, ?, ?, ?)
                """,
                (
                    contributor_id,
                    _access_token_digest(access_token),
                    _hash_recovery_code(recovery_code),
                    session_ref,
                    now,
                    now,
                ),
            )
        return {
            "contributor_id": contributor_id,
            "access_token": access_token,
            "recovery_code": recovery_code,
            "session_ref": session_ref,
        }

    def authenticate(self, access_token: str) -> Dict[str, object]:
        token_hash = _access_token_digest(str(access_token or ""))
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM qq_like_contributors
                WHERE access_token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        if row is None:
            raise QQLikeStoreError("贡献凭证无效")
        contributor = dict(row)
        if contributor["status"] == "revoked":
            raise QQLikeStoreError("贡献账号已停止")
        return contributor

    def recover_access(self, contributor_id: str, recovery_code: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT recovery_hash, status
                FROM qq_like_contributors
                WHERE id = ?
                """,
                (contributor_id,),
            ).fetchone()
            if row is None or row["status"] == "revoked":
                raise QQLikeStoreError("贡献账号不存在或已停止")
            if not _verify_recovery_code(
                str(recovery_code or "").strip().upper(),
                str(row["recovery_hash"]),
            ):
                raise QQLikeStoreError("恢复码错误")
            access_token = secrets.token_urlsafe(32)
            conn.execute(
                """
                UPDATE qq_like_contributors
                SET access_token_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    _access_token_digest(access_token),
                    self._now_text(),
                    contributor_id,
                ),
            )
        return access_token

    def get_contributor(self, contributor_id: str) -> Optional[Dict[str, object]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM qq_like_contributors WHERE id = ?",
                (contributor_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_contributor_active(
        self,
        contributor_id: str,
        qq_number: str,
    ) -> Dict[str, object]:
        normalized_qq = _normalize_qq_number(qq_number)
        now = self._now_text()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    UPDATE qq_like_contributors
                    SET qq_number = ?, status = 'active',
                        last_health_at = ?, last_error = '', updated_at = ?
                    WHERE id = ? AND status != 'revoked'
                    """,
                    (normalized_qq, now, now, contributor_id),
                )
                if cursor.rowcount != 1:
                    raise QQLikeStoreError("贡献账号不存在或已停止")
        except sqlite3.IntegrityError as exc:
            raise QQLikeStoreError("该 QQ 已经作为贡献账号存在") from exc
        self.assign_pending_requests()
        contributor = self.get_contributor(contributor_id)
        if contributor is None:
            raise QQLikeStoreError("贡献账号不存在")
        return contributor

    def update_contributor_status(
        self,
        contributor_id: str,
        status: str,
        *,
        error: str = "",
    ) -> None:
        if status not in CONTRIBUTOR_STATUSES - {"active", "revoked"}:
            raise QQLikeStoreError("不支持的贡献账号状态")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE qq_like_contributors
                SET status = ?, last_error = ?, updated_at = ?
                WHERE id = ? AND status != 'revoked'
                """,
                (status, str(error or "")[:500], self._now_text(), contributor_id),
            )
            if cursor.rowcount != 1:
                raise QQLikeStoreError("贡献账号不存在或已停止")

    def revoke_contributor(self, contributor_id: str) -> None:
        now = self._now_text()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM qq_like_contributors WHERE id = ?",
                (contributor_id,),
            ).fetchone()
            if row is None:
                raise QQLikeStoreError("贡献账号不存在")
            if row["status"] == "revoked":
                return

            assigned_rows = conn.execute(
                """
                SELECT id
                FROM qq_like_requests
                WHERE source_contributor_id = ? AND status = 'assigned'
                """,
                (contributor_id,),
            ).fetchall()
            for assigned in assigned_rows:
                request_id = str(assigned["id"])
                conn.execute(
                    "DELETE FROM qq_like_source_usage WHERE request_id = ?",
                    (request_id,),
                )
                conn.execute(
                    """
                    UPDATE qq_like_requests
                    SET source_contributor_id = NULL, status = 'waiting_source',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, request_id),
                )

            conn.execute(
                """
                UPDATE qq_like_requests
                SET status = 'canceled', updated_at = ?, finished_at = ?,
                    result_message = '贡献账号已停止'
                WHERE contributor_id = ?
                  AND status IN ('waiting_source', 'assigned')
                """,
                (now, now, contributor_id),
            )
            conn.execute(
                """
                UPDATE qq_like_contributors
                SET status = 'revoked', revoked_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, contributor_id),
            )
        self.assign_pending_requests()

    def create_request(
        self,
        *,
        target_qq: str,
        idempotency_key: str,
        contributor_id: Optional[str] = None,
        admin: bool = False,
        requested_times: Optional[int] = None,
        remote_addr: str = "",
    ) -> Dict[str, object]:
        self.init_schema()
        normalized_target = _normalize_qq_number(target_qq)
        clean_idempotency = str(idempotency_key or "").strip()
        if not clean_idempotency:
            raise QQLikeStoreError("缺少幂等请求 ID")
        if len(clean_idempotency) > 160:
            raise QQLikeStoreError("幂等请求 ID 过长")
        if not admin and not contributor_id:
            raise QQLikeStoreError("普通请求必须关联贡献账号")

        request_times = self.likes_per_request
        if admin and requested_times is not None:
            request_times = max(1, min(int(requested_times), 10))

        requester_kind = "admin" if admin else "contributor"
        request_id = f"qlr_{uuid.uuid4().hex}"
        business_date = self.business_date()
        now = self._now_text()
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    """
                    SELECT *
                    FROM qq_like_requests
                    WHERE idempotency_key = ?
                    """,
                    (clean_idempotency,),
                ).fetchone()
                if existing is not None:
                    same_requester = (
                        existing["requester_kind"] == requester_kind
                        and existing["contributor_id"] == contributor_id
                        and existing["target_qq"] == normalized_target
                    )
                    if not same_requester:
                        raise QQLikeStoreError("幂等请求 ID 已被其他请求使用")
                    return dict(existing)

                if contributor_id:
                    contributor = conn.execute(
                        """
                        SELECT status
                        FROM qq_like_contributors
                        WHERE id = ?
                        """,
                        (contributor_id,),
                    ).fetchone()
                    if contributor is None:
                        raise QQLikeStoreError("贡献账号不存在")
                    if not admin and contributor["status"] != "active":
                        raise QQLikeStoreError("贡献账号当前不可用")

                conn.execute(
                    """
                    INSERT INTO qq_like_requests (
                        id, requester_kind, contributor_id, target_qq,
                        business_date, status, requested_times,
                        idempotency_key, remote_addr, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'waiting_source', ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        requester_kind,
                        contributor_id,
                        normalized_target,
                        business_date,
                        request_times,
                        clean_idempotency,
                        str(remote_addr or "")[:120],
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "qq_like_requests.contributor_id" in str(exc):
                raise QQLikeStoreError("今天已经使用过互赞资格") from exc
            raise

        self.assign_pending_requests()
        request = self.get_request(request_id)
        if request is None:
            raise QQLikeStoreError("互赞任务创建失败")
        return request

    def _assign_one(
        self,
        conn: sqlite3.Connection,
        request: sqlite3.Row,
    ) -> bool:
        candidates = conn.execute(
            """
            SELECT contributor.id, contributor.qq_number
            FROM qq_like_contributors AS contributor
            LEFT JOIN qq_like_source_usage AS usage
              ON usage.source_contributor_id = contributor.id
             AND usage.business_date = ?
            WHERE contributor.status = 'active'
              AND contributor.qq_number IS NOT NULL
              AND contributor.qq_number != ?
              AND contributor.id != COALESCE(?, '')
              AND usage.source_contributor_id IS NULL
            ORDER BY COALESCE(contributor.last_health_at, contributor.created_at) ASC,
                     contributor.created_at ASC,
                     contributor.id ASC
            LIMIT 1
            """,
            (
                request["business_date"],
                request["target_qq"],
                request["contributor_id"],
            ),
        ).fetchone()
        if candidates is None:
            return False

        now = self._now_text()
        conn.execute(
            """
            INSERT INTO qq_like_source_usage (
                source_contributor_id, target_qq, business_date,
                request_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                candidates["id"],
                request["target_qq"],
                request["business_date"],
                request["id"],
                now,
            ),
        )
        conn.execute(
            """
            UPDATE qq_like_requests
            SET source_contributor_id = ?, status = 'assigned', updated_at = ?
            WHERE id = ? AND status = 'waiting_source'
            """,
            (candidates["id"], now, request["id"]),
        )
        return True

    def assign_pending_requests(self, limit: int = 100) -> int:
        self.init_schema()
        assigned = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT *
                FROM qq_like_requests
                WHERE status = 'waiting_source'
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
            for request in rows:
                try:
                    if self._assign_one(conn, request):
                        assigned += 1
                except sqlite3.IntegrityError:
                    continue
        return assigned

    def get_request(self, request_id: str) -> Optional[Dict[str, object]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM qq_like_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_requests(
        self,
        *,
        contributor_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, object]]:
        safe_limit = max(1, min(int(limit), 200))
        query = "SELECT * FROM qq_like_requests"
        params: List[object] = []
        if contributor_id:
            query += " WHERE contributor_id = ?"
            params.append(contributor_id)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(safe_limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def list_contributors(self, limit: int = 200) -> List[Dict[str, object]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM qq_like_contributors
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_retained_contributors(self) -> int:
        with self._connect() as conn:
            count = conn.execute(
                """
                SELECT COUNT(*)
                FROM qq_like_contributors
                WHERE status != 'revoked'
                """
            ).fetchone()[0]
        return int(count)

    def list_stale_pending_contributors(
        self,
        *,
        older_than_hours: int = 24,
    ) -> List[Dict[str, object]]:
        cutoff = (
            self._now() - timedelta(hours=max(1, int(older_than_hours)))
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM qq_like_contributors
                WHERE status = 'pending_login' AND updated_at <= ?
                ORDER BY updated_at ASC, id ASC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def contributor_daily_summary(self, contributor_id: str) -> Dict[str, int]:
        business_date = self.business_date()
        with self._connect() as conn:
            request_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM qq_like_requests
                WHERE contributor_id = ? AND business_date = ?
                  AND status != 'canceled'
                """,
                (contributor_id, business_date),
            ).fetchone()[0]
            source_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM qq_like_source_usage
                WHERE source_contributor_id = ? AND business_date = ?
                """,
                (contributor_id, business_date),
            ).fetchone()[0]
            queued_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM qq_like_requests
                WHERE contributor_id = ?
                  AND status IN ('waiting_source', 'assigned', 'running')
                """,
                (contributor_id,),
            ).fetchone()[0]
        return {
            "request_used": int(request_count),
            "source_used": int(source_count),
            "queued_requests": int(queued_count),
        }

    def next_assigned_request(self) -> Optional[Dict[str, object]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM qq_like_requests
                WHERE status = 'assigned'
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row is not None else None

    def begin_request(self, request_id: str, source_contributor_id: str) -> bool:
        now = self._now_text()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE qq_like_requests
                SET status = 'running', started_at = ?, updated_at = ?
                WHERE id = ? AND source_contributor_id = ?
                  AND status = 'assigned'
                """,
                (now, now, request_id, source_contributor_id),
            )
        return cursor.rowcount == 1

    def release_assignment(
        self,
        request_id: str,
        source_contributor_id: str,
        *,
        reason: str = "",
    ) -> bool:
        now = self._now_text()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT status, source_contributor_id
                FROM qq_like_requests
                WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "assigned"
                or row["source_contributor_id"] != source_contributor_id
            ):
                return False
            conn.execute(
                "DELETE FROM qq_like_source_usage WHERE request_id = ?",
                (request_id,),
            )
            conn.execute(
                """
                UPDATE qq_like_requests
                SET source_contributor_id = NULL, status = 'waiting_source',
                    result_code = 'source_unavailable', result_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (str(reason or "来源账号暂不可用")[:500], now, request_id),
            )
        self.assign_pending_requests()
        return True

    def recover_interrupted_requests(self) -> int:
        now = self._now_text()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE qq_like_requests
                SET status = 'uncertain',
                    result_code = 'worker_interrupted',
                    result_message = '执行器重启，结果需要人工确认',
                    finished_at = ?, updated_at = ?
                WHERE status = 'running'
                """,
                (now, now),
            )
        return cursor.rowcount

    def finish_request(
        self,
        request_id: str,
        *,
        status: str,
        result_code: str = "",
        result_message: str = "",
    ) -> None:
        if status not in {"succeeded", "failed", "uncertain"}:
            raise QQLikeStoreError("不支持的任务完成状态")
        now = self._now_text()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE qq_like_requests
                SET status = ?, result_code = ?, result_message = ?,
                    finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    status,
                    str(result_code or "")[:120],
                    str(result_message or "")[:500],
                    now,
                    now,
                    request_id,
                ),
            )
            if cursor.rowcount != 1:
                raise QQLikeStoreError("任务不在执行中")

    def cancel_request(self, request_id: str) -> None:
        now = self._now_text()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM qq_like_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise QQLikeStoreError("互赞任务不存在")
            if row["status"] not in {"waiting_source", "assigned"}:
                raise QQLikeStoreError("当前任务不能取消")
            conn.execute(
                "DELETE FROM qq_like_source_usage WHERE request_id = ?",
                (request_id,),
            )
            conn.execute(
                """
                UPDATE qq_like_requests
                SET status = 'canceled', finished_at = ?, updated_at = ?,
                    result_message = '用户取消'
                WHERE id = ?
                """,
                (now, now, request_id),
            )
        self.assign_pending_requests()

    def acquire_runtime_lease(
        self,
        *,
        lease_name: str,
        owner_id: str,
        ttl_seconds: int = 90,
    ) -> bool:
        now = self._now()
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")
        expires_at = (now + timedelta(seconds=max(10, ttl_seconds))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT owner_id, expires_at
                FROM qq_like_runtime_leases
                WHERE lease_name = ?
                """,
                (lease_name,),
            ).fetchone()
            if (
                row is not None
                and row["owner_id"] != owner_id
                and str(row["expires_at"]) > now_text
            ):
                return False
            conn.execute(
                """
                INSERT INTO qq_like_runtime_leases (
                    lease_name, owner_id, expires_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(lease_name) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (lease_name, owner_id, expires_at, now_text),
            )
        return True

    def release_runtime_lease(self, *, lease_name: str, owner_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM qq_like_runtime_leases
                WHERE lease_name = ? AND owner_id = ?
                """,
                (lease_name, owner_id),
            )
