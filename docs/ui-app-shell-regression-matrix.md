# ZEUS App Shell - Matrice di regressione

Stato: Fasi 0-1, baseline locale del 2026-07-13.

## Invarianti

- Nessuna modifica a modelli, migration, API, URL name, permessi, task Celery o contratti tenant.
- I form POST mantengono CSRF, destinazione, payload e redirect correnti.
- I target HTMX e gli header `HX-Redirect` restano invariati finche' un test non prova la migrazione.
- Login, signup e landing restano superfici pubbliche senza App Shell tenant.
- Ogni fase deve lasciare verdi test Django, browser, Ruff, system check e migration check.

## Route principali

| Superficie | URL name | Path | Contratto corrente |
| --- | --- | --- | --- |
| Landing | `tenant-landing` | `/` | Route bloccata; pagina pubblica senza `#app-main`. |
| Login | `account_login` | `/accounts/login/` | Route, form CSRF e assenza shell tenant bloccati. |
| Logout | `account_logout` | `/accounts/logout/` | Redirect pubblico assoluto e rimozione cookie workspace bloccati. |
| Dashboard | `tenant-dashboard` | `/dashboard/` | Login obbligatorio; link onboarding/logout e bootstrap CSRF bloccati. |
| Preview App Shell | `app-shell-preview` | `/__shell_preview/` | `404` con flag off; shell renderizzata soltanto con `ZEUS_APP_SHELL_ENABLED=True`. |
| Onboarding | `onboarding-index` | `/onboarding/` | Precedenza resolver, rendering autenticato e `#onboarding-step` bloccati. |
| Specialisti | `product-list-create` | `/products/` | Rendering autenticato e form creazione bloccati. |
| Motore B | `motore-b-report` | `/company/dna/motore-b/` | Route bloccata; comportamento dati coperto dalla suite Companies. |
| Motore C | `consistency-report` | `/company/dna/consistency/` | Route e partial `#consistency-report-root` coperti. |

I contratti route e pubblici sono in `tests/test_ui_app_shell_baseline.py`. La logica di dominio resta coperta da `tests/test_companies.py`; la Fase 0 non la duplica.

## Contratti HTMX e asincroni

| Contratto | Superfici | Protezione corrente |
| --- | --- | --- |
| `#onboarding-step` | Fonte e avanzamento onboarding | Test HTML e redirect non-HTMX esistenti. |
| `#company-files-list` | Upload e cancellazione file aziendali | ID e azioni conservati nella matrice. |
| `#dna-review-root` | Approva/modifica sezioni DNA | Test partial e assenza redirect prematuro esistenti. |
| `#consistency-report-root` | Polling Motore C | Test partial, pending e assenza `DOCTYPE` esistenti. |
| `hx-target="body"` | DNA generating | Test attesa e `HX-Redirect` esistenti. |
| `hx-target="body"` | Product DNA loading | Gate browser obbligatorio in Fase 4. |
| `hx-target="body"` | Product questions loading | Gate browser obbligatorio in Fase 4. |
| `hx-target="body"` | Product gap processing | Gate browser obbligatorio in Fase 4. |
| `hx-target="body"` | Product feedback loading | Test `HX-Redirect` esistenti; gate browser in Fase 4. |

## Baseline browser

Il test `tests/test_ui_browser_baseline.py` usa `StaticLiveServerTestCase`, Chromium Playwright e il middleware solo-test `tests/browser_support.py`. Il runtime di prodotto non importa questi helper.

| Superficie | Desktop 1440x900 | Tablet 1024x768 | Mobile 390x844 | Assert funzionali |
| --- | --- | --- | --- | --- |
| Login | `login-desktop.png` | `login-tablet.png` | `login-mobile.png` | `200`, heading, CSRF, no overflow. |
| Dashboard | `dashboard-desktop.png` | `dashboard-tablet.png` | `dashboard-mobile.png` | Sessione, tenant, onboarding, no overflow. |
| Onboarding | `onboarding-desktop.png` | `onboarding-tablet.png` | `onboarding-mobile.png` | Sessione, tenant, `#onboarding-step`, no overflow. |
| Specialisti | `products-desktop.png` | `products-tablet.png` | `products-mobile.png` | Sessione, tenant, form creazione, no overflow. |
| Preview App Shell | `app-shell-preview-desktop.png` | `app-shell-preview-tablet.png` | `app-shell-preview-mobile.png` | Flag, sidebar, header, `#app-main`, no overflow. |

Le immagini sono in `docs/ui-baseline/`. Il confronto e' bloccante e non aggiorna file. Per approvare intenzionalmente una nuova baseline:

```bash
ZEUS_UPDATE_UI_BASELINE=1 uv run pytest -o addopts='' tests/test_ui_browser_baseline.py
```

## Comandi del gate locale

```bash
uv run pytest
uv run ruff check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest -o addopts='' tests/test_ui_browser_baseline.py
```

Playwright richiede una sola inizializzazione locale del browser:

```bash
uv run playwright install chromium
```

## Vincoli ambiente rilevati

- Docker Compose non parte su questo host: il daemon fallisce la creazione della coppia `veth` con `operation not supported`.
- La baseline browser usa quindi il live server Django locale con SQLite, sessione reale e tenant solo-test.
- La venv editor usa Python 3.14, mentre il container ZEUS usa Python 3.11. Il test client di Django 5.0 non puo' copiare i template context su Python 3.14; i test di rendering seguono il pattern `RequestFactory` gia' usato dalla suite.
- Il gate Docker resta da ripetere quando il supporto networking del daemon e' disponibile; non e' sostituito da un deploy remoto.

## Gate Fase 0

- [x] Mockup approvato presente in `docs/ui-mockup-app-shell.html`.
- [x] Matrice route, azioni e target HTMX documentata.
- [x] Fixture locali per utente, sessione e tenant browser.
- [x] Test di caratterizzazione Django.
- [x] Screenshot desktop, tablet e mobile riproducibili.
- [x] Suite completa e check statici verdi: `259 passed`, coverage `71.72%`, Ruff e Django check puliti.
- [x] Approvazione visuale di Guglielmo.
- [x] Commit locale isolato della Fase 0: `9befad4`.

## Gate Fase 1

- [x] Feature flag `ZEUS_APP_SHELL_ENABLED` disattivo per default.
- [x] Route dedicata `/__shell_preview/`, non sovrapposta alle route esistenti.
- [x] Foundation standalone con slot `shell_sidebar`, `shell_header`, `shell_main` e `shell_scripts`.
- [x] Preview collegata soltanto a destinazioni ZEUS reali.
- [x] Login, landing, dashboard e le 12 baseline Fase 0 restano invariati con flag attivo.
- [x] Preview verificata a 1440, 1024 e 390 px senza overflow orizzontale.
- [x] Suite completa e check statici verdi: `264 passed`, coverage `71.72%`, Ruff e Django check puliti.