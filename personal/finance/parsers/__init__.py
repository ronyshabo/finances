"""Statement parsers. Each turns a file into records for the master workbook."""
import subprocess
from pathlib import Path


def read_text(path):
    """Text of a .txt/.md/.csv file, or of a PDF via poppler's pdftotext."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                             capture_output=True, text=True, check=True)
        return out.stdout
    return path.read_text(errors="replace")


def detect(path):
    """'brb' or 'bank', from the file's contents."""
    from . import brb
    try:
        text = read_text(path)
    except (OSError, subprocess.CalledProcessError):
        return "bank"
    return "brb" if brb.looks_like_brb(text) else "bank"
