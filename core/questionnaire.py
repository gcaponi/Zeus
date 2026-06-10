"""Gestione questionari D1-D20 e A1-A20.

Esporta le domande in formato inviabile al cliente.
Gestisce le risposte inserite manualmente o da file YAML.
"""

from pathlib import Path
from typing import Any

import yaml

from zeus.config import get_config
from zeus.core.dna_company import DOMANDE_AZIENDA
from zeus.core.dna_family import DOMANDE_FAMIGLIA


def export_family_questions(output_path: Path | None = None) -> Path:
    """Esporta le 20 domande D1-D20 in un file YAML inviabile al cliente.

    Args:
        output_path: Path output (default: ./domande_famiglia_prodotto.yaml)

    Returns:
        Path del file creato
    """
    if output_path is None:
        output_path = Path("domande_famiglia_prodotto.yaml")

    data: dict[str, Any] = {
        "meta": {
            "tipo": "DNA_FAMIGLIA_PRODOTTO",
            "domande": 20,
            "istruzioni": (
                "Per ogni domanda, rispondere nel modo piu completo possibile "
                "facendo riferimento a: brochure tecnica, disegni tecnici, manuale di montaggio. "
                "Se una informazione non e disponibile, scrivere 'Non disponibile nelle fonti'."
            ),
        },
        "domande": {},
    }

    for code, (titolo, testo) in DOMANDE_FAMIGLIA.items():
        data["domande"][code] = {
            "titolo": titolo,
            "domanda": testo,
            "risposta": "",
            "fonti_riferite": [],
        }

    output_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return output_path


def export_company_questions(output_path: Path | None = None) -> Path:
    """Esporta le 20 domande A1-A20 in un file YAML inviabile al cliente.

    Args:
        output_path: Path output (default: ./domande_dna_aziendale.yaml)

    Returns:
        Path del file creato
    """
    if output_path is None:
        output_path = Path("domande_dna_aziendale.yaml")

    data: dict[str, Any] = {
        "meta": {
            "tipo": "DNA_AZIENDALE",
            "domande": 20,
            "istruzioni": (
                "Rispondere riflettendo sull'intera gamma di prodotti dell'azienda. "
                "Ogni principio aziendale deve essere ancorato a esempi concreti di prodotti esistenti."
            ),
        },
        "domande": {},
    }

    for code, (titolo, testo) in DOMANDE_AZIENDA.items():
        data["domande"][code] = {
            "titolo": titolo,
            "domanda": testo,
            "risposta": "",
            "esempi_prodotti": [],
        }

    output_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return output_path


def load_answers(path: Path) -> dict[str, dict[str, Any]]:
    """Carica le risposte da un file YAML.

    Args:
        path: Path al file YAML con le risposte

    Returns:
        Dizionario {codice: {titolo, domanda, risposta, ...}}
    """
    if not path.exists():
        raise FileNotFoundError(f"File risposte non trovato: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("domande", {})


def build_family_from_answers(
    nome: str,
    nome_commerciale: str,
    answers_path: Path,
    fonti_path: Path | None = None,
) -> "DNAFamily":
    """Costruisce un DNAFamily da risposte fornite in YAML.

    Args:
        nome: Nome identificativo famiglia
        nome_commerciale: Nome commerciale
        answers_path: Path al file YAML con risposte D1-D20
        fonti_path: Path opzionale alle fonti tecniche

    Returns:
        DNAFamily popolato
    """
    from zeus.models.schemas import DNAFamily, FontiTecniche, SezioneDNA

    answers = load_answers(answers_path)
    sezioni: dict[str, SezioneDNA] = {}

    for code, data in answers.items():
        sezioni[code] = SezioneDNA(
            codice=code,
            titolo=data.get("titolo", ""),
            domanda=data.get("domanda", ""),
            risposta=data.get("risposta", ""),
            citazioni_fonti=data.get("fonti_riferite", []),
        )

    fonti = FontiTecniche()
    if fonti_path:
        # TODO: caricare fonti da directory
        pass

    return DNAFamily(
        nome=nome,
        nome_commerciale=nome_commerciale,
        fonti=fonti,
        sezioni=sezioni,
        checksum_fonti=fonti.checksum_summary(),
    )


def build_company_from_answers(
    nome_azienda: str,
    answers_path: Path,
    famiglie_nomi: list[str] | None = None,
) -> "DNACompany":
    """Costruisce un DNACompany da risposte fornite in YAML.

    Args:
        nome_azienda: Nome dell'azienda
        answers_path: Path al file YAML con risposte A1-A20
        famiglie_nomi: Lista nomi famiglie riferite

    Returns:
        DNACompany popolato
    """
    from zeus.models.schemas import DNACompany, SezioneDNA

    answers = load_answers(answers_path)
    sezioni: dict[str, SezioneDNA] = {}
    pilastri: list[str] = []

    for code, data in answers.items():
        sezioni[code] = SezioneDNA(
            codice=code,
            titolo=data.get("titolo", ""),
            domanda=data.get("domanda", ""),
            risposta=data.get("risposta", ""),
        )
        # Estrai pilastri dalla A3 se presente
        if code == "A3":
            for line in data.get("risposta", "").split("\n"):
                line = line.strip()
                if line.startswith(("- ", "* ")):
                    pilastri.append(line[2:].strip())

    return DNACompany(
        nome_azienda=nome_azienda,
        sezioni=sezioni,
        pilastri_trasversali=pilastri,
        famiglie_riferite=famiglie_nomi or [],
    )
