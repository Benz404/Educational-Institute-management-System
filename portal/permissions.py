from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import Allotment, Exam, Offering, Student


def superuser_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def offerings_for(user):
    qs = Offering.objects.select_related("course__term")
    return qs if user.is_superuser else qs.filter(allotments__teacher=user).distinct()


def get_offering(user, pk):
    """A teacher can only open papers allotted to them; the superuser can open any."""
    return get_object_or_404(offerings_for(user), pk=pk)


def get_exam(user, pk):
    exam = get_object_or_404(Exam.objects.select_related("offering__course__term"), pk=pk)
    get_offering(user, exam.offering_id)
    return exam


def can_edit_exam(user, exam):
    return user.is_superuser or not exam.is_locked


def students_visible_to(user):
    """Teachers see only students enrolled in papers allotted to them."""
    if user.is_superuser:
        return Student.objects.select_related("term")
    return Student.objects.select_related("term").filter(
        enrollments__offering__allotments__teacher=user).distinct()
