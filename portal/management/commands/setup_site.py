from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from portal import setup_logic
from portal.models import DEFAULT_DIVISIONS, DEFAULT_GRADE_BANDS


class Command(BaseCommand):
    help = ("Non-interactive alternative to the setup wizard (handy for servers / Docker / testing).\n"
            "Example: manage.py setup_site --type school --name 'Sunrise School' --prefix Class --count 10 "
            "--exams 'Term 1:50,Term 2:50' --pass-percent 35 --admin-username admin --admin-password 'S3cret-pass!'")

    def add_arguments(self, p):
        p.add_argument("--type", default="college", choices=["school", "college", "university", "other"])
        p.add_argument("--name", required=True)
        p.add_argument("--department", default="")
        p.add_argument("--footer", default="")
        p.add_argument("--level-label")
        p.add_argument("--paper-label")
        p.add_argument("--classes", help="Comma separated class names, in order")
        p.add_argument("--prefix", help="Used with --count to generate 'Prefix 1' ... 'Prefix N'")
        p.add_argument("--count", type=int)
        p.add_argument("--exams", default="", help="'Name:max,Name:max' e.g. 'Sessional 1:20,Sessional 2:20,Final:60'")
        p.add_argument("--pass-percent", default="40")
        p.add_argument("--grades", action="store_true", help="Show letter grades (default table)")
        p.add_argument("--division", action="store_true", help="Show First/Second/Third division")
        p.add_argument("--signatures")
        p.add_argument("--admin-username", required=True)
        p.add_argument("--admin-password", required=True)
        p.add_argument("--admin-name", default="")

    def handle(self, *a, **o):
        kind = o["type"].upper()
        d = setup_logic.TYPE_DEFAULTS[kind]
        try:
            if o["classes"]:
                classes = setup_logic.parse_class_names(o["classes"].replace(",", "\n"))
            else:
                classes = setup_logic.parse_class_names(setup_logic.generate_names(o["prefix"] or d["prefix"], o["count"] or d["count"]))
            if o["exams"]:
                rows = []
                for part in o["exams"].split(","):
                    nm, _, mx = part.rpartition(":")
                    rows.append((nm.strip(), Decimal(mx)))
            else:
                rows = [(n, Decimal(m)) for n, m in setup_logic.default_exam_rows(3)]
            post = {}
            for i, (n, m) in enumerate(rows, 1):
                post[f"exam_name_{i}"], post[f"exam_max_{i}"] = n, str(m)
            rows = setup_logic.parse_exam_rows(post, len(rows))
            setup_logic.apply_setup(
                institution_type=kind, name=o["name"], department=o["department"], footer=o["footer"],
                level_label=o["level_label"] or d["level_label"], paper_label=o["paper_label"] or d["paper_label"],
                classes=classes, exams=rows, pass_percent=Decimal(o["pass_percent"]),
                use_grades=o["grades"], grade_bands=DEFAULT_GRADE_BANDS, show_division=o["division"],
                division_rules=DEFAULT_DIVISIONS, signatures=o["signatures"] or d["signatures"],
                admin={"username": o["admin_username"], "password": o["admin_password"], "full_name": o["admin_name"], "email": ""})
        except (ValueError, RuntimeError, ArithmeticError) as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Portal set up for {o['name']} with {len(classes)} classes and {len(rows)} exams."))
