import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from qq_like import QQLikeStore, QQLikeStoreError


SHANGHAI_TZ = timezone(timedelta(hours=8))


class QQLikeStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 7, 24, 10, 0, tzinfo=SHANGHAI_TZ)
        self.store = QQLikeStore(
            Path(self.temp_dir.name) / "test.sqlite3",
            now_factory=lambda: self.now,
        )
        self.store.init_schema()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_active(self, qq_number: str):
        created = self.store.create_contributor()
        self.store.mark_contributor_active(created["contributor_id"], qq_number)
        return created

    def test_storage_permissions_and_stale_pending_detection(self) -> None:
        self.assertEqual(0o700, os.stat(self.store.db_path.parent).st_mode & 0o777)
        self.assertEqual(0o600, os.stat(self.store.db_path).st_mode & 0o777)

        pending = self.store.create_contributor()
        self.create_active("100001")
        self.assertEqual(2, self.store.count_retained_contributors())
        self.now += timedelta(hours=25)

        stale = self.store.list_stale_pending_contributors()
        self.assertEqual(
            [pending["contributor_id"]],
            [item["id"] for item in stale],
        )

    def test_create_authenticate_and_recover_contributor(self) -> None:
        created = self.store.create_contributor()
        contributor = self.store.authenticate(created["access_token"])
        self.assertEqual(created["contributor_id"], contributor["id"])
        self.assertEqual("pending_login", contributor["status"])

        replacement = self.store.recover_access(
            created["contributor_id"],
            created["recovery_code"],
        )
        with self.assertRaises(QQLikeStoreError):
            self.store.authenticate(created["access_token"])
        self.assertEqual(
            created["contributor_id"],
            self.store.authenticate(replacement)["id"],
        )

    def test_request_requires_active_contributor(self) -> None:
        created = self.store.create_contributor()
        with self.assertRaisesRegex(QQLikeStoreError, "当前不可用"):
            self.store.create_request(
                contributor_id=created["contributor_id"],
                target_qq="3313696759",
                idempotency_key="pending-account",
            )

    def test_single_account_waits_for_another_source(self) -> None:
        contributor = self.create_active("100001")
        request = self.store.create_request(
            contributor_id=contributor["contributor_id"],
            target_qq="3313696759",
            idempotency_key="single-source",
        )
        self.assertEqual("waiting_source", request["status"])
        self.assertIsNone(request["source_contributor_id"])

    def test_second_account_is_assigned_and_never_self_likes(self) -> None:
        requester = self.create_active("100001")
        source = self.create_active("100002")
        request = self.store.create_request(
            contributor_id=requester["contributor_id"],
            target_qq="100001",
            idempotency_key="two-sources",
        )
        self.assertEqual("assigned", request["status"])
        self.assertEqual(source["contributor_id"], request["source_contributor_id"])

    def test_contributor_has_one_request_per_day(self) -> None:
        requester = self.create_active("100001")
        self.create_active("100002")
        self.store.create_request(
            contributor_id=requester["contributor_id"],
            target_qq="3313696759",
            idempotency_key="daily-one",
        )
        with self.assertRaisesRegex(QQLikeStoreError, "今天已经"):
            self.store.create_request(
                contributor_id=requester["contributor_id"],
                target_qq="3313696760",
                idempotency_key="daily-two",
            )

    def test_idempotency_returns_original_request(self) -> None:
        requester = self.create_active("100001")
        self.create_active("100002")
        first = self.store.create_request(
            contributor_id=requester["contributor_id"],
            target_qq="3313696759",
            idempotency_key="same-request",
        )
        second = self.store.create_request(
            contributor_id=requester["contributor_id"],
            target_qq="3313696759",
            idempotency_key="same-request",
        )
        self.assertEqual(first["id"], second["id"])

    def test_idempotency_cannot_cross_contributors(self) -> None:
        requester_one = self.create_active("100001")
        requester_two = self.create_active("100002")
        self.create_active("100003")
        self.store.create_request(
            contributor_id=requester_one["contributor_id"],
            target_qq="3313696759",
            idempotency_key="same-key",
        )
        with self.assertRaisesRegex(QQLikeStoreError, "其他请求"):
            self.store.create_request(
                contributor_id=requester_two["contributor_id"],
                target_qq="3313696760",
                idempotency_key="same-key",
            )

    def test_source_account_is_used_once_per_day(self) -> None:
        source_one = self.create_active("100001")
        source_two = self.create_active("100002")
        first = self.store.create_request(
            admin=True,
            target_qq="3313696759",
            idempotency_key="source-one",
        )
        second = self.store.create_request(
            admin=True,
            target_qq="3313696760",
            idempotency_key="source-two",
        )
        third = self.store.create_request(
            admin=True,
            target_qq="3313696761",
            idempotency_key="source-three",
        )
        self.assertEqual(
            {source_one["contributor_id"], source_two["contributor_id"]},
            {first["source_contributor_id"], second["source_contributor_id"]},
        )
        self.assertEqual("waiting_source", third["status"])

    def test_admin_bypasses_contribution_but_not_source_capacity(self) -> None:
        source = self.create_active("100001")
        first = self.store.create_request(
            admin=True,
            target_qq="3313696759",
            idempotency_key="admin-one",
        )
        second = self.store.create_request(
            admin=True,
            target_qq="3313696760",
            idempotency_key="admin-two",
        )
        self.assertEqual(source["contributor_id"], first["source_contributor_id"])
        self.assertEqual("waiting_source", second["status"])

    def test_uncertain_result_keeps_daily_source_usage(self) -> None:
        source = self.create_active("100001")
        request = self.store.create_request(
            admin=True,
            target_qq="3313696759",
            idempotency_key="uncertain",
        )
        self.assertTrue(
            self.store.begin_request(request["id"], source["contributor_id"])
        )
        self.store.finish_request(
            request["id"],
            status="uncertain",
            result_code="timeout",
            result_message="执行结果待确认",
        )
        admin_request = self.store.create_request(
            admin=True,
            target_qq="3313696760",
            idempotency_key="after-uncertain",
        )
        self.assertEqual("waiting_source", admin_request["status"])

    def test_revoke_releases_unstarted_source_assignment(self) -> None:
        requester = self.create_active("100001")
        source = self.create_active("100002")
        request = self.store.create_request(
            contributor_id=requester["contributor_id"],
            target_qq="3313696759",
            idempotency_key="revoke-source",
        )
        self.assertEqual(source["contributor_id"], request["source_contributor_id"])
        replacement = self.store.create_contributor()
        self.store.revoke_contributor(source["contributor_id"])
        waiting = self.store.get_request(request["id"])
        self.assertEqual("waiting_source", waiting["status"])
        self.store.mark_contributor_active(replacement["contributor_id"], "100003")
        updated = self.store.get_request(request["id"])
        self.assertEqual("assigned", updated["status"])
        self.assertEqual(
            replacement["contributor_id"],
            updated["source_contributor_id"],
        )

    def test_release_offline_source_reassigns_without_consuming_it(self) -> None:
        requester = self.create_active("100001")
        source = self.create_active("100002")
        request = self.store.create_request(
            contributor_id=requester["contributor_id"],
            target_qq="3313696759",
            idempotency_key="offline-source",
        )
        self.assertEqual(source["contributor_id"], request["source_contributor_id"])
        replacement = self.create_active("100003")
        self.store.update_contributor_status(
            source["contributor_id"],
            "offline",
            error="登录态失效",
        )
        self.assertTrue(
            self.store.release_assignment(
                request["id"],
                source["contributor_id"],
                reason="登录态失效",
            )
        )
        updated = self.store.get_request(request["id"])
        self.assertEqual("assigned", updated["status"])
        self.assertEqual(
            replacement["contributor_id"],
            updated["source_contributor_id"],
        )

        self.store.update_contributor_status(
            requester["contributor_id"],
            "paused",
        )
        self.store.update_contributor_status(
            replacement["contributor_id"],
            "paused",
        )
        self.store.mark_contributor_active(
            source["contributor_id"],
            "100002",
        )
        admin_request = self.store.create_request(
            admin=True,
            target_qq="3313696760",
            idempotency_key="reuse-recovered-source-capacity",
        )
        self.assertEqual(
            source["contributor_id"],
            admin_request["source_contributor_id"],
        )

    def test_interrupted_running_request_becomes_uncertain(self) -> None:
        source = self.create_active("100001")
        request = self.store.create_request(
            admin=True,
            target_qq="3313696759",
            idempotency_key="interrupted",
        )
        self.assertTrue(
            self.store.begin_request(request["id"], source["contributor_id"])
        )
        self.assertEqual(1, self.store.recover_interrupted_requests())
        recovered = self.store.get_request(request["id"])
        self.assertEqual("uncertain", recovered["status"])
        self.assertEqual("worker_interrupted", recovered["result_code"])

    def test_runtime_lease_allows_only_one_owner(self) -> None:
        self.assertTrue(
            self.store.acquire_runtime_lease(
                lease_name="napcat-worker",
                owner_id="worker-a",
            )
        )
        self.assertFalse(
            self.store.acquire_runtime_lease(
                lease_name="napcat-worker",
                owner_id="worker-b",
            )
        )
        self.store.release_runtime_lease(
            lease_name="napcat-worker",
            owner_id="worker-a",
        )
        self.assertTrue(
            self.store.acquire_runtime_lease(
                lease_name="napcat-worker",
                owner_id="worker-b",
            )
        )


if __name__ == "__main__":
    unittest.main()
