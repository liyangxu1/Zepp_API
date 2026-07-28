import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANDROID_ROOT = ROOT / "android-app"
JAVA_ROOT = (
    ANDROID_ROOT
    / "app/src/main/java/com/litianyi/napcatassistant"
)


class AndroidMutualLikeContractTest(unittest.TestCase):
    def test_release_address_is_production_and_local_override_is_build_only(
        self,
    ) -> None:
        build_gradle = (ANDROID_ROOT / "app/build.gradle").read_text()
        self.assertIn("https://openmemory.cloud:18080", build_gradle)
        self.assertIn("mutualLikeServerUrl", build_gradle)
        self.assertFalse(
            (
                ANDROID_ROOT
                / "app/src/debug/res/values/server_config.xml"
            ).exists()
        )

    def test_login_auto_registers_but_execution_stays_manual(self) -> None:
        activity = (JAVA_ROOT / "MainActivity.java").read_text()
        self.assertIn("registerAndHeartbeat(login.qqId)", activity)
        self.assertIn(
            "mutualLikeButton.setOnClickListener("
            "view -> beginMutualLikeSync())",
            activity,
        )
        self.assertIn("mutualLikeCancellation.set(true)", activity)
        self.assertIn("当前账号未加入测试名单", activity)

    def test_executor_pages_until_empty_and_stops_on_report_failure(self) -> None:
        executor = (JAVA_ROOT / "MutualLikeExecutor.java").read_text()
        self.assertIn("while (!canceled.get())", executor)
        self.assertIn("if (tasks.isEmpty())", executor)
        self.assertIn("flushPendingResults()", executor)
        self.assertIn("下次只补报结果", executor)
        self.assertIn("throw error;", executor)

    def test_interrupted_send_is_uncertain_and_never_replayed(self) -> None:
        journal = (JAVA_ROOT / "TaskJournal.java").read_text()
        executor = (JAVA_ROOT / "MutualLikeExecutor.java").read_text()
        self.assertIn('"outcome", "uncertain"', journal)
        self.assertIn('"app_interrupted"', journal)
        self.assertIn("JSONObject existing = journal.find(task.taskId)", executor)
        self.assertIn("跳过点赞并补报结果", executor)


if __name__ == "__main__":
    unittest.main()
