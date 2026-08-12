"""Quick end-to-end sanity check: build a report from the sample data and write the PDF to disk."""
import io
from pathlib import Path

from app import report_builder

SAMPLE = Path(__file__).parent / "sample_data"


def _f(name):
    data = (SAMPLE / name).read_bytes()
    buf = io.BytesIO(data)
    buf.name = name
    return (name, buf)


uploads = {
    "analytics": _f("web_analytics.csv"),
    "seo": _f("seo_audit.csv"),
    "sales": _f("sales_pipeline.xlsx"),
}
branding = {
    "agency_name": "Northlight Growth Partners",
    "client_name": "Aurora Home Goods",
    "primary_color": "#2a78d6",
    "accent_color": "#eda100",
}

result = report_builder.build_report(uploads, branding)

out = Path(__file__).parent / "generated" / "smoke_test_report.pdf"
out.parent.mkdir(exist_ok=True)
out.write_bytes(result["pdf_bytes"])

print("AI generated:", result["report"].get("_ai_generated"))
print("AI error:", result["report"].get("_ai_error"))
print("Sections:", [s["heading"] for s in result["report"]["sections"]])
print("PDF bytes:", len(result["pdf_bytes"]))
print("Wrote:", out)
