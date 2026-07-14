# App Shell controlled release record

Status: flag-off deployment complete; tenant App Shell enablement awaits authenticated smoke tests

## Scope

This release promotes the tenant App Shell phases 0-5, the dedicated ZeusAdmin phase 6, and
the cross-platform visual/release gate from phase 7.1. The production gap starts after commit
`ef087d3` and the deployed application release is `ba3a7e5`.

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
