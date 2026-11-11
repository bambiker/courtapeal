"""בדיקות לחילוץ - על טקסטים סינתטיים במבנה של פסקי דין של העליון.

כשיש פסקי דין אמיתיים, כדאי להוסיף כאן דוגמאות אמיתיות (tests/samples/) עם התוצאה הצפויה.
"""
from courtapeal.parse import parse_text, is_appeal_from_district

CIVIL_PARTIAL = """
בבית המשפט העליון בשבתו כבית משפט לערעורים אזרחיים

ע"א 1234/24

לפני:
כבוד השופט נ' סולברג
כבוד השופטת ד' ברק-ארז
כבוד השופט א' שטיין

המערערת:
פלונית בע"מ

נ ג ד

המשיבים:
1. אלמוני
2. אלמונית

ערעור על פסק דינו של בית המשפט המחוזי בתל אביב-יפו בת"א 45678-03-21 מיום 12.2.2024 שניתן על ידי כבוד השופטת ע' ברון

תאריך הישיבה: ה' בטבת התשפ"ה (5.1.2025)

בשם המערערת: עו"ד ישראל ישראלי; עו"ד רונית כהן
בשם המשיבים: עו"ד משה לוי

פסק-דין

השופט נ' סולברג:
1. לפנינו ערעור על פסק דינו של בית המשפט המחוזי...
אילו היה הערעור מתקבל, היה מקום להשיב את הסכום.
...
סוף דבר: אציע לחבריי כי הערעור יתקבל בחלקו, במובן זה שסכום הפיצוי יופחת.

ניתן היום, ‏כ"ב בשבט התשפ"ה (‏20.2.2025).
"""

CRIMINAL_REJECTED = """
ע״פ 5555/24

לפני: כבוד הנשיא י' עמית
כבוד השופט ע' גרוסקופף
כבוד השופטת ג' כנפי-שטייניץ

המערער: פלוני

נ ג ד

המשיבה: מדינת ישראל

ערעור על גזר הדין של בית המשפט המחוזי בבאר שבע (כב' השופט א' חזק) בתפ"ח 1111-01-22 מיום 3.3.2024

בשם המערער: עו"ד אבי אברהמי
בשם המשיבה: עו"ד שירה ישראלי

פסק-דין
...
לאור כל האמור, דין הערעור להידחות.

ניתן היום, ‏ט' באדר התשפ"ה (‏9.3.2025).
"""

MIXED = """
ע"פ 7777/24
ע"פ 7788/24

לפני: כבוד השופט י' אלרון

המערער בע"פ 7777/24: פלוני
המשיבה: מדינת ישראל

ערעורים על פסק דינו של בית המשפט המחוזי בחיפה בת"פ 2222-02-23 שניתן על ידי כבוד השופט ש' שרון

בשם המערער: עו"ד דן דנון
בשם המשיבה: עו"ד רות רותם

פסק-דין
...
סוף דבר: ערעור המדינה מתקבל וערעור הנאשם נדחה.

ניתן היום (1.4.2025).
"""

ADMIN_STRUCK = """
עע"מ 3030/24

לפני: כבוד השופטת י' וילנר

המערערים: עמותת פלונית

נ ג ד

המשיבה: עיריית ירושלים

ערעור על פסק דינו של בית המשפט המחוזי בירושלים בשבתו כבית משפט לעניינים מנהליים בעת"מ 5050-05-23 שניתן על ידי כב' השופט א' דורות

בשם המערערים: עו"ד גל גלעדי
בשם המשיבה: עו"ד נועה נוי

פסק-דין
בהסכמת הצדדים, הערעור נמחק ללא צו להוצאות.

ניתן היום (15.5.2025).
"""


def test_civil_partial():
    p = parse_text(CIVIL_PARTIAL)
    assert p.case_numbers == ['ע"א 1234/24']
    assert p.case_type == 'ע"א'
    assert p.panel == ["נ' סולברג", "ד' ברק-ארז", "א' שטיין"]
    assert p.lower_court == "תל אביב-יפו"
    assert p.lower_case.startswith('ת"א 45678-03-21')
    assert p.lower_judges == "ע' ברון"
    assert p.appellant_lawyers == ["ישראל ישראלי", "רונית כהן"]
    assert p.respondent_lawyers == ["משה לוי"]
    assert p.outcome == "partial"
    assert p.decision_date == "2025-02-20"
    assert p.doc_kind == "פסק דין"
    assert is_appeal_from_district(p)


def test_criminal_rejected():
    p = parse_text(CRIMINAL_REJECTED)
    assert p.case_type == 'ע"פ'
    assert p.panel[0] == "י' עמית"
    assert len(p.panel) == 3
    assert p.lower_court == "באר שבע"
    assert p.lower_judges == "א' חזק"
    assert p.appellant_lawyers == ["אבי אברהמי"]
    assert p.respondent_lawyers == ["שירה ישראלי"]
    assert p.outcome == "rejected"
    assert p.decision_date == "2025-03-09"


def test_mixed():
    p = parse_text(MIXED)
    assert p.case_numbers == ['ע"פ 7777/24', 'ע"פ 7788/24']
    assert p.lower_court == "חיפה"
    assert p.lower_judges == "ש' שרון"
    assert p.outcome == "mixed"


def test_admin_struck():
    p = parse_text(ADMIN_STRUCK)
    assert p.case_type == 'עע"מ'
    assert p.lower_court == "ירושלים"
    assert p.lower_judges == "א' דורות"
    assert p.outcome == "struck"
    assert p.panel == ["י' וילנר"]


def test_case_number_hyphen_and_not_district():
    from courtapeal.parse import find_case_numbers
    assert find_case_numbers('ע"א 4067-24 ובע"פ 1234-05-22') == ['ע"א 4067/24']


def test_recorder_links_doc_to_search_result(tmp_path, monkeypatch):
    import json
    import sqlite3
    from courtapeal import record as R
    from courtapeal.store import doc_ext

    monkeypatch.setattr(R, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(R, "ROOT", tmp_path)
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE hits(id TEXT PRIMARY KEY, day TEXT, meta TEXT, doc_path TEXT,"
                      " status TEXT DEFAULT 'new', error TEXT);")
    rec = R.Recorder(con)
    fn = "d755773c94010000090037f6ac67c454"
    meta = {"Id": 813645, "CaseDesc": 'ע"א 4067-24', "Type": "פסק-דין", "FileName": fn,
            "Path": "NetVerdicts\\2025\\1\\6\\2024-0-4067-8-1"}
    assert rec.on_search_results([meta]) == 1
    assert rec.on_search_results([meta]) == 0  # בלי כפילויות

    url = f"https://supremedecisions.court.gov.il/Home/Download?path=x&fileName={fn}&type=2"
    body = b"PK\x03\x04" + b"0" * 100
    assert doc_ext(body, "", url) == ".docx"
    rid, desc = rec.on_document(url, body, ".docx")
    assert rid == "813645" and 'ע"א 4067-24' in desc
    status, path = con.execute("SELECT status, doc_path FROM hits WHERE id='813645'").fetchone()
    assert status == "downloaded" and (tmp_path / path).exists()

    # מסמך שלא קושר לחיפוש - נשמר בכל זאת
    rid2, _ = rec.on_document("https://x/Home/Download?id=1", b"%PDF-1.4 ...", ".pdf")
    assert rid2.startswith("url-")


def test_doc_ext_ignores_regular_pages():
    from courtapeal.store import doc_ext
    page = b"<html>" + "שלום עולם ".encode() * 1000
    assert doc_ext(page, "text/html", "https://supremedecisions.court.gov.il/") is None
    assert doc_ext(page, "text/html", "https://supremedecisions.court.gov.il/Home/Download?x=1") == ".html"


def test_recorder_links_html_page_by_post_body(tmp_path, monkeypatch):
    import sqlite3
    from courtapeal import record as R
    from courtapeal.store import doc_ext

    monkeypatch.setattr(R, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(R, "ROOT", tmp_path)
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE hits(id TEXT PRIMARY KEY, day TEXT, meta TEXT, doc_path TEXT,"
                      " status TEXT DEFAULT 'new', error TEXT);")
    rec = R.Recorder(con)
    rec.on_search_results([{"Id": 813645, "CaseDesc": 'ע"א 4067-24', "Type": "פסק-דין"}])
    url = "https://supremedecisions.court.gov.il/Home/GetHtmlPage"
    body = ("<div>" + "פסק דין " * 200 + "</div>").encode()
    assert doc_ext(body, "text/html; charset=utf-8", url) == ".html"
    rid, desc = rec.on_document(url, body, ".html", '{"id":813645,"lan":1}')
    assert rid == "813645" and 'ע"א 4067-24' in desc
