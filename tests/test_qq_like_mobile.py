import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import app
from qq_like import (
    MobileQQLikeService,
    MobileQQLikeStore,
    MobileQQLikeStoreError,
    QQLikeStore,
)


SHANGHAI_TZ = timezone(timedelta(hours=8))


class MobileQQLikeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "qq-like.sqlite3"
        self.now = datetime(2026, 7, 28, 10, 0, tzinfo=SHANGHAI_TZ)
        self.store = MobileQQLikeStore(
            self.db_path,
            now_factory=lambda: self.now,
        )
        self.service = MobileQQLikeService(
            self.store,
            max_batch_size=8,
            lease_seconds=15,
            likes_per_target=10,
        )
        self.service.start()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def register(self, qq_number: str, *, heartbeat: bool = True):
        self.store.upsert_allowlist(
            qq_number,
            note=f"测试账号 {qq_number}",
        )
        response = self.service.register(
            qq_number=qq_number,
            install_id=f"install-{qq_number}",
            app_version="0.1.0-test",
        )
        if heartbeat:
            self.service.heartbeat(response["access_token"])
        return response

    def test_allowlist_rejects_unlisted_and_disabled_accounts(self) -> None:
        with self.assertRaisesRegex(
            MobileQQLikeStoreError,
            "未加入测试名单",
        ) as unlisted:
            self.service.register(
                qq_number="100001",
                install_id="install-100001",
                app_version="0.1.0-test",
            )
        self.assertEqual(403, unlisted.exception.http_status)

        self.store.upsert_allowlist("100001", enabled=False)
        with self.assertRaisesRegex(
            MobileQQLikeStoreError,
            "已被停用",
        ) as disabled:
            self.service.register(
                qq_number="100001",
                install_id="install-100001",
                app_version="0.1.0-test",
            )
        self.assertEqual(403, disabled.exception.http_status)

    def test_register_is_opt_in_and_only_token_hash_is_stored(self) -> None:
        response = self.register("100001")
        token = response["access_token"]
        self.assertEqual("success", response["status"])
        self.assertTrue(response["created"])
        self.assertEqual("100001", response["device"]["qq_number"])
        self.assertNotIn("password", response["privacy"].lower())

        with sqlite3.connect(self.db_path) as conn:
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(qq_like_mobile_accounts)"
                )
            }
            stored = conn.execute(
                """
                SELECT access_token_hash
                FROM qq_like_mobile_accounts
                WHERE qq_number = '100001'
                """
            ).fetchone()[0]
        self.assertNotEqual(token, stored)
        self.assertNotIn("access_token", columns)
        self.assertFalse(
            {"password", "cookie", "ticket", "session", "credential"} & columns
        )

    def test_existing_registration_requires_and_reuses_original_token(self) -> None:
        first = self.register("100001")
        with self.assertRaisesRegex(MobileQQLikeStoreError, "原任务凭证"):
            self.register("100001")

        repeated = self.service.register(
            qq_number="100001",
            install_id="install-100001",
            app_version="0.1.0-test",
            access_token=first["access_token"],
        )
        self.assertFalse(repeated["created"])
        self.assertEqual(first["access_token"], repeated["access_token"])
        self.assertEqual(first["device"]["id"], repeated["device"]["id"])

    def test_heartbeat_authenticates_and_returns_daily_summary(self) -> None:
        registered = self.register("100001")
        response = self.service.heartbeat(registered["access_token"])
        self.assertEqual("success", response["status"])
        self.assertEqual(registered["device"]["id"], response["device"]["id"])
        self.assertEqual("2026-07-28", response["business_date"])
        self.assertEqual(0, response["tasks"]["queued"])

        with self.assertRaisesRegex(MobileQQLikeStoreError, "凭证无效"):
            self.service.heartbeat("not-the-token")

    def test_reset_binding_invalidates_old_token_and_allows_rebind(self) -> None:
        first = self.register("100001")
        self.store.set_account_action(
            "100001",
            action="reset_binding",
        )
        with self.assertRaisesRegex(MobileQQLikeStoreError, "凭证无效"):
            self.service.heartbeat(first["access_token"])

        rebound = self.service.register(
            qq_number="100001",
            install_id="new-install-100001",
            app_version="0.1.1-test",
        )
        self.assertTrue(rebound["rebound"])
        self.assertNotEqual(first["access_token"], rebound["access_token"])

    def test_only_accounts_heartbeating_today_enter_active_pool(self) -> None:
        source = self.register("100001")
        inactive = self.register("100002", heartbeat=False)
        active = self.register("100003")

        leased = self.service.lease(source["access_token"])
        self.assertEqual(
            ["100003"],
            [task["target_qq"] for task in leased["tasks"]],
        )
        self.assertTrue(inactive["access_token"])
        self.assertTrue(active["access_token"])

    def test_daily_directional_tasks_exclude_self_and_are_unique(self) -> None:
        one = self.register("100001")
        two = self.register("100002")
        three = self.register("100003")

        first = self.service.lease(one["access_token"], requested_limit=20)
        self.assertEqual(2, len(first["tasks"]))
        self.assertEqual(
            {"100002", "100003"},
            {task["target_qq"] for task in first["tasks"]},
        )
        self.assertTrue(all(task["times"] == 10 for task in first["tasks"]))
        self.assertTrue(all(task["lease_token"] for task in first["tasks"]))
        self.assertNotIn("100001", {task["target_qq"] for task in first["tasks"]})

        repeated = self.service.lease(one["access_token"], requested_limit=2)
        self.assertTrue(repeated["reused"])
        self.assertEqual(
            [task["id"] for task in first["tasks"]],
            [task["id"] for task in repeated["tasks"]],
        )

        second_source = self.service.lease(
            two["access_token"],
            requested_limit=2,
        )
        self.assertEqual(
            {"100001", "100003"},
            {task["target_qq"] for task in second_source["tasks"]},
        )
        self.assertNotEqual(
            {task["id"] for task in first["tasks"]},
            {task["id"] for task in second_source["tasks"]},
        )

        with sqlite3.connect(self.db_path) as conn:
            duplicates = conn.execute(
                """
                SELECT source_account_id, target_account_id, business_date,
                       COUNT(*)
                FROM qq_like_mobile_tasks
                GROUP BY source_account_id, target_account_id, business_date
                HAVING COUNT(*) > 1
                """
            ).fetchall()
            self_edges = conn.execute(
                """
                SELECT COUNT(*)
                FROM qq_like_mobile_tasks
                WHERE source_account_id = target_account_id
                """
            ).fetchone()[0]
        self.assertEqual([], duplicates)
        self.assertEqual(0, self_edges)
        self.assertTrue(three["access_token"])

    def test_result_is_idempotent_and_key_cannot_cross_tasks(self) -> None:
        source = self.register("100001")
        self.register("100002")
        self.register("100003")
        leased = self.service.lease(source["access_token"], requested_limit=2)
        first, second = leased["tasks"]

        completed = self.service.result(
            source["access_token"],
            task_id=first["id"],
            lease_token=first["lease_token"],
            outcome="succeeded",
            idempotency_key="result-one",
            result_code="ok",
            result_message="已点赞",
        )
        self.assertFalse(completed["task"]["idempotent"])
        self.assertEqual("succeeded", completed["task"]["status"])

        repeated = self.service.result(
            source["access_token"],
            task_id=first["id"],
            lease_token=first["lease_token"],
            outcome="failed",
            idempotency_key="result-one",
            result_code="different",
        )
        self.assertTrue(repeated["task"]["idempotent"])
        self.assertEqual("succeeded", repeated["task"]["status"])
        self.assertEqual("ok", repeated["task"]["result_code"])

        with self.assertRaisesRegex(MobileQQLikeStoreError, "其他任务"):
            self.service.result(
                source["access_token"],
                task_id=second["id"],
                lease_token=second["lease_token"],
                outcome="succeeded",
                idempotency_key="result-one",
            )

    def test_result_cannot_be_reported_by_another_source(self) -> None:
        source = self.register("100001")
        other = self.register("100002")
        task = self.service.lease(source["access_token"])["tasks"][0]

        with self.assertRaisesRegex(MobileQQLikeStoreError, "任务不存在"):
            self.service.result(
                other["access_token"],
                task_id=task["id"],
                lease_token=task["lease_token"],
                outcome="succeeded",
                idempotency_key="wrong-source",
            )

    def test_expired_lease_becomes_uncertain_and_is_never_reissued(self) -> None:
        source = self.register("100001")
        self.register("100002")
        leased = self.service.lease(source["access_token"])
        task = leased["tasks"][0]

        self.now += timedelta(seconds=16)
        heartbeat = self.service.heartbeat(source["access_token"])
        self.assertEqual(1, heartbeat["tasks"]["uncertain"])
        next_lease = self.service.lease(source["access_token"])
        self.assertEqual([], next_lease["tasks"])
        stored = self.store.list_tasks(source["device"]["id"])
        self.assertEqual(1, len(stored))
        self.assertEqual("uncertain", stored[0]["status"])
        self.assertEqual("lease_expired", stored[0]["result_code"])

        late = self.service.result(
            source["access_token"],
            task_id=task["id"],
            lease_token=task["lease_token"],
            outcome="succeeded",
            idempotency_key="late-result",
        )
        self.assertTrue(late["task"]["idempotent"])
        self.assertEqual("uncertain", late["task"]["status"])

    def test_new_business_date_creates_new_pair_once(self) -> None:
        source = self.register("100001")
        target = self.register("100002")
        first = self.service.lease(source["access_token"])["tasks"][0]
        self.service.result(
            source["access_token"],
            task_id=first["id"],
            lease_token=first["lease_token"],
            outcome="succeeded",
            idempotency_key="day-one",
        )

        self.now += timedelta(days=1)
        self.service.heartbeat(source["access_token"])
        self.service.heartbeat(target["access_token"])
        second = self.service.lease(source["access_token"])["tasks"][0]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual("2026-07-29", second["business_date"])

    def test_concurrent_lease_calls_return_one_atomic_batch(self) -> None:
        source = self.register("100001")
        for qq_number in ("100002", "100003", "100004"):
            self.register(qq_number)

        barrier = threading.Barrier(3)
        results = []
        errors = []

        def lease() -> None:
            try:
                barrier.wait()
                results.append(
                    self.service.lease(
                        source["access_token"],
                        requested_limit=2,
                    )
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=lease) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertEqual(
            {task["id"] for task in results[0]["tasks"]},
            {task["id"] for task in results[1]["tasks"]},
        )
        self.assertEqual(2, len(results[0]["tasks"]))

    def test_eight_item_batches_continue_until_all_targets_are_done(self) -> None:
        source = self.register("100001")
        for number in range(2, 12):
            self.register(f"1000{number:02d}")

        first = self.service.lease(source["access_token"])
        self.assertEqual(8, len(first["tasks"]))
        for index, task in enumerate(first["tasks"]):
            self.service.result(
                source["access_token"],
                task_id=task["id"],
                lease_token=task["lease_token"],
                outcome="succeeded",
                idempotency_key=f"first-batch-{index}",
            )

        second = self.service.lease(source["access_token"])
        self.assertEqual(2, len(second["tasks"]))
        self.assertFalse(
            {task["id"] for task in first["tasks"]}
            & {task["id"] for task in second["tasks"]}
        )
        for index, task in enumerate(second["tasks"]):
            self.service.result(
                source["access_token"],
                task_id=task["id"],
                lease_token=task["lease_token"],
                outcome="succeeded",
                idempotency_key=f"second-batch-{index}",
            )
        finished = self.service.lease(source["access_token"])
        self.assertEqual([], finished["tasks"])
        self.assertEqual(10, finished["summary"]["succeeded"])
        self.assertEqual(0, finished["summary"]["pending"])

    def test_admin_overview_contains_full_qq_but_no_token_or_install_hash(self) -> None:
        source = self.register("100001")
        self.register("100002")
        self.service.lease(source["access_token"])

        overview = self.service.admin_overview()
        self.assertEqual(2, overview["summary"]["active_accounts"])
        self.assertEqual(
            {"100001", "100002"},
            {item["qq_number"] for item in overview["accounts"]},
        )
        serialized = json.dumps(overview, ensure_ascii=False)
        self.assertNotIn("access_token", serialized)
        self.assertNotIn("install_id_hash", serialized)

    def test_mobile_tasks_are_invisible_to_existing_docker_worker_store(self) -> None:
        source = self.register("100001")
        self.register("100002")
        self.service.lease(source["access_token"])

        legacy_store = QQLikeStore(self.db_path)
        legacy_store.init_schema()
        self.assertIsNone(legacy_store.next_assigned_request())
        self.assertEqual(
            1,
            len(self.store.list_tasks(source["device"]["id"])),
        )


class MobileQQLikeHTTPAPITest(unittest.TestCase):
    class FakeLegacyService:
        def __init__(self) -> None:
            self.enabled = True
            self.started = False
            self.stopped = False

        def start(self) -> None:
            self.started = True
            raise RuntimeError("fake Docker runtime unavailable")

        def stop(self) -> None:
            self.stopped = True

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "qq-like.sqlite3"
        self.tool_db_path = root / "tools.sqlite3"
        self.legacy_service = self.FakeLegacyService()
        self.servers = []
        servers = self.servers
        base_server = app.ThreadingHTTPServer

        class RecordingHTTPServer(base_server):
            def __init__(server_self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                servers.append(server_self)

        self.patchers = [
            mock.patch.object(app, "ThreadingHTTPServer", RecordingHTTPServer),
            mock.patch.object(
                app.QQMutualLikeService,
                "from_paths",
                return_value=self.legacy_service,
            ),
            mock.patch.multiple(
                app,
                QQ_LIKE_ENABLED=False,
                QQ_LIKE_MOBILE_ENABLED=True,
                QQ_LIKE_DB_PATH=self.db_path,
                QQ_LIKE_MOBILE_DB_PATH=self.db_path,
                QQ_LIKE_DATA_ROOT=root / "runtime",
                QQ_LIKE_MOBILE_MAX_BATCH_SIZE=8,
                QQ_LIKE_MOBILE_LEASE_SECONDS=600,
                QQ_LIKE_MOBILE_LIKES_PER_TARGET=10,
                ADMIN_PASSWORD="test-admin-password",
                ADMIN_SESSIONS={},
                DB_PATH=self.tool_db_path,
                BAIDU_SHARE_WORKER_ENABLED=False,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        for qq_number in ("100001", "100002"):
            MobileQQLikeStore(self.db_path).upsert_allowlist(qq_number)
        self.server_thread = threading.Thread(
            target=app._run_http_server,
            kwargs={"host": "127.0.0.1", "port": 0},
            daemon=True,
        )
        self.server_thread.start()
        deadline = time.time() + 5
        while not self.servers and time.time() < deadline:
            time.sleep(0.01)
        if not self.servers:
            self.fail("测试 HTTP 服务未启动")
        self.httpd = self.servers[0]
        self.port = int(self.httpd.server_address[1])

    def tearDown(self) -> None:
        if hasattr(self, "httpd"):
            self.httpd.shutdown()
            self.httpd.server_close()
        if hasattr(self, "server_thread"):
            self.server_thread.join(timeout=5)
        for patcher in reversed(getattr(self, "patchers", [])):
            patcher.stop()
        self.temp_dir.cleanup()

    def request(
        self,
        path: str,
        payload=None,
        *,
        token: str = "",
        idempotency_key: str = "",
        method: str = "POST",
    ):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.port,
            timeout=5,
        )
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = json.loads(response.read().decode("utf-8"))
            return response.status, data
        finally:
            connection.close()

    def register(self, qq_number: str):
        status, payload = self.request(
            "/api/tools/qq-like/mobile/register",
            {
                "qq_number": qq_number,
                "install_id": f"install-{qq_number}",
                "app_version": "0.1.0-test",
            },
        )
        self.assertEqual(200, status)
        heartbeat_status, _ = self.request(
            "/api/tools/qq-like/mobile/heartbeat",
            {},
            token=payload["access_token"],
        )
        self.assertEqual(200, heartbeat_status)
        return payload

    def test_four_mobile_routes_follow_android_contract(self) -> None:
        source = self.register("100001")
        target = self.register("100002")
        self.assertEqual(
            {"id", "qq_number"},
            set(source["device"]),
        )
        self.assertTrue(source["access_token"])
        self.assertNotEqual(source["device"]["id"], target["device"]["id"])

        status, heartbeat = self.request(
            "/api/tools/qq-like/mobile/heartbeat",
            {},
            token=source["access_token"],
        )
        self.assertEqual(200, status)
        self.assertEqual(source["device"]["id"], heartbeat["device"]["id"])

        status, leased = self.request(
            "/api/tools/qq-like/mobile/tasks/lease",
            {"limit": 5},
            token=source["access_token"],
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(leased["tasks"]))
        task = leased["tasks"][0]
        self.assertEqual("100002", task["target_qq"])
        self.assertTrue(
            {
                "id",
                "target_qq",
                "times",
                "lease_token",
                "lease_expires_at",
            }.issubset(task)
        )

        status, missing_key = self.request(
            "/api/tools/qq-like/mobile/tasks/result",
            {
                "task_id": task["id"],
                "lease_token": task["lease_token"],
                "outcome": "succeeded",
            },
            token=source["access_token"],
        )
        self.assertEqual(400, status)
        self.assertIn("幂等", missing_key["error"])

        result_payload = {
            "task_id": task["id"],
            "lease_token": task["lease_token"],
            "outcome": "succeeded",
            "result_code": "ok",
            "result_message": "done",
        }
        status, result = self.request(
            "/api/tools/qq-like/mobile/tasks/result",
            result_payload,
            token=source["access_token"],
            idempotency_key="http-result-one",
        )
        self.assertEqual(200, status)
        self.assertEqual("succeeded", result["task"]["status"])

        status, repeated = self.request(
            "/api/tools/qq-like/mobile/tasks/result",
            result_payload,
            token=source["access_token"],
            idempotency_key="http-result-one",
        )
        self.assertEqual(200, status)
        self.assertTrue(repeated["task"]["idempotent"])
        self.assertFalse(self.legacy_service.started)

    def test_http_rejects_unlisted_and_invalid_credentials(self) -> None:
        status, rejected = self.request(
            "/api/tools/qq-like/mobile/register",
            {
                "qq_number": "100003",
                "install_id": "install-100003",
                "app_version": "0.1.0-test",
            },
        )
        self.assertEqual(403, status)
        self.assertEqual("not_allowlisted", rejected["error_code"])

        status, invalid = self.request(
            "/api/tools/qq-like/mobile/heartbeat",
            {},
            token="invalid-token",
        )
        self.assertEqual(401, status)
        self.assertEqual("token_invalid", invalid["error_code"])

    def test_admin_mobile_overview_requires_login_and_lists_allowlist(self) -> None:
        status, unauthorized = self.request(
            "/api/admin/qq-like/mobile/overview",
            method="GET",
        )
        self.assertEqual(401, status)
        self.assertIn("登录", unauthorized["error"])

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.port,
            timeout=5,
        )
        try:
            body = json.dumps(
                {"password": "test-admin-password"}
            ).encode("utf-8")
            connection.request(
                "POST",
                "/api/admin/login",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            login_response = connection.getresponse()
            login_response.read()
            self.assertEqual(200, login_response.status)
            cookie = login_response.getheader("Set-Cookie", "").split(";", 1)[0]
        finally:
            connection.close()

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.port,
            timeout=5,
        )
        try:
            connection.request(
                "GET",
                "/api/admin/qq-like/mobile/overview",
                headers={"Cookie": cookie},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()
        self.assertEqual(200, response.status)
        self.assertEqual("success", payload["status"])
        self.assertEqual(
            {"100001", "100002"},
            {item["qq_number"] for item in payload["allowlist"]},
        )


if __name__ == "__main__":
    unittest.main()
