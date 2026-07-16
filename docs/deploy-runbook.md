# ZEUS production deploy runbook

This runbook covers the controlled App Shell and ZeusAdmin release on the ZEUS production
host. Production is the `pcc` VPS, the checkout is `/opt/Zeus`, and the runtime is Gunicorn
plus Celery under systemd behind nginx.

## Safety contract

- Deploy only a commit already pushed to `origin/main` with every GitHub Actions job green.
- Never print or copy values from `/opt/Zeus/.env`.
- Stop when the tracked production worktree is dirty, a service is inactive, health is not
  `{"status":"ok"}`, or the update is not a fast-forward.
- Preserve every untracked production file. In the 2026-07-14 inventory these are
  `FETCH_HEAD` and `start_celery.sh`.
- Record `PREVIOUS_SHA` and `RELEASE_SHA` outside the shell transcript before changing code.
- The App Shell flag controls tenant pages only. ZeusAdmin needs a code rollback if its smoke
  test fails.

## 1. CI and release selection

From the local checkout:

```bash
git status --short
git rev-parse HEAD
git log -1 --oneline
```

Set the exact green commit as `RELEASE_SHA`. Confirm the GitHub jobs `lint`, `test`, `build`,
and `release-gate` are successful. Do not deploy a moving branch name without recording its
commit.

## 2. Read-only production preflight

Run the versioned preflight without copying it to the server:

```bash
ssh pcc 'bash -s' < scripts/production_preflight.sh
```

Then record the current commit:

```bash
ssh pcc 'cd /opt/Zeus && git rev-parse HEAD'
```

Expected preflight properties:

- tracked worktree clean;
- `zeus`, `zeus-celery`, and `nginx` active;
- required environment variable names present;
- local and public health return `{"status":"ok"}`;
- App Shell flag missing or disabled before the first rollout;
- untracked file list unchanged from the recorded inventory.

The production Django check currently reports that `SECURE_SSL_REDIRECT` and HSTS are not set
inside Django. Nginx already performs the HTTP-to-HTTPS redirect and forwards
`X-Forwarded-Proto`. HSTS is not enabled at the proxy. Do not introduce HSTS during this UI
release: it affects every tenant subdomain and needs a separate security rollout and rollback
decision.

## 3. Fetch and fast-forward validation

On the VPS, define the two recorded commits explicitly:

```bash
cd /opt/Zeus
PREVIOUS_SHA='<recorded-current-sha>'
RELEASE_SHA='<green-release-sha>'
git fetch --prune origin
git cat-file -e "${RELEASE_SHA}^{commit}"
git merge-base --is-ancestor "$PREVIOUS_SHA" "$RELEASE_SHA"
test -z "$(git status --porcelain --untracked-files=no)"
```

Stop if any command fails. The planned App Shell release contains no migration files, but the
normal migration commands remain in the deployment procedure as a guard.

## 4. Deploy code with App Shell disabled

Keep `.env` unchanged for the first restart. A missing `ZEUS_APP_SHELL_ENABLED` value is the
supported disabled state.

```bash
cd /opt/Zeus
git merge --ff-only "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
set -a
source .env
set +a
export DJANGO_SETTINGS_MODULE=config.settings.prod
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate_schemas --shared --noinput
.venv/bin/python manage.py migrate_schemas --tenant --noinput
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py shell -c "from django.template.loader import get_template; [get_template(name) for name in ('core/app_shell_tenant.html', 'zeus_admin/base.html', 'zeus_admin/client_detail.html')]"
systemctl restart zeus zeus-celery
systemctl is-active zeus zeus-celery
.venv/bin/celery -A config inspect registered --timeout=10 | grep -E 'generate_company_questions_task|process_company_gap_round_task'
curl -fsS -H 'Host: zeus.cais.uno' http://127.0.0.1:8000/health/
curl -fsS https://zeus.cais.uno/health/
```

The Celery inspection must report both Company Foundation tasks on every active worker:
`apps.companies.tasks.generate_company_questions_task` and
`apps.companies.tasks.process_company_gap_round_task`. Stop the release if either task is absent.

### Flag-off smoke tests

- `/`, `/accounts/login/`, and `/health/` return the expected public responses.
- An unauthenticated `/zeus-admin/` request redirects to login.
- An authenticated tenant still sees the legacy UI while the flag is disabled.
- Recent `zeus` and `zeus-celery` logs contain no new errors.

## 5. Enable the tenant App Shell

Back up `.env` on the server without displaying it, then set the flag. This is an explicit
go/no-go step and must not be combined silently with the code pull.

```bash
cd /opt/Zeus
install -m 600 .env ".env.before-app-shell-${RELEASE_SHA}"
if grep -q '^ZEUS_APP_SHELL_ENABLED=' .env; then
    sed -i 's/^ZEUS_APP_SHELL_ENABLED=.*/ZEUS_APP_SHELL_ENABLED=true/' .env
else
    printf '\nZEUS_APP_SHELL_ENABLED=true\n' >> .env
fi
systemctl restart zeus
systemctl is-active zeus
curl -fsS https://zeus.cais.uno/health/
```

### Flag-on authenticated smoke tests

Using an authorized tenant account, verify:

- Dashboard, Onboarding, Specialisti, Motore B, and Motore C open inside the App Shell.
- Active navigation, breadcrumbs, theme toggle, and command palette work.
- HTMX partial updates do not replace the shell.
- Opening Foundation questions shows the processing page immediately while the worker generates
  the initial questions, then redirects automatically to the questionnaire.
- Submitting Foundation or Gap answers shows the processing page while the worker evaluates the
  round, then redirects automatically to the next follow-up round or complete-DNA generation.
- Recent `zeus-celery` logs show successful `generate_company_questions_task` and
  `process_company_gap_round_task` execution without tenant schema or timeout errors.
- Mobile drawer, keyboard focus, and Escape behavior work.
- Existing upload, generation, review, PDF, and feedback actions remain reachable.

Using a staff account, verify:

- ZeusAdmin Dashboard and Clienti render with the staff shell.
- Client filters still update `#clients-results`.
- Client detail opens attachments and DNA in the accessible modal.
- Protected delete/configuration actions retain their confirmation and authorization behavior.

## 6. Rollback

### Tenant-shell rollback

Use this first when the problem is limited to tenant App Shell pages:

```bash
cd /opt/Zeus
sed -i 's/^ZEUS_APP_SHELL_ENABLED=.*/ZEUS_APP_SHELL_ENABLED=false/' .env
systemctl restart zeus
systemctl is-active zeus
curl -fsS https://zeus.cais.uno/health/
```

### Code rollback

Use this for ZeusAdmin regressions, shared code failures, or unsuccessful flag-off smoke tests.
It temporarily runs the recorded previous commit without rewriting branch history or deleting
untracked files.

```bash
cd /opt/Zeus
test -z "$(git status --porcelain --untracked-files=no)"
git switch --detach "$PREVIOUS_SHA"
set -a
source .env
set +a
export DJANGO_SETTINGS_MODULE=config.settings.prod
.venv/bin/python manage.py check
.venv/bin/python manage.py collectstatic --noinput
systemctl restart zeus zeus-celery
systemctl is-active zeus zeus-celery
curl -fsS https://zeus.cais.uno/health/
```

After a corrective commit is green, return to the tracked branch with `git switch main`, pull
using `--ff-only`, and repeat the full procedure. Never use `git reset --hard` for this rollout.

## 7. Completion evidence

Record all of the following in the release note:

- previous and released commit SHAs;
- GitHub Actions run URL;
- migration and `collectstatic` results;
- service states and public health response;
- flag state;
- tenant and ZeusAdmin smoke-test results;
- rollback decision or confirmation that rollback was not required.
