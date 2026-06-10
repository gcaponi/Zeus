"""Configurazione globale ZEUS via Pydantic Settings."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    """Configurazione LLM."""

    provider: Literal["kimi-coding", "openai", "anthropic"] = "kimi-coding"
    model: str = "kimi-k2.6"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=1)
    api_key: str = ""
    base_url: str = ""
    timeout: int = Field(default=120, ge=1)


class ParsingConfig(BaseSettings):
    """Configurazione parsing fonti tecniche."""

    pdf_engine: Literal["pymupdf"] = "pymupdf"
    image_engine: Literal["gemini-vision", "tesseract"] = "gemini-vision"
    ocr_enabled: bool = True
    ocr_language: str = "ita"


class PathsConfig(BaseSettings):
    """Configurazione path."""

    clients_root: Path = Path("D:/Zeus/clients")
    templates_dir: Path = Path("D:/Zeus/templates")
    output_dir: Path = Path("D:/Zeus/output")
    archive_dir: Path = Path("D:/Zeus/archive")

    @field_validator("clients_root", "templates_dir", "output_dir", "archive_dir", mode="before")
    @classmethod
    def expand_path(cls, v: str | Path) -> Path:
        return Path(v).expanduser().resolve()


class ValidationConfig(BaseSettings):
    """Configurazione validazione."""

    min_sections_family: int = 19
    min_sections_company: int = 20
    require_source_citations: bool = True
    require_cross_references: bool = True
    max_terminology_violations: int = 0


class ZeusConfig(BaseSettings):
    """Configurazione globale ZEUS.

    Legge da:
    1. Variabili d'ambiente (ZEUS_*)
    2. File config.yaml (se esiste)
    3. Default
    """

    model_config = SettingsConfigDict(
        env_prefix="ZEUS_",
        env_nested_delimiter="__",
    )

    llm: LLMConfig = Field(default_factory=LLMConfig)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """Aggiunge caricamento da YAML alle sorgenti di configurazione."""
        from pydantic_settings import YamlConfigSettingsSource

        yaml_config = Path("D:/Zeus/config.yaml")
        if yaml_config.exists():
            return (
                init_settings,
                env_settings,
                dotenv_settings,
                YamlConfigSettingsSource(settings_cls, yaml_file=yaml_config),
                file_secret_settings,
            )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    def client_path(self, client_name: str) -> Path:
        """Restituisce il path root di un cliente."""
        return self.paths.clients_root / client_name

    def client_output_path(self, client_name: str) -> Path:
        """Restituisce il path output di un cliente."""
        return self.client_path(client_name) / "output"

    def client_archive_path(self, client_name: str) -> Path:
        """Restituisce il path archive di un cliente."""
        return self.client_path(client_name) / "output" / "archive"


# Istanza globale lazy-loaded
_config: ZeusConfig | None = None


def get_config() -> ZeusConfig:
    """Restituisce l'istanza globale della configurazione."""
    global _config
    if _config is None:
        _config = ZeusConfig()
    return _config


def reload_config() -> ZeusConfig:
    """Ricarica la configurazione da file."""
    global _config
    _config = ZeusConfig()
    return _config
