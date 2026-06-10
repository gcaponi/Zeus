"""Esportatori per questionari in formati leggibili dal cliente.

Supporta:
- Markdown (.md) — leggibile, stampabile, convertibile
- PDF (.pdf) — formato professionale da inviare via email
- YAML (.yaml) — formato strutturato per import automatico
"""

from pathlib import Path
from typing import Literal

from fpdf import FPDF

from zeus.core.dna_company import DOMANDE_AZIENDA
from zeus.core.dna_family import DOMANDE_FAMIGLIA


class QuestionnairePDF(FPDF):
    """PDF personalizzato per questionari ZEUS."""

    def header(self) -> None:
        self.set_font("helvetica", "B", 12)
        self.cell(0, 10, "ZEUS - Meta-Framework Knowledge Engineering", border=0, align="L")
        self.ln(6)
        self.set_font("helvetica", "", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, "Caponi AI Studio - Questionario per Generazione DNA", border=0, align="L")
        self.ln(10)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(8)
        self.set_text_color(0, 0, 0)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Pagina {self.page_no()}", align="C")

    def chapter_title(self, code: str, title: str) -> None:
        self.set_font("helvetica", "B", 11)
        self.set_text_color(0, 80, 120)
        self.cell(0, 8, f"{code} - {title}", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def chapter_body(self, body: str) -> None:
        self.set_font("helvetica", "", 10)
        self.multi_cell(0, 6, body)
        self.ln(4)

    def answer_box(self, lines: int = 6) -> None:
        """Disegna una casella per la risposta."""
        y = self.get_y()
        self.set_draw_color(180, 180, 180)
        self.set_fill_color(250, 250, 250)
        box_height = lines * 8
        self.rect(10, y, 190, box_height, style="DF")
        self.set_y(y + box_height + 4)


def export_family_markdown(output_path: Path | None = None) -> Path:
    """Esporta D1-D20 in Markdown leggibile.

    Args:
        output_path: Path output (default: ./domande_famiglia_prodotto.md)

    Returns:
        Path del file creato
    """
    if output_path is None:
        output_path = Path("domande_famiglia_prodotto.md")

    lines: list[str] = [
        "# ZEUS - Questionario DNA Famiglia Prodotto",
        "",
        "**Istruzioni:** Rispondere nel modo piu completo possibile, facendo riferimento a: "
        "brochure tecnica, disegni tecnici, manuale di montaggio. "
        "Se una informazione non e disponibile, indicare 'Non disponibile nelle fonti'.",
        "",
        "---",
        "",
    ]

    for code, (titolo, testo) in DOMANDE_FAMIGLIA.items():
        lines.append(f"## {code} - {titolo}")
        lines.append("")
        lines.append(f"**Domanda:** {testo}")
        lines.append("")
        lines.append("**Risposta:**")
        lines.append("")
        lines.append("_" * 80)
        lines.append("")
        lines.append("**Fonti tecniche citate:**")
        lines.append("")
        lines.append("_" * 40)
        lines.append("")
        lines.append("---")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def export_company_markdown(output_path: Path | None = None) -> Path:
    """Esporta A1-A20 in Markdown leggibile.

    Args:
        output_path: Path output (default: ./domande_dna_aziendale.md)

    Returns:
        Path del file creato
    """
    if output_path is None:
        output_path = Path("domande_dna_aziendale.md")

    lines: list[str] = [
        "# ZEUS - Questionario DNA Aziendale",
        "",
        "**Istruzioni:** Rispondere riflettendo sull'intera gamma di prodotti dell'azienda. "
        "Ogni principio aziendale deve essere ancorato a esempi concreti di prodotti esistenti.",
        "",
        "---",
        "",
    ]

    for code, (titolo, testo) in DOMANDE_AZIENDA.items():
        lines.append(f"## {code} - {titolo}")
        lines.append("")
        lines.append(f"**Domanda:** {testo}")
        lines.append("")
        lines.append("**Risposta:**")
        lines.append("")
        lines.append("_" * 80)
        lines.append("")
        lines.append("**Esempi di prodotti concreti:**")
        lines.append("")
        lines.append("_" * 40)
        lines.append("")
        lines.append("---")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def export_family_pdf(output_path: Path | None = None) -> Path:
    """Esporta D1-D20 in PDF professionale.

    Args:
        output_path: Path output (default: ./domande_famiglia_prodotto.pdf)

    Returns:
        Path del file creato
    """
    if output_path is None:
        output_path = Path("domande_famiglia_prodotto.pdf")

    pdf = QuestionnairePDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Titolo e istruzioni
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "Questionario DNA Famiglia Prodotto", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(80, 80, 80)
    pdf.multi_cell(
        0,
        6,
        "Istruzioni: Rispondere nel modo piu completo possibile, facendo riferimento a "
        "brochure tecnica, disegni tecnici, manuale di montaggio. "
        "Se una informazione non e disponibile, indicare 'Non disponibile nelle fonti'.",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)

    for code, (titolo, testo) in DOMANDE_FAMIGLIA.items():
        # Verifica spazio pagina
        if pdf.get_y() > 240:
            pdf.add_page()

        pdf.chapter_title(code, titolo)
        pdf.chapter_body(testo)
        pdf.set_font("helvetica", "B", 10)
        pdf.cell(0, 6, "Risposta (da inviare via e-mail):", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 10)
        pdf.answer_box(lines=2)

    pdf.output(str(output_path))
    return output_path


def export_company_pdf(output_path: Path | None = None) -> Path:
    """Esporta A1-A20 in PDF professionale.

    Args:
        output_path: Path output (default: ./domande_dna_aziendale.pdf)

    Returns:
        Path del file creato
    """
    if output_path is None:
        output_path = Path("domande_dna_aziendale.pdf")

    pdf = QuestionnairePDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "Questionario DNA Aziendale", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(80, 80, 80)
    pdf.multi_cell(
        0,
        6,
        "Istruzioni: Rispondere riflettendo sull'intera gamma di prodotti dell'azienda. "
        "Ogni principio aziendale deve essere ancorato a esempi concreti di prodotti esistenti.",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)

    for code, (titolo, testo) in DOMANDE_AZIENDA.items():
        if pdf.get_y() > 240:
            pdf.add_page()

        pdf.chapter_title(code, titolo)
        pdf.chapter_body(testo)
        pdf.set_font("helvetica", "B", 10)
        pdf.cell(0, 6, "Risposta (da inviare via e-mail):", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 10)
        pdf.answer_box(lines=2)

    pdf.output(str(output_path))
    return output_path
