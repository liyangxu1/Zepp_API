import unittest
from pathlib import Path

import app


class QQLikeCommunityTest(unittest.TestCase):
    def test_qq_like_community_is_scoped_to_the_qq_like_workspace(self) -> None:
        page = app._simple_page_html()

        self.assertIn('id="qqLikePanel"', page)
        self.assertIn('id="qqLikeCommunityTitle"', page)
        self.assertIn("QQ 互赞交流群", page)
        self.assertIn(app.QQ_LIKE_GROUP_NAME, page)
        self.assertIn(app.QQ_LIKE_GROUP_NUMBER, page)
        self.assertLess(
            page.index('id="qqLikeCommunityTitle"'),
            page.index("<h2>发起今日互赞</h2>"),
        )

    def test_original_group_and_qq_like_group_are_independent(self) -> None:
        page = app._simple_page_html()

        self.assertIn(app.QQ_GROUP_NAME, page)
        self.assertIn(app.QQ_GROUP_NUMBER, page)
        self.assertNotEqual(app.QQ_GROUP_NUMBER, app.QQ_LIKE_GROUP_NUMBER)
        self.assertIn('id="copyGroupNumber"', page)
        self.assertIn('id="qqLikeCopyGroupNumber"', page)

    def test_qq_like_group_assets_exist(self) -> None:
        asset_dir = Path(app.__file__).with_name("assets")

        for filename in ("qq-like-group.jpg", "qq-like-group-avatar.jpg"):
            path = asset_dir / filename
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 1024)

    def test_pending_login_resumes_after_page_refresh(self) -> None:
        page = app._simple_page_html()

        self.assertIn(
            "data?.contributor?.status === 'pending_login'",
            page,
        )
        self.assertIn(
            "登录任务正在后端运行，关闭或刷新页面不会中断。",
            page,
        )
        self.assertIn(
            "qqLikeReload.addEventListener('click', () => loadQQLikeDashboard())",
            page,
        )


if __name__ == "__main__":
    unittest.main()
