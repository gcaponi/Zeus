# Changelog

All notable ZEUS changes are documented in this file.

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
