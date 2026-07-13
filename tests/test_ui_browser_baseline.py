import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright

from apps.companies.dna_schemas import LAYER_KEYS, PRODUCT_LAYER_KEYS
from apps.companies.models import (
    Company,
    CompanyDNA,
    CompanyQuestion,
    Product,
    ProductDNA,
)


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

    @override_settings(ZEUS_APP_SHELL_ENABLED=True)
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

    @override_settings(ZEUS_APP_SHELL_ENABLED=True)
    def test_tenant_app_shell_visual_baselines(self):
        user = get_user_model().objects.create_user(
            username="browser-app-shell",
            email="browser-app-shell@example.com",
            password="test-password",
        )
        self.client.force_login(user)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for viewport_name, viewport in VIEWPORTS.items():
                context = browser.new_context(
                    viewport=viewport,
                    color_scheme="light",
                    reduced_motion="reduce",
                    extra_http_headers={"X-Zeus-Test-Tenant": "ui-baseline"},
                )
                context.add_init_script(
                    "localStorage.setItem('zeus-theme', 'light')"
                )
                context.add_cookies(
                    [
                        {
                            "name": settings.SESSION_COOKIE_NAME,
                            "value": session_cookie,
                            "url": self.live_server_url,
                        }
                    ]
                )

                dashboard_page = context.new_page()
                dashboard_response = dashboard_page.goto(
                    f"{self.live_server_url}{reverse('tenant-dashboard')}",
                    wait_until="networkidle",
                )

                self.assertTrue(dashboard_response.ok)
                self.assertTrue(dashboard_page.locator(".zeus-app-shell--tenant").count())
                self.assertEqual(dashboard_page.locator("h1").inner_text(), "UI Baseline")
                self.assertEqual(
                    dashboard_page.locator(".zeus-app-nav a.is-active").inner_text(),
                    "Dashboard",
                )
                self.assertTrue(dashboard_page.get_by_text("Inizia onboarding").count())
                self._assert_no_horizontal_overflow(dashboard_page)
                self._assert_visual_baseline(
                    dashboard_page,
                    f"app-shell-dashboard-{viewport_name}",
                )

                theme_toggle = dashboard_page.locator("[data-app-theme-toggle]")
                theme_toggle.click()
                self.assertTrue(
                    dashboard_page.locator("html").evaluate(
                        "element => element.classList.contains('dark')"
                    )
                )
                theme_toggle.click()
                self.assertFalse(
                    dashboard_page.locator("html").evaluate(
                        "element => element.classList.contains('dark')"
                    )
                )

                if viewport_name == "mobile":
                    menu_toggle = dashboard_page.locator("[data-app-menu-toggle]")
                    self.assertTrue(menu_toggle.is_visible())
                    menu_toggle.click()
                    self.assertEqual(menu_toggle.get_attribute("aria-expanded"), "true")
                    dashboard_page.keyboard.press("Escape")
                    self.assertEqual(menu_toggle.get_attribute("aria-expanded"), "false")

                products_page = context.new_page()
                products_response = products_page.goto(
                    f"{self.live_server_url}{reverse('product-list-create')}",
                    wait_until="networkidle",
                )

                self.assertTrue(products_response.ok)
                self.assertTrue(products_page.locator(".zeus-app-shell--tenant").count())
                self.assertEqual(products_page.locator("h1").inner_text(), "Specialisti")
                self.assertEqual(
                    products_page.locator(".zeus-app-nav a.is-active").inner_text(),
                    "Specialisti",
                )
                self.assertTrue(products_page.locator('form input[name="name"]').count())
                self.assertTrue(
                    products_page.locator('input[name="csrfmiddlewaretoken"]').count()
                )
                self._assert_no_horizontal_overflow(products_page)
                self._assert_visual_baseline(
                    products_page,
                    f"app-shell-products-{viewport_name}",
                )
                context.close()
            browser.close()

    @override_settings(ZEUS_APP_SHELL_ENABLED=True)
    def test_onboarding_app_shell_visual_baselines(self):
        user = get_user_model().objects.create_user(
            username="browser-onboarding-shell",
            email="browser-onboarding-shell@example.com",
            password="test-password",
        )
        company = Company.objects.create(
            schema_name="ui-baseline",
            name="UI Baseline",
        )
        pre_dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_PRE,
            is_current=False,
            content={key: f"Contesto preliminare {key}." for key in LAYER_KEYS},
        )
        CompanyQuestion.objects.create(
            company=company,
            dna=pre_dna,
            code="A1",
            section_key="identita",
            principle="Principio non negoziabile",
            question="Quale principio guida le decisioni tecniche del workspace?",
            answer_depth="mirata",
            answer_guidance="Descrivi una scelta concreta e il criterio usato.",
        )
        complete_content = {
            key: f"Contenuto verificato per {key}." for key in LAYER_KEYS
        }
        complete_content["sintesi_cognitiva"] = (
            "UI Baseline traduce il contesto tecnico in decisioni verificabili."
        )
        CompanyDNA.objects.create(
            company=company,
            version=2,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content=complete_content,
        )

        self.client.force_login(user)
        session = self.client.session
        session["pending_complete_min_version"] = 3
        session.save()
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        surfaces = [
            (
                "onboarding",
                f"{reverse('onboarding-index')}?revise=1",
                "Onboarding",
                "#onboarding-step",
                True,
            ),
            (
                "dna-questions",
                reverse("dna-questions"),
                "10 domande per completare il DNA",
                'textarea[name^="answer_"]',
                True,
            ),
            (
                "dna-generating",
                reverse("dna-generating"),
                "Generazione DNA in corso",
                '[hx-target="body"]',
                True,
            ),
            (
                "dna-review",
                reverse("dna-review"),
                "Revisione DNA",
                "#dna-review-root",
                True,
            ),
            (
                "dna-visualize",
                reverse("dna-visualize"),
                "Visualizzazione finale",
                "text=DNA Generale",
                False,
            ),
        ]

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for viewport_name, viewport in VIEWPORTS.items():
                context = browser.new_context(
                    viewport=viewport,
                    color_scheme="light",
                    reduced_motion="reduce",
                    extra_http_headers={"X-Zeus-Test-Tenant": "ui-baseline"},
                )
                context.add_init_script(
                    "localStorage.setItem('zeus-theme', 'light')"
                )
                context.add_cookies(
                    [
                        {
                            "name": settings.SESSION_COOKIE_NAME,
                            "value": session_cookie,
                            "url": self.live_server_url,
                        }
                    ]
                )

                for surface_name, path, heading, contract, requires_htmx in surfaces:
                    page_errors = []
                    page = context.new_page()
                    page.on(
                        "pageerror",
                        lambda error, errors=page_errors: errors.append(str(error)),
                    )
                    response = page.goto(
                        f"{self.live_server_url}{path}",
                        wait_until="networkidle",
                    )

                    self.assertTrue(response.ok)
                    self.assertTrue(page.locator(".zeus-app-shell--tenant").count())
                    self.assertEqual(page.locator("h1").first.inner_text(), heading)
                    self.assertEqual(
                        page.locator(".zeus-app-nav a.is-active").inner_text(),
                        "Onboarding",
                    )
                    self.assertTrue(page.locator(contract).count())
                    if requires_htmx:
                        self.assertTrue(
                            page.evaluate("typeof window.htmx !== 'undefined'")
                        )
                    for overlay_selector in (
                        "#generating-popup",
                        "#dna-popup-overlay",
                    ):
                        overlay = page.locator(overlay_selector)
                        if overlay.count():
                            self.assertFalse(overlay.is_visible())
                    self.assertFalse(page_errors)
                    self._assert_no_horizontal_overflow(page)
                    self._assert_visual_baseline(
                        page,
                        f"app-shell-{surface_name}-{viewport_name}",
                    )
                    page.close()
                context.close()
            browser.close()

    @override_settings(ZEUS_APP_SHELL_ENABLED=True)
    def test_specialist_app_shell_visual_baselines(self):
        user = get_user_model().objects.create_user(
            username="browser-specialist-shell",
            email="browser-specialist-shell@example.com",
            password="test-password",
        )
        company = Company.objects.create(
            schema_name="ui-baseline",
            name="UI Baseline",
        )
        product = Product.objects.create(
            company=company,
            name="Vasca Premium",
            slug="vasca-premium",
            tipologia="Componente",
            status=Product.STATUS_BOZZA,
        )
        ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_PRE,
            is_current=False,
            content={key: f"Contesto pre-DNA {key}." for key in PRODUCT_LAYER_KEYS},
        )
        complete_content = {
            key: f"Contenuto verificato per {key}." for key in PRODUCT_LAYER_KEYS
        }
        complete_dna = ProductDNA.objects.create(
            product=product,
            version=2,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content=complete_content,
        )
        ProductDNA.objects.filter(pk=complete_dna.pk).update(
            created_at=datetime(2026, 7, 13, 18, 56, tzinfo=UTC)
        )

        self.client.force_login(user)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        surfaces = [
            (
                "specialist-list",
                reverse("product-list-create"),
                "Specialisti",
                'input[name="name"]',
                False,
            ),
            (
                "specialist-detail",
                reverse("product-detail", args=[product.pk]),
                "Dettaglio Specialista",
                "text=Vasca Premium",
                False,
            ),
            (
                "specialist-questions",
                reverse("product-questions", args=[product.pk]),
                "Preparazione Domande",
                "text=Preparazione domande",
                True,
            ),
            (
                "specialist-review",
                reverse("product-review", args=[product.pk]),
                "Revisione Specialista",
                "text=Identità del prodotto",
                False,
            ),
            (
                "specialist-visualize",
                reverse("product-dna-visualize", args=[product.pk]),
                "DNA Specialista",
                "text=Identità del prodotto",
                False,
            ),
        ]

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for viewport_name, viewport in VIEWPORTS.items():
                context = browser.new_context(
                    viewport=viewport,
                    color_scheme="light",
                    reduced_motion="reduce",
                    extra_http_headers={"X-Zeus-Test-Tenant": "ui-baseline"},
                )
                context.add_init_script(
                    "localStorage.setItem('zeus-theme', 'light')"
                )
                context.add_cookies(
                    [
                        {
                            "name": settings.SESSION_COOKIE_NAME,
                            "value": session_cookie,
                            "url": self.live_server_url,
                        }
                    ]
                )

                for surface_name, path, title, contract, requires_htmx in surfaces:
                    page_errors = []
                    page = context.new_page()
                    page.on(
                        "pageerror",
                        lambda error, errors=page_errors: errors.append(str(error)),
                    )
                    response = page.goto(
                        f"{self.live_server_url}{path}",
                        wait_until="networkidle",
                    )

                    self.assertTrue(response.ok)
                    self.assertTrue(page.locator(".zeus-app-shell--tenant").count())
                    self.assertEqual(
                        page.locator(".zeus-app-breadcrumb strong").inner_text(),
                        title,
                    )
                    self.assertEqual(
                        page.locator(".zeus-app-nav a.is-active").inner_text(),
                        "Specialisti",
                    )
                    self.assertTrue(page.locator(contract).count())
                    if requires_htmx:
                        self.assertTrue(
                            page.evaluate("typeof window.htmx !== 'undefined'")
                        )
                    for overlay_selector in (
                        "#generating-popup",
                        "#dna-popup-overlay",
                    ):
                        overlay = page.locator(overlay_selector)
                        if overlay.count():
                            self.assertFalse(overlay.is_visible())
                    self.assertFalse(page_errors)
                    self._assert_no_horizontal_overflow(page)
                    self._assert_visual_baseline(
                        page,
                        f"app-shell-{surface_name}-{viewport_name}",
                    )
                    page.close()
                context.close()
            browser.close()
