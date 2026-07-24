import hashlib
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qq_like.napcat import (
    ManagedNapCatContainer,
    NapCatError,
    NapCatProtocolError,
    NapCatRuntimeBusy,
    NapCatSession,
    NapCatWebUIRateLimitError,
)
from qq_like.service import QQMutualLikeService, QQMutualLikeServiceError
from qq_like.store import QQLikeStore, QQLikeStoreError


class FakeWebUI:
    def __init__(self, runtime):
        self.runtime = runtime

    def request_qr_code(self):
        self.runtime.qr_request_count += 1
        if not self.runtime.qr_generation_delayed:
            self.runtime.write_qr()
        return "https://txz.qq.com/p?k=test"

    def refresh_qr_code(self):
        self.runtime.refresh_count += 1
        self.runtime.qr_generation += 1
        return {}

    def check_login_status(self):
        self.runtime.webui_status_count += 1
        if self.runtime.webui_status_error:
            raise self.runtime.webui_status_error
        return {
            "isLogin": self.runtime.login_ready,
            "isOffline": not self.runtime.login_ready,
            "loginError": "",
        }

    def login(self):
        self.runtime.webui_auth_count += 1
        return "credential"


class FakeOneBot:
    def __init__(self, runtime, session):
        self.runtime = runtime
        self.session = session

    def get_status(self):
        if self.runtime.login_error or not self.runtime.onebot_ready:
            raise NapCatProtocolError(self.runtime.login_error)
        return {
            "online": self.runtime.status_online,
            "good": self.runtime.status_good,
        }

    def get_login_info(self):
        if self.runtime.login_error:
            raise NapCatProtocolError(self.runtime.login_error)
        qq_number = self.runtime.qq_numbers.get(self.session.contributor_id)
        if not qq_number:
            qq_number = self.runtime.pending_login_qq
        return {"user_id": qq_number, "nickname": "测试账号"}

    def send_like(self, target_qq, *, times=10):
        self.runtime.send_attempts += 1
        if self.runtime.send_error:
            raise NapCatProtocolError(self.runtime.send_error)
        self.runtime.likes.append(
            {
                "source": self.session.contributor_id,
                "target": target_qq,
                "times": times,
            }
        )
        return {}


class FakeRuntime:
    def __init__(self, root):
        self.root = Path(root)
        self.active_contributor = ""
        self.qq_numbers = {}
        self.pending_login_qq = "3313696759"
        self.login_ready = False
        self.onebot_ready = True
        self.status_online = True
        self.status_good = True
        self.login_error = ""
        self.send_error = ""
        self.refresh_count = 0
        self.qr_request_count = 0
        self.qr_generation = 1
        self.qr_generation_delayed = False
        self.webui_auth_count = 0
        self.webui_status_count = 0
        self.webui_status_error = None
        self.webui = FakeWebUI(self)
        self.likes = []
        self.send_attempts = 0
        self.deleted = []
        self.stop_all_count = 0
        self.start_count = 0
        self.start_error = ""
        self.managed = []
        self.delete_running_failures = 0

    def _session(self, contributor_id):
        root = self.root / contributor_id
        (root / "cache").mkdir(parents=True, exist_ok=True)
        return NapCatSession(
            contributor_id=contributor_id,
            root=root,
            webui_token="webui",
            onebot_token="onebot",
            webui_port=16199,
            onebot_port=16100,
            qq_number=self.qq_numbers.get(contributor_id, ""),
        )

    def start(self, contributor_id):
        self.start_count += 1
        if self.start_error:
            raise NapCatError(self.start_error)
        if self.active_contributor and self.active_contributor != contributor_id:
            raise NapCatRuntimeBusy("busy")
        self.active_contributor = contributor_id
        return self._session(contributor_id)

    def stop(self, contributor_id):
        if self.active_contributor == contributor_id:
            self.active_contributor = ""

    def stop_all_managed(self):
        self.active_contributor = ""
        self.managed = []
        self.stop_all_count += 1
        return 0

    def delete_session(self, contributor_id):
        if self.delete_running_failures > 0:
            self.delete_running_failures -= 1
            raise NapCatError("贡献账号仍在运行，不能删除登录信息")
        self.deleted.append(contributor_id)

    def wait_for_webui(self, session, *, attempts=30, interval_seconds=1):
        self.webui_auth_count += 1
        return self.webui

    def prepare_session(self, contributor_id):
        return self._session(contributor_id)

    def managed_containers(self):
        return list(self.managed)

    def read_qr_code_png(self, session):
        return session.qr_code_path.read_bytes()

    def read_qr_code_png_with_revision(self, session):
        try:
            payload = session.qr_code_path.read_bytes()
        except OSError as exc:
            raise NapCatError("登录二维码尚未生成") from exc
        return payload, hashlib.sha256(payload).hexdigest()[:16]

    def clear_qr_code(self, session):
        session.qr_code_path.unlink(missing_ok=True)

    def wait_for_qr_code(
        self,
        session,
        *,
        previous_revision="",
        attempts=30,
        interval_seconds=0.1,
    ):
        if not session.qr_code_path.exists():
            raise NapCatError("登录二维码尚未生成")
        payload, revision = self.read_qr_code_png_with_revision(session)
        if previous_revision and revision == previous_revision:
            raise NapCatError("新的登录二维码尚未生成")
        return payload, revision

    def write_qr(self):
        if not self.active_contributor:
            return
        session = self._session(self.active_contributor)
        session.qr_code_path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + f"fake-{self.qr_generation}".encode("ascii")
        )

    def onebot_client(self, session):
        return FakeOneBot(self, session)

    def record_login(self, contributor_id, qq_number):
        self.qq_numbers[contributor_id] = qq_number
        return self._session(contributor_id)


class QQMutualLikeServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = QQLikeStore(root / "test.sqlite3")
        self.runtime = FakeRuntime(root / "runtime")
        self.service = QQMutualLikeService(
            self.store,
            self.runtime,
            sleep=lambda _: None,
        )
        self.store.init_schema()

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_active(self, qq_number):
        created = self.store.create_contributor()
        self.store.mark_contributor_active(created["contributor_id"], qq_number)
        self.runtime.record_login(created["contributor_id"], qq_number)
        return created

    def advance_login(self, contributor_id=""):
        with self.service._login_lock:
            return self.service._advance_active_login_locked(contributor_id)

    def test_new_contribution_qr_and_login_activation(self):
        started = self.service.start_login(
            remote_addr="127.0.0.1",
            user_agent="test",
        )
        access_token = started["access_token"]
        contributor_id = started["contributor"]["contributor_id"]
        self.assertEqual("waiting_scan", started["login_state"])
        self.assertTrue(started["recovery_code"])
        self.assertTrue(
            self.service.read_login_qr(access_token).startswith(b"\x89PNG")
        )

        self.runtime.login_ready = True
        self.advance_login(contributor_id)
        completed = self.service.poll_login(access_token)
        self.assertEqual("active", completed["login_state"])
        self.assertEqual("贡献账号在线", completed["dashboard"]["contributor"]["status_label"])
        self.assertEqual("", self.runtime.active_contributor)
        self.assertEqual(
            "3313696759",
            self.store.get_contributor(contributor_id)["qq_number"],
        )

    def test_only_one_login_can_run_at_a_time(self):
        self.service.start_login(remote_addr="first", user_agent="test")
        with self.assertRaisesRegex(QQMutualLikeServiceError, "另一个 QQ"):
            self.service.start_login(remote_addr="second", user_agent="test")

    def test_status_reads_do_not_repeat_webui_authentication(self):
        started = self.service.start_login(
            remote_addr="127.0.0.1",
            user_agent="test",
        )

        for _ in range(100):
            status = self.service.poll_login(started["access_token"])
            self.assertEqual("waiting_scan", status["login_state"])

        self.assertEqual(1, self.runtime.webui_auth_count)
        self.assertEqual(0, self.runtime.webui_status_count)
        self.advance_login(started["contributor"]["contributor_id"])
        self.assertEqual(1, self.runtime.webui_status_count)
        self.assertEqual(1, self.runtime.webui_auth_count)

    def test_webui_rate_limit_waits_without_authentication_storm(self):
        started = self.service.start_login(
            remote_addr="127.0.0.1",
            user_agent="test",
        )
        self.runtime.webui_status_error = NapCatWebUIRateLimitError(
            "login rate limit"
        )

        self.advance_login(started["contributor"]["contributor_id"])
        for _ in range(20):
            self.advance_login(started["contributor"]["contributor_id"])

        status = self.service.poll_login(started["access_token"])
        self.assertEqual("waiting_scan", status["login_state"])
        self.assertIn("一分钟后", status["login_error"])
        self.assertEqual(1, self.runtime.webui_status_count)
        self.assertEqual(1, self.runtime.webui_auth_count)

    def test_transient_webui_error_does_not_claim_scan_confirmed(self):
        started = self.service.start_login(
            remote_addr="127.0.0.1",
            user_agent="test",
        )
        self.runtime.webui_status_error = NapCatProtocolError("连接被重置")

        self.advance_login(started["contributor"]["contributor_id"])

        status = self.service.poll_login(started["access_token"])
        self.assertEqual("waiting_scan", status["login_state"])
        self.assertIn("连接被重置", status["login_error"])

    def test_delayed_qr_file_keeps_started_login_and_later_updates_revision(self):
        self.runtime.qr_generation_delayed = True
        started = self.service.start_login(
            remote_addr="127.0.0.1",
            user_agent="test",
        )
        contributor_id = started["contributor"]["contributor_id"]

        self.assertEqual("waiting_scan", started["login_state"])
        self.assertEqual("", started["qr_revision"])
        self.assertIn("正在生成二维码", started["login_error"])

        self.runtime.qr_generation_delayed = False
        self.runtime.write_qr()
        self.advance_login(contributor_id)

        updated = self.service.poll_login(started["access_token"])
        self.assertTrue(updated["qr_revision"])
        self.assertEqual("", updated["login_error"])

    def test_automatic_qr_replacement_changes_revision_without_extending_task(self):
        started = self.service.start_login(
            remote_addr="127.0.0.1",
            user_agent="test",
        )
        contributor_id = started["contributor"]["contributor_id"]
        initial_revision = started["qr_revision"]
        initial_expiry = self.service._active_login.expires_at

        self.runtime.qr_generation += 1
        self.runtime.write_qr()
        self.advance_login(contributor_id)

        updated = self.service.poll_login(started["access_token"])
        self.assertNotEqual(initial_revision, updated["qr_revision"])
        self.assertEqual(initial_expiry, self.service._active_login.expires_at)

    def test_manual_qr_refresh_reuses_webui_and_resets_task_deadline(self):
        started = self.service.start_login(
            remote_addr="127.0.0.1",
            user_agent="test",
        )
        initial_revision = started["qr_revision"]
        self.service._active_login.expires_at -= 120

        refreshed = self.service.refresh_login_qr(started["access_token"])

        self.assertNotEqual(initial_revision, refreshed["qr_revision"])
        self.assertGreaterEqual(refreshed["expires_in_seconds"], 299)
        self.assertEqual(1, self.runtime.webui_auth_count)
        self.assertEqual(1, self.runtime.refresh_count)

    def test_worker_confirms_login_without_browser_polling(self):
        self.service.worker_interval_seconds = 0.2
        self.service.start()
        started = self.service.start_login(
            remote_addr="127.0.0.1",
            user_agent="test",
        )
        contributor_id = started["contributor"]["contributor_id"]
        self.runtime.login_ready = True
        self.service._worker_wakeup.set()

        deadline = time.time() + 2
        while time.time() < deadline:
            if self.store.get_contributor(contributor_id)["status"] == "active":
                break
            time.sleep(0.02)
        self.service.stop()

        self.assertEqual(
            "active",
            self.store.get_contributor(contributor_id)["status"],
        )
        self.assertEqual("", self.runtime.active_contributor)

    def test_login_stays_finalizing_until_onebot_is_ready(self):
        started = self.service.start_login(
            remote_addr="127.0.0.1",
            user_agent="test",
        )
        self.runtime.login_ready = True
        self.runtime.onebot_ready = False

        self.advance_login(started["contributor"]["contributor_id"])
        pending = self.service.poll_login(started["access_token"])
        self.assertEqual("finalizing", pending["login_state"])
        self.assertNotEqual("", self.runtime.active_contributor)

        self.runtime.onebot_ready = True
        self.advance_login(started["contributor"]["contributor_id"])
        completed = self.service.poll_login(started["access_token"])
        self.assertEqual("active", completed["login_state"])
        self.assertEqual("", self.runtime.active_contributor)

    def test_expired_login_is_stopped_and_reports_rescan(self):
        started = self.service.start_login(
            remote_addr="127.0.0.1",
            user_agent="test",
        )
        contributor_id = started["contributor"]["contributor_id"]
        self.service._active_login.expires_at = self.service.time_factory() - 1

        result = self.service.poll_login(started["access_token"])

        self.assertEqual("not_started", result["login_state"])
        contributor = self.store.get_contributor(contributor_id)
        self.assertEqual("pending_login", contributor["status"])
        self.assertIn("重新扫码", contributor["last_error"])
        self.assertEqual("", self.runtime.active_contributor)

    def test_startup_recovers_confirmed_legacy_login_and_stops_container(self):
        created = self.store.create_contributor()
        contributor_id = created["contributor_id"]
        session = self.runtime.prepare_session(contributor_id)
        owner_id = f"login:{contributor_id}:legacy"
        self.store.acquire_runtime_lease(
            lease_name="qq-like-napcat-runtime",
            owner_id=owner_id,
            ttl_seconds=300,
        )
        self.runtime.active_contributor = contributor_id
        self.runtime.managed = [
            ManagedNapCatContainer(name=session.container_name)
        ]
        self.runtime.login_ready = True

        self.service._recover_managed_runtime()

        contributor = self.store.get_contributor(contributor_id)
        self.assertEqual("active", contributor["status"])
        self.assertEqual("3313696759", contributor["qq_number"])
        self.assertEqual([], self.runtime.managed)
        self.assertIsNone(
            self.store.get_runtime_lease("qq-like-napcat-runtime")
        )

    def test_startup_releases_orphaned_login_lease_and_requests_rescan(self):
        created = self.store.create_contributor()
        contributor_id = created["contributor_id"]
        owner_id = f"login:{contributor_id}:orphaned"
        self.store.acquire_runtime_lease(
            lease_name="qq-like-napcat-runtime",
            owner_id=owner_id,
            ttl_seconds=300,
        )

        self.service._recover_managed_runtime()

        contributor = self.store.get_contributor(contributor_id)
        self.assertIn("重新扫码", contributor["last_error"])
        self.assertIsNone(
            self.store.get_runtime_lease("qq-like-napcat-runtime")
        )

    def test_orphaned_pending_login_keeps_recovery_ownership(self):
        created = self.store.create_contributor()
        contributor_id = created["contributor_id"]

        marked = self.service._mark_orphaned_pending_logins()

        contributor = self.store.get_contributor(contributor_id)
        self.assertEqual(1, marked)
        self.assertEqual("pending_login", contributor["status"])
        self.assertIn("重新扫码", contributor["last_error"])
        recovered = self.store.recover_access(
            contributor_id,
            created["recovery_code"],
        )
        self.assertTrue(recovered)

    def test_session_delete_retries_container_auto_remove_race(self):
        created = self.store.create_contributor()
        self.runtime.delete_running_failures = 2

        self.service._delete_session_safely(created["contributor_id"])

        self.assertEqual(0, self.runtime.delete_running_failures)
        self.assertEqual(
            [created["contributor_id"]],
            self.runtime.deleted,
        )

    def test_contributor_request_uses_another_account_and_ten_likes(self):
        requester = self.create_active("100001")
        source = self.create_active("100002")
        request = self.service.submit_request(
            access_token=requester["access_token"],
            target_qq="3313696759",
            idempotency_key="client-request-1",
        )
        self.assertEqual("等待执行", request["status_label"])

        self.assertTrue(self.service.worker_once())
        result = self.store.get_request(request["request_id"])
        self.assertEqual("succeeded", result["status"])
        self.assertEqual(
            [
                {
                    "source": source["contributor_id"],
                    "target": "3313696759",
                    "times": 10,
                }
            ],
            self.runtime.likes,
        )

    def test_worker_requeues_before_send_when_source_login_is_invalid(self):
        requester = self.create_active("100001")
        source = self.create_active("100002")
        request = self.service.submit_request(
            access_token=requester["access_token"],
            target_qq="3313696759",
            idempotency_key="client-request-offline",
        )
        self.runtime.login_error = "登录态失效"

        self.assertTrue(self.service.worker_once())
        updated = self.store.get_request(request["request_id"])
        self.assertEqual("waiting_source", updated["status"])
        self.assertEqual(
            "offline",
            self.store.get_contributor(source["contributor_id"])["status"],
        )
        self.assertEqual([], self.runtime.likes)

    def test_send_result_error_is_marked_uncertain_without_retry(self):
        requester = self.create_active("100001")
        self.create_active("100002")
        request = self.service.submit_request(
            access_token=requester["access_token"],
            target_qq="3313696759",
            idempotency_key="client-request-uncertain",
        )
        self.runtime.send_error = "连接在返回结果前断开"

        self.assertTrue(self.service.worker_once())
        updated = self.store.get_request(request["request_id"])
        self.assertEqual("uncertain", updated["status"])
        self.assertEqual("execution_uncertain", updated["result_code"])
        self.assertEqual(1, self.runtime.send_attempts)
        self.assertEqual([], self.runtime.likes)

    def test_runtime_start_failure_does_not_disable_source_or_busy_loop(self):
        requester = self.create_active("100001")
        source = self.create_active("100002")
        request = self.service.submit_request(
            access_token=requester["access_token"],
            target_qq="3313696759",
            idempotency_key="runtime-unavailable",
        )
        self.runtime.start_error = "Docker 暂时不可用"

        self.assertTrue(self.service.worker_once())
        self.assertEqual(
            "active",
            self.store.get_contributor(source["contributor_id"])["status"],
        )
        self.assertIn(
            self.store.get_request(request["request_id"])["status"],
            {"waiting_source", "assigned"},
        )
        self.assertFalse(self.service.worker_once())
        self.assertEqual(1, self.runtime.start_count)

    def test_revoke_deletes_only_current_tool_session(self):
        contributor = self.create_active("100001")
        result = self.service.revoke(contributor["access_token"])
        self.assertEqual("success", result["status"])
        self.assertEqual(
            [contributor["contributor_id"]],
            self.runtime.deleted,
        )
        self.assertEqual(
            "revoked",
            self.store.get_contributor(contributor["contributor_id"])["status"],
        )

    def test_admin_can_submit_without_contributor(self):
        source = self.create_active("100001")
        request = self.service.submit_admin_request(
            target_qq="3313696759",
            idempotency_key="admin-1",
        )
        self.assertEqual(source["contributor_id"], self.store.get_request(request["request_id"])["source_contributor_id"])

    def test_disabled_service_does_not_start_docker(self):
        disabled = QQMutualLikeService(
            self.store,
            self.runtime,
            enabled=False,
        )
        disabled.start()
        self.assertEqual(0, self.runtime.stop_all_count)
        with self.assertRaisesRegex(QQMutualLikeServiceError, "尚未启用"):
            disabled.start_login()

    def test_contributor_limit_and_stale_pending_cleanup(self):
        root = Path(self.temp_dir.name)
        current_time = [
            datetime(2026, 7, 24, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        ]
        store = QQLikeStore(
            root / "limited.sqlite3",
            now_factory=lambda: current_time[0],
        )
        runtime = FakeRuntime(root / "limited-runtime")
        service = QQMutualLikeService(
            store,
            runtime,
            max_contributors=1,
            pending_retention_hours=24,
            sleep=lambda _: None,
        )
        stale = store.create_contributor()
        with self.assertRaisesRegex(QQMutualLikeServiceError, "服务器上限"):
            service.start_login(remote_addr="127.0.0.1")

        current_time[0] += timedelta(hours=25)
        started = service.start_login(remote_addr="127.0.0.1")
        self.assertNotEqual(
            stale["contributor_id"],
            started["contributor"]["contributor_id"],
        )
        self.assertEqual(
            "pending_login",
            store.get_contributor(stale["contributor_id"])["status"],
        )
        self.assertIn(
            "重新扫码",
            store.get_contributor(stale["contributor_id"])["last_error"],
        )
        self.assertNotIn(stale["contributor_id"], runtime.deleted)

    def test_recovery_rate_limit_is_scoped_by_remote_address(self):
        created = self.store.create_contributor()
        for _ in range(5):
            with self.assertRaises(QQLikeStoreError):
                self.service.recover_access(
                    created["contributor_id"],
                    "WRONG-CODE",
                    remote_addr="192.0.2.1",
                )
        with self.assertRaisesRegex(QQMutualLikeServiceError, "过于频繁"):
            self.service.recover_access(
                created["contributor_id"],
                "WRONG-CODE",
                remote_addr="192.0.2.1",
            )
        with self.assertRaises(QQLikeStoreError):
            self.service.recover_access(
                created["contributor_id"],
                "WRONG-CODE",
                remote_addr="192.0.2.2",
            )

    def test_rate_limit_memory_is_bounded(self):
        for index in range(4200):
            self.service._check_rate_limit(
                "login",
                f"192.0.2.{index}",
                limit=4,
                window_seconds=600,
            )
        self.assertLessEqual(len(self.service._rate_events), 4096)


if __name__ == "__main__":
    unittest.main()
