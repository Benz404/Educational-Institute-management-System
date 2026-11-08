from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth import login as auth_login

from . import importers
from . import setup_logic
from .forms import (AdminAccountForm, CourseForm, CourseImportForm, ImportForm, InstitutionForm, LabelsForm,
                    PaperUploadForm, RulesForm, SiteSettingsForm, StudentAddForm, StudentForm, TeacherForm, TermForm)
from .models import (DEFAULT_DIVISIONS, DEFAULT_GRADE_BANDS, Allotment, Backlog, Course, Enrollment, Exam, ExamTemplate, Mark, MarkLog, Offering,
                     SiteConfig, Student, Term, fmt, log_mark_change)
from .pdf import render_sheets
from .permissions import (can_edit_exam, get_exam, get_offering, offerings_for, students_visible_to,
                          superuser_required)
from .services import (build_sheets, enroll, enroll_term_core, enrollment_result, exam_insights,
                       offering_summary, semester_sheets, student_sheets, sync_backlogs, with_related)

User = get_user_model()


def labels():
    site = SiteConfig.objects.first()
    if not site:
        return SimpleNamespace(paper="Subject", papers="Subjects", paper_l="subject", papers_l="subjects",
                               level="Class", levels="Classes", level_l="class", levels_l="classes")
    return SimpleNamespace(paper=site.paper_label, papers=site.paper_plural, paper_l=site.paper_label.lower(),
                           papers_l=site.paper_plural.lower(), level=site.level_label, levels=site.level_plural,
                           level_l=site.level_label.lower(), levels_l=site.level_plural.lower())


# ------------------------------------------------------------------ helpers
def current_session():
    s = Offering.objects.order_by("-session").values_list("session", flat=True).first()
    return s or Student.objects.order_by("-session").values_list("session", flat=True).first() or ""


def success(request, title, message, links):
    """Remember the outcome and show the dedicated success page."""
    from django.urls import reverse
    resolved = [(label, reverse(name, args=[arg] if arg is not None else [])) for label, name, arg in links]
    request.session["success"] = {"title": title, "message": message, "links": resolved}
    return redirect("success")


def teachers():
    return User.objects.filter(is_superuser=False, is_active=True).order_by("first_name", "username")


def pdf_response(data, filename):
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp


def error_403(request, exception=None):
    return render(request, "portal/error.html", {"code": 403, "title": "Not allowed",
                  "text": "You do not have permission to open this page."}, status=403)


def error_404(request, exception=None):
    return render(request, "portal/error.html", {"code": 404, "title": "Page not found",
                  "text": "We could not find what you were looking for."}, status=404)


# ------------------------------------------------------------- public pages
def index(request):
    return render(request, "portal/index.html")


@login_required
def success_page(request):
    data = request.session.pop("success", None)
    if not data:
        return redirect("dashboard")
    return render(request, "portal/success.html", data)


# ---------------------------------------------------------------- dashboard
@login_required
def dashboard(request):
    sessions = list(offerings_for(request.user).order_by("-session").values_list("session", flat=True).distinct())
    session = request.GET.get("session") or (sessions[0] if sessions else "")
    offerings = list(offerings_for(request.user).filter(session=session).annotate(n=Count("enrollments", distinct=True)))
    rows = []
    for o in offerings:
        entered = 0
        for ex in o.exams.all():
            have = ex.marks.filter(Q(marks__isnull=False) | Q(absent=True)).count()
            entered += 1 if o.n and have >= o.n else 0
        rows.append({"o": o, "exams_done": entered,
                     "open_backlogs": Backlog.objects.filter(course=o.course, status=Backlog.OPEN).count()})
    ctx = {"sessions": sessions, "session": session, "rows": rows}
    if request.user.is_superuser:
        ctx.update(n_students=Student.objects.filter(is_active=True).count(),
                   n_teachers=teachers().count(), n_papers=Offering.objects.filter(session=session).count(),
                   n_backlogs=Backlog.objects.filter(status=Backlog.OPEN).count())
    return render(request, "portal/dashboard.html", ctx)


# ----------------------------------------------------------------- students
@login_required
def student_list(request):
    qs = students_visible_to(request.user)
    q = request.GET.get("q", "").strip()
    term, session, section = request.GET.get("term", ""), request.GET.get("session", ""), request.GET.get("section", "")
    if q:
        qs = qs.filter(Q(admission_no__icontains=q) | Q(name__icontains=q) | Q(mobile__icontains=q))
    if term:
        qs = qs.filter(term_id=term)
    if session:
        qs = qs.filter(session=session)
    if section:
        qs = qs.filter(section=section)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    qd = request.GET.copy()
    qd.pop("page", None)
    return render(request, "portal/student_list.html", {
        "page": page, "terms": Term.objects.all(),
        "sessions": Student.objects.order_by("-session").values_list("session", flat=True).distinct(),
        "f": {"q": q, "term": term, "session": session, "section": section}, "qs": qd.urlencode()})


@login_required
def student_detail(request, pk):
    student = get_object_or_404(students_visible_to(request.user), pk=pk)
    sheets = student_sheets(student)
    backlogs = student.backlogs.select_related("course", "origin", "cleared_in")
    return render(request, "portal/student_detail.html", {
        "student": student, "sheets": sheets, "backlogs": backlogs,
        "open_backlogs": [b for b in backlogs if b.status == Backlog.OPEN]})


@superuser_required
def student_add(request):
    form = StudentAddForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        student = form.save()
        n = 0
        if cd["enrol_core"]:
            for off in Offering.objects.filter(session=student.session, course__term=student.term).select_related("course"):
                if off.course.compulsory:
                    n += enroll(student, off)[1]
        return success(request, "Student added",
                       f"{student.name} ({student.admission_no}) was added"
                       + (f" and enrolled in {n} paper(s)." if n else "."),
                       [("Add another student", "student_add", None), ("Open student profile", "student_detail", student.pk),
                        ("All students", "student_list", None)])
    return render(request, "portal/student_form.html", {"form": form, "title": "Add a student", "adding": True})


@superuser_required
def student_edit(request, pk):
    student = get_object_or_404(Student, pk=pk)
    form = StudentForm(request.POST or None, instance=student)
    if request.method == "POST":
        if "toggle_active" in request.POST:
            student.is_active = not student.is_active
            student.save()
            return success(request, "Student updated",
                           f"{student.name} is now {'active' if student.is_active else 'inactive'}.",
                           [("Open student profile", "student_detail", student.pk), ("All students", "student_list", None)])
        if form.is_valid():
            form.save()
            return success(request, "Student updated", f"Details of {student.name} were saved.",
                           [("Open student profile", "student_detail", student.pk), ("All students", "student_list", None)])
    return render(request, "portal/student_form.html", {"form": form, "title": f"Edit {student.name}", "student": student})


@superuser_required
def student_import(request):
    result = None
    form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            result = importers.import_students(cd["file"], cd["file"].name, cd["term"], cd["session"],
                                               stream=cd["stream"], section=cd["section"], auto_enroll=cd["auto_enroll"])
        except ValueError as exc:
            form.add_error("file", str(exc))
    return render(request, "portal/student_import.html", {"form": form, "result": result})


# ----------------------------------------------------------------- teachers
@superuser_required
def teacher_list(request):
    rows = [{"t": t, "papers": Offering.objects.filter(allotments__teacher=t).select_related("course").order_by("-session", "course__code")}
            for t in teachers()]
    return render(request, "portal/teacher_list.html", {"rows": rows})


@superuser_required
def teacher_add(request):
    form = TeacherForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        first, _, last = cd["full_name"].strip().partition(" ")
        user = User.objects.create_user(cd["username"], email=cd["email"], password=cd["password1"],
                                        first_name=first, last_name=last, is_staff=False)
        return success(request, "Teacher added",
                       f"Login created for {cd['full_name']} (username: {user.username}). "
                       f"Allot {labels().papers_l} to the teacher so they appear after login.",
                       [(f"Open {labels().papers_l} and allot", "offering_list", None), ("Add another teacher", "teacher_add", None),
                        ("All teachers", "teacher_list", None)])
    return render(request, "portal/teacher_form.html", {"form": form})


# ------------------------------------------------------------------ courses
@superuser_required
def course_list(request):
    qs = Course.objects.select_related("term")
    term = request.GET.get("term", "")
    if term:
        qs = qs.filter(term_id=term)
    return render(request, "portal/course_list.html", {"courses": qs, "terms": Term.objects.all(), "term": term})


@superuser_required
def course_add(request):
    form = CourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        c = form.save()
        L = labels()
        return success(request, f"{L.paper} added", f"{c.code} - {c.name} ({c.term}) was added.",
                       [(f"Open {L.papers_l} for a session", "offering_open", None), (f"Add another {L.paper_l}", "course_add", None),
                        (f"All {L.papers_l}", "course_list", None)])
    return render(request, "portal/course_form.html", {"form": form})


# ---------------------------------------------------------------- offerings
@login_required
def offering_list(request):
    qs = offerings_for(request.user)
    session, term = request.GET.get("session", ""), request.GET.get("term", "")
    if session:
        qs = qs.filter(session=session)
    if term:
        qs = qs.filter(course__term_id=term)
    qs = qs.annotate(n=Count("enrollments", distinct=True)).prefetch_related("allotments__teacher")
    sessions = offerings_for(request.user).order_by("-session").values_list("session", flat=True).distinct()
    return render(request, "portal/offering_list.html", {"offerings": qs, "sessions": sessions,
                  "terms": Term.objects.all(), "session": session, "term": term})


@superuser_required
def offering_open(request):
    session = (request.GET.get("session") or request.POST.get("session") or "").strip()
    term_id = request.GET.get("term") or request.POST.get("term") or ""
    terms = Term.objects.all()
    courses, term, error = [], None, ""
    if session and term_id:
        if len(session) != 7 or session[4] != "-" or not (session[:4] + session[5:]).isdigit():
            error = "Use the session format 2026-27."
        else:
            term = get_object_or_404(terms, pk=term_id)
            opened = set(Offering.objects.filter(session=session).values_list("course_id", flat=True))
            courses = [{"c": c, "opened": c.pk in opened} for c in Course.objects.filter(term=term)]
    if request.method == "POST" and courses and not error:
        picked = set(request.POST.getlist("course"))
        made, enrolled = 0, 0
        with transaction.atomic():
            for row in courses:
                c = row["c"]
                if str(c.pk) not in picked or row["opened"]:
                    continue
                off = Offering.objects.create(course=c, session=session)
                made += 1
                for tid in request.POST.getlist(f"teacher_{c.pk}"):
                    if tid:
                        Allotment.objects.get_or_create(offering=off, teacher_id=tid)
                if request.POST.get("enrol_core"):
                    enrolled += enroll_term_core(off)
        L = labels()
        return success(request, f"{L.papers} opened",
                       f"{made} {L.paper_l}(s) opened for session {session} ({term})"
                       + (f"; {enrolled} enrolment(s) created." if enrolled else "."),
                       [(f"View {L.papers_l}", "offering_list", None), (f"Open more {L.papers_l}", "offering_open", None)])
    return render(request, "portal/offering_open.html", {"terms": terms, "courses": courses, "session": session,
                  "term": term, "term_id": term_id, "teachers": teachers(), "error": error})


@login_required
def offering_detail(request, pk):
    offering = get_offering(request.user, pk)
    summary = offering_summary(offering)
    exams = list(offering.exams.all())
    for ex in exams:
        ex.insight = exam_insights(ex)
    chart = {"labels": [e["exam"].label for e in summary["exam_avgs"]],
             "values": [e["avg_pct"] if e["avg_pct"] is not None else 0 for e in summary["exam_avgs"]]}
    return render(request, "portal/offering_detail.html", {
        "o": offering, "summary": summary, "exams": exams, "chart": chart,
        "teachers": offering.teachers, "can_manage": request.user.is_superuser})


@superuser_required
def offering_allot(request, pk):
    offering = get_object_or_404(Offering.objects.select_related("course"), pk=pk)
    current = set(offering.allotments.values_list("teacher_id", flat=True))
    if request.method == "POST":
        chosen = {int(x) for x in request.POST.getlist("teacher") if x.isdigit()}
        valid = set(teachers().filter(pk__in=chosen).values_list("pk", flat=True))
        offering.allotments.exclude(teacher_id__in=valid).delete()
        for tid in valid:
            Allotment.objects.get_or_create(offering=offering, teacher_id=tid)
        return success(request, "Teachers allotted", f"{len(valid)} teacher(s) allotted to {offering.course.code} ({offering.session}).",
                       [("Open the paper", "offering_detail", offering.pk), ("All papers", "offering_list", None)])
    return render(request, "portal/offering_allot.html", {"o": offering, "teachers": teachers(), "current": current})


@superuser_required
def offering_enrol(request, pk):
    offering = get_object_or_404(Offering.objects.select_related("course__term"), pk=pk)
    if request.method == "POST":
        n = 0
        if "enrol_term" in request.POST:
            n = enroll_term_core(offering)
        else:
            for s in Student.objects.filter(pk__in=request.POST.getlist("student"), is_active=True):
                n += enroll(s, offering)[1]
        messages.success(request, f"{n} student(s) enrolled.")
        return redirect("offering_enrol", pk=pk)
    enrolled = offering.enrollments.values_list("student_id", flat=True)
    q = request.GET.get("q", "").strip()
    term = request.GET.get("term", str(offering.course.term_id))
    qs = Student.objects.filter(is_active=True).exclude(pk__in=enrolled).select_related("term")
    if term:
        qs = qs.filter(term_id=term)
    if q:
        qs = qs.filter(Q(admission_no__icontains=q) | Q(name__icontains=q))
    return render(request, "portal/offering_enrol.html", {"o": offering, "students": qs[:400], "q": q, "term": term,
                  "terms": Term.objects.all(), "enrolled_count": len(enrolled)})


@login_required
def offering_backlogs(request, pk):
    offering = get_offering(request.user, pk)
    enrolled = offering.enrollments.values_list("student_id", flat=True)
    pending = Backlog.objects.filter(course=offering.course, status=Backlog.OPEN).exclude(student_id__in=enrolled) \
        .select_related("student", "origin")
    if request.method == "POST":
        ids = set(request.POST.getlist("student"))
        n = 0
        for b in pending:
            if "all" in request.POST or str(b.student_id) in ids:
                n += enroll(b.student, offering, is_backlog=True)[1]
        messages.success(request, f"{n} backlog student(s) added.")
        return redirect("offering_detail", pk=pk)
    already = offering.enrollments.filter(is_backlog=True).select_related("student")
    return render(request, "portal/offering_backlogs.html", {"o": offering, "pending": pending, "already": already})


# -------------------------------------------------------------------- exams
@login_required
def exam_detail(request, pk):
    exam = get_exam(request.user, pk)
    ins = exam_insights(exam)
    return render(request, "portal/exam_detail.html", {
        "exam": exam, "o": exam.offering, "ins": ins, "upload_form": PaperUploadForm(),
        "editable": can_edit_exam(request.user, exam),
        "chart": {"labels": ins["bin_labels"], "values": ins["bin_counts"]}})


def _parse(raw, mx):
    raw = raw.strip()
    if raw == "":
        return None
    try:
        v = Decimal(raw)
    except InvalidOperation:
        raise ValueError("not a number")
    if not v.is_finite():
        raise ValueError("not a number")
    if v < 0 or v > mx:
        raise ValueError(f"must be 0 to {fmt(mx)}")
    return v.quantize(Decimal("0.01"))


@login_required
def exam_marks(request, pk):
    exam = get_exam(request.user, pk)
    editable = can_edit_exam(request.user, exam)
    enrollments = list(exam.offering.enrollments.select_related("student").prefetch_related("marks"))
    existing = {m.enrollment_id: m for e in enrollments for m in e.marks.all() if m.exam_id == exam.id}
    errors, posted = {}, {}
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        changes = []
        for e in enrollments:
            raw, ab = request.POST.get(f"m_{e.id}", ""), bool(request.POST.get(f"ab_{e.id}"))
            posted[e.id] = {"raw": raw, "ab": ab}
            try:
                value = None if ab else _parse(raw, exam.max_marks)
            except ValueError as exc:
                errors[e.id] = str(exc)
                continue
            cur = existing.get(e.id)
            old = cur.display() if cur else ""
            new = "AB" if ab else fmt(value)
            if old != new:
                changes.append((e, cur, value, ab, old, new))
        if errors:
            messages.error(request, f"{len(errors)} mark(s) are invalid, so nothing was saved. Fix the highlighted rows.")
        else:
            with transaction.atomic():
                for e, cur, value, ab, old, new in changes:
                    if new == "":
                        cur.delete()
                    else:
                        Mark.objects.update_or_create(enrollment=e, exam=exam, defaults={"marks": value, "absent": ab})
                    log_mark_change(request.user, e, exam, old, new)
                created, cleared = sync_backlogs(exam.offering)
            msg = f"Saved {exam.label} marks ({len(changes)} change(s))."
            if created:
                msg += f" {created} student(s) newly marked as backlog."
            if cleared:
                msg += f" {cleared} backlog(s) cleared."
            messages.success(request, msg)
            return redirect("exam_detail", pk=exam.pk)
    rows = []
    for e in enrollments:
        cur = existing.get(e.id)
        p = posted.get(e.id)
        rows.append({"e": e, "error": errors.get(e.id),
                     "value": p["raw"] if p else (fmt(cur.marks) if cur and not cur.absent else ""),
                     "ab": p["ab"] if p else bool(cur and cur.absent)})
    return render(request, "portal/exam_marks.html", {"exam": exam, "o": exam.offering, "rows": rows, "editable": editable})


@login_required
@require_POST
def exam_paper_upload(request, pk):
    exam = get_exam(request.user, pk)
    if not can_edit_exam(request.user, exam):
        raise PermissionDenied
    form = PaperUploadForm(request.POST, request.FILES)
    if form.is_valid():
        if exam.question_paper:
            exam.question_paper.delete(save=False)
        exam.question_paper = form.cleaned_data["file"]
        exam.paper_uploaded_by, exam.paper_uploaded_at = request.user, timezone.now()
        exam.save()
        messages.success(request, f"Question paper for {exam.label} uploaded.")
    else:
        messages.error(request, " ".join(form.errors["file"]) if "file" in form.errors else "Upload failed.")
    return redirect("exam_detail", pk=exam.pk)


@login_required
def exam_paper_view(request, pk):
    exam = get_exam(request.user, pk)
    if not exam.question_paper:
        raise Http404
    resp = FileResponse(exam.question_paper.open("rb"), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{exam.offering.course.code}-{exam.offering.session}-{exam.kind}.pdf"'
    return resp


@login_required
@require_POST
def exam_paper_remove(request, pk):
    exam = get_exam(request.user, pk)
    if not can_edit_exam(request.user, exam):
        raise PermissionDenied
    if exam.question_paper:
        exam.question_paper.delete(save=False)
        exam.question_paper = ""
        exam.paper_uploaded_by = exam.paper_uploaded_at = None
        exam.save()
        messages.success(request, "Question paper removed.")
    return redirect("exam_detail", pk=exam.pk)


@superuser_required
@require_POST
def exam_lock(request, pk):
    exam = get_object_or_404(Exam, pk=pk)
    exam.is_locked = not exam.is_locked
    exam.save()
    messages.success(request, f"{exam.label} is now {'locked (only you can edit)' if exam.is_locked else 'unlocked'}.")
    return redirect("exam_detail", pk=exam.pk)


# ---------------------------------------------------------------- marksheets
@superuser_required
def marksheets(request):
    sessions = list(Offering.objects.order_by("-session").values_list("session", flat=True).distinct())
    session = request.GET.get("session") or (sessions[0] if sessions else "")
    term_id = request.GET.get("term", "")
    sheets, term = [], None
    if session and term_id:
        term = get_object_or_404(Term, pk=term_id)
        sheets = semester_sheets(session, term)
    return render(request, "portal/marksheets.html", {"sessions": sessions, "session": session, "term": term,
                  "term_id": term_id, "terms": Term.objects.all(), "sheets": sheets})


@superuser_required
def marksheet_semester_pdf(request):
    session, term = request.GET.get("session", ""), get_object_or_404(Term, pk=request.GET.get("term"))
    sheets = semester_sheets(session, term)
    return pdf_response(render_sheets(sheets), f"marksheets-{term.name}-{session}.pdf".replace(" ", "_"))


@superuser_required
def marksheet_student_pdf(request, pk):
    student = get_object_or_404(Student, pk=pk)
    term_id, session = request.GET.get("term"), request.GET.get("session", "")
    sheets = student_sheets(student)
    if term_id:
        sheets = [s for s in sheets if str(s["term"].pk) == term_id and s["session"] == session]
        if not sheets:
            raise Http404
    name = student.admission_no.replace("/", "-")
    return pdf_response(render_sheets(sheets, backlog_note_for=None if term_id else student),
                        f"marksheet-{name}{'' if term_id else '-complete'}.pdf")


# ------------------------------------------------------------------ backlogs
@login_required
def backlog_list(request):
    qs = Backlog.objects.select_related("student", "course__term", "origin", "cleared_in")
    if not request.user.is_superuser:
        qs = qs.filter(course__offerings__allotments__teacher=request.user).distinct()
    status = request.GET.get("status", "OPEN")
    term = request.GET.get("term", "")
    if status in ("OPEN", "CLEARED"):
        qs = qs.filter(status=status)
    if term:
        qs = qs.filter(course__term_id=term)
    return render(request, "portal/backlog_list.html", {"backlogs": qs, "status": status, "term": term,
                  "terms": Term.objects.all()})


@superuser_required
def audit_log(request):
    q = request.GET.get("q", "").strip()
    logs = MarkLog.objects.select_related("changed_by")
    if q:
        logs = logs.filter(Q(admission_no__icontains=q) | Q(paper__icontains=q))
    return render(request, "portal/audit_log.html", {"page": Paginator(logs, 100).get_page(request.GET.get("page")), "q": q})


# ================================================================ one-time setup
def _setup_guard(request):
    if SiteConfig.objects.filter(setup_complete=True).exists():
        raise Http404
    if not settings.SETUP_ANYWHERE and request.META.get("REMOTE_ADDR") not in ("127.0.0.1", "::1"):
        return render(request, "portal/setup_local_only.html", status=403)
    return None


def setup(request):
    blocked = _setup_guard(request)
    return blocked or redirect("setup_step", step=1)


def setup_step(request, step):
    blocked = _setup_guard(request)
    if blocked:
        return blocked
    data = request.session.setdefault("setup", {})
    if step not in (1, 2, 3, 4):
        raise Http404
    if step > 1 and "name" not in data:
        return redirect("setup_step", step=1)
    if step > 2 and "classes_text" not in data:
        return redirect("setup_step", step=2)
    if step > 3 and "exams" not in data:
        return redirect("setup_step", step=3)
    kind = data.get("institution_type", "COLLEGE")
    defaults = setup_logic.TYPE_DEFAULTS[kind]
    ctx = {"step": step, "steps": [(1, "Institution"), (2, "Classes"), (3, "Exams & rules"), (4, "Admin account")]}

    if step == 1:
        form = InstitutionForm(request.POST or None, initial={
            "institution_type": data.get("institution_type", "COLLEGE"), "name": data.get("name", ""),
            "department": data.get("department", ""), "footer": data.get("footer", "")})
        if request.method == "POST" and form.is_valid():
            cd = form.cleaned_data
            if cd["institution_type"] != data.get("institution_type"):      # type changed: refresh the suggestions
                for k in ("level_label", "paper_label", "classes_text", "exams", "use_grades", "show_division", "signatures"):
                    data.pop(k, None)
            data.update(institution_type=cd["institution_type"], name=cd["name"], department=cd["department"], footer=cd["footer"])
            request.session.modified = True
            return redirect("setup_step", step=2)
        ctx["form"] = form

    elif step == 2:
        form = LabelsForm(request.POST or None, initial={
            "level_label": data.get("level_label", defaults["level_label"]),
            "paper_label": data.get("paper_label", defaults["paper_label"]),
            "classes_text": data.get("classes_text", setup_logic.generate_names(defaults["prefix"], defaults["count"]))})
        if request.method == "POST" and form.is_valid():
            cd = form.cleaned_data
            try:
                names = setup_logic.parse_class_names(cd["classes_text"])
            except ValueError as exc:
                form.add_error("classes_text", str(exc))
            else:
                data.update(level_label=cd["level_label"].strip(), paper_label=cd["paper_label"].strip(),
                            classes_text="\n".join(names))
                request.session.modified = True
                return redirect("setup_step", step=3)
        ctx.update(form=form, prefix=defaults["prefix"])

    elif step == 3:
        exam_rows = data.get("exams") or [list(r) for r in setup_logic.default_exam_rows(3)]
        form = RulesForm(request.POST or None, initial={
            "exam_count": len(exam_rows), "pass_percent": data.get("pass_percent", 40),
            "use_grades": data.get("use_grades", defaults["use_grades"]), "grade_bands": data.get("grade_bands", DEFAULT_GRADE_BANDS),
            "show_division": data.get("show_division", defaults["show_division"]),
            "division_rules": data.get("division_rules", DEFAULT_DIVISIONS),
            "signatures": data.get("signatures", defaults["signatures"])})
        if request.method == "POST":
            try:
                count = int(request.POST.get("exam_count", "0"))
            except ValueError:
                count = 0
            if form.is_valid():
                try:
                    rows = setup_logic.parse_exam_rows(request.POST, form.cleaned_data["exam_count"])
                except ValueError as exc:
                    form.add_error(None, str(exc))
                    exam_rows = [[request.POST.get(f"exam_name_{i}", ""), request.POST.get(f"exam_max_{i}", "")] for i in range(1, count + 1)]
                else:
                    cd = form.cleaned_data
                    data.update(exams=[[n, str(m)] for n, m in rows], pass_percent=str(cd["pass_percent"]),
                                use_grades=cd["use_grades"], grade_bands=cd["grade_bands"], show_division=cd["show_division"],
                                division_rules=cd["division_rules"], signatures=cd["signatures"])
                    request.session.modified = True
                    return redirect("setup_step", step=4)
            else:
                exam_rows = [[request.POST.get(f"exam_name_{i}", ""), request.POST.get(f"exam_max_{i}", "")] for i in range(1, max(count, 1) + 1)]
        padded = list(exam_rows) + [["", ""]] * (setup_logic.MAX_EXAMS - len(exam_rows))
        ctx.update(form=form, exam_slots=[(i, r[0], r[1]) for i, r in enumerate(padded[:setup_logic.MAX_EXAMS], 1)],
                   max_exams=setup_logic.MAX_EXAMS, level=data.get("level_label", "class"))

    else:
        form = AdminAccountForm(request.POST or None)
        exams = [(n, Decimal(m)) for n, m in data["exams"]]
        if request.method == "POST" and form.is_valid():
            cd = form.cleaned_data
            site, user = setup_logic.apply_setup(
                institution_type=data["institution_type"], name=data["name"], department=data.get("department", ""),
                footer=data.get("footer", ""), level_label=data["level_label"], paper_label=data["paper_label"],
                classes=data["classes_text"].splitlines(), exams=exams, pass_percent=Decimal(data["pass_percent"]),
                use_grades=data["use_grades"], grade_bands=data["grade_bands"], show_division=data["show_division"],
                division_rules=data["division_rules"], signatures=data["signatures"],
                admin={"username": cd["username"], "password": cd["password1"], "full_name": cd["full_name"], "email": cd["email"]})
            request.session.pop("setup", None)
            auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return success(request, "Setup complete",
                           f"{site.institution_name} is ready. You are logged in as the superuser. "
                           "Next: add your logo in Settings, add teachers, then open subjects for a session.",
                           [("Institution settings (add your logo)", "site_settings", None), ("Add a teacher", "teacher_add", None),
                            (f"Add or import {site.paper_plural.lower()}", "course_list", None)])
        ctx.update(form=form, summary={
            "type": dict(SiteConfig.TYPE_CHOICES)[data["institution_type"]], "name": data["name"], "department": data.get("department"),
            "classes": data["classes_text"].splitlines(), "level": data["level_label"], "paper": data["paper_label"],
            "exams": exams, "exam_total": fmt(sum((m for _, m in exams), Decimal(0))), "pass_percent": data["pass_percent"],
            "grades": data["use_grades"], "division": data["show_division"]})
    return render(request, "portal/setup.html", ctx)


# ======================================================== institution settings
@superuser_required
def site_settings(request):
    site = SiteConfig.objects.get()
    locked = Offering.objects.exists()
    exam_rows = [[e.name, fmt(e.max_marks)] for e in ExamTemplate.objects.all()]
    form = SiteSettingsForm(request.POST or None, request.FILES or None, instance=site)
    if request.method == "POST":
        rows, row_error = None, None
        if not locked:
            try:
                rows = setup_logic.parse_exam_rows(request.POST, int(request.POST.get("exam_count", "0") or 0))
                if not rows:
                    raise ValueError("Add at least one exam.")
            except ValueError as exc:
                row_error = str(exc)
        if form.is_valid() and not row_error:
            old_logo = site.logo.name if site.logo else ""
            obj = form.save(commit=False)
            if form.cleaned_data.get("remove_logo") and old_logo:
                obj.logo = ""
            obj.save()
            if old_logo and old_logo != (obj.logo.name if obj.logo else ""):
                try:
                    obj.logo.storage.delete(old_logo)
                except Exception:
                    pass
            if rows is not None:
                setup_logic.save_exam_templates(rows)
            messages.success(request, "Settings saved.")
            return redirect("site_settings")
        if row_error:
            messages.error(request, row_error)
        if rows is None and not locked:
            exam_rows = [[request.POST.get(f"exam_name_{i}", ""), request.POST.get(f"exam_max_{i}", "")] for i in range(1, 7)]
    padded = list(exam_rows) + [["", ""]] * (setup_logic.MAX_EXAMS - len(exam_rows))
    return render(request, "portal/site_settings.html", {
        "form": form, "locked": locked, "exam_count": len(exam_rows), "max_exams": setup_logic.MAX_EXAMS,
        "exam_slots": [(i, r[0], r[1]) for i, r in enumerate(padded[:setup_logic.MAX_EXAMS], 1)],
        "exam_summary": " + ".join(f"{n} ({m})" for n, m in exam_rows)})


def branding_logo(request):
    site = SiteConfig.objects.first()
    if not site or not site.logo:
        raise Http404
    ctype = "image/png" if site.logo.name.lower().endswith(".png") else "image/jpeg"
    resp = FileResponse(site.logo.open("rb"), content_type=ctype)
    resp["Cache-Control"] = "public, max-age=300"
    return resp


# ================================================================ class levels
@superuser_required
def class_list(request):
    form = TermForm(request.POST or None, initial={"order": Term.objects.count() + 1})
    if request.method == "POST" and form.is_valid():
        t = form.save()
        messages.success(request, f"{t.name} added.")
        return redirect("class_list")
    terms = Term.objects.annotate(n_students=Count("students", distinct=True), n_courses=Count("courses", distinct=True))
    return render(request, "portal/class_list.html", {"terms": terms, "form": form})


@superuser_required
def class_edit(request, pk):
    term = get_object_or_404(Term, pk=pk)
    used = term.students.exists() or term.courses.exists()
    form = TermForm(request.POST or None, instance=term)
    if request.method == "POST":
        if "delete" in request.POST:
            if used:
                messages.error(request, "This class has students or subjects, so it cannot be deleted.")
            else:
                name = term.name
                term.delete()
                messages.success(request, f"{name} deleted.")
                return redirect("class_list")
        elif form.is_valid():
            form.save()
            messages.success(request, "Class saved.")
            return redirect("class_list")
    return render(request, "portal/class_edit.html", {"form": form, "term": term, "used": used})


# =========================================================== subject import
@superuser_required
def course_import(request):
    result = None
    form = CourseImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = importers.import_courses(form.cleaned_data["file"], form.cleaned_data["file"].name)
        except ValueError as exc:
            form.add_error("file", str(exc))
    return render(request, "portal/course_import.html", {"form": form, "result": result})


@superuser_required
def course_sample_csv(request):
    import csv
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="subjects-sample.csv"'
    w = csv.writer(resp)
    w.writerow(["Class", "Code", "Name", "Credits", "Compulsory"])
    first = list(Term.objects.all()[:2]) or [SimpleNamespace(name="Class 1")]
    for t in first:
        w.writerow([t.name, "MAT-1", "Mathematics", "", "yes"])
        w.writerow([t.name, "ENG-1", "English", "", "yes"])
        w.writerow([t.name, "ART-1", "Art (optional)", "", "no"])
    return resp
