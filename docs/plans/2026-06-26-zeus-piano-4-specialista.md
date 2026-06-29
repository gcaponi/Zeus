# PIANO 4 — Specialista Lifecycle + DNA Specialista

> **Data:** 2026-06-26
> **Stato:** Draft — pronto per review Guglielmo
> **Scope:** Fasi 7-10 del design doc (crea specialista → biblioteca → intervista → DNA Specialista)
> **Deprecato:** Rename `Product→Specialista` rimandato a ciclo futuro (decisione 2026-06-23, memory)
> **Stack:** Python + Django + Celery, NO rename, NO Instructor/Pydantic in questo ciclo

---

## 0. Contesto

### Cosa c'è già
| Componente | Stato |
|---|---|
| Modello `Product` | ✅ name, slug, company FK |
| Modello `ProductDNA` | ✅ 5 sezioni legacy (descrizione, applicazione, specifiche, vincoli, valore) |
| Modello `ProductQuestion` | ✅ esiste, analogo a CompanyQuestion |
| Modello `ProductFile` | ✅ esiste, upload documenti |
| Modello `ProductSectionApproval` | ✅ esiste |
| View `product_list_create` | ✅ lista + crea prodotto |
| View `product_detail` | ✅ dettaglio prodotto |
| View `product_file_upload` | ✅ upload file |
| View `product_questions` | ✅ intervista prodotto (Round 1) |
| View `product_review` | ✅ review DNA prodotto |
| View `product_dna_generate` | ✅ genera DNA prodotto |
| View `product_section_approve/edit` | ✅ approva/modifica sezione |

### Cosa manca
| Componente | Manca |
|---|---|
| Stati specialista (5) | `Product.status` (bozza, in_costruzione, in_validazione, attivo, archiviato) |
| Tipologia e codice | `Product.tipologia` (libero), `Product.codice` (univoco per azienda) |
| DNA Specialista a 6 strati | ProductDNA.content da 5 legacy a 6 strati cognitivi + sintesi_cognitiva |
| Prompt DNA Specialista | `prompts/dna_specialista_v1.md` (include CompanyDNA.content come contesto) |
| Gap Engine per Product | Limiti diversi da CompanyDNA (Foundation 2r/5f, Pro 3r/10f, Legacy illimitato) |
| Schermata Round 2+ | `product_gap_questions` view + template |
| Self-critique + enrichment | Duplicato temporaneo da CompanyDNA, refactor generico in PIANO 5 |
| Safe_mode ProductDNA | Blocco approvazione se CRITICAL |
| Navigation stato | Dashboard con badge stato (5 colori)

---

## 1. Architettura DNA Specialista

### 1.1 I 6 strati (stessa struttura DNA Generale, focus famiglia prodotto)

| Strato interno | Titolo review | Focus |
|---|---|---|
| `identita` | Chi siamo e come ci poniamo | Identità dello specialista nel contesto aziendale |
| `modelli_mentali` | Come ragioniamo | Sequenza di pensiero per problemi di questa famiglia |
| `nucleo_tecnico` | Cosa ci rende unici | Approccio distintivo, trade-off, varianti prodotto |
| `confini` | I nostri confini | Cosa NON fa questa famiglia, richieste rifiutate |
| `tono` | Il nostro tono | Registro comunicativo per questo specialista |
| `logica_decisionale` | Come prendiamo decisioni | Custom, escalation, incertezza per questa famiglia |

### 1.2 Struttura JSON

```json
{
  "identita": { "postura": "...", "convinzioni": ["..."] },
  "modelli_mentali": { "pilastri": ["..."], "sequenza_di_lettura": "..." },
  "nucleo_tecnico": { "approccio_distintivo": "...", "trade_off_scelti": "...", "famiglie_prodotto": ["..."] },
  "confini": { "anti_pattern": ["..."], "richieste_rifiutate": "..." },
  "tono": { "registro": "...", "esempi": [{"sbagliato": "...", "giusto": "..."}] },
  "logica_decisionale": { "filosofia_custom": "...", "escalation": "..." }
}
```

---

## 2. Task TDD — Ordine di implementazione

### Task 1: Migration — stato, tipologia, codice su Product

**Test:** `test_product_has_status_tipologia_codice`
- Crea Product con status="bozza", tipologia="canale", codice="CI-001"
- Verifica che `status` sia nella lista choices
- Verifica che `get_status_display()` ritorni "Bozza"

**Impl:**
- Aggiungere `STATUS_CHOICES` su Product (5 stati):
  ```python
  STATUS_BOZZA = "bozza"
  STATUS_IN_COSTRUZIONE = "in_costruzione"
  STATUS_IN_VALIDAZIONE = "in_validazione"
  STATUS_ATTIVO = "attivo"
  STATUS_ARCHIVIATO = "archiviato"
  ```
- Aggiungere `tipologia` (CharField, max_length=100, libero, help_text con esempi)
- Aggiungere `codice` (CharField, max_length=50, UniqueConstraint con company)
- Migration `0016_product_status_tipologia_codice.py`

**Nota:** `in_aggiornamento` NON è uno stato — è gestito come flag `has_pending_updates` su stato Attivo.

**Commit:** `feat(product): add status, tipologia, codice to Product model`

---

### Task 2: Migration — ProductDNA 6 strati + sezione key mapping

**Test:** `test_product_dna_has_six_cognitive_layers`
- Crea ProductDNA con content che ha tutte e 6 le chiavi
- Verifica `missing_sections()` ritorni set vuoto
- Verifica che le chiavi legacy (descrizione, applicazione, specifiche, vincoli, valore) NON siano più richieste

**Impl:**
- Aggiornare `ProductDNA.missing_sections()` per usare `LAYER_KEYS` (6 strati)
- Aggiungere `ProductDNA.TYPE_COMPLETE` con 6 strati
- Migration `0017_productdna_six_layers.py` (opzionale: purge vecchi dati di test)

**Commit:** `feat(product-dna): migrate ProductDNA to 6 cognitive layers`

---

### Task 3: Prompt — `dna_specialista_v1.md`

**Test:** `test_specialista_prompt_has_six_layers`
- Legge il file `prompts/dna_specialista_v1.md`
- Verifica che contenga le 6 sezioni: identita, modelli_mentali, nucleo_tecnico, confini, tono, logica_decisionale
- Verifica che menzioni `[SRC:...]` e focus famiglia prodotto

**Impl:**
- Creare `apps/companies/prompts/dna_specialista_v1.md`
- Adattare da `dna_generale_v1.md` ma con focus **100% sulla famiglia prodotto**
- **Sezione contesto DNA Generale:** include `CompanyDNA.content` come ground truth. Istruzione: "Eredita i principi del DNA Generale. NON ripetere ciò che è già nel Generale. Aggiungi SOLO specificità prodotto (varianti, dimensioni, materiali, casi d'uso specifici)."
- Sezione `nucleo_tecnico` → varianti prodotto, dimensioni, materiali specifici
- Sezione `confini` → limiti stretti della famiglia, cosa NON si personalizza
- Sezione `tono` → registro adattato al contesto prodotto (se diverso dal Generale)

**Commit:** `feat(prompts): add dna_specialista_v1.md with 6 cognitive layers`

**Commit:** `feat(prompts): add dna_specialista_v1.md with 6 cognitive layers`

---

### Task 4: Prompt — generazione domande specialista (2 pool)

**Test:** `test_product_question_generation_uses_two_pools`
- Genera domande per un Product con Plan Professional
- Verifica che alcune domande abbiano `pool="template"` e altre `pool="kb_anchored"`
- Verifica che le domande kb_anchored facciano riferimento a `ProductFile`

**Impl:**
- Aggiungere campo `pool` a `ProductQuestion` (se non esiste già)
- Creare prompt `prompts/questions_specialista_v1.md`
- View `_generate_product_questions` → genera 7 template + 8 kb_anchored = 15 domande

**Commit:** `feat(product-questions): two-pool question generation for specialist`

---

### Task 5: Gap Engine per ProductDNA

**Test:** `test_product_gap_engine_creates_follow_up`
- Risponde insufficiente a 3 domande Product
- Verifica che Gap Engine generi follow-up Round 2
- Verifica che `ProductQuestion.question_round=2` e `parent_question` sia popolato

**Impl:**
- Riutilizzare `_evaluate_answer_sufficiency` (già generica, accetta questions)
- View `product_questions` POST → chiama `_process_answers_after_round` per ProductQuestion
- View `product_gap_questions` (nuova) per Round 2+
- Template `product_gap_questions.html`
- `GAP_ENGINE_PRODUCT_LIMITS` (nuovo, diverso da CompanyDNA):
  - Foundation: max 2 round, max 5 follow-up
  - Professional: max 3 round, max 10 follow-up
  - Legacy: illimitato
- ProductQuestion aggiunge `pool`, `question_round`, `parent_question` (migration)

**Commit:** `feat(product-gap): integrate Gap Engine for ProductDNA`

---

### Task 6: Sintesi globale DNA Specialista

**Test:** `test_complete_product_dna_has_six_layers`
- Genera DNA Specialista completo
- Verifica che `content` contenga tutte e 6 le chiavi
- Verifica che `sintesi_cognitiva` esista

**Impl:**
- Funzione `_global_product_dna_synthesis()` (analogo a `_global_dna_synthesis`)
- Usa prompt `SINTESI_GLOBALE_DNA` adattato per specialista
- Applica `_normalize_synthesis_layers` (già implementato)
- Chiama `_safe_merge_synthesis`

**Commit:** `feat(product-dna): global synthesis with 6 layers for specialist`

---

### Task 7: Self-critique + enrichment per ProductDNA

**Test:** `test_product_dna_has_enrichment_after_complete`
- Genera DNA Specialista completo
- Verifica che `_enrichment` esista con validation + scoring
- Verifica che `audit_hash` esista

**Impl:**
- Duplicare `_apply_self_critique` e `_finalize_complete_dna` per ProductDNA (temporaneo)
- Usa `dna_validator.py`, `dna_scoring.py`, `dna_critique.py`, `audit.py` (già generici)
- **Nota:** refactor in funzioni generiche (accettano qualsiasi modello con `.content`, `._enrichment`, `.audit_hash`) rimandato a PIANO 5

**Commit:** `feat(product-dna): self-critique + enrichment + audit chain (duplicato temporaneo)`

---

### Task 8: Safe_mode per ProductDNA

**Test:** `test_product_dna_safe_mode_blocks_approval`
- Crea ProductDNA con flag CRITICAL in enrichment
- Verifica che `product_section_approve` ritorni 409
- Verifica che il messaggio dica quale strato è vuoto

**Impl:**
- `_approval_block_reasons` per ProductDNA (analogo a CompanyDNA)
- View `product_section_approve` → check safe_mode
- View `product_section_edit` → check safe_mode

**Commit:** `feat(product-dna): safe_mode blocks approval on CRITICAL flags`

---

### Task 9: View crea specialista (stato Bozza)

**Test:** `test_create_product_sets_status_bozza`
- POST a `product_list_create` con nome + tipologia + codice
- Verifica che Product abbia `status="bozza"`
- Verifica redirect a `product_detail`

**Impl:**
- Aggiornare `product_list_create` per ricevere `tipologia` e `codice`
- Default `status="bozza"`
- Template `product_list.html` mostra stato come badge

**Commit:** `feat(product): create specialist with status bozza`

---

### Task 10: Dashboard stato specialista

**Test:** `test_dashboard_shows_specialist_status_badge`
- Carica dashboard con 3 specialista (bozza, attivo, archiviato)
- Verifica che ogni badge abbia la classe CSS corretta per lo stato

**Impl:**
- Template `product_list.html` → badge colorato per stato
- CSS: bozza (grigio), in_costruzione (giallo), in_validazione (arancione), attivo (verde), archiviato (rosso)
- **Nota:** 5 stati, non 6. `in_aggiornamento` è gestito come flag su stato Attivo.

**Commit:** `ui(product): status badges for specialist list`

---

### Task 11: Transizione stato → In Costruzione

**Test:** `test_generate_complete_dna_sets_status_in_costruzione`
- Genera DNA Specialista completo
- Verifica che `product.status` sia diventato `"in_costruzione"`

**Impl:**
- View `product_dna_generate` → dopo generazione completa, `product.status = "in_costruzione"`
- `product.save(update_fields=["status"])`

**Commit:** `feat(product): transition to in_costruzione after DNA complete`

---

### Task 12: Regression + mock client update

**Test:** `test_product_dna_regression`
- Run test suite completa ProductDNA
- Verifica nessun errore sui vecchi test

**Impl:**
- Aggiornare `MockLLMClient` per supportare `PRODUCT_DNA_GENERATION` branch
- Mock ritorna 6 strati + sintesi_cognitiva

**Commit:** `test(product): regression run + mock client updated`

---

## 3. Smoke Test Finale (manuale)

1. Crea specialista "Canale Ispezionabile" (tipologia: canale, codice: CI-001) → stato Bozza
2. Upload 2-3 file tecnici (brochure, disegni) → biblioteca specialistica
3. Avvia intervista specialistica (10 domande Round 1)
4. Rispondi in modo insufficiente a 3 domande → trigger Gap Engine → Round 2 (max 5 follow-up per Foundation)
5. Rispondi a Round 2 → genera DNA Specialista
6. Verifica stato = In Costruzione
7. Review DNA: tutte e 6 le sezioni piene? Sintesi Cognitiva presente?
8. Safe_mode: se ci sono sezioni vuote, approvazione bloccata?
9. Approva tutte le sezioni → stato = In Validazione (F11, PIANO 5: Promote + Attivo)

---

## 4. Rischi e mitigazioni

| Rischio | Probabilità | Impatto | Mitigazione |
|---|---|---|---|
| Prompt specialista duplica contenuto del Generale | Alta | Medio | Sezione contesto nel prompt: "Eredita, non ripete". Focus 100% famiglia prodotto vs 90% prodotti del Generale. |
| ProductDNA 5→6 strati rompe vecchi dati | Bassa | Medio | Migration purge (dati di test solo) |
| Gap Engine per Product condivide codice Company → regressione | Media | Alto | Test unitari separati, mock isolati, limiti diversi |
| Stati specialista non usati da nessuna view → morto | Media | Medio | Task 9-11 implementano transizioni reali nel flusso |

---

## 5. Dipendenze

```
Task 1 (modello stato)  ──────┬──→ Task 9 (crea specialista)
                              │
Task 2 (6 strati) ────────────┼──→ Task 6 (sintesi) ──→ Task 7 (enrichment)
                              │
Task 3 (prompt) ──────────────┘
                              │
Task 4 (domande) ─────────────┴──→ Task 5 (Gap Engine) ──→ Task 11 (transizione)
                              │
Task 8 (safe_mode) ←──────────┘
```

---

## 6. Metriche target

- Test nuovi: ~15 test
- Coverage target: >80% (come PIANO 3)
- Commit target: 12 task = ~12 commit
- Tempo stimato: 2-3 sessioni (deploy alla fine)

---

## 7. Cosa NON è in PIANO 4 (rimandato)

- **PROMOTE (Motore B):** PIANO 5 — estrazione principi trasversali da DNA Specialista → DNA Generale
- **Coerenza T3 (Motore C):** PIANO 6 — audit ogni 3 specialisti
- **Rename Product→Specialista:** ciclo dedicato quando il sistema è stabile
- **Pubblicazione F15:** integration futura (sito web, e-commerce)
- **Re-intervista specialista attivo:** ciclo futuro

---

*Fine piano — review con Guglielmo prima di esecuzione.*