# App Shell controlled release record

Status: complete; tenant context sidebar correction deployed globally and authenticated smoke-tested

## Scope

This release promotes the tenant App Shell phases 0-5, the dedicated ZeusAdmin phase 6, and
the cross-platform visual/release gate from phase 7.1. The initial production gap started after
commit `ef087d3`; subsequent verified hotfix deployments advanced production to `516f5ef`.

There are no Django migration files in the observed commit range. Runtime changes are Python,
templates, CSS, JavaScript, CI configuration, tests, and static assets.

## Production inventory observed on 2026-07-14

- Host alias: `pcc` (`91.230.110.7`).
- Checkout: `/opt/Zeus`.
- Current commit: `ef087d3`.
- Web runtime: Gunicorn bound to `127.0.0.1:8000`, three workers, 300-second timeout.
- Worker runtime: Celery concurrency 2, using the same `/opt/Zeus/.env` environment file.
- Services: `zeus`, `zeus-celery`, and `nginx` active.
- Public health: HTTP 200 with `{"status":"ok"}`.
- App Shell flag: missing, therefore disabled by default.
- Untracked production files: `FETCH_HEAD`, `start_celery.sh`.
- Tracked worktree: clean.
- Filesystem: 100 GB free (31% used).
- Nginx: HTTP-to-HTTPS redirect and `X-Forwarded-Proto` forwarding confirmed; HSTS is not
  currently configured and is explicitly outside this UI rollout.

The untracked files are operational state and must not be deleted, moved, staged, or overwritten
by the rollout.

## Release gates

- [x] Final-release `lint` green.
- [x] Final-release `test` green with coverage at or above 70%.
- [x] Final-release `build` green.
- [x] Final-release `release-gate` green.
- [x] Production preflight script green.
- [x] Final release SHA recorded.
- [x] Previous production SHA recorded.
- [x] No migration files in the final production gap.

## Deployment evidence

- GitHub Actions run: [29339315205](https://github.com/gcaponi/Zeus/actions/runs/29339315205) (all four jobs green).
- Previous SHA: `ef087d3`.
- Release SHA: `ba3a7e537ca1b3046c890931f78f66b7a1c758fe`.
- Shared migrations: no migrations to apply.
- Tenant migrations: no migrations to apply.
- Static collection: 9 files copied, 128 unchanged.
- Flag-off smoke: health local/public `{"status":"ok"}`, root/login HTTP 200, anonymous ZeusAdmin
  redirects to login, all services active after `daemon-reload`.
- Flag-on tenant smoke: pending authenticated verification.
- ZeusAdmin staff smoke: pending authenticated verification.
- Rollback required: no.

Execution follows [the production deploy runbook](../deploy-runbook.md).

## Onboarding and upload hotfix deployment evidence

- GitHub Actions run: [29343543621](https://github.com/gcaponi/Zeus/actions/runs/29343543621) (lint, test, build and release-gate green).
- Previous SHA: `ba3a7e537ca1b3046c890931f78f66b7a1c758fe`.
- Release SHA: `75620c82f4a505eb54b96a76871cf64b96212fb2`.
- Shared and tenant migrations: no migrations to apply.
- Static collection: 1 file copied, 136 unchanged.
- App Shell flag: enabled and retained through the restart.
- Post-deploy smoke: `zeus`, `zeus-celery` and nginx active; local/public health
  `{"status": "ok"}`; root and login HTTP 200; anonymous ZeusAdmin redirects to login.
- Recent web and worker logs: no new traceback, exception or error entries.
- Rollback required: no.

## Specialist feedback and DNA review deployment evidence

- GitHub Actions run: [29356550506](https://github.com/gcaponi/Zeus/actions/runs/29356550506) (lint, test, build and release-gate green).
- Release SHA: `0017ee5ec6c34db8b87c3b6cb5bf3e1ce31458fb`.
- Shared and tenant migrations: no migrations to apply.
- App Shell flag: enabled and retained through the restart.
- Post-deploy smoke: `zeus`, `zeus-celery` and nginx active; local/public health green; recent journals clean.
- Corrective GitHub Actions run: [29358232431](https://github.com/gcaponi/Zeus/actions/runs/29358232431) (all four jobs green).
- Current production SHA: `516f5efdd1119e3277da5d0daaaec57536deac1e`.
- Corrective postflight: no migrations, services active, local/public health green and journals clean.
- Rollback required: no.

## Authenticated smoke evidence - 2026-07-15

- Authorized tenant role: Dashboard, Onboarding, Specialisti, Motore B, and Motore C returned
  HTTP 200 inside the App Shell with active navigation and no desktop overflow.
- Tenant interactions: command palette exposed five real routes and preserved focus across button,
  `Ctrl+K`, and `Escape`; mobile drawer focus, `inert`, `Escape`, theme persistence, and 390 px
  overflow checks passed.
- Tenant HTMX: authenticated Motore C refresh returned only `#consistency-report-root`, without a
  document wrapper or App Shell, while the current shell remained mounted.
- Authorized staff role: ZeusAdmin Dashboard, Clienti, and Cais detail rendered inside the staff
  shell with no desktop or mobile overflow.
- Staff interactions: Clienti filtering updated the results from 3 to 1 workspace; the DNA modal
  loaded real content, trapped and restored focus, marked the shell `inert`, and closed with
  `Escape`; the mobile drawer passed the same focus and `inert` checks.
- Protected staff actions were not executed. Delete forms remained POST-only with CSRF and an
  explicit confirmation; configuration remained a separate POST form.
- Production SHA: `516f5efdd1119e3277da5d0daaaec57536deac1e`; tracked worktree clean; `zeus`,
  `zeus-celery`, and nginx active; public health HTTP 200.
- Rollback required: no.

## Corrective follow-up - DNA review sidebar scrolling

- Commit `2133ea5` moved the sidebar into normal flow, but authenticated production review showed
  that it left the viewport and produced an empty right column during long scrolling.
- Clarified requirement: the right-hand controls must remain visible while the DNA document scrolls.
- Root cause: `.zeus-app-shell__main { overflow: auto; }` became the nearest sticky scroll ancestor,
  but that element expanded with the 11,000+ px document and never scrolled; the document scrolled
  instead, so the sidebar's declared `position: sticky` was ineffective.
- Local fix: DNA review adds a dedicated main-container class with `overflow: visible`, allowing the
  existing context sidebar to stick to document scrolling without altering other App Shell pages.
- Regression: the tablet browser test requires main overflow to be visible, computed sidebar
  `position: sticky`, a 76 px sticky offset after intermediate scrolling, and full sidebar viewport
  visibility at maximum document scroll.
- Validation: targeted onboarding App Shell test passed; complete browser suite `8 passed`; full
  suite `299 passed` with `73.93%` coverage; Ruff, Django system check, and migration check passed;
  existing visual baselines remained green.
- Previous attempt: commit `2133ea5`, CI run
  [29396582780](https://github.com/gcaponi/Zeus/actions/runs/29396582780), deployed successfully but
  behavior rejected during production review.
- Sticky correction: commit `9e9118f`, CI run
  [29398632739](https://github.com/gcaponi/Zeus/actions/runs/29398632739) (#46), all four jobs green.
- First corrective deploy advanced production from `2133ea5` to `9e9118f`; services and health were
  green, but the authenticated browser retained the old unversioned stylesheet from cache.
- Cache correction: commit `6ceb141` versions the App Shell stylesheet URL and adds a rendering
  regression; CI run [29399998010](https://github.com/gcaponi/Zeus/actions/runs/29399998010) (#47)
  completed with all four jobs green.
- Final production SHA: `6ceb141c9b152436ce36c3f9cefff8a0333158a6`; tracked worktree clean;
  `zeus`, `zeus-celery`, and nginx active; local/public health green; recent journals clean.
- Authenticated production smoke at 768x900: stylesheet URL includes `?v=20260715-3`, main overflow
  is visible, sidebar computes to `position: sticky` with `top: 76px`, remains fully visible after
  intermediate and maximum document scrolling, and introduces no horizontal overflow.
- Rollback required: no.

## Global follow-up - tenant context sidebars

- The previous correction was scoped only to DNA review. The shared tenant parent now applies
  `zeus-app-shell__main--document-scroll` to every tenant App Shell page, and the Review-only
  override has been removed.
- Context sidebars keep `position: sticky` with a 76 px offset. Tenant bottom action bars are kept
  static so the global scroll change does not alter their established behavior or visual baselines.
- Regression coverage checks the shared main class and visible overflow across the core and
  Specialist flows. Every surface containing `.zeus-app-page-context` is then scrolled for real and
  must move toward the sticky offset while remaining fully inside the viewport.
- Validation: focused core and Specialist browser tests `2 passed`; complete browser suite
  `8 passed`; full suite `299 passed` with `73.93%` coverage; Ruff, Django system check, and migration
  check passed; the 24 canonical visual baselines remained unchanged.
- Release commit: `c336af7`; CI run
  [29403513380](https://github.com/gcaponi/Zeus/actions/runs/29403513380) (#49) completed with `lint`,
  `test`, `build`, and `release-gate` green.
- Production advanced from `6ceb141` to `c336af7`; no migrations were required, one static file was
  collected, and `zeus`, `zeus-celery`, and nginx remained active.
- Authenticated production smoke at 1024x768 verified stylesheet `?v=20260715-4`, shared main
  overflow, sticky movement to 76 px, full sidebar viewport visibility, and no horizontal overflow
  on Onboarding, core DNA Questions/Review/Visualization, and Specialist Review/Visualization.
  Specialist Detail, Questions, and Feedback also inherited the shared main behavior without layout
  overflow; those steps do not render a context sidebar.
- Final production SHA: `c336af79b0dc3714c781cdd13a7360a21b5e04f8`; tracked worktree clean,
  public health HTTP 200 with `{"status":"ok"}`, and no recent web or worker errors.
- Rollback required: no.
