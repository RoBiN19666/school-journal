"""Админка Django — создание методистов и администраторов."""
from django.contrib import admin
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group as AuthGroup
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    AcademicYear,
    Attendance,
    Branch,
    ContestResult,
    Group,
    IpRule,
    MailSettings,
    Room,
    Schedule,
    Student,
    Subject,
    TeacherAbsence,
    TeacherDevelopment,
    Term,
    User,
    VisitLog,
)


@admin.register(ContestResult)
class ContestResultAdmin(admin.ModelAdmin):
    list_display = ("date", "title", "student", "status", "subject")
    list_filter = ("status", "date")
    search_fields = ("title", "student__last_name", "subject")


@admin.register(TeacherDevelopment)
class TeacherDevelopmentAdmin(admin.ModelAdmin):
    list_display = ("date", "teacher", "kind", "program", "organization", "hours")
    list_filter = ("kind", "teacher")
    search_fields = ("program", "organization", "teacher__last_name")


@admin.register(TeacherAbsence)
class TeacherAbsenceAdmin(admin.ModelAdmin):
    """Отсутствия преподавателей: командировки и больничные."""

    list_display = (
        "teacher",
        "date_from",
        "date_to",
        "kind",
        "created_by",
        "created_at",
    )
    list_filter = ("kind",)
    search_fields = (
        "teacher__last_name",
        "teacher__first_name",
        "teacher__middle_name",
    )
    date_hierarchy = "date_from"


@admin.register(IpRule)
class IpRuleAdmin(admin.ModelAdmin):
    list_display = ("address", "kind", "comment", "created_at")
    list_filter = ("kind",)


@admin.register(MailSettings)
class MailSettingsAdmin(admin.ModelAdmin):
    list_display = ("host", "port", "username", "updated_at")


@admin.register(VisitLog)
class VisitLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "ip", "user", "username", "path", "detail")
    list_filter = ("kind", "ip")
    search_fields = ("ip", "username", "path", "detail")
    date_hierarchy = "created_at"


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "order")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "branch", "order")
    list_filter = ("branch",)


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("start_date", "end_date")


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ("year", "number", "start_date", "end_date")
    list_filter = ("year",)


class UserAddForm(UserCreationForm):
    """Добавление пользователя в админке: имя и фамилию спрашиваем сразу.

    Стандартная форма админки запрашивает только логин, поэтому учётную запись
    можно было создать без ФИО — а имя и фамилия обязательны.
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "last_name", "first_name")
        error_messages = {
            "first_name": {"required": "Укажите имя."},
            "last_name": {"required": "Укажите фамилию."},
        }


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserAddForm
    list_display = ("username", "last_name", "first_name", "role", "is_active")
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    fieldsets = BaseUserAdmin.fieldsets + (("Роль в журнале", {"fields": ("role",)}),)
    # Состав полей — как у стандартной админки, плюс имя и фамилия.
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "last_name",
                    "first_name",
                    "password1",
                    "password2",
                ),
            },
        ),
        ("Роль в журнале", {"fields": ("role",)}),
    )


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "birth_date")
    search_fields = ("last_name", "first_name")


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "teacher")
    list_filter = ("teacher",)


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "subject", "term")
    list_filter = ("subject", "term")
    filter_horizontal = ("students",)


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "group",
        "branch",
        "room",
        "day_of_week",
        "lesson_date",
        "start_time",
        "end_time",
        "teacher",
    )
    list_filter = ("day_of_week", "branch", "room", "teacher", "group__subject")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("student", "schedule", "date", "status")
    list_filter = ("status", "date")
    search_fields = ("student__last_name", "student__first_name")


# Служебные группы Django («Пользователи и группы») журналу не нужны:
# доступ строится на ролях пользователя, а не на правах групп. Модель
# остаётся в базе, но в админке не показывается, чтобы не путать
# с группами журнала («Электронный журнал» → «Группы»).
for _model in (AuthGroup,):
    try:
        admin.site.unregister(_model)
    except admin.sites.NotRegistered:
        pass
