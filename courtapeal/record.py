"""מצב הקלטה: אתה גולש באתר, הכלי שומר.

הכלי פותח דפדפן גלוי ולא שולח לאתר אף בקשה משלו. הוא רק שומר את מה
שהדפדפן כבר קיבל בזמן שאתה גולש:
- תוצאות חיפוש (JSON) -> נשמרות כמטא-דאטה: מספר תיק, צדדים, תאריך, סוג מסמך.
- כל פסק דין שאתה פותח או מוריד (PDF/DOCX/...) -> נשמר ל-data/docs ומקושר
  לשורת החיפוש המתאימה.
אחר כך: `parse` ו-`export` כרגיל.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from urllib.parse import urlparse

from .store import DATA, DOCS, ROOT, db, doc_ext, file_keys, find_records, safe_name

START_URL = "https://supremedecisions.court.gov.il/"
_IGNORE_HOSTS = ("gbqofs.io", "google", "doubleclick", "clarity.ms", "hotjar", "facebook")


class Recorder:
    """הלוגיקה בלי Playwright - כדי שאפשר יהיה לבדוק אותה."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.lock = threading.Lock()
        self.by_key: dict[str, str] = {}  # FileName -> hit id
        self.n_meta = self.n_docs = 0
        for rid, meta in con.execute("SELECT id, meta FROM hits WHERE meta IS NOT NULL"):
            self._index(rid, json.loads(meta))

    def _index(self, rid: str, rec: dict):
        for k in ("FileName", "DocName", "Path", "PathForWeb"):
            for key in file_keys(str(rec.get(k) or "")):
                self.by_key[key] = rid

    def on_search_results(self, records: list[dict]) -> int:
        new = 0
        with self.lock:
            for r in records:
                rid = str(r.get("Id") or hashlib.sha1(json.dumps(r, sort_keys=True).encode()).hexdigest()[:16])
                cur = self.con.execute(
                    "INSERT OR IGNORE INTO hits(id, meta) VALUES(?,?)", (rid, json.dumps(r, ensure_ascii=False))
                )
                new += cur.rowcount
                self._index(rid, r)
            self.con.commit()
            self.n_meta += new
        return new

    def on_document(self, url: str, body: bytes, ext: str) -> tuple[str, str]:
        """שומר מסמך; מחזיר (id, תיאור) לתצוגה."""
        with self.lock:
            rid = next((self.by_key[k] for k in file_keys(url) if k in self.by_key), None)
            if rid is None:
                rid = "url-" + hashlib.sha1(url.encode()).hexdigest()[:16]
                self.con.execute("INSERT OR IGNORE INTO hits(id, meta) VALUES(?, NULL)", (rid,))
            DOCS.mkdir(parents=True, exist_ok=True)
            fn = DOCS / (safe_name(rid) + ext)
            fn.write_bytes(body)
            self.con.execute(
                "UPDATE hits SET doc_path=?, status='downloaded', error=NULL WHERE id=?",
                (str(fn.relative_to(ROOT)), rid),
            )
            self.con.commit()
            self.n_docs += 1
            row = self.con.execute("SELECT meta FROM hits WHERE id=?", (rid,)).fetchone()
            meta = json.loads(row[0]) if row and row[0] else {}
            desc = " ".join(str(meta.get(k, "")) for k in ("CaseDesc", "Type", "VerdictsDtString")).strip()
            return rid, desc or "(לא קושר לתוצאת חיפוש)"


def record(start_url: str = START_URL):
    from playwright.sync_api import sync_playwright

    con = db()
    rec = Recorder(con)
    seen_urls: set[str] = set()
    DATA.mkdir(exist_ok=True)
    log = (DATA / "record.log").open("a", encoding="utf-8")

    def logline(msg: str):
        log.write(msg + "\n")
        log.flush()
    print(
        "\nמצב הקלטה. בדפדפן שנפתח:\n"
        "  1. חפש פסקי דין (למשל: סוג מסמך פסק דין, טווח תאריכים).\n"
        "  2. פתח את פסקי הדין אחד אחרי השני (פתיחה או הורדה - שניהם נקלטים).\n"
        "  3. כשתסיים - סגור את הדפדפן.\n"
        "הכלי לא שולח בקשות משלו - הוא רק שומר את מה שאתה פותח.\n"
    )

    # צופה ה-PDF של Chrome מגיש את הקובץ מכתובת chrome-extension:// פנימית;
    # זוכרים את כתובת ה-PDF האמיתית האחרונה כדי לקשר אליה
    last_pdf_url = {"url": None}

    def handle_doc(url: str, body: bytes, ctype: str = "", disp: str = ""):
        if url in seen_urls:
            return
        ext = doc_ext(body, ctype, url, disp)
        if not ext:
            return
        seen_urls.add(url)
        rid, desc = rec.on_document(url, body, ext)
        logline(f"SAVED {rid} {ext} {len(body)}B {url}")
        print(f"  ✓ נשמר מסמך #{rec.n_docs}: {desc}")

    def on_response(resp):
        req = resp.request
        host = urlparse(req.url).hostname or ""
        if any(h in host for h in _IGNORE_HOSTS) or req.resource_type in ("image", "stylesheet", "font", "script"):
            return
        ctype = resp.headers.get("content-type", "")
        disp = resp.headers.get("content-disposition", "")
        logline(f"{resp.status} {req.method} {req.resource_type} [{ctype}] [{disp}] {req.url}")
        url = req.url
        if url.startswith(("http://", "https://")) and "pdf" in ctype.lower():
            last_pdf_url["url"] = url
        if url.startswith("chrome-extension://"):
            if "pdf" not in ctype.lower() or not last_pdf_url["url"]:
                return
            url = last_pdf_url["url"]
            if url in seen_urls:
                return
        try:
            if "json" in ctype:
                recs = find_records(resp.json())
                if recs and any("CaseDesc" in r or "FileName" in r for r in recs[:3]):
                    new = rec.on_search_results(recs)
                    print(f"[חיפוש] {len(recs)} תוצאות ({new} חדשות)")
                return
            if req.resource_type in ("document", "xhr", "fetch", "other") and resp.ok:
                handle_doc(url, resp.body(), ctype, disp)
        except Exception as e:
            logline(f"  ERR {type(e).__name__}: {e}")
            # הורדות (attachment) לא תמיד חושפות body כאן - נתפסות ב-on_download
            pass

    def on_download(download):
        logline(f"DOWNLOAD {download.url} {download.suggested_filename}")
        try:
            path = download.path()
            if path:
                handle_doc(download.url, Path(path).read_bytes(), "", "attachment")
        except Exception as e:
            logline(f"  DOWNLOAD-ERR {e}")
            print(f"  ✗ לא הצלחתי לשמור הורדה: {e}")

    def wire_page(page):
        page.on("download", on_download)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        context.on("response", on_response)
        context.on("page", wire_page)
        page = context.new_page()
        wire_page(page)
        page.goto(start_url)
        try:
            # ממתינים עד שהמשתמש סוגר את כל החלונות
            while context.pages:
                context.pages[0].wait_for_event("close", timeout=0)
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass

    log.close()
    total = con.execute("SELECT COUNT(*) FROM hits WHERE status IN ('downloaded','parsed')").fetchone()[0]
    print(f"\nבסשן הזה נשמרו {rec.n_docs} מסמכים. סה\"כ מסמכים במאגר: {total}.")
    print("הבא בתור:  python -m courtapeal parse   ואז   python -m courtapeal export")
