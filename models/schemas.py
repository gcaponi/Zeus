"""Schema dati Pydantic per ZEUS.

Definisce le entità core del meta-framework:
- FontiTecniche: input grezzi (PDF, disegni, manuali)
- DNAFamily: output Step 1 (DNA Famiglia Prodotto)
- DNACompany: output Step 2 (DNA Aziendale)
- KnowledgeBase: output Step 3 (KB assemblata)
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FontiTecniche(BaseModel):
    """Fonti tecniche vincolanti per una famiglia prodotto."""

    model_config = ConfigDict(extra="allow")

    brochure_path: Path | None = None
    drawings_dir: Path | None = None
    manual_path: Path | None = None
    attachments: list[Path] = Field(default_factory=list)

    def all_files(self) -> list[Path]:
        """Restituisce tutti i file fonte come lista flat."""
        files: list[Path] = []
        if self.brochure_path:
            files.append(self.brochure_path)
        if self.manual_path:
            files.append(self.manual_path)
        if self.drawings_dir and self.drawings_dir.exists():
            files.extend(sorted(self.drawings_dir.iterdir()))
        files.extend(self.attachments)
        return files

    def checksum_summary(self) -> str:
        """Restituisce un riepilogo delle fonti (nome + size)."""
        parts = []
        for f in self.all_files():
            size = f.stat().st_size if f.exists() else 0
            parts.append(f"{f.name}:{size}")
        return "|".join(parts)


class SezioneDNA(BaseModel):
    """Singola sezione di un DNA (domanda + risposta)."""

    codice: str  # es. "D1", "A1"
    titolo: str
    domanda: str
    risposta: str
    citazioni_fonti: list[str] = Field(default_factory=list)
    note_review: str = ""  # note durante la review umana


class DNAFamily(BaseModel):
    """DNA Famiglia Prodotto — output Step 1."""

    model_config = ConfigDict(extra="allow")

    nome: str
    nome_commerciale: str = ""
    versione: str = "1.0.0"
    created: datetime = Field(default_factory=datetime.utcnow)
    updated: datetime = Field(default_factory=datetime.utcnow)
    checksum_fonti: str = ""

    fonti: FontiTecniche = Field(default_factory=FontiTecniche)
    sezioni: dict[str, SezioneDNA] = Field(default_factory=dict)

    # Metadati strutturali
    terminologia_corretta: dict[str, str] = Field(default_factory=dict)
    terminologia_bandita: list[str] = Field(default_factory=list)
    pilastri_funzionali: list[str] = Field(default_factory=list)

    def get_section(self, code: str) -> SezioneDNA | None:
        """Restituisce una sezione per codice."""
        return self.sezioni.get(code)

    def section_codes(self) -> list[str]:
        """Restituisce tutti i codici sezione ordinati."""
        return sorted(self.sezioni.keys())

    def is_complete(self, min_sections: int = 19) -> bool:
        """Verifica che il DNA abbia almeno N sezioni."""
        return len(self.sezioni) >= min_sections

    def to_markdown(self) -> str:
        """Renderizza il DNA in Markdown (usa template Jinja2)."""
        from jinja2 import Environment, PackageLoader

        env = Environment(loader=PackageLoader("zeus", "models/templates"))
        template = env.get_template("dna_famiglia.md.j2")
        return template.render(dna=self)


class DNACompany(BaseModel):
    """DNA Aziendale — output Step 2."""

    model_config = ConfigDict(extra="allow")

    nome_azienda: str
    versione: str = "1.0.0"
    created: datetime = Field(default_factory=datetime.utcnow)
    updated: datetime = Field(default_factory=datetime.utcnow)

    sezioni: dict[str, SezioneDNA] = Field(default_factory=dict)
    pilastri_trasversali: list[str] = Field(default_factory=list)
    famiglie_riferite: list[str] = Field(default_factory=list)

    def get_section(self, code: str) -> SezioneDNA | None:
        return self.sezioni.get(code)

    def is_complete(self, min_sections: int = 20) -> bool:
        return len(self.sezioni) >= min_sections

    def to_markdown(self) -> str:
        from jinja2 import Environment, PackageLoader

        env = Environment(loader=PackageLoader("zeus", "models/templates"))
        template = env.get_template("dna_aziendale.md.j2")
        return template.render(dna=self)


class ComportamentoSistema(BaseModel):
    """Blocco 1 — Comportamento del Sistema (template istanziato)."""

    nome_agente: str
    nome_azienda: str
    versione: str = "1.0.0"
    contenuto: str = ""  # Markdown completo

    def to_markdown(self) -> str:
        return self.contenuto


class KnowledgeBase(BaseModel):
    """Knowledge Base assemblata — output Step 3."""

    model_config = ConfigDict(extra="allow")

    cliente: str
    versione: str = "1.0.0"
    created: datetime = Field(default_factory=datetime.utcnow)

    dna_aziendale: DNACompany | None = None
    dna_famiglie: list[DNAFamily] = Field(default_factory=list)
    comportamento_sistema: ComportamentoSistema | None = None

    # Indice navigabile
    indice: dict[str, str] = Field(default_factory=dict)

    def add_family(self, dna: DNAFamily) -> None:
        """Aggiunge un DNA famiglia alla KB."""
        self.dna_famiglie.append(dna)
        self.indice[f"DNA_FAMIGLIA_{dna.nome}.md"] = (
            f"DNA Famiglia Prodotto: {dna.nome_commerciale or dna.nome}"
        )

    def set_company(self, dna: DNACompany) -> None:
        """Imposta il DNA aziendale."""
        self.dna_aziendale = dna
        self.indice["DNA_AZIENDALE.md"] = f"DNA Aziendale: {dna.nome_azienda}"

    def set_behaviour(self, comp: ComportamentoSistema) -> None:
        """Imposta il comportamento sistema."""
        self.comportamento_sistema = comp
        self.indice["COMPORTAMENTO_SISTEMA.md"] = (
            f"Comportamento Sistema: {comp.nome_agente}"
        )

    def to_index_markdown(self) -> str:
        """Genera l'INDEX_KNOWLEDGE_BASE.md."""
        from jinja2 import Environment, PackageLoader

        env = Environment(loader=PackageLoader("zeus", "models/templates"))
        template = env.get_template("index_kb.md.j2")
        return template.render(kb=self)

    def validate(self) -> list[str]:
        """Validazione strutturale della KB. Restituisce lista errori."""
        errors: list[str] = []
        if self.dna_aziendale is None:
            errors.append("Manca DNA_AZIENDALE")
        elif not self.dna_aziendale.is_complete():
            errors.append(
                f"DNA_AZIENDALE incompleto: {len(self.dna_aziendale.sezioni)}/20 sezioni"
            )
        if not self.dna_famiglie:
            errors.append("Nessuna DNA_FAMIGLIA presente")
        for fam in self.dna_famiglie:
            if not fam.is_complete():
                errors.append(
                    f"DNA_FAMIGLIA_{fam.nome} incompleto: {len(fam.sezioni)}/19 sezioni"
                )
        if self.comportamento_sistema is None:
            errors.append("Manca COMPORTAMENTO_SISTEMA")
        return errors
