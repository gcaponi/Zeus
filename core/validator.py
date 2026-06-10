"""Validatore per DNA e Knowledge Base.

Controlla:
- Completezza sezioni (minimo richiesto)
- Citazioni fonti (presenza riferimenti alle fonti tecniche)
- Terminologia (termini banditi non devono apparire)
- Cross-reference (DNA aziendale deve riferire famiglie)
- Struttura Markdown (header, sezioni, etc.)
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from zeus.config import get_config
from zeus.models.schemas import DNACompany, DNAFamily, KnowledgeBase


@dataclass
class ValidationIssue:
    """Singolo problema di validazione."""

    level: str  # "error", "warning", "info"
    code: str  # codice identificativo
    message: str
    source: str = ""  # nome file/sorgente


@dataclass
class ValidationReport:
    """Report completo di validazione."""

    source: str
    passed: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, level: str, code: str, message: str) -> None:
        """Aggiunge un issue al report."""
        self.issues.append(ValidationIssue(level, code, message, self.source))
        if level == "error":
            self.passed = False

    def summary(self) -> str:
        """Restituisce un riepilogo testuale."""
        counts = {"error": 0, "warning": 0, "info": 0}
        for issue in self.issues:
            counts[issue.level] = counts.get(issue.level, 0) + 1

        status = "PASS" if self.passed else "FAIL"
        return (
            f"[{status}] {self.source} | "
            f"errors={counts['error']} warnings={counts['warning']} info={counts['info']}"
        )


class DNAValidator:
    """Validatore per DNA Famiglia e DNA Aziendale."""

    def __init__(self) -> None:
        self.config = get_config().validation

    def validate_family(self, dna: DNAFamily) -> ValidationReport:
        """Valida un DNA Famiglia Prodotto.

        Args:
            dna: Il DNA famiglia da validare

        Returns:
            Report di validazione
        """
        report = ValidationReport(source=f"DNA_FAMIGLIA_{dna.nome}")

        # 1. Completezza sezioni
        if not dna.is_complete(self.config.min_sections_family):
            report.add(
                "error",
                "F001",
                f"Sezioni insufficienti: {len(dna.sezioni)}/{self.config.min_sections_family}",
            )

        # 2. Check D1-D20 presenti
        expected = [f"D{i}" for i in range(1, 21)]
        missing = [code for code in expected if code not in dna.sezioni]
        if missing:
            report.add("error", "F002", f"Sezioni mancanti: {', '.join(missing)}")

        # 3. Citazioni fonti
        if self.config.require_source_citations:
            for code, sezione in dna.sezioni.items():
                if not sezione.citazioni_fonti:
                    report.add(
                        "warning",
                        "F003",
                        f"Sezione {code} senza citazioni fonti",
                    )

        # 4. Terminologia bandita
        if dna.terminologia_bandita:
            for code, sezione in dna.sezioni.items():
                for term in dna.terminologia_bandita:
                    if re.search(rf"\b{re.escape(term)}\b", sezione.risposta, re.I):
                        report.add(
                            "error",
                            "F004",
                            f"Termine bandito '{term}' trovato in {code}",
                        )

        # 5. Check fonti esistono
        for f in dna.fonti.all_files():
            if not f.exists():
                report.add("error", "F005", f"File fonte mancante: {f}")

        return report

    def validate_company(self, dna: DNACompany) -> ValidationReport:
        """Valida un DNA Aziendale.

        Args:
            dna: Il DNA aziendale da validare

        Returns:
            Report di validazione
        """
        report = ValidationReport(source="DNA_AZIENDALE")

        # 1. Completezza sezioni
        if not dna.is_complete(self.config.min_sections_company):
            report.add(
                "error",
                "C001",
                f"Sezioni insufficienti: {len(dna.sezioni)}/{self.config.min_sections_company}",
            )

        # 2. Check A1-A20 presenti
        expected = [f"A{i}" for i in range(1, 21)]
        missing = [code for code in expected if code not in dna.sezioni]
        if missing:
            report.add("error", "C002", f"Sezioni mancanti: {', '.join(missing)}")

        # 3. Cross-reference famiglie
        if self.config.require_cross_references:
            if not dna.famiglie_riferite:
                report.add(
                    "warning",
                    "C003",
                    "Nessuna famiglia prodotto riferita nel DNA aziendale",
                )

        # 4. Pilastri trasversali
        if not dna.pilastri_trasversali:
            report.add("warning", "C004", "Nessun pilastro trasversale definito")

        return report

    def validate_kb(self, kb: KnowledgeBase) -> list[ValidationReport]:
        """Valida una Knowledge Base completa.

        Args:
            kb: La Knowledge Base da validare

        Returns:
            Lista di report per ogni componente
        """
        reports: list[ValidationReport] = []

        # Validazione strutturale KB
        errors = kb.validate()
        if errors:
            report = ValidationReport(source="INDEX_KNOWLEDGE_BASE", passed=False)
            for err in errors:
                report.add("error", "KB001", err)
            reports.append(report)
        else:
            reports.append(ValidationReport(source="INDEX_KNOWLEDGE_BASE", passed=True))

        # Validazione DNA aziendale
        if kb.dna_aziendale:
            reports.append(self.validate_company(kb.dna_aziendale))

        # Validazione DNA famiglie
        for famiglia in kb.dna_famiglie:
            reports.append(self.validate_family(famiglia))

        return reports


def validate_file(path: Path) -> ValidationReport:
    """Valida un file Markdown DNA esistente.

    Args:
        path: Path al file Markdown

    Returns:
        Report di validazione
    """
    report = ValidationReport(source=str(path))

    if not path.exists():
        report.add("error", "V001", f"File non trovato: {path}")
        return report

    content = path.read_text(encoding="utf-8")

    # Check header principale
    if not re.search(r"^# .+ — DNA", content, re.M):
        report.add("warning", "V002", "Header principale mancante o malformato")

    # Check sezioni
    sections = re.findall(r"^##? .+", content, re.M)
    if len(sections) < 10:
        report.add(
            "warning",
            "V003",
            f"Poche sezioni trovate: {len(sections)}",
        )

    return report
