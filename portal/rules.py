"""Result rules (pass mark, grades, divisions) read from the institution's setup."""
from decimal import Decimal, InvalidOperation

from .models import SiteConfig


class RuleError(ValueError):
    pass


def parse_grade_bands(text):
    """'90,O,10' per line -> [(Decimal 90, 'O', Decimal 10), ...] sorted high to low."""
    bands = []
    for n, line in enumerate((text or "").splitlines(), 1):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            raise RuleError(f"Grade line {n}: write it as  minimum percent, grade, grade point  (e.g. 90,O,10).")
        try:
            minimum, points = Decimal(parts[0]), Decimal(parts[2])
        except InvalidOperation:
            raise RuleError(f"Grade line {n}: the percent and grade point must be numbers.")
        if not parts[1] or not (0 <= minimum <= 100):
            raise RuleError(f"Grade line {n}: need a grade name and a percent between 0 and 100.")
        bands.append((minimum, parts[1], points))
    bands.sort(key=lambda b: -b[0])
    return bands


def parse_divisions(text):
    """'60,First Division' per line -> [(Decimal 60, 'First Division'), ...] sorted high to low."""
    out = []
    for n, line in enumerate((text or "").splitlines(), 1):
        if not line.strip():
            continue
        minimum, _, label = line.partition(",")
        try:
            minimum = Decimal(minimum.strip())
        except InvalidOperation:
            raise RuleError(f"Division line {n}: write it as  minimum percent, name  (e.g. 60,First Division).")
        if not label.strip() or not (0 <= minimum <= 100):
            raise RuleError(f"Division line {n}: need a name and a percent between 0 and 100.")
        out.append((minimum, label.strip()))
    out.sort(key=lambda d: -d[0])
    return out


class Rules:
    """Loaded once per request/report so results are cheap to compute."""

    def __init__(self, site=None):
        site = site or SiteConfig.objects.first()
        self.site = site
        self.pass_percent = site.pass_percent if site else Decimal("40")
        self.use_grades = bool(site and site.use_grades)
        self.bands = parse_grade_bands(site.grade_bands) if self.use_grades else []
        self.show_division = bool(site and site.show_division)
        self.divisions = parse_divisions(site.division_rules) if self.show_division else []

    def pass_for(self, term):
        return term.pass_percent if term.pass_percent is not None else self.pass_percent

    def grade(self, percent):
        if not self.use_grades:
            return "", None
        for minimum, grade, points in self.bands:
            if percent >= minimum:
                return grade, points
        return "F", Decimal("0")

    def division(self, percent):
        if not self.show_division:
            return ""
        for minimum, label in self.divisions:
            if percent >= minimum:
                return label
        return ""
