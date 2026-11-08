"""Superuser safety net. Day-to-day work happens on the portal pages, not here."""
from django.contrib import admin

from . import models

for m in (models.SiteConfig, models.ExamTemplate, models.Term, models.Course, models.Student, models.Offering,
          models.Allotment, models.Exam, models.Enrollment, models.Mark, models.Backlog, models.MarkLog):
    admin.site.register(m)
