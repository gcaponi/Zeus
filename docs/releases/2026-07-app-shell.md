# App Shell controlled release record

Status: production preparation complete; deployment blocked pending a green visual CI run

## Scope

This release promotes the tenant App Shell phases 0-5, the dedicated ZeusAdmin phase 6, and
the cross-platform visual/release gate from phase 7.1. The production gap starts after commit
`ef087d3` and currently ends at `e129e06`; the final release SHA will include this preparation
record.

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

- [ ] Final-release `lint` green.
- [ ] Final-release `test` green with coverage at or above 70%.
- [ ] Final-release `build` green.
- [ ] Final-release `release-gate` green.
- [x] Production preflight script green.
- [ ] Final release SHA recorded.
- [x] Previous production SHA recorded.
- [x] No migration files in the final production gap.

## Deployment evidence

- Latest CI run: `e129e06` has green `lint`, `build`, and `release-gate`; `test` is blocked
  only by cross-platform visual baseline drift and must be green for the final release SHA.
- Previous SHA: `ef087d3`.
- Release SHA: pending.
- Shared migrations: pending.
- Tenant migrations: pending.
- Static collection: pending.
- Flag-off smoke: pending.
- Flag-on tenant smoke: pending.
- ZeusAdmin staff smoke: pending.
- Rollback required: pending.

Execution follows [the production deploy runbook](../deploy-runbook.md).
