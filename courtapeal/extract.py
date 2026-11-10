"""חילוץ טקסט מקבצי פסקי דין (HTML / PDF / DOCX / RTF / TXT)."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path


def _html(data: bytes) -> str:
    from bs4 import BeautifulSoup

    for enc in ("utf-8", "windows-1255"):
        try:
            s = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        s = data.decode("utf-8", "ignore")
    soup = BeautifulSoup(s, "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()
    for br in soup.find_all(["br", "p", "div", "tr", "li"]):
        br.append("\n")
    return soup.get_text()


def _pdf(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return re.sub(r"<[^>]+>", "", xml)


def _rtf(data: bytes) -> str:
    s = data.decode("latin-1")
    # \'xx -> בייט ב-cp1255
    s = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1255", "ignore"), s)
    s = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), s)
    s = re.sub(r"\\par[d]?\b", "\n", s)
    s = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", s)
    return re.sub(r"[{}]", "", s)


def _doc(path: Path) -> str:
    """Word ישן (.doc) - דורש LibreOffice מותקן (soffice בנתיב)."""
    import shutil
    import subprocess
    import tempfile

    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise ValueError(f"{path}: קובץ .doc ישן - התקן LibreOffice כדי לחלץ ממנו טקסט")
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([exe, "--headless", "--convert-to", "txt:Text (encoded):UTF8", "--outdir", td, str(path)],
                       check=True, capture_output=True, timeout=120)
        return next(Path(td).glob("*.txt")).read_text(encoding="utf-8", errors="ignore")


def extract_text(path: str | Path) -> str:
    path = Path(path)
    data = path.read_bytes()
    head = data[:8].lower()
    if head.startswith(b"%pdf"):
        return _pdf(path)
    if head.startswith(b"pk"):
        return _docx(path)
    if head.startswith(b"{\\rtf"):
        return _rtf(data)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return _doc(path)
    if b"<html" in data[:2000].lower() or b"<!doctype" in data[:200].lower() or path.suffix.lower() in (".html", ".htm"):
        return _html(data)
    for enc in ("utf-8", "windows-1255"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    raise ValueError(f"פורמט לא נתמך: {path} (אולי .doc ישן? המר ל-docx/pdf)")
