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

st.set_page_config(page_title="تحليل نتائج الطلبة", layout="wide", page_icon="📊")

st.markdown(
    """
    <style>
    .stApp { direction: rtl; text-align: right; }
    div[data-testid="stMetricValue"] { direction: ltr; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📊 أداة تحليل ملفات نتائج الطلبة (PDF)")
st.caption(
    "ارفع ملف PDF لنتائج الطلبة (حتى لو كان بترميز خط غير قابل للنسخ مباشرة)، "
    "وسيتم فك تشفيره تلقائيًا."
)


@st.cache_data(show_spinner=False)
def _process_pdf(file_bytes: bytes):
    """Cache heavy work per uploaded file (by content hash)."""
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
        import unicodedata
        from font_decoder import DecodedLine

        lines = []
        with pdfplumber.open(tmp_path) as pdf:
            for pi, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                for row_i, row_text in enumerate(text.split("\n")):
                    lines.append(DecodedLine(page=pi + 1, top=float(row_i), text=row_text))
        decoded_ok = False

    return lines, decoded_ok, font_name


uploaded = st.file_uploader("ارفع ملف PDF", type=["pdf"])

if not uploaded:
    st.info("👆 ارفع ملفًا للبدء.")
    st.stop()

with st.spinner("جاري فك تشفير الملف وقراءة كل الصفحات... قد يستغرق هذا دقيقة لملفات كبيرة."):
    lines, decoded_ok, font_name = _process_pdf(uploaded.getvalue())

n_pages = max((l.page for l in lines), default=0)
st.success(f"تم تحليل {n_pages} صفحة، {len(lines)} سطرًا نصيًا.")
if not decoded_ok:
    st.warning(
        "لم أجد خط CID مخصص يحتاج فك تشفير -- تم استخدام الاستخراج المباشر للنص "
        "(الملف على الأرجح لا يعاني من مشكلة الترميز)."
    )

tab_search, tab_stats = st.tabs(["🔍 البحث عن طالب", "📈 إحصائيات المعدلات"])

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

    rows = parse_average_rows(lines)
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
