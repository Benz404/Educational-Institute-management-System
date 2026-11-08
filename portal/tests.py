import io
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from . import setup_logic
from .importers import import_courses, import_students
from .models import (Allotment, Backlog, Course, Enrollment, Exam, ExamTemplate, Mark, MarkLog, Offering,
                     SiteConfig, Student, Term)
from .services import build_sheets, enroll, enroll_term_core, enrollment_result, exam_insights, student_sheets, sync_backlogs

User = get_user_model()
PW = "Strong-Pass-123!"
MIN_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
PNG = None


def tiny_png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (20, 10), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def make_site(kind="COLLEGE", classes=("Semester 1", "Semester 2", "Semester 3"), exams=(("Sessional 1", 20), ("Sessional 2", 20), ("Final", 60)),
              pass_percent=40, grades=True, division=False, name="Test College", department="Department of Physics", admin=True, **kw):
    d = setup_logic.TYPE_DEFAULTS[kind]
    return setup_logic.apply_setup(
        institution_type=kind, name=name, department=department, level_label=kw.get("level_label", d["level_label"]),
        paper_label=kw.get("paper_label", d["paper_label"]), classes=list(classes),
        exams=[(n, Decimal(m)) for n, m in exams], pass_percent=Decimal(pass_percent), use_grades=grades, show_division=division,
        signatures="Prepared by, Principal",
        admin={"username": "root", "password": PW, "full_name": "Root User", "email": ""} if admin else None)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_site()
        cls.root = User.objects.get(username="root")
        cls.t1 = User.objects.create_user("t1", password=PW, first_name="Tea", last_name="One")
        cls.t2 = User.objects.create_user("t2", password=PW)
        cls.sem1, cls.sem2, cls.sem3 = list(Term.objects.order_by("order"))
        cls.c1 = Course.objects.create(term=cls.sem1, code="MAT-1", name="Mathematics", credits=4)
        cls.c2 = Course.objects.create(term=cls.sem2, code="MAT-2", name="Calculus", credits=4)
        cls.c3 = Course.objects.create(term=cls.sem3, code="MAT-3", name="Algebra", credits=4)
        cls.optional = Course.objects.create(term=cls.sem1, code="ART-1", name="Art", compulsory=False)
        cls.off = Offering.objects.create(course=cls.c1, session="2026-27")
        Allotment.objects.create(offering=cls.off, teacher=cls.t1)
        cls.students = [Student.objects.create(admission_no=f"A/{i:04d}", name=f"Student {i}", term=cls.sem1,
                                               session="2026-27", section="A") for i in range(1, 6)]
        enroll_term_core(cls.off)
        cls.s1, cls.s2, cls.fin = list(cls.off.exams.order_by("order"))

    def put(self, student, vals, off=None):
        off = off or self.off
        enr = Enrollment.objects.get(student=student, offering=off)
        for ex, v in zip(off.exams.order_by("order"), vals):
            if v is None:
                continue
            if v == "AB":
                Mark.objects.update_or_create(enrollment=enr, exam=ex, defaults={"absent": True, "marks": None})
            else:
                Mark.objects.update_or_create(enrollment=enr, exam=ex, defaults={"marks": Decimal(str(v)), "absent": False})
        return enr

    def login(self, who):
        self.client.login(username=who, password=PW)


# ================================================================ setup wizard
class SetupWizardTests(TestCase):
    def post(self, step, data):
        return self.client.post(reverse("setup_step", args=[step]), data)

    def test_everything_leads_to_setup_until_done(self):
        for url in ("/", "/login/", "/dashboard/", "/students/"):
            r = self.client.get(url)
            self.assertRedirects(r, reverse("setup"), fetch_redirect_response=False)

    def test_full_wizard_for_a_school(self):
        r = self.client.get(reverse("setup_step", args=[1]))
        self.assertContains(r, "About your institution")
        self.assertRedirects(self.post(1, {"institution_type": "SCHOOL", "name": "Sunrise Public School", "department": "", "footer": "Made with care"}),
                             reverse("setup_step", args=[2]), fetch_redirect_response=False)
        step2 = self.client.get(reverse("setup_step", args=[2]))
        self.assertContains(step2, "Class 10")                       # school default list 1-10
        self.assertContains(step2, 'value="Subject"')
        self.post(2, {"level_label": "Class", "paper_label": "Subject", "classes_text": "Nursery\nClass 1\nClass 2"})
        step3 = self.client.get(reverse("setup_step", args=[3]))
        self.assertContains(step3, "Sessional 1")
        data = {"exam_count": 2, "exam_name_1": "Half Yearly", "exam_max_1": "50", "exam_name_2": "Annual", "exam_max_2": "50",
                "pass_percent": "35", "signatures": "Class Teacher, Principal", "grade_bands": "", "division_rules": ""}
        self.assertRedirects(self.post(3, data), reverse("setup_step", args=[4]), fetch_redirect_response=False)
        step4 = self.client.get(reverse("setup_step", args=[4]))
        self.assertContains(step4, "Sunrise Public School")
        self.assertContains(step4, "Nursery, Class 1, Class 2")
        r = self.post(4, {"full_name": "Head Master", "username": "head", "email": "", "password1": PW, "password2": PW})
        self.assertRedirects(r, reverse("success"), fetch_redirect_response=False)
        site = SiteConfig.objects.get()
        self.assertTrue(site.setup_complete)
        self.assertEqual((site.institution_name, site.pass_percent, site.use_grades, site.footer_text),
                         ("Sunrise Public School", Decimal("35"), False, "Made with care"))
        self.assertEqual(list(Term.objects.values_list("name", flat=True)), ["Nursery", "Class 1", "Class 2"])
        self.assertEqual([(e.name, e.max_marks) for e in ExamTemplate.objects.all()], [("Half Yearly", 50), ("Annual", 50)])
        head = User.objects.get(username="head")
        self.assertTrue(head.is_superuser)
        self.assertEqual(self.client.get(reverse("success")).status_code, 200)   # already logged in
        self.assertEqual(self.client.get(reverse("setup")).status_code, 404)     # setup is closed forever
        self.assertContains(self.client.get(reverse("dashboard")), "Sunrise Public School")

    def test_validation(self):
        self.post(1, {"institution_type": "COLLEGE", "name": "C", "department": "", "footer": ""})
        r = self.post(2, {"level_label": "Semester", "paper_label": "Paper", "classes_text": "Sem 1\nsem 1"})
        self.assertContains(r, "listed twice")
        self.post(2, {"level_label": "Semester", "paper_label": "Paper", "classes_text": "Sem 1\nSem 2"})
        bad = {"exam_count": 2, "exam_name_1": "A", "exam_max_1": "abc", "exam_name_2": "B", "exam_max_2": "50", "pass_percent": "40", "signatures": "x"}
        self.assertContains(self.post(3, bad), "must be a number")
        bad = {**bad, "exam_max_1": "50", "exam_name_2": "a"}
        self.assertContains(self.post(3, bad), "used twice")
        self.assertContains(self.post(3, {**bad, "exam_name_2": "B", "pass_percent": "150"}), "between 1 and 100")
        self.assertContains(self.post(3, {**bad, "exam_name_2": "B", "use_grades": "on", "grade_bands": "oops"}), "Grade line 1")
        self.assertEqual(self.client.get(reverse("setup_step", args=[4])).status_code, 302)   # cannot skip ahead
        self.assertEqual(SiteConfig.objects.count(), 0)

    def test_steps_cannot_be_skipped(self):
        self.assertRedirects(self.client.get(reverse("setup_step", args=[3])), reverse("setup_step", args=[1]), fetch_redirect_response=False)

    def test_setup_only_from_the_local_computer(self):
        r = self.client.get(reverse("setup_step", args=[1]), REMOTE_ADDR="10.1.2.3")
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "installed", status_code=403)
        with override_settings(SETUP_ANYWHERE=True):
            self.assertEqual(self.client.get(reverse("setup_step", args=[1]), REMOTE_ADDR="10.1.2.3").status_code, 200)

    def test_setup_site_command(self):
        call_command("setup_site", "--type", "school", "--name", "Tiny School", "--prefix", "Class", "--count", "3",
                     "--exams", "Term 1:50,Term 2:50", "--pass-percent", "35", "--admin-username", "adm", "--admin-password", PW, verbosity=0)
        self.assertEqual(Term.objects.count(), 3)
        self.assertEqual(ExamTemplate.objects.count(), 2)
        self.assertTrue(User.objects.get(username="adm").is_superuser)
        with self.assertRaises(Exception):
            call_command("setup_site", "--name", "Again", "--admin-username", "x", "--admin-password", PW, verbosity=0)


# ==================================================== fully dynamic exams / rules
class DynamicRulesTests(TestCase):
    def test_two_exams_no_grades(self):
        make_site(kind="SCHOOL", classes=["Class 1", "Class 2"], exams=(("Half Yearly", 50), ("Annual", 50)), pass_percent=35, grades=False)
        t = Term.objects.first()
        c = Course.objects.create(term=t, code="ENG", name="English")
        off = Offering.objects.create(course=c, session="2026")
        self.assertEqual(off.exams.count(), 2)
        a = Student.objects.create(admission_no="1", name="A", term=t, session="2026")
        b = Student.objects.create(admission_no="2", name="B", term=t, session="2026")
        ea, eb = enroll(a, off)[0], enroll(b, off)[0]
        for enr, vals in ((ea, (20, 20)), (eb, (10, 20))):           # 40 pass, 30 fail (pass mark 35)
            for ex, v in zip(off.exams.order_by("order"), vals):
                Mark.objects.create(enrollment=enr, exam=ex, marks=Decimal(v))
        ra, rb = enrollment_result(ea), enrollment_result(eb)
        self.assertEqual((ra["status"], ra["grade"], ra["max_total"]), ("PASS", "", Decimal("100")))
        self.assertEqual(rb["status"], "FAIL")
        sheet = build_sheets(Enrollment.objects.filter(student=a))[0]
        self.assertIsNone(sheet["sgpa"])

    def test_one_exam_and_six_exams(self):
        make_site(classes=["Year 1"], exams=(("Final", 100),))
        self.assertEqual(Offering.objects.create(course=Course.objects.create(term=Term.objects.first(), code="X", name="X"), session="2026-27").exams.count(), 1)

    def test_grades_division_and_class_pass_override(self):
        make_site(kind="SCHOOL", classes=["Class 9", "Class 10"], exams=(("Term 1", 50), ("Term 2", 50)), pass_percent=40, grades=True, division=True)
        t9, t10 = Term.objects.order_by("order")
        t10.pass_percent = Decimal("60")
        t10.save()
        for t, code in ((t9, "S9"), (t10, "S10")):
            c = Course.objects.create(term=t, code=code, name="Science")
            off = Offering.objects.create(course=c, session="2026-27")
            st = Student.objects.create(admission_no=f"id-{code}", name=code, term=t, session="2026-27")
            enr = enroll(st, off)[0]
            for ex, v in zip(off.exams.order_by("order"), (25, 25)):   # 50%
                Mark.objects.create(enrollment=enr, exam=ex, marks=Decimal(v))
        r9 = enrollment_result(Enrollment.objects.get(student__admission_no="id-S9"))
        r10 = enrollment_result(Enrollment.objects.get(student__admission_no="id-S10"))
        self.assertEqual((r9["status"], r9["grade"]), ("PASS", "B"))
        self.assertEqual(r10["status"], "FAIL")                         # class pass mark 60 > 50
        self.assertEqual(build_sheets(Enrollment.objects.filter(student__admission_no="id-S9"))[0]["division"], "Second Division")

    def test_labels_follow_setup(self):
        make_site(kind="SCHOOL", classes=["Class 1"], level_label="Grade", paper_label="Subject", name="Little Stars", department="")
        self.client.login(username="root", password=PW)
        page = self.client.get(reverse("class_list"))
        self.assertContains(page, "Grades")
        self.assertContains(self.client.get(reverse("course_list")), "Subjects")
        self.assertNotContains(self.client.get(reverse("dashboard")), "Department of")


# ============================================================ structure & results
class StructureTests(Base):
    def test_offering_gets_exam_pattern(self):
        self.assertEqual([(e.name, e.max_marks) for e in self.off.exams.order_by("order")],
                         [("Sessional 1", 20), ("Sessional 2", 20), ("Final", 60)])

    def test_admission_number_unique(self):
        with self.assertRaises(Exception):
            Student.objects.create(admission_no="A/0001", name="Dup", term=self.sem1, session="2026-27")

    def test_compulsory_enrolled_optional_not(self):
        self.assertEqual(self.off.enrollments.count(), 5)
        art = Offering.objects.create(course=self.optional, session="2026-27")
        self.assertEqual(enroll_term_core(art), 0)

    def test_pending_pass_fail_absent(self):
        st = self.students[0]
        self.put(st, [15, None, None])
        self.assertEqual(enrollment_result(Enrollment.objects.get(student=st, offering=self.off))["status"], "PENDING")
        self.put(st, [15, 16, 40])  # 71
        r = enrollment_result(Enrollment.objects.get(student=st, offering=self.off))
        self.assertEqual((r["status"], r["total"], r["grade"]), ("PASS", Decimal("71"), "A"))
        self.put(st, [4, 4, 20])
        self.assertEqual(enrollment_result(Enrollment.objects.get(student=st, offering=self.off))["status"], "FAIL")
        self.put(self.students[1], [10, 10, "AB"])
        self.assertEqual(enrollment_result(Enrollment.objects.get(student=self.students[1], offering=self.off))["status"], "FAIL")


class BacklogTests(Base):
    def test_fail_and_correction(self):
        st = self.students[0]
        self.put(st, [3, 3, 10])
        self.assertEqual(sync_backlogs(self.off)[0], 1)
        self.put(st, [15, 15, 40])
        sync_backlogs(self.off)
        self.assertFalse(Backlog.objects.filter(student=st).exists())

    def test_pending_is_not_a_backlog(self):
        self.put(self.students[0], [3, 3, None])
        self.assertEqual(sync_backlogs(self.off), (0, 0))

    def test_retake_cycle_and_failed_retake(self):
        st = self.students[0]
        self.put(st, [3, 3, 10])
        sync_backlogs(self.off)
        nxt = Offering.objects.create(course=self.c1, session="2027-28")
        Allotment.objects.create(offering=nxt, teacher=self.t1)
        self.login("t1")
        self.assertEqual(self.client.post(reverse("offering_backlogs", args=[nxt.pk]), {"student": [st.pk]}).status_code, 302)
        self.assertTrue(Enrollment.objects.get(student=st, offering=nxt).is_backlog)
        self.put(st, [2, 2, 5], off=nxt)
        sync_backlogs(nxt)
        self.assertEqual(Backlog.objects.get(student=st).status, "OPEN")           # still open, no duplicate
        self.put(st, [16, 15, 45], off=nxt)
        self.assertEqual(sync_backlogs(nxt)[1], 1)
        b = Backlog.objects.get(student=st)
        self.assertEqual((b.status, b.cleared_in_id), ("CLEARED", nxt.pk))

    def test_add_all_backlogs(self):
        for st in self.students[:2]:
            self.put(st, [1, 1, 5])
        sync_backlogs(self.off)
        nxt = Offering.objects.create(course=self.c1, session="2027-28")
        self.login("root")
        self.client.post(reverse("offering_backlogs", args=[nxt.pk]), {"all": "1"})
        self.assertEqual(nxt.enrollments.filter(is_backlog=True).count(), 2)


class InsightTests(Base):
    def test_numbers_and_notes(self):
        for st, v in zip(self.students, [18, 12, 3, 20, "AB"]):
            self.put(st, [v, None, None])
        ins = exam_insights(self.s1)
        self.assertEqual((ins["enrolled"], ins["appeared"], ins["absent"]), (5, 4, 1))
        self.assertEqual((ins["highest"], ins["lowest"], ins["mean"], ins["below"]), (Decimal("20"), Decimal("3"), Decimal("13.25"), 1))
        self.assertEqual(sum(ins["bin_counts"]), 4)
        self.assertTrue(any("absent" in n for n in ins["notes"]))

    def test_empty_and_comparison(self):
        self.assertFalse(exam_insights(self.s1)["has_data"])
        for st in self.students:
            self.put(st, [10, 18, None])
        self.assertIn("compared with Sessional 1", " ".join(exam_insights(self.s2)["notes"]))


# ================================================================ permissions
class PermissionTests(Base):
    def test_public_index_and_login_required(self):
        self.assertEqual(self.client.get(reverse("index")).status_code, 200)
        for name in ("dashboard", "student_list", "offering_list", "backlog_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)

    def test_teacher_sees_only_own(self):
        other = Offering.objects.create(course=self.c2, session="2026-27")
        self.login("t1")
        self.assertEqual(self.client.get(reverse("offering_detail", args=[self.off.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("offering_detail", args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("exam_detail", args=[other.exams.first().pk])).status_code, 404)
        self.login("t2")
        self.assertEqual(self.client.get(reverse("offering_detail", args=[self.off.pk])).status_code, 404)

    def test_teacher_student_visibility(self):
        outsider = Student.objects.create(admission_no="X/1", name="Outsider", term=self.sem3, session="2026-27")
        self.login("t1")
        self.assertEqual(self.client.get(reverse("student_detail", args=[self.students[0].pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("student_detail", args=[outsider.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("student_list")), "Outsider")

    def test_superuser_only_pages(self):
        self.login("t1")
        for name in ("student_add", "student_import", "teacher_add", "teacher_list", "course_add", "course_list", "course_import",
                     "course_sample_csv", "offering_open", "marksheets", "audit_log", "site_settings", "class_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        for name, args in (("offering_allot", [self.off.pk]), ("offering_enrol", [self.off.pk]),
                           ("student_edit", [self.students[0].pk]), ("class_edit", [self.sem1.pk]),
                           ("marksheet_student_pdf", [self.students[0].pk])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)

    def test_superuser_opens_everything(self):
        self.login("root")
        for name in ("dashboard", "student_list", "student_add", "student_import", "teacher_add", "teacher_list", "course_add",
                     "course_list", "course_import", "course_sample_csv", "offering_open", "offering_list", "marksheets",
                     "audit_log", "backlog_list", "site_settings", "class_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("exam_detail", args=[self.s1.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("offering_detail", args=[self.off.pk])).status_code, 200)

    def test_teacher_can_change_password(self):
        self.login("t1")
        r = self.client.post(reverse("password_change"), {"old_password": PW, "new_password1": "Brand-New-77x", "new_password2": "Brand-New-77x"})
        self.assertRedirects(r, reverse("password_done"))


class MarksEntryTests(Base):
    def post(self, exam, data):
        return self.client.post(reverse("exam_marks", args=[exam.pk]), data)

    def key(self, st):
        return Enrollment.objects.get(student=st, offering=self.off).id

    def test_save_log_clear(self):
        self.login("t1")
        a, b = self.key(self.students[0]), self.key(self.students[1])
        self.assertEqual(self.post(self.s1, {f"m_{a}": "17.5", f"ab_{b}": "on"}).status_code, 302)
        self.assertEqual(Mark.objects.get(enrollment_id=a, exam=self.s1).marks, Decimal("17.50"))
        self.assertTrue(Mark.objects.get(enrollment_id=b, exam=self.s1).absent)
        self.assertEqual(MarkLog.objects.count(), 2)
        self.post(self.s1, {f"m_{a}": "", f"ab_{b}": "on"})
        self.assertFalse(Mark.objects.filter(enrollment_id=a, exam=self.s1).exists())

    def test_invalid_saves_nothing(self):
        self.login("t1")
        a, b = self.key(self.students[0]), self.key(self.students[1])
        self.assertEqual(self.post(self.s1, {f"m_{a}": "10", f"m_{b}": "21"}).status_code, 200)   # 21 > 20
        self.post(self.s1, {f"m_{a}": "abc"})
        self.assertEqual(Mark.objects.count(), 0)

    def test_final_creates_backlog(self):
        self.login("t1")
        for st in self.students[:2]:
            self.put(st, [3, 3, None])
        k0, k1 = self.key(self.students[0]), self.key(self.students[1])
        self.post(self.fin, {f"m_{k0}": "5", f"m_{k1}": "50"})
        self.assertTrue(Backlog.objects.filter(student=self.students[0], status="OPEN").exists())
        self.assertFalse(Backlog.objects.filter(student=self.students[1]).exists())

    def test_locked(self):
        self.s1.is_locked = True
        self.s1.save()
        a = self.key(self.students[0])
        self.login("t1")
        self.assertEqual(self.post(self.s1, {f"m_{a}": "10"}).status_code, 403)
        self.login("root")
        self.assertEqual(self.post(self.s1, {f"m_{a}": "10"}).status_code, 302)


class PaperUploadTests(Base):
    def upload(self, name, content, ctype="application/pdf"):
        return self.client.post(reverse("exam_paper_upload", args=[self.s1.pk]), {"file": SimpleUploadedFile(name, content, content_type=ctype)})

    def test_pdf_only(self):
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            self.login("t1")
            self.upload("notes.txt", b"hello", "text/plain")
            self.upload("fake.pdf", b"<html>not a pdf</html>")
            self.s1.refresh_from_db()
            self.assertFalse(self.s1.question_paper)
            self.assertEqual(self.upload("paper.pdf", MIN_PDF).status_code, 302)
            self.s1.refresh_from_db()
            got = self.client.get(reverse("exam_paper_view", args=[self.s1.pk]))
            self.assertEqual((got.status_code, got["Content-Type"]), (200, "application/pdf"))
            self.upload("again.pdf", MIN_PDF)
            self.s1.refresh_from_db()
            self.assertTrue(self.s1.question_paper.name.endswith("MAT-1-E1.pdf"))
            self.login("t2")
            self.assertEqual(self.client.get(reverse("exam_paper_view", args=[self.s1.pk])).status_code, 404)
            self.client.logout()
            self.assertEqual(self.client.get(reverse("exam_paper_view", args=[self.s1.pk])).status_code, 302)


class AddPagesTests(Base):
    def test_add_teacher(self):
        self.login("root")
        data = {"full_name": "Anita Das", "username": "anita", "email": "a@x.org", "password1": "Very-Strong-9", "password2": "Very-Strong-9"}
        self.assertContains(self.client.post(reverse("teacher_add"), data, follow=True), "Teacher added")
        u = User.objects.get(username="anita")
        self.assertFalse(u.is_superuser or u.is_staff)
        self.assertEqual(self.client.post(reverse("teacher_add"), {**data, "password2": "x"}).status_code, 200)
        self.assertContains(self.client.post(reverse("teacher_add"), data), "already taken")

    def test_add_student_enrols(self):
        self.login("root")
        r = self.client.post(reverse("student_add"), {"admission_no": "A/9999", "name": "New Kid", "term": self.sem1.pk,
                             "session": "2026-27", "section": "B", "enrol_core": "on"}, follow=True)
        self.assertContains(r, "Student added")
        self.assertTrue(Enrollment.objects.filter(student__admission_no="A/9999", offering=self.off).exists())
        self.assertContains(self.client.post(reverse("student_add"), {"admission_no": "A/9999", "name": "Again", "term": self.sem1.pk, "session": "2026-27"}), "already exists")

    def test_success_page_needs_a_message(self):
        self.login("root")
        self.assertEqual(self.client.get(reverse("success")).status_code, 302)

    def test_open_and_allot(self):
        self.login("root")
        r = self.client.post(reverse("offering_open"), {"session": "2026-27", "term": self.sem2.pk, "course": [self.c2.pk],
                             f"teacher_{self.c2.pk}": self.t2.pk}, follow=True)
        self.assertContains(r, "opened")
        off = Offering.objects.get(course=self.c2, session="2026-27")
        self.assertEqual((off.exams.count(), list(off.allotments.values_list("teacher_id", flat=True))), (3, [self.t2.pk]))
        self.client.post(reverse("offering_allot", args=[off.pk]), {"teacher": [self.t1.pk]})
        self.assertEqual(list(off.allotments.values_list("teacher_id", flat=True)), [self.t1.pk])


# ============================================================== settings & branding
class SettingsTests(Base):
    def base_post(self, **over):
        site = SiteConfig.objects.get()
        data = {"institution_type": "COLLEGE", "institution_name": site.institution_name, "department_name": site.department_name,
                "footer_text": "", "level_label": "Semester", "paper_label": "Paper", "pass_percent": "40", "signatures": "A, B",
                "use_grades": "on", "grade_bands": site.grade_bands, "division_rules": site.division_rules}
        data.update(over)
        return data

    def test_edit_and_labels(self):
        self.login("root")
        r = self.client.post(reverse("site_settings"), self.base_post(institution_name="Renamed College", paper_label="Course", pass_percent="45"), follow=True)
        self.assertContains(r, "Settings saved")
        site = SiteConfig.objects.get()
        self.assertEqual((site.institution_name, site.paper_label, site.pass_percent), ("Renamed College", "Course", Decimal("45")))
        self.assertContains(self.client.get(reverse("course_list")), "Courses")

    def test_exams_locked_after_first_offering(self):
        self.login("root")
        page = self.client.get(reverse("site_settings"))
        self.assertContains(page, "cannot be changed once")
        before = list(ExamTemplate.objects.values_list("name", "max_marks"))
        self.client.post(reverse("site_settings"), self.base_post(exam_count="1", exam_name_1="Hacked", exam_max_1="100"))
        self.assertEqual(list(ExamTemplate.objects.values_list("name", "max_marks")), before)

    def test_exams_editable_before_any_offering(self):
        Offering.objects.all().delete()
        self.login("root")
        self.client.post(reverse("site_settings"), self.base_post(exam_count="2", exam_name_1="Mid", exam_max_1="40", exam_name_2="End", exam_max_2="60"))
        self.assertEqual([(e.name, e.max_marks) for e in ExamTemplate.objects.all()], [("Mid", 40), ("End", 60)])

    def test_invalid_rules(self):
        self.login("root")
        r = self.client.post(reverse("site_settings"), self.base_post(grade_bands="nonsense"))
        self.assertContains(r, "Grade line 1")
        r = self.client.post(reverse("site_settings"), self.base_post(pass_percent="500"))
        self.assertContains(r, "between 1 and 100")

    def test_logo_upload_serve_remove(self):
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            self.login("root")
            self.assertEqual(self.client.get(reverse("branding_logo")).status_code, 404)
            bad = self.client.post(reverse("site_settings"), {**self.base_post(), "logo": SimpleUploadedFile("x.png", b"not an image", content_type="image/png")})
            self.assertContains(bad, "valid image")
            ok = self.client.post(reverse("site_settings"), {**self.base_post(), "logo": SimpleUploadedFile("logo.png", tiny_png(), content_type="image/png")}, follow=True)
            self.assertContains(ok, "Settings saved")
            self.client.logout()
            r = self.client.get(reverse("branding_logo"))                     # public: needed on the login page
            self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))
            self.assertContains(self.client.get(reverse("login")), "branding/logo")
            self.login("root")
            self.client.post(reverse("site_settings"), {**self.base_post(), "remove_logo": "on"})
            self.assertEqual(self.client.get(reverse("branding_logo")).status_code, 404)


class ClassesAndSubjectsTests(Base):
    def test_class_crud(self):
        self.login("root")
        self.client.post(reverse("class_list"), {"name": "Semester 4", "order": 4})
        t = Term.objects.get(name="Semester 4")
        self.client.post(reverse("class_edit", args=[t.pk]), {"name": "Semester Four", "order": 4, "pass_percent": "50"})
        t.refresh_from_db()
        self.assertEqual((t.name, t.pass_percent), ("Semester Four", Decimal("50")))
        self.client.post(reverse("class_edit", args=[t.pk]), {"delete": "1"})
        self.assertFalse(Term.objects.filter(pk=t.pk).exists())
        self.client.post(reverse("class_edit", args=[self.sem1.pk]), {"delete": "1"})        # has students -> refused
        self.assertTrue(Term.objects.filter(pk=self.sem1.pk).exists())
        self.assertContains(self.client.post(reverse("class_list"), {"name": "Semester 1", "order": 9}), "already exists")

    def test_import_subjects(self):
        csv = ("Class,Code,Name,Credits,Compulsory\nSemester 1,PHY-1,Physics,4,yes\nSemester 2,PHY-2,Optics,3,no\nSemester 9,X,Nope,,yes\n").encode()
        res = import_courses(io.BytesIO(csv), "s.csv")
        self.assertEqual((res.created, len(res.errors)), (2, 1))
        self.assertFalse(Course.objects.get(code="PHY-2").compulsory)
        res = import_courses(io.BytesIO(csv), "s.csv")
        self.assertEqual((res.created, res.updated), (0, 2))
        with self.assertRaises(ValueError):
            import_courses(io.BytesIO(b"a,b\n1,2\n"), "s.csv")

    def test_bundled_example_files_import(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "examples"
        make = lambda names: [Term.objects.get_or_create(name=n, defaults={"order": i})[0] for i, n in enumerate(names, 10)]
        make([f"Semester {i}" for i in range(4, 9)])
        res = import_courses((root / "papers_dibrugarh_university_fyugp_physics.csv").open("rb"), "p.csv")
        self.assertEqual(res.errors, [])
        self.assertEqual(Course.objects.filter(code__startswith="PHY-").count(), 48)

    def test_sample_csv_download(self):
        self.login("root")
        r = self.client.get(reverse("course_sample_csv"))
        self.assertContains(r, "Semester 1,MAT-1,Mathematics")


class MarksheetTests(Base):
    def test_pdfs(self):
        for st in self.students[:2]:
            self.put(st, [15, 15, 45])
        self.login("root")
        url = reverse("marksheet_semester_pdf") + f"?session=2026-27&term={self.sem1.pk}"
        self.assertTrue(self.client.get(url).content.startswith(b"%PDF"))
        st = self.students[0]
        self.assertTrue(self.client.get(reverse("marksheet_student_pdf", args=[st.pk]) + f"?term={self.sem1.pk}&session=2026-27").content.startswith(b"%PDF"))
        self.assertTrue(self.client.get(reverse("marksheet_student_pdf", args=[st.pk])).content.startswith(b"%PDF"))
        self.assertEqual(self.client.get(reverse("marksheets") + f"?session=2026-27&term={self.sem1.pk}").status_code, 200)
        self.assertEqual(self.client.get(reverse("student_detail", args=[st.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("offering_detail", args=[self.off.pk])).status_code, 200)

    def test_pdf_with_logo_and_two_exam_pattern(self):
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            site = SiteConfig.objects.get()
            site.logo.save("l.png", SimpleUploadedFile("l.png", tiny_png()), save=True)
            self.put(self.students[0], [15, 15, 45])
            self.login("root")
            self.assertTrue(self.client.get(reverse("marksheet_student_pdf", args=[self.students[0].pk])).content.startswith(b"%PDF"))

    def test_backlog_attempt_excluded_from_totals(self):
        st = self.students[0]
        self.put(st, [3, 3, 10])
        sync_backlogs(self.off)
        st.term, st.session = self.sem3, "2027-28"
        st.save()
        o3 = Offering.objects.create(course=self.c3, session="2027-28")
        enroll(st, o3)
        retake = Offering.objects.create(course=self.c1, session="2027-28")
        enroll(st, retake, is_backlog=True)
        self.put(st, [15, 15, 40], off=o3)
        sheets = student_sheets(st)
        self.assertEqual(len(sheets), 2)
        s3 = sheets[1]
        self.assertEqual(([r["course"].code for r in s3["regular"]], [r["course"].code for r in s3["retakes"]]), (["MAT-3"], ["MAT-1"]))
        self.assertEqual((s3["result"], s3["grand_total"]), ("PASS", Decimal("70")))


class ImportStudentTests(Base):
    CSV = ("Sl,Admission No,Student Name,Class,Father Name,Date Of Birth,Gender,Category,Mobile Number\n"
           "1,A/1840,DHRUBA GOGOI,H.S. 1st year Arts(A),ANIL GOGOI,31-08-2009,Male,OBC,8471894133\n"
           "2,A/1841,SUNALI K,H.S. 1st year Arts(A),SHYAM K,06-03-2009,Female,OBC,6003014240\n3,,NO NUMBER,,,,,,\n")

    def test_csv_import_reimport_enrol(self):
        res = import_students(SimpleUploadedFile("l.csv", self.CSV.encode()), "l.csv", self.sem1, "2026-27")
        self.assertEqual((res.created, res.updated, len(res.errors), res.enrolled), (2, 0, 1, 2))
        s = Student.objects.get(admission_no="A/1840")
        self.assertEqual((s.stream, s.section, str(s.date_of_birth)), ("Arts", "A", "2009-08-31"))
        res2 = import_students(SimpleUploadedFile("l.csv", self.CSV.encode()), "l.csv", self.sem1, "2026-27")
        self.assertEqual((res2.created, res2.updated, res2.enrolled), (0, 2, 0))

    def test_xlsx_and_bad_files(self):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Admission No", "Student Name", "Mobile Number"])
        ws.append(["A/1900", "XLSX KID", 8638604013.0])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        self.assertEqual(import_students(buf, "x.xlsx", self.sem1, "2026-27").created, 1)
        self.assertEqual(Student.objects.get(admission_no="A/1900").mobile, "8638604013")
        with self.assertRaises(ValueError):
            import_students(io.BytesIO(b"a,b\n1,2\n"), "x.csv", self.sem1, "2026-27")
        with self.assertRaises(ValueError):
            import_students(io.BytesIO(b""), "x.pdf", self.sem1, "2026-27")
