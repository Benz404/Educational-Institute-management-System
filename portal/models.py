from decimal import Decimal

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

session_validator = RegexValidator(r"^\d{4}(-\d{2,4})?$", "Use a session like 2026-27 (or just 2026).")

DEFAULT_GRADE_BANDS = "90,O,10\n80,A+,9\n70,A,8\n60,B+,7\n50,B,6\n40,C,5\n0,P,4"
DEFAULT_DIVISIONS = "60,First Division\n45,Second Division\n0,Third Division"


def fmt(value):
    """Decimal -> tidy string (10.00 -> '10', 7.50 -> '7.5')."""
    if value is None:
        return ""
    s = format(Decimal(value).quantize(Decimal("0.01")), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def plural(word):
    w = (word or "").strip()
    if not w:
        return w
    return w + "es" if w.lower().endswith(("s", "x", "ch", "sh")) else w + "s"


class SiteConfig(models.Model):
    """The one-time setup of the institution (a single row)."""
    SCHOOL, COLLEGE, UNIVERSITY, OTHER = "SCHOOL", "COLLEGE", "UNIVERSITY", "OTHER"
    TYPE_CHOICES = [(SCHOOL, "School"), (COLLEGE, "College"), (UNIVERSITY, "University / institute"), (OTHER, "Other")]

    institution_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=COLLEGE)
    institution_name = models.CharField(max_length=150)
    department_name = models.CharField(max_length=150, blank=True, help_text="Optional, e.g. Department of Physics")
    footer_text = models.CharField(max_length=200, blank=True)
    logo = models.ImageField(upload_to="branding/", blank=True)
    level_label = models.CharField(max_length=20, default="Class", help_text="What a class level is called: Class, Semester, Year ...")
    paper_label = models.CharField(max_length=20, default="Subject", help_text="Subject, Paper, Course ...")
    pass_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("40"))
    use_grades = models.BooleanField(default=True)
    grade_bands = models.TextField(default=DEFAULT_GRADE_BANDS, blank=True)
    show_division = models.BooleanField(default=False)
    division_rules = models.TextField(default=DEFAULT_DIVISIONS, blank=True)
    signatures = models.CharField(max_length=200, default="Class Teacher, Principal")
    setup_complete = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.institution_name

    @property
    def level_plural(self):
        return plural(self.level_label)

    @property
    def paper_plural(self):
        return plural(self.paper_label)

    @property
    def signature_list(self):
        return [x.strip() for x in self.signatures.split(",") if x.strip()]


class ExamTemplate(models.Model):
    """Every paper/subject gets these exams in every session (e.g. Sessional 1 /20, Sessional 2 /20, Final /60)."""
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=60)
    max_marks = models.DecimalField(max_digits=5, decimal_places=2)
    order = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.name} ({fmt(self.max_marks)})"


class Term(models.Model):
    """A class level: 'Class 7', 'Semester 3', 'H.S. 1st Year' ..."""
    name = models.CharField(max_length=60, unique=True)
    order = models.PositiveSmallIntegerField(default=1)
    pass_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                       help_text="Leave empty to use the institution's pass mark.")

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class Course(models.Model):
    """A subject / paper of a class level (permanent). It is 'opened' for a session as an Offering."""
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="courses")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=140)
    credits = models.DecimalField(max_digits=4, decimal_places=1, default=0)
    compulsory = models.BooleanField(default=True, help_text="Students of the class are enrolled automatically.")

    class Meta:
        ordering = ["term__order", "term__name", "code"]
        unique_together = [("term", "code")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Student(models.Model):
    admission_no = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    father_name = models.CharField(max_length=120, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, blank=True)
    category = models.CharField(max_length=30, blank=True)
    mobile = models.CharField(max_length=15, blank=True)
    stream = models.CharField(max_length=30, blank=True)
    section = models.CharField(max_length=5, blank=True)
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="students")  # current class
    session = models.CharField(max_length=9, validators=[session_validator])           # current session
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["admission_no"]

    def __str__(self):
        return f"{self.admission_no} - {self.name}"

    def save(self, *args, **kwargs):
        self.admission_no = (self.admission_no or "").strip()
        self.name = (self.name or "").strip()
        super().save(*args, **kwargs)


class Offering(models.Model):
    """A paper running in one session. Teachers, enrolments and exams hang off this."""
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="offerings")
    session = models.CharField(max_length=9, validators=[session_validator])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-session", "course__term__order", "course__code"]
        unique_together = [("course", "session")]

    def __str__(self):
        return f"{self.course.code} ({self.session})"

    def save(self, *args, **kwargs):
        new = self.pk is None
        super().save(*args, **kwargs)
        if new:
            templates = list(ExamTemplate.objects.all())
            if not templates:
                templates = [ExamTemplate(code="E1", name="Exam", max_marks=Decimal("100"), order=1)]
            for t in templates:
                Exam.objects.create(offering=self, kind=t.code, name=t.name, max_marks=t.max_marks, order=t.order)

    @property
    def teachers(self):
        return [a.teacher for a in self.allotments.select_related("teacher")]


class Allotment(models.Model):
    offering = models.ForeignKey(Offering, on_delete=models.CASCADE, related_name="allotments")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="allotments")

    class Meta:
        unique_together = [("offering", "teacher")]


def paper_path(instance, filename):
    code = instance.offering.course.code.replace("/", "-")
    return f"question_papers/{instance.offering.session}/{code}-{instance.kind}.pdf"


class Exam(models.Model):
    offering = models.ForeignKey(Offering, on_delete=models.CASCADE, related_name="exams")
    kind = models.CharField(max_length=10)
    name = models.CharField(max_length=60, default="Exam")
    max_marks = models.DecimalField(max_digits=5, decimal_places=2)
    order = models.PositiveSmallIntegerField(default=1)
    held_on = models.DateField(null=True, blank=True)
    question_paper = models.FileField(upload_to=paper_path, blank=True)
    paper_uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="+")
    paper_uploaded_at = models.DateTimeField(null=True, blank=True)
    is_locked = models.BooleanField(default=False)

    class Meta:
        ordering = ["offering", "order"]
        unique_together = [("offering", "kind")]

    def __str__(self):
        return f"{self.offering} / {self.label}"

    @property
    def label(self):
        return self.name


class Enrollment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="enrollments")
    offering = models.ForeignKey(Offering, on_delete=models.CASCADE, related_name="enrollments")
    student_term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="+",
                                     help_text="The class the student was in during that session.")
    is_backlog = models.BooleanField(default=False, help_text="Re-attempt of a paper failed earlier.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["student__section", "student__admission_no"]
        unique_together = [("student", "offering")]

    def __str__(self):
        return f"{self.student.admission_no} / {self.offering}"


class Mark(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="marks")
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="marks")
    marks = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    absent = models.BooleanField(default=False)

    class Meta:
        unique_together = [("enrollment", "exam")]

    def display(self):
        return "AB" if self.absent else fmt(self.marks)


class Backlog(models.Model):
    OPEN, CLEARED = "OPEN", "CLEARED"
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="backlogs")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="backlogs")
    origin = models.ForeignKey(Offering, null=True, on_delete=models.SET_NULL, related_name="+",
                               help_text="The session in which the paper was failed.")
    status = models.CharField(max_length=7, default=OPEN, choices=[(OPEN, "Open"), (CLEARED, "Cleared")])
    cleared_in = models.ForeignKey(Offering, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["course__term__order", "course__code", "student__admission_no"]
        unique_together = [("student", "course")]

    def __str__(self):
        return f"{self.student.admission_no} / {self.course.code} ({self.status})"


class MarkLog(models.Model):
    """Audit trail of every mark change."""
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    admission_no = models.CharField(max_length=40)
    paper = models.CharField(max_length=60)
    session = models.CharField(max_length=9)
    exam = models.CharField(max_length=30)
    old_value = models.CharField(max_length=10, blank=True)
    new_value = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["-changed_at", "-id"]


def log_mark_change(user, enrollment, exam, old, new):
    MarkLog.objects.create(
        changed_by=user if getattr(user, "pk", None) else None,
        admission_no=enrollment.student.admission_no, paper=enrollment.offering.course.code,
        session=enrollment.offering.session, exam=exam.label, old_value=old, new_value=new)
