from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from portal.models import Allotment, Course, ExamTemplate, Mark, Offering, SiteConfig, Student, Term
from portal.services import enroll, sync_backlogs

DEMO_PASSWORD = "Demo-Pass-2026"
SHARES = [0.9, 0.75, 0.6, 0.97, 0.25, 0.7]


class Command(BaseCommand):
    help = "Add a small FAKE data set (1 teacher, 6 students, 1 subject with marks) so you can try the portal. Delete it before real use."

    def handle(self, *args, **opts):
        site = SiteConfig.objects.first()
        term = Term.objects.first()
        if not site or not term:
            raise CommandError("Run the setup first.")
        User = get_user_model()
        teacher, made = User.objects.get_or_create(username="demo_teacher", defaults={"first_name": "Demo", "last_name": "Teacher"})
        if made:
            teacher.set_password(DEMO_PASSWORD)
            teacher.save()
        course, _ = Course.objects.get_or_create(term=term, code="DEMO-1", defaults={"name": "Demo subject", "credits": 0, "compulsory": True})
        off, _ = Offering.objects.get_or_create(course=course, session="2026-27")
        Allotment.objects.get_or_create(offering=off, teacher=teacher)
        exams = list(off.exams.order_by("order"))
        for n, share in enumerate(SHARES, 1):
            st, _ = Student.objects.get_or_create(admission_no=f"DEMO/{n:04d}", defaults={
                "name": f"Demo Student {n}", "term": term, "session": "2026-27", "section": "A"})
            enr, _ = enroll(st, off)
            for ex in exams:
                if n == 6 and ex.order == 2 and len(exams) > 1:
                    Mark.objects.update_or_create(enrollment=enr, exam=ex, defaults={"absent": True, "marks": None})
                else:
                    Mark.objects.update_or_create(enrollment=enr, exam=ex, defaults={
                        "marks": (ex.max_marks * Decimal(str(share))).quantize(Decimal("1")), "absent": False})
        sync_backlogs(off)
        self.stdout.write(self.style.SUCCESS(f"Demo data added. Teacher login: demo_teacher / {DEMO_PASSWORD}  (delete before real use)"))
