"""Drop-off folder: statements dropped in inbox/ are identified, imported and archived.

A file is identified by its name first (patterns in config "filename_patterns"),
then the parser confirms it from the contents. Card names come from the last
four digits (config "cards"). After a successful import the file moves to
archive/<kind>/ with a standard name such as 2026-08_card_Discover-1234.pdf.
"""
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_DIR
from .parsers import detect

INBOX = PROJECT_DIR / "inbox"
ARCHIVE = PROJECT_DIR / "archive"
SUPPORTED = {".pdf", ".csv", ".tsv", ".txt", ".md"}


@dataclass
class FileInfo:
    path: Path
    kind: str           # "brb" | "bank" | "card"
    period: str = ""    # "2026-08" when the name says
    last4: str = ""
    account: str = ""   # friendly name, when known from the name
    matched_by: str = "contents"

    @property
    def label(self):
        bits = [self.kind]
        if self.account:
            bits.append(self.account)
        if self.period:
            bits.append(self.period)
        return " · ".join(bits)


def identify(path, cfg):
    """Work out what a statement is from its file name, falling back to its contents."""
    name = path.name
    for pattern, kind in cfg["filename_patterns"]:
        m = re.search(pattern, name, re.IGNORECASE)
        if not m:
            continue
        g = m.groupdict()
        info = FileInfo(path, kind, matched_by="file name")
        if g.get("y") and g.get("m"):
            info.period = f"{g['y']}-{g['m']}"
        if g.get("last4"):
            info.last4 = g["last4"]
            info.account = cfg["cards"].get(info.last4, "")
        elif kind == "bank":
            info.account = cfg.get("bank_name", "")
        return info
    return FileInfo(path, "brb" if detect(path) == "brb" else "bank")


def pending():
    INBOX.mkdir(exist_ok=True)
    return sorted(p for p in INBOX.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED)


def _slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")


def archive(info, period=None, account=None):
    """Move an imported file to archive/<kind>/ under a standard name."""
    period = period or info.period or "undated"
    account = account or info.account or info.path.stem
    dest_dir = ARCHIVE / info.kind
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{period}_{info.kind}_{_slug(account)}{info.path.suffix.lower()}"
    n = 2
    while dest.exists():
        dest = dest_dir / f"{period}_{info.kind}_{_slug(account)}_{n}{info.path.suffix.lower()}"
        n += 1
    shutil.move(str(info.path), dest)
    return dest
