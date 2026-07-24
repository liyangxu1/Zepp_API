import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qq_like.napcat import (
    MANAGED_LABEL_KEY,
    MANAGED_LABEL_VALUE,
    NAPCAT_IMAGE,
    DockerNapCatRuntime,
    NapCatError,
    NapCatOneBotClient,
    NapCatProtocolError,
    NapCatRuntimeBusy,
    UrllibJsonTransport,
    NapCatWebUIClient,
)


CONTRIBUTOR_ID = "qlc_0123456789abcdef0123456789abcdef"


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post_json(self, path, payload, *, bearer_token=""):
        self.calls.append(
            {
                "path": path,
                "payload": payload,
                "bearer_token": bearer_token,
            }
        )
        return self.responses.pop(0)


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.managed_names = []
        self.fail_run = False

    def run(self, args, *, timeout=60):
        self.calls.append((list(args), timeout))
        if args[:2] == ["docker", "ps"]:
            stdout = "\n".join(self.managed_names)
            if stdout:
                stdout += "\n"
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        if args[:2] == ["docker", "run"]:
            if self.fail_run:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="启动失败")
            container_name = args[args.index("--name") + 1]
            self.managed_names = [container_name]
            return subprocess.CompletedProcess(args, 0, stdout="container-id\n", stderr="")
        if args[:2] == ["docker", "stop"]:
            self.managed_names = []
            return subprocess.CompletedProcess(args, 0, stdout="stopped\n", stderr="")
        raise AssertionError(f"未预期的命令: {args}")


class NapCatWebUIClientTest(unittest.TestCase):
    def test_login_and_qr_request_use_webui_credential(self) -> None:
        transport = FakeTransport(
            [
                {"code": 0, "data": {"Credential": "credential"}, "message": "success"},
                {
                    "code": 0,
                    "data": {"qrcode": "https://txz.qq.com/p?k=test"},
                    "message": "success",
                },
            ]
        )
        client = NapCatWebUIClient(transport, "secret")

        self.assertEqual("https://txz.qq.com/p?k=test", client.request_qr_code())
        self.assertEqual("/api/auth/login", transport.calls[0]["path"])
        self.assertEqual(
            "2f46dd3e88247a09cf4cb34c07ec6c857e53a08dd3e7f05b3830ff2082934098",
            transport.calls[0]["payload"]["hash"],
        )
        self.assertEqual(
            "credential",
            transport.calls[1]["bearer_token"],
        )

    def test_qr_request_rejects_insecure_url(self) -> None:
        transport = FakeTransport(
            [
                {"code": 0, "data": {"Credential": "credential"}},
                {"code": 0, "data": {"qrcode": "http://invalid.example/q"}},
            ]
        )
        with self.assertRaisesRegex(NapCatProtocolError, "安全二维码"):
            NapCatWebUIClient(transport, "secret").request_qr_code()

    def test_connection_reset_is_normalized_for_startup_retry(self) -> None:
        transport = UrllibJsonTransport("http://127.0.0.1:16199")
        with patch(
            "qq_like.napcat.urllib.request.urlopen",
            side_effect=ConnectionResetError("连接被重置"),
        ):
            with self.assertRaisesRegex(NapCatProtocolError, "连接失败"):
                transport.post_json("/api/auth/login", {"hash": "test"})


class NapCatOneBotClientTest(unittest.TestCase):
    def test_send_like_uses_only_expected_action_and_payload(self) -> None:
        transport = FakeTransport(
            [{"status": "ok", "retcode": 0, "data": None}]
        )
        client = NapCatOneBotClient(transport, "onebot-secret")

        self.assertEqual({}, client.send_like("3313696759", times=10))
        self.assertEqual(
            {
                "path": "/send_like",
                "payload": {"user_id": "3313696759", "times": 10},
                "bearer_token": "onebot-secret",
            },
            transport.calls[0],
        )

    def test_send_like_rejects_invalid_target_or_count(self) -> None:
        client = NapCatOneBotClient(FakeTransport([]), "secret")
        with self.assertRaisesRegex(NapCatError, "QQ 号格式"):
            client.send_like("../qqbot")
        with self.assertRaisesRegex(NapCatError, "1-10"):
            client.send_like("3313696759", times=11)

    def test_onebot_error_is_not_treated_as_success(self) -> None:
        transport = FakeTransport(
            [
                {
                    "status": "failed",
                    "retcode": 1200,
                    "message": "点赞失败",
                    "data": None,
                }
            ]
        )
        with self.assertRaisesRegex(NapCatProtocolError, "点赞失败"):
            NapCatOneBotClient(transport, "secret").send_like("3313696759")


class DockerNapCatRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runner = FakeRunner()
        self.runtime = DockerNapCatRuntime(
            Path(self.temp_dir.name),
            runner=self.runner,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_prepare_session_writes_private_minimal_config(self) -> None:
        session = self.runtime.prepare_session(CONTRIBUTOR_ID)
        secret_path = session.root / "private.json"
        config_path = session.root / "config" / "onebot11.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(0o600, os.stat(secret_path).st_mode & 0o777)
        self.assertEqual(0o600, os.stat(config_path).st_mode & 0o777)
        self.assertEqual(1, len(config["network"]["httpServers"]))
        self.assertEqual([], config["network"]["httpClients"])
        self.assertEqual([], config["network"]["websocketServers"])
        self.assertEqual([], config["network"]["websocketClients"])
        self.assertEqual(
            session.onebot_token,
            config["network"]["httpServers"][0]["token"],
        )

        repeated = self.runtime.prepare_session(CONTRIBUTOR_ID)
        self.assertEqual(session.webui_token, repeated.webui_token)
        self.assertEqual(session.onebot_token, repeated.onebot_token)

    def test_start_uses_pinned_image_loopback_ports_and_limits(self) -> None:
        session = self.runtime.start(CONTRIBUTOR_ID)
        run_command = self.runner.calls[-1][0]

        self.assertEqual(["docker", "run"], run_command[:2])
        self.assertEqual(NAPCAT_IMAGE, run_command[-1])
        self.assertIn("--pull", run_command)
        self.assertIn("never", run_command)
        self.assertIn("--memory", run_command)
        self.assertIn("512m", run_command)
        self.assertIn("--cpus", run_command)
        self.assertIn("0.75", run_command)
        self.assertIn("--pids-limit", run_command)
        self.assertIn("160", run_command)
        self.assertIn("127.0.0.1:16199:6099", run_command)
        self.assertIn("127.0.0.1:16100:3000", run_command)
        self.assertIn(
            f"{MANAGED_LABEL_KEY}={MANAGED_LABEL_VALUE}",
            run_command,
        )
        self.assertNotIn("/data/web/qqbot", " ".join(run_command))
        self.assertEqual(
            f"qq-like-napcat-{CONTRIBUTOR_ID[-12:]}",
            session.container_name,
        )

    def test_runtime_rejects_another_managed_container(self) -> None:
        self.runner.managed_names = ["qq-like-napcat-other"]
        with self.assertRaisesRegex(NapCatRuntimeBusy, "另一个 QQ"):
            self.runtime.start(CONTRIBUTOR_ID)

    def test_record_login_adds_only_account_environment(self) -> None:
        self.runtime.record_login(CONTRIBUTOR_ID, "3313696759")
        self.runtime.start(CONTRIBUTOR_ID)
        run_command = self.runner.calls[-1][0]
        self.assertIn("ACCOUNT=3313696759", run_command)

    def test_stop_only_targets_current_managed_container(self) -> None:
        session = self.runtime.start(CONTRIBUTOR_ID)
        self.runtime.stop(CONTRIBUTOR_ID)
        stop_command = self.runner.calls[-1][0]
        self.assertEqual(
            ["docker", "stop", "--time", "20", session.container_name],
            stop_command,
        )

    def test_qr_reader_requires_png_signature(self) -> None:
        session = self.runtime.prepare_session(CONTRIBUTOR_ID)
        session.qr_code_path.write_bytes(b"not-png")
        with self.assertRaisesRegex(NapCatError, "有效 PNG"):
            self.runtime.read_qr_code_png(session)
        session.qr_code_path.write_bytes(b"\x89PNG\r\n\x1a\npayload")
        self.assertTrue(
            self.runtime.read_qr_code_png(session).startswith(b"\x89PNG")
        )

    def test_invalid_contributor_id_cannot_escape_session_root(self) -> None:
        with self.assertRaisesRegex(NapCatError, "ID 格式"):
            self.runtime.prepare_session("../../qqbot")


if __name__ == "__main__":
    unittest.main()
