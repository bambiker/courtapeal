"""משיכת פסקי דין ממאגר בית המשפט העליון בעזרת דפדפן (Playwright).

שני שלבים:
1. `discover` - פותח דפדפן גלוי ומקליט את הבקשות לאתר בזמן שאתה מחפש ופותח מסמך.
   כך מגלים את מבנה ה-API בלי לנחש. (תבנית החיפוש כבר נמצאת ב-config/search_template.json)
2. `fetch` - מריץ את בקשת החיפוש לכל יום בטווח, מסנן לפי סוג תיק וסוג מסמך,
   ומוריד כל מסמך. הכל נשמר ב-SQLite כדי שאפשר יהיה לחדש מאותה נקודה.

הבקשות נשלחות מתוך הדפדפן עצמו (אותן עוגיות כמו גלישה רגילה) ובקצב איטי.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "settings.json"
TEMPLATE = ROOT / "config" / "search_template.json"
DATA = ROOT / "data"
DOCS = DATA / "docs"
DB = DATA / "court.db"


def load_settings() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def save_settings(s: dict):
    CONFIG.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    parts = [str(rec.get(k, "")) for k in keys if rec.get(k) not in (None, "")]
    return "|".join(parts) if parts else json.dumps(rec, ensure_ascii=False, sort_keys=True)[:300]


def fmt_date(d: dt.date, fmt: str) -> str:
    """'jerusalem_midnight_utc' = חצות בשעון ישראל, מבוטא ב-UTC (כמו שהאתר שולח:
    2025-01-04T22:00:00.000Z עבור 5.1.2025). אחרת - פורמט strftime רגיל."""
    if fmt == "jerusalem_midnight_utc":
        from zoneinfo import ZoneInfo

        local = dt.datetime(d.year, d.month, d.day, tzinfo=ZoneInfo("Asia/Jerusalem"))
        return local.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return d.strftime(fmt)


# ---------------------------------------------------------------- discover
_IGNORE_HOSTS = ("gbqofs.io", "google", "doubleclick", "clarity.ms", "hotjar")


def discover(seconds: int = 600):
    """פותח דפדפן גלוי ומקליט את הבקשות לאתר (כולל הורדות מסמכים ולשוניות חדשות)."""
    from playwright.sync_api import sync_playwright

    s = load_settings()
    out_dir = DATA / "discovery"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = (out_dir / "requests.log").open("a", encoding="utf-8")
    captured: list[dict] = []

    def on_response(resp):
        req = resp.request
        host = urlparse(req.url).hostname or ""
        if any(h in host for h in _IGNORE_HOSTS) or req.resource_type in ("image", "stylesheet", "font", "script"):
            return
        ctype = resp.headers.get("content-type", "")
        disp = resp.headers.get("content-disposition", "")
        log.write(f"{resp.status} {req.method} {req.resource_type} [{ctype}] [{disp}] {req.url}\n")
        log.flush()
        if "download" in req.url.lower() or disp or any(x in ctype for x in ("pdf", "word", "officedocument", "octet")):
            print(f"[מסמך] {req.method} {req.url}  ({ctype})")
        if req.method != "POST":
            return
        try:
            body = resp.json()
        except Exception:
            return
        recs = _find_records(body)
        item = {
            "url": req.url,
            "method": req.method,
            "headers": {k: v for k, v in req.headers.items() if k.lower() in ("content-type", "accept", "x-requested-with")},
            "post_data": req.post_data,
            "n_records": len(recs),
            "sample_record": (recs or [None])[0],
        }
        captured.append(item)
        (out_dir / f"{len(captured):02d}.json").write_text(
            json.dumps({**item, "response": body}, ensure_ascii=False, indent=1)[:5_000_000], encoding="utf-8"
        )
        print(f"[נקלט] {req.url}  ({item['n_records']} רשומות)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        context.on("response", on_response)  # תופס גם לשוניות/חלונות חדשים
        page = context.new_page()
        page.goto(s["base_url"])
        print(
            "\nבדפדפן שנפתח: בצע חיפוש, ואז פתח/הורד מסמך אחד מהתוצאות.\n"
            "שורה שמתחילה ב-[מסמך] = נקלטה הורדה. כשתסיים - סגור את הדפדפן.\n"
        )
        try:
            page.wait_for_event("close", timeout=seconds * 1000)
        except Exception:
            pass
        browser.close()
    log.close()

    searches = [c for c in captured if c["n_records"] > 0 and c["post_data"]]
    if TEMPLATE.exists():
        print(f"\nהתבנית {TEMPLATE.name} כבר קיימת - לא נדרסה. ההקלטות ב-{out_dir}")
        return
    if not searches:
        print("לא נקלטה בקשת חיפוש עם תוצאות. בדוק את data/discovery/.")
        return
    best = max(searches, key=lambda c: c["n_records"])
    try:
        body, body_is_json = json.loads(best["post_data"]), True
    except Exception:
        body, body_is_json = best["post_data"], False
    tpl = {"url": best["url"], "headers": best["headers"], "body_is_json": body_is_json, "body": body,
           "sample_record": best["sample_record"]}
    TEMPLATE.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nנשמרה תבנית: {TEMPLATE} - החלף בה את התאריכים ב-{{{{FROM}}}} / {{{{TO}}}}")


# ---------------------------------------------------------------- search
def _search_day(page, tpl: dict, s: dict, day: dt.date) -> list[dict]:
    fmt = s["date_format"]
    to_day = day + dt.timedelta(days=int(s.get("to_offset_days", 0)))
    body = _fill(tpl["body"], {"{{FROM}}": fmt_date(day, fmt), "{{TO}}": fmt_date(to_day, fmt)})
    headers = dict(tpl.get("headers") or {})
    if tpl.get("body_is_json", True):
        headers.setdefault("content-type", "application/json;charset=UTF-8")
        resp = page.request.post(tpl["url"], data=json.dumps(body, ensure_ascii=False), headers=headers, timeout=180_000)
    else:
        resp = page.request.post(tpl["url"], data=body, headers=headers, timeout=180_000)
    if not resp.ok:
        raise RuntimeError(f"חיפוש {day}: HTTP {resp.status}")
    return _find_records(resp.json())


def case_type_of(rec: dict, s: dict) -> str:
    desc = str(rec.get(s.get("case_desc_field", "CaseDesc")) or "")
    m = re.match(r'\s*([֐-׿]+"[֐-׿])', desc.replace("״", '"'))
    return m.group(1) if m else ""


def _keep(rec: dict, s: dict) -> bool:
    """סינון לפי מטא-דאטה (לפני הורדה), כדי לא להוריד אלפי החלטות ביניים."""
    types = s.get("case_types") or []
    if types and case_type_of(rec, s) not in types:
        return False
    if s.get("only_final_judgments"):
        t = str(rec.get(s.get("doc_type_field", "Type")) or "")
        if not any(m in t for m in s.get("final_judgment_markers", ["פסק"])):
            return False
    return True


# ---------------------------------------------------------------- download
def _magic_ext(body: bytes, ctype: str = "") -> str | None:
    h = body[:8]
    if h.startswith(b"%PDF"):
        return ".pdf"
    if h.startswith(b"PK"):
        return ".docx"
    if h.startswith(b"{\\rtf"):
        return ".rtf"
    if h.startswith(b"\xd0\xcf\x11\xe0"):
        return ".doc"
    low = body[:3000].lower()
    if (b"<html" in low or "html" in ctype) and len(body) > 3000:
        # עמוד HTML של מסמך (ולא עמוד שגיאה/בית) - חייב להכיל עברית
        if re.search("[֐-׿]{3}".encode("utf-8"), body) or b"&#1" in body:
            return ".html"
    return None


def _candidate_urls(rec: dict, s: dict) -> list[str]:
    q = {k: quote(str(v), safe="") for k, v in rec.items() if v is not None}
    out = []
    for t in s.get("download_url_templates") or []:
        try:
            out.append(t.format(**q))
        except (KeyError, IndexError, ValueError):
            pass
    return out


def _download(page, rec: dict, s: dict) -> tuple[bytes, str, str]:
    """מנסה את התבניות לפי הסדר; תבנית שעבדה עוברת לראש הרשימה ונשמרת."""
    errors = []
    for i, url in enumerate(_candidate_urls(rec, s)):
        try:
            resp = page.request.get(url, timeout=120_000)
        except Exception as e:
            errors.append(f"{url}: {e}")
            continue
        body = resp.body() if resp.ok else b""
        ext = _magic_ext(body, resp.headers.get("content-type", "")) if body else None
        if ext:
            if i:  # לקדם את התבנית שעבדה
                tpls = s["download_url_templates"]
                tpls.insert(0, tpls.pop(i))
                save_settings(s)
                print(f"  ✓ תבנית הורדה שעובדת: {tpls[0]}")
            return body, ext, url
        errors.append(f"{url}: HTTP {resp.status} {resp.headers.get('content-type', '')} {len(body)}B")
    raise RuntimeError(" || ".join(errors)[:1000] or "אין תבניות הורדה")


# ---------------------------------------------------------------- fetch
def fetch(start: str, end: str, headed: bool = False, download: bool = True):
    from playwright.sync_api import sync_playwright

    if not TEMPLATE.exists():
        raise SystemExit("חסר config/search_template.json")
    tpl = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if "{{FROM}}" not in json.dumps(tpl["body"], ensure_ascii=False):
        raise SystemExit("בתבנית אין {{FROM}} / {{TO}} - ערוך את config/search_template.json")
    s = load_settings()
    con = db()
    DOCS.mkdir(parents=True, exist_ok=True)
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    delay = float(s.get("delay_seconds", 2.0))
    id_keys = s.get("record_id_keys", ["Id"])

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
            recs = None
            for attempt in range(4):
                try:
                    recs = _search_day(page, tpl, s, day)
                    break
                except Exception as e:
                    print(f"  שגיאה ({attempt + 1}/4): {e}")
                    time.sleep(delay * (2 ** (attempt + 1)))
            if recs is None:
                print(f"! מדלג על {iso} (ירוץ שוב בהרצה הבאה)")
                day += dt.timedelta(days=1)
                continue
            kept = [r for r in recs if _keep(r, s)]
            cap = s.get("result_cap_warning")
            warn = "  ⚠ ייתכן שהתוצאות נחתכו" if cap and len(recs) >= cap else ""
            print(f"{iso}: {len(recs)} מסמכים, {len(kept)} רלוונטיים{warn}")
            for r in kept:
                con.execute(
                    "INSERT OR IGNORE INTO hits(id, day, meta) VALUES(?,?,?)",
                    (_record_id(r, id_keys), iso, json.dumps(r, ensure_ascii=False)),
                )
            con.execute("INSERT OR REPLACE INTO days VALUES(?,?,?)", (iso, len(kept), dt.datetime.now().isoformat()))
            con.commit()
            time.sleep(delay)
            day += dt.timedelta(days=1)

        # --- שלב 2: הורדת מסמכים
        if download:
            rows = con.execute(
                "SELECT id, meta FROM hits WHERE status IN ('new','error') AND day BETWEEN ? AND ?", (start, end)
            ).fetchall()
            print(f"\nמוריד {len(rows)} מסמכים...")
            fails = 0
            for i, (rid, meta) in enumerate(rows, 1):
                rec = json.loads(meta)
                try:
                    body, ext, _ = _download(page, rec, s)
                    fn = DOCS / (re.sub(r"[^\w\-]+", "_", rid)[:150] + ext)
                    fn.write_bytes(body)
                    con.execute("UPDATE hits SET status='downloaded', doc_path=?, error=NULL WHERE id=?",
                                (str(fn.relative_to(ROOT)), rid))
                    fails = 0
                except Exception as e:
                    con.execute("UPDATE hits SET status='error', error=? WHERE id=?", (str(e)[:1000], rid))
                    fails += 1
                    if fails == 1 or fails % 10 == 0:
                        print(f"  ✗ {rec.get('CaseDesc', rid)}: {str(e)[:300]}")
                    if fails >= 5 and i == fails:
                        con.commit()
                        raise SystemExit(
                            "\nאף הורדה לא הצליחה - כתובת ההורדה לא ידועה עדיין.\n"
                            "הרץ `python -m courtapeal discover`, פתח מסמך אחד, ושלח את השורות [מסמך]\n"
                            "ואת data/discovery/requests.log."
                        )
                if i % 20 == 0:
                    con.commit()
                    print(f"  {i}/{len(rows)}")
                time.sleep(delay)
            con.commit()
        browser.close()
