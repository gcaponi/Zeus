"""CLI ZEUS — Entry point principale.

Comandi disponibili:
  init              Inizializza progetto cliente
  family            Gestione famiglie prodotto
  company           Gestione DNA aziendale
  validate          Validazione output
  assemble          Assemblaggio KB finale
  export            Esportazione per HERMES
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from zeus.config import get_config

app = typer.Typer(
    name="zeus",
    help="ZEUS — Meta-Framework Knowledge Engineering per agenti tecnici AI",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


# ─────────────────────────────────────────────────────────────
# Helper visivi
# ─────────────────────────────────────────────────────────────

def _header() -> None:
    """Stampa header ZEUS."""
    text = Text()
    text.append("[Z] ", style="bold yellow")
    text.append("ZEUS", style="bold cyan")
    text.append(" - Meta-Framework Knowledge Engineering\n", style="dim")
    text.append("v1.0.0 - Caponi AI Studio", style="dim italic")
    console.print(Panel(text, border_style="cyan"))


# ─────────────────────────────────────────────────────────────
# Comando: init
# ─────────────────────────────────────────────────────────────

@app.command()
def init(
    cliente: str = typer.Argument(..., help="Nome identificativo cliente"),
    force: bool = typer.Option(False, "--force", help="Sovrascrive se esiste"),
) -> None:
    """Inizializza la struttura progetto per un nuovo cliente."""
    _header()
    config = get_config()
    client_path = config.client_path(cliente)

    if client_path.exists() and not force:
        console.print(f"[red]Errore:[/red] il cliente '{cliente}' esiste già. Usa --force per sovrascrivere.")
        raise typer.Exit(1)

    # Crea struttura directory
    dirs = [
        client_path / "fonti",
        client_path / "risposte",
        client_path / "output" / "archive",
        client_path / "templates",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Scrivi config cliente base
    client_config = client_path / "config.yaml"
    client_config.write_text(
        f"""# Configurazione progetto ZEUS — {cliente}
cliente: {cliente}
created: auto
versione: 1.0.0

# Famiglie prodotto registrate
famiglie: []

# Stato workflow
stato:
  step1_dna_famiglie: false
  step2_dna_aziendale: false
  step3_kb_assemblata: false
""",
        encoding="utf-8",
    )

    console.print(f"[green][OK][/green] Cliente [bold cyan]{cliente}[/bold cyan] inizializzato.")
    console.print(f"  Path: [dim]{client_path}[/dim]")
    console.print("\n[yellow]Prossimi passi:[/yellow]")
    console.print(f'  1. [bold]zeus family add[/bold] <nome> --brochure <pdf> ...')
    console.print(f'  2. [bold]zeus family generate[/bold] <nome>')


# ─────────────────────────────────────────────────────────────
# Gruppo: family
# ─────────────────────────────────────────────────────────────

family_app = typer.Typer(help="Gestione famiglie prodotto")
app.add_typer(family_app, name="family")


@family_app.command("add")
def family_add(
    nome: str = typer.Argument(..., help="Nome identificativo famiglia"),
    brochure: Path = typer.Option(None, "--brochure", help="Path brochure tecnica (PDF)"),
    drawings: Path = typer.Option(None, "--drawings", help="Path directory disegni tecnici"),
    manual: Path = typer.Option(None, "--manual", help="Path manuale di montaggio (PDF)"),
    cliente: str = typer.Option(None, "--cliente", help="Nome cliente (default: cwd)"),
) -> None:
    """Registra una nuova famiglia prodotto con le sue fonti tecniche."""
    _header()
    config = get_config()
    client_name = cliente or "default"
    client_path = config.client_path(client_name)
    fonti_dir = client_path / "fonti" / nome
    fonti_dir.mkdir(parents=True, exist_ok=True)

    # Copia fonti nella struttura cliente
    if brochure and brochure.exists():
        dest = fonti_dir / f"brochure{brochure.suffix}"
        import shutil
        shutil.copy2(brochure, dest)
        console.print(f"  [green][OK][/green] Brochure: [dim]{dest}[/dim]")
    if manual and manual.exists():
        dest = fonti_dir / f"manuale{manual.suffix}"
        import shutil
        shutil.copy2(manual, dest)
        console.print(f"  [green][OK][/green] Manuale:  [dim]{dest}[/dim]")
    if drawings and drawings.exists() and drawings.is_dir():
        dest = fonti_dir / "disegni"
        dest.mkdir(exist_ok=True)
        for f in drawings.iterdir():
            if f.is_file():
                import shutil
                shutil.copy2(f, dest / f.name)
        console.print(f"  [green][OK][/green] Disegni:  [dim]{dest}[/dim]")

    console.print(f"\n[green][OK][/green] Famiglia [bold]{nome}[/bold] registrata per cliente [bold]{client_name}[/bold]")


@family_app.command("questions")
def family_questions(
    formato: str = typer.Option("pdf", "--format", help="Formato: pdf, markdown, yaml"),
    output: Path = typer.Option(None, "--output", help="Path output file"),
) -> None:
    """Esporta le 20 domande D1-D20 da inviare al cliente."""
    _header()

    if formato.lower() == "yaml":
        from zeus.core.questionnaire import export_family_questions
        out = export_family_questions(output)
    elif formato.lower() == "markdown":
        from zeus.core.exporters import export_family_markdown
        out = export_family_markdown(output)
    elif formato.lower() == "pdf":
        from zeus.core.exporters import export_family_pdf
        out = export_family_pdf(output)
    else:
        console.print(f"[red]Errore:[/red] formato '{formato}' non supportato. Usa: pdf, markdown, yaml")
        raise typer.Exit(1)

    console.print(f"[green][OK][/green] Domande D1-D20 esportate in: [bold]{out}[/bold]")
    console.print(f"  [dim]Invia questo file al cliente e attendi le risposte.[/dim]")


@family_app.command("generate")
def family_generate(
    nome: str = typer.Argument(..., help="Nome famiglia da generare"),
    cliente: str = typer.Option(None, "--cliente", help="Nome cliente"),
    from_answers: Path = typer.Option(None, "--from-answers", help="Path file YAML con risposte"),
    review: bool = typer.Option(True, "--review/--no-review", help="Attiva review interattiva"),
) -> None:
    """Genera il DNA Famiglia Prodotto (Step 1) da fonti o da risposte fornite."""
    _header()
    config = get_config()
    client_name = cliente or "default"
    output_dir = config.client_output_path(client_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    if from_answers:
        # Modalità batch: da risposte YAML
        from zeus.core.questionnaire import build_family_from_answers

        console.print(f"Genero DNA Famiglia [bold]{nome}[/bold] da risposte fornite...")
        dna = build_family_from_answers(
            nome=nome,
            nome_commerciale=nome,
            answers_path=from_answers,
        )
        md_path = output_dir / f"DNA_FAMIGLIA_{nome}.md"
        md_path.write_text(dna.to_markdown(), encoding="utf-8")
        console.print(f"[green][OK][/green] DNA salvato in: [bold]{md_path}[/bold]")
        console.print(f"  Sezioni: {len(dna.sezioni)}/20")
        return

    # Modalità LLM: da fonti tecniche
    console.print(f"[yellow][TODO][/yellow] Genera DNA Famiglia [bold]{nome}[/bold] da fonti tecniche")
    if review:
        console.print("  Review interattiva: [green]abilitata[/green]")


@family_app.command("review")
def family_review(
    nome: str = typer.Argument(..., help="Nome famiglia da revisionare"),
    cliente: str = typer.Option(None, "--cliente", help="Nome cliente"),
) -> None:
    """Revisione interattiva domanda-per-domanda del DNA Famiglia."""
    _header()
    console.print(f"[yellow][TODO][/yellow] Review DNA Famiglia [bold]{nome}[/bold]")


# ─────────────────────────────────────────────────────────────
# Gruppo: company
# ─────────────────────────────────────────────────────────────

company_app = typer.Typer(help="Gestione DNA aziendale")
app.add_typer(company_app, name="company")


@company_app.command("questions")
def company_questions(
    formato: str = typer.Option("pdf", "--format", help="Formato: pdf, markdown, yaml"),
    output: Path = typer.Option(None, "--output", help="Path output file"),
) -> None:
    """Esporta le 20 domande A1-A20 da inviare al cliente."""
    _header()

    if formato.lower() == "yaml":
        from zeus.core.questionnaire import export_company_questions
        out = export_company_questions(output)
    elif formato.lower() == "markdown":
        from zeus.core.exporters import export_company_markdown
        out = export_company_markdown(output)
    elif formato.lower() == "pdf":
        from zeus.core.exporters import export_company_pdf
        out = export_company_pdf(output)
    else:
        console.print(f"[red]Errore:[/red] formato '{formato}' non supportato. Usa: pdf, markdown, yaml")
        raise typer.Exit(1)

    console.print(f"[green][OK][/green] Domande A1-A20 esportate in: [bold]{out}[/bold]")
    console.print(f"  [dim]Invia questo file al cliente e attendi le risposte.[/dim]")


@company_app.command("interview")
def company_interview(
    cliente: str = typer.Option(None, "--cliente", help="Nome cliente"),
) -> None:
    """Pone interattivamente le 20 domande A1-A20 per il DNA Aziendale."""
    _header()
    console.print("[yellow][TODO][/yellow] Interview DNA Aziendale — domande A1-A20")


@company_app.command("generate")
def company_generate(
    cliente: str = typer.Option(None, "--cliente", help="Nome cliente"),
    from_answers: Path = typer.Option(None, "--from-answers", help="Path file YAML con risposte A1-A20"),
    risposte: Path = typer.Option(None, "--risposte", help="[deprecated] usa --from-answers"),
) -> None:
    """Genera il DNA Aziendale (Step 2) da risposte + DNA famiglie."""
    _header()
    config = get_config()
    client_name = cliente or "default"
    output_dir = config.client_output_path(client_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Backward compatibility
    answers_path = from_answers or risposte

    if answers_path:
        from zeus.core.questionnaire import build_company_from_answers

        console.print("Genero DNA Aziendale da risposte fornite...")
        # TODO: raccogliere nomi famiglie dal config cliente
        dna = build_company_from_answers(
            nome_azienda=client_name,
            answers_path=answers_path,
        )
        md_path = output_dir / "DNA_AZIENDALE.md"
        md_path.write_text(dna.to_markdown(), encoding="utf-8")
        console.print(f"[green][OK][/green] DNA Aziendale salvato in: [bold]{md_path}[/bold]")
        console.print(f"  Sezioni: {len(dna.sezioni)}/20")
        return

    console.print("[yellow][TODO][/yellow] Genera DNA Aziendale da LLM + famiglie")


# ─────────────────────────────────────────────────────────────
# Comando: validate
# ─────────────────────────────────────────────────────────────

@app.command()
def validate(
    cliente: str = typer.Option(None, "--cliente", help="Nome cliente"),
    family: str = typer.Option(None, "--family", help="Valida solo una famiglia"),
    strict: bool = typer.Option(False, "--strict", help="Validazione rigida (zero tolleranza)"),
) -> None:
    """Valida i DNA generati (struttura, sezioni, citazioni, terminologia)."""
    _header()
    console.print("[yellow][TODO][/yellow] Validazione output")
    if strict:
        console.print("  Modalità: [red]strict[/red]")


# ─────────────────────────────────────────────────────────────
# Comando: assemble
# ─────────────────────────────────────────────────────────────

@app.command()
def assemble(
    cliente: str = typer.Option(None, "--cliente", help="Nome cliente"),
    output: Path = typer.Option(None, "--output", help="Directory output (default: cliente/output)"),
) -> None:
    """Assembla la Knowledge Base finale (Step 3)."""
    _header()
    console.print("[yellow][TODO][/yellow] Assemblaggio KB finale")


# ─────────────────────────────────────────────────────────────
# Comando: export
# ─────────────────────────────────────────────────────────────

@app.command()
def export_hermes(
    formato: str = typer.Argument("hermes", help="Formato export: hermes, markdown, json"),
    cliente: str = typer.Option(None, "--cliente", help="Nome cliente"),
    output: Path = typer.Option(None, "--output", help="Directory output export"),
) -> None:
    """Esporta la KB per HERMES o altri formati."""
    _header()
    console.print(f"[yellow][TODO][/yellow] Export formato [bold]{formato}[/bold]")


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

def main() -> None:
    app()


if __name__ == "__main__":
    main()
