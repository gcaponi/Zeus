"""Assemblatore Knowledge Base — Step 3 ZEUS.

Assembla i componenti finali:
- DNA_AZIENDALE.md
- DNA_FAMIGLIA_*.md (una per famiglia)
- COMPORTAMENTO_SISTEMA.md
- INDEX_KNOWLEDGE_BASE.md

Produce una Knowledge Base coerente e validata.
"""

from pathlib import Path

from jinja2 import Environment, PackageLoader

from zeus.config import get_config
from zeus.models.schemas import (
    ComportamentoSistema,
    DNACompany,
    DNAFamily,
    KnowledgeBase,
)


class KBAssembler:
    """Assemblatore Knowledge Base finale."""

    def __init__(self) -> None:
        self.config = get_config()
        self.env = Environment(loader=PackageLoader("zeus", "models/templates"))

    def assemble(
        self,
        cliente: str,
        dna_aziendale: DNACompany,
        dna_famiglie: list[DNAFamily],
        nome_agente: str,
    ) -> KnowledgeBase:
        """Assembla la Knowledge Base completa.

        Args:
            cliente: Nome identificativo cliente
            dna_aziendale: DNA Aziendale generato
            dna_famiglie: Lista DNA Famiglie Prodotto
            nome_agente: Nome dell'agente tecnico (es. "Tec.Andrea")

        Returns:
            KnowledgeBase assemblata e validata
        """
        kb = KnowledgeBase(cliente=cliente)

        # Assembla comportamento sistema
        comportamento = self._build_behaviour(nome_agente, dna_aziendale.nome_azienda)
        kb.set_behaviour(comportamento)

        # Assembla DNA aziendale
        kb.set_company(dna_aziendale)

        # Assembla DNA famiglie
        for famiglia in dna_famiglie:
            kb.add_family(famiglia)

        return kb

    def _build_behaviour(
        self,
        nome_agente: str,
        nome_azienda: str,
    ) -> ComportamentoSistema:
        """Costruisce il file COMPORTAMENTO_SISTEMA.md istanziando il template."""
        template = self.env.get_template("comportamento_sistema.md.j2")
        content = template.render(
            nome_agente=nome_agente,
            nome_azienda=nome_azienda,
            versione="1.0.0",
        )
        return ComportamentoSistema(
            nome_agente=nome_agente,
            nome_azienda=nome_azienda,
            contenuto=content,
        )

    def write_kb(self, kb: KnowledgeBase, output_dir: Path | None = None) -> list[Path]:
        """Scrive tutti i file della Knowledge Base su disco.

        Args:
            kb: KnowledgeBase da scrivere
            output_dir: Directory output (default: config.paths.output_dir / cliente)

        Returns:
            Lista dei file scritti
        """
        if output_dir is None:
            output_dir = self.config.client_output_path(kb.cliente)
        output_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []

        # Scrive DNA Aziendale
        if kb.dna_aziendale:
            path = output_dir / "DNA_AZIENDALE.md"
            path.write_text(kb.dna_aziendale.to_markdown(), encoding="utf-8")
            written.append(path)

        # Scrive DNA Famiglie
        for famiglia in kb.dna_famiglie:
            path = output_dir / f"DNA_FAMIGLIA_{famiglia.nome}.md"
            path.write_text(famiglia.to_markdown(), encoding="utf-8")
            written.append(path)

        # Scrive Comportamento Sistema
        if kb.comportamento_sistema:
            path = output_dir / "COMPORTAMENTO_SISTEMA.md"
            path.write_text(kb.comportamento_sistema.to_markdown(), encoding="utf-8")
            written.append(path)

        # Scrive Index
        path = output_dir / "INDEX_KNOWLEDGE_BASE.md"
        path.write_text(kb.to_index_markdown(), encoding="utf-8")
        written.append(path)

        return written

    def archive_current(self, cliente: str) -> Path | None:
        """Archivia la versione precedente della KB prima di sovrascrivere.

        Args:
            cliente: Nome cliente

        Returns:
            Path della directory archive, o None se non c'era nulla da archiviare
        """
        output_dir = self.config.client_output_path(cliente)
        if not output_dir.exists():
            return None

        archive_dir = self.config.client_archive_path(cliente)
        archive_dir.mkdir(parents=True, exist_ok=True)

        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = archive_dir / f"kb_{timestamp}"
        dest.mkdir(exist_ok=True)

        for f in output_dir.iterdir():
            if f.is_file():
                import shutil

                shutil.copy2(f, dest / f.name)

        return dest
