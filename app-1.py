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

import io
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
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"] { font-family: 'Tajawal', sans-serif !important; }
.stApp { direction: rtl; text-align: right; }
div[data-testid="stMetricValue"] { direction: ltr; }
.app-hero {
background: linear-gradient(120deg, #1f6feb 0%, #6a4bd6 100%);
border-radius: 18px;
padding: 28px 32px;
margin-bottom: 22px;
color: #ffffff;
box-shadow: 0 8px 24px rgba(31, 111, 235, 0.25);
}
.app-hero h1 { margin: 0 0 6px 0; font-size: 1.9rem; font-weight: 900; }
.app-hero p { margin: 0; opacity: 0.92; font-size: 1.02rem; }
div[data-testid="stMetric"] {
background: rgba(31, 111, 235, 0.07);
border: 1px solid rgba(31, 111, 235, 0.18);
border-radius: 14px;
padding: 14px 16px;
}
.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] {
border-radius: 10px 10px 0 0;
padding: 10px 18px;
font-weight: 700;
}
div[data-testid="stDataFrame"] * { text-align: right !important; }
.stDownloadButton button, .stButton button {
border-radius: 10px;
font-weight: 700;
}
hr, div[data-testid="stDivider"] { opacity: 0.35; }
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


@st.cache_data(show_spinner=False)
def _process_pdf(file_bytes: bytes, _progress_cb=None):
    """Cache heavy work per uploaded file (by content hash).

    `_progress_cb` (leading underscore) is excluded from the cache key by
    Streamlit's convention -- it's only used to report progress on a
    cache *miss* (first time this exact file is uploaded); repeat runs on
    the same file are served straight from cache with no re-processing.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    gid_map, font_name = build_cid_to_unicode_map(tmp_path)

    if gid_map:
        prefix = font_name.lstrip("/").split("+")[0] if font_name else ""
        lines = decode_pdf_lines(tmp_path, gid_map, prefix, progress_callback=_progress_cb)
        decoded_ok = True
    else:
        # No broken CID font detected -- fall back to plain extraction
        import pdfplumber
        import unicodedata
        from font_decoder import DecodedLine

        lines = []
        with pdfplumber.open(tmp_path) as pdf:
            total = len(pdf.pages)
            for pi, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                for row_i, row_text in enumerate(text.split("\n")):
                    lines.append(DecodedLine(page=pi + 1, top=float(row_i), text=row_text))
                if _progress_cb:
                    _progress_cb(pi + 1, total)
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


uploaded = st.file_uploader("ارفع ملف PDF", type=["pdf"])

if not uploaded:
    st.info("👆 ارفع ملفًا للبدء.")
    st.stop()

file_bytes = uploaded.getvalue()

progress_placeholder = st.empty()
status_placeholder = st.empty()

# Initial message shown immediately on upload, before the first page
# callback fires (font/CID-map scanning happens first and can itself
# take a moment on large files) -- so the user sees *something* right
# away instead of a blank gap.
progress_placeholder.progress(0, text="📥 جاري فحص الملف وتجهيز فك الترميز...")


def _report_progress(done: int, total: int):
    pct = done / total if total else 0
    progress_placeholder.progress(
        pct, text=f"⏳ جاري تحليل الصفحة {done:,} من {total:,} ({pct:.0%})"
    )


lines, decoded_ok, font_name = _process_pdf(file_bytes, _progress_cb=_report_progress)
progress_placeholder.empty()

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
    if query:
        target = normalize_for_search(query)
        matches = [l for l in lines if target in normalize_for_search(l.text)]
        st.write(f"عدد النتائج: **{len(matches)}**")
        for m in matches[:200]:
            st.markdown(f"**صفحة {m.page}:** {m.text}")
        if len(matches) > 200:
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
