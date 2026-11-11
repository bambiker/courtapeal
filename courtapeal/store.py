"""נתיבים, מסד הנתונים (SQLite) ועזרים משותפים."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = DATA / "docs"
DB = DATA / "court.db"


def db() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS hits(
            id TEXT PRIMARY KEY, day TEXT, meta TEXT,
            doc_path TEXT, status TEXT DEFAULT 'new', error TEXT);
        CREATE TABLE IF NOT EXISTS parsed(id TEXT PRIMARY KEY, row TEXT);
        """
    )
    return con


def find_records(obj) -> list[dict]:
    """מוצא בתשובת JSON את הרשימה הגדולה ביותר של אובייקטים - אלה תוצאות החיפוש."""
    best: list[dict] = []

    def walk(o):
        nonlocal best
        if isinstance(o, list):
            if o and all(isinstance(x, dict) for x in o) and len(o) > len(best):
                best = o
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)

    walk(obj)
    return best


def doc_ext(body: bytes, ctype: str = "", url: str = "", disposition: str = "") -> str | None:
    """מחזיר סיומת אם התוכן הוא מסמך (פסק דין), אחרת None."""
    h = body[:8]
    if h.startswith(b"%PDF"):
        return ".pdf"
    if h.startswith(b"PK"):
        return ".docx"
    if h.startswith(b"{\\rtf"):
        return ".rtf"
    if h.startswith(b"\xd0\xcf\x11\xe0"):
        return ".doc"
    # HTML נשמר רק אם ברור שזה מסמך (כתובת הורדה / קובץ מצורף), לא סתם עמוד באתר
    looks_doc = "download" in url.lower() or "attachment" in disposition.lower()
    if looks_doc and ("html" in ctype or b"<html" in body[:3000].lower()) and len(body) > 3000:
        if re.search("[֐-׿]{3}".encode("utf-8"), body) or b"&#1" in body:
            return ".html"
    return None


_FILE_KEY_RE = re.compile(r"[0-9a-fA-F]{32}")


def file_keys(text: str) -> list[str]:
    """מזהי קבצים (32 תווי hex) שמופיעים בכתובת - כך מקשרים מסמך לשורת חיפוש."""
    return [m.lower() for m in _FILE_KEY_RE.findall(text)]


def safe_name(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s)[:150]
