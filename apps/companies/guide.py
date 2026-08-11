"""ZEUS Guide — assistente-guida in-app (percorso deterministico + aiuto LLM).

La guida accompagna il titolare dell'azienda attraverso le fasi del prodotto:
anagrafica → sito web → documenti → pre-DNA → domande DNA Generale →
DNA Generale approvato → Specialista → domande Specialista → DNA Specialista
approvato → agente pronto.

Architettura ibrida:
- percorso e prossimo passo sono DETERMINISTICI, calcolati dallo stato del DB
  (compute_progress): l'LLM non decide mai dove si trova l'utente;
- l'LLM serve solo a spiegare ("perche' questo passo?") e ad aiutare
  ("bozza di risposta" alle domande, grounded sui documenti caricati).

A differenza della chat "Testa il tuo agente" (apps.companies.agent), la guida
NON ha gate: funziona sempre, anche prima dell'anagrafica e senza DNA.
La conversazione vive nella Django session (chiave SESSION_HISTORY_KEY).
"""

import logging

from django.urls import reverse

from apps.companies.agent import (
    format_retrieval_block,
    get_approved_company_dna,
    retrieve_context,
)
from apps.companies.llm_client import GUIDE_CHAT_MARKER
from apps.companies.models import DNAGenerale, ProductDNA, ProductQuestion

logger = logging.getLogger(__name__)

# Chiave di sessione che contiene la storia chat della guida:
# lista di dict {role, content}. Mai persistita su DB (solo LLMCall per audit).
SESSION_HISTORY_KEY = "guide_chat_history"


# ---------------------------------------------------------------------------
# Fasi del percorso — predicati deterministici sullo stato del tenant
# ---------------------------------------------------------------------------

def _has_complete_anagrafica(company):
    return company.has_complete_anagrafica()


def _has_connected_site(company):
    """Sito collegato: URL noto, da una Source registrata o dall'anagrafica.

    Il campo `sito_web` dell'anagrafica sopravvive al reset dell'onboarding
    ("Riparti da capo" cancella le Source): l'utente considera il sito gia'
    collegato e la guida deve rifletterlo. L'analisi vera e' gated dalla
    fase pre-DNA, che richiede lo scraping alla generazione.
    """
    if company.sources.exists():
        return True
    return bool(company.sito_web)


def _has_company_files(company):
    return company.company_files.exists()


def _has_pre_dna(company):
    return company.dna_versions.filter(dna_type=DNAGenerale.TYPE_PRE).exists()


def _dna_questions_answered(company):
    """Tutte le domande del DNA Generale hanno una risposta (almeno una esiste)."""
    questions = company.company_questions.all()
    return questions.exists() and not questions.filter(answered_at__isnull=True).exists()


def _has_approved_dna(company):
    return get_approved_company_dna(company) is not None


def _has_product(company):
    return company.products.exists()


def _specialist_questions_answered(company):
    """Almeno uno Specialista con documenti caricati e domande tutte risposte."""
    for product in company.products.all():
        if not product.product_files.exists():
            continue
        questions = product.product_questions.all()
        if questions.exists() and not questions.filter(answered_at__isnull=True).exists():
            return True
    return False


def _has_approved_product_dna(company):
    return ProductDNA.objects.filter(
        product__company=company,
        dna_type=ProductDNA.TYPE_COMPLETE,
        is_current=True,
        is_approved__isnull=False,
    ).exists()


def _agent_ready(company):
    """Gate finale: DNA Generale approvato + almeno un DNA Specialista approvato."""
    return _has_approved_dna(company) and _has_approved_product_dna(company)


# Metadati curati delle 10 fasi, nell'ordine fisso del percorso.
# cta_url_name: route esistente senza argomenti (reverse in compute_progress).
PHASES = [
    {
        "id": "anagrafica",
        "title": "Anagrafica aziendale",
        "why": (
            "E' l'identita' ufficiale della tua azienda: ragione sociale, contatti "
            "e canali. Senza questi dati ZEUS non puo' costruire un agente credibile "
            "ne' farlo rispondere ai tuoi clienti."
        ),
        "tips": [
            "Prepara partita IVA, REA e PEC: li inserisci in un minuto.",
            "Aggiungi almeno un telefono, un WhatsApp e un profilo social: "
            "sono i canali che l'agente usera' per indirizzare i clienti.",
        ],
        "cta_url_name": "company-anagrafica",
        "cta_label": "Compila l'anagrafica",
        "is_done": _has_complete_anagrafica,
    },
    {
        "id": "sito_web",
        "title": "Sito web collegato",
        "why": (
            "Il sito e' la prima fonte da cui ZEUS impara come lavori: prodotti, "
            "servizi, tono. Analizzandolo costruisce la prima bozza di conoscenza "
            "dell'agente."
        ),
        "tips": [
            "Incolla l'indirizzo del sito: la scansione parte da sola.",
            "Se non hai un sito, carica i documenti aziendali: la conoscenza "
            "arrivera' da quelli.",
        ],
        "cta_url_name": "onboarding-index",
        "cta_label": "Collega il tuo sito",
        "is_done": _has_connected_site,
    },
    {
        "id": "documenti_aziendali",
        "title": "Documenti aziendali",
        "why": (
            "Listini, schede tecniche, presentazioni: quello che non e' sul sito "
            "sta nei tuoi documenti. Piu' materiale carichi, piu' l'agente risponde "
            "con fatti tuoi invece che con frasi generiche."
        ),
        "tips": [
            "Carica PDF o testo: il contenuto viene letto e indicizzato.",
            "Un documento alla volta: puoi aggiungere altro quando vuoi.",
        ],
        "cta_url_name": "onboarding-index",
        "cta_label": "Carica un documento",
        "is_done": _has_company_files,
        # I documenti sono OPZIONALI nel flusso reale di onboarding: la fase
        # non blocca il "passo corrente", resta un consiglio (recommended).
        "optional": True,
    },
    {
        "id": "pre_dna",
        "title": "Analisi iniziale (pre-DNA)",
        "why": (
            "ZEUS incrocia sito e documenti e ti mostra come ha capito l'azienda. "
            "E' la fotografia di partenza: da qui nascono le domande giuste per te."
        ),
        "tips": [
            "L'analisi si genera dalla pagina di onboarding dopo la scansione.",
            "Leggila con attenzione: se qualcosa non torna, lo correggi "
            "rispondendo alle domande.",
        ],
        "cta_url_name": "onboarding-index",
        "cta_label": "Genera l'analisi iniziale",
        "is_done": _has_pre_dna,
    },
    {
        "id": "domande_dna",
        "title": "Domande sul DNA Generale",
        "why": (
            "Sono le domande che un consulente ti farebbe prima di parlare ai tuoi "
            "clienti. Le tue risposte trasformano la bozza automatica nel vero DNA "
            "dell'azienda."
        ),
        "tips": [
            "Rispondi come parleresti a un nuovo venditore: concreto, con esempi.",
            "Puoi chiedere a me una bozza di risposta basata sui tuoi documenti.",
        ],
        "cta_url_name": "dna-questions",
        "cta_label": "Rispondi alle domande",
        "is_done": _dna_questions_answered,
    },
    {
        "id": "dna_generale",
        "title": "DNA Generale approvato",
        "why": (
            "Il DNA Generale e' la fonte di verita' dell'agente: identita', confini, "
            "tono. Con la tua approvazione diventa operativo e sblocca gli "
            "Specialisti di prodotto."
        ),
        "tips": [
            "Rileggi il DNA generato e approva sezione per sezione.",
            "Se una sezione non ti convince, modificala prima di approvare.",
        ],
        "cta_url_name": "dna-current",
        "cta_label": "Apri il DNA Generale",
        "is_done": _has_approved_dna,
    },
    {
        "id": "specialista",
        "title": "Primo Specialista",
        "why": (
            "Ogni prodotto o servizio parla in modo diverso. Lo Specialista e' "
            "l'esperto di quel prodotto: nasce dal DNA Generale e impara dai "
            "documenti specifici."
        ),
        "tips": [
            "Crea uno Specialista per il prodotto che vendi di piu'.",
            "Nome e codice interno ti aiutano a ritrovarlo tra documenti e listini.",
        ],
        "cta_url_name": "specialista-list-create",
        "cta_label": "Crea il primo Specialista",
        "is_done": _has_product,
    },
    {
        "id": "domande_specialista",
        "title": "Domande dello Specialista",
        "why": (
            "Come per il DNA Generale: carichi i documenti del prodotto, ZEUS "
            "genera le domande tecniche e tu rispondi. E' cosi' che lo Specialista "
            "impara dettagli, varianti e limiti."
        ),
        "tips": [
            "Carica schede tecniche e listini del prodotto prima delle domande.",
            "Anche qui puoi chiedermi una bozza di risposta basata sui documenti.",
        ],
        "cta_url_name": "specialista-list-create",
        "cta_label": "Apri i tuoi Specialisti",
        "is_done": _specialist_questions_answered,
    },
    {
        "id": "dna_specialista",
        "title": "DNA Specialista approvato",
        "why": (
            "Con l'approvazione il DNA del prodotto diventa operativo: lo "
            "Specialista entra a far parte della conoscenza attiva dell'agente."
        ),
        "tips": [
            "Verifica il DNA generato e approva le sezioni.",
            "Quando e' approvato, lo Specialista e' pronto per essere attivato.",
        ],
        "cta_url_name": "specialista-list-create",
        "cta_label": "Vai allo Specialista",
        "is_done": _has_approved_product_dna,
    },
    {
        "id": "agente_pronto",
        "title": "Agente pronto",
        "why": (
            "DNA Generale e almeno uno Specialista approvati: l'agente ora risponde "
            "come il tuo miglior tecnico-commerciale, usando solo la tua conoscenza."
        ),
        "tips": [
            "Provalo con le domande che i clienti ti fanno davvero.",
            "Se una risposta non ti convince, affina il DNA: l'agente migliora "
            "di conseguenza.",
        ],
        "cta_url_name": "agent-chat",
        "cta_label": "Testa il tuo agente",
        "is_done": _agent_ready,
    },
]


GUIDE_RULES = """## Regole di comportamento
- Non inventare mai il percorso: le fasi e il loro ordine sono SOLO quelli della checklist qui sopra. Non aggiungere, rinominare o riordinare passi.
- Il prossimo passo e' sempre quello marcato come "in corso": indicalo in modo concreto, dicendo cosa fare e dove (usa la CTA indicata). I passi marcati "(consigliato, non obbligatorio)" arricchiscono la conoscenza ma non bloccano il percorso: proponili solo se pertinenti, mai come ostacolo.
- Rispondi sempre nella lingua in cui l'utente scrive.
- Tono: professionale, diretto, concreto. Frasi brevi, niente frasi di circostanza.
- Non sei l'agente dell'azienda: non rispondere a domande sui prodotti come farebbe un tecnico. Se l'utente esce dal percorso, riportalo al prossimo passo.
- Non promettere tempi, prezzi o funzionalita' di ZEUS non descritte qui."""


# Quick-action del widget: messaggio canonico usato quando arriva solo l'action.
QUICK_ACTION_PROMPTS = {
    "missing": "Cosa mi manca per completare il prossimo passo?",
    "why": "Perche' questo passo e' importante? Cosa ottengo quando lo completo?",
    "draft": "Puoi propormi una bozza di risposta per la domanda che devo compilare?",
}


# ---------------------------------------------------------------------------
# Progresso deterministico
# ---------------------------------------------------------------------------

def compute_progress(company):
    """Checklist delle fasi con stato done/current/todo, calcolato dal DB.

    "current" e' la prima fase obbligatoria non completata; le fasi opzionali
    non completate sono "recommended" (consigliate, non bloccanti) e tutte le
    successive sono "todo".
    Ritorna {"phases": [...], "current": dict|None, "done_count", "total_count"}.
    Ogni fase esposta contiene: id, title, why, tips, cta_url, cta_label, status.
    """
    phases = []
    current_found = False
    for phase in PHASES:
        done = phase["is_done"](company)
        if done:
            status = "done"
        elif phase.get("optional"):
            status = "recommended"
        elif not current_found:
            status = "current"
            current_found = True
        else:
            status = "todo"
        phases.append(
            {
                "id": phase["id"],
                "title": phase["title"],
                "why": phase["why"],
                "tips": list(phase["tips"]),
                "cta_url": reverse(phase["cta_url_name"]),
                "cta_label": phase["cta_label"],
                "status": status,
            }
        )
    current = next((p for p in phases if p["status"] == "current"), None)
    return {
        "phases": phases,
        "current": current,
        "done_count": sum(1 for p in phases if p["status"] == "done"),
        "total_count": len(phases),
    }


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_STATUS_MARKERS = {"done": "[x]", "current": "[>]", "recommended": "[+]", "todo": "[ ]"}


def _checklist_block(progress):
    lines = ["## Checklist del percorso (stato reale, calcolato dal database)"]
    for phase in progress["phases"]:
        marker = _STATUS_MARKERS[phase["status"]]
        if phase["status"] == "current":
            suffix = "  <- passo corrente"
        elif phase["status"] == "recommended":
            suffix = "  (consigliato, non obbligatorio)"
        else:
            suffix = ""
        lines.append(f"- {marker} {phase['title']}{suffix}")
    return "\n".join(lines)


def _current_phase_block(progress):
    current = progress["current"]
    if current is None:
        return (
            "## Stato attuale\nPercorso completato: l'agente e' pronto e "
            "l'utente puo' testarlo nella chat dedicata."
        )
    lines = [
        "## Passo corrente",
        f"Fase: {current['title']}",
        f"Perche' serve: {current['why']}",
    ]
    if current["tips"]:
        lines.append("Consigli:")
        lines.extend(f"- {tip}" for tip in current["tips"])
    lines.append(f"CTA: {current['cta_label']} ({current['cta_url']})")
    return "\n".join(lines)


def build_guide_system_prompt(company):
    """System prompt della guida: ruolo + snapshot deterministico della checklist.

    Nessun gate: il prompt esiste per qualunque stato dell'azienda, anche
    appena registrata. Il percorso descritto e' solo quello in PHASES.
    """
    progress = compute_progress(company)
    sections = [
        f"[{GUIDE_CHAT_MARKER}]",
        (
            f"Sei ZEUS Guide, la guida operativa che accompagna il titolare di "
            f"{company.name} nella costruzione del suo agente, passo dopo passo. "
            "Conosci il percorso ZEUS e lo stato esatto dell'azienda: lo vedi "
            "nella checklist qui sotto, calcolata dai dati reali del suo account."
        ),
        _checklist_block(progress),
        _current_phase_block(progress),
        GUIDE_RULES,
    ]
    return "\n\n".join(s.strip() for s in sections if s and s.strip())


# ---------------------------------------------------------------------------
# Quick-action "draft" — bozza di risposta grounded sui documenti del tenant
# ---------------------------------------------------------------------------

def get_current_unanswered_question(company):
    """Prima domanda senza risposta: DNA Generale, poi domande Specialista."""
    question = (
        company.company_questions.filter(answered_at__isnull=True)
        .order_by("question_round", "id")
        .first()
    )
    if question is not None:
        return question.question
    product_question = (
        ProductQuestion.objects.filter(product__company=company, answered_at__isnull=True)
        .order_by("question_round", "id")
        .first()
    )
    return product_question.question if product_question is not None else None


def build_draft_block(company):
    """Contesto extra per la quick-action "draft".

    Prende la prima domanda senza risposta, recupera gli estratti rilevanti
    dalla KB del tenant (retrieve_context) e chiede all'LLM una bozza di
    risposta grounded. Ritorna "" se non ci sono domande aperte.
    """
    question_text = get_current_unanswered_question(company)
    if not question_text:
        return ""
    parts = [
        (
            "## Bozza guidata — domanda corrente\n"
            "La domanda a cui il cliente deve rispondere ora e':\n"
            f"\"{question_text}\""
        )
    ]
    retrieval_block = format_retrieval_block(retrieve_context(company, question_text))
    if retrieval_block:
        parts.append(retrieval_block)
    parts.append(
        "Proponi una bozza di risposta in prima persona, come se fossi il "
        "titolare dell'azienda. Usa SOLO fatti ricavabili dagli estratti qui "
        "sopra e cita la fonte di ogni affermazione; se un punto non e' "
        "coperto dai documenti, segnalalo con [da completare] invece di "
        "inventare. Il cliente revisionera' la bozza prima di salvarla."
    )
    return "\n\n".join(parts)
