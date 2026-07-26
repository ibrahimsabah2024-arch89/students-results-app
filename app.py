"""
Streamlit app: أداة قراءة وتحليل ملفات نتائج الطلبة (PDF)
------------------------------------------------------
- يدعم ملفات PDF التي تستخدم ترميز خط عربي غير قياسي (شائع في بعض تصدير
  نتائج وزارة التربية العراقية)، حيث يقوم بفك الترميز تلقائيًا لكل ملف
  يُرفع (لأن كل ملف قد يحمل خريطة ترميز مختلفة حتى لو تشابه اسم الخط).
- تبويب "البحث عن طالب": يبحث عن اسم/كلمة داخل كل صفحات الملف.
- تبويب "إحصائيات المعدلات": مخصص لملفات قوائم المعدلات (ت / المعدل
  الأصلي / درجة اللغات / الإضافة / المعدل بعد الإضافة)، ويتيح إضافة درجة
  إلى أي عمود، عرض الأعداد حسب الفئة، وحساب أدنى معدل ضمن أعلى N طالب.
"""

import html
import io
import re
import tempfile

import pandas as pd
import streamlit as st

from font_decoder import build_cid_to_unicode_map, decode_pdf_lines, normalize_for_search
from parser_averages import parse_average_rows
from parser_medicine import parse_admission_rows, medicine_admission_counts

st.set_page_config(page_title="تحليل نتائج الطلبة", layout="wide", page_icon="📊")

st.markdown(
    """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {
--bg: #0B1220;
--surface: #141C2E;
--surface-2: #1B2540;
--text: #E8ECF4;
--text-dim: #8891A5;
--gold: #D4AF37;
--gold-soft: rgba(212, 175, 55, 0.14);
--border: rgba(212, 175, 55, 0.22);
}
html, body, [class*="css"] { font-family: 'Tajawal', sans-serif !important; }
.stApp { direction: rtl; text-align: right; background: var(--bg); }
div[data-testid="stMetricValue"] { direction: ltr; font-family: 'JetBrains Mono', monospace !important; color: var(--gold); }
div[data-testid="stMetricLabel"] { color: var(--text-dim); }
.mono { font-family: 'JetBrains Mono', monospace; direction: ltr; unicode-bidi: plaintext; }
.app-hero {
background: linear-gradient(160deg, var(--surface) 0%, var(--surface-2) 100%);
border: 1px solid var(--border);
border-radius: 18px;
padding: 28px 32px;
margin-bottom: 22px;
color: var(--text);
box-shadow: 0 8px 28px rgba(0, 0, 0, 0.35);
position: relative;
overflow: hidden;
}
.app-hero::after {
content: "";
position: absolute;
bottom: 0; right: 0; left: 0;
height: 3px;
background: linear-gradient(90deg, transparent, var(--gold), transparent);
}
.app-hero h1 { margin: 0 0 8px 0; font-size: 1.9rem; font-weight: 900; color: var(--text); }
.app-hero p { margin: 0; opacity: 0.8; font-size: 1.02rem; color: var(--text-dim); }
div[data-testid="stMetric"] {
background: var(--surface);
border: 1px solid var(--border);
border-radius: 14px;
padding: 14px 16px;
}
.stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid var(--border); }
.stTabs [data-baseweb="tab"] {
border-radius: 10px 10px 0 0;
padding: 10px 18px;
font-weight: 700;
color: var(--text-dim);
}
.stTabs [aria-selected="true"] { color: var(--gold) !important; }
.stTabs [data-baseweb="tab-highlight"] { background-color: var(--gold) !important; }
div[data-testid="stDataFrame"] * { text-align: right !important; }
div[data-testid="stDataFrame"] { border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.stDownloadButton button, .stButton button {
border-radius: 10px;
font-weight: 700;
border: 1px solid var(--border);
}
hr, div[data-testid="stDivider"] { opacity: 0.25; border-color: var(--gold); }
.rank-badge {
display: inline-flex;
align-items: center;
justify-content: center;
min-width: 1.7em;
height: 1.7em;
padding: 0 0.3em;
border-radius: 999px;
background: var(--gold-soft);
border: 1px solid var(--gold);
color: var(--gold);
font-family: 'JetBrains Mono', monospace;
font-weight: 700;
font-size: 0.85rem;
direction: ltr;
margin-left: 0.5em;
}
.result-row {
background: var(--surface);
border: 1px solid var(--border);
border-radius: 12px;
padding: 10px 14px;
margin-bottom: 8px;
}
.result-row .context-line { color: var(--text-dim); font-size: 0.92rem; padding: 2px 0; }
.result-row .match-line { color: var(--text); font-size: 1.02rem; padding: 2px 0; }
</style>""",
    unsafe_allow_html=True,
)

st.markdown(
    """<div class="app-hero">
<h1>📊 أداة تحليل ملفات نتائج الطلبة</h1>
<p>ارفع ملف PDF لنتائج الطلبة (حتى لو كان بترميز خط غير قابل للنسخ مباشرة) —
وسيتم فك تشفيره وتحليله تلقائيًا: بحث بالاسم، إحصائيات المعدلات، وعدد
المقبولين في كليات الطب حسب كل جامعة.</p>
</div>""",
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner="⏳ جاري تحليل الملف وفك ترميزه... قد يستغرق ذلك بضع لحظات للملفات الكبيرة.")
def _process_pdf(file_bytes: bytes):
    """Cache heavy work per uploaded file (by content hash).

    IMPORTANT: this function must not call any Streamlit UI commands
    (st.progress, st.write, etc.) directly or indirectly (e.g. via a
    callback closure). Streamlit's cache tries to record/replay any UI
    elements produced inside a cached function, and a closure over a
    live widget/placeholder can't be replayed on a cache hit -- this
    previously caused a CacheReplayClosureError. Progress is now shown
    only via st.cache_data's own `show_spinner` text above.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    gid_map, font_name = build_cid_to_unicode_map(tmp_path)

    if gid_map:
        prefix = font_name.lstrip("/").split("+")[0] if font_name else ""
        lines = decode_pdf_lines(tmp_path, gid_map, prefix)
        decoded_ok = True
    else:
        # No broken CID font detected -- fall back to plain extraction
        import pdfplumber
        from font_decoder import DecodedLine

        lines = []
        with pdfplumber.open(tmp_path) as pdf:
            for pi, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                for row_i, row_text in enumerate(text.split("\n")):
                    lines.append(DecodedLine(page=pi + 1, top=float(row_i), text=row_text))
        decoded_ok = False

    return lines, decoded_ok, font_name


@st.cache_data(show_spinner=False)
def _cached_average_rows(file_bytes: bytes, _lines):
    """Cache the parsed averages-table rows too, so moving a slider in
    the stats tab doesn't re-scan tens of thousands of lines on every
    single rerun -- only the (cheap) arithmetic on the cached DataFrame
    re-runs."""
    return parse_average_rows(_lines)


@st.cache_data(show_spinner=False)
def _cached_admission_rows(file_bytes: bytes, _lines):
    """Same idea as above, for the Medicine-colleges admission parser."""
    return parse_admission_rows(_lines)


_LEADING_NUM_RE = re.compile(r"^\s*(\d+)\s+(.*)$")


def _line_html(text: str) -> str:
    """Render one decoded PDF line as safe HTML, turning a leading
    sequence number (e.g. "59 وليد بهاء احمد شهاب...") into the gold
    rank-badge signature element instead of plain text."""
    m = _LEADING_NUM_RE.match(text)
    if m:
        num, rest = m.group(1), m.group(2)
        return f'<span class="rank-badge">{html.escape(num)}</span>{html.escape(rest)}'
    return html.escape(text)


uploaded = st.file_uploader("ارفع ملف PDF", type=["pdf"])

if not uploaded:
    st.info("👆 ارفع ملفًا للبدء.")
    st.stop()

file_bytes = uploaded.getvalue()

status_placeholder = st.empty()

lines, decoded_ok, font_name = _process_pdf(file_bytes)

n_pages = max((l.page for l in lines), default=0)
status_placeholder.success(f"✅ تم تحليل {n_pages:,} صفحة، {len(lines):,} سطرًا نصيًا.")
if not decoded_ok:
    st.warning(
        "لم أجد خط CID مخصص يحتاج فك تشفير -- تم استخدام الاستخراج المباشر للنص "
        "(الملف على الأرجح لا يعاني من مشكلة الترميز)."
    )

tab_search, tab_stats, tab_medicine = st.tabs(
    ["🔍 البحث عن طالب", "📈 إحصائيات المعدلات", "🩺 كليات الطب"]
)

# ----------------------------------------------------------------------
# TAB 1: Search
# ----------------------------------------------------------------------
with tab_search:
    st.subheader("البحث عن طالب/طالبة بالاسم")
    query = st.text_input(
        "اكتب الاسم (أو جزءًا منه، مثل: اسم الطالب واسم والده)",
        placeholder="مثال: احمد ليث عبد الحسين",
    )
    context_n = st.number_input(
        "عدد الأسطر المجاورة (قبل/بعد) لعرضها مع كل نتيجة",
        min_value=0, max_value=10, value=0, step=1,
        help="فعّلها إذا كانت معلومات الطالب (كالمعدل أو الكلية) موزعة على "
             "أكثر من سطر في الملف، لعرض ما حول السطر المطابق ضمن نفس الصفحة.",
    )
    if query:
        target = normalize_for_search(query)
        match_indices = [
            i for i, l in enumerate(lines) if target in normalize_for_search(l.text)
        ]
        st.write(f"عدد النتائج: **{len(match_indices)}**")
        for idx in match_indices[:200]:
            m = lines[idx]
            if context_n == 0:
                st.markdown(
                    f'<div class="result-row"><span class="match-line">'
                    f'صفحة {m.page}: {_line_html(m.text)}</span></div>',
                    unsafe_allow_html=True,
                )
                continue

            # Widen the window to include up to `context_n` neighboring
            # lines on each side, but never cross into a different page.
            lo = idx
            while lo > 0 and idx - lo < context_n and lines[lo - 1].page == m.page:
                lo -= 1
            hi = idx
            while hi < len(lines) - 1 and hi - idx < context_n and lines[hi + 1].page == m.page:
                hi += 1

            row_html = [f'<div class="result-row"><div class="context-line">صفحة {m.page}</div>']
            for j in range(lo, hi + 1):
                cls = "match-line" if j == idx else "context-line"
                prefix = "➡️ " if j == idx else ""
                row_html.append(f'<div class="{cls}">{prefix}{_line_html(lines[j].text)}</div>')
            row_html.append("</div>")
            st.markdown("".join(row_html), unsafe_allow_html=True)
        if len(match_indices) > 200:
            st.caption("تم عرض أول 200 نتيجة فقط.")

# ----------------------------------------------------------------------
# TAB 2: Averages statistics
# ----------------------------------------------------------------------
with tab_stats:
    st.subheader("إحصائيات ملف قائمة المعدلات")
    st.caption(
        "هذا القسم مخصص لملفات تحتوي أعمدة رقمية فقط (ت، المعدل الأصلي، "
        "درجة اللغات، الإضافة، المعدل بعد الإضافة) بدون أسماء."
    )

    rows = _cached_average_rows(file_bytes, lines)
    if not rows:
        st.warning("لم يتم العثور على صفوف بهذا التنسيق في الملف المرفوع.")
    else:
        df = pd.DataFrame(
            [
                {
                    "الفئة": r.group,
                    "الصفحة": r.page,
                    "ت": r.idx,
                    "المعدل الأصلي": r.orig_avg,
                    "المعدل بعد الإضافة (الأصلي)": r.final_avg_source,
                }
                for r in rows
            ]
        )

        total_students = len(df)
        n_groups = df["الفئة"].nunique()
        st.metric("العدد الكلي للطلبة", total_students)
        st.metric("عدد الفئات", n_groups)

        st.markdown("**عدد الطلبة حسب كل فئة:**")
        counts = df.groupby("الفئة").size().reset_index(name="عدد الطلبة")
        st.dataframe(counts, use_container_width=True)

        st.divider()
        st.markdown("### إضافة درجة على المعدل")
        col1, col2, col3 = st.columns(3)
        with col1:
            points = st.number_input("عدد الدرجات المضافة", value=1.0, step=0.5)
        with col2:
            base_col = st.selectbox(
                "أضف الدرجة على:",
                ["المعدل بعد الإضافة (الأصلي)", "المعدل الأصلي"],
            )
        with col3:
            cap_100 = st.checkbox("تحديد حد أقصى 100", value=False)

        df["المعدل بعد الإضافة الجديدة"] = df[base_col] + points
        if cap_100:
            df["المعدل بعد الإضافة الجديدة"] = df["المعدل بعد الإضافة الجديدة"].clip(upper=100)

        st.dataframe(df, use_container_width=True, height=400)

        st.divider()
        st.markdown("### كم عدد الطلبة الذين بلغ معدلهم قيمة معينة فأكثر؟")
        threshold = st.number_input("القيمة الحدية", value=100.0, step=0.5, key="thresh")
        n_ge = (df["المعدل بعد الإضافة الجديدة"] >= threshold).sum()
        st.metric(f"عدد الطلبة بمعدل ≥ {threshold}", int(n_ge))

        st.divider()
        st.markdown("### أعلى N طالب -- ما هو أدنى معدل ضمنهم؟")
        top_n = st.number_input(
            "عدد الطلبة (N)", min_value=1, max_value=total_students,
            value=min(100, total_students), step=1,
        )
        sorted_vals = df["المعدل بعد الإضافة الجديدة"].sort_values(ascending=False).reset_index(drop=True)
        if top_n <= len(sorted_vals):
            cutoff = sorted_vals.iloc[top_n - 1]
            above = (sorted_vals > cutoff).sum()
            tied = (sorted_vals == cutoff).sum()
            st.metric(f"أدنى معدل ضمن أعلى {top_n}", f"{cutoff:.2f}")
            st.caption(
                f"عدد الطلبة الذين معدلهم أعلى من {cutoff:.2f}: {above} — "
                f"وعدد المتساوين عند هذه القيمة بالضبط: {tied}."
            )

        st.divider()
        st.markdown("### تنزيل النتائج")
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="البيانات", index=False)
            counts.to_excel(writer, sheet_name="الملخص", index=False)
        st.download_button(
            "⬇️ تنزيل النتائج كملف Excel",
            data=buf.getvalue(),
            file_name="نتائج_الطلبة.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# ----------------------------------------------------------------------
# TAB 3: Medicine colleges admission counts
# ----------------------------------------------------------------------
with tab_medicine:
    st.subheader("عدد الطلبة المقبولين في كليات الطب (حسب كل جامعة)")
    st.caption(
        "مخصص لملفات القبول المركزي (كل سطر فيه اسم الطالب + الرقم الامتحاني + "
        "الجامعة/الكلية المقبول فيها). يُستبعد طب الأسنان والطب البيطري تلقائيًا "
        "(الكلية البشرية فقط)، وتُدمج قنوات القبول المحلي (أبناء المحافظة) مع "
        "القبول العام لنفس الكلية في صف واحد."
    )

    admission_rows = _cached_admission_rows(file_bytes, lines)
    st.write(f"عدد صفوف الطلبة التي تم التعرف عليها في الملف: **{len(admission_rows)}**")

    if not admission_rows:
        st.warning("لم يتم العثور على صفوف قبول بهذا التنسيق في الملف المرفوع.")
    else:
        med_df = medicine_admission_counts(admission_rows)
        if med_df.empty:
            st.info("لم يتم العثور على أي كلية طب (بشري) ضمن هذا الملف.")
        else:
            st.dataframe(med_df, use_container_width=True, height=min(700, 40 * len(med_df) + 40))

            med_buf = io.BytesIO()
            with pd.ExcelWriter(med_buf, engine="openpyxl") as writer:
                med_df.to_excel(writer, sheet_name="كليات الطب", index=False)
            st.download_button(
                "⬇️ تنزيل جدول كليات الطب كملف Excel",
                data=med_buf.getvalue(),
                file_name="كليات_الطب.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
