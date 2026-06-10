# ⚡ ZEUS — Meta-Framework Knowledge Engineering

**Versione:** 1.0.0  
**Autore:** Caponi AI Studio  
**Stato:** Alpha / In sviluppo

---

## Cos'è ZEUS

ZEUS è un meta-framework CLI per generare **Knowledge Base strutturate** per agenti tecnici AI verticali. Non conosce il prodotto né l'azienda in anticipo. Interroga fonti tecniche e genera DNA strutturato: prima per ogni famiglia prodotto, poi a livello aziendale.

> **"ZEUS non legge i dati. ZEUS interpreta i dati attraverso il DNA."**

---

## Architettura a Tre Livelli

```
┌─────────────────────────────────────────────────────────────┐
│  LIVELLO 3 — Agente Tecnico Operativo                       │
│  Knowledge Base = DNA Aziendale + DNA Famiglie Prodotto     │
├─────────────────────────────────────────────────────────────┤
│  LIVELLO 2 — Generatore DNA Aziendale                       │
│  Input: DNA Famiglie + Risposte domande azienda             │
├─────────────────────────────────────────────────────────────┤
│  LIVELLO 1 — Generatore DNA Famiglia Prodotto               │
│  Input: Fonti tecniche + Risposte 20 domande               │
└─────────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# 1. Inizializza un nuovo progetto cliente
zeus init RossiMetalli

# 2. Aggiungi una famiglia prodotto con le sue fonti
zeus family add CanaleIspezionabile \
  --brochure ./fonti/brochure.pdf \
  --drawings ./fonti/disegni/ \
  --manual ./fonti/manuale.pdf

# 3. Genera il DNA Famiglia (Step 1)
zeus family generate CanaleIspezionabile

# 4. Rispondi al questionario aziendale (Step 2)
zeus company interview

# 5. Genera il DNA Aziendale
zeus company generate

# 6. Assembla la Knowledge Base finale (Step 3)
zeus assemble

# 7. Esporta per HERMES
zeus export hermes
```

---

## Struttura Progetto

```
D:\Zeus\
├── zeus/              # Package Python
├── clients/           # Progetti cliente (isolati)
├── templates/         # Template base
├── tests/             # Test suite
└── docs/              # Documentazione
```

---

## Requisiti

- Python 3.11+
- API key per LLM (Kimi, OpenAI, Anthropic)
- Fonti tecniche del cliente (PDF, immagini, Markdown)

---

## Documentazione

- [Architettura](docs/ARCHITECTURE.md)
- [Workflow](docs/WORKFLOW.md)

---

*Meta-Framework ZEUS v1.0 — Caponi AI Studio*
