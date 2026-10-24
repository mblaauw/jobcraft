"""document conversion primitives."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _soffice() -> str | None:
    candidates = [
        os.environ.get("SOFFICE_BIN"),
        shutil.which("soffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def convert_pdf(docx_path: Path) -> Path | None:
    """LibreOffice when present, else the app image as a converter (dev only)."""
    soffice = _soffice()
    if soffice:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(docx_path.parent),
                str(docx_path),
            ],
            check=True,
            timeout=180,
            capture_output=True,
        )
        pdf = docx_path.with_suffix(".pdf")
        return pdf if pdf.exists() else None
    if shutil.which("docker"):
        image = os.environ.get("DOC_PDF_IMAGE", "jobcraft:local")
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{docx_path.parent}:/work",
                image,
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                "/work",
                f"/work/{docx_path.name}",
            ],
            check=True,
            timeout=300,
            capture_output=True,
        )
        pdf = docx_path.with_suffix(".pdf")
        return pdf if pdf.exists() else None
    return None
