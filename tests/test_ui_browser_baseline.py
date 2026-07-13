import hashlib
import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright


BROWSER_MIDDLEWARE = [
    "tests.browser_support.BrowserTenantMiddleware",
    *settings.MIDDLEWARE,
]
SCREENSHOT_DIR = Path(settings.BASE_DIR) / "docs" / "ui-baseline"
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "tablet": {"width": 1024, "height": 768},
    "mobile": {"width": 390, "height": 844},
}


@override_settings(
    ROOT_URLCONF="config.urls",
    MIDDLEWARE=BROWSER_MIDDLEWARE,
    ALLOWED_HOSTS=["*"],
    DEBUG=True,
    ZEUS_APP_SHELL_ENABLED=True,
)
class TestUIBrowserBaseline(StaticLiveServerTestCase):
    def _assert_no_horizontal_overflow(self, page):
        has_overflow = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        self.assertFalse(has_overflow, f"Overflow orizzontale su {page.url}")

    def _assert_visual_baseline(self, page, name):
        page.add_style_tag(
            content="""
                *, *::before, *::after {
                    animation: none !important;
                    transition: none !important;
                    caret-color: transparent !important;
                }
                .cursor-glow, .scroll-progress { display: none !important; }
            """
        )
        screenshot = page.screenshot(full_page=True, animations="disabled")
        baseline_path = SCREENSHOT_DIR / f"{name}.png"

        if os.environ.get("ZEUS_UPDATE_UI_BASELINE") == "1":
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_bytes(screenshot)

        self.assertTrue(
            baseline_path.exists(),
            f"Baseline assente: eseguire con ZEUS_UPDATE_UI_BASELINE=1 ({baseline_path})",
        )
        expected_hash = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
        actual_hash = hashlib.sha256(screenshot).hexdigest()
        self.assertEqual(actual_hash, expected_hash, f"Regressione visuale: {name}")

    def test_login_and_dashboard_visual_baselines(self):
        user = get_user_model().objects.create_user(
            username="browser-baseline",
            email="browser-baseline@example.com",
            password="test-password",
        )
        self.client.force_login(user)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for viewport_name, viewport in VIEWPORTS.items():
                public_context = browser.new_context(
                    viewport=viewport,
                    color_scheme="light",
                    reduced_motion="reduce",
                )
                public_context.add_init_script(
                    "localStorage.setItem('zeus-theme', 'light')"
                )
                login_page = public_context.new_page()
                login_response = login_page.goto(
                    f"{self.live_server_url}{reverse('account_login')}",
                    wait_until="networkidle",
                )

                self.assertTrue(login_response.ok)
                self.assertEqual(login_page.locator("h1").inner_text(), "Bentornato")
                self.assertTrue(
                    login_page.locator('input[name="csrfmiddlewaretoken"]').count()
                )
                self._assert_no_horizontal_overflow(login_page)
                self._assert_visual_baseline(login_page, f"login-{viewport_name}")
                public_context.close()

                tenant_context = browser.new_context(
                    viewport=viewport,
                    color_scheme="light",
                    reduced_motion="reduce",
                    extra_http_headers={"X-Zeus-Test-Tenant": "ui-baseline"},
                )
                tenant_context.add_init_script(
                    "localStorage.setItem('zeus-theme', 'light')"
                )
                tenant_context.add_cookies(
                    [
                        {
                            "name": settings.SESSION_COOKIE_NAME,
                            "value": session_cookie,
                            "url": self.live_server_url,
                        }
                    ]
                )
                dashboard_page = tenant_context.new_page()
                dashboard_response = dashboard_page.goto(
                    f"{self.live_server_url}{reverse('tenant-dashboard')}",
                    wait_until="networkidle",
                )

                self.assertTrue(dashboard_response.ok)
                self.assertEqual(dashboard_page.locator("h1").inner_text(), "UI Baseline")
                self.assertTrue(dashboard_page.get_by_text("Inizia onboarding").count())
                self._assert_no_horizontal_overflow(dashboard_page)
                self._assert_visual_baseline(dashboard_page, f"dashboard-{viewport_name}")

                onboarding_page = tenant_context.new_page()
                onboarding_response = onboarding_page.goto(
                    f"{self.live_server_url}{reverse('onboarding-index')}",
                    wait_until="networkidle",
                )

                self.assertTrue(onboarding_response.ok)
                self.assertEqual(onboarding_page.locator("h1").inner_text(), "Onboarding")
                self.assertTrue(onboarding_page.locator("#onboarding-step").count())
                self._assert_no_horizontal_overflow(onboarding_page)
                self._assert_visual_baseline(onboarding_page, f"onboarding-{viewport_name}")

                products_page = tenant_context.new_page()
                products_response = products_page.goto(
                    f"{self.live_server_url}{reverse('product-list-create')}",
                    wait_until="networkidle",
                )

                self.assertTrue(products_response.ok)
                self.assertEqual(products_page.locator("h1").inner_text(), "Specialisti")
                self.assertTrue(products_page.locator('form input[name="name"]').count())
                self._assert_no_horizontal_overflow(products_page)
                self._assert_visual_baseline(products_page, f"products-{viewport_name}")
                tenant_context.close()
            browser.close()

    def test_app_shell_preview_visual_baselines(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for viewport_name, viewport in VIEWPORTS.items():
                context = browser.new_context(
                    viewport=viewport,
                    color_scheme="light",
                    reduced_motion="reduce",
                )
                page = context.new_page()
                response = page.goto(
                    f"{self.live_server_url}{reverse('app-shell-preview')}",
                    wait_until="networkidle",
                )

                self.assertTrue(response.ok)
                self.assertEqual(page.locator("h1").inner_text(), "Contratto App Shell")
                self.assertTrue(page.locator("#app-sidebar").count())
                self.assertTrue(page.locator("#app-header").count())
                self.assertTrue(page.locator("#app-main").count())
                self._assert_no_horizontal_overflow(page)
                self._assert_visual_baseline(page, f"app-shell-preview-{viewport_name}")
                context.close()
            browser.close()
