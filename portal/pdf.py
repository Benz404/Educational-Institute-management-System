"""Marksheet PDFs (reportlab): one semester sheet per page, or a student's complete record."""
from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import ExamTemplate, SiteConfig, fmt

_b = getSampleStyleSheet()["Normal"]
S_COLLEGE = ParagraphStyle("c", parent=_b, fontName="Helvetica-Bold", fontSize=17, alignment=TA_CENTER, leading=21)
S_DEPT = ParagraphStyle("d", parent=_b, fontName="Helvetica", fontSize=11, alignment=TA_CENTER, leading=14)
S_TITLE = ParagraphStyle("t", parent=_b, fontName="Helvetica-Bold", fontSize=13, alignment=TA_CENTER, leading=17)
S_CELL = ParagraphStyle("cell", parent=_b, fontName="Helvetica", fontSize=9, leading=11)
S_HEAD = ParagraphStyle("h", parent=S_CELL, fontName="Helvetica-Bold", alignment=TA_CENTER)
S_C = ParagraphStyle("cc", parent=S_CELL, alignment=TA_CENTER)
S_NOTE = ParagraphStyle("n", parent=_b, fontName="Helvetica-Oblique", fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
S_SMALL = ParagraphStyle("s", parent=_b, fontName="Helvetica", fontSize=8, alignment=TA_CENTER)
GRID, HEAD_BG = colors.HexColor("#444444"), colors.HexColor("#e8e8e8")


def _p(text, style=S_CELL):
    return Paragraph(escape(str(text)), style)


def _style(extra=()):
    return TableStyle([("BOX", (0, 0), (-1, -1), 0.6, GRID), ("INNERGRID", (0, 0), (-1, -1), 0.3, GRID),
                       ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), *extra])


def _logo_flowable(site):
    try:
        if site and site.logo and site.logo.path and __import__("os").path.exists(site.logo.path):
            from PIL import Image as PILImage
            w, h = PILImage.open(site.logo.path).size
            height = 18 * mm
            return Image(site.logo.path, width=height * w / h, height=height, hAlign="CENTER")
    except Exception:
        pass
    return None


def _header(title, subtitle):
    site = SiteConfig.objects.first()
    out = []
    logo = _logo_flowable(site)
    if logo:
        out += [logo, Spacer(1, 2 * mm)]
    out.append(Paragraph(escape(site.institution_name if site else "Marksheet"), S_COLLEGE))
    if site and site.department_name:
        out.append(Paragraph(escape(site.department_name), S_DEPT))
    return out + [Spacer(1, 3 * mm), Paragraph(escape(title), S_TITLE), Paragraph(escape(subtitle), S_DEPT), Spacer(1, 4 * mm)]


def _student_box(st):
    dob = st.date_of_birth.strftime("%d-%m-%Y") if st.date_of_birth else "-"
    t = Table([[_p("Name of Student"), _p(st.name.upper()), _p("Admission No."), _p(st.admission_no)],
               [_p("Father's Name"), _p(st.father_name or "-"), _p("Date of Birth"), _p(dob)]],
              colWidths=[32 * mm, 62 * mm, 32 * mm, 54 * mm])
    t.setStyle(_style([("BACKGROUND", (0, 0), (0, -1), HEAD_BG), ("BACKGROUND", (2, 0), (2, -1), HEAD_BG)]))
    return t


def _marks_table(results, exam_labels, show_grade=True, paper_word="Subject"):
    head = ["Code", paper_word, *exam_labels, "Total", "%", *(["Grade"] if show_grade else []), "Result"]
    rows = [[_p(h, S_HEAD) for h in head]]
    for r in results:
        pend = r["status"] == "PENDING"
        cells = []
        for c in r["cells"]:
            cells.append(_p(f"{c['text'] or '-'} / {fmt(c['exam'].max_marks)}", S_C))
        title = r["course"].name + (" (Backlog attempt)" if r["is_backlog"] else "")
        rows.append([_p(r["course"].code), _p(title), *cells,
                     _p("-" if pend else f"{fmt(r['total'])} / {fmt(r['max_total'])}", S_C),
                     _p("-" if pend else fmt(r["percent"]), S_C), *([_p(r["grade"] or "-", S_C)] if show_grade else []),
                     _p({"PASS": "Pass", "FAIL": "Fail", "PENDING": "Pending"}[r["status"]], S_C)])
    n = len(exam_labels)
    widths = [22 * mm, 44 * mm] + [17 * mm] * n + [19 * mm, 11 * mm] + ([12 * mm] if show_grade else []) + [14 * mm]
    scale = 180 * mm / sum(widths)
    t = Table(rows, colWidths=[w * scale for w in widths], repeatRows=1)
    t.setStyle(_style([("BACKGROUND", (0, 0), (-1, 0), HEAD_BG)]))
    return t


def _sheet_story(sheet, exam_labels, site):
    term = sheet["term"]
    show_grade = bool(site and site.use_grades)
    paper_word = site.paper_label if site else "Subject"
    story = _header("STATEMENT OF MARKS", f"{term.name}  |  Session {sheet['session']}")
    story += [_student_box(sheet["student"]), Spacer(1, 4 * mm)]
    if sheet["regular"]:
        story.append(_marks_table(sheet["regular"], exam_labels, show_grade, paper_word))
    if sheet["retakes"]:
        story += [Spacer(1, 4 * mm), Paragraph("Backlog attempts in this session "
                                               "(not counted in the totals below)", S_NOTE),
                  _marks_table(sheet["retakes"], exam_labels, show_grade, paper_word)]
    story.append(Spacer(1, 4 * mm))
    summary = []
    if sheet["grand_total"] is not None:
        summary += [("Grand Total", f"{fmt(sheet['grand_total'])} / {fmt(sheet['grand_max'])}"),
                    ("Percentage", f"{fmt(sheet['percent'])} %")]
        if sheet["sgpa"] is not None:
            summary.append(("SGPA", f"{sheet['sgpa']}  (credits {fmt(sheet['total_credits'])})"))
        if sheet["division"]:
            summary.append(("Division", sheet["division"]))
    result = {"PASS": "PASSED", "FAIL": "FAILED  (" + ", ".join(sheet["failed"]) + ")",
              "INCOMPLETE": "PROVISIONAL - marks not complete", "RETAKE": "Backlog attempts only"}[sheet["result"]]
    summary.append(("Result", result))
    t = Table([[_p(k, S_HEAD), _p(v)] for k, v in summary], colWidths=[40 * mm, 140 * mm])
    t.setStyle(_style([("BACKGROUND", (0, 0), (0, -1), HEAD_BG)]))
    story += [t, Spacer(1, 3 * mm),
              Paragraph(_pattern_note(), S_NOTE),
              Spacer(1, 18 * mm)]
    labels = (site.signature_list if site else []) or ["Signature"]
    sig = Table([[_p("_" * 28, S_SMALL) for _ in labels], [_p(l, S_SMALL) for l in labels]],
                colWidths=[180 * mm / len(labels)] * len(labels))
    story += [sig, Spacer(1, 5 * mm), Paragraph(f"Generated on {date.today():%d-%m-%Y}", S_SMALL)]
    return story


def _pattern_note():
    ex = list(ExamTemplate.objects.all())
    total = sum((e.max_marks for e in ex), 0)
    return "Each subject: " + " + ".join(f"{e.name} ({fmt(e.max_marks)})" for e in ex) + f" = {fmt(total)}. AB = absent."


def render_sheets(sheets, backlog_note_for=None):
    """sheets: list from services.build_sheets. backlog_note_for: student whose open backlogs are listed last."""
    buf = BytesIO()
    site = SiteConfig.objects.first()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title="Marksheet",
                            author=site.institution_name if site else "")
    labels = [f"{e.name} ({fmt(e.max_marks)})" for e in ExamTemplate.objects.all()]
    story = []
    for i, sh in enumerate(sheets):
        if i:
            story.append(PageBreak())
        story += _sheet_story(sh, labels, site)
    if backlog_note_for is not None:
        from .models import Backlog
        open_bl = Backlog.objects.filter(student=backlog_note_for, status=Backlog.OPEN).select_related("course")
        if open_bl:
            story += [Spacer(1, 6 * mm), Paragraph("Open backlogs: " + ", ".join(
                f"{b.course.code} ({b.course.name})" for b in open_bl), S_NOTE)]
    if not story:
        story = [Paragraph("No marksheets to print.", S_DEPT)]
    doc.build(story)
    return buf.getvalue()
