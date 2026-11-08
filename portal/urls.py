from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views as v

urlpatterns = [
    path("", v.index, name="index"),
    path("setup/", v.setup, name="setup"),
    path("setup/<int:step>/", v.setup_step, name="setup_step"),
    path("branding/logo/", v.branding_logo, name="branding_logo"),
    path("settings/", v.site_settings, name="site_settings"),
    path("classes/", v.class_list, name="class_list"),
    path("classes/<int:pk>/", v.class_edit, name="class_edit"),
    path("syllabus/import/", v.course_import, name="course_import"),
    path("syllabus/sample.csv", v.course_sample_csv, name="course_sample_csv"),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/", auth_views.PasswordChangeView.as_view(success_url=reverse_lazy("password_done")), name="password_change"),
    path("password/done/", auth_views.PasswordChangeDoneView.as_view(), name="password_done"),
    path("dashboard/", v.dashboard, name="dashboard"),
    path("success/", v.success_page, name="success"),

    path("students/", v.student_list, name="student_list"),
    path("students/add/", v.student_add, name="student_add"),
    path("students/import/", v.student_import, name="student_import"),
    path("students/<int:pk>/", v.student_detail, name="student_detail"),
    path("students/<int:pk>/edit/", v.student_edit, name="student_edit"),

    path("teachers/", v.teacher_list, name="teacher_list"),
    path("teachers/add/", v.teacher_add, name="teacher_add"),

    path("syllabus/", v.course_list, name="course_list"),
    path("syllabus/add/", v.course_add, name="course_add"),

    path("papers/", v.offering_list, name="offering_list"),
    path("papers/open/", v.offering_open, name="offering_open"),
    path("papers/<int:pk>/", v.offering_detail, name="offering_detail"),
    path("papers/<int:pk>/allot/", v.offering_allot, name="offering_allot"),
    path("papers/<int:pk>/enrol/", v.offering_enrol, name="offering_enrol"),
    path("papers/<int:pk>/backlogs/", v.offering_backlogs, name="offering_backlogs"),

    path("exams/<int:pk>/", v.exam_detail, name="exam_detail"),
    path("exams/<int:pk>/marks/", v.exam_marks, name="exam_marks"),
    path("exams/<int:pk>/paper/upload/", v.exam_paper_upload, name="exam_paper_upload"),
    path("exams/<int:pk>/paper/", v.exam_paper_view, name="exam_paper_view"),
    path("exams/<int:pk>/paper/remove/", v.exam_paper_remove, name="exam_paper_remove"),
    path("exams/<int:pk>/lock/", v.exam_lock, name="exam_lock"),

    path("marksheets/", v.marksheets, name="marksheets"),
    path("marksheets/semester.pdf", v.marksheet_semester_pdf, name="marksheet_semester_pdf"),
    path("marksheets/student/<int:pk>.pdf", v.marksheet_student_pdf, name="marksheet_student_pdf"),

    path("backlogs/", v.backlog_list, name="backlog_list"),
    path("audit/", v.audit_log, name="audit_log"),
]
