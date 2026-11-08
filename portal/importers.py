"""Import students from a CSV / XLSX class list (e.g. exported from the college portal)."""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from django.db import transaction

from .models import Course, Offering, Student, Term
from .services import enroll

ALIASES = {
    "admission_no": {"admissionno", "admissionnumber", "admno", "admission", "admissionid"},
    "name": {"studentname", "name", "nameofstudent"},
    "father_name": {"fathername", "fathersname", "father"},
    "date_of_birth": {"dateofbirth", "dob", "birthdate"},
    "gender": {"gender", "sex"},
    "category": {"category", "caste"},
    "mobile": {"mobilenumber", "mobile", "phone", "phonenumber", "mobileno"},
    "section": {"section"},
    "class": {"class", "classname"},
}
_LOOKUP = {a: f for f, names in ALIASES.items() for a in names}
DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y")
STREAMS = ("Arts", "Science", "Commerce")


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    enrolled: int = 0
    errors: list = field(default_factory=list)


def _norm(h):
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def _raw_rows(uploaded, filename):
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        wb = load_workbook(uploaded, read_only=True, data_only=True)
        raw = [list(r) for r in wb.active.iter_rows(values_only=True)]
    elif name.endswith(".csv"):
        data = uploaded.read()
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        raw = list(csv.reader(io.StringIO(text)))
    else:
        raise ValueError("Please upload a .csv or .xlsx file (open an .xls file in Excel/LibreOffice and save it as .xlsx).")
    return raw


def read_rows(uploaded, filename):
    raw = _raw_rows(uploaded, filename)
    header_idx = next((i for i, row in enumerate(raw[:10])
                       if any(_LOOKUP.get(_norm(c)) == "admission_no" for c in row)), None)
    if header_idx is None:
        raise ValueError("Could not find an 'Admission No' column in the first rows of the file.")
    columns = [_LOOKUP.get(_norm(c)) for c in raw[header_idx]]
    rows = []
    for line_no, row in enumerate(raw[header_idx + 1:], start=header_idx + 2):
        if not any(str(c).strip() for c in row if c is not None):
            continue
        rec = {"_line": line_no}
        for col, value in zip(columns, row):
            if col and col not in rec:
                rec[col] = value
        rows.append(rec)
    return rows


def _text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    t = _text(v)
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(t, f).date()
        except ValueError:
            pass
    return None


def _from_class_text(t):
    stream = next((s for s in STREAMS if s.lower() in t.lower()), "")
    m = re.search(r"\(([A-Za-z0-9]{1,5})\)\s*$", t)
    return stream, (m.group(1) if m else "")


@transaction.atomic
def import_students(uploaded, filename, term, session, stream="", section="", auto_enroll=True):
    result = ImportResult()
    offerings = list(Offering.objects.filter(session=session, course__term=term).select_related("course"))
    offerings = [o for o in offerings if o.course.compulsory]
    for rec in read_rows(uploaded, filename):
        adm, name = _text(rec.get("admission_no")), _text(rec.get("name"))
        if not adm or not name:
            result.errors.append(f"Line {rec['_line']}: admission number and name are required.")
            continue
        c_stream, c_section = _from_class_text(_text(rec.get("class")))
        values = {
            "name": name, "father_name": _text(rec.get("father_name")), "gender": _text(rec.get("gender")),
            "category": _text(rec.get("category")), "mobile": _text(rec.get("mobile"))[:15],
            "stream": stream or c_stream, "section": _text(rec.get("section")) or section or c_section,
        }
        dob = _date(rec.get("date_of_birth"))
        if dob:
            values["date_of_birth"] = dob
        elif _text(rec.get("date_of_birth")):
            result.errors.append(f"Line {rec['_line']} ({adm}): unreadable date of birth, left blank.")
        values = {k: v for k, v in values.items() if v not in ("", None) or k == "name"}
        student, created = Student.objects.update_or_create(
            admission_no=adm, defaults={**values, "term": term, "session": session, "is_active": True})
        result.created += created
        result.updated += not created
        if auto_enroll:
            for off in offerings:
                result.enrolled += enroll(student, off)[1]
    return result


# ------------------------------------------------------------ subjects / papers
COURSE_ALIASES = {
    "class": {"class", "classname", "semester", "year", "level", "grade", "term"},
    "code": {"code", "subjectcode", "papercode", "coursecode"},
    "name": {"name", "subject", "paper", "course", "title", "subjectname", "papername", "papertitle"},
    "credits": {"credits", "credit"},
    "compulsory": {"compulsory", "core", "mandatory"},
}
_COURSE_LOOKUP = {a: f for f, names in COURSE_ALIASES.items() for a in names}


@dataclass
class CourseImportResult:
    created: int = 0
    updated: int = 0
    errors: list = field(default_factory=list)


@transaction.atomic
def import_courses(uploaded, filename):
    """Columns: Class, Code, Name, Credits (optional), Compulsory yes/no (optional, default yes)."""
    raw = _raw_rows(uploaded, filename)
    header_idx = next((i for i, row in enumerate(raw[:10])
                       if {"class", "code", "name"} <= {_COURSE_LOOKUP.get(_norm(c)) for c in row}), None)
    if header_idx is None:
        raise ValueError("The first rows must contain the headings: Class, Code, Name (Credits and Compulsory are optional).")
    columns = [_COURSE_LOOKUP.get(_norm(c)) for c in raw[header_idx]]
    terms = {t.name.lower(): t for t in Term.objects.all()}
    result = CourseImportResult()
    for line_no, row in enumerate(raw[header_idx + 1:], start=header_idx + 2):
        if not any(str(c).strip() for c in row if c is not None):
            continue
        rec = {}
        for col, value in zip(columns, row):
            if col and col not in rec:
                rec[col] = value
        cname, code, name = _text(rec.get("class")), _text(rec.get("code")), _text(rec.get("name"))
        term = terms.get(cname.lower())
        if not (cname and code and name):
            result.errors.append(f"Line {line_no}: class, code and name are all required.")
            continue
        if term is None:
            result.errors.append(f"Line {line_no}: there is no class called '{cname}'. Add it under Classes first.")
            continue
        try:
            credits = float(_text(rec.get("credits")) or 0)
        except ValueError:
            result.errors.append(f"Line {line_no} ({code}): credits must be a number, left as 0.")
            credits = 0
        comp = _text(rec.get("compulsory")).lower() not in ("no", "n", "false", "0", "optional", "elective")
        _, created = Course.objects.update_or_create(
            term=term, code=code[:20], defaults={"name": name[:140], "credits": credits, "compulsory": comp})
        result.created += created
        result.updated += not created
    return result
