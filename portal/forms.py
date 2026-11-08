from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import Course, SiteConfig, Student, Term, session_validator
from .rules import RuleError, parse_divisions, parse_grade_bands
from .setup_logic import MAX_EXAMS

User = get_user_model()
FC = {"class": "form-control"}
FS = {"class": "form-select"}
FCHECK = {"class": "form-check-input"}


def _pct(value):
    if value is None or not (Decimal("1") <= value <= Decimal("100")):
        raise ValidationError("Enter a percentage between 1 and 100.")
    return value


# ----------------------------------------------------------------- students
class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["admission_no", "name", "father_name", "date_of_birth", "gender", "category",
                  "mobile", "stream", "section", "term", "session"]
        widgets = {
            "admission_no": forms.TextInput(attrs={**FC, "placeholder": "Unique number, e.g. 2026/0001"}),
            "name": forms.TextInput(attrs=FC), "father_name": forms.TextInput(attrs=FC),
            "date_of_birth": forms.DateInput(attrs={**FC, "type": "date"}, format="%Y-%m-%d"),
            "gender": forms.Select(attrs=FS, choices=[("", "Select"), ("Male", "Male"), ("Female", "Female"), ("Other", "Other")]),
            "category": forms.TextInput(attrs=FC),
            "mobile": forms.TextInput(attrs={**FC, "inputmode": "numeric"}),
            "stream": forms.TextInput(attrs={**FC, "placeholder": "Optional (Arts / Science ...)"}),
            "section": forms.TextInput(attrs=FC),
            "term": forms.Select(attrs=FS),
            "session": forms.TextInput(attrs={**FC, "placeholder": "2026-27"}),
        }
        error_messages = {"admission_no": {"unique": "A student with this admission number already exists."}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        site = SiteConfig.objects.first()
        self.fields["term"].queryset = Term.objects.all()
        self.fields["term"].label = site.level_label if site else "Class"
        self.fields["gender"].required = False


class StudentAddForm(StudentForm):
    enrol_core = forms.BooleanField(required=False, initial=True,
                                    label="Also enrol in the compulsory subjects already opened for this class and session")


class ImportForm(forms.Form):
    file = forms.FileField(label="Class list (.csv or .xlsx)", widget=forms.ClearableFileInput(attrs=FC))
    term = forms.ModelChoiceField(Term.objects.all(), widget=forms.Select(attrs=FS))
    session = forms.CharField(max_length=9, validators=[session_validator], initial="2026-27", widget=forms.TextInput(attrs=FC))
    stream = forms.CharField(max_length=30, required=False, widget=forms.TextInput(attrs=FC))
    section = forms.CharField(max_length=5, required=False, widget=forms.TextInput(attrs=FC))
    auto_enroll = forms.BooleanField(required=False, initial=True,
                                     label="Enrol in compulsory subjects already opened for this class and session")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        site = SiteConfig.objects.first()
        self.fields["term"].label = site.level_label if site else "Class"
        self.fields["term"].queryset = Term.objects.all()


# ----------------------------------------------------------------- teachers
class PasswordPairMixin(forms.Form):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(attrs=FC))
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput(attrs=FC))

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        if not all(c.isalnum() or c in "._-" for c in u):
            raise ValidationError("Use letters, digits and . _ - only.")
        if User.objects.filter(username__iexact=u).exists():
            raise ValidationError("This username is already taken.")
        return u

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "The two passwords do not match.")
            else:
                try:
                    validate_password(p1)
                except ValidationError as exc:
                    self.add_error("password1", exc)
        return data


class TeacherForm(PasswordPairMixin):
    full_name = forms.CharField(max_length=120, widget=forms.TextInput(attrs=FC))
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs=FC),
                               help_text="Used to log in. Letters, digits and . _ - only.")
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs=FC))


class AdminAccountForm(PasswordPairMixin):
    full_name = forms.CharField(max_length=120, widget=forms.TextInput(attrs=FC))
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs=FC),
                               help_text="You will log in with this. Letters, digits and . _ - only.")
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs=FC))


# ------------------------------------------------- classes / subjects / papers
class TermForm(forms.ModelForm):
    class Meta:
        model = Term
        fields = ["name", "order", "pass_percent"]
        widgets = {"name": forms.TextInput(attrs=FC), "order": forms.NumberInput(attrs=FC),
                   "pass_percent": forms.NumberInput(attrs={**FC, "step": "0.5"})}

    def clean_pass_percent(self):
        v = self.cleaned_data.get("pass_percent")
        return _pct(v) if v is not None else v


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["term", "code", "name", "credits", "compulsory"]
        widgets = {"term": forms.Select(attrs=FS), "code": forms.TextInput(attrs=FC), "name": forms.TextInput(attrs=FC),
                   "credits": forms.NumberInput(attrs={**FC, "step": "0.5"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        site = SiteConfig.objects.first()
        self.fields["term"].label = site.level_label if site else "Class"
        self.fields["credits"].required = False
        self.fields["credits"].help_text = "Optional. Only needed for credit based grading (SGPA)."
        self.fields["compulsory"].label = "Compulsory (every student of the class is enrolled automatically)"

    def clean_credits(self):
        return self.cleaned_data.get("credits") or Decimal("0")


class CourseImportForm(forms.Form):
    file = forms.FileField(label="List of subjects / papers (.csv or .xlsx)", widget=forms.ClearableFileInput(attrs=FC))


class PaperUploadForm(forms.Form):
    file = forms.FileField()

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".pdf"):
            raise ValidationError("Only PDF files are allowed.")
        if f.size > settings.MAX_PAPER_UPLOAD_MB * 1024 * 1024:
            raise ValidationError(f"The file is larger than {settings.MAX_PAPER_UPLOAD_MB} MB.")
        head = f.read(5)
        f.seek(0)
        if head != b"%PDF-":
            raise ValidationError("This file does not look like a real PDF.")
        return f


# -------------------------------------------------------------- setup / settings
class InstitutionForm(forms.Form):
    institution_type = forms.ChoiceField(choices=SiteConfig.TYPE_CHOICES, widget=forms.RadioSelect, initial="COLLEGE",
                                         label="What kind of institution is this?")
    name = forms.CharField(max_length=150, label="Name of the school / college",
                           widget=forms.TextInput(attrs={**FC, "placeholder": "e.g. Sunrise Public School"}))
    department = forms.CharField(max_length=150, required=False, label="Department (optional)",
                                 widget=forms.TextInput(attrs={**FC, "placeholder": "e.g. Department of Physics"}))
    footer = forms.CharField(max_length=200, required=False, label="Footer text (optional)",
                             widget=forms.TextInput(attrs={**FC, "placeholder": "e.g. Created by Mr. A. Kumar"}))


class LabelsForm(forms.Form):
    level_label = forms.CharField(max_length=20, label="What do you call a class level?",
                                  widget=forms.TextInput(attrs=FC), help_text="Class, Semester, Year, Grade ...")
    paper_label = forms.CharField(max_length=20, label="What do you call a subject?",
                                  widget=forms.TextInput(attrs=FC), help_text="Subject, Paper, Course ...")
    classes_text = forms.CharField(label="Class names, one per line, in order", widget=forms.Textarea(attrs={**FC, "rows": 10}))


class RulesForm(forms.Form):
    exam_count = forms.IntegerField(min_value=1, max_value=MAX_EXAMS, initial=3, widget=forms.NumberInput(attrs=FC),
                                    label="How many exams does every subject have in a session?")
    pass_percent = forms.DecimalField(max_digits=5, decimal_places=2, initial=40, widget=forms.NumberInput(attrs={**FC, "step": "0.5"}),
                                      label="Pass mark (% of the total)")
    use_grades = forms.BooleanField(required=False, label="Show letter grades")
    grade_bands = forms.CharField(required=False, widget=forms.Textarea(attrs={**FC, "rows": 7}),
                                  label="Grade table (minimum percent, grade, grade point - one per line)")
    show_division = forms.BooleanField(required=False, label="Show First / Second / Third division")
    division_rules = forms.CharField(required=False, widget=forms.Textarea(attrs={**FC, "rows": 4}),
                                     label="Division table (minimum percent, name - one per line)")
    signatures = forms.CharField(max_length=200, widget=forms.TextInput(attrs=FC),
                                 label="Signature lines on marksheets (comma separated)")

    def clean_pass_percent(self):
        return _pct(self.cleaned_data.get("pass_percent"))

    def clean(self):
        data = super().clean()
        try:
            if data.get("use_grades"):
                if not parse_grade_bands(data.get("grade_bands")):
                    raise RuleError("Add at least one grade line, or untick 'Show letter grades'.")
            if data.get("show_division"):
                if not parse_divisions(data.get("division_rules")):
                    raise RuleError("Add at least one division line, or untick 'Show divisions'.")
        except RuleError as exc:
            raise ValidationError(str(exc))
        return data


def check_logo(f):
    name = f.name.lower()
    if not name.endswith((".png", ".jpg", ".jpeg")):
        raise ValidationError("Please upload a PNG or JPG image.")
    if f.size > settings.MAX_LOGO_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"The image is larger than {settings.MAX_LOGO_UPLOAD_MB} MB.")
    from PIL import Image
    try:
        img = Image.open(f)
        img.verify()
        fmt_ = (img.format or "").upper()
    except Exception:
        raise ValidationError("This file is not a valid image.")
    finally:
        f.seek(0)
    if fmt_ not in ("PNG", "JPEG"):
        raise ValidationError("Please upload a PNG or JPG image.")
    return f


class SiteSettingsForm(forms.ModelForm):
    remove_logo = forms.BooleanField(required=False, label="Remove the current logo")

    class Meta:
        model = SiteConfig
        fields = ["institution_type", "institution_name", "department_name", "footer_text", "logo",
                  "level_label", "paper_label", "pass_percent", "use_grades", "grade_bands",
                  "show_division", "division_rules", "signatures"]
        widgets = {
            "institution_type": forms.Select(attrs=FS), "institution_name": forms.TextInput(attrs=FC),
            "department_name": forms.TextInput(attrs=FC), "footer_text": forms.TextInput(attrs=FC),
            "logo": forms.FileInput(attrs={**FC, "accept": "image/png,image/jpeg"}),
            "level_label": forms.TextInput(attrs=FC), "paper_label": forms.TextInput(attrs=FC),
            "pass_percent": forms.NumberInput(attrs={**FC, "step": "0.5"}),
            "grade_bands": forms.Textarea(attrs={**FC, "rows": 7}),
            "division_rules": forms.Textarea(attrs={**FC, "rows": 4}), "signatures": forms.TextInput(attrs=FC),
        }
        labels = {"institution_name": "Name of the school / college", "level_label": "What a class level is called",
                  "paper_label": "What a subject is called", "pass_percent": "Pass mark (% of the total)",
                  "use_grades": "Show letter grades", "show_division": "Show First / Second / Third division",
                  "grade_bands": "Grade table (minimum percent, grade, grade point)",
                  "division_rules": "Division table (minimum percent, name)",
                  "signatures": "Signature lines on marksheets (comma separated)", "footer_text": "Footer text"}

    def clean_pass_percent(self):
        return _pct(self.cleaned_data.get("pass_percent"))

    def clean_logo(self):
        f = self.cleaned_data.get("logo")
        if f and hasattr(f, "content_type"):   # a new upload (not the existing file)
            check_logo(f)
        return f

    def clean(self):
        data = super().clean()
        try:
            if data.get("use_grades") and not parse_grade_bands(data.get("grade_bands")):
                raise RuleError("Add at least one grade line, or untick 'Show letter grades'.")
            if data.get("show_division") and not parse_divisions(data.get("division_rules")):
                raise RuleError("Add at least one division line, or untick 'Show divisions'.")
        except RuleError as exc:
            raise ValidationError(str(exc))
        return data
