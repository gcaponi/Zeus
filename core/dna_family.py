"""Generatore DNA Famiglia Prodotto — Step 1 ZEUS.

Legge fonti tecniche (brochure, disegni, manuale) e genera il DNA Famiglia
rispondendo alle 20 domande D1-D20.
"""

from pathlib import Path

from zeus.config import get_config
from zeus.llm.client import LLMClient
from zeus.models.schemas import DNAFamily, FontiTecniche, SezioneDNA
from zeus.parsers.pdf import parse_pdf

# Domande D1-D20 con titoli
DOMANDE_FAMIGLIA: dict[str, tuple[str, str]] = {
    "D1": ("Identita tecnica autonoma", "Qual e l'identita tecnica autonoma di questa famiglia? Cosa la distingue concettualmente da una 'versione' o 'variante' di un prodotto generico?"),
    "D2": ("Valore distintivo progettuale", "Qual e il problema tecnico reale che questa famiglia risolve, e in che modo la sua soluzione differisce dalle soluzioni concorrenti sul mercato?"),
    "D3": ("Riferimento geometrico/funzionale chiave", "Qual e il riferimento geometrico o funzionale chiave da cui derivano tutti gli altri componenti? (es. sezione, profilo, modulo)"),
    "D4": ("Terminologia vincolante", "Quali termini tecnici corretti devono essere usati, e quali termini colloquiali interni devono essere assolutamente evitati nel DNA?"),
    "D5": ("Sistema meccanico e vincoli esecutivi", "Esistono elementi meccanici specifici (guide, supporti, piastrini, ecc.) con funzione strutturale? Qual e la loro logica di montaggio e le fonti che ne definiscono quote e posizionamento?"),
    "D6": ("Gerarchia delle fonti tecniche", "Qual e la gerarchia corretta delle fonti tecniche? Cosa fornisce la brochure, cosa i disegni, cosa il manuale, cosa eventuali allegati?"),
    "D7": ("Metodi di costruzione", "Esistono due o piu metodi di costruzione/montaggio applicabili allo stesso sistema? Quali sono le differenze in termini di processo, tolleranze e target produttivo?"),
    "D8": ("Comportamento spessore-dipendente", "Lo spessore del materiale cambia il comportamento funzionale o meccanico di qualche componente? Quali soglie spessore determinano cambi di comportamento?"),
    "D9": ("Parametri funzionali", "Esistono offset, gap, tolleranze o dimensioni che potrebbero sembrare estetiche ma sono in realta funzionali e vincolate al funzionamento?"),
    "D10": ("Continuita sistemica", "Quali componenti devono lavorare in continuita tra loro, e quali discontinuita comprometterebbero la coerenza sistemica?"),
    "D11": ("Compatibilita componenti esterni", "Quali componenti esterni (pilette, scarichi, accessori) devono essere compatibili? Esistono vincoli tecnici specifici (spessori, filettature, serraggi)?"),
    "D12": ("Configurazioni installative", "Esistono configurazioni installative alternative (es. integrata vs sottotop, soprapiano vs sottopiano)? Cosa cambia nella lavorazione e cosa resta invariato nell'identita del prodotto?"),
    "D13": ("Logica del custom - confini fissi", "Quali elementi del sistema sono geometricamente/strutturalmente FISSI e non modificabili, e quali sono ADATTABILI in dimensione/lunghezza?"),
    "D14": ("Soglie strutturali", "Esistono soglie dimensionali (lunghezza, larghezza, profondita) oltre le quali un componente cambia principio di funzionamento o supporto?"),
    "D15": ("Procedura custom formale", "Qual e la procedura operativa corretta per una richiesta custom? Quali fasi sono di competenza del cliente, quali dell'azienda, e quali non possono essere saltate o sostituite dall'agente?"),
    "D16": ("Accessibilita vs complessita", "Il prodotto e accessibile a operatori con attrezzature semplici, o richiede strutture produttive avanzate? Questa accessibilita e in contrasto con la complessita tecnica reale?"),
    "D17": ("Sicurezza passiva", "Il prodotto svolge funzioni di sicurezza, contenimento o protezione passiva non immediatamente evidenti? Quali sono e come funzionano?"),
    "D18": ("Interfaccia impianti", "Con quali impianti o sistemi esterni il prodotto deve dialogare? Esistono standard, dimensioni o riferimenti normativi vincolanti?"),
    "D19": ("Percezione qualitativa", "Il prodotto genera un valore percettivo specifico per il cliente finale? Cosa percepisce e quali qualita comunica?"),
    "D20": ("Principio di adattamento", "Qual e il principio di adattamento corretto: il sistema si adatta alla variante, o e la variante che si adatta al sistema mantenendo invariati i principi strutturali?"),
}


class DNAFamilyGenerator:
    """Generatore DNA Famiglia Prodotto."""

    SYSTEM_PROMPT = """Sei ZEUS, ingegnere della conoscenza. Non conosci il prodotto in anticipo.
Leggi le fonti tecniche e rispondi alle domande generate.

REGOLE FONDAMENTALI:
1. Non dedurre, non interpretare liberamente: ricava TUTTO dalle fonti tecniche fornite.
2. Se una informazione NON e nelle fonti, dichiara esplicitamente "Non presente nelle fonti".
3. Usa terminologia tecnica precisa. Evita termini colloquiali.
4. Ogni risposta deve includere riferimenti alle fonti (pagina, sezione, figura).
5. Principio ZEUS: "ZEUS non legge i dati. ZEUS interpreta i dati attraverso il DNA."

FORMATO RISPOSTA:
Per ogni domanda, rispondi con:
- ANALISI: breve analisi delle fonti rilevanti
- RISPOSTA: risposta strutturata e completa
- CITAZIONI: riferimenti specifici alle fonti (es. "Brochure p.3", "Disegno 002-A")
- TERMINOLOGIA: eventuali termini tecnici specifici menzionati"""

    def __init__(self) -> None:
        self.config = get_config()
        self.llm = LLMClient()

    def _load_sources(self, fonti: FontiTecniche) -> str:
        """Carica e concatena tutte le fonti tecniche in un unico contesto."""
        parts: list[str] = []

        if fonti.brochure_path and fonti.brochure_path.exists():
            text, _ = parse_pdf(fonti.brochure_path)
            parts.append(f"=== BROCHURE TECNICA: {fonti.brochure_path.name} ===\n{text}\n")

        if fonti.manual_path and fonti.manual_path.exists():
            text, _ = parse_pdf(fonti.manual_path)
            parts.append(f"=== MANUALE DI MONTAGGIO: {fonti.manual_path.name} ===\n{text}\n")

        if fonti.drawings_dir and fonti.drawings_dir.exists():
            for drawing in sorted(fonti.drawings_dir.iterdir()):
                if drawing.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg"):
                    if drawing.suffix.lower() == ".pdf":
                        text, _ = parse_pdf(drawing)
                        parts.append(f"=== DISEGNO: {drawing.name} ===\n{text}\n")
                    else:
                        parts.append(f"=== IMMAGINE: {drawing.name} ===\n[Immagine CAD: {drawing.name}]\n")

        for att in fonti.attachments:
            if att.exists() and att.suffix.lower() == ".pdf":
                text, _ = parse_pdf(att)
                parts.append(f"=== ALLEGATO: {att.name} ===\n{text}\n")

        return "\n".join(parts)

    def _ask_question(self, context: str, code: str, titolo: str, domanda: str) -> SezioneDNA:
        """Pone una singola domanda al LLM e restituisce la sezione."""
        user_prompt = f"""FONTI TECNICHE:
{context[:40000]}  # limita contesto per non eccedere token

DOMANDA {code}: {titolo}
{domanda}

Rispondi seguendo il formato richiesto nel system prompt."""

        response = self.llm.chat(
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )

        # Estrai citazioni e terminologia dalla risposta
        citazioni: list[str] = []
        terminologia: dict[str, str] = {}

        # Parsing semplice delle citazioni
        for line in response.split("\n"):
            if "CITAZIONI:" in line or "cit." in line.lower():
                citazioni.append(line.strip())

        return SezioneDNA(
            codice=code,
            titolo=titolo,
            domanda=domanda,
            risposta=response,
            citazioni_fonti=citazioni,
        )

    def generate(
        self,
        nome: str,
        nome_commerciale: str,
        fonti: FontiTecniche,
    ) -> DNAFamily:
        """Genera il DNA Famiglia Prodotto completo.

        Args:
            nome: Nome identificativo famiglia (es. "BVCI")
            nome_commerciale: Nome commerciale (es. "Canale Ispezionabile")
            fonti: Fonti tecniche vincolanti

        Returns:
            DNAFamily completo con tutte le sezioni D1-D20
        """
        context = self._load_sources(fonti)
        sezioni: dict[str, SezioneDNA] = {}
        terminologia_corretta: dict[str, str] = {}
        terminologia_bandita: list[str] = []

        for code, (titolo, domanda) in DOMANDE_FAMIGLIA.items():
            sezione = self._ask_question(context, code, titolo, domanda)
            sezioni[code] = sezione

        # Estrai terminologia dalle risposte (parsing semplificato)
        # TODO: migliorare con regex o LLM dedicato
        for code in ("D4",):
            if code in sezioni:
                # La D4 contiene terminologia, prova a estrarla
                pass

        return DNAFamily(
            nome=nome,
            nome_commerciale=nome_commerciale,
            fonti=fonti,
            sezioni=sezioni,
            terminologia_corretta=terminologia_corretta,
            terminologia_bandita=terminologia_bandita,
            checksum_fonti=fonti.checksum_summary(),
        )

    def generate_draft(
        self,
        nome: str,
        nome_commerciale: str,
        fonti: FontiTecniche,
    ) -> DNAFamily:
        """Genera una bozza DNA (placeholder per review interattiva).

        Args:
            nome: Nome identificativo famiglia
            nome_commerciale: Nome commerciale
            fonti: Fonti tecniche

        Returns:
            DNAFamily bozza
        """
        return self.generate(nome, nome_commerciale, fonti)
