# App Shell controlled release record

Status: deployed release complete; DNA review sidebar scroll correction validated locally and pending deployment

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

- Production review on 2026-07-15 confirmed that the right-hand DNA review sidebar remained sticky
  while the user expected it to scroll together with the page.
- Root cause: the shared `.zeus-app-page-context` rule applied `position: sticky`; the existing
  browser assertion only checked that the sidebar remained visible after scrolling.
- Local fix: DNA review overrides the context sidebar to normal flow while other contextual
  sidebars retain their existing sticky behavior.
- Regression: the tablet browser test now requires computed `position: static` and verifies that
  the sidebar's viewport position changes during document scrolling.
- Validation: targeted onboarding App Shell test passed; complete browser suite `8 passed`; full
  suite `299 passed` with `73.93%` coverage; Ruff, Django system check, and migration check passed;
  existing visual baselines remained green.
- Deployment status: pending commit, CI, production deploy, and authenticated review-page smoke.
