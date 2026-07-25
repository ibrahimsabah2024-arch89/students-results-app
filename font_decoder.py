"""
font_decoder.py
----------------
Utilities to read Arabic exam-result PDFs that use a broken/custom CID font
encoding (common with some Iraqi MOE "الدفتر الالكتروني" exports generated
by certain reporting tools). These PDFs embed a subset TrueType font whose
Identity-H CID codes do NOT map to correct Unicode via the PDF's own
ToUnicode CMap (poppler/pdfplumber extraction comes out garbled).

The trick: the embedded font file itself still carries an internal Unicode
cmap table (used by font editors), and because the PDF uses Identity
CIDToGIDMap (CID == GID), we can invert that internal cmap to build a
GID -> Unicode lookup, then decode every CID character in the document.

This must be rebuilt per-PDF: different export runs can produce different
subset fonts with different CID assignments, even if the font name looks
identical (e.g. "ABCDEE+Arial,Bold").
"""

import io
import unicodedata
from dataclasses import dataclass, field

import pikepdf
import pdfplumber
from fontTools.ttLib import TTFont


@dataclass
class DecodedLine:
    page: int          # 1-indexed page number
    top: float         # vertical position (rounding key), useful for row grouping
    text: str          # decoded, NFKC-normalized, reading-order text


def build_cid_to_unicode_map(pdf_path: str):
    """
    Extract the first embedded CIDFontType2 (TrueType) font found in the PDF
    and build a dict mapping CID -> unicode codepoint, using the font's own
    internal cmap table. Returns (mapping, font_name) or (None, None) if no
    such font is found (meaning the PDF likely has normal, directly
    extractable text).
    """
    pdf = pikepdf.open(pdf_path)
    try:
        for obj in pdf.objects:
            try:
                if (
                    obj.get("/Type") == pikepdf.Name("/Font")
                    and obj.get("/Subtype") == pikepdf.Name("/CIDFontType2")
                ):
                    desc = obj.get("/FontDescriptor")
                    if desc is None:
                        continue
                    ff2 = desc.get("/FontFile2")
                    if ff2 is None:
                        continue
                    font_bytes = ff2.read_bytes()
                    font_name = str(obj.get("/BaseFont", "unknown"))

                    font = TTFont(io.BytesIO(font_bytes))
                    glyph_order = font.getGlyphOrder()
                    name_to_gid = {name: i for i, name in enumerate(glyph_order)}
                    cmap = font.getBestCmap()
                    if not cmap:
                        continue

                    gid_to_unicode = {}
                    for uni, gname in cmap.items():
                        gid = name_to_gid.get(gname)
                        if gid is not None:
                            gid_to_unicode[gid] = uni

                    if gid_to_unicode:
                        return gid_to_unicode, font_name
            except Exception:
                continue
    finally:
        pdf.close()
    return None, None


def _is_arabic_char(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch[0])
    return (
        0x0600 <= cp <= 0x06FF   # Arabic
        or 0x0750 <= cp <= 0x077F  # Arabic Supplement
        or 0xFB50 <= cp <= 0xFDFF  # Arabic Presentation Forms-A
        or 0xFE70 <= cp <= 0xFEFF  # Arabic Presentation Forms-B
    )


def _bidi_reorder(decoded_units) -> str:
    """
    decoded_units: list of (char_text, gap_space_before) in visual
    left-to-right (x-ascending) order.

    Groups characters into runs of consecutive Arabic vs non-Arabic
    characters, then reverses the *order* of runs (RTL paragraph: the
    visually-rightmost run is read first) and reverses character order
    *within* Arabic runs only (since Arabic runs are stored in visual
    left-to-right / increasing-x order, which is the reverse of their
    logical reading order). Non-Arabic (digit/Latin) runs keep their
    internal order untouched.
    """
    # flatten with explicit spaces
    flat = []
    for text, gap_space in decoded_units:
        if gap_space:
            flat.append(" ")
        flat.append(text)

    runs = []  # list of [chars_list, class_or_None]
    current_chars = []
    current_class = None
    for ch in flat:
        if ch == " ":
            current_chars.append(ch)
            continue
        ch_class = _is_arabic_char(ch)
        if current_class is None:
            current_class = ch_class
        elif ch_class != current_class:
            runs.append((current_chars, current_class))
            current_chars = []
            current_class = ch_class
        current_chars.append(ch)
    if current_chars:
        runs.append((current_chars, current_class))

    runs.reverse()

    out_parts = []
    for chars, cls in runs:
        if cls:  # Arabic run -> reverse internal order
            out_parts.append("".join(reversed(chars)))
        else:  # non-Arabic run (digits/Latin/space-only) -> keep as-is
            out_parts.append("".join(chars))
    return "".join(out_parts)


def _decode_char(char_dict, gid_to_unicode, arabic_font_prefix):
    """Decode a single pdfplumber char dict using the CID map when relevant."""
    fontname = char_dict.get("fontname", "")
    text = char_dict.get("text", "")
    if arabic_font_prefix and fontname.startswith(arabic_font_prefix):
        if text.startswith("(cid:"):
            try:
                cid = int(text[5:-1])
            except ValueError:
                return "?"
            u = gid_to_unicode.get(cid)
            return chr(u) if u is not None else "?"
        return text
    return text


def decode_pdf_lines(pdf_path: str, gid_to_unicode: dict, arabic_font_prefix: str,
                      progress_callback=None):
    """
    Decode every page of the PDF into logical lines of text (grouped by
    vertical position, ordered right-to-left, NFKC-normalized so Arabic
    presentation forms collapse back to standard letters).

    Yields DecodedLine objects one page at a time (generator, so the caller
    can show progress / avoid holding everything in memory unnecessarily
    if desired -- though we do return a full list for convenience below).
    """
    lines_out = []
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for pi in range(total):
            page = pdf.pages[pi]
            chars = sorted(page.chars, key=lambda c: (c["top"], c["x0"]))

            # cluster into lines by tolerance on vertical position, robust
            # to sub-point baseline jitter between characters on the same
            # visual row
            clusters = []
            for c in chars:
                placed = False
                for cluster in clusters:
                    if abs(cluster["top"] - c["top"]) <= 2.0:
                        cluster["chars"].append(c)
                        placed = True
                        break
                if not placed:
                    clusters.append({"top": c["top"], "chars": [c]})

            for cluster in clusters:
                row_sorted = sorted(cluster["chars"], key=lambda c: c["x0"])
                decoded_units = []  # list of (text, gap_space_before)
                prev_x1 = None
                for c in row_sorted:
                    gap_space = prev_x1 is not None and (c["x0"] - prev_x1) > 3.0
                    decoded_units.append((_decode_char(c, gid_to_unicode, arabic_font_prefix), gap_space))
                    prev_x1 = c["x1"]

                raw_visual = _bidi_reorder(decoded_units)
                decoded = unicodedata.normalize("NFKC", raw_visual)
                lines_out.append(DecodedLine(page=pi + 1, top=cluster["top"], text=decoded))

            page.flush_cache()
            del page, chars, clusters
            if progress_callback:
                progress_callback(pi + 1, total)
    return lines_out


def normalize_for_search(text: str) -> str:
    """Strip spaces/NBSP so multi-word name searches aren't broken by
    inconsistent whitespace glyphs in the source font."""
    return text.replace(" ", "").replace("\xa0", "")
