"""חילוץ שדות מטקסט של פסק דין של בית המשפט העליון.

המודול לא תלוי ברשת: הוא מקבל טקסט (כבר מחולץ מ-HTML/PDF/DOCX) ומחזיר dict.
החילוץ מבוסס על ביטויים רגולריים ועל המבנה הקבוע יחסית של פסקי הדין
(כותרת "לפני:", "בשם המערער:", פסקה אופרטיבית בסוף). הוא לא מושלם,
ולכן נשמר גם `outcome_evidence` - המשפט שעליו התבססה הקביעה - לבדיקה ידנית.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict

# --- סוגי הליכים -----------------------------------------------------------
# ערעור בזכות על פסק דין של בית משפט מחוזי
APPEAL_TYPES = {
    'ע"א': "ערעור אזרחי",
    'ע"פ': "ערעור פלילי",
    'עע"מ': "ערעור מנהלי",
    'עמ"ה': "ערעור מס הכנסה",  # נדיר בעליון, נשאר לשם שלמות
    'ע"מ': "ערעור מנהלי (ישן)",
    'עש"מ': "ערעור משמעת",
    'ע"ב': "ערעור בחירות",
}
# בקשות רשות ערעור - בדרך כלל על החלטה של מחוזי (לעתים מחוזי בשבתו כערכאת ערעור)
LEAVE_TYPES = {
    'רע"א': "רשות ערעור אזרחי",
    'רע"פ': "רשות ערעור פלילי",
    'בר"ם': "בקשת רשות ערעור מנהלי",
    'רע"ב': "רשות ערעור (אסירים)",
    'רע"מ': "רשות ערעור מנהלי",
    'בש"פ': "ערר פלילי (מעצרים)",
}
ALL_TYPES = {**APPEAL_TYPES, **LEAVE_TYPES}

# גרשיים יכולים להופיע כ-" או ״ או ' '
_QUOTES = '"״“”'


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    # הסרת סימני כיווניות
    text = re.sub("[‎‏‪-‮⁦-⁩]", "", text)
    for q in _QUOTES[1:]:
        text = text.replace(q, '"')
    text = text.replace("׳", "'").replace("’", "'")
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


_TYPE_ALT = "|".join(sorted((re.escape(t) for t in ALL_TYPES), key=len, reverse=True))
CASE_RE = re.compile(rf"(?<![֐-׿\"])({_TYPE_ALT})\s*(\d{{1,6}})\s*/\s*(\d{{2,4}})")


def find_case_numbers(text: str) -> list[str]:
    seen, out = set(), []
    for m in CASE_RE.finditer(text[:3000]):  # רק בכותרת - אחרת נתפוס אסמכתאות
        cn = f"{m.group(1)} {m.group(2)}/{m.group(3)}"
        if cn not in seen:
            seen.add(cn)
            out.append(cn)
    return out


# --- שופטים ---------------------------------------------------------------
_HONORIFIC = r"(?:כבוד|כב'|כב\.)\s*(?:ה)?(?:נשיא(?:ה)?|משנה\s+ל?נשיא(?:ה)?|ממלא(?:ת)?\s+מקום\s+ה?נשיא(?:ה)?|שופט(?:ת)?|רשם|רשמת)"


def _clean_name(s: str) -> str:
    s = re.sub(_HONORIFIC, "", s)
    s = re.sub(r"\b(?:ה)?(?:שופט(?:ת)?|נשיא(?:ה)?|משנה\s+ל?נשיא(?:ה)?|סגן\s+ה?נשיא(?:ה)?|סגנית\s+ה?נשיא(?:ה)?|עו\"ד|עוה\"ד|ד\"ר|פרופ'|בדימוס|\(בדימ'\)|\(בדימוס\))", "", s)
    s = re.sub(r"[\s,;:.()\-]+$", "", s)
    s = re.sub(r"^[\s,;:.()\-]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def find_panel(text: str) -> list[str]:
    """הרכב השופטים בעליון - מהבלוק שאחרי 'לפני:' ועד שם הצד הראשון."""
    m = re.search(r"לפני\s*:?\s*\n?(.*?)(?=\n\s*(?:ה?מערער|ה?מבקש|ה?עורר|ה?עותר|נ\s*ג\s*ד|ה?משיב)|\Z)", text[:4000], re.S)
    if not m:
        return []
    block = m.group(1)
    names = []
    for line in re.split(r"\n|(?=כבוד)|(?=כב')", block):
        line = line.strip()
        if not line or not re.search(r"כבוד|כב'|שופט|נשיא", line):
            continue
        name = _clean_name(line)
        if name and len(name) <= 40 and name not in names:
            names.append(name)
    return names


# --- עורכי דין -------------------------------------------------------------
def _split_lawyers(s: str) -> list[str]:
    s = re.sub(r"\s+", " ", s)
    parts = re.split(r";|,|\s+ו(?=עו\"ד)|\s+ו-?(?=עוה\"ד)", s)
    out = []
    for p in parts:
        p = re.sub(r"^(?:ו-?)?\s*(?:עו\"ד|עוה\"ד|עורכ(?:י|ות)\s+ה?דין|עורך\s+ה?דין|עורכת\s+ה?דין)\s*", "", p.strip())
        p = _clean_name(p)
        if p and len(p) <= 40 and not re.search(r"אין\s+הופעה|לא\s+התייצב|בעצמו|בעצמה", p):
            out.append(p)
    return out


def find_lawyers(text: str) -> dict[str, list[str]]:
    """מחזיר {'appellant': [...], 'respondent': [...]} מהשורות 'בשם המערער: ...'."""
    res = {"appellant": [], "respondent": []}
    for m in re.finditer(r"^\s*בשם\s+(ה?[֐-׿\"' ]{2,40}?)\s*:\s*(.+)$", text, re.M):
        who, names = m.group(1), m.group(2)
        if re.search(r"מערער|מבקש|עורר|עותר|נאשם", who):
            key = "appellant"
        elif re.search(r"משיב|מדינה|מאשימה", who):
            key = "respondent"
        else:
            continue
        for n in _split_lawyers(names):
            if n not in res[key]:
                res[key].append(n)
    return res


# --- ערכאה קמא ---------------------------------------------------------------
DISTRICTS = ["ירושלים", "תל אביב-יפו", "תל אביב", "חיפה", "באר שבע", "נצרת", "מרכז-לוד", "מרכז", "לוד"]
_DISTRICT_RE = re.compile(
    r"בית\s+ה?משפט\s+ה?מחוזי\s+(?:ב|ל)?-?\s*(" + "|".join(re.escape(d) for d in DISTRICTS) + r")"
)
# שם שופט: "ע' ברון" / "א' ב' כהן" / "ש' שרון-לוי" / או "יוסף כהן"
_JUDGE_NAME = (
    r"((?:[֐-׿]{1,3}'\s?)+[֐-׿][֐-׿\-]*"
    r"|[֐-׿]{2,}\s+[֐-׿][֐-׿\-]+)"
)
_LOWER_JUDGE_RE = re.compile(
    r"(?:על\s+ידי|ע\"י|מפי|\()\s*(?:(?:כבוד|כב'|כב\.)\s*)?"
    r"ה?(?:שופט(?:ת)?|נשיא(?:ה)?|סגן\s+ה?נשיא(?:ה)?|סגנית\s+ה?נשיא(?:ה)?)\s+" + _JUDGE_NAME
)


def find_lower_court(text: str) -> dict[str, str]:
    """בית המשפט המחוזי, מספר התיק שם והשופט/ים - מהמשפט 'ערעור על פסק דינו של...'."""
    head = text[:6000]
    out = {"lower_court": "", "lower_case": "", "lower_judges": ""}
    # מחפשים את המשפט שמתאר על מה הערעור; הוא לרוב ממש לפני "פסק-דין"
    anchor = re.search(r"(?:ערעור(?:ים)?|בקשת\s+רשות\s+ערעור|ערר)\s+על\s+.{0,600}?בית\s+ה?משפט\s+ה?מחוזי.{0,400}", head, re.S)
    seg = anchor.group(0) if anchor else head
    dm = _DISTRICT_RE.search(seg) or _DISTRICT_RE.search(head)
    if dm:
        d = dm.group(1)
        out["lower_court"] = {"תל אביב": "תל אביב-יפו", "מרכז": "מרכז-לוד", "לוד": "מרכז-לוד"}.get(d, d)
    # מספר תיק במחוזי (נט-המשפט): 45678-03-21; התחילית ב/ו היא אות שימוש
    cm = re.search(r"(?<![֐-׿\"])[בו]?-?((?:תפ\"ח|ת\"א|ת\"פ|ע\"פ|ע\"א|עת\"מ|עמ\"ה|ת\"צ|ה\"פ|עמ\"נ|תמ\"ש|רמ\"ש|ע\"ח|מ\"ת|בש\"א|ע\"מ|עפ\"א|עפ\"ג|עש\"א|רע\"צ)\s*-?\s*\d{1,6}-\d{2}-\d{2})", seg)
    if cm:
        out["lower_case"] = re.sub(r"\s+", " ", cm.group(1))
    judges = []
    for jm in _LOWER_JUDGE_RE.finditer(seg):
        n = _clean_name(jm.group(1))
        if n and n not in judges:
            judges.append(n)
    # הרכב בערכאה קמא: "השופטים א' ב', ג' ד' ו-ה' ו'"
    if not judges:
        pm = re.search(r"(?:על\s+ידי|ע\"י|מפי)\s+(?:כבוד\s+)?(?:ה)?שופטים\s+(.{3,120}?)(?:\)|\.|\n|$)", seg.replace("\n", " "))
        if pm:
            for n in re.split(r",|\s+ו-?(?=[֐-׿])", pm.group(1)):
                n = _clean_name(n)
                if n and len(n) <= 30 and n not in judges:
                    judges.append(n)
    out["lower_judges"] = "; ".join(judges)
    return out


# --- תוצאה -------------------------------------------------------------------
# סדר חשוב: "התקבל בחלקו" לפני "התקבל"
_OUTCOME_PATTERNS: list[tuple[str, str]] = [
    ("partial", r"(?:הערעור|הערר|הבקשה|ערעור\s+[֐-׿\"]+)\s+(?:מתקבל(?:ת)?|התקבל(?:ה)?|יתקבל|מתקבלים)\s+(?:בחלקו|בחלקה|באופן\s+חלקי|חלקית|במובן\s+זה|ב?חלק(?:ו|ה)?)"),
    ("partial", r"דין\s+ה(?:ערעור|ערר)\s+להתקבל\s+(?:בחלקו|באופן\s+חלקי|חלקית)"),
    ("partial", r"(?:מקבל(?:ים)?|לקבל)\s+את\s+ה(?:ערעור|ערר)\s+(?:בחלקו|באופן\s+חלקי|חלקית)"),
    ("accepted", r"(?:הערעור|הערר|ערעור\s+[֐-׿\"]+)\s+(?:מתקבל|התקבל|יתקבל)"),
    ("accepted", r"דין\s+ה(?:ערעור|ערר)\s+להתקבל"),
    ("accepted", r"(?:אנו\s+)?(?:מקבל(?:ים)?|לקבל)\s+את\s+ה(?:ערעור|ערר)"),
    ("accepted", r"(?:הבקשה|בקשת\s+רשות\s+הערעור)\s+מתקבלת"),
    ("rejected", r"(?:הערעור|הערר|ערעור\s+[֐-׿\"]+)\s+(?:נדחה|נדחים|יידחה|נדחית)"),
    ("rejected", r"דין\s+ה(?:ערעור|ערר|בקשה)\s+להידחות"),
    ("rejected", r"(?:אנו\s+)?(?:דוחים|דוחה|לדחות)\s+את\s+ה(?:ערעור|ערר|בקשה)"),
    ("rejected", r"(?:הבקשה|בקשת\s+רשות\s+הערעור)\s+נדחית"),
    ("struck", r"(?:הערעור|הערר|הבקשה)\s+(?:נמחק|נמחקת|יימחק|תימחק)"),
    ("struck", r"(?:על\s+)?מחיקת\s+ה(?:ערעור|ערר|בקשה)"),
]


def find_outcome(text: str) -> tuple[str, str]:
    """מחזיר (outcome, evidence). מחפש מהסוף להתחלה, כי ההכרעה בסוף פסק הדין.

    outcome: accepted / partial / rejected / struck / mixed / unknown
    mixed = נמצאו בפסקה האחרונה גם קבלה וגם דחייה (למשל שני ערעורים מאוחדים).
    """
    # הפסקה האופרטיבית: לרוב בתוך 3000 התווים האחרונים, לפני "ניתן היום"
    end = text
    gm = list(re.finditer(r"נית(?:ן|נה|נו)\s+(?:היום|ביום)", text))
    if gm:
        end = text[: gm[-1].start()]
    tail = end[-3000:]
    found: list[tuple[int, str, str]] = []
    for label, pat in _OUTCOME_PATTERNS:
        for m in re.finditer(pat, tail):
            # להתעלם מאזכור של הליך קודם / "אילו היה מתקבל" וכו'
            pre = tail[max(0, m.start() - 25): m.start()]
            if re.search(r"אילו|אם|לו\s+|ככל\s+ש|בטרם|בעבר", pre):
                continue
            found.append((m.start(), label, m.group(0)))
    if not found:
        return "unknown", ""
    found.sort()
    labels = {l for _, l, _ in found}
    last_pos, last_label, last_ev = found[-1]
    # "ערעור המדינה מתקבל וערעור הנאשם נדחה"
    if "rejected" in labels and ("accepted" in labels or "partial" in labels):
        # אם כל המופעים קרובים זה לזה בסוף - מדובר בהכרעה מעורבת
        positions = [p for p, l, _ in found if l in ("rejected", "accepted", "partial")]
        if max(positions) - min(positions) < 600:
            ev = " | ".join(e for _, _, e in found)
            return "mixed", ev
    if "partial" in labels and last_label == "accepted":
        last_label = "partial"
    return last_label, last_ev


# --- סוג מסמך --------------------------------------------------------------------
def doc_kind(text: str) -> str:
    head = text[:5000]
    if re.search(r"פ\s*ס\s*ק\s*-?\s*ה?\s*ד\s*י\s*ן", head):
        return "פסק דין"
    if re.search(r"ה\s*ח\s*ל\s*ט\s*ה", head):
        return "החלטה"
    return ""


_HEB_DATE_RE = re.compile(r"נית(?:ן|נה|נו)\s+(?:היום|ביום)[^\n]{0,60}?\(\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s*\)")


def find_date(text: str) -> str:
    m = list(_HEB_DATE_RE.finditer(text))
    if not m:
        return ""
    d, mo, y = m[-1].groups()
    y = int(y)
    if y < 100:
        y += 2000
    return f"{y:04d}-{int(mo):02d}-{int(d):02d}"


@dataclass
class Parsed:
    case_numbers: list[str] = field(default_factory=list)
    case_type: str = ""
    case_type_desc: str = ""
    is_leave: bool = False
    doc_kind: str = ""
    decision_date: str = ""
    panel: list[str] = field(default_factory=list)
    lower_court: str = ""
    lower_case: str = ""
    lower_judges: str = ""
    appellant_lawyers: list[str] = field(default_factory=list)
    respondent_lawyers: list[str] = field(default_factory=list)
    outcome: str = "unknown"
    outcome_evidence: str = ""

    def as_row(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, list):
                d[k] = "; ".join(v)
        return d


def parse_text(raw: str) -> Parsed:
    text = normalize(raw)
    p = Parsed()
    p.case_numbers = find_case_numbers(text)
    if p.case_numbers:
        t = p.case_numbers[0].split(" ")[0]
        p.case_type = t
        p.case_type_desc = ALL_TYPES.get(t, "")
        p.is_leave = t in LEAVE_TYPES
    p.doc_kind = doc_kind(text)
    p.decision_date = find_date(text)
    p.panel = find_panel(text)
    lc = find_lower_court(text)
    p.lower_court, p.lower_case, p.lower_judges = lc["lower_court"], lc["lower_case"], lc["lower_judges"]
    law = find_lawyers(text)
    p.appellant_lawyers, p.respondent_lawyers = law["appellant"], law["respondent"]
    p.outcome, p.outcome_evidence = find_outcome(text)
    return p


def is_appeal_from_district(p: Parsed, include_leave: bool = True) -> bool:
    if p.case_type in APPEAL_TYPES:
        # ע"א/ע"פ בעליון הם תמיד על מחוזי (או על בית דין מיוחד) - נדרוש שיימצא מחוזי
        return bool(p.lower_court) or p.case_type in ('ע"א', 'ע"פ', 'עע"מ')
    if include_leave and p.case_type in LEAVE_TYPES:
        return bool(p.lower_court)
    return False
