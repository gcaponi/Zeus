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
    ConsistencyIssue,
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

    def _assert_visual_baseline(self, page, name, full_page=True):
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
        screenshot = page.screenshot(full_page=full_page, animations="disabled")
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

                command_trigger = dashboard_page.locator("[data-command-open]")
                dashboard_page.keyboard.press("Control+k")
                command_palette = dashboard_page.locator("[data-command-palette]")
                command_input = dashboard_page.locator("[data-command-input]")
                self.assertTrue(command_palette.is_visible())
                self.assertEqual(command_trigger.get_attribute("aria-expanded"), "true")
                self.assertTrue(command_input.evaluate("element => element === document.activeElement"))
                dashboard_page.keyboard.press("Control+k")
                self.assertFalse(command_palette.is_visible())
                self.assertTrue(command_trigger.evaluate("element => element === document.activeElement"))
                dashboard_page.keyboard.press("Control+k")
                self.assertTrue(command_palette.is_visible())
                self.assertTrue(command_input.evaluate("element => element === document.activeElement"))
                self._assert_visual_baseline(
                    dashboard_page,
                    f"app-shell-command-palette-{viewport_name}",
                )

                command_input.fill("motore c")
                visible_commands = dashboard_page.locator(
                    "[data-command-item]:not([hidden])"
                )
                self.assertEqual(visible_commands.count(), 1)
                self.assertEqual(
                    visible_commands.locator("strong").inner_text(),
                    "Motore C",
                )
                command_input.fill("destinazione inesistente")
                self.assertTrue(
                    dashboard_page.locator("[data-command-empty]").is_visible()
                )
                command_input.fill("")
                command_input.press("ArrowDown")
                self.assertEqual(
                    dashboard_page.locator(":focus strong").inner_text(),
                    "Dashboard",
                )
                dashboard_page.keyboard.press("Escape")
                self.assertFalse(command_palette.is_visible())
                self.assertEqual(command_trigger.get_attribute("aria-expanded"), "false")
                self.assertTrue(command_trigger.evaluate("element => element === document.activeElement"))

                command_trigger.click()
                last_command = dashboard_page.locator("[data-command-item]").last
                last_command.focus()
                dashboard_page.keyboard.press("Tab")
                self.assertTrue(
                    dashboard_page.locator(
                        '[data-command-close]:not([tabindex="-1"])'
                    ).evaluate("element => element === document.activeElement")
                )
                dashboard_page.keyboard.press("Escape")

                command_trigger.click()
                command_input.press("Enter")
                dashboard_page.wait_for_load_state("networkidle")
                self.assertEqual(
                    dashboard_page.url,
                    f"{self.live_server_url}{reverse('tenant-dashboard')}",
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
                    app_main = dashboard_page.locator("#app-main")
                    sidebar = dashboard_page.locator("#app-sidebar")
                    self.assertTrue(menu_toggle.is_visible())
                    self.assertTrue(sidebar.get_attribute("inert") is not None)
                    self.assertEqual(sidebar.get_attribute("aria-hidden"), "true")
                    menu_toggle.click()
                    self.assertEqual(menu_toggle.get_attribute("aria-expanded"), "true")
                    self.assertTrue(app_main.get_attribute("inert") is not None)
                    self.assertTrue(
                        sidebar.locator("[data-app-menu-close]").evaluate(
                            "element => element === document.activeElement"
                        )
                    )
                    self._assert_visual_baseline(
                        dashboard_page,
                        "app-shell-drawer-mobile",
                        full_page=False,
                    )
                    sidebar.locator("a").last.focus()
                    dashboard_page.keyboard.press("Tab")
                    self.assertTrue(
                        sidebar.locator("a").first.evaluate(
                            "element => element === document.activeElement"
                        )
                    )
                    dashboard_page.keyboard.press("Escape")
                    self.assertEqual(menu_toggle.get_attribute("aria-expanded"), "false")
                    self.assertTrue(app_main.get_attribute("inert") is None)
                    self.assertTrue(
                        menu_toggle.evaluate(
                            "element => element === document.activeElement"
                        )
                    )
                    menu_toggle.click()
                    dashboard_page.set_viewport_size({"width": 768, "height": 844})
                    self.assertEqual(menu_toggle.get_attribute("aria-expanded"), "false")
                    self.assertTrue(sidebar.get_attribute("inert") is None)
                    self.assertTrue(sidebar.get_attribute("aria-hidden") is None)
                    self.assertTrue(app_main.get_attribute("inert") is None)
                    self.assertTrue(
                        sidebar.locator('a[aria-current="page"]').evaluate(
                            "element => element === document.activeElement"
                        )
                    )
                    dashboard_page.set_viewport_size(viewport)
                    self.assertTrue(sidebar.get_attribute("inert") is not None)
                    self.assertEqual(sidebar.get_attribute("aria-hidden"), "true")
                    self.assertTrue(
                        menu_toggle.evaluate(
                            "element => element === document.activeElement"
                        )
                    )
                    transition_seconds = sidebar.evaluate(
                        """element => {
                            const value = getComputedStyle(element).transitionDuration;
                            return value.endsWith('ms')
                                ? parseFloat(value) / 1000
                                : parseFloat(value);
                        }"""
                    )
                    self.assertLessEqual(transition_seconds, 0.001)

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
                with products_page.expect_navigation(wait_until="networkidle") as navigation:
                    products_page.locator(".zeus-product-create-form").evaluate(
                        "form => form.submit()"
                    )
                self.assertEqual(navigation.value.status, 400)
                self.assertTrue(
                    products_page.locator('[data-app-state="error"][role="alert"]').is_visible()
                )
                self._assert_no_horizontal_overflow(products_page)
                self._assert_visual_baseline(
                    products_page,
                    f"app-shell-products-error-{viewport_name}",
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
        complete_dna = CompanyDNA.objects.create(
            company=company,
            version=2,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content=complete_content,
        )
        CompanyDNA.objects.filter(pk=complete_dna.pk).update(
            created_at=datetime(2026, 7, 13, 18, 30, tzinfo=UTC)
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
                    if surface_name == "dna-generating":
                        spinner = page.locator(".animate-spin").first
                        animation = spinner.evaluate(
                            """element => {
                                const style = getComputedStyle(element);
                                const duration = style.animationDuration;
                                return {
                                    duration: duration.endsWith('ms')
                                        ? parseFloat(duration) / 1000
                                        : parseFloat(duration),
                                    iterations: style.animationIterationCount,
                                };
                            }"""
                        )
                        self.assertLessEqual(animation["duration"], 0.001)
                        self.assertEqual(animation["iterations"], "1")
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

    @override_settings(ZEUS_APP_SHELL_ENABLED=True)
    def test_engine_app_shell_visual_baselines(self):
        user = get_user_model().objects.create_user(
            username="browser-engine-shell",
            email="browser-engine-shell@example.com",
            password="test-password",
        )
        company = Company.objects.create(
            schema_name="ui-baseline",
            name="UI Baseline",
        )
        products = []
        source_dna_ids = []
        for index, name in enumerate(("Vasca Premium", "Canale Tecnico"), start=1):
            product = Product.objects.create(
                company=company,
                name=name,
                slug=f"specialista-{index}",
                tipologia="Componente",
                codice=f"ENG-{index:02d}",
                status=Product.STATUS_ATTIVO,
            )
            product_dna = ProductDNA.objects.create(
                product=product,
                version=1,
                dna_type=ProductDNA.TYPE_COMPLETE,
                content={key: f"{name}: {key}" for key in PRODUCT_LAYER_KEYS},
            )
            products.append(product)
            source_dna_ids.append(product_dna.pk)

        company_dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={
                **{key: f"Contenuto verificato per {key}." for key in LAYER_KEYS},
                "_cross_specialist": {
                    "source_dna_ids": source_dna_ids,
                    "summary": "I due specialisti condividono una postura tecnica verificabile.",
                    "shared_patterns": [
                        {
                            "theme": "Validazione tecnica",
                            "evidence": "Entrambi richiedono verifica del contesto applicativo.",
                            "impact": "Il DNA Generale mantiene una regola trasversale.",
                        }
                    ],
                    "conflicts": [
                        {
                            "severity": "medium",
                            "products": [product.name for product in products],
                            "issue": "Le soglie operative richiedono contesti distinti.",
                            "recommendation": "Mantenere il vincolo nello specialista.",
                        }
                    ],
                    "consolidation_proposals": [
                        {
                            "target_layer": "logica_decisionale",
                            "title": "Validazione prima della proposta",
                            "proposed_value": "Verificare sempre il contesto tecnico.",
                            "rationale": "Pattern condiviso dai due specialisti.",
                            "source_products": [product.name for product in products],
                        }
                    ],
                },
            },
        )
        CompanyDNA.objects.filter(pk=company_dna.pk).update(
            created_at=datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
        )
        issue = ConsistencyIssue.objects.create(
            company=company,
            scope=ConsistencyIssue.SCOPE_PERIODIC,
            severity=ConsistencyIssue.SEVERITY_MEDIUM,
            title="Confine da verificare",
            description="Un vincolo specialista non deve diventare assoluto.",
            recommendation="Mantenere il dettaglio nel DNA Specialista.",
            company_layer="confini",
            product_layer="vincoli",
            product=products[0],
        )
        ConsistencyIssue.objects.filter(pk=issue.pk).update(
            created_at=datetime(2026, 7, 14, 9, 5, tzinfo=UTC)
        )

        self.client.force_login(user)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        surfaces = [
            (
                "engine-b",
                reverse("motore-b-report"),
                "Motore B",
                "Motore B — Cross Specialist",
                "Rianalizza specialisti",
            ),
            (
                "engine-c",
                reverse("consistency-report"),
                "Motore C",
                "Motore C — Coerenza",
                "Confine da verificare",
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

                for surface_name, path, nav_label, heading, contract in surfaces:
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
                        nav_label,
                    )
                    self.assertEqual(
                        page.locator(".zeus-app-nav a.is-active").inner_text(),
                        nav_label,
                    )
                    self.assertEqual(page.locator("h1").first.inner_text(), heading)
                    self.assertTrue(page.get_by_text(contract, exact=True).count())
                    self.assertTrue(page.locator('input[name="csrfmiddlewaretoken"]').count())
                    self.assertTrue(page.evaluate("typeof window.htmx !== 'undefined'"))
                    self.assertFalse(page_errors)
                    self._assert_no_horizontal_overflow(page)
                    self._assert_visual_baseline(
                        page,
                        f"app-shell-{surface_name}-{viewport_name}",
                    )
                    page.close()
                context.close()
            browser.close()
