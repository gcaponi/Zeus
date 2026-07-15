# ZEUS App Shell - Matrice di regressione

Stato: Fasi 0-7 e correzione scroll sidebar Revisione DNA rilasciate e verificate in produzione.

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
| Dashboard | `tenant-dashboard` | `/dashboard/` | Login obbligatorio; legacy con flag off, App Shell tenant con flag on. |
| Preview App Shell | `app-shell-preview` | `/__shell_preview/` | `404` con flag off; shell renderizzata soltanto con `ZEUS_APP_SHELL_ENABLED=True`. |
| Onboarding | `onboarding-index` | `/onboarding/` | Legacy con flag off; App Shell con flag on; rendering autenticato e `#onboarding-step` bloccati. |
| Domande DNA | `dna-questions` | `/company/dna/questions/` | Full page flag-aware; POST, CSRF e avanzamento restano sulla view esistente. |
| Gap Engine DNA | `dna-gap-questions` | `/company/dna/gap-questions/<round>/` | Full page flag-aware; round e contratti delle domande restano invariati. |
| Generazione DNA | `dna-generating` | `/company/dna/generating/` | Full page flag-aware; polling HTMX conserva `hx-target="body"` e `HX-Redirect`. |
| Revisione DNA | `dna-review` | `/company/dna/review/` | Full page flag-aware; `#dna-review-root`, approvazione e modifica sezioni invariati. |
| Visualizzazione DNA | `dna-visualize` | `/company/dna/visualize/` | Full page flag-aware; sidebar contestuale, feedback e PDF restano raggiungibili. |
| Specialisti | `product-list-create` | `/products/` | Legacy con flag off; App Shell con flag on su GET, create POST e rami di errore. |
| Dettaglio Specialista | `product-detail` | `/products/<pk>/` | Full page e rami upload/generazione flag-aware; file, note, CSRF, errori `400/403` e azioni invariati. |
| Domande Specialista | `product-questions` | `/products/<pk>/questions/` | Form e loading flag-aware; POST, errori `400`, polling e redirect restano sulla view esistente. |
| Gap Specialista | `product-gap-questions` | `/products/<pk>/gap-questions/<round>/` | Form e processing flag-aware; round, POST, polling e `HX-Redirect` invariati. |
| Review Specialista | `product-review` | `/products/<pk>/review/` | Full page flag-aware; approvazione/modifica HTMX, promozione e pubblicazione invariati. |
| DNA Specialista | `product-dna-visualize` | `/products/<pk>/visualize/` | Full page flag-aware; sezioni, feedback, sidebar contestuale e PDF invariati. |
| Feedback Specialista | `product-dna-feedback` | `/products/<pk>/feedback/` | Full page e loading flag-aware; proposte, polling, apply POST e `HX-Redirect` invariati. |
| Motore B | `motore-b-report` | `/company/dna/motore-b/` | Route bloccata; comportamento dati coperto dalla suite Companies. |
| Motore C | `consistency-report` | `/company/dna/consistency/` | Route e partial `#consistency-report-root` coperti. |

I contratti route e pubblici sono in `tests/test_ui_app_shell_baseline.py`. La logica di dominio resta coperta da `tests/test_companies.py`; la Fase 0 non la duplica.

## Contratti HTMX e asincroni

| Contratto | Superfici | Protezione corrente |
| --- | --- | --- |
| `#onboarding-step` | Fonte e avanzamento onboarding | Flag on restituisce App Shell nelle risposte full page e lo stesso frammento puro nelle richieste HTMX. |
| `#company-files-list` | Upload e cancellazione file aziendali | ID e azioni conservati nella matrice. |
| `#dna-review-root` | Approva/modifica sezioni DNA | Test partial e assenza redirect prematuro esistenti. |
| `#consistency-report-root` | Polling Motore C | Test partial, pending e assenza `DOCTYPE` esistenti. |
| `hx-target="body"` | DNA generating | Test attesa e `HX-Redirect` esistenti. |
| `hx-target="body"` | Product DNA loading | Test flag-on verifica App Shell, polling invariato e `HX-Redirect` verso le domande. |
| `hx-target="body"` | Product questions loading | Test flag-on e baseline browser verificano shell, HTMX disponibile e polling invariato. |
| `hx-target="body"` | Product gap processing | Test flag-on verifica shell, progresso persistito, polling e redirect a round/review. |
| `hx-target="body"` | Product feedback loading | Test flag-on verifica shell, progresso persistito, polling e `HX-Redirect` a proposte pronte. |

## Baseline browser

Il test `tests/test_ui_browser_baseline.py` usa `StaticLiveServerTestCase`, Chromium Playwright e il middleware solo-test `tests/browser_support.py`. Il runtime di prodotto non importa questi helper.

| Superficie | Desktop 1440x900 | Tablet 1024x768 | Mobile 390x844 | Assert funzionali |
| --- | --- | --- | --- | --- |
| Login | `login-desktop.png` | `login-tablet.png` | `login-mobile.png` | `200`, heading, CSRF, no overflow. |
| Dashboard legacy | `dashboard-desktop.png` | `dashboard-tablet.png` | `dashboard-mobile.png` | Flag off, sessione, tenant, onboarding, no overflow. |
| Onboarding | `onboarding-desktop.png` | `onboarding-tablet.png` | `onboarding-mobile.png` | Sessione, tenant, `#onboarding-step`, no overflow. |
| Specialisti legacy | `products-desktop.png` | `products-tablet.png` | `products-mobile.png` | Flag off, sessione, tenant, form creazione, no overflow. |
| Preview App Shell | `app-shell-preview-desktop.png` | `app-shell-preview-tablet.png` | `app-shell-preview-mobile.png` | Flag, sidebar, header, `#app-main`, no overflow. |
| Dashboard App Shell | `app-shell-dashboard-desktop.png` | `app-shell-dashboard-tablet.png` | `app-shell-dashboard-mobile.png` | Flag on, tema, drawer mobile, navigazione e no overflow. |
| Specialisti App Shell | `app-shell-products-desktop.png` | `app-shell-products-tablet.png` | `app-shell-products-mobile.png` | Flag on, form reale, empty state e no overflow. |
| Onboarding App Shell | `app-shell-onboarding-desktop.png` | `app-shell-onboarding-tablet.png` | `app-shell-onboarding-mobile.png` | Flag on, stepper, fonte, sidebar contestuale e no overflow. |
| Domande DNA App Shell | `app-shell-dna-questions-desktop.png` | `app-shell-dna-questions-tablet.png` | `app-shell-dna-questions-mobile.png` | Flag on, textarea reale, stepper, HTMX disponibile e no overflow. |
| Generazione DNA App Shell | `app-shell-dna-generating-desktop.png` | `app-shell-dna-generating-tablet.png` | `app-shell-dna-generating-mobile.png` | Flag on, polling `body`, popup chiuso al load e no overflow. |
| Revisione DNA App Shell | `app-shell-dna-review-desktop.png` | `app-shell-dna-review-tablet.png` | `app-shell-dna-review-mobile.png` | Flag on, `#dna-review-root`, popup chiuso al load e no overflow. |
| Visualizzazione DNA App Shell | `app-shell-dna-visualize-desktop.png` | `app-shell-dna-visualize-tablet.png` | `app-shell-dna-visualize-mobile.png` | Flag on, DNA reale, sidebar contestuale e no overflow. |
| Specialisti popolati App Shell | `app-shell-specialist-list-desktop.png` | `app-shell-specialist-list-tablet.png` | `app-shell-specialist-list-mobile.png` | Flag on, prodotto reale, azioni dettaglio/delete e no overflow. |
| Dettaglio Specialista App Shell | `app-shell-specialist-detail-desktop.png` | `app-shell-specialist-detail-tablet.png` | `app-shell-specialist-detail-mobile.png` | Flag on, upload, file list, DNA reale, stepper e no overflow. |
| Domande Specialista loading | `app-shell-specialist-questions-desktop.png` | `app-shell-specialist-questions-tablet.png` | `app-shell-specialist-questions-mobile.png` | Flag on, polling `body`, HTMX disponibile e no overflow. |
| Review Specialista App Shell | `app-shell-specialist-review-desktop.png` | `app-shell-specialist-review-tablet.png` | `app-shell-specialist-review-mobile.png` | Flag on, sezioni reali, azioni HTMX, sidebar contestuale e no overflow. |
| DNA Specialista App Shell | `app-shell-specialist-visualize-desktop.png` | `app-shell-specialist-visualize-tablet.png` | `app-shell-specialist-visualize-mobile.png` | Flag on, DNA reale, feedback/PDF, sidebar contestuale e no overflow. |
| Motore B App Shell | `app-shell-engine-b-desktop.png` | `app-shell-engine-b-tablet.png` | `app-shell-engine-b-mobile.png` | Flag on, analisi cross-specialist, form CSRF, badge/proposte e no overflow. |
| Motore C App Shell | `app-shell-engine-c-desktop.png` | `app-shell-engine-c-tablet.png` | `app-shell-engine-c-mobile.png` | Flag on, issue reali, azioni, refresh HTMX e no overflow. |
| Command palette | `app-shell-command-palette-desktop.png` | `app-shell-command-palette-tablet.png` | `app-shell-command-palette-mobile.png` | Dialog aperto, cinque route reali, focus input e no overflow. |

Le immagini sono in `docs/ui-baseline/`. Il confronto e' bloccante e non aggiorna file. Per approvare intenzionalmente una nuova baseline:

```bash
ZEUS_UPDATE_UI_BASELINE=1 uv run pytest -o addopts='' tests/test_ui_browser_baseline.py
```

## Comandi del gate locale

```bash
uv run pytest
uv run ruff check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run --settings=config.settings.test
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

## Gate Fase 2

- [x] Shell tenant comune con navigazione reale, breadcrumb, stato attivo, logout, light default e tema persistito.
- [x] Drawer mobile accessibile: stato `aria-expanded`, overlay e chiusura con `Escape` verificati in Chromium.
- [x] Dashboard seleziona legacy/App Shell esclusivamente tramite `ZEUS_APP_SHELL_ENABLED`.
- [x] Lista Specialisti condivide un solo partial tra legacy e App Shell; create, errori, CSRF, detail e delete restano sulle view e route esistenti.
- [x] Login, landing, onboarding e le 15 baseline precedenti restano invariati.
- [x] Sei nuove baseline Dashboard/Specialisti verificate a 1440, 1024 e 390 px senza overflow.
- [x] Gate mirato: `43 passed`; suite completa: `269 passed`, coverage `71.75%`.
- [x] Ruff, Django system check e migration check SQLite puliti; nessun modello o migration modificato.

## Gate Fase 3a

- [x] Sei view onboarding/DNA selezionano legacy o App Shell esclusivamente tramite `ZEUS_APP_SHELL_ENABLED`.
- [x] I template legacy e i wrapper App Shell includono gli stessi partial di contenuto; nessuna logica di dominio e' stata duplicata.
- [x] Le richieste HTMX alla fonte onboarding restano frammenti puri; polling DNA, `HX-Redirect`, CSRF, `#onboarding-step`, `#company-files-list` e `#dna-review-root` restano invariati.
- [x] Sidebar contestuale destra, stepper, sticky action bar e popup vivono nel contenuto del flusso; il chrome tenant resta comune.
- [x] Le 21 baseline storiche restano byte-identiche; 15 nuove baseline onboarding/DNA sono verificate a 1440, 1024 e 390 px.
- [x] Gate mirato: `146 passed`; suite completa: `273 passed`, coverage `72.27%`.
- [x] Ruff, Django system check e migration check SQLite puliti; nessun modello, migration, task Celery o deploy modificato.

## Gate Fase 3b

- [x] Dieci template full page del ciclo Specialista selezionano legacy o App Shell esclusivamente tramite `ZEUS_APP_SHELL_ENABLED`.
- [x] Template legacy e wrapper App Shell includono gli stessi partial di contenuto/runtime; il dettaglio centralizza la scelta del template anche nei rami upload/generazione `400/403`.
- [x] File/note, CSRF, domande, Gap Engine, review, approvazione/modifica, promozione, pubblicazione, feedback, PDF e relative route restano sulle view e azioni esistenti.
- [x] I quattro loading Specialista conservano `hx-target="body"`, frequenze di poll, progressi persistiti e `HX-Redirect`; overlay chiusi al load e HTMX disponibile sono verificati.
- [x] Le 36 baseline precedenti restano byte-identiche; 15 nuove baseline Specialista sono verificate a 1440, 1024 e 390 px. Il timestamp dettaglio e' fissato nella fixture per hash deterministici.
- [x] Gate mirato Companies: `148 passed`; tutte le baseline browser: `5 passed`; suite completa: `280 passed`, coverage `73.44%`.
- [x] Ruff, Django system check e migration check SQLite puliti; nessun modello, migration, task Celery o deploy modificato.

## Gate Fase 4

- [x] Motore B e Motore C selezionano legacy/App Shell esclusivamente tramite feature flag.
- [x] Refresh `#consistency-report-root`, CSRF, POST, redirect e azioni issue restano invariati.
- [x] Sei baseline nuove Motore B/C; 51 immagini precedenti byte-identiche.
- [x] Suite completa: `283 passed`, coverage `73.57%`; browser `6 passed`, 57 immagini.
- [x] Ruff, Django system check e migration check puliti; nessun modello, migration, task Celery o deploy modificato.

## Gate Fase 5.1

- [x] Command palette limitata a cinque route reali: Dashboard, Onboarding, Specialisti, Motore B e Motore C.
- [x] Trigger topbar nominato, dialog modale, filtro locale e stato vuoto accessibile.
- [x] `Ctrl/Cmd+K`, toggle, frecce, `Enter`, `Escape`, focus trap e ripristino focus verificati in Chromium.
- [x] Tre baseline palette nuove; 42 baseline tenant aggiornate intenzionalmente per il trigger topbar; 15 baseline legacy/preview non modificate.
- [x] Contratti App Shell: `19 passed`; browser: `6 passed`, 60 immagini; suite completa: `284 passed`, coverage `73.57%`.
- [x] Ruff, Django system check e migration check puliti; nessun modello, migration, task Celery o deploy modificato.

## Gate Fase 5.2

- [x] Stati `empty`, `error`, `loading` e `completed` uniformati senza cambiare view, payload o polling.
- [x] Errori, progressi e spinner espongono ruoli ARIA e live region coerenti.
- [x] Drawer mobile con focus iniziale, trap, ripristino, `Escape` e background `inert` verificato.
- [x] Browser: `6 passed`, 64 immagini; suite completa: `286 passed`, coverage `73.57%`.
- [x] Ruff, Django system check e migration check puliti; commit `812204f` pubblicato.

## Gate Fase 5.3

- [x] Audit responsive a 1440, 1024 e 390 px e transizione dinamica `767↔768` verificati.
- [x] Focus, `inert`, `aria-hidden` e `prefers-reduced-motion` verificati in Chromium.
- [x] Tutte le 64 baseline restano byte-identiche; browser `6 passed` e suite completa `286 passed`.
- [x] Ruff, Django system check e migration check puliti; commit `972baad` pubblicato.

## Gate Fase 6

- [x] ZeusAdmin usa una shell staff dedicata, separata dal chrome tenant.
- [x] Dashboard, lista Clienti, dettaglio e modali mantengono autorizzazioni staff e azioni protette.
- [x] I filtri Clienti preservano il partial HTMX puro `#clients-results`.
- [x] Drawer e modale coprono focus trap, `Escape`, ripristino focus e background `inert`.
- [x] Implementazione pubblicata nel commit `a9a9e91`.
- [x] Smoke ZeusAdmin con account staff autorizzato in produzione: Dashboard, Clienti, filtro HTMX,
  dettaglio Cais, modale DNA e drawer mobile verificati senza eseguire azioni distruttive.

## Gate Fase 7

- [x] CI `lint`, `test`, `build` e `release-gate` verdi.
- [x] Preflight, deploy controllato, feature flag e rollback documentati.
- [x] Produzione aggiornata al commit `516f5ef`; nessuna migration, servizi e health verdi, journal puliti.
- [x] App Shell tenant abilitata in produzione; rollback non necessario.
- [x] Smoke tenant autenticato su cinque route, palette, tema, drawer mobile e partial HTMX Motore C.
- [x] Smoke autenticati tenant e staff registrati nella release note il 2026-07-15.

## Gate correttivo - scroll sidebar Revisione DNA

- [x] Sidebar destra mantenuta nella colonna contestuale a 768 px.
- [x] Requisito chiarito: sidebar sempre visibile durante lo scroll della lunga Revisione DNA.
- [x] Main della sola Revisione DNA con overflow visibile; altre pagine App Shell invariate.
- [x] Test browser discriminante su `position: sticky`, offset 76 px e visibilita' completa anche a fondo pagina.
- [x] Test mirato verde; suite browser `8 passed`; suite completa `299 passed`, coverage `73.93%`;
    Ruff, Django system check e migration check puliti; baseline visuali verdi.
- [x] Tentativo `2133ea5` identificato come non conforme: la sidebar statica usciva dal viewport.
- [x] Correzione sticky `9e9118f` e cache-buster stylesheet `6ceb141` con CI #46/#47 verdi e deploy completati.
- [x] Smoke autenticato a 768x900: sticky a 76 px, sidebar completamente visibile a scroll intermedio
    e a fondo pagina, CSS versionato e nessun overflow orizzontale.
