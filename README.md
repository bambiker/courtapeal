# courtapeal: ערעורים על בתי משפט מחוזיים בבית המשפט העליון

הכלי בונה טבלה (CSV) של פסקי הדין של בית המשפט העליון שהם ערעור על פסק דין של בית משפט מחוזי, לצורך סטטיסטיקה של קבלת ערעורים. בשלב הראשון: שנת 2025.

לכל תיק נרשמים: מספר התיק, סוג ההליך, תאריך, תוצאה (התקבל / התקבל חלקית / נדחה / נמחק / מעורב), הרכב העליון, בית המשפט המחוזי, מספר התיק ושופט/ים בערכאה קמא, ועורכי הדין של כל צד.

> ⚠️ **יש להריץ ממחשב בישראל.** אתרי הרשות השופטת לא עונים לכתובות מחו"ל.

## התקנה

```bash
python3 -m venv .venv
source .venv/bin/activate          # ב-Windows:  .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

## הרצה, צעד אחרי צעד

### 1. הקלטת ה-API של האתר (פעם אחת)

```bash
python -m courtapeal discover
```

ייפתח דפדפן. בצע בו חיפוש פסקי דין לטווח קצר (למשל יום אחד ב-2025) ופתח מסמך אחד מהתוצאות. אחר כך סגור את הדפדפן.
הסקריפט שומר את בקשת החיפוש ל-`config/search_template.json`.

אחרי ההקלטה, ערוך את הקבצים:
- ב-`config/search_template.json`: החלף את התאריכים שבגוף הבקשה ב-`{{FROM}}` וב-`{{TO}}`.
- ב-`config/settings.json`:
  - `date_format`: הפורמט של התאריך כפי שהאתר שלח אותו, למשל `%d/%m/%Y`.
  - `record_id_keys`: שדות ב-`sample_record` שמזהים מסמך באופן ייחודי.
  - `download_url_template`: כתובת הורדת המסמך, עם `{שם_שדה}` מתוך `sample_record`. את הכתובת האמיתית רואים ב-`data/discovery/` או בכלי המפתחים של הדפדפן כשמורידים מסמך.

(אם אתה עובד עם Claude Code, אפשר פשוט לבקש ממנו לעשות את זה. ראה `CLAUDE.md`.)

### 2. חיפוש והורדה

```bash
python -m courtapeal fetch --start 2025-01-01 --end 2025-01-07 --headed
```

מומלץ להתחיל בשבוע אחד כדי לוודא שהכל עובד, ורק אחר כך להריץ על כל השנה:

```bash
python -m courtapeal fetch --start 2025-01-01 --end 2025-12-31
```

ההתקדמות נשמרת ב-`data/court.db`. אם הריצה נעצרה, מריצים שוב את אותה פקודה והיא ממשיכה מאיפה שעצרה.

### 3. ניתוח וייצוא

```bash
python -m courtapeal parse
```
```bash
python -m courtapeal export --out output/appeals_2025.csv
```

הקובץ נפתח ישירות באקסל (קידוד UTF-8 עם BOM).
- `--final-only`: רק פסקי דין, בלי החלטות.
- `parse --no-leave`: בלי בקשות רשות ערעור (רע"א, רע"פ, בר"ם...).

### בדיקה על קובץ בודד

```bash
python -m courtapeal parse-file data/docs/<file>.pdf
```

## עמודות הטבלה

| עמודה | משמעות |
|---|---|
| `case_number` | מספר התיק בעליון (הראשון, אם התיקים אוחדו) |
| `case_type` / `is_leave` | סוג ההליך; `True` = בקשת רשות ערעור |
| `outcome` / `outcome_he` | accepted / partial / rejected / struck / mixed / unknown |
| `outcome_evidence` | המשפט שממנו נקבעה התוצאה, לבדיקה ידנית |
| `panel` | הרכב העליון |
| `lower_court`, `lower_case`, `lower_judges` | הערכאה קמא |
| `appellant_lawyers`, `respondent_lawyers` | עורכי הדין |

## מגבלות ידועות

- החילוץ מבוסס על ביטויים רגולריים. כדאי לבדוק ידנית מדגם לפני שמסיקים מסקנות.
- `mixed`: כמה ערעורים מאוחדים עם תוצאות שונות (למשל "ערעור המדינה מתקבל וערעור הנאשם נדחה").
- ערעור על עונש שהתקבל (הפחתת עונש) מסווג כ"התקבל" ולא כ"התקבל חלקית".
- שופט הערכאה קמא לא תמיד מוזכר בפסק הדין של העליון.
- קובצי `.doc` ישנים דורשים LibreOffice מותקן.

## בדיקות

```bash
pytest -q
```
