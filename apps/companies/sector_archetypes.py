"""Sector archetype library — tacit knowledge patterns per macro-sector.

This module provides ZEUS with sector-aware context for question generation
without hardcoding sector-specific questions. Each archetype defines categories
of tacit operational knowledge that are unlikely to appear in public documents
(sites, brochures) but are critical for a complete company DNA.

Usage:
    from apps.companies.sector_archetypes import get_archetype_context
    context = get_archetype_context(company)
    # Returns a structured string for the LLM prompt, or empty string.

Design principles:
    - Archetypes are inspiration, not templates. The LLM generates original
      questions inspired by these categories, never copied from them.
    - The library is agnostic: it knows about "manufacturing" as a category,
      not about "stainless steel" or "protective film".
    - New archetypes can be added by extending SECTOR_ARCHETYPES.
    - If the company has no settore_primario, the library returns only
      universal meta-questions (always relevant, any sector).
"""

from __future__ import annotations

from apps.companies.models import Company


SECTOR_ARCHETYPES: dict[str, dict] = {
    Company.ARCHETIPO_BENI_FISICI: {
        "label": "Produzione di beni fisici",
        "tacit_knowledge_categories": [
            "Gestione dei materiali consumabili nel processo produttivo (protezioni, collanti, film, sigillanti)",
            "Regole operative pre-lavorazione che esistono ma non sono scritte (verifiche, validazioni, sequenze obbligatorie)",
            "Comportamento del prodotto a contatto con materiali terzi (cosa succede in cantiere, reazioni chimiche, incompatibilità)",
            "Errori ricorrenti di chi installa o usa il prodotto fisicamente (errori costosi, errori frequenti, errori banali)",
            "Cultura aziendale sul custom non scritta nei documenti (quando si accetta, quando si rifiuta, perché)",
        ],
    },
    Company.ARCHETIPO_LAVORAZIONE: {
        "label": "Trasformazione/lavorazione materiali",
        "tacit_knowledge_categories": [
            "Tolleranze reali di lavorazione vs tolleranze dichiarate (cosa si può davvero ottenere, cosa è irreale)",
            "Sequenze di lavorazione non documentate ma critiche per il risultato finale",
            "Comportamento del materiale durante la lavorazione (deformazioni, ritiri, tensioni residue)",
            "Scarti tipici e loro cause (cosa va storto più spesso, come si previene)",
            "Limiti della capacità produttiva che non compaiono nelle schede tecniche",
        ],
    },
    Company.ARCHETIPO_INSTALLAZIONE: {
        "label": "Installazione/posa",
        "tacit_knowledge_categories": [
            "Procedure di posa che esistono ma non sono nei manuali (trucchetti del mestiere, accorgimenti pratici)",
            "Condizioni di cantiere che cambiano il risultato (umidità, temperatura, piano di appoggio)",
            "Errori tipici dell'installatore e come si prevengono (cosa non fanno, cosa saltano, cosa sbagliano)",
            "Manutenzione nella realtà vs manutenzione dichiarata (cosa serve davvero, ogni quanto, chi la fa)",
            "Adattamenti sul campo non previsti a progetto (come si gestiscono, chi decide, limiti)",
        ],
    },
    Company.ARCHETIPO_SERVIZI: {
        "label": "Servizi professionali",
        "tacit_knowledge_categories": [
            "Come si qualifica un cliente (chi si accetta, chi si rifiuta, segnali di allarme)",
            "Come si gestisce l'escalation al senior (quando, come, chi decide)",
            "Promesse che non si fanno mai al cliente (perché, cosa succede se le si fa)",
            "Gestione delle aspettative nei primi 90 giorni (cosa il cliente si aspetta vs cosa riceve)",
            "Cultura di consegna: trade-off tra qualità e tempi che non sono scritti da nessuna parte",
        ],
    },
    Company.ARCHETIPO_SOFTWARE: {
        "label": "Software/SaaS",
        "tacit_knowledge_categories": [
            "Limite di responsabilità tra prodotto e integrazione cliente (cosa è colpa vostra, cosa non lo è)",
            "Cosa non è documentato perché ovvio per il team ma non per il cliente (assunzioni implicite)",
            "Configurazioni che funzionano male ma sono tecnicamente possibili (anti-pattern reali)",
            "Onboarding: cosa il cliente nuovo fa di sbagliato nel primo mese (errori tipici, prevenibili)",
            "Scalabilità: dove il prodotto smette di funzionare bene ma nessuno lo scrive (limiti reali)",
        ],
    },
    Company.ARCHETIPO_DISTRIBUZIONE: {
        "label": "Distribuzione/commercio",
        "tacit_knowledge_categories": [
            "Criteri di selezione fornitori che non sono nei documenti (affidabilità reale vs dichiarata)",
            "Gestione delle non conformità nella realtà (cosa si fa quando arriva merce sbagliata)",
            "Logistica: colli di bottiglia non visibili dall'esterno (tempi reali vs dichiarati)",
            "Politica resi e sostituzioni non scritta (quando si fa, chi paga, limiti)",
            "Relazioni con produttori: cosa si può e non si può ottenere (modifiche, priorità, eccezioni)",
        ],
    },
    Company.ARCHETIPO_FORMAZIONE: {
        "label": "Formazione/consulenza",
        "tacit_knowledge_categories": [
            "Cosa cambia tra un cliente che riesce e uno che fallisce (pattern di successo/insuccesso)",
            "Adattamenti del programma che si fanno sempre ma non sono scritti (personalizzazione tacita)",
            "Gestione delle aspettative: cosa promettere e cosa non promettere mai",
            "Momenti critici del percorso formativo (dove i clienti si bloccano, dove si perdono)",
            "Cultura metodologica non documentata (come si lavora davvero, non come è scritto nel programma)",
        ],
    },
}


UNIVERSAL_META_QUESTIONS = [
    "Cosa sa chi lavora qui da 10 anni che non è scritto da nessuna parte?",
    "Qual è l'errore più costoso che un cliente nuovo fa nel primo mese?",
    "Cosa non dite mai nelle brochure o sul sito ma che ogni cliente dovrebbe sapere prima di iniziare?",
]


def get_archetype_context(company: Company) -> str:
    """Build a context string for the LLM prompt based on the company's operational profile.

    Returns a structured text block containing:
    - The company's detected archetype and its tacit knowledge categories
    - Additional flags (prodotto fisico, installatori, custom)
    - Universal meta-questions (always included)

    Returns empty string if settore_primario is not set.
    """
    archetype_key = company.settore_primario
    if not archetype_key or archetype_key not in SECTOR_ARCHETYPES:
        parts = []
        parts.append("DOMANDE META UNIVERSALI (sempre includere negli ultimi 2-3 slot):")
        for q in UNIVERSAL_META_QUESTIONS:
            parts.append(f"- {q}")
        return "\n".join(parts)

    archetype = SECTOR_ARCHETYPES[archetype_key]
    lines = []

    lines.append(f"SETTORE RILEVATO: {archetype['label']}")
    lines.append("")
    lines.append("CATEGORIE DI CONOSCENZA TACITA DA ESPLORARE (genera domande ispirate a queste categorie, non copiarle):")
    for cat in archetype["tacit_knowledge_categories"]:
        lines.append(f"- {cat}")

    flags = []
    if company.prodotto_fisico is True:
        flags.append("Prodotto fisico: SÌ — la conoscenza operativa di cantiere/laboratorio è rilevante")
    elif company.prodotto_fisico is False:
        flags.append("Prodotto fisico: NO — salta domande su installazione fisica, materiali, cantiere")
    if company.installatori_in_filiera is True:
        flags.append("Installatori/posatori in filiera: SÌ — esplora errori operativi a valle, gestione cantiere, comunicazione con posatore")
    if company.custom_frequenza == Company.CUSTOM_SISTEMATICO:
        flags.append("Custom sistematico: esplora profondamente i criteri decisionali del custom (quando sì, quando no, limiti)")
    elif company.custom_frequenza == Company.CUSTOM_RARAMENTE:
        flags.append("Custom raro: esplora come si gestisce quando arriva, non è il core ma succede")
    elif company.custom_frequenza == Company.CUSTOM_MAI:
        flags.append("Niente custom: esplora come si gestisce la richiesta senza offendersi o sembrare rigidi")
    if company.cliente_diretto == Company.CLIENTE_B2B_TECNICO:
        flags.append("Cliente B2B tecnico: il tono delle domande può essere tecnico-specializzato")
    elif company.cliente_diretto == Company.CLIENTE_B2C:
        flags.append("Cliente B2C: il tono delle domande deve essere accessibile, non gergale")

    if flags:
        lines.append("")
        lines.append("CONTESTO OPERATIVO AGGIUNTIVO:")
        for f in flags:
            lines.append(f"- {f}")

    if company.contesto_libero:
        lines.append("")
        lines.append("CONTESTO LIBERO FORNITO DAL CLIENTE (fonte primaria di conoscenza tacita):")
        lines.append(company.contesto_libero)

    lines.append("")
    lines.append("DOMANDE META UNIVERSALI (sempre includere negli ultimi 2-3 slot, a prescindere dal settore):")
    for q in UNIVERSAL_META_QUESTIONS:
        lines.append(f"- {q}")

    return "\n".join(lines)
