# Changelog

All notable ZEUS changes are documented in this file.

## 2026-07-15 - Foundation onboarding offers grounded answer options

### Added

- Each of the 10 initial DNA Generale questions generated for Foundation now includes exactly
  three possible answers grounded in the same pre-DNA, website, notes, and documents.
- Users can select a proposed answer to populate the existing textarea and edit it before submit;
  only the confirmed textarea value enters the complete DNA and Gap Engine.
- Suggested answers are persisted on `CompanyQuestion` through migration `0023` and remain empty
  for Professional, Enterprise, existing questions, and Gap Engine follow-ups.
- Parser validation rejects missing, duplicate, empty, non-string, or malformed proposals and
  returns a controlled page error after retry exhaustion instead of an HTTP 500.
- Browser coverage validates selection, manual editing, accessibility state, wrapping, overflow,
  and visual baselines on desktop, tablet, and mobile.

## 2026-07-15 - Tenant login opens the Dashboard

### Fixed

- Successful tenant login now opens the tenant Dashboard instead of Onboarding.
- The custom public login follows the central `LOGIN_REDIRECT_URL` policy.
- The redundant "Workspace pubblico" entry was removed from Dashboard quick access.
- Login redirect and Dashboard rendering contracts are covered by tests; the six affected
  Dashboard and command-palette baselines were updated without touching unrelated images.

### Deployment

- Commit `b4705b1` passed CI #51 and was deployed successfully.
- Authenticated production smoke confirmed the final `/dashboard/` URL, active Dashboard
  navigation, exactly two quick links, no "Workspace pubblico" text, and no horizontal overflow.
- Production services and public health remained green; recent web and worker journals were clean.

## 2026-07-15 - Tenant context sidebar correction generalized

### Fixed

- Every tenant App Shell page now uses document scrolling through the shared tenant shell, rather
  than enabling the corrected sticky behavior only on DNA review.
- Right-hand context sidebars remain visible at the 76 px sticky offset across onboarding, core DNA,
  and Specialist review/visualization workflows.
- Tenant bottom action bars retain their previous non-sticky behavior while the context sidebar fix
  is applied globally.
- Browser coverage performs real document scrolling on every core and Specialist surface that owns
  a context sidebar; existing visual baselines remain unchanged.

### Deployment

- Global correction commit `c336af7` passed CI #49 and was deployed successfully.
- Authenticated production smoke confirmed stylesheet `?v=20260715-4`, shared main overflow,
  sticky movement and full viewport visibility on four core steps and both Specialist context pages.
- All checked tenant steps had no horizontal overflow; services, public health and recent journals
  remained green.

## 2026-07-15 - DNA review sticky sidebar correction

### Fixed

- The right-hand context sidebar on tenant DNA review remains visible while the long review page
  scrolls, including at the bottom of the document.
- The DNA review main container now exposes document scrolling so the existing sticky sidebar can
  bind to the real scrolling element without changing other App Shell pages.
- The App Shell stylesheet URL is versioned so browsers load the corrected CSS immediately after
  deployment instead of retaining the previous cached behavior.
- Browser coverage verifies the sticky offset and full viewport visibility at intermediate and
  maximum document scroll positions.

### Deployment

- Sticky correction commit `9e9118f` passed CI #46 and was deployed successfully.
- Stylesheet cache-buster commit `6ceb141` passed CI #47 and was deployed successfully.
- Authenticated production smoke confirmed the sidebar at 76 px after intermediate and maximum
  document scrolling, with no horizontal overflow.

## 2026-07-15 - DNA review sidebar normal-flow attempt

### Changed

- Commit `2133ea5` moved the DNA review sidebar into normal page flow. Production review showed
  that the sidebar then left the viewport and the right column became empty during long scrolling.
- This behavior did not meet the clarified requirement that the sidebar remain visible and is
  superseded by the sticky correction above.

## 2026-07-14 - App Shell controlled release

### Added

- Feature-flagged tenant App Shell with persistent navigation, breadcrumb, theme control, and
  command palette.
- Responsive and accessible shells for tenant workflows and ZeusAdmin staff pages.
- Browser coverage for desktop, tablet, mobile, HTMX contracts, modal focus, and reduced motion.
- Production release gate for Django settings, Gunicorn, migrations, static collection, and
  required UI assets.
- Read-only production preflight and a documented flag-first/code-second rollback procedure.

### Changed

- Dashboard, Onboarding, Specialist, Motore B, and Motore C tenant surfaces can render inside
  the App Shell while preserving their existing views, forms, and HTMX partials.
- ZeusAdmin Dashboard, Clienti, and client detail now use a dedicated staff shell.
- Visual regression checks use bounded perceptual comparison across operating systems while
  retaining exact comparison as the fast path.

### Fixed

- Focus restoration and inert state across mobile/desktop shell transitions.
- Command palette focus initialization race.
- Accessible focus trapping and restoration in the ZeusAdmin content modal.
- Cross-platform screenshot false failures in GitHub Actions, with diagnostics retained as CI
  artifacts.

### Deployment

- Tenant App Shell is enabled in production through `ZEUS_APP_SHELL_ENABLED=true`.
- ZeusAdmin is not controlled by the tenant flag; its authenticated staff smoke test passed on
  2026-07-15.
- No database migrations are introduced by this release.
