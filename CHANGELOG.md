# Changelog

All notable ZEUS changes are documented in this file.

## Unreleased — App Shell controlled release

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

- Tenant App Shell remains disabled unless `ZEUS_APP_SHELL_ENABLED=true` is configured.
- ZeusAdmin is not controlled by the tenant flag and requires staff smoke testing after code
  deployment.
- No database migrations are introduced by this release.
