"""The one-time installation: store institution details, classes, exams and rules (and the admin login)."""
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import DEFAULT_DIVISIONS, DEFAULT_GRADE_BANDS, ExamTemplate, SiteConfig, Term, fmt
from .rules import RuleError, parse_divisions, parse_grade_bands

MAX_EXAMS = 6
MAX_CLASSES = 60

TYPE_DEFAULTS = {
    "SCHOOL": {"level_label": "Class", "paper_label": "Subject", "prefix": "Class", "count": 10,
               "signatures": "Class Teacher, Principal", "use_grades": False, "show_division": False},
    "COLLEGE": {"level_label": "Semester", "paper_label": "Paper", "prefix": "Semester", "count": 6,
                "signatures": "Prepared by, Head of Department, Principal", "use_grades": True, "show_division": False},
    "UNIVERSITY": {"level_label": "Semester", "paper_label": "Course", "prefix": "Semester", "count": 8,
                   "signatures": "Prepared by, Head of Department, Controller of Examinations", "use_grades": True, "show_division": False},
    "OTHER": {"level_label": "Class", "paper_label": "Subject", "prefix": "Class", "count": 5,
              "signatures": "Prepared by, Principal", "use_grades": False, "show_division": False},
}


def default_exam_rows(count):
    """Sensible starting names/marks: 3 exams -> 20 + 20 + 60, otherwise an equal split of 100."""
    if count == 3:
        return [("Sessional 1", "20"), ("Sessional 2", "20"), ("Final", "60")]
    if count == 1:
        return [("Final Exam", "100")]
    base = 100 // count
    rows = [(f"Exam {i}", str(base)) for i in range(1, count + 1)]
    rows[-1] = (rows[-1][0], str(100 - base * (count - 1)))
    return rows


def generate_names(prefix, count):
    return "\n".join(f"{prefix} {i}" for i in range(1, count + 1))


def parse_class_names(text):
    names, seen = [], set()
    for line in (text or "").splitlines():
        n = " ".join(line.split())
        if not n:
            continue
        if n.lower() in seen:
            raise ValueError(f"'{n}' is listed twice.")
        if len(n) > 60:
            raise ValueError(f"'{n[:20]}...' is too long (60 characters at most).")
        seen.add(n.lower())
        names.append(n)
    if not names:
        raise ValueError("Enter at least one class name.")
    if len(names) > MAX_CLASSES:
        raise ValueError(f"At most {MAX_CLASSES} classes can be set up here (you can add more later).")
    return names


def parse_exam_rows(post, count):
    """Read exam_name_i / exam_max_i for i in 1..count -> [(name, Decimal)] or raise ValueError."""
    rows, seen = [], set()
    for i in range(1, count + 1):
        name = " ".join(post.get(f"exam_name_{i}", "").split())
        raw = post.get(f"exam_max_{i}", "").strip()
        if not name:
            raise ValueError(f"Exam {i}: please enter a name.")
        if name.lower() in seen:
            raise ValueError(f"Exam name '{name}' is used twice.")
        seen.add(name.lower())
        try:
            mx = Decimal(raw)
        except InvalidOperation:
            raise ValueError(f"Exam {i} ('{name}'): maximum marks must be a number.")
        if not mx.is_finite() or mx <= 0 or mx > 1000:
            raise ValueError(f"Exam {i} ('{name}'): maximum marks must be between 1 and 1000.")
        rows.append((name, mx))
    return rows


def check_rules(use_grades, grade_text, show_division, division_text):
    if use_grades:
        if not parse_grade_bands(grade_text):
            raise RuleError("Add at least one grade line, or untick 'Show letter grades'.")
    if show_division:
        if not parse_divisions(division_text):
            raise RuleError("Add at least one division line, or untick 'Show divisions'.")


@transaction.atomic
def save_exam_templates(rows):
    ExamTemplate.objects.all().delete()
    for i, (name, mx) in enumerate(rows, 1):
        ExamTemplate.objects.create(code=f"E{i}", name=name, max_marks=mx, order=i)


@transaction.atomic
def apply_setup(*, institution_type, name, department="", footer="", level_label="Class", paper_label="Subject",
                classes, exams, pass_percent, use_grades=False, grade_bands=DEFAULT_GRADE_BANDS,
                show_division=False, division_rules=DEFAULT_DIVISIONS, signatures="Class Teacher, Principal",
                admin=None):
    """Create everything for a fresh install. `admin` = dict(username, password, full_name, email)."""
    if SiteConfig.objects.filter(setup_complete=True).exists():
        raise RuntimeError("The portal is already set up.")
    SiteConfig.objects.all().delete()
    site = SiteConfig.objects.create(
        institution_type=institution_type, institution_name=name, department_name=department,
        footer_text=footer, level_label=level_label, paper_label=paper_label, pass_percent=pass_percent,
        use_grades=use_grades, grade_bands=grade_bands, show_division=show_division,
        division_rules=division_rules, signatures=signatures, setup_complete=True)
    save_exam_templates(exams)
    Term.objects.all().delete()
    for order, cname in enumerate(classes, 1):
        Term.objects.create(name=cname, order=order)
    user = None
    if admin:
        first, _, last = (admin.get("full_name") or "").strip().partition(" ")
        User = get_user_model()
        user = User.objects.create_superuser(admin["username"], email=admin.get("email", ""),
                                             password=admin["password"], first_name=first, last_name=last)
    return site, user


def exam_summary(rows):
    return " + ".join(fmt(m) for _, m in rows) + f" = {fmt(sum((m for _, m in rows), Decimal(0)))}"
