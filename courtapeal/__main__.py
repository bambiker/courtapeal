"""שורת פקודה.

    python -m courtapeal record                         # גלישה באתר; פסקי הדין שתפתח נשמרים
    python -m courtapeal parse                          # חילוץ שדות מכל המסמכים שהורדו
    python -m courtapeal export --out output/appeals_2025.csv
    python -m courtapeal parse-file path/to/decision.pdf   # בדיקה על קובץ בודד
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from . import store as F
from .extract import extract_text
from .parse import is_appeal_from_district, parse_text

COLUMNS = [
    "case_number", "case_name", "doc_id", "meta_doc_type", "case_type", "case_type_desc", "is_leave", "decision_date", "doc_kind",
    "outcome", "outcome_he", "panel", "lower_court", "lower_case", "lower_judges",
    "appellant_lawyers", "respondent_lawyers", "outcome_evidence", "source_file", "all_case_numbers",
]
OUTCOME_HE = {
    "accepted": "התקבל", "partial": "התקבל חלקית", "rejected": "נדחה",
    "struck": "נמחק", "mixed": "מעורב", "unknown": "לא זוהה",
}


def cmd_parse(args):
    con = F.db()
    rows = con.execute("SELECT id, doc_path, meta FROM hits WHERE status IN ('downloaded','parsed')").fetchall()
    n_ok = n_err = 0
    for rid, path, meta in rows:
        meta = json.loads(meta or "{}")
        try:
            text = extract_text(F.ROOT / path)
            p = parse_text(text)
            row = p.as_row()
            row["source_file"] = path
            # מטא-דאטה מהחיפוש - אמין יותר מהחילוץ מהטקסט
            desc = (meta.get("CaseDesc") or "").replace("-", "/")
            if desc:
                row["case_numbers"] = "; ".join(dict.fromkeys([desc] + [c for c in row["case_numbers"].split("; ") if c]))
                if not p.case_type:
                    p.case_type = row["case_type"] = desc.split(" ")[0]
            row["case_name"] = meta.get("CaseName", "")
            row["doc_id"] = meta.get("Id", "")
            row["meta_doc_type"] = meta.get("Type", "")
            if not row["decision_date"] and meta.get("VerdictsDtString"):
                d, m, y = meta["VerdictsDtString"].split("/")
                row["decision_date"] = f"{y}-{m}-{d}"
            row["_relevant"] = is_appeal_from_district(p, include_leave=not args.no_leave)
            con.execute("INSERT OR REPLACE INTO parsed VALUES(?,?)", (rid, json.dumps(row, ensure_ascii=False)))
            con.execute("UPDATE hits SET status='parsed' WHERE id=?", (rid,))
            n_ok += 1
        except Exception as e:
            con.execute("UPDATE hits SET error=? WHERE id=?", (f"parse: {e}"[:300], rid))
            n_err += 1
    con.commit()
    print(f"נותחו {n_ok} מסמכים, {n_err} שגיאות")


def cmd_export(args):
    con = F.db()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    with out.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig = נפתח נכון באקסל
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for (row_json,) in con.execute("SELECT row FROM parsed"):
            r = json.loads(row_json)
            if not args.all and not r.get("_relevant"):
                continue
            if args.final_only and "פסק" not in (r.get("meta_doc_type") or r.get("doc_kind") or ""):
                continue
            r["all_case_numbers"] = r.get("case_numbers", "")
            r["case_number"] = (r.get("case_numbers") or "").split("; ")[0]
            r["outcome_he"] = OUTCOME_HE.get(r.get("outcome"), "")
            w.writerow(r)
            stats[r["outcome_he"]] += 1
    total = sum(stats.values())
    print(f"נכתבו {total} שורות ל-{out}")
    for k, v in stats.most_common():
        print(f"  {k}: {v} ({v / total:.0%})" if total else "")


def cmd_parse_file(args):
    p = parse_text(extract_text(args.path))
    print(json.dumps(p.as_row(), ensure_ascii=False, indent=2))
    print("רלוונטי (ערעור על מחוזי):", is_appeal_from_district(p))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="courtapeal")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="מצב הקלטה: גולשים באתר והמסמכים נשמרים")
    r.add_argument("--url", default=None, help="כתובת פתיחה (ברירת מחדל: מאגר פסקי הדין של העליון)")

    p = sub.add_parser("parse", help="ניתוח המסמכים שהורדו")
    p.add_argument("--no-leave", action="store_true", help="לא לכלול בקשות רשות ערעור")

    e = sub.add_parser("export", help="ייצוא CSV")
    e.add_argument("--out", default="output/appeals_2025.csv")
    e.add_argument("--all", action="store_true", help="לכלול גם מסמכים שאינם ערעור על מחוזי")
    e.add_argument("--final-only", action="store_true", help="רק פסקי דין (לא החלטות)")

    pf = sub.add_parser("parse-file", help="ניתוח קובץ בודד והדפסה")
    pf.add_argument("path")

    args = ap.parse_args(argv)
    if args.cmd == "record":
        from .record import START_URL, record
        record(args.url or START_URL)
    elif args.cmd == "parse":
        cmd_parse(args)
    elif args.cmd == "export":
        cmd_export(args)
    elif args.cmd == "parse-file":
        cmd_parse_file(args)


if __name__ == "__main__":
    sys.exit(main())
