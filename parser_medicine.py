r"""
parser_medicine.py
-------------------
Parses "القبول المركزي" (central university admission) PDF-derived line
lists and extracts, per row, the university/college the student was
accepted into -- then aggregates how many students were accepted into
colleges of (human) Medicine, university by university.

This is deliberately built to be robust across several real-world exports
that differ in subtle ways (spacing, whether the score is glued directly
to the university name with no space, quota-channel suffixes like "ابناء
المحافظة", doubled-alef spelling of "الاسنان", etc.) -- these variations
were each found and handled by hand while auditing several actual PDFs:

  - Column separated by runs of 2+ spaces (typical of `pdftotext -layout`
    output, or of font_decoder output when a column gap is wide).
  - Score glued directly onto the university name with **no** separating
    space at all (seen in at least one real export), e.g.:
        "714.0جامعة بغداد/كلية الطب"
  - Two different admission channels for the same college reported as
    separate-looking strings ("جامعة واسط/كلية الطب" vs "جامعة واسط/كلية
    الطب؟ابناء المحافظة") -- these are the *same* college and are merged
    for reporting, while every individual student row is still counted.
  - Dentistry ("كلية طب الاسنان") and Veterinary Medicine ("كلية الطب
    البيطري") colleges must NOT be counted as Medicine -- both contain the
    word "طب" so a naive substring match would wrongly include them.

Every row is anchored on the presence of a long digit run (a 9-17 digit
exam number, رقم امتحاني) found via a *digit*-boundary match rather than
Python's regular `\b` word-boundary -- this distinction matters a lot in
practice: `\b` treats Arabic letters as "word" characters, so on files
where the exam number is glued directly onto the student's name with **no
separating space at all** (a real, fairly common layout, e.g.
"182511331150022حسين نديم فرحان دخيل"), a naive `\b\d{9,17}\b` regex
silently finds *no match at all* on that line -- which was found, by
hand, to make the whole row (and therefore the college it lists) vanish
from the count. `(?<!\d)\d{9,17}(?!\d)` only requires the characters
immediately before/after to not themselves be digits, so it still matches
correctly whether the ID sits next to a space, a letter, or the start/end
of the line.

Main entry points:
    parse_admission_rows(lines)      -> List[AdmissionRow]
    medicine_admission_counts(rows)  -> pandas.DataFrame
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional

from font_decoder import DecodedLine

EXAM_ID_RE = re.compile(r"(?<!\d)\d{9,17}(?!\d)")
SCORE_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)?")

BRANCH_WORDS = ("علمي", "احيائي", "أحيائي", "ادبي", "أدبي", "تطبيقي")

# Fallback extractor for lines where the score is glued directly onto the
# university name with no space at all, or where column gaps are single
# spaces (so a naive "split on 2+ spaces" finds nothing). Captures from
# "جامعة"/"الجامعة" through the college name, stopping right before the
# academic-branch word, the next run of digits, a double space, or EOL.
COLLEGE_FALLBACK_RE = re.compile(
    r"(?:جامعة|الجامعة)[^\d]{0,80}?كلية[^\d]{0,80}?"
    r"(?=\s(?:" + "|".join(BRANCH_WORDS) + r")\b|\s\d|\s{2,}|$)"
)


@dataclass
class AdmissionRow:
    page: int
    exam_id: str
    college_field: str  # raw, e.g. "جامعة بغداد/كلية الطب"


def _extract_college_field(line_text: str) -> Optional[str]:
    # Strategy 1: split on runs of 2+ spaces (handles the common case where
    # columns are separated by wide gaps).
    for part in re.split(r"\s{2,}", line_text.strip()):
        part = part.strip()
        if part.startswith("جامعة") or part.startswith("الجامعة"):
            if "كلية" in part:
                return SCORE_PREFIX_RE.sub("", part).strip()

    # Strategy 2: regex fallback for tightly-spaced / glued-digit text.
    m = COLLEGE_FALLBACK_RE.search(line_text)
    if m:
        return m.group(0).strip()

    return None


def parse_admission_rows(lines: List[DecodedLine]) -> List[AdmissionRow]:
    """Extract one AdmissionRow per genuine student row found in `lines`.

    A line is treated as a data row only if it contains BOTH a 9-17 digit
    exam number (matched with digit-boundaries, not word-boundaries -- see
    module docstring) and the word "جامعة" -- this reliably excludes page
    headers/footers/titles. Duplicate exam numbers are dropped (defensive
    guard against any accidental double-extraction of the same row).
    """
    rows: List[AdmissionRow] = []
    seen_exam_ids = set()
    for line in lines:
        text = line.text.strip()
        if not text or "جامعة" not in text:
            continue
        m = EXAM_ID_RE.search(text)
        if not m:
            continue
        exam_id = m.group(0)
        if exam_id in seen_exam_ids:
            continue
        college_field = _extract_college_field(text)
        if not college_field:
            continue
        seen_exam_ids.add(exam_id)
        rows.append(AdmissionRow(page=line.page, exam_id=exam_id, college_field=college_field))
    return rows


def _normalize_college_key(college_field: str) -> str:
    """Collapse local-quota channel suffixes (e.g. "...؟ابناء المحافظة")
    into the base university/college key so both channels of the same
    college are reported as one row (every student is still counted)."""
    college_field = re.sub(r"\s+", " ", college_field).strip()
    if "/" not in college_field:
        return college_field
    uni, college = college_field.split("/", 1)
    college_base = re.split(r"[؟\-]|ابناء|البناء", college)[0].strip()
    return f"{uni.strip()}/{college_base}"


def is_human_medicine(college_field: str) -> bool:
    """True only for actual human-medicine colleges ("كلية الطب" / "كلية
    طب <city>"), explicitly excluding Dentistry and Veterinary Medicine,
    which both also contain the word "طب"."""
    college_part = college_field.split("/")[-1].strip()
    # "سنان" alone covers "الاسنان" / "االسنان" (doubled-alef export
    # variant) / "اسنان" without worrying about alef-hamza spelling.
    if "سنان" in college_part:
        return False
    if "بيطري" in college_part:
        return False
    return bool(
        re.search(r"كلية\s+الطب\b", college_part)
        or re.search(r"كلية\s+طب\s", college_part)
        or college_part == "كلية طب"
    )


def medicine_admission_counts(rows: List[AdmissionRow]):
    """Returns a pandas DataFrame with one row per (normalized) Medicine
    college: ["الجامعة/الكلية", "عدد الطلبة المقبولين"], sorted descending,
    plus the grand total as the last "المجموع" row."""
    import pandas as pd

    counter: Counter = Counter()
    for r in rows:
        if is_human_medicine(r.college_field):
            counter[_normalize_college_key(r.college_field)] += 1

    items = sorted(counter.items(), key=lambda kv: -kv[1])
    df = pd.DataFrame(items, columns=["الجامعة / الكلية", "عدد الطلبة المقبولين"])
    if not df.empty:
        total_row = pd.DataFrame(
            [["المجموع الكلي", int(df["عدد الطلبة المقبولين"].sum())]],
            columns=df.columns,
        )
        df = pd.concat([df, total_row], ignore_index=True)
    return df
