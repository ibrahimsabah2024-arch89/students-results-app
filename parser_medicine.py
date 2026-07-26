"""
parser_medicine.py
-------------------
Parses "القبول المركزي" (central university admission) PDF-derived line
lists and extracts, per row, the university/college the student was
accepted into -- then aggregates how many students were accepted into
colleges of (human) Medicine, university by university.
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional

from font_decoder import DecodedLine

EXAM_ID_RE = re.compile(r"(?<!\d)\d{9,17}(?!\d)")
SCORE_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)?")

BRANCH_WORDS = ("علمي", "احيائي", "أحيائي", "ادبي", "أدبي", "تطبيقي")

COLLEGE_FALLBACK_RE = re.compile(
    r"(?:جامعة|الجامعة)[^\d]{0,80}?كلية[^\d]{0,80}?"
    r"(?=\s(?:" + "|".join(BRANCH_WORDS) + r")\b|\s\d|\s{2,}|$)"
)


@dataclass
class AdmissionRow:
    page: int
    exam_id: str
    college_field: str


def _extract_college_field(line_text: str) -> Optional[str]:
    for part in re.split(r"\s{2,}", line_text.strip()):
        part = part.strip()
        if part.startswith("جامعة") or part.startswith("الجامعة"):
            if "كلية" in part:
                return SCORE_PREFIX_RE.sub("", part).strip()

    m = COLLEGE_FALLBACK_RE.search(line_text)
    if m:
        return m.group(0).strip()

    return None


def parse_admission_rows(lines: List[DecodedLine]) -> List[AdmissionRow]:
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
    college_field = re.sub(r"\s+", " ", college_field).strip()
    if "/" not in college_field:
        return college_field
    uni, college = college_field.split("/", 1)
    college_base = re.split(r"[؟\-]|ابناء|البناء", college)[0].strip()
    return f"{uni.strip()}/{college_base}"


def is_human_medicine(college_field: str) -> bool:
    college_part = college_field.split("/")[-1].strip()
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