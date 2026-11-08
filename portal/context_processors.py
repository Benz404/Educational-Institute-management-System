from django.conf import settings

from .models import ExamTemplate, SiteConfig, fmt


def _lower(s):
    return (s or "").lower()


def branding(request):
    site = SiteConfig.objects.first()
    exams = list(ExamTemplate.objects.all())
    ctx = {
        "SITE": site, "EXAM_COLS": exams, "EXAM_TOTAL": fmt(sum((e.max_marks for e in exams), 0)),
        "SOFTWARE_CREDIT": settings.SOFTWARE_CREDIT if settings.SHOW_SOFTWARE_CREDIT else "",
        "COLLEGE_NAME": site.institution_name if site else "Student Records Portal",
        "DEPARTMENT_NAME": site.department_name if site else "",
    }
    if site:
        ctx.update(LEVEL=site.level_label, LEVELS=site.level_plural, PAPER=site.paper_label, PAPERS=site.paper_plural,
                   LEVEL_L=_lower(site.level_label), LEVELS_L=_lower(site.level_plural),
                   PAPER_L=_lower(site.paper_label), PAPERS_L=_lower(site.paper_plural))
    else:
        ctx.update(LEVEL="Class", LEVELS="Classes", PAPER="Subject", PAPERS="Subjects",
                   LEVEL_L="class", LEVELS_L="classes", PAPER_L="subject", PAPERS_L="subjects")
    return ctx
