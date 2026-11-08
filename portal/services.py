"""All result, insight, backlog and marksheet logic lives here. Rules come from the institution setup."""
import statistics
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

from .models import Backlog, Enrollment, Exam, fmt
from .rules import Rules

ZERO = Decimal("0")


def q2(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def with_related(qs):
    return qs.select_related("student", "student_term", "offering__course__term") \
             .prefetch_related("marks", "offering__exams")


# ----------------------------------------------------------------- per paper
def enrollment_result(enr, rules=None):
    rules = rules or Rules()
    offering = enr.offering
    course = offering.course
    marks = {m.exam_id: m for m in enr.marks.all()}
    cells, total, max_total, pending = [], ZERO, ZERO, False
    for exam in sorted(offering.exams.all(), key=lambda e: e.order):
        max_total += exam.max_marks
        m = marks.get(exam.id)
        if m is None or (m.marks is None and not m.absent):
            pending = True
            cells.append({"exam": exam, "text": "", "absent": False})
        elif m.absent:
            cells.append({"exam": exam, "text": "AB", "absent": True})
        else:
            total += m.marks
            cells.append({"exam": exam, "text": fmt(m.marks), "absent": False})
    percent = q2(total * 100 / max_total) if max_total else ZERO
    if pending:
        status, grade, points = "PENDING", "", None
    elif percent >= rules.pass_for(course.term):
        status = "PASS"
        grade, points = rules.grade(percent)
    else:
        status = "FAIL"
        grade, points = ("F", ZERO) if rules.use_grades else ("", None)
    return {"enrollment": enr, "student": enr.student, "course": course, "offering": offering,
            "cells": cells, "total": total, "max_total": max_total, "percent": percent,
            "status": status, "grade": grade, "grade_point": points, "credits": course.credits,
            "is_backlog": enr.is_backlog}


def offering_results(offering, rules=None):
    rules = rules or Rules()
    return [enrollment_result(e, rules) for e in with_related(offering.enrollments.all())]


def sync_backlogs(offering):
    """Mark failed students as backlog; clear backlog when a re-attempt is passed."""
    created = cleared = 0
    for res in offering_results(offering):
        enr, student = res["enrollment"], res["student"]
        bl = Backlog.objects.filter(student=student, course=offering.course).first()
        if res["status"] == "FAIL":
            if bl is None:
                Backlog.objects.create(student=student, course=offering.course, origin=offering)
                created += 1
            elif bl.status == Backlog.CLEARED:
                bl.status, bl.cleared_in = Backlog.OPEN, None
                bl.save()
        elif res["status"] == "PASS" and bl is not None:
            if enr.is_backlog and bl.status == Backlog.OPEN:
                bl.status, bl.cleared_in = Backlog.CLEARED, offering
                bl.save()
                cleared += 1
            elif not enr.is_backlog and bl.origin_id == offering.id and bl.status == Backlog.OPEN:
                bl.delete()  # a corrected mark means this student no longer has the backlog
    return created, cleared


def offering_summary(offering):
    results = offering_results(offering)
    counts = {"PASS": 0, "FAIL": 0, "PENDING": 0}
    for r in results:
        counts[r["status"]] += 1
    decided = counts["PASS"] + counts["FAIL"]
    exam_avgs = []
    for exam in offering.exams.all():
        ins = exam_insights(exam)
        exam_avgs.append({"exam": exam, "avg_pct": ins["mean_pct"], "entered": ins["entered"]})
    return {"results": results, "counts": counts, "enrolled": len(results),
            "pass_rate": round(100 * counts["PASS"] / decided, 1) if decided else None,
            "exam_avgs": exam_avgs,
            "open_backlogs": Backlog.objects.filter(course=offering.course, status=Backlog.OPEN).count(),
            "retakers": sum(1 for r in results if r["is_backlog"])}


# ------------------------------------------------------------------ insights
def exam_insights(exam):
    enrollments = list(exam.offering.enrollments.select_related("student"))
    marks = {m.enrollment_id: m for m in exam.marks.all()}
    scored, absent, pending = [], [], []
    for e in enrollments:
        m = marks.get(e.id)
        if m is None or (m.marks is None and not m.absent):
            pending.append(e)
        elif m.absent:
            absent.append(e)
        else:
            scored.append((e, m.marks))

    mx = exam.max_marks
    pass_line = mx * Rules().pass_for(exam.offering.course.term) / 100
    ins = {"exam": exam, "enrolled": len(enrollments), "appeared": len(scored), "absent": len(absent),
           "pending": len(pending), "entered": len(scored) + len(absent), "has_data": bool(scored),
           "max": mx, "pass_line": q2(pass_line), "notes": [], "mean_pct": None}
    edges = [mx * i / 5 for i in range(6)]
    ins["bin_labels"] = [f"{fmt(edges[i])}-{fmt(edges[i + 1])}" for i in range(5)]
    ins["bin_counts"] = [0] * 5
    ins.update(mean=None, median=None, highest=None, lowest=None, pass_rate=None,
               top=[], attention=[], below=0)

    notes = ins["notes"]
    if not enrollments:
        notes.append("No students are enrolled in this paper yet.")
        return ins
    if not scored:
        notes.append(f"No marks entered yet for {exam.label}." if not absent else
                     f"Only absentees recorded so far ({len(absent)}).")
        return ins

    values = [v for _, v in scored]
    mean = sum(values) / len(values)
    ordered = sorted(scored, key=lambda p: (-p[1], p[0].student.name))
    ins.update(mean=q2(mean), median=q2(Decimal(str(statistics.median(values)))),
               highest=max(values), lowest=min(values), mean_pct=round(float(mean * 100 / mx), 1))
    ins["top"] = ordered[:3]
    low = [(e, v) for e, v in reversed(ordered) if v < pass_line]
    ins["attention"] = low[:10]
    ins["below"] = len(low)
    ins["pass_rate"] = round(100 * (len(values) - len(low)) / len(values), 1)
    for _, v in scored:
        idx = min(int(v * 5 / mx), 4) if mx else 0
        ins["bin_counts"][idx] += 1

    notes.append(f"Marks are in for {ins['entered']} of {ins['enrolled']} enrolled students"
                 + (f" ({len(pending)} still pending)." if pending else "."))
    notes.append(f"Class average is {fmt(ins['mean'])} out of {fmt(mx)} ({ins['mean_pct']}%), "
                 f"median {fmt(ins['median'])}.")
    notes.append(f"Highest score {fmt(ins['highest'])} ({ordered[0][0].student.name}); "
                 f"lowest {fmt(ins['lowest'])}.")
    if low:
        notes.append(f"{len(low)} student{'s' if len(low) != 1 else ''} "
                     f"({round(100 * len(low) / len(values))}%) scored below the pass line of "
                     f"{fmt(ins['pass_line'])}/{fmt(mx)} and may need extra support.")
    else:
        notes.append("Every student who appeared is at or above the pass line.")
    if absent:
        notes.append(f"{len(absent)} student{'s were' if len(absent) != 1 else ' was'} absent.")
    best_bin = max(range(5), key=lambda i: ins["bin_counts"][i])
    notes.append(f"Most students ({ins['bin_counts'][best_bin]}) fall in the {ins['bin_labels'][best_bin]} mark band.")
    retakers = sum(1 for e, _ in scored if e.is_backlog)
    if retakers:
        notes.append(f"{retakers} of the students are re-attempting this paper (backlog).")

    prev = exam.offering.exams.filter(order__lt=exam.order).order_by("-order").first()
    if prev:
        pins = exam_insights_basic_pct(prev)
        if pins is not None:
            diff = round(ins["mean_pct"] - pins, 1)
            word = "up" if diff > 0 else "down" if diff < 0 else "unchanged"
            if diff == 0:
                notes.append(f"Average is unchanged compared with {prev.label}.")
            else:
                notes.append(f"Average percentage is {word} {abs(diff)} points compared with {prev.label}.")
    return ins


def exam_insights_basic_pct(exam):
    vals = [m.marks for m in exam.marks.filter(absent=False, marks__isnull=False)]
    if not vals:
        return None
    return round(float(sum(vals) / len(vals) * 100 / exam.max_marks), 1)


# --------------------------------------------------------------- marksheets
def build_sheets(enrollments):
    """Group enrolments into class marksheets keyed by (student, session, class at the time)."""
    rules = Rules()
    groups = OrderedDict()
    for e in with_related(enrollments.order_by("student__section", "student__admission_no",
                                               "offering__course__code")):
        groups.setdefault((e.student_id, e.offering.session, e.student_term_id), []).append(e)
    sheets = []
    for ens in groups.values():
        first = ens[0]
        results = [enrollment_result(e, rules) for e in ens]
        regular = [r for r in results if not r["is_backlog"]]
        retakes = [r for r in results if r["is_backlog"]]
        term = first.student_term
        sheet = {"student": first.student, "session": first.offering.session, "term": term,
                 "regular": regular, "retakes": retakes, "grand_total": None, "grand_max": None,
                 "percent": None, "sgpa": None, "division": "", "failed": [],
                 "total_credits": sum((r["credits"] for r in regular), ZERO)}
        if not regular:
            sheet["result"] = "RETAKE"
        elif any(r["status"] == "PENDING" for r in regular):
            sheet["result"] = "INCOMPLETE"
        else:
            gt = sum((r["total"] for r in regular), ZERO)
            gm = sum((r["max_total"] for r in regular), ZERO)
            sheet.update(grand_total=gt, grand_max=gm, percent=q2(gt * 100 / gm) if gm else ZERO)
            sheet["failed"] = [r["course"].code for r in regular if r["status"] == "FAIL"]
            if sheet["total_credits"] > 0 and rules.use_grades:
                pts = sum((r["grade_point"] * r["credits"] for r in regular), ZERO)
                sheet["sgpa"] = q2(pts / sheet["total_credits"])
            if sheet["failed"]:
                sheet["result"] = "FAIL"
            else:
                sheet["result"] = "PASS"
                sheet["division"] = rules.division(sheet["percent"])
        sheets.append(sheet)
    return sheets


def semester_sheets(session, term):
    return build_sheets(Enrollment.objects.filter(offering__session=session, student_term=term))


def student_sheets(student):
    sheets = build_sheets(Enrollment.objects.filter(student=student))
    sheets.sort(key=lambda s: (s["session"], s["term"].order))
    return sheets


# ------------------------------------------------------------------ helpers
def enroll(student, offering, is_backlog=False):
    obj, created = Enrollment.objects.get_or_create(
        student=student, offering=offering,
        defaults={"student_term": student.term, "is_backlog": is_backlog})
    return obj, created


def enroll_term_core(offering):
    """Enrol every active student of the course's class level (only for compulsory subjects)."""
    n = 0
    if not offering.course.compulsory:
        return n
    from .models import Student
    for s in Student.objects.filter(term=offering.course.term, is_active=True):
        n += enroll(s, offering)[1]
    return n
