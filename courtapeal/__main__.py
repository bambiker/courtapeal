"""שורת פקודה.

    python -m courtapeal discover                       # הקלטת ה-API של האתר (פעם אחת)
    python -m courtapeal fetch --start 2025-01-01 --end 2025-12-31
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

from . import fetch as F
from .extract import extract_text
from .parse import is_appeal_from_district, parse_text

COLUMNS = [
    "case_number", "case_type", "case_type_desc", "is_leave", "decision_date", "doc_kind",
    "outcome", "outcome_he", "panel", "lower_court", "lower_case", "lower_judges",
    "appellant_lawyers", "respondent_lawyers", "outcome_evidence", "source_file", "all_case_numbers",
]
OUTCOME_HE = {
    "accepted": "התקבל", "partial": "התקבל חלקית", "rejected": "נדחה",
    "struck": "נמחק", "mixed": "מעורב", "unknown": "לא זוהה",
}


def cmd_parse(args):
    con = F.db()
    rows = con.execute("SELECT id, doc_path FROM hits WHERE status IN ('downloaded','parsed')").fetchall()
    n_ok = n_err = 0
    for rid, path in rows:
        try:
            text = extract_text(F.ROOT / path)
            p = parse_text(text)
            row = p.as_row()
            row["source_file"] = path
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
            if args.final_only and r.get("doc_kind") != "פסק דין":
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

    d = sub.add_parser("discover", help="הקלטת בקשת החיפוש של האתר")
    d.add_argument("--seconds", type=int, default=600)

    f = sub.add_parser("fetch", help="חיפוש והורדה")
    f.add_argument("--start", default="2025-01-01")
    f.add_argument("--end", default="2025-12-31")
    f.add_argument("--headed", action="store_true", help="להציג את הדפדפן")
    f.add_argument("--no-download", action="store_true", help="רק חיפוש, בלי הורדת מסמכים")

    p = sub.add_parser("parse", help="ניתוח המסמכים שהורדו")
    p.add_argument("--no-leave", action="store_true", help="לא לכלול בקשות רשות ערעור")

    e = sub.add_parser("export", help="ייצוא CSV")
    e.add_argument("--out", default="output/appeals_2025.csv")
    e.add_argument("--all", action="store_true", help="לכלול גם מסמכים שאינם ערעור על מחוזי")
    e.add_argument("--final-only", action="store_true", help="רק פסקי דין (לא החלטות)")

    pf = sub.add_parser("parse-file", help="ניתוח קובץ בודד והדפסה")
    pf.add_argument("path")

    args = ap.parse_args(argv)
    if args.cmd == "discover":
        F.discover(args.seconds)
    elif args.cmd == "fetch":
        F.fetch(args.start, args.end, headed=args.headed, download=not args.no_download)
    elif args.cmd == "parse":
        cmd_parse(args)
    elif args.cmd == "export":
        cmd_export(args)
    elif args.cmd == "parse-file":
        cmd_parse_file(args)


if __name__ == "__main__":
    sys.exit(main())
