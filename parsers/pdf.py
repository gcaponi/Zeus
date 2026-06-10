"""Parser PDF per estrazione testo e immagini da fonti tecniche.

Supporta:
- Estrazione testo da PDF nativi (PyMuPDF)
- Estrazione immagini incorporate
- OCR fallback per PDF scannerizzati (future)
"""

from pathlib import Path

import fitz  # PyMuPDF

from zeus.config import get_config


class PDFParser:
    """Parser per file PDF tecnici."""

    def __init__(self) -> None:
        self.config = get_config().parsing

    def extract_text(self, path: Path) -> str:
        """Estrae tutto il testo da un PDF.

        Args:
            path: Path al file PDF

        Returns:
            Testo estratto concatenato da tutte le pagine
        """
        if not path.exists():
            raise FileNotFoundError(f"PDF non trovato: {path}")

        doc = fitz.open(str(path))
        pages_text: list[str] = []

        for page_num, page in enumerate(doc, start=1):
            text = page.get_text()
            if text.strip():
                pages_text.append(f"\n--- Pagina {page_num} ---\n{text}")

        doc.close()
        return "\n".join(pages_text)

    def extract_images(self, path: Path, output_dir: Path | None = None) -> list[Path]:
        """Estrae le immagini incorporate in un PDF.

        Args:
            path: Path al file PDF
            output_dir: Directory dove salvare le immagini (default: stesso path del PDF)

        Returns:
            Lista dei path delle immagini estratte
        """
        if not path.exists():
            raise FileNotFoundError(f"PDF non trovato: {path}")

        if output_dir is None:
            output_dir = path.parent / f"{path.stem}_images"
        output_dir.mkdir(parents=True, exist_ok=True)

        doc = fitz.open(str(path))
        extracted: list[Path] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            images = page.get_images(full=True)

            for img_idx, img in enumerate(images, start=1):
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                ext = base_image["ext"]

                img_path = output_dir / f"page{page_num + 1}_img{img_idx}.{ext}"
                img_path.write_bytes(image_bytes)
                extracted.append(img_path)

        doc.close()
        return extracted

    def get_metadata(self, path: Path) -> dict[str, str | int]:
        """Restituisce i metadati del PDF.

        Args:
            path: Path al file PDF

        Returns:
            Dizionario con metadati (title, author, pages, etc.)
        """
        doc = fitz.open(str(path))
        meta = {
            "pages": len(doc),
            "title": doc.metadata.get("title", ""),
            "author": doc.metadata.get("author", ""),
            "subject": doc.metadata.get("subject", ""),
            "path": str(path),
            "size_bytes": path.stat().st_size,
        }
        doc.close()
        return meta


def parse_pdf(path: Path) -> tuple[str, list[Path]]:
    """Funzione di convenienza: estrae testo e immagini in un solo passaggio.

    Args:
        path: Path al file PDF

    Returns:
        Tuple (testo, immagini estratte)
    """
    parser = PDFParser()
    text = parser.extract_text(path)
    images = parser.extract_images(path)
    return text, images
