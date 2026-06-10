"""Generatore DNA Aziendale — Step 2 ZEUS.

Incrocia tutti i DNA Famiglia Prodotto generati con le risposte alle 20 domande
A1-A20 per estrarre i principi trasversali aziendali.
"""

from zeus.config import get_config
from zeus.llm.client import LLMClient
from zeus.models.schemas import DNACompany, DNAFamily, SezioneDNA

# Domande A1-A20 con titoli
DOMANDE_AZIENDA: dict[str, tuple[str, str]] = {
    "A1": ("DNA come sistema interpretativo", "Come deve leggere l'agente tecnico i prodotti di questa azienda? Qual e il modo corretto di interpretare un problema tecnico prima di entrare nel dettaglio di una famiglia?"),
    "A2": ("Gerarchia DNA", "Qual e la gerarchia corretta tra i tre livelli di conoscenza: principi trasversali (generali), logiche specifiche di famiglia, dati reali delle fonti tecniche?"),
    "A3": ("Pilastri trasversali", "Attraverso quali pilastri funzionali trasversali devono essere letti TUTTI i prodotti dell'azienda? (es. sicurezza, struttura, interfaccia impianti, produttivita)"),
    "A4": ("Principio di affiancamento", "Quando un cliente dice 'ho sempre fatto senza questo prodotto', qual e il ragionamento corretto che l'agente deve usare per non contrapporsi ma affiancarsi?"),
    "A5": ("Spostamento del valore", "Qual e lo spostamento di valore che l'azienda propone? Da quale metrica errata (es. prezzo pezzo) a quale metrica corretta (es. costo sistema)?"),
    "A6": ("Materiali come comportamento", "Quali materiali lavora l'azienda? Non l'elenco, ma qual e la chiave di lettura comportamentale che cambia l'approccio tecnico in base al materiale?"),
    "A7": ("Dichotomia materiali", "Esiste una distinzione fondamentale tra due classi di materiali che cambia radicalmente le operazioni di lavorazione, fissaggio e montaggio? Qual e?"),
    "A8": ("Segmentazione funzionale", "Esistono segmenti di mercato o tipologie funzionali (es. bagno/cucina, interno/esterno) con riferimenti nominali? Come deve l'agente gestire il rapporto tra questi riferimenti e i dati reali?"),
    "A9": ("Logica del film/spessore", "Esistono configurazioni materiali alternative (spessori, protezioni, rivestimenti) che indicano destinazioni d'uso diverse? Come si leggono?"),
    "A10": ("Progettazione per il finito", "I prodotti sono progettati per il grezzo o per il finito? Cosa questo implica per il cliente e per l'uso corretto/sbagliato?"),
    "A11": ("Fissaggio differenziato", "La stessa operazione (fissaggio, incollaggio, saldatura) cambia in base alla classe di materiale? In che modo?"),
    "A12": ("Incollaggio come gestione", "Qual e la filosofia corretta dell'incollaggio/sigillatura nei prodotti dell'azienda? E una barriera assoluta o un sistema di gestione?"),
    "A13": ("Piste/flussi controllati", "Esistono percorsi di deflusso, ventilazione o gestione che devono essere lasciati APERTI e non sigillati? Quali e perche?"),
    "A14": ("Tolleranza come realta produttiva", "Qual e l'approccio corretto alle tolleranze produttive? Il sistema le assorbe o l'operatore le gestisce?"),
    "A15": ("Validazione preliminare", "Esiste una regola ferrea di validazione preliminare che vale per tutti i prodotti? Quale?"),
    "A16": ("Custom come condizione", "Qual e la postura corretta dell'agente di fronte a una richiesta custom? Come deve essere letta: liberta, diritto, o condizione tecnica?"),
    "A17": ("Standard come ottimizzazione", "Perche lo standard non e un limite ma una soluzione ottimizzata? Come lo spiega l'agente?"),
    "A18": ("Postura anti-custom", "Quali sono i tre comportamenti vietati di fronte al custom? (es. non incoraggiare automaticamente, non promettere, non banalizzare)"),
    "A19": ("Tono comunicativo adattivo", "Come cambia il tono comunicativo dell'agente in base all'interlocutore? Esistono almeno due registri distinti?"),
    "A20": ("Principio di non ridondanza", "Qual e la regola di non ridondanza? Quando l'agente deve attivare i principi generali e quando deve restare nel specifico della domanda?"),
}


class DNACompanyGenerator:
    """Generatore DNA Aziendale."""

    SYSTEM_PROMPT = """Sei ZEUS, architetto della conoscenza aziendale.
Hai generato N DNA famiglia prodotto. Ora incrociali con la visione dell'azienda
per estrarre i principi trasversali.

REGOLE FONDAMENTALI:
1. Ogni principio aziendale DEVE essere ancorato a almeno 2 esempi concreti tratti dai DNA famiglia prodotto.
2. Non creare principi generici: ogni affermazione deve essere verificabile nei DNA famiglia.
3. La gerarchia e: DNA Aziendale (principi) > DNA Famiglia (specifici) > Fonti Tecniche (dati reali).
4. Se un principio non trova conferma nei DNA famiglia, dichiaralo come "ipotetico".
5. Principio ZEUS: "ZEUS non legge i dati. ZEUS interpreta i dati attraverso il DNA."

FORMATO RISPOSTA:
Per ogni domanda, rispondi con:
- PRINCIPIO: enunciato chiaro e conciso del principio aziendale
- ESEMPI: almeno 2 esempi concreti da DNA famiglia (cita la famiglia e la sezione)
- APPLICAZIONE: come l'agente deve usarlo nelle risposte
- LIMITI: quando NON applicare questo principio"""

    def __init__(self) -> None:
        self.config = get_config()
        self.llm = LLMClient()

    def _build_context(self, famiglie: list[DNAFamily], risposte_interview: dict[str, str] | None = None) -> str:
        """Costruisce il contesto incrociando tutti i DNA famiglia."""
        parts: list[str] = []

        parts.append("=== DNA FAMIGLIE PRODOTTO ===\n")
        for fam in famiglie:
            parts.append(f"\n--- FAMIGLIA: {fam.nome_commerciale or fam.nome} ---\n")
            for code, sezione in fam.sezioni.items():
                parts.append(f"\n{code}: {sezione.titolo}\n{sezione.risposta[:2000]}\n")

        if risposte_interview:
            parts.append("\n=== RISPOSTE INTERVIEW AZIENDALE ===\n")
            for code, risposta in risposte_interview.items():
                parts.append(f"\n{code}: {risposta}\n")

        return "\n".join(parts)

    def _ask_question(
        self,
        context: str,
        code: str,
        titolo: str,
        domanda: str,
    ) -> SezioneDNA:
        """Pone una singola domanda A1-A20 al LLM."""
        user_prompt = f"""CONTESTO:
{context[:40000]}  # limita contesto

DOMANDA {code}: {titolo}
{domanda}

Rispondi seguendo il formato richiesto."""

        response = self.llm.chat(
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )

        return SezioneDNA(
            codice=code,
            titolo=titolo,
            domanda=domanda,
            risposta=response,
        )

    def generate(
        self,
        nome_azienda: str,
        famiglie: list[DNAFamily],
        risposte_interview: dict[str, str] | None = None,
    ) -> DNACompany:
        """Genera il DNA Aziendale completo.

        Args:
            nome_azienda: Nome dell'azienda cliente
            famiglie: Lista di DNA Famiglia già generati
            risposte_interview: Risposte dell'utente alle domande A1-A20 (opzionale)

        Returns:
            DNACompany completo con tutte le sezioni A1-A20
        """
        context = self._build_context(famiglie, risposte_interview)
        sezioni: dict[str, SezioneDNA] = {}
        pilastri: list[str] = []
        famiglie_riferite = [f.nome for f in famiglie]

        for code, (titolo, domanda) in DOMANDE_AZIENDA.items():
            sezione = self._ask_question(context, code, titolo, domanda)
            sezioni[code] = sezione

            # Estrai pilastri dalla A3
            if code == "A3" and "Pilastri" in sezione.risposta:
                # Parsing semplificato
                for line in sezione.risposta.split("\n"):
                    if line.strip().startswith("-") or line.strip().startswith("*"):
                        pilastri.append(line.strip("- *").strip())

        return DNACompany(
            nome_azienda=nome_azienda,
            sezioni=sezioni,
            pilastri_trasversali=pilastri,
            famiglie_riferite=famiglie_riferite,
        )
