"""משיכת פסקי דין ממאגר בית המשפט העליון בעזרת דפדפן (Playwright).

שני שלבים:
1. `discover` - פותח דפדפן גלוי. אתה מבצע חיפוש אחד ידנית באתר; הסקריפט מקליט
   את בקשת החיפוש (URL + גוף) ואת התשובה, ושומר אותן ל-config/search_template.json.
   כך אנחנו לא צריכים לנחש את ה-API של האתר.
2. `fetch` - משתמש בתבנית שהוקלטה, מחליף את התאריכים ומריץ חיפוש לכל יום
   בטווח, ואז מוריד כל מסמך. הכל נשמר ב-SQLite כדי שאפשר יהיה לחדש מאותה נקודה.

הבקשות נשלחות מתוך הדפדפן עצמו (אותן עוגיות כמו גלישה רגילה) ובקצב איטי.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "settings.json"
TEMPLATE = ROOT / "config" / "search_template.json"
DATA = ROOT / "data"
DOCS = DATA / "docs"
DB = DATA / "court.db"


def load_settings() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- DB
def db() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY, n INTEGER, fetched_at TEXT);
        CREATE TABLE IF NOT EXISTS hits(
            id TEXT PRIMARY KEY, day TEXT, meta TEXT,
            doc_path TEXT, status TEXT DEFAULT 'new', error TEXT);
        CREATE TABLE IF NOT EXISTS parsed(id TEXT PRIMARY KEY, row TEXT);
        """
    )
    return con


# ---------------------------------------------------------------- helpers
def _find_records(obj) -> list[dict]:
    """מוצא בתשובת ה-JSON את הרשימה הגדולה ביותר של אובייקטים - אלה התוצאות."""
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


def _fill(obj, mapping: dict[str, str]):
    if isinstance(obj, str):
        for k, v in mapping.items():
            obj = obj.replace(k, v)
        return obj
    if isinstance(obj, list):
        return [_fill(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: _fill(v, mapping) for k, v in obj.items()}
    return obj


def _record_id(rec: dict, keys: list[str]) -> str:
    parts = [str(rec.get(k, "")) for k in keys if rec.get(k)]
    return "|".join(parts) if parts else json.dumps(rec, ensure_ascii=False, sort_keys=True)[:300]


def _fmt_date(d: dt.date, fmt: str) -> str:
    return d.strftime(fmt)


# ---------------------------------------------------------------- discover
def discover(seconds: int = 600):
    """פותח דפדפן גלוי ומקליט את בקשות ה-XHR בזמן שאתה מחפש באתר."""
    from playwright.sync_api import sync_playwright

    s = load_settings()
    out_dir = DATA / "discovery"
    out_dir.mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []

    def on_response(resp):
        req = resp.request
        if req.resource_type not in ("xhr", "fetch") or req.method != "POST":
            return
        try:
            body = resp.json()
        except Exception:
            return
        item = {
            "url": req.url,
            "method": req.method,
            "headers": {k: v for k, v in req.headers.items() if k.lower() in ("content-type", "accept", "x-requested-with")},
            "post_data": req.post_data,
            "n_records": len(_find_records(body)),
            "sample_record": (_find_records(body) or [None])[0],
        }
        captured.append(item)
        (out_dir / f"{len(captured):02d}.json").write_text(
            json.dumps({**item, "response": body}, ensure_ascii=False, indent=1)[:2_000_000], encoding="utf-8"
        )
        print(f"[נקלט] {req.url}  ({item['n_records']} רשומות)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.on("response", on_response)
        page.goto(s["base_url"])
        print(
            "\nבדפדפן שנפתח: בצע חיפוש של פסקי דין בטווח תאריכים קצר (למשל יום אחד ב-2025),\n"
            "ופתח/הורד מסמך אחד מהתוצאות. כשתסיים - סגור את הדפדפן.\n"
        )
        try:
            page.wait_for_event("close", timeout=seconds * 1000)
        except Exception:
            pass
        browser.close()

    searches = [c for c in captured if c["n_records"] > 0 and c["post_data"]]
    if not searches:
        print("לא נקלטה בקשת חיפוש עם תוצאות. נסה שוב, או בדוק את data/discovery/.")
        return
    best = max(searches, key=lambda c: c["n_records"])
    try:
        body = json.loads(best["post_data"])
        body_is_json = True
    except Exception:
        body, body_is_json = best["post_data"], False
    tpl = {
        "url": best["url"],
        "headers": best["headers"],
        "body_is_json": body_is_json,
        "body": body,
        "sample_record": best["sample_record"],
        "_note": "החלף בגוף הבקשה את ערכי התאריכים ב-{{FROM}} ו-{{TO}} (בפורמט שב-settings.json: date_format).",
    }
    TEMPLATE.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nנשמרה תבנית: {TEMPLATE}")
    print("פתח אותה ושים {{FROM}} / {{TO}} במקום התאריכים שחיפשת. עדכן ב-settings.json את")
    print("record_id_keys ואת download_url_template לפי השדות של sample_record.")


# ---------------------------------------------------------------- fetch
def _search_day(page, tpl: dict, s: dict, day: dt.date) -> list[dict]:
    fmt = s["date_format"]
    mapping = {"{{FROM}}": _fmt_date(day, fmt), "{{TO}}": _fmt_date(day, fmt)}
    body = _fill(tpl["body"], mapping)
    headers = dict(tpl.get("headers") or {})
    if tpl.get("body_is_json", True):
        headers.setdefault("content-type", "application/json; charset=UTF-8")
        resp = page.request.post(tpl["url"], data=json.dumps(body, ensure_ascii=False), headers=headers, timeout=120_000)
    else:
        resp = page.request.post(tpl["url"], data=body, headers=headers, timeout=120_000)
    if not resp.ok:
        raise RuntimeError(f"חיפוש {day}: HTTP {resp.status}")
    return _find_records(resp.json())


def _download_url(rec: dict, s: dict) -> str | None:
    tmpl = s.get("download_url_template")
    if not tmpl:
        return None
    try:
        return tmpl.format(**{k: quote(str(v), safe="") for k, v in rec.items()})
    except KeyError:
        return None


def _keep(rec: dict, s: dict) -> bool:
    """סינון מוקדם לפי מטא-דאטה (לפני הורדה), כדי לא להוריד אלפי החלטות ביניים."""
    blob = json.dumps(rec, ensure_ascii=False)
    pref = s.get("case_type_prefixes") or []
    if pref and not any(p in blob for p in pref):
        return False
    if s.get("only_final_judgments"):
        kinds = s.get("final_judgment_markers") or ["פסק-דין", "פסק דין"]
        if not any(k in blob for k in kinds):
            # אם המטא-דאטה לא מציין סוג מסמך בכלל - לא מסננים
            if any(k in blob for k in ["החלטה", "החלטות"]):
                return False
    return True


def fetch(start: str, end: str, headed: bool = False, download: bool = True):
    from playwright.sync_api import sync_playwright

    if not TEMPLATE.exists():
        raise SystemExit("אין config/search_template.json - הרץ קודם: python -m courtapeal discover")
    tpl = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if "{{FROM}}" not in json.dumps(tpl["body"], ensure_ascii=False):
        raise SystemExit("בתבנית אין {{FROM}} / {{TO}} - ערוך את config/search_template.json")
    s = load_settings()
    con = db()
    DOCS.mkdir(parents=True, exist_ok=True)
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    delay = float(s.get("delay_seconds", 2.0))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        page = browser.new_page()
        page.goto(s["base_url"])
        time.sleep(delay)

        # --- שלב 1: חיפוש יום-יום
        day = d0
        while day <= d1:
            iso = day.isoformat()
            if con.execute("SELECT 1 FROM days WHERE day=?", (iso,)).fetchone():
                day += dt.timedelta(days=1)
                continue
            for attempt in range(4):
                try:
                    recs = _search_day(page, tpl, s, day)
                    break
                except Exception as e:
                    print(f"  שגיאה ({attempt + 1}/4): {e}")
                    time.sleep(delay * (2 ** (attempt + 1)))
            else:
                print(f"! מדלג על {iso}")
                day += dt.timedelta(days=1)
                continue
            kept = [r for r in recs if _keep(r, s)]
            cap = s.get("result_cap_warning")
            warn = "  ⚠ ייתכן שהתוצאות נחתכו" if cap and len(recs) >= cap else ""
            print(f"{iso}: {len(recs)} תוצאות, {len(kept)} רלוונטיות{warn}")
            for r in kept:
                rid = _record_id(r, s.get("record_id_keys", []))
                con.execute(
                    "INSERT OR IGNORE INTO hits(id, day, meta) VALUES(?,?,?)",
                    (rid, iso, json.dumps(r, ensure_ascii=False)),
                )
            con.execute("INSERT OR REPLACE INTO days VALUES(?,?,?)", (iso, len(kept), dt.datetime.now().isoformat()))
            con.commit()
            time.sleep(delay)
            day += dt.timedelta(days=1)

        # --- שלב 2: הורדת מסמכים
        if download:
            rows = con.execute("SELECT id, meta FROM hits WHERE status IN ('new','error')").fetchall()
            print(f"\nמוריד {len(rows)} מסמכים...")
            for i, (rid, meta) in enumerate(rows, 1):
                rec = json.loads(meta)
                url = _download_url(rec, s)
                if not url:
                    con.execute("UPDATE hits SET status='error', error=? WHERE id=?", ("no download url", rid))
                    continue
                try:
                    resp = page.request.get(url, timeout=120_000)
                    if not resp.ok:
                        raise RuntimeError(f"HTTP {resp.status}")
                    body = resp.body()
                    ext = _guess_ext(body, resp.headers.get("content-type", ""))
                    fn = DOCS / (re.sub(r"[^\w\-]+", "_", rid)[:150] + ext)
                    fn.write_bytes(body)
                    con.execute("UPDATE hits SET status='downloaded', doc_path=?, error=NULL WHERE id=?", (str(fn.relative_to(ROOT)), rid))
                except Exception as e:
                    con.execute("UPDATE hits SET status='error', error=? WHERE id=?", (str(e)[:300], rid))
                if i % 20 == 0:
                    con.commit()
                    print(f"  {i}/{len(rows)}")
                time.sleep(delay)
            con.commit()
        browser.close()


def _guess_ext(body: bytes, ctype: str) -> str:
    h = body[:8].lower()
    if h.startswith(b"%pdf"):
        return ".pdf"
    if h.startswith(b"pk"):
        return ".docx"
    if h.startswith(b"{\\rtf"):
        return ".rtf"
    if h.startswith(b"\xd0\xcf\x11\xe0"):
        return ".doc"
    if "html" in ctype or b"<html" in body[:2000].lower():
        return ".html"
    return ".txt"
