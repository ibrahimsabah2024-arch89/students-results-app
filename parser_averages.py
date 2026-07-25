"""
parser_averages.py
-------------------
Parses the numeric "averages list" PDF format used for files like the
original 4,305-student report: each row has a sequential index (ت) that
resets to 1 whenever a new group/category begins, an original average
(المعدل الأصلي), and optionally a language-bonus score (درجة اللغات), a
computed addition (الإضافة), and the resulting average after that addition
(المعدل بعد الإضافة).

Column count varies row to row (blank cells are simply omitted rather than
shown as 0), so we parse from the *edges* inward:
    - last token  -> ت (serial index)
    - 2nd-to-last -> المعدل الأصلي (original average)
    - 1st token   -> المعدل بعد الإضافة (average after addition, as already
                     computed in the source file)
    - any middle tokens -> درجة اللغات / الإضافة (best-effort, kept as-is)
"""

from dataclasses import dataclass, field
from typing import List, Optional

from font_decoder import DecodedLine


@dataclass
class AverageRow:
    page: int
    group: int          # which reset-group (category) this row belongs to
    idx: int             # ت, the serial number within the group
    orig_avg: float       # المعدل الأصلي
    final_avg_source: float  # المعدل بعد الإضافة, as found in the source file
    lang_score: Optional[str] = None
    addition: Optional[str] = None


def _looks_like_data_row(tokens: List[str]) -> bool:
    if len(tokens) < 3:
        return False
    try:
        int(float(tokens[-1]))
        float(tokens[-2])
        float(tokens[0])
    except ValueError:
        return False
    return True


def parse_average_rows(lines: List[DecodedLine]) -> List[AverageRow]:
    raw_rows = []
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        # skip lines containing Arabic letters (headers / titles) -- data
        # rows in this format are purely numeric tokens
        if any("\u0600" <= ch <= "\u06FF" for ch in text):
            continue
        tokens = text.split()
        if not _looks_like_data_row(tokens):
            continue
        try:
            idx = int(float(tokens[-1]))
            orig = float(tokens[-2])
            final_src = float(tokens[0])
        except ValueError:
            continue

        lang = None
        addition = None
        if len(tokens) == 5:
            addition = tokens[1]
            lang = tokens[2]
        elif len(tokens) == 4:
            lang = tokens[1]

        raw_rows.append((line.page, idx, orig, final_src, lang, addition))

    # detect group boundaries: a reset to idx==1 (after a non-1 idx) starts
    # a new group/category
    result: List[AverageRow] = []
    group = 0
    prev_idx = None
    for page, idx, orig, final_src, lang, addition in raw_rows:
        if prev_idx is None or (idx == 1 and prev_idx != 1):
            group += 1
        prev_idx = idx
        result.append(
            AverageRow(
                page=page,
                group=group,
                idx=idx,
                orig_avg=orig,
                final_avg_source=final_src,
                lang_score=lang,
                addition=addition,
            )
        )
    return result
