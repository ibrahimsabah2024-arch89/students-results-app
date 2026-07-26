"""
Parser for the "مركز فحص الدراسة / مشروع الدفتر الالكتروني" school exam
results PDF format -- a per-student row report with columns (in the
original RTL visual layout, right to left):
تسلسل | الرقم الامتحاني | اسم الطالب | <subject scores...> | النتيجة | المجموع | المعدل

Two quirks in the decoded text (both due to tiny/no horizontal gaps
between adjacent PDF text runs, same root cause class as the row-height
issue fixed in font_decoder.py, but horizontal instead of vertical) mean
we can't just split on whitespace naively:

1. The student's name is glued directly to the first subject score with
   no space, e.g. "...فليح حسن50 50 52 34 100 50 61  معيد0 0".
2. The نتيجة (ناجح/معيد) word is glued directly to the المعدل (average)
   value that follows it, e.g. "معيد0" or "ناجح66.57".

Rather than trying to parse every subject-score cell (column count
varies per student -- the "اللغات" column is often blank), we only
extract what's needed for participation / success-rate / grade-band
stats: the sequence number, exam number, name, pass/repeat result,
average, and total.
"""

import re
from dataclasses import dataclass

_HEAD_RE = re.compile(r"^(\d{10,})\s+(\d+)\s+([^\d%]+?)(?=[\d%])")
_TAIL_RE = re.compile(r"(ناجح|معيد|دور\s*ثان\S*)([\d.]+)\s+([\d.]+)\s*$")


@dataclass
class SchoolExamRow:
    seq: int
    exam_no: str
    name: str
    result: str  # "ناجح" or "معيد" (or similar, verbatim from the file)
    average: float
    total: float


def parse_school_exam_rows(lines):
    """Parse decoded PDF lines into SchoolExamRow records. Lines that
    don't match the expected shape (e.g. header/footer rows) are simply
    skipped -- this is safe because it's driven by strict anchors
    (a long exam-number digit run at the very start of the line)."""
    rows = []
    for l in lines:
        hm = _HEAD_RE.match(l.text)
        if not hm:
            continue
        tm = _TAIL_RE.search(l.text)
        if not tm:
            continue
        exam_no, seq_s, name = hm.group(1), hm.group(2), hm.group(3).strip()
        result, avg_s, total_s = tm.group(1), tm.group(2), tm.group(3)
        try:
            seq = int(seq_s)
            average = float(avg_s)
            total = float(total_s)
        except ValueError:
            continue
        rows.append(SchoolExamRow(seq, exam_no, name, result, average, total))
    return rows


# Standard Iraqi/Arab secondary-school grade bands, applied only to
# passing (ناجح) students -- students marked معيد always show a 0
# average in this report (their average isn't computed until they clear
# the repeat subjects), so bucketing them by average would be meaningless.
GRADE_BANDS = [
    ("ممتاز (90-100)", 90, 100.0001),
    ("جيد جدًا (80-89.9)", 80, 90),
    ("جيد (70-79.9)", 70, 80),
    ("متوسط (60-69.9)", 60, 70),
    ("مقبول (50-59.9)", 50, 60),
]


def summarize_school_exam(rows):
    """Return a dict with participation counts, success rate, and a
    grade-band distribution (among passing students only)."""
    participants = len(rows)
    najeh = [r for r in rows if r.result == "ناجح"]
    raseb = [r for r in rows if r.result != "ناجح"]
    success_rate = (len(najeh) / participants * 100) if participants else 0.0

    band_counts = {}
    for label, lo, hi in GRADE_BANDS:
        band_counts[label] = sum(1 for r in najeh if lo <= r.average < hi)

    return {
        "participants": participants,
        "najeh_count": len(najeh),
        "raseb_count": len(raseb),
        "success_rate": success_rate,
        "band_counts": band_counts,
    }
