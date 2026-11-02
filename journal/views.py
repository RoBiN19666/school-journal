"""Представления электронного журнала."""
from collections import Counter
from datetime import date as date_cls
from datetime import datetime
from datetime import time as time_cls
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import (
    Case,
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import TruncDate
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

#: Сообщения, подписи и заголовки, которые видит пользователь, переводятся лениво:
#: часть строк вычисляется при импорте модуля (константы, атрибуты классов), а язык
#: запроса известен только при показе страницы. gettext_lazy откладывает перевод
#: до обращения к строке и работает и в f-строках, и в операторе %.
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from . import __version__
from . import backup as backup_tools
from . import crypto
from . import demo as demo_tools
from . import demo_router
from . import excel
from . import image_export
from . import importers
from . import notifications
from . import periods as periods_tools
from . import security as security_tools
from . import setup as setup_tools
from . import updates as update_tools
from .absences import (
    absence_caption,
    absence_on,
    absences_by_teacher,
    pick_absence,
    schedule_absence_labels,
)
from .availability import (
    DEFAULT_DURATION_MINUTES,
    room_day_usage,
    teacher_availability,
)
from .pagination import ListPaginationMixin
from .substitutions import (
    absence_occurrences,
    busy_by_day,
    conflict_reasons,
    conflicts_for,
    create_substitutions,
    existing_one_offs,
    hard_conflict,
    program_teachers,
    replaced_lessons,
    row_conflicts,
    row_key,
    report_skipped,
    report_text,
    soft_conflict,
    substitution_map,
    substitution_place,
)

from .forms import (
    AcademicYearForm,
    ContestResultForm,
    GroupForm,
    IpRuleForm,
    MailSettingsForm,
    OrganizationNameForm,
    SiteSettingsForm,
    ProfileForm,
    RestoreBackupForm,
    ScheduleForm,
    SetupAdminForm,
    SetupSiteSettingsForm,
    StaffMemberCreateForm,
    StaffMemberEditForm,
    StudentForm,
    StudentImportForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
    SubjectForm,
    TeacherAbsenceForm,
    TeacherCreateForm,
    TeacherDevelopmentForm,
    TeacherEditForm,
    teacher_choices,
)
from .models import (
    AcademicYear,
    Attendance,
    Branch,
    ContestResult,
    Group,
    IpRule,
    LessonTopic,
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
    SiteSettings,
)

ISSUED_PASSWORD_KEY = "issued_password"
IMPORT_SESSION_KEY = "student_import"


def remember_password(request, user, password):
    """Пароль показываем один раз на следующей странице — чтобы его можно было записать."""
    if not password:
        return
    request.session[ISSUED_PASSWORD_KEY] = {
        "name": user.full_name,
        "username": user.username,
        "password": password,
    }


def pop_issued_password(request):
    return request.session.pop(ISSUED_PASSWORD_KEY, None)


# --- Миксины доступа ---------------------------------------------------------
class RoleRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Нет прав — возвращаем пользователя на его домашнюю страницу."""

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        messages.error(self.request, _("Недостаточно прав для этого раздела."))
        return redirect("home")


class StaffRequiredMixin(RoleRequiredMixin):
    """Администратор или методист."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_staff_member


class AdminRequiredMixin(RoleRequiredMixin):
    """Администратор: главный администратор или обычный администратор."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_admin


class SuperadminRequiredMixin(RoleRequiredMixin):
    """Только главный администратор: резервные копии и восстановление."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_superadmin


class TeacherRequiredMixin(RoleRequiredMixin):
    """Только преподаватель."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_teacher


class ActivePeriodMixin:
    """Даёт представлению активные учебный год, полугодие и адрес (филиал)."""

    def dispatch(self, request, *args, **kwargs):
        self.period_year, self.period_term = periods_tools.resolve_period(request)
        self.period_branch = periods_tools.resolve_branch(request)
        return super().dispatch(request, *args, **kwargs)

    def week_monday(self):
        """Понедельник показываемой недели — в границах активного полугодия.

        Листать недели за полугодие нельзя: занятий там нет. Неделя из адреса
        может оказаться вне границ (старая ссылка, правка периода) — показываем
        ближайшую допустимую и сообщаем об этом вместо ошибки 404. Если у
        полугодия не заполнены даты, ограничения нет.
        """
        requested = self.request.GET.get("week")
        monday, _unused = week_bounds(requested)
        monday, clamped = clamp_week(monday, self.period_term)
        if clamped and requested:
            messages.info(
                self.request,
                _("Эта неделя вне полугодия: показана ближайшая — %(date)s.")
                % {"date": f"{monday:%d.%m.%Y}"},
            )
        return monday, monday + timedelta(days=5)


# --- Аутентификация и домашняя страница --------------------------------------
class JournalLoginView(LoginView):
    """Вход с защитой от подбора пароля и уведомлениями администратору."""

    template_name = "journal/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        address = security_tools.get_client_ip(self.request)
        context["login_locked"] = security_tools.is_login_locked(
            address, self.request.POST.get("username") or None
        )
        return context

    def post(self, request, *args, **kwargs):
        address = security_tools.get_client_ip(request)
        username = (request.POST.get("username") or "").strip() or None
        if security_tools.is_login_locked(address, username):
            messages.error(
                request,
                _(
                    "Слишком много неудачных попыток входа. "
                    "Попробуйте снова через %(minutes)s минут."
                )
                % {"minutes": security_tools.LOCK_MINUTES},
            )
            return redirect("login")
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        user = form.get_user()
        # «Сохранить вход»: продлеваем сессию, иначе действует обычный срок из настроек.
        # Django при входе меняет ключ сессии (защита от подмены) и хранит хеш пароля,
        # поэтому смена пароля автоматически закрывает такие сессии.
        if self.request.POST.get("remember_me"):
            self.request.session.set_expiry(settings.REMEMBER_ME_DAYS * 24 * 3600)
        else:
            self.request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        security_tools.record_event(
            VisitLog.Kind.LOGIN,
            request=self.request,
            user=user,
            path=self.request.path,
        )
        notifications.notify(
            "logins",
            _("Вход в журнал"),
            f"Пользователь: {user.full_name} ({user.username}), роль: {user.get_role_display()}.\n"
            f"IP-адрес: {security_tools.get_client_ip(self.request)}\n"
            f"Браузер: {self.request.META.get('HTTP_USER_AGENT', '')[:200]}",
        )
        return response

    def form_invalid(self, form):
        request = self.request
        address = security_tools.get_client_ip(request)
        username = (request.POST.get("username") or "")[:150]
        security_tools.record_event(
            VisitLog.Kind.LOGIN_FAILED,
            request=request,
            username=username,
            path=request.path,
            detail=_("неверный логин или пароль"),
        )
        notifications.notify(
            "failed_logins",
            _("Неудачная попытка входа"),
            f"Логин: {username or '—'}\nIP-адрес: {address}\n"
            f"Неудачных попыток за {security_tools.LOCK_MINUTES} минут: "
            f"{security_tools.failed_attempts(address)} из {security_tools.MAX_FAILED_ATTEMPTS}",
        )
        return super().form_invalid(form)


def branding_file(request, path):
    """Отдаёт только картинки брендинга (логотип и фавикон).

    Несмотря на то что загруженные файлы лежат в отдельной папке, маршрут закрываем
    дополнительно: любые другие файлы и любые не-картинки отсюда не отдаются.
    """
    from pathlib import Path as _Path

    from django.views.static import serve as _serve

    clean = (path or "").replace("\\", "/").lstrip("/")
    parts = [part for part in clean.split("/") if part]
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".ico"}

    if not parts or parts[0] != "branding":
        raise Http404(_("Файл не найден."))
    if any(part in ("..", ".") for part in parts):
        raise Http404(_("Файл не найден."))
    if _Path(clean).suffix.lower() not in allowed:
        raise Http404(_("Файл не найден."))

    return _serve(request, clean, document_root=settings.MEDIA_ROOT)


class JournalLogoutView(LogoutView):
    next_page = reverse_lazy("login")
    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "user", None) and request.user.is_authenticated:
            security_tools.record_event(
                VisitLog.Kind.LOGOUT, request=request, user=request.user, path=request.path
            )
        return super().dispatch(request, *args, **kwargs)


class SetupView(View):
    """Первоначальная настройка журнала на чистой базе: /setup/.

    Один экран: первая учётная запись (главный администратор) и внешний вид
    журнала. Показывается только тогда, когда в журнале ещё нет ни одного
    администратора; на настроенном журнале страница просто уводит на сводку,
    чтобы второй раз мастер не показывался.
    """

    template_name = "journal/setup/index.html"

    def dispatch(self, request, *args, **kwargs):
        if not setup_tools.setup_required():
            # Журнал уже настроен: формы быть не должно даже по прямому адресу.
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return self.render_page(request, SetupAdminForm(), self.settings_form())

    def post(self, request):
        admin_form = SetupAdminForm(request.POST)
        site_form = self.settings_form(request.POST, request.FILES)
        # Проверяем обе формы всегда: иначе ошибки второй не будут видны.
        admin_valid = admin_form.is_valid()
        site_valid = site_form.is_valid()
        if not (admin_valid and site_valid):
            return self.render_page(request, admin_form, site_form)

        with transaction.atomic():
            site_form.save()
            user = admin_form.save()
        login(request, user)
        security_tools.record_event(
            VisitLog.Kind.EVENT,
            request=request,
            user=user,
            path=request.path,
            detail=_("первоначальная настройка журнала, создан %(username)s")
            % {"username": user.username},
        )
        messages.success(request, _("Настройка завершена. Добро пожаловать в журнал!"))
        return redirect("dashboard")

    @staticmethod
    def settings_form(*args, **kwargs):
        """Форма оформления: та же модель и те же проверки, что в «Кастомизации»."""
        kwargs.setdefault("instance", SiteSettings.load())
        return SetupSiteSettingsForm(*args, **kwargs)

    def render_page(self, request, admin_form, site_form):
        return render(
            request,
            self.template_name,
            {
                "title": _("Первоначальная настройка журнала"),
                "form": admin_form,
                "site_form": site_form,
            },
        )


class HomeView(LoginRequiredMixin, View):
    """Редирект в зависимости от роли."""

    def get(self, request):
        if request.user.is_staff_member:
            return redirect("dashboard")
        if request.user.is_teacher:
            return redirect("teacher_dashboard")
        messages.error(request, _("Для вашей учётной записи не назначена роль."))
        return redirect("login")


# --- Дашборд -----------------------------------------------------------------
class AboutView(TemplateView):
    """Страница о продукте: версия, автор, возможности, технологии."""

    template_name = "journal/about.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Название организации выводится из настроек (см. context_processors.branding).
        # Форму правки показываем только главному администратору: остальным она
        # не нужна, а права всё равно проверяются на сервере в OrganizationNameView.
        if self.request.user.is_authenticated and self.request.user.is_superadmin:
            context.setdefault(
                "organization_form",
                OrganizationNameForm(instance=SiteSettings.load()),
            )
            # Обновления проверяет только главный администратор: он решает,
            # когда обновлять сервер, и только ему показывается этот блок.
            # Для остальных ролей GitHub не запрашивается вовсе.
            release = update_tools.latest_release()
            context["update_available"] = (
                release
                if release is not None
                and update_tools.is_newer(__version__, release["tag"])
                else None
            )
            context["update_check_failed"] = release is None
            context["update_releases_url"] = update_tools.RELEASES_PAGE_URL
        return context


class OrganizationNameView(AdminRequiredMixin, View):
    """Смена названия организации со страницы «О программе».

    Кнопка-карандаш есть только у главного администратора, но права проверяются
    и здесь: обычный администратор и методист получают отказ, даже если отправят
    POST напрямую, в обход спрятанной кнопки.
    """

    def post(self, request, *args, **kwargs):
        if not request.user.is_superadmin:
            raise PermissionDenied(
                _("Название организации меняет только главный администратор.")
            )
        site = SiteSettings.load()
        form = OrganizationNameForm(request.POST, instance=site)
        if form.is_valid():
            form.save()
            messages.success(request, _("Название организации сохранено."))
            return redirect("about")
        # Ничего не сохраняем и не теряем: форма возвращается с введённым
        # значением и понятной ошибкой под полем.
        messages.error(request, _("Не удалось сохранить название организации."))
        return render(
            request,
            "journal/about.html",
            {"organization_form": form},
            status=200,
        )


class FaqView(TemplateView):
    """Справка: что где лежит — понятным языком, по ролям."""

    template_name = "journal/faq.html"


#: Сколько дней показывать на гистограмме входов на дашборде.
DASHBOARD_CHART_DAYS = 14
#: За сколько дней (включая сегодня) считать отметки посещаемости в итогах.
DASHBOARD_ATTENDANCE_DAYS = 7
#: Сколько последних входов показывать администратору.
DASHBOARD_RECENT_LOGINS = 10
#: Короткие названия дней недели для подписей под столбиками.
WEEKDAY_SHORT = (
    _("пн"), _("вт"), _("ср"), _("чт"), _("пт"), _("сб"), _("вс"),
)


def local_day_start(day, tzinfo=None):
    """Начало дня по местному времени: с учётом часового пояса, если он включён."""
    moment = datetime.combine(day, time_cls.min)
    if tzinfo is not None:
        moment = timezone.make_aware(moment, tzinfo)
    return moment


def daily_login_series(day_totals, today, days=DASHBOARD_CHART_DAYS):
    """Столбики гистограммы входов: дата, подпись, число входов и высота в процентах.

    ``day_totals`` — словарь «дата → сколько входов». Высота считается от максимума
    за период: самый высокий столбик занимает всю высоту. Пустой журнал даёт нули,
    поэтому деления на ноль не будет.
    """
    days = max(int(days), 1)
    first = today - timedelta(days=days - 1)
    counts = [
        max(int(day_totals.get(first + timedelta(days=offset)) or 0), 0)
        for offset in range(days)
    ]
    maximum = max(counts) if counts else 0
    series = []
    for offset, count in enumerate(counts):
        day = first + timedelta(days=offset)
        series.append(
            {
                "date": day,
                "label": f"{day:%d.%m}",
                "weekday": WEEKDAY_SHORT[day.weekday()],
                "count": count,
                # Пустые дни оставляем с нулевой высотой, но с подписью даты:
                # иначе шкала дней «съезжает».
                "percent": round(count * 100 / maximum, 1) if maximum else 0,
                "is_today": day == today,
            }
        )
    return series


def distinct_people(queryset):
    """Сколько разных людей входило: по учётной записи, а без неё — по логину."""
    people = set()
    for user_id, username in queryset.values_list("user_id", "username"):
        if user_id:
            people.add(("id", user_id))
        elif username:
            people.add(("login", username.strip().lower()))
    return len(people)


def dashboard_statistics():
    """Сводка для блока «Статистика» на дашборде администратора.

    Считает только то, что есть в базе: пустой журнал событий даёт нули, а не
    ошибку, поэтому свежая установка работает как обычно.
    """
    now = timezone.now()
    if timezone.is_aware(now):
        now = timezone.localtime(now)
        tzinfo = now.tzinfo
    else:
        tzinfo = None
    today = now.date()
    today_start = local_day_start(today, tzinfo)
    week_start = today_start - timedelta(days=6)  # 7 дней, включая сегодня
    month_start = today_start - timedelta(days=29)  # 30 дней, включая сегодня

    logins = VisitLog.objects.filter(kind=VisitLog.Kind.LOGIN)
    chart_start = today_start - timedelta(days=DASHBOARD_CHART_DAYS - 1)
    day_totals = {}
    for row in (
        logins.filter(created_at__gte=chart_start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
    ):
        day = row["day"]
        if day is None:
            continue
        day_totals[day.date() if hasattr(day, "date") else day] = row["total"]

    chart = daily_login_series(day_totals, today)
    attendance_start = today - timedelta(days=DASHBOARD_ATTENDANCE_DAYS - 1)

    return {
        "today": today,
        "chart_days": DASHBOARD_CHART_DAYS,
        "recent_limit": DASHBOARD_RECENT_LOGINS,
        "logins_today": logins.filter(created_at__gte=today_start).count(),
        "logins_week": logins.filter(created_at__gte=week_start).count(),
        "logins_month": logins.filter(created_at__gte=month_start).count(),
        "people_today": distinct_people(logins.filter(created_at__gte=today_start)),
        "people_week": distinct_people(logins.filter(created_at__gte=week_start)),
        "chart": chart,
        "chart_total": sum(item["count"] for item in chart),
        "chart_peak": max((item["count"] for item in chart), default=0),
        "totals": [
            {
                "label": _("Отметки посещаемости за %(days)s дней")
                % {"days": DASHBOARD_ATTENDANCE_DAYS},
                "value": Attendance.objects.filter(
                    date__gte=attendance_start, date__lte=today
                ).count(),
                "url": "week_journal",
            },
            {
                "label": _("Повышение квалификации педагогов"),
                "value": TeacherDevelopment.objects.count(),
                "url": "development",
            },
            {
                "label": _("Участие в конкурсах"),
                "value": ContestResult.objects.count(),
                "url": "contests",
            },
        ],
        "recent_logins": list(
            logins.select_related("user").order_by("-created_at")[
                :DASHBOARD_RECENT_LOGINS
            ]
        ),
    }


class DashboardView(StaffRequiredMixin, ActivePeriodMixin, TemplateView):
    template_name = "journal/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = self.period_term
        branch = self.period_branch
        subjects = Subject.objects.select_related("teacher", "branch", "term")
        if term is not None:
            subjects = subjects.filter(term=term)
        if branch is not None:
            subjects = subjects.filter(branch=branch)
        context["students_count"] = Student.objects.count()
        context["subjects_count"] = subjects.count()
        context["groups_count"] = Group.objects.filter(term=term).count()
        context["teachers_count"] = User.objects.filter(
            role=User.Role.TEACHER
        ).count()
        context["schedules_count"] = Schedule.objects.filter(group__term=term).count()
        context["students"] = Student.objects.all()[:5]
        context["branch"] = branch
        context["subjects"] = subjects.annotate(
            groups_count=Count("groups", distinct=True)
        )[:5]
        if self.request.user.is_admin:
            # Статистику по журналу событий видят только администраторы.
            context["dashboard_stats"] = dashboard_statistics()
        return context


# --- Ученики -----------------------------------------------------------------
class StudentListView(StaffRequiredMixin, ListPaginationMixin, ListView):
    model = Student
    template_name = "journal/students/list.html"
    context_object_name = "students"

    def get_queryset(self):
        # Порядок задаём явно: при annotate() Meta.ordering не применяется,
        # а постраничному выводу нужен устойчивый порядок записей.
        queryset = Student.objects.annotate(
            groups_count=Count("groups", distinct=True)
        ).order_by("last_name", "first_name", "pk")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(last_name__icontains=query)
                | Q(first_name__icontains=query)
                | Q(middle_name__icontains=query)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "")
        return context


class StudentCreateView(StaffRequiredMixin, CreateView):
    model = Student
    form_class = StudentForm
    template_name = "journal/students/form.html"
    success_url = reverse_lazy("students")

    def form_valid(self, form):
        messages.success(self.request, _("Ученик добавлен."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Новый ученик")
        return context


class StudentUpdateView(StaffRequiredMixin, UpdateView):
    model = Student
    form_class = StudentForm
    template_name = "journal/students/form.html"
    success_url = reverse_lazy("students")

    def form_valid(self, form):
        messages.success(self.request, _("Данные ученика обновлены."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Ученик · %(name)s") % {"name": self.object.full_name}
        return context


class StudentDeleteView(StaffRequiredMixin, DeleteView):
    model = Student
    template_name = "journal/confirm_delete.html"
    success_url = reverse_lazy("students")

    def form_valid(self, form):
        messages.success(
            self.request,
            _("Ученик «%(name)s» удалён.") % {"name": self.object.full_name},
        )
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Удаление ученика")
        context["object_name"] = self.object.full_name
        context["cancel_url"] = reverse("students")
        context["details"] = [
            _("Групп, из которых он будет исключён: %(count)s")
            % {"count": self.object.groups.count()},
            _("Отметок посещаемости будет удалено: %(count)s")
            % {"count": self.object.attendances.count()},
        ]
        return context


# --- Программы и группы -------------------------------------------------------
class SubjectListView(
    StaffRequiredMixin, ActivePeriodMixin, ListPaginationMixin, ListView
):
    model = Subject
    template_name = "journal/subjects/list.html"
    context_object_name = "subjects"

    def get_queryset(self):
        queryset = Subject.objects.select_related("teacher", "branch", "term")
        if self.period_term is not None:
            queryset = queryset.filter(term=self.period_term)
        if self.period_branch is not None:
            queryset = queryset.filter(branch=self.period_branch)
        return queryset.annotate(
            groups_count=Count("groups", distinct=True)
        ).order_by("name", "pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["term"] = self.period_term
        context["branch"] = self.period_branch
        others = Subject.objects.exclude(term=self.period_term) if self.period_term else Subject.objects.none()
        if self.period_branch is not None:
            others = others.exclude(branch=self.period_branch)
        context["other_scope_count"] = others.count()
        return context


class SubjectCreateView(StaffRequiredMixin, ActivePeriodMixin, CreateView):
    model = Subject
    form_class = SubjectForm
    template_name = "journal/subjects/form.html"
    success_url = reverse_lazy("subjects")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["term"] = self.period_term
        kwargs["branch"] = self.period_branch
        return kwargs

    def form_valid(self, form):
        form.instance.term = self.period_term
        form.instance.branch = self.period_branch
        messages.success(
            self.request,
            _("Программа «%(name)s» создан в %(branch)s, %(term)s.")
            % {
                "name": form.instance.name,
                "branch": self.period_branch.name,
                "term": self.period_term.title,
            },
        )
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Новый программа")
        context["term"] = self.period_term
        context["branch"] = self.period_branch
        return context


class SubjectUpdateView(StaffRequiredMixin, UpdateView):
    model = Subject
    form_class = SubjectForm
    template_name = "journal/subjects/form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["term"] = self.object.term
        kwargs["branch"] = self.object.branch
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, _("Программа обновлена."))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("subject_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Программа · %(name)s") % {"name": self.object.name}
        context["term"] = self.object.term
        context["branch"] = self.object.branch
        context["cancel_url"] = reverse("subject_detail", args=[self.object.pk])
        return context


class SubjectDeleteView(StaffRequiredMixin, DeleteView):
    model = Subject
    template_name = "journal/confirm_delete.html"
    success_url = reverse_lazy("subjects")

    def form_valid(self, form):
        messages.success(
            self.request,
            _("Программа «%(name)s» удалена.") % {"name": self.object.name},
        )
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        groups = self.object.groups.count()
        schedules = Schedule.objects.filter(group__subject=self.object).count()
        context["title"] = _("Удаление программы")
        context["object_name"] = self.object.name
        context["cancel_url"] = reverse("subjects")
        context["details"] = [
            _("Групп будет удалено: %(count)s") % {"count": groups},
            _("Занятий в расписании будет удалено: %(count)s") % {"count": schedules},
            _("Вся посещаемость по этим занятиям тоже будет удалена."),
        ]
        return context


class SubjectDetailView(StaffRequiredMixin, ActivePeriodMixin, DetailView):
    model = Subject
    template_name = "journal/subjects/detail.html"
    context_object_name = "subject"

    def get_queryset(self):
        # Программа открывается всегда: если он из другого полугодия или адреса,
        # это видно на самой странице, а не превращается в 404.
        return Subject.objects.select_related("teacher", "branch", "term")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = self.object.term
        context["term"] = term
        context["branch"] = self.object.branch
        context["term_groups"] = (
            self.object.groups.filter(term=term)
            .prefetch_related("students")
            .select_related("subject")
        )
        problem_groups = self.object.groups.exclude(term=term).count()
        context["other_terms_count"] = problem_groups
        context["all_students_count"] = Student.objects.count()
        context["same_name_other_scopes"] = (
            Subject.objects.filter(name=self.object.name)
            .exclude(pk=self.object.pk)
            .count()
        )
        return context


class GroupCreateView(StaffRequiredMixin, ActivePeriodMixin, CreateView):
    model = Group
    form_class = GroupForm
    template_name = "journal/subjects/form.html"

    def dispatch(self, request, *args, **kwargs):
        self.subject = get_object_or_404(
            Subject.objects.select_related("branch", "term"), pk=kwargs["subject_id"]
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        # Программа и полугодие известны заранее — так проверка уникальности работает в форме.
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = Group(subject=self.subject, term=self.subject.term)
        return kwargs

    def form_valid(self, form):
        form.instance.subject = self.subject
        form.instance.term = self.subject.term
        if self.subject.term:
            message = _("Группа создана в полугодии «%(term)s» (%(branch)s).") % {
                "term": self.subject.term.title,
                "branch": self.subject.branch.name,
            }
        else:
            message = _("Группа создана.")
        messages.success(self.request, message)
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("subject_detail", args=[self.subject.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Новая группа · %(name)s") % {"name": self.subject.name}
        context["cancel_url"] = reverse("subject_detail", args=[self.subject.pk])
        context["term"] = self.subject.term
        context["branch"] = self.subject.branch
        return context


class GroupUpdateView(StaffRequiredMixin, UpdateView):
    model = Group
    form_class = GroupForm
    template_name = "journal/subjects/form.html"

    def form_valid(self, form):
        messages.success(self.request, _("Группа обновлена."))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("subject_detail", args=[self.object.subject.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Группа · %(name)s") % {"name": self.object.name}
        context["cancel_url"] = reverse("subject_detail", args=[self.object.subject.pk])
        return context


class GroupDeleteView(StaffRequiredMixin, DeleteView):
    model = Group
    template_name = "journal/confirm_delete.html"

    def form_valid(self, form):
        messages.success(
            self.request,
            _("Группа «%(name)s» удалена.") % {"name": self.object.name},
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("subject_detail", args=[self.subject_pk])

    def dispatch(self, request, *args, **kwargs):
        self.subject_pk = get_object_or_404(Group, pk=kwargs["pk"]).subject_id
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Удаление группы")
        context["object_name"] = f"{self.object.name} ({self.object.subject.name})"
        context["cancel_url"] = reverse(
            "subject_detail", args=[self.object.subject.pk]
        )
        context["details"] = [
            _("Учеников в группе: %(count)s") % {"count": self.object.students.count()},
            _("Занятий в расписании будет удалено: %(count)s")
            % {"count": self.object.schedules.count()},
            _("Посещаемость этих занятий тоже будет удалена. Сами ученики останутся в базе."),
        ]
        return context


class GroupStudentsView(StaffRequiredMixin, View):
    """Состав группы: постраничный список учеников, поиск по всей базе, сохранение галочками."""

    template_name = "journal/subjects/group_students.html"
    PER_PAGE_CHOICES = (10, 25, 50, 100)
    DEFAULT_PER_PAGE = 25

    def get_group(self):
        return get_object_or_404(
            Group.objects.select_related("subject"), pk=self.kwargs["pk"]
        )

    def get_per_page(self, request):
        try:
            value = int(request.GET.get("per_page") or self.DEFAULT_PER_PAGE)
        except (TypeError, ValueError):
            value = self.DEFAULT_PER_PAGE
        return value if value in self.PER_PAGE_CHOICES else self.DEFAULT_PER_PAGE

    def get_context(self, group, request):
        query = (request.GET.get("q") or "").strip()
        per_page = self.get_per_page(request)
        # Поиск идёт по всей базе, а не только по видимой странице.
        students = Student.objects.all().order_by("last_name", "first_name", "middle_name")
        if query:
            students = students.filter(
                Q(last_name__icontains=query)
                | Q(first_name__icontains=query)
                | Q(middle_name__icontains=query)
            )
        paginator = Paginator(students, per_page)
        page = paginator.get_page(request.GET.get("page"))
        current = set(group.students.values_list("pk", flat=True))
        rows = [
            {"student": student, "checked": student.pk in current}
            for student in page.object_list
        ]
        shown = {student.pk for student in page.object_list}
        # Ученики из группы, которых нет на этой странице: их сохраняем скрытыми полями,
        # иначе сохранение со страницы выкинуло бы их из группы.
        keep = group.students.exclude(pk__in=shown).order_by("last_name", "first_name")
        return {
            "group": group,
            "title": _("Состав группы · %(name)s") % {"name": group.name},
            "rows": rows,
            "page": page,
            "paginator": paginator,
            "query": query,
            "per_page": per_page,
            "per_page_choices": self.PER_PAGE_CHOICES,
            "checked_count": len(current),
            "found_count": paginator.count,
            "total_count": Student.objects.count(),
            "group_count": len(current),
            "keep_students": keep,
            "hidden_count": keep.count(),
        }

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context(self.get_group(), request))

    def post(self, request, *args, **kwargs):
        group = self.get_group()
        selected = [value for value in request.POST.getlist("students") if value.isdigit()]
        group.students.set(Student.objects.filter(pk__in=selected))
        messages.success(
            request,
            _("Состав группы «%(name)s» сохранён: %(selected)s уч. "
              "(в группе всего %(total)s).")
            % {
                "name": group.name,
                "selected": len(selected),
                "total": group.students.count(),
            },
        )
        target = reverse("group_students", args=[group.pk])
        query = request.POST.get("back", "")
        return redirect(f"{target}?{query}" if query else target)


# --- Расписание --------------------------------------------------------------
def schedule_form_context(request, schedule=None, term=None, branch=None):
    """Подсказка для формы занятия: занятость преподавателя и кабинетов."""
    group_id = request.GET.get("group") or request.POST.get("group")
    group = None
    if group_id and str(group_id).isdigit():
        group = (
            Group.objects.select_related("subject__teacher")
            .prefetch_related("subject__teachers")
            .filter(pk=int(group_id))
            .first()
        )
    if group is None and schedule is not None:
        group = schedule.group
    # Преподавателя можно выбрать из тех, кто ведёт программу; в занятости показываем выбранного.
    teacher = None
    teacher_id = request.POST.get("teacher") or request.GET.get("teacher")
    if group is not None:
        options = group.subject.teacher_options
        if teacher_id and str(teacher_id).isdigit():
            teacher = next((item for item in options if item.pk == int(teacher_id)), None)
        if teacher is None and schedule is not None and schedule.teacher_id:
            teacher = next(
                (item for item in options if item.pk == schedule.teacher_id), None
            )
        if teacher is None:
            teacher = group.subject.teacher or (options[0] if options else None)
    if group is not None:
        term = group.term

    # Адрес занятия определяется программой выбранной группы.
    if group is not None and group.subject.branch_id:
        branch = group.subject.branch
    if branch is None and schedule is not None and schedule.branch_id:
        branch = schedule.branch

    # День, выбранный в форме, показываем первым, остальные — ниже.
    selected_day = ""
    if request.method == "POST" and request.POST.get("day_of_week") not in (None, ""):
        selected_day = str(request.POST.get("day_of_week"))
    elif request.GET.get("day") not in (None, ""):
        selected_day = str(request.GET.get("day"))
    elif schedule is not None:
        selected_day = str(schedule.day_of_week)

    # Дата разового занятия: день недели и занятость кабинетов считаем по ней.
    one_off_date = parse_iso_date(
        request.POST.get("lesson_date") or request.GET.get("lesson_date")
    )
    if one_off_date is None and schedule is not None:
        one_off_date = schedule.lesson_date
    if one_off_date is not None:
        selected_day = str(one_off_date.weekday())

    days = teacher_availability(
        teacher,
        exclude_pk=schedule.pk if schedule else None,
        term=term,
        # Для разового занятия панель считаем по его дате: разовые занятия других
        # дат того же дня недели окна не занимают.
        day=one_off_date,
    )
    selected = next(
        (day for day in days if str(day["value"]) == selected_day), None
    )
    others = [day for day in days if day is not selected]

    room_usage = []
    room_usage_day_label = ""
    # День недели приходит и из адреса: там может оказаться мусор («day=99»).
    # Такой день просто не показываем — страница открывается, а не падает.
    if (
        branch is not None
        and selected_day.isdigit()
        and 0 <= int(selected_day) <= 6
    ):
        room_usage = room_day_usage(
            branch,
            int(selected_day),
            term=term,
            exclude_pk=schedule.pk if schedule else None,
            day=one_off_date,
        )
        room_usage_day_label = Schedule.Weekday(int(selected_day)).label

    return {
        "availability_selected": selected,
        "availability_others": others,
        "availability_teacher": teacher,
        "availability_is_extra": bool(
            # «Подменяющий» — только тот, кто вообще не заявлен у программы.
            # Если программа ведут два преподавателя, это не подмена.
            teacher is not None
            and group is not None
            and teacher.pk not in {item.pk for item in group.subject.teacher_options}
        ),
        "availability_term": term,
        "availability_branch": branch,
        "room_usage": room_usage,
        "room_usage_day": selected_day,
        "room_usage_day_label": room_usage_day_label,
        "room_usage_date": one_off_date,
        "selected_group_id": str(group.pk) if group else "",
        "selected_day": selected_day,
        "duration_minutes": DEFAULT_DURATION_MINUTES,
    }


class ScheduleListView(StaffRequiredMixin, ActivePeriodMixin, TemplateView):
    template_name = "journal/schedule/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = self.period_term
        branch = self.period_branch
        queryset = (
            Schedule.objects.filter(group__term=term, branch=branch)
            .select_related("group__subject", "group__term", "teacher", "room")
            # Шаблон списка показывает число учеников группы. Считаем его один раз
            # аннотацией (students_count) и подгружаем учеников одним запросом:
            # тогда lesson.group.students.count берётся из кеша prefetch, без
            # запроса на каждое занятие.
            .annotate(students_count=Count("group__students", distinct=True))
            .prefetch_related("group__students")
            .all()
        )

        subject_id = self.request.GET.get("subject")
        if subject_id and subject_id.isdigit():
            queryset = queryset.filter(group__subject_id=int(subject_id))

        # Расписание показывает ОДНУ неделю (по умолчанию текущую, листание —
        # `?week=`): обычные занятия повторяются каждую неделю, а разовые (в том
        # числе подмены) проходят только в свою дату — оставляем те, что попадают
        # в показанную неделю.
        monday, _saturday = self.week_monday()
        sunday = monday + timedelta(days=6)
        items = [
            item
            for item in queryset
            if item.lesson_date is None or monday <= item.lesson_date <= sunday
        ]
        # Пометки «отсутствует 12.10–20.10»: график недельный, конкретной даты у
        # обычного занятия нет, поэтому период отсутствия ищется в полугодии группы.
        # Показываем период показанной недели, а на текущей неделе — ещё и
        # ближайший будущий (см. ``absences.schedule_absence_labels``).
        # Пометку вешаем на сами занятия — они же попадают и в сетку, и в список.
        absence_labels = schedule_absence_labels(items, week=(monday, sunday))
        # Подмена отвечает за своего преподавателя (заменяющего), а не за того,
        # кого заменяют: у неё вместо пометки об отсутствии — подпись «для кого».
        replaced = replaced_lessons([item for item in items if item.is_substitution])
        for item in items:
            if item.is_substitution:
                item.absence = None
            else:
                item.absence = absence_labels.get(item.pk)
            lesson, absence = replaced.get(item.pk, (None, None))
            item.replacement_for = lesson
            item.replacement_caption = (
                str(absence_caption(absence)) if absence is not None else ""
            )
        # Дальше список занятий используется и в day_list — вместе с students_count.
        days = Schedule.Weekday.choices

        # Строки таблицы — это сами временные промежутки занятий: номер урока не нужен,
        # таблица подстраивается под то время, которое указали.
        slots = sorted({(item.start_time, item.end_time) for item in items})
        grid = {}
        for item in items:
            grid.setdefault((item.start_time, item.end_time), {}).setdefault(
                item.day_of_week, []
            ).append(item)

        rows = [
            {
                "time_label": f"{start:%H:%M}–{end:%H:%M}",
                "minutes": (end.hour * 60 + end.minute)
                - (start.hour * 60 + start.minute),
                "cells": [
                    grid.get((start, end), {}).get(day_value, [])
                    for day_value, _ in days
                ],
            }
            for start, end in slots
        ]

        # Список: по дням, внутри дня — по времени начала занятия.
        day_list = []
        for day_value, label in days:
            lessons = sorted(
                (item for item in items if item.day_of_week == day_value),
                key=lambda item: item.sort_key,
            )
            if lessons:
                day_list.append({"label": label, "lessons": lessons})

        view_mode = self.request.GET.get("view", "grid")
        if view_mode not in {"grid", "list"}:
            view_mode = "grid"

        context.update(
            {
                "term": term,
                "branch": branch,
                "branch_lessons": Schedule.objects.filter(group__term=term).count(),
                "other_branches_lessons": Schedule.objects.filter(
                    group__term=term
                )
                .exclude(branch=branch)
                .count(),
                "subjects": Subject.objects.filter(
                    term=term, branch=branch
                ).select_related("teacher"),
                "selected_subject": subject_id or "",
                "days": days,
                "rows": rows,
                "day_list": day_list,
                "view_mode": view_mode,
                "schedules_count": len(items),
                "slots_count": len(slots),
                # В шапке пишем, какая неделя показана: разовые занятия видны
                # только этой недели, а листание ограничено полугодием.
                "monday": monday,
                "week_start": monday,
                "week_end": sunday,
            }
        )
        context.update(week_navigation(term, monday))
        return context
        return context


def schedule_initial_from_request(request):
    """Значения формы занятия из адреса: чтобы выбранные группа, день и время не терялись.

    Форма перезагружается при смене группы, преподавателя и дня недели — панели занятости
    пересчитываются, а выбранные значения берутся отсюда. Так же приходит и переход
    «Поставить подмену» с отменённого занятия: группа, кабинет, день, дата и время
    уже заполнены, методисту остаётся выбрать преподавателя.

    Значения проверяются: нечисловой идентификатор, чужой день недели или мусорная
    дата просто игнорируются (форма откроется с пустым полем, без ошибки 500).
    """
    initial = {}
    group_id = request.GET.get("group")
    if group_id and str(group_id).isdigit():
        initial["group"] = int(group_id)
    teacher_id = request.GET.get("teacher")
    if teacher_id and str(teacher_id).isdigit():
        initial["teacher"] = int(teacher_id)
    room_id = request.GET.get("room")
    if room_id and str(room_id).isdigit():
        initial["room"] = int(room_id)
    day = request.GET.get("day")
    if day is not None and str(day).isdigit() and 0 <= int(day) <= 6:
        initial["day_of_week"] = int(day)
    # Дату разового занятия принимаем и как «lesson_date» (так её передают панели
    # формы), и как короткое «date» — оба имени означают одно и то же поле.
    raw_date = request.GET.get("lesson_date") or request.GET.get("date")
    if raw_date:
        parsed = parse_iso_date(raw_date)
        if parsed is not None:
            initial["lesson_date"] = parsed
    for key, field in (("start", "start_time"), ("end", "end_time")):
        raw = request.GET.get(key)
        if raw:
            try:
                initial[field] = time_cls.fromisoformat(raw)
            except ValueError:
                pass
    # Кого заменяют — приходит ссылкой «Поставить подмену» от отменённого
    # занятия. Значение проверяем: мусор и отключённый преподаватель просто
    # игнорируются, форма открывается как обычно.
    replaced_id = request.GET.get("replaced_teacher")
    if replaced_id and str(replaced_id).isdigit():
        if User.objects.filter(
            pk=int(replaced_id), role=User.Role.TEACHER, is_active=True
        ).exists():
            initial["replaced_teacher"] = int(replaced_id)
    return initial


def substitution_url(schedule, day):
    """Адрес формы «Поставить подмену» для отменённого занятия.

    Переносим в форму всё, что о занятии уже известно: группу, кабинет, день
    недели, время, дату конкретного занятия и того, кого заменяют
    (``replaced_teacher``). Дату передаём всегда — подмена ставится вместо
    состоявшегося бы занятия, а не повторяется каждую неделю; по дате форма к
    тому же снимает конфликт занятости отсутствующего преподавателя (см.
    ``availability.teacher_conflict``). Методисту остаётся выбрать преподавателя.

    ``schedule`` — отменённое занятие, ``day`` — дата отменённого занятия.
    """
    query = {
        "group": schedule.group_id,
        "day": day.weekday(),
        "lesson_date": day.isoformat(),
        "start": f"{schedule.start_time:%H:%M}",
        "end": f"{schedule.end_time:%H:%M}",
    }
    if schedule.teacher_id:
        # Подмена ставится этому преподавателю: занятие ведёт заменяющий.
        query["replaced_teacher"] = schedule.teacher_id
    if schedule.room_id:
        query["room"] = schedule.room_id
    return f"{reverse('schedule_add')}?{urlencode(query)}"


class ScheduleSaveGuardMixin:
    """Сохранение занятия с понятным сообщением вместо ошибки базы.

    Проверки формы (``ScheduleForm.clean``) не дают поставить второе занятие на
    то же место, но возможна гонка: два методиста сохранили форму одновременно.
    Тогда база отказывает по ограничению ``unique_group_one_off_start`` (у группы
    уже есть разовое занятие на эту дату и время) или ``unique_group_lesson_start``
    — показываем форму с текстом, а не страницу ошибки 500.
    """

    saved_message = _("Занятие добавлено.")

    def form_valid(self, form):
        group = form.cleaned_data["group"]
        # Преподавателя выбирает методист (у программы их может быть несколько),
        # адрес занятия = адрес программы группы.
        form.instance.teacher = form.cleaned_data["teacher"]
        form.instance.branch = group.subject.branch
        try:
            with transaction.atomic():
                response = super().form_valid(form)
        except IntegrityError:
            form.add_error(
                "start_time",
                _(
                    "На это время у группы уже есть занятие (возможно, подмена). "
                    "Обновите страницу и проверьте расписание."
                ),
            )
            return self.form_invalid(form)
        # Сообщение — только после успешного сохранения: иначе при отказе базы
        # методист видел бы «Занятие добавлено» и несохранённое занятие.
        messages.success(self.request, self.saved_message)
        return response


class ScheduleCreateView(
    ScheduleSaveGuardMixin, StaffRequiredMixin, ActivePeriodMixin, CreateView
):
    model = Schedule
    form_class = ScheduleForm
    template_name = "journal/schedule/form.html"
    success_url = reverse_lazy("schedule")
    saved_message = _("Занятие добавлено.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["term"] = self.period_term
        kwargs["branch"] = self.period_branch
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        initial.update(schedule_initial_from_request(self.request))
        # Переход «Поставить подмену» с отменённого занятия несёт дату разового
        # занятия: в этом случае занятие сразу помечаем как подмену. При обычном
        # открытии формы (без даты в адресе) галочка остаётся пустой. Сама пометка
        # ничего не отключает: занятость заменяющего проверяется как обычно.
        if "lesson_date" in initial:
            initial["is_substitution"] = True
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Новое занятие")
        context.update(
            schedule_form_context(
                self.request, term=self.period_term, branch=self.period_branch
            )
        )
        return context


class ScheduleUpdateView(
    ScheduleSaveGuardMixin, StaffRequiredMixin, ActivePeriodMixin, UpdateView
):
    model = Schedule
    form_class = ScheduleForm
    template_name = "journal/schedule/form.html"
    success_url = reverse_lazy("schedule")
    saved_message = _("Занятие обновлено.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Правим занятие в его собственном полугодии и адресе, даже если активны другие.
        kwargs["term"] = self.object.group.term
        kwargs["branch"] = self.object.group.subject.branch
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        # Значения из адреса важнее значений занятия: иначе при смене группы или дня
        # форма возвращалась бы к прежним, а панели занятости считались бы не для того занятия.
        initial.update(schedule_initial_from_request(self.request))
        # Преподаватель по умолчанию — тот, кто ведёт программу занятия.
        initial.setdefault("teacher", self.object.teacher_id)
        return initial

    def form_valid(self, form):
        group = form.cleaned_data["group"]
        form.instance.teacher = form.cleaned_data["teacher"]
        form.instance.branch = group.subject.branch
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Занятие · %(label)s") % {
            "label": self.object.date_label or self.object.get_day_of_week_display()
        }
        context["delete_url"] = reverse("schedule_delete", args=[self.object.pk])
        context.update(
            schedule_form_context(
                self.request,
                schedule=self.object,
                term=self.object.group.term,
                branch=self.object.group.subject.branch,
            )
        )
        return context


class ScheduleDeleteView(StaffRequiredMixin, DeleteView):
    model = Schedule
    template_name = "journal/confirm_delete.html"
    success_url = reverse_lazy("schedule")

    def form_valid(self, form):
        messages.success(self.request, _("Занятие удалено из расписания."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Удаление занятия")
        context["object_name"] = str(self.object)
        context["cancel_url"] = reverse("schedule")
        context["details"] = [
            _("Отметок посещаемости будет удалено: %(count)s")
            % {"count": self.object.attendances.count()},
        ]
        return context


# --- Преподаватели -----------------------------------------------------------
class TeacherListView(StaffRequiredMixin, ListPaginationMixin, ListView):
    model = User
    template_name = "journal/teachers/list.html"
    context_object_name = "teachers"

    def get_queryset(self):
        # Порядок задаём явно: при annotate() Meta.ordering не применяется,
        # а постраничному выводу нужен устойчивый порядок записей.
        today = timezone.localdate()
        queryset = User.objects.filter(role=User.Role.TEACHER).annotate(
            subjects_count=Count("subjects", distinct=True),
            # Пометка «отсутствует»: период накрывает сегодняшний день. Подзапрос,
            # а не запрос на строку, — иначе список преподавателей дал бы N+1.
            is_absent=Exists(
                TeacherAbsence.objects.filter(
                    teacher=OuterRef("pk"),
                    date_from__lte=today,
                    date_to__gte=today,
                )
            ),
        ).order_by("last_name", "first_name", "pk")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(last_name__icontains=query)
                | Q(first_name__icontains=query)
                | Q(username__icontains=query)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issued_password"] = pop_issued_password(self.request)
        context["query"] = self.request.GET.get("q", "")
        return context


class TeacherCreateView(StaffRequiredMixin, CreateView):
    model = User
    form_class = TeacherCreateForm
    template_name = "journal/teachers/form.html"
    success_url = reverse_lazy("teachers")
    # Имя объекта в контексте: иначе Django положит редактируемого пользователя
    # в переменную «user» и перебьёт вошедшего — меню и шапка станут его роли.
    context_object_name = "teacher"

    def form_valid(self, form):
        response = super().form_valid(form)
        remember_password(self.request, self.object, form.cleaned_data["password1"])
        notify_account_change(
            self.request,
            _("Создан преподаватель"),
            f"Создана учётная запись преподавателя: {self.object.full_name} "
            f"({self.object.username}).",
        )
        messages.success(self.request, _("Преподаватель создан."))
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Новый преподаватель")
        return context


class TeacherUpdateView(StaffRequiredMixin, UpdateView):
    """Методист или администратор правит данные преподавателя и меняет ему пароль."""

    model = User
    form_class = TeacherEditForm
    template_name = "journal/teachers/form.html"
    success_url = reverse_lazy("teachers")
    # См. TeacherCreateView: не даём редактируемому пользователю занять имя «user».
    context_object_name = "teacher"

    def get_queryset(self):
        return User.objects.filter(role=User.Role.TEACHER)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Кто правит карточку: свою учётную запись выключить нельзя.
        kwargs["viewer"] = self.request.user
        return kwargs

    def get_context(self, password_form=None):
        # Отсутствия вносят в отдельном разделе (см. AbsenceListView): в карточке
        # остаётся одна строка о текущем или ближайшем периоде.
        return {
            "title": _("Преподаватель · %(name)s") % {"name": self.object.full_name},
            "subjects_count": self.object.subjects.count(),
            "password_form": password_form or StyledSetPasswordForm(user=self.object),
            "current_absence": self.current_absence(),
        }

    def current_absence(self):
        """Текущее или ближайшее будущее отсутствие преподавателя (или None).

        Берём первый период, который ещё не закончился: если идёт сейчас — он и
        показывается, иначе ближайший по дате начала. Полностью прошедшие
        периоды карточка не показывает.
        """
        return (
            self.object.absences.filter(date_to__gte=timezone.localdate())
            .order_by("date_from", "pk")
            .first()
        )

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()

        if request.POST.get("form") == "password":
            password_form = StyledSetPasswordForm(
                user=self.object, data=request.POST
            )
            if password_form.is_valid():
                password_form.save()
                remember_password(
                    request, self.object, password_form.cleaned_data["new_password1"]
                )
                notify_account_change(
                    request,
                    _("Сменён пароль преподавателя"),
                    f"Преподавателю {self.object.full_name} ({self.object.username}) "
                    "задан новый пароль.",
                )
                messages.success(
                    request,
                    _("Пароль преподавателя %(name)s изменён.")
                    % {"name": self.object.full_name},
                )
                return redirect(self.success_url)
            messages.error(request, _("Пароль не изменён: исправьте ошибки в форме."))
            context = self.get_context_data()
            context.update(self.get_context(password_form=password_form))
            return self.render_to_response(context)

        form = self.get_form()
        if form.is_valid():
            messages.success(request, _("Данные преподавателя обновлены."))
            return self.form_valid(form)
        return self.form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_context())
        return context


# --- Отсутствия преподавателей: отдельный раздел ------------------------------
class TeacherAbsenceCreateView(StaffRequiredMixin, CreateView):
    """Новый период отсутствия: раздел «Отсутствия» → «Добавить отсутствие».

    Список преподавателей ведёт сюда с ``?teacher=<pk>``, поэтому выбранного
    педагога подставляем заранее. Идентификатор из адреса проверяется: чужой,
    отключённый или не преподаватель просто игнорируется — селект остаётся
    пустым, страница открывается как обычно.
    """

    model = TeacherAbsence
    form_class = TeacherAbsenceForm
    template_name = "journal/absences/form.html"
    success_url = reverse_lazy("absence_list")

    def get_initial(self):
        initial = super().get_initial()
        teacher_id = self.request.GET.get("teacher", "")
        if teacher_id.isdigit() and User.objects.filter(
            pk=int(teacher_id), role=User.Role.TEACHER, is_active=True
        ).exists():
            initial["teacher"] = int(teacher_id)
        return initial

    def form_valid(self, form):
        # Кто внёс период — тот, кто его добавил (см. TeacherAbsence.created_by).
        form.instance.created_by = self.request.user
        messages.success(
            self.request,
            _("Отсутствие добавлено: %(title)s.") % {"title": form.instance.title},
        )
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Новое отсутствие")
        context["delete_url"] = ""
        return context


class TeacherAbsenceUpdateView(StaffRequiredMixin, UpdateView):
    """Правка периода отсутствия: те же поля, что и при добавлении."""

    model = TeacherAbsence
    form_class = TeacherAbsenceForm
    template_name = "journal/absences/form.html"
    success_url = reverse_lazy("absence_list")

    def get_queryset(self):
        return TeacherAbsence.objects.select_related("teacher").filter(
            teacher__role=User.Role.TEACHER
        )

    def form_valid(self, form):
        messages.success(
            self.request,
            _("Отсутствие обновлено: %(title)s.") % {"title": form.instance.title},
        )
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Отсутствие · %(name)s") % {
            "name": self.object.teacher.full_name
        }
        context["delete_url"] = reverse("absence_delete", args=[self.object.pk])
        return context


class TeacherAbsenceDeleteView(StaffRequiredMixin, DeleteView):
    """Удаление периода отсутствия: подтверждение, как у остальных удалений."""

    model = TeacherAbsence
    template_name = "journal/confirm_delete.html"
    success_url = reverse_lazy("absence_list")

    def get_queryset(self):
        return TeacherAbsence.objects.select_related("teacher").filter(
            teacher__role=User.Role.TEACHER
        )

    def form_valid(self, form):
        messages.success(self.request, _("Отсутствие удалено."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Удаление отсутствия")
        context["object_name"] = self.object.title
        context["cancel_url"] = reverse("absence_list")
        context["details"] = [
            _("Преподаватель: %(name)s") % {"name": self.object.teacher.full_name},
            _("Причина: %(kind)s") % {"kind": self.object.get_kind_display()},
        ]
        return context


class AbsenceListView(StaffRequiredMixin, ListPaginationMixin, ListView):
    """Раздел «Отсутствия»: командировки и больничные всех преподавателей.

    По умолчанию показываются текущие и будущие периоды — методист сразу видит,
    кто отсутствует сейчас. Второй вариант фильтра («Все периоды») добавляет к
    ним прошедшие: они идут следом за текущими и ближайшими.
    """

    model = TeacherAbsence
    template_name = "journal/absences/list.html"
    context_object_name = "absences"

    def show_past(self):
        """Показывать ли прошедшие периоды: только по явному выбору фильтра."""
        return self.request.GET.get("scope") == "all"

    def get_queryset(self):
        today = timezone.localdate()
        queryset = TeacherAbsence.objects.select_related("teacher", "created_by")
        teacher_id = self.request.GET.get("teacher", "")
        if teacher_id.isdigit():
            queryset = queryset.filter(teacher_id=int(teacher_id))
        if not self.show_past():
            # «Только текущие и будущие»: период ещё не закончился.
            queryset = queryset.filter(date_to__gte=today)
        # Сортировка: сначала текущие и ближайшие (по дате начала), затем
        # прошедшие — пометка is_past и есть разделитель этих двух групп.
        return queryset.annotate(
            is_past=Case(
                When(date_to__lt=today, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        ).order_by("is_past", "date_from", "pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "title": _("Отсутствия"),
                # В фильтре — только действующие преподаватели.
                "teachers": teacher_choices(),
                "selected_teacher": self.request.GET.get("teacher", ""),
                "scope": "all" if self.show_past() else "actual",
                # Сегодняшний день нужен шаблону: им помечаются периоды, которые
                # идут прямо сейчас (лишнего запроса для этого не нужно).
                "today": timezone.localdate(),
                "has_filters": bool(
                    self.request.GET.get("teacher") or self.show_past()
                ),
            }
        )
        return context


class AbsenceSubstitutionsView(StaffRequiredMixin, View):
    """Подмены сразу на весь период отсутствия: /absences/<pk>/substitutions/.

    Страница показывает все занятия отсутствующего внутри периода по конкретным
    датам (``substitutions.absence_occurrences``) и даёт поставить заменяющего
    сразу на все дни. Конфликты — занятость заменяющего, кабинета и группы —
    видны заранее и подсвечиваются в строке; строка с конфликтом не создаётся,
    пока методист не снимет галочку «ставить подмену» или не отметит «не
    учитывать занятость» именно у неё.

    «Проверить конфликты» пересчитывает страницу по выбранным значениям, ничего
    не сохраняя: так страница работает и без JavaScript. «Поставить подмены»
    создаёт занятия одной транзакцией и возвращает в раздел «Отсутствия».
    Повторный запуск дублей не создаёт: занятые места и уже стоящие подмены
    пропускаются с сообщением.
    """

    template_name = "journal/absences/substitutions.html"

    def get_absence(self):
        return get_object_or_404(
            TeacherAbsence.objects.select_related("teacher"), pk=self.kwargs["pk"]
        )

    def available_teachers(self, absence):
        """Все действующие преподаватели — кроме самого отсутствующего.

        Это таблица для разбора присланных значений: выбрать в строке можно
        только преподавателя программы (см. ``program_options``), но если
        значение пришло мимо списка, строку нужно показать с понятной причиной,
        а не молча потерять.
        """
        return list(
            User.objects.filter(role=User.Role.TEACHER, is_active=True)
            .exclude(pk=absence.teacher_id)
            .order_by("last_name", "first_name")
        )

    def program_options(self, rows, absence):
        """Кто допустим в строках плана: ``([(занятие, дата, allowed)], общий список)``.

        Занятие ведёт только преподаватель своей программы — основной или
        дополнительный (``Subject.teacher_options``). Поэтому у каждой строки
        свой список заменяющих, а поле «Один заменяющий на все дни» предлагает
        объединение: выбранный из него подставится только в те строки, где он
        допустим, остальные нужно выбрать отдельно.
        """
        allowed_by_subject = program_teachers(
            {schedule.group.subject_id for schedule, _day in rows}
        )
        actives = self.available_teachers(absence)
        by_id = {teacher.pk: teacher for teacher in actives}
        prepared = []
        master = {}
        for schedule, day in rows:
            allowed = [
                teacher
                for teacher in actives
                if teacher.pk in allowed_by_subject.get(schedule.group.subject_id, set())
            ]
            for teacher in allowed:
                master[teacher.pk] = teacher
            prepared.append((schedule, day, allowed))
        return prepared, sorted(
            master.values(), key=lambda item: (item.last_name, item.first_name)
        )

    def master_id(self, request):
        """«Один заменяющий на все дни»: из формы или из адреса (?teacher=<pk>)."""
        raw = request.POST.get("master_teacher") or request.GET.get("teacher") or ""
        return int(raw) if str(raw).isdigit() else None

    def selections(self, request, prepared):
        """Выбор по строкам плана: из формы, иначе — общий заменяющий.

        Ключ строки — ``substitutions.row_key``: по нему форма присылает
        ``teacher_<ключ>``, ``apply_<ключ>`` и ``ignore_<ключ>``. Пустой выбор
        заменяющего подставляет общий (поле «Один заменяющий на все дни»), но
        только если он ведёт программу этой строки: чужого преподавателя строка
        не получает — его выбирают отдельно.
        """
        master = self.master_id(request)
        chosen = {}
        for schedule, day, allowed in prepared:
            key = row_key(schedule, day)
            allowed_ids = {teacher.pk for teacher in allowed}
            fallback = master if master in allowed_ids else None
            if request.method == "POST":
                raw = request.POST.get(f"teacher_{key}", "")
                teacher_id = int(raw) if str(raw).isdigit() else None
                chosen[key] = {
                    "teacher_id": teacher_id or fallback,
                    "apply": request.POST.get(f"apply_{key}") is not None,
                    "ignore": request.POST.get(f"ignore_{key}") is not None,
                }
            else:
                chosen[key] = {"teacher_id": fallback, "apply": True, "ignore": False}
        return chosen

    def plan(self, absence, request):
        """Строки страницы, сводка и данные для подсветки конфликтов.

        Возвращает ``(rows, counts, conflicts_json, teachers)``: строки с
        готовой пометкой, числа «создать / пропустить / с конфликтами», данные
        для живого пересчёта подсветки в браузере и общий список заменяющих.
        """
        found = absence_occurrences(absence)
        prepared, teachers = self.program_options(found, absence)
        allowed_by_subject = {
            schedule.group.subject_id: {teacher.pk for teacher in allowed}
            for schedule, _day, allowed in prepared
        }
        by_id = {teacher.pk: teacher for teacher in self.available_teachers(absence)}
        chosen = self.selections(request, prepared)
        busy = busy_by_day(found, by_id)
        known = existing_one_offs(found)
        rows = []
        counts = {"create": 0, "skipped": 0, "conflicts": 0}
        script = {"rows": {}, "labels": self.labels()}
        for schedule, day, allowed in prepared:
            key = row_key(schedule, day)
            decision = chosen[key]
            teacher = by_id.get(decision["teacher_id"])
            allowed_ids = {item.pk for item in allowed}
            empty = {
                "teacher": [],
                "room": [],
                "group": [],
                "absent": None,
                "program": None,
            }
            conflicts = (
                conflicts_for(
                    schedule,
                    day,
                    teacher,
                    busy,
                    allowed=allowed_by_subject.get(schedule.group.subject_id),
                )
                if teacher
                else empty
            )
            hard = conflict_reasons(
                schedule,
                {
                    "room": conflicts["room"],
                    "group": conflicts["group"],
                    "absent": conflicts["absent"],
                    "program": conflicts["program"],
                    "teacher": [],
                },
            )
            soft = conflict_reasons(
                schedule,
                {
                    "room": [],
                    "group": [],
                    "absent": None,
                    "program": None,
                    "teacher": conflicts["teacher"],
                },
            )
            existing = known.get((schedule.group_id, day, schedule.start_time))
            already = existing is not None and existing.pk != schedule.pk
            # Общий заменяющий подставляется только там, где он ведёт программу:
            # у чужой строки заменяющего выбирают отдельно.
            master = self.master_id(request)
            foreign_note = (
                str(
                    _(
                        "Здесь нужен свой заменяющий: выбранный в поле «Один "
                        "заменяющий на все дни» не ведёт программу «%(subject)s»."
                    )
                    % {"subject": schedule.group.subject.name}
                )
                if master is not None and master not in allowed_ids
                else ""
            )
            if not decision["apply"]:
                state, note = "skipped", _("Строка пропускается: галочка снята.")
                counts["skipped"] += 1
            elif not allowed:
                # Выбирать не из кого: у программы не указан ни один преподаватель.
                state = "conflict"
                note = _(
                    "У программы «%(subject)s» не указаны преподаватели: подмену "
                    "провести некому. Добавьте их в карточке программы."
                ) % {"subject": schedule.group.subject.name}
                counts["conflicts"] += 1
            elif teacher is None:
                if foreign_note:
                    state, note = "foreign", foreign_note
                else:
                    state, note = "empty", _("Выберите заменяющего преподавателя.")
                counts["skipped"] += 1
            elif already:
                state = "already"
                note = _("Уже стоит подмена: %(teacher)s.") % {
                    "teacher": existing.teacher.full_name
                }
                counts["skipped"] += 1
            elif hard:
                state, note = "conflict", " ".join(hard)
                counts["conflicts"] += 1
            elif soft and not decision["ignore"]:
                state = "conflict"
                note = " ".join(soft) + " " + str(self.hint())
                counts["conflicts"] += 1
            else:
                state = "create"
                note = (
                    _("Преподаватель разового занятия будет заменён.")
                    if schedule.is_one_off
                    else _("Подмена будет создана.")
                )
                counts["create"] += 1
            row_script = {
                "already": (
                    str(
                        _("Уже стоит подмена: %(teacher)s.")
                        % {"teacher": existing.teacher.full_name}
                    )
                    if already
                    else ""
                ),
                "foreign": foreign_note,
                "in_place": bool(schedule.is_one_off),
            }
            for teacher_id, reasons in row_conflicts(
                schedule,
                day,
                allowed,
                busy,
                allowed=allowed_by_subject.get(schedule.group.subject_id),
            ).items():
                row_script[str(teacher_id)] = {
                    "hard": " ".join(str(item) for item in reasons["hard"]),
                    "soft": " ".join(str(item) for item in reasons["soft"]),
                }
            script["rows"][key] = row_script
            rows.append(
                {
                    "key": key,
                    "schedule": schedule,
                    "date": day,
                    "weekday": schedule.get_day_of_week_display(),
                    "time": schedule.time_range,
                    "group": schedule.group.name,
                    "subject": schedule.group.subject.name,
                    "room": schedule.room.name if schedule.room_id else "",
                    "branch": schedule.branch.name if schedule.branch_id else "",
                    "one_off": schedule.is_one_off,
                    # Заменяющих строке предлагает только её программа.
                    "teachers": allowed,
                    "selected_teacher_id": decision["teacher_id"],
                    "apply": decision["apply"],
                    "ignore": decision["ignore"],
                    "state": state,
                    "note": note,
                }
            )
        return rows, counts, script, teachers

    def labels(self):
        """Подписи строк для пересчёта подсветки в браузере (``gettext``)."""
        return {
            "create": str(_("Подмена будет создана.")),
            "replace": str(_("Преподаватель разового занятия будет заменён.")),
            "skipped": str(_("Строка пропускается: галочка снята.")),
            "empty": str(_("Выберите заменяющего преподавателя.")),
            "conflict": str(_("Конфликт: подмену не создать, пока не решите строку.")),
            "conflictBadge": str(_("конфликт")),
            "alreadyBadge": str(_("уже стоит")),
            "foreignBadge": str(_("нужен свой заменяющий")),
            "hint": str(self.hint()),
        }

    def hint(self):
        """Что делать со строкой, у которой занят заменяющий."""
        return _(
            "Снимите галочку «ставить подмену» или отметьте «не учитывать "
            "занятость» именно у этой строки."
        )

    def conflict_hint(self):
        """Что делать со строкой при любом конфликте — для сообщения после отправки.

        В сообщении могут быть и мягкие конфликты (занятость заменяющего), и
        неустранимые (кабинет, группа, чужой преподаватель, отсутствие
        заменяющего), поэтому перечисляем оба пути.
        """
        return _(
            "Занятость заменяющего снимается галочкой «не учитывать занятость» у "
            "этой строки; занятый кабинет, занятая группа и заменяющий не из "
            "программы — нет: строку снимите галочкой «ставить подмену» или "
            "выберите заменяющего из преподавателей программы."
        )

    def render_page(self, absence, request):
        rows, counts, script, teachers = self.plan(absence, request)
        return render(
            request,
            self.template_name,
            {
                "title": _("Подмены на период"),
                "absence": absence,
                "rows": rows,
                "counts": counts,
                "teachers": teachers,
                "substitutions_json": script,
                "master_teacher": self.master_id(request) or "",
                "period_caption": absence_caption(absence),
            },
        )

    def get(self, request, pk):
        return self.render_page(self.get_absence(), request)

    def post(self, request, pk):
        absence = self.get_absence()
        # «Проверить конфликты»: показываем строки с пометками, ничего не создаём.
        if request.POST.get("action") != "save":
            return self.render_page(absence, request)
        rows = absence_occurrences(absence)
        prepared, _teachers = self.program_options(rows, absence)
        allowed_by_subject = {
            schedule.group.subject_id: {teacher.pk for teacher in allowed}
            for schedule, _day, allowed in prepared
        }
        by_id = {teacher.pk: teacher for teacher in self.available_teachers(absence)}
        chosen = self.selections(request, prepared)
        # Строки, которым общий заменяющий не подошёл: в них преподавателя
        # программы выбирают отдельно — о них и скажем после сохранения.
        master = self.master_id(request)
        foreign_rows = [
            f"{day:%d.%m.%Y}"
            for schedule, day, allowed in prepared
            if master is not None
            and master not in {teacher.pk for teacher in allowed}
            and chosen[row_key(schedule, day)]["apply"]
            and chosen[row_key(schedule, day)]["teacher_id"] is None
        ]
        decisions = [
            {
                "schedule": schedule,
                "date": day,
                "teacher": by_id.get(chosen[row_key(schedule, day)]["teacher_id"]),
                "apply": chosen[row_key(schedule, day)]["apply"],
                "ignore": chosen[row_key(schedule, day)]["ignore"],
            }
            for schedule, day in rows
        ]
        report = create_substitutions(
            decisions,
            busy_by_day(rows, by_id),
            existing_one_offs(rows),
            allowed_by_subject=allowed_by_subject,
        )
        details = report_text(report)
        messages.success(
            request,
            _("Создано подмен: %(created)s, пропущено: %(skipped)s.")
            % {"created": report["created"], "skipped": report_skipped(report)},
        )
        if report["already"]:
            messages.info(
                request,
                _("Уже стояли подмены (%(count)s): %(rows)s.")
                % {"count": report["already"], "rows": details["already"]},
            )
        if report["conflicts"]:
            messages.warning(
                request,
                _(
                    "Пропущены из-за конфликтов (%(count)s): %(rows)s. %(hint)s"
                )
                % {
                    "count": report["conflicts"],
                    "rows": details["conflicts"],
                    "hint": self.conflict_hint(),
                },
            )
        if foreign_rows:
            messages.warning(
                request,
                _(
                    "В строках другой программы заменяющего нужно выбрать "
                    "отдельно: %(rows)s."
                )
                % {"rows": ", ".join(foreign_rows)},
            )
        if report["errors"]:
            messages.warning(
                request,
                _("Не выбран заменяющий (%(count)s): %(rows)s.")
                % {"count": report["errors"], "rows": details["errors"]},
            )
        return redirect("absence_list")


class SubstitutionListView(
    StaffRequiredMixin, ActivePeriodMixin, ListPaginationMixin, ListView
):
    """Раздел «Подмены»: все занятия-подмены полугодия и адреса — только просмотр.

    Подмены ставят в других местах (раздел «Отсутствия» → «Подмены» на весь
    период или отменённое занятие в недельном журнале), поэтому здесь нет ни
    создания, ни удаления: список нужен, чтобы посмотреть и найти — кто кого
    заменяет, по какому отсутствию и на какие даты.

    «Кого заменяет» — не поле подмены, а связь с исходным занятием: её считает
    ``substitutions.replaced_lessons`` (то же место: группа, дата, время). Если
    пару определить нельзя (подмена поставлена заменой преподавателя у самого
    разового занятия), в списке честно стоит «—».
    """

    model = Schedule
    template_name = "journal/substitutions/list.html"
    context_object_name = "substitutions"

    def filters(self):
        """Значения фильтров из адреса: пустая строка — фильтр не задан."""
        return {
            "teacher": self.request.GET.get("teacher", ""),
            "replaced": self.request.GET.get("replaced", ""),
            "group": self.request.GET.get("group", ""),
            "date_from": self.request.GET.get("date_from", ""),
            "date_to": self.request.GET.get("date_to", ""),
        }

    def get_queryset(self):
        filters = self.filters()
        term = self.period_term
        branch = self.period_branch
        queryset = Schedule.objects.filter(
            is_substitution=True, group__term=term
        ).select_related("group__subject", "teacher", "branch", "room")
        if branch is not None:
            queryset = queryset.filter(branch=branch)
        if filters["teacher"].isdigit():
            queryset = queryset.filter(teacher_id=int(filters["teacher"]))
        if filters["group"].isdigit():
            queryset = queryset.filter(group_id=int(filters["group"]))
        date_from = parse_iso_date(filters["date_from"])
        date_to = parse_iso_date(filters["date_to"])
        if date_from is not None:
            queryset = queryset.filter(lesson_date__gte=date_from)
        if date_to is not None:
            queryset = queryset.filter(lesson_date__lte=date_to)

        items = list(queryset)
        # «Кого заменяет и по какому отсутствию» — из поля ``replaced_teacher``, а
        # для старых записей без него — поиском по месту (несколькими запросами
        # на весь список). Отдельное имя атрибута: поле модели не перезаписываем.
        replaced = replaced_lessons(items)
        for item in items:
            person, absence = replaced.get(item.pk, (None, None))
            item.replaced_person = person
            item.replaced_caption = (
                str(absence_caption(absence)) if absence is not None else ""
            )
        # «Кого заменяют» фильтруем в Python: у старых записей это связь с исходным
        # занятием, а не поле — запросом по базе её не выразить.
        if filters["replaced"].isdigit():
            replaced_id = int(filters["replaced"])
            items = [
                item
                for item in items
                if item.replaced_person is not None
                and item.replaced_person.pk == replaced_id
            ]
        # Свежие сверху: список смотрят, чтобы проверить ближайшие и недавние
        # подмены, а не листать полугодие с сентября.
        items.sort(
            key=lambda item: (item.lesson_date, item.start_time, item.pk), reverse=True
        )
        return items

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = self.period_term
        branch = self.period_branch
        page_items = (
            list(context["page_obj"].object_list) if context.get("page_obj") else []
        )
        # «Заменяющий тоже отсутствует» на дату подмены — одним запросом на
        # страницу, без запроса на каждую строку.
        absences = absences_by_teacher({item.teacher_id for item in page_items})
        for item in page_items:
            item.teacher_absence = pick_absence(
                absences.get(item.teacher_id), item.lesson_date
            )
        filters = self.filters()
        context.update(
            {
                "title": _("Подмены"),
                "term": term,
                "branch": branch,
                "filters": filters,
                "teachers": teacher_choices(),
                "groups": (
                    Group.objects.filter(term=term, subject__branch=branch)
                    .select_related("subject")
                    .order_by("subject__name", "name")
                    if branch is not None
                    else Group.objects.filter(term=term).select_related("subject")
                ),
                "shown_count": len(page_items),
                "has_filters": any(
                    filters[name]
                    for name in ("teacher", "replaced", "group", "date_from", "date_to")
                ),
                "reset_url": reverse("substitution_list"),
            }
        )
        return context


def notify_account_change(request, subject, message):
    """Уведомление администратору об изменении учётной записи."""
    security_tools.record_event(
        VisitLog.Kind.EVENT,
        request=request,
        user=request.user if request.user.is_authenticated else None,
        path=request.path,
        detail=subject,
    )
    notifications.notify(
        "accounts",
        subject,
        f"{message}\nКто изменил: {request.user.full_name} ({request.user.username})\n"
        f"IP-адрес: {security_tools.get_client_ip(request)}",
    )


# --- Профиль: свои данные и свой пароль --------------------------------------
class ProfileView(LoginRequiredMixin, View):
    """Преподаватель (и любой сотрудник) правит свои данные и меняет свой пароль."""

    template_name = "journal/profile/index.html"

    def get_context(self, request, profile_form=None, password_form=None):
        return {
            "profile_form": profile_form or ProfileForm(instance=request.user),
            "password_form": password_form
            or StyledPasswordChangeForm(user=request.user),
        }

    def get(self, request):
        return render(request, self.template_name, self.get_context(request))

    def post(self, request):
        if request.POST.get("action") == "password":
            form = StyledPasswordChangeForm(user=request.user, data=request.POST)
            if form.is_valid():
                user = form.save()
                update_session_auth_hash(request, user)
                notify_account_change(
                    request,
                    _("Смена своего пароля"),
                    f"Пользователь {request.user.full_name} сменил свой пароль.",
                )
                messages.success(
                    request, _("Пароль изменён — используйте его при следующем входе.")
                )
                return redirect("profile")
            messages.error(request, _("Пароль не изменён: исправьте ошибки в форме."))
            return render(
                request,
                self.template_name,
                self.get_context(request, password_form=form),
            )

        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, _("Данные сохранены."))
            return redirect("profile")
        messages.error(request, _("Данные не сохранены: исправьте ошибки в форме."))
        return render(
            request, self.template_name, self.get_context(request, profile_form=form)
        )


# --- Сотрудники (методисты и администраторы) ---------------------------------
class StaffMemberListView(AdminRequiredMixin, ListPaginationMixin, ListView):
    """Список сотрудников УМО. Доступен только администратору."""

    model = User
    template_name = "journal/staff/list.html"
    context_object_name = "members"

    def get_queryset(self):
        return User.objects.filter(
            role__in=[User.Role.ADMIN, User.Role.METHODIST]
        ).order_by("role", "last_name", "first_name", "pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issued_password"] = pop_issued_password(self.request)
        return context


class StaffMemberCreateView(AdminRequiredMixin, CreateView):
    """Создание учётной записи методиста (или ещё одного администратора)."""

    model = User
    form_class = StaffMemberCreateForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["viewer"] = self.request.user
        return kwargs
    template_name = "journal/staff/form.html"
    success_url = reverse_lazy("staff")
    # См. TeacherCreateView: своё имя для объекта, чтобы не перебить «user».
    context_object_name = "member"

    def form_valid(self, form):
        response = super().form_valid(form)
        remember_password(self.request, self.object, form.cleaned_data["password1"])
        notify_account_change(
            self.request,
            _("Создан сотрудник УМО"),
            f"Создана учётная запись: {self.object.full_name} "
            f"({self.object.username}), роль: {self.object.get_role_display()}.",
        )
        messages.success(
            self.request,
            _("Сотрудник создан. Передайте ему логин и пароль — он войдёт на этой же странице входа."),
        )
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Новый сотрудник УМО")
        return context


# --- Кабинет преподавателя ---------------------------------------------------
class TeacherDashboardView(TeacherRequiredMixin, ActivePeriodMixin, TemplateView):
    template_name = "journal/teacher/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = self.period_term
        schedules = list(
            Schedule.objects.filter(teacher=self.request.user, group__term=term)
            .select_related("group__subject")
        )
        days = []
        # Дата для каждого дня недели — соответствующая дата текущей недели.
        # Иначе при открытии занятия журнал считал, что речь о сегодняшнем дне,
        # и вместо отметки показывал предупреждение «в этот день занятия нет».
        today_date = date_cls.today()
        monday = today_date - timedelta(days=today_date.weekday())
        sunday = monday + timedelta(days=6)
        # Подмены недели — одним запросом: и чтобы видеть их на своих карточках,
        # и чтобы не предлагать «Поставить подмену» там, где она уже стоит.
        substitutions = substitution_map(monday, sunday)
        # Отсутствия на даты текущей недели: занятие в такой день преподаватель не
        # ведёт, оно не считается состоявшимся, а его часы уходят в «отменено».
        # Заменяющих тоже берём: подмена отвечает за своё отсутствие.
        week_absences = absences_by_teacher(
            {item.teacher_id for item in schedules}
            | {item.teacher_id for item in substitutions.values()},
            monday,
            sunday,
        )
        replaced = replaced_lessons(
            [item for item in schedules if item.is_substitution]
        )
        hours_total = 0
        cancelled_hours = 0
        cancelled_lessons = 0
        for value, label in Schedule.Weekday.choices:
            lessons = sorted(
                (
                    item
                    for item in schedules
                    if item.day_of_week == value
                    # Разовое занятие видно только в свою неделю: в другие недели его нет.
                    and (not item.is_one_off or monday <= item.lesson_date <= sunday)
                ),
                key=lambda item: item.sort_key,
            )
            if not lessons:
                continue
            day_date = monday + timedelta(days=value)
            for item in lessons:
                own_absence = pick_absence(
                    week_absences.get(item.teacher_id), day_date
                )
                # Подмена на этом месте: себя саму не показываем.
                substitution = substitutions.get(substitution_place(item) or ())
                if substitution is not None and substitution.pk == item.pk:
                    substitution = None
                if item.is_substitution:
                    # Отвечает только за своего преподавателя (заменяющего), а
                    # отсутствие того, кого заменяют, на этой карточке не место.
                    item.absence = None
                    item.replacement_absent = own_absence
                else:
                    item.absence = own_absence
                    item.replacement_absent = (
                        pick_absence(week_absences.get(substitution.teacher_id), day_date)
                        if substitution is not None
                        else None
                    )
                item.cancelled = own_absence is not None
                item.substitution_teacher = (
                    substitution.teacher if substitution is not None else None
                )
                # Подпись «Подмена для … · Больничный с … по …»: заменяемый берётся
                # из поля подмены, для старых записей — из пары по месту.
                replacement_person, replaced_absence = replaced.get(
                    item.pk, (None, None)
                )
                item.replacement_for = replacement_person
                item.replacement_caption = (
                    str(absence_caption(replaced_absence))
                    if replaced_absence is not None
                    else ""
                )
                # Кнопка «Поставить подмену» — только отменённому занятию и только
                # если подмены на этом месте ещё нет.
                item.substitution_url = (
                    substitution_url(item, day_date)
                    if item.cancelled
                    and not item.is_substitution
                    and substitution is None
                    else ""
                )
                if item.cancelled:
                    cancelled_hours += item.academic_hours
                    cancelled_lessons += 1
                else:
                    hours_total += item.academic_hours
            days.append({
                "label": label,
                "lessons": lessons,
                "date": day_date,
            })
        context["days"] = days
        context["term"] = term
        context["today"] = date_cls.today()
        context["hours_total"] = hours_total
        context["cancelled_hours"] = cancelled_hours
        context["cancelled_lessons"] = cancelled_lessons
        context["other_terms_lessons"] = (
            Schedule.objects.filter(teacher=self.request.user)
            .exclude(group__term=term)
            .count()
        )
        # Педагог видит своё повышение квалификации и успехи своих учеников.
        development = list(
            TeacherDevelopment.objects.filter(teacher=self.request.user).order_by("-date")
        )
        context["development"] = development
        context["development_hours"] = sum(item.hours or 0 for item in development)
        group_ids = {item.group_id for item in schedules}
        context["contests"] = (
            ContestResult.objects.filter(student__groups__id__in=group_ids)
            .select_related("student")
            .distinct()
            .order_by("-date")[:10]
        )
        return context


#: Короткие подписи статусов отметки для узких экранов.
SHORT_ATTENDANCE_LABELS = {
    "P": _("Был"),
    "A": _("Нет"),
    "L": _("Опозд."),
}


def lesson_days_text(schedules):
    """Дни занятий группы в виде «по понедельникам и четвергам» — без повторов.

    Разовые занятия пропускаем: они не повторяются каждую неделю.
    """
    #: Дательный падеж дней недели: «по понедельникам и четвергам».
    cases = {
        0: _("понедельникам"), 1: _("вторникам"), 2: _("средам"), 3: _("четвергам"),
        4: _("пятницам"), 5: _("субботам"), 6: _("воскресеньям"),
    }
    unique = []
    for item in schedules:
        if item.is_one_off:
            continue
        name = cases.get(item.day_of_week)
        if name and name not in unique:
            # Ленивый перевод приводим к строке: join() принимает только строки.
            unique.append(str(name))
    if not unique:
        return ""
    if len(unique) == 1:
        return _("по %(days)s") % {"days": unique[0]}
    return _("по %(days)s и %(last)s") % {
        "days": ", ".join(unique[:-1]),
        "last": unique[-1],
    }


class AttendanceView(LoginRequiredMixin, View):
    """Отметка посещаемости группы на занятии.

    Преподаватель открывает только свои занятия, методист и администратор — любые.
    Дата всегда приводится к дню, в который это занятие действительно бывает.
    """

    template_name = "journal/teacher/attendance.html"

    def get_schedule(self):
        queryset = Schedule.objects.select_related(
            "group__subject", "group__term", "teacher", "branch", "room"
        )
        if not self.request.user.is_staff_member:
            queryset = queryset.filter(teacher=self.request.user)
        return get_object_or_404(queryset, pk=self.kwargs["pk"])

    def available_schedules(self, schedule):
        """Занятия этой группы, доступные пользователю: по дням недели и датам разовых."""
        queryset = schedule.group.schedules.select_related("teacher")
        if not self.request.user.is_staff_member:
            queryset = queryset.filter(teacher=self.request.user)
        return list(queryset)

    def parse_date(self, schedule):
        """Дата занятия. Если выбрана дата без занятия — подставляем ближайшую подходящую.

        Возвращает тройку: дата, запрошенная дата (может быть None) и признак исправления.
        """
        raw = self.request.POST.get("date") or self.request.GET.get("date")
        term = schedule.group.term
        requested = None
        if raw:
            try:
                requested = date_cls.fromisoformat(raw)
            except ValueError:
                requested = None
        if schedule.is_one_off:
            # Разовое занятие бывает только в свою дату: соседние даты не подставляем.
            return (
                schedule.lesson_date,
                requested,
                requested is not None and requested != schedule.lesson_date,
            )

        chosen = requested
        if chosen is None:
            today = date_cls.today()
            chosen = today + timedelta(days=(schedule.day_of_week - today.weekday()) % 7)

        if chosen.weekday() != schedule.day_of_week:
            # Ближайший день недели, в который занятие действительно проходит.
            shift = (schedule.day_of_week - chosen.weekday()) % 7
            if shift > 3:
                shift -= 7
            chosen += timedelta(days=shift)

        if term is not None:
            if chosen < term.start_date:
                chosen = term.start_date + timedelta(
                    days=(schedule.day_of_week - term.start_date.weekday()) % 7
                )
            elif chosen > term.end_date:
                chosen = term.end_date - timedelta(
                    days=(term.end_date.weekday() - schedule.day_of_week) % 7
                )
        # Сообщаем об исправлении только если дата действительно изменилась.
        return chosen, requested, requested is not None and chosen != requested

    MONTHS = (
        "",
        _("январь"), _("февраль"), _("март"), _("апрель"), _("май"), _("июнь"),
        _("июль"), _("август"), _("сентябрь"), _("октябрь"), _("ноябрь"), _("декабрь"),
    )

    def calendar_context(self, schedule, lesson_date, requested=None):
        """Календарь месяца по всем занятиям группы, а не только по текущему дню недели.

        Группа может заниматься несколько раз в неделю и в разное время, поэтому каждый
        день календаря ведёт на своё занятие со своей датой.
        """
        term = schedule.group.term
        if term is None:
            # На всякий случай: границы полугодия берём из активного периода.
            term = getattr(self, "period_term", None)
        by_weekday = {}
        by_date = {}
        for item in self.available_schedules(schedule):
            if item.is_one_off:
                # Разовое занятие показываем только в его дату, а не в каждый такой день недели.
                by_date[item.lesson_date] = item
            else:
                by_weekday.setdefault(item.day_of_week, item)

        first = lesson_date.replace(day=1)
        try:
            year, month = (int(part) for part in (self.request.GET.get("month") or "").split("-"))
            first = date_cls(year, month, 1)
        except (ValueError, TypeError):
            pass

        next_month = (first + timedelta(days=32)).replace(day=1)
        prev_month = (first - timedelta(days=1)).replace(day=1)

        def lesson_for(day):
            if term is not None and not (term.start_date <= day <= term.end_date):
                return None
            return by_date.get(day) or by_weekday.get(day.weekday())

        weeks, cursor = [], first - timedelta(days=first.weekday())
        last_day = next_month - timedelta(days=1)
        # Отсутствия преподавателя за показанный месяц: дни, в которые он не ведёт
        # занятия, помечаем и в календаре — занятие по ним отменено.
        calendar_absences = absences_by_teacher(
            {schedule.teacher_id}, cursor, last_day + timedelta(days=6)
        ).get(schedule.teacher_id, [])
        while cursor <= last_day:
            week = []
            for _index in range(7):
                found = lesson_for(cursor)
                absence = pick_absence(calendar_absences, cursor)
                cell = {
                    "date": cursor,
                    "in_month": cursor.month == first.month,
                    "lesson": found,
                    "has_lesson": found is not None,
                    "is_current": cursor == lesson_date,
                    "is_requested": requested is not None and cursor == requested,
                    "absence": absence,
                    "url": (
                        f"{reverse('attendance', args=[found.pk])}?date={cursor.isoformat()}"
                        if found is not None
                        else ""
                    ),
                    "title": (
                        f"{found.get_day_of_week_display()}, {found.time_range}"
                        + (f" · {found.date_label}" if found.is_one_off else "")
                        if found is not None
                        else str(_("Занятия нет"))
                    ),
                }
                if absence is not None:
                    # В календаре видны только числа, поэтому причину и период
                    # дописываем в подсказку дня.
                    cell["title"] = "%s · %s" % (
                        cell["title"],
                        _("Отсутствие: %(title)s") % {"title": absence.title},
                    )
                week.append(cell)
                cursor += timedelta(days=1)
            weeks.append(week)

        return {
            "calendar_weeks": weeks,
            "calendar_title": f"{self.MONTHS[first.month]} {first.year}",
            "calendar_prev": prev_month.strftime("%Y-%m"),
            "calendar_next": next_month.strftime("%Y-%m"),
            "term_start": term.start_date.isoformat() if term else "",
            "term_end": term.end_date.isoformat() if term else "",
            "term": term,
            "group_lessons": self.available_schedules(schedule),
            "group_lesson_days": lesson_days_text(self.available_schedules(schedule)),
        }

    def neighbour(self, schedule, lesson_date, step):
        """Ближайшее занятие этой группы до или после указанной даты."""
        if schedule.is_one_off:
            # У разового занятия соседей нет: оно бывает один раз.
            return None
        schedules = {
            item.day_of_week: item
            for item in self.available_schedules(schedule)
            if not item.is_one_off
        }
        if not schedules:
            return None
        term = schedule.group.term
        cursor = lesson_date
        for _step in range(400):
            cursor += timedelta(days=step)
            if term is not None and not (term.start_date <= cursor <= term.end_date):
                return None
            target = schedules.get(cursor.weekday())
            if target is not None:
                return target, cursor
        return None

    def lesson_link(self, found):
        if found is None:
            return None
        target, when = found
        return {
            "schedule": target,
            "date": when,
            "url": f"{reverse('attendance', args=[target.pk])}?date={when.isoformat()}",
            "weekday": target.get_day_of_week_display(),
            "time": target.time_range,
        }

    def get_context(self, schedule, lesson_date, corrected=False, requested=None):
        students = schedule.group.students.all()
        existing = {
            item.student_id: item.status
            for item in Attendance.objects.filter(schedule=schedule, date=lesson_date)
        }
        rows = [
            {
                "student": student,
                "status": existing.get(student.id, Attendance.Status.PRESENT),
            }
            for student in students
        ]
        topic = LessonTopic.objects.filter(
            schedule=schedule, date=lesson_date
        ).first()
        # Отсутствие СВОЕГО преподавателя на дату занятия: страница открывается,
        # но отметить посещаемость нельзя — занятие не считается состоявшимся.
        # У подмены свой преподаватель (заменяющий), поэтому чужое отсутствие её
        # не отменяет: на ней отметки доступны как на обычном занятии.
        absence = absence_on(schedule.teacher, lesson_date)
        # Подмена, уже стоящая на этом месте: вторую база не примет, поэтому
        # кнопку «Поставить подмену» не показываем, а пишем, кто проведёт.
        substitution = (
            Schedule.objects.filter(
                group=schedule.group,
                lesson_date=lesson_date,
                start_time=schedule.start_time,
                is_substitution=True,
            )
            .exclude(pk=schedule.pk)
            .select_related("teacher")
            .first()
        )
        # Для самой подмены — кого заменяют и на какой период отсутствия.
        replacement_person, replaced_absence = replaced_lessons([schedule]).get(
            schedule.pk, (None, None)
        )
        context = {
            "schedule": schedule,
            "lesson_date": lesson_date,
            "absence": absence,
            "substitution_teacher": (
                substitution.teacher if substitution is not None else None
            ),
            "replacement_for": replacement_person,
            "replacement_caption": (
                str(absence_caption(replaced_absence))
                if replaced_absence is not None
                else ""
            ),
            # Отменённое занятие без подмены: кнопка «Поставить подмену» ведёт на
            # форму нового занятия с уже заполненными группой, кабинетом, днём и
            # временем. Если подмена уже стоит — кнопки нет, иначе получится дубль.
            "substitution_url": (
                substitution_url(schedule, lesson_date)
                if absence is not None
                and not schedule.is_substitution
                and substitution is None
                else ""
            ),
            "date_corrected": corrected,
            "date_requested": requested,
            "rows": rows,
            # Для узких экранов к каждому статусу даём короткую подпись,
            # чтобы столбцы отметок помещались без горизонтальной прокрутки.
            "statuses": [
                {
                    "value": value,
                    "label": label,
                    "short": SHORT_ATTENDANCE_LABELS.get(value, label),
                }
                for value, label in Attendance.Status.choices
            ],
            "topic": topic,
            "academic_hours": schedule.academic_hours,
            # У разового занятия переход на неделю вперёд/назад не имеет смысла.
            "prev_date": (
                "" if schedule.is_one_off else (lesson_date - timedelta(days=7)).isoformat()
            ),
            "next_date": (
                "" if schedule.is_one_off else (lesson_date + timedelta(days=7)).isoformat()
            ),
            "prev_lesson": self.lesson_link(self.neighbour(schedule, lesson_date, -1)),
            "next_lesson": self.lesson_link(self.neighbour(schedule, lesson_date, 1)),
            "is_staff_view": self.request.user.is_staff_member,
        }
        context.update(self.calendar_context(schedule, lesson_date, requested))
        return context

    def get(self, request, *args, **kwargs):
        schedule = self.get_schedule()
        lesson_date, requested, corrected = self.parse_date(schedule)
        if corrected:
            if schedule.is_one_off:
                messages.warning(
                    request,
                    _("%(requested)s — занятия нет: оно разовое и проходит "
                      "%(lesson)s. Открыта его дата.")
                    % {
                        "requested": f"{requested:%d.%m.%Y}",
                        "lesson": f"{schedule.lesson_date:%d.%m.%Y}",
                    },
                )
            else:
                days = lesson_days_text(self.available_schedules(schedule))
                messages.warning(
                    request,
                    _("%(requested)s — занятия нет%(group)s. Открыто ближайшее: "
                      "%(lesson)s (%(weekday)s).")
                    % {
                        "requested": f"{requested:%d.%m.%Y}",
                        "group": _(", у группы «%(name)s» занятия %(days)s")
                        % {"name": schedule.group.name, "days": days}
                        if days
                        else "",
                        "lesson": f"{lesson_date:%d.%m.%Y}",
                        "weekday": schedule.get_day_of_week_display().lower(),
                    },
                )
        return render(
            request,
            self.template_name,
            self.get_context(schedule, lesson_date, corrected, requested),
        )

    def post(self, request, *args, **kwargs):
        """Сохраняем только отметки.

        Тема занятия здесь не меняется: она заполняется и правится в разделе
        «По неделям: темы и часы», а на этой странице только показывается.
        """
        schedule = self.get_schedule()
        lesson_date, requested, corrected = self.parse_date(schedule)
        # Отметки на отменённое занятие не сохраняем: преподаватель в этот день
        # отсутствует, занятие не состоялось. Уже стоящие отметки не трогаем —
        # они просто не учитываются в отчётах.
        absence = absence_on(schedule.teacher, lesson_date)
        if absence is not None:
            messages.error(
                request,
                _("Занятие отменено: %(absence)s. Отметки не сохраняются.")
                % {"absence": absence.title},
            )
            return redirect(
                f"{reverse('attendance', args=[schedule.pk])}"
                f"?date={lesson_date.isoformat()}"
            )
        if corrected:
            if schedule.is_one_off:
                messages.warning(
                    request,
                    _("Занятие разовое: отмечена его дата %(date)s.")
                    % {"date": f"{lesson_date:%d.%m.%Y}"},
                )
            else:
                messages.warning(
                    request,
                    _("Дата исправлена: занятие бывает по %(weekday)s, показано "
                      "%(date)s.")
                    % {
                        "weekday": schedule.get_day_of_week_display().lower(),
                        "date": f"{lesson_date:%d.%m.%Y}",
                    },
                )
        students = list(schedule.group.students.all())
        valid_statuses = {value for value, _ in Attendance.Status.choices}

        # Отметки трогаем только если форма их содержит.
        has_statuses = any(
            key.startswith("status_") for key in request.POST.keys()
        )
        if has_statuses:
            marked = []
            cleared_ids = []
            for student in students:
                status = request.POST.get(f"status_{student.id}")
                if status in valid_statuses:
                    marked.append(
                        Attendance(
                            student=student,
                            schedule=schedule,
                            date=lesson_date,
                            status=status,
                        )
                    )
                else:
                    cleared_ids.append(student.id)
            # Сохраняем одной командой: вставка с обновлением уже существующих отметок
            # по ключу (ученик, занятие, дата) и одно удаление снятых отметок.
            with transaction.atomic():
                if marked:
                    Attendance.objects.bulk_create(
                        marked,
                        update_conflicts=True,
                        unique_fields=["student", "schedule", "date"],
                        update_fields=["status"],
                    )
                if cleared_ids:
                    Attendance.objects.filter(
                        schedule=schedule, date=lesson_date, student_id__in=cleared_ids
                    ).delete()

        messages.success(request, _("Посещаемость сохранена."))
        return redirect(f"{reverse('attendance', args=[schedule.pk])}?date={lesson_date.isoformat()}")


# --- Темы занятий и академические часы ---------------------------------------
def parse_iso_date(raw, default=None):
    if raw:
        try:
            return date_cls.fromisoformat(str(raw))
        except ValueError:
            pass
    return default


def week_bounds(raw=None):
    """Понедельник и суббота недели, к которой относится дата."""
    day = parse_iso_date(raw, timezone.localdate())
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=5)


def term_week_limits(term):
    """Первая и последняя допустимые недели полугодия (их понедельники).

    Первая неделя — та, что содержит дату начала полугодия, последняя — та, что
    содержит дату окончания. Если даты не заполнены, ограничения с этой стороны
    нет (``None``): полугодие может быть заполнено не полностью, и листание
    недель тогда не ограничиваем — лишь бы страница открывалась.
    """
    start = getattr(term, "start_date", None)
    end = getattr(term, "end_date", None)
    first = start - timedelta(days=start.weekday()) if start else None
    last = end - timedelta(days=end.weekday()) if end else None
    return first, last


def clamp_week(monday, term):
    """Ближайшая допустимая неделя полугодия и признак «значение изменилось».

    Неделя из адреса может оказаться за границами полугодия (старая ссылка,
    правка периода): показываем ближайшую допустимую неделю, а не 404.
    """
    first, last = term_week_limits(term)
    if first is not None and monday < first:
        return first, True
    if last is not None and monday > last:
        return last, True
    return monday, False


def week_navigation(term, monday):
    """Кнопки листания недель: адреса соседей, их доступность и «текущая неделя».

    ``prev_allowed``/``next_allowed`` — можно ли листать дальше: на границе
    полугодия кнопка показывается неактивной (без ссылки), чтобы было понятно,
    что дальше занятий нет. «Текущая неделя» ведёт к неделе сегодняшнего дня, а
    если сегодня вне полугодия — к ближайшей допустимой неделе.
    """
    first, last = term_week_limits(term)
    previous = monday - timedelta(days=7)
    following = monday + timedelta(days=7)
    today_week, _changed = clamp_week(week_bounds()[0], term)
    return {
        "prev_week": previous.isoformat(),
        "next_week": following.isoformat(),
        "this_week": today_week.isoformat(),
        "prev_allowed": first is None or previous >= first,
        "next_allowed": last is None or following <= last,
    }


def week_lessons(schedules, monday):
    """Раскладывает занятия недели по дням, подставляя даты, темы и часы.

    Занятия, попавшие в период отсутствия преподавателя, помечаются периодом
    (``absence``): они не считаются состоявшимися, их часы не входят в общий
    итог, а попадают в отдельную строку «отменено».
    """
    saturday = monday + timedelta(days=5)
    schedule_ids = [item.pk for item in schedules]
    # Подмены недели берём одним запросом: по ним видно, кто проведёт отменённое
    # занятие и не нужно ли показать кнопку «Поставить подмену».
    substitutions = substitution_map(monday, saturday)
    # Отсутствия за неделю — тоже одним запросом, и на преподавателей занятий, и
    # на заменяющих: у подмены своя ответственность за своё отсутствие.
    week_absences = absences_by_teacher(
        {item.teacher_id for item in schedules}
        | {item.teacher_id for item in substitutions.values()},
        monday,
        saturday,
    )
    # Для подмен — занятие, вместо которого она стоит, и период отсутствия
    # заменяемого (подпись «Подмена для … · Больничный с … по …»).
    replaced = replaced_lessons(substitutions.values())
    topics = {}
    if schedule_ids:
        for topic in LessonTopic.objects.filter(
            schedule_id__in=schedule_ids, date__range=(monday, saturday)
        ):
            topics[(topic.schedule_id, topic.date)] = topic
    marks = {}
    if schedule_ids:
        for mark in Attendance.objects.filter(
            schedule_id__in=schedule_ids, date__range=(monday, saturday)
        ):
            marks.setdefault((mark.schedule_id, mark.date), {})[mark.student_id] = mark.status

    days = []
    for offset in range(6):  # понедельник — суббота
        current = monday + timedelta(days=offset)
        lessons = []
        for schedule in schedules:
            # Обычное занятие — в свой день недели, разовое — только в свою дату.
            if not schedule.occurs_on(current):
                continue
            day_marks = marks.get((schedule.pk, current), {})
            students = list(schedule.group.students.all())
            # Один проход по списку: отметок нет — считаем, что ученик присутствовал.
            counts = Counter(
                day_marks.get(student.pk, Attendance.Status.PRESENT)
                for student in students
            )
            present = counts[Attendance.Status.PRESENT]
            absent = counts[Attendance.Status.ABSENT]
            late = counts[Attendance.Status.LATE]
            # Отсутствие СВОЕГО преподавателя на эту дату: занятие отменено, часы
            # в план/факт не идут (см. «отменено: N ч» в шапке недели).
            own_absence = pick_absence(week_absences.get(schedule.teacher_id), current)
            # Подмена на этом месте (группа, дата, время начала). Себя саму не
            # показываем: подмена отвечает за своё отсутствие, а не за чужое.
            substitution = substitutions.get(
                (schedule.group_id, current, schedule.start_time)
            )
            if substitution is not None and substitution.pk == schedule.pk:
                substitution = None
            if schedule.is_substitution:
                # Занятие ведёт заменяющий: отсутствие того, кого заменяют, на
                # нём не показываем (оно видно на исходном занятии), а отсутствие
                # самого заменяющего — отдельным предупреждением.
                absence = None
                replacement_absence = own_absence
            else:
                absence = own_absence
                replacement_absence = (
                    pick_absence(week_absences.get(substitution.teacher_id), current)
                    if substitution is not None
                    else None
                )
            cancelled = own_absence is not None
            replacement_person, replaced_absence = replaced.get(
                schedule.pk, (None, None)
            )
            hours = schedule.academic_hours
            lessons.append(
                {
                    "schedule": schedule,
                    "date": current,
                    "topic": topics.get((schedule.pk, current)),
                    "students": [
                        {
                            "student": student,
                            "status": day_marks.get(student.pk, Attendance.Status.PRESENT),
                        }
                        for student in students
                    ],
                    "filled": bool(day_marks),
                    "present": present,
                    "absent": absent,
                    "late": late,
                    "total": len(students),
                    "hours": hours,
                    "absence": absence,
                    "cancelled": cancelled,
                    # Кто проведёт отменённое занятие: подмена уже стоит на это
                    # место (группа, дата и время начала).
                    "substitution_teacher": (
                        substitution.teacher if substitution is not None else None
                    ),
                    # Заменяющий и сам отсутствует в этот день — предупреждение
                    # вместо обычной пометки об отмене.
                    "replacement_absent": replacement_absence,
                    # Подпись подмены: для кого она и на какой период отсутствия.
                    "replacement_for": replacement_person,
                    "replacement_caption": (
                        str(absence_caption(replaced_absence))
                        if replaced_absence is not None
                        else ""
                    ),
                    # Кнопка «Поставить подмену»: только отменённому занятию и
                    # только если подмены на этом месте ещё нет — иначе дубль.
                    "substitution_url": (
                        substitution_url(schedule, current)
                        if cancelled
                        and not schedule.is_substitution
                        and substitution is None
                        else ""
                    ),
                    # Считанные часы: у отменённого занятия это 0, а сами часы
                    # видны в «cancelled_hours» отдельной строкой.
                    "counted_hours": 0 if cancelled else hours,
                    "cancelled_hours": hours if cancelled else 0,
                }
            )
        if lessons:
            days.append({"date": current, "lessons": lessons})
    return days


class TopicSaveView(LoginRequiredMixin, View):
    """Сохранение темы занятия на дату: методист — любому занятию, преподаватель — своему."""

    def post(self, request):
        schedule = get_object_or_404(
            Schedule.objects.select_related("group__subject", "teacher"),
            pk=request.POST.get("schedule") or 0,
        )
        is_staff = request.user.is_staff_member
        if not is_staff and schedule.teacher_id != request.user.pk:
            raise Http404(_("Это занятие другого преподавателя."))
        lesson_date = parse_iso_date(request.POST.get("date"))
        if lesson_date is None:
            messages.error(request, _("Не удалось определить дату занятия."))
            return redirect(request.POST.get("next") or "home")

        topic_text = (request.POST.get("topic") or "").strip()[:300]
        if topic_text:
            LessonTopic.objects.update_or_create(
                schedule=schedule,
                date=lesson_date,
                defaults={"topic": topic_text, "teacher": request.user},
            )
            messages.success(
                request,
                _("Тема сохранена: %(date)s, %(hours)s акад. ч.")
                % {"date": f"{lesson_date:%d.%m.%Y}", "hours": schedule.academic_hours},
            )
        else:
            # Второй элемент delete() — разбивка по моделям, она не нужна; имя
            # переменной не должно совпадать с псевдонимом перевода «_».
            removed, _unused = LessonTopic.objects.filter(
                schedule=schedule, date=lesson_date
            ).delete()
            messages.success(
                request,
                _("Тема удалена.") if removed else _("Тема не была заполнена."),
            )
        target = request.POST.get("next") or ""
        if not (target.startswith("/") and not target.startswith("//")):
            target = reverse("schedule")
        return redirect(target)


class TeacherWeekView(TeacherRequiredMixin, ActivePeriodMixin, TemplateView):
    """Мои занятия по неделям: даты, темы, часы и посещаемость детей."""

    template_name = "journal/teacher/week.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Суббота в контексте не нужна; имя переменной не должно перекрывать
        # псевдоним перевода «_».
        monday, _unused = self.week_monday()
        schedules = list(
            Schedule.objects.filter(
                teacher=self.request.user, group__term=self.period_term
            )
            .select_related("group__subject", "group__term", "branch", "room")
            # Учеников групп берём одним запросом на все занятия недели.
            .prefetch_related("group__students")
        )
        days = week_lessons(schedules, monday)
        context.update(
            {
                "title": _("Мои занятия по неделям"),
                "term": self.period_term,
                "monday": monday,
                "week_end": monday + timedelta(days=5),
                "days": days,
                "lessons_total": sum(len(day["lessons"]) for day in days),
                # Часы отменённых занятий в итог не входят: они показываются
                # отдельной строкой «отменено: N ч».
                "hours_total": sum(
                    item["counted_hours"] for day in days for item in day["lessons"]
                ),
                "cancelled_hours": sum(
                    item["cancelled_hours"] for day in days for item in day["lessons"]
                ),
                "cancelled_lessons": sum(
                    1 for day in days for item in day["lessons"] if item["cancelled"]
                ),
                "is_teacher_view": True,
                "next_url": reverse("teacher_week")
                + f"?week={monday.isoformat()}",
            }
        )
        # Кнопки недель: на границе полугодия соседняя кнопка неактивна.
        context.update(week_navigation(self.period_term, monday))
        return context


class WeekJournalView(StaffRequiredMixin, ActivePeriodMixin, TemplateView):
    """Посещаемость и темы по неделям для методиста: фильтры по группе, программе, преподавателю."""

    template_name = "journal/weeks/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Суббота в контексте не нужна (см. TeacherWeekView).
        monday, _unused = self.week_monday()
        term = self.period_term
        branch = self.period_branch
        schedules = Schedule.objects.filter(group__term=term).select_related(
            "group__subject__teacher", "group__term", "teacher", "branch", "room"
        ).prefetch_related("group__students")
        if branch is not None:
            schedules = schedules.filter(branch=branch)

        group_id = self.request.GET.get("group") or ""
        subject_id = self.request.GET.get("subject") or ""
        teacher_id = self.request.GET.get("teacher") or ""
        if group_id.isdigit():
            schedules = schedules.filter(group_id=int(group_id))
        if subject_id.isdigit():
            schedules = schedules.filter(group__subject_id=int(subject_id))
        if teacher_id.isdigit():
            schedules = schedules.filter(teacher_id=int(teacher_id))

        schedules = list(schedules.order_by("start_time"))
        days = week_lessons(schedules, monday)

        context.update(
            {
                "title": _("Посещаемость и темы по неделям"),
                "term": term,
                "branch": branch,
                "monday": monday,
                "week_end": monday + timedelta(days=5),
                "days": days,
                "lessons_total": sum(len(day["lessons"]) for day in days),
                "hours_total": sum(
                    item["counted_hours"] for day in days for item in day["lessons"]
                ),
                "cancelled_hours": sum(
                    item["cancelled_hours"] for day in days for item in day["lessons"]
                ),
                "cancelled_lessons": sum(
                    1 for day in days for item in day["lessons"] if item["cancelled"]
                ),
                "groups": Group.objects.filter(term=term, subject__branch=branch)
                .select_related("subject")
                .order_by("subject__name", "name")
                if branch is not None
                else Group.objects.filter(term=term).select_related("subject"),
                "subjects": Subject.objects.filter(term=term, branch=branch).order_by("name")
                if branch is not None
                else Subject.objects.none(),
                "teachers": User.objects.filter(
                    role=User.Role.TEACHER, is_active=True
                ).order_by(
                    "last_name", "first_name"
                ),
                "selected_group": group_id,
                "selected_subject": subject_id,
                "selected_teacher": teacher_id,
                "is_teacher_view": False,
                "next_url": reverse("week_journal")
                + f"?week={monday.isoformat()}&group={group_id}&subject={subject_id}&teacher={teacher_id}",
            }
        )
        # Кнопки недель: на границе полугодия соседняя кнопка неактивна.
        context.update(week_navigation(term, monday))
        return context


# --- Импорт учеников из Excel -------------------------------------------------
class StudentImportView(StaffRequiredMixin, ActivePeriodMixin, TemplateView):
    """Загрузка списка детей из Excel с предварительным просмотром."""

    template_name = "journal/students/import.html"

    def get_context(self, request, form=None, parsed=None, warnings=None):
        context = {
            "title": _("Импорт учеников из Excel"),
            "form": form
            or StudentImportForm(term=self.period_term, branch=self.period_branch),
            "parsed": parsed,
            "warnings": warnings or [],
            "term": self.period_term,
            "branch": self.period_branch,
            "groups": Group.objects.filter(term=self.period_term).select_related("subject")
            if self.period_term
            else Group.objects.none(),
        }
        return context

    def get(self, request):
        request.session.pop(IMPORT_SESSION_KEY, None)
        return render(request, self.template_name, self.get_context(request))

    def post(self, request):
        form = StudentImportForm(
            request.POST,
            request.FILES,
            term=self.period_term,
            branch=self.period_branch,
        )
        if not form.is_valid():
            messages.error(request, _("Файл не принят: проверьте форму."))
            return render(request, self.template_name, self.get_context(request, form=form))

        try:
            parsed, warnings = importers.parse_students(form.cleaned_data["file"])
        except Exception as error:  # noqa: BLE001 - битый файл не должен давать 500
            messages.error(
                request,
                _("Не удалось прочитать файл: %(error)s") % {"error": error},
            )
            return render(
                request,
                self.template_name,
                self.get_context(
                    request, form=form, warnings=[_("Файл не удалось прочитать.")]
                ),
            )

        importers.mark_duplicates(parsed)
        group = form.cleaned_data.get("group")
        request.session[IMPORT_SESSION_KEY] = {
            "students": parsed,
            "group": group.pk if group else None,
            "skip_duplicates": bool(form.cleaned_data.get("skip_duplicates")),
            "warnings": warnings[:50],
        }
        return render(
            request,
            "journal/students/import_preview.html",
            {
                "title": _("Проверьте список"),
                "parsed": parsed,
                "warnings": warnings,
                "group": group,
                "skip_duplicates": bool(form.cleaned_data.get("skip_duplicates")),
                "duplicates": sum(1 for item in parsed if item.get("duplicate")),
                "term": self.period_term,
                "branch": self.period_branch,
            },
        )


class StudentImportConfirmView(StaffRequiredMixin, ActivePeriodMixin, View):
    """Подтверждение импорта: создаём учеников и добавляем их в выбранную группу."""

    def post(self, request):
        payload = request.session.get(IMPORT_SESSION_KEY) or {}
        students = payload.get("students") or []
        if not students:
            messages.error(request, _("Список для импорта не найден — загрузите файл заново."))
            return redirect("student_import")

        selected = {
            int(value) for value in request.POST.getlist("rows") if str(value).isdigit()
        }
        skip_duplicates = payload.get("skip_duplicates", True)
        created, skipped = 0, 0
        new_students = []
        for student in students:
            index = student.get("index", 0)
            if selected and index not in selected:
                continue
            if skip_duplicates and student.get("duplicate"):
                skipped += 1
                continue
            birth = parse_iso_date(student.get("birth_date"))
            dismissal = student.get("dismissal") or Student.Dismissal.NONE.value
            valid_statuses = {value for value, _ in Student.Dismissal.choices}
            if dismissal not in valid_statuses:
                dismissal = Student.Dismissal.NONE.value
            obj, was_created = Student.objects.get_or_create(
                last_name=student["last_name"],
                first_name=student["first_name"],
                middle_name=student.get("middle_name", ""),
                defaults={
                    "birth_date": birth,
                    "dismissal_status": dismissal,
                },
            )
            # Если ученик уже был — заполняем только пустые поля.
            if not was_created:
                changed = []
                if obj.birth_date is None and birth is not None:
                    obj.birth_date = birth
                    changed.append("birth_date")
                if (
                    obj.dismissal_status == Student.Dismissal.NONE.value
                    and dismissal != Student.Dismissal.NONE.value
                ):
                    obj.dismissal_status = dismissal
                    changed.append("dismissal_status")
                if changed:
                    obj.save(update_fields=changed)
            new_students.append(obj)
            created += 1 if was_created else 0
            skipped += 0 if was_created else 1

        group = None
        group_id = payload.get("group")
        if group_id and new_students:
            group = Group.objects.filter(pk=group_id).first()
            if group is not None:
                group.students.add(*new_students)

        request.session.pop(IMPORT_SESSION_KEY, None)
        messages.success(
            request,
            _("Импорт завершён: добавлено учеников — %(created)s, "
              "уже было в базе — %(skipped)s.")
            % {"created": created, "skipped": skipped}
            + (
                _(" Все зачислены в группу «%(name)s».") % {"name": group.name}
                if group
                else ""
            ),
        )
        return redirect("students")


# --- Участие в конкурсах и повышение квалификации ----------------------------
def teacher_students(user):
    """Ученики групп, которые ведёт преподаватель: из них он выбирает участников."""
    groups = Group.objects.filter(
        Q(subject__teacher=user) | Q(subject__teachers=user)
    ).distinct()
    return Student.objects.filter(groups__in=groups).distinct().order_by(
        "last_name", "first_name"
    )


class ContestListView(
    LoginRequiredMixin, ActivePeriodMixin, ListPaginationMixin, ListView
):
    """Участие детей в конкурсах.

    Методист и администратор видят все записи, преподаватель — только те,
    которые добавил сам.
    """

    model = ContestResult
    template_name = "journal/contests/list.html"
    context_object_name = "contests"

    def get_queryset(self):
        queryset = ContestResult.objects.select_related("student", "created_by")
        if self.request.user.is_teacher:
            queryset = queryset.filter(created_by=self.request.user)
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(student__last_name__icontains=query)
                | Q(student__first_name__icontains=query)
                | Q(subject__icontains=query)
            )
        status = self.request.GET.get("status", "")
        if status:
            queryset = queryset.filter(status=status)
        date_from = parse_iso_date(self.request.GET.get("date_from"))
        date_to = parse_iso_date(self.request.GET.get("date_to"))
        if date_from:
            queryset = queryset.filter(date__gte=date_from)
        if date_to:
            queryset = queryset.filter(date__lte=date_to)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        own = ContestResult.objects.filter(created_by=user) if user.is_teacher \
            else ContestResult.objects.all()
        context.update(
            {
                "title": _("Участие в конкурсах"),
                "statuses": ContestResult.Status.choices,
                "status_counts": {
                    value: own.filter(status=value).count()
                    for value, _ in ContestResult.Status.choices
                },
                "query": self.request.GET.get("q", ""),
                "selected_status": self.request.GET.get("status", ""),
                "date_from": self.request.GET.get("date_from", ""),
                "date_to": self.request.GET.get("date_to", ""),
                "can_delete": user.is_staff_member,
                "own_only": user.is_teacher,
                "can_export": True,
            }
        )
        return context


class ContestCreateView(LoginRequiredMixin, CreateView):
    model = ContestResult
    form_class = ContestResultForm
    template_name = "journal/contests/form.html"
    success_url = reverse_lazy("contests")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.user.is_teacher:
            kwargs["students"] = teacher_students(self.request.user)
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        student_id = self.request.GET.get("student")
        if student_id and student_id.isdigit():
            initial["student"] = student_id
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, _("Участие в конкурсе добавлено."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Новое участие в конкурсе")
        context["can_delete"] = False
        return context


class ContestUpdateView(LoginRequiredMixin, UpdateView):
    model = ContestResult
    form_class = ContestResultForm
    template_name = "journal/contests/form.html"
    success_url = reverse_lazy("contests")

    def get_queryset(self):
        queryset = ContestResult.objects.all()
        if self.request.user.is_teacher:
            # Преподаватель правит только свои записи.
            queryset = queryset.filter(created_by=self.request.user)
        return queryset

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.user.is_teacher:
            kwargs["students"] = teacher_students(self.request.user)
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, _("Запись обновлена."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Конкурс · %(title)s") % {"title": self.object.title}
        context["can_delete"] = self.request.user.is_staff_member
        if context["can_delete"]:
            context["delete_url"] = reverse("contest_delete", args=[self.object.pk])
        return context


class ContestDeleteView(StaffRequiredMixin, DeleteView):
    model = ContestResult
    template_name = "journal/confirm_delete.html"
    success_url = reverse_lazy("contests")

    def form_valid(self, form):
        messages.success(self.request, _("Запись удалена."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Удаление записи о конкурсе")
        context["object_name"] = str(self.object)
        context["cancel_url"] = reverse("contests")
        context["details"] = [
            _("Ученик: %(name)s") % {"name": self.object.student.full_name},
            _("Мероприятие: %(title)s") % {"title": self.object.title},
            _("Дата: %(date)s") % {"date": f"{self.object.date:%d.%m.%Y}"},
        ]
        return context


class DevelopmentListView(LoginRequiredMixin, ListPaginationMixin, ListView):
    """Повышение квалификации педагогов.

    Методист и администратор видят все записи, преподаватель — только свои.
    """
    """Повышение квалификации и конкурсы педагогов."""

    model = TeacherDevelopment
    template_name = "journal/development/list.html"
    context_object_name = "records"

    def get_queryset(self):
        queryset = TeacherDevelopment.objects.select_related("teacher", "created_by")
        if self.request.user.is_teacher:
            # Преподаватель видит только свои записи.
            queryset = queryset.filter(teacher=self.request.user)
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(program__icontains=query)
                | Q(organization__icontains=query)
                | Q(teacher__last_name__icontains=query)
                | Q(teacher__first_name__icontains=query)
            )
        kind = self.request.GET.get("kind", "")
        if kind:
            queryset = queryset.filter(kind=kind)
        teacher_id = self.request.GET.get("teacher", "")
        if teacher_id.isdigit():
            queryset = queryset.filter(teacher_id=int(teacher_id))
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        own = TeacherDevelopment.objects.filter(teacher=user) if user.is_teacher \
            else TeacherDevelopment.objects.all()
        context.update(
            {
                "title": _("Повышение квалификации педагогов"),
                "kinds": TeacherDevelopment.Kind.choices,
                "teachers": User.objects.filter(
                    role=User.Role.TEACHER, is_active=True
                ).order_by(
                    "last_name", "first_name"
                ),
                "query": self.request.GET.get("q", ""),
                "selected_kind": self.request.GET.get("kind", ""),
                "selected_teacher": self.request.GET.get("teacher", ""),
                "hours_total": sum(item.hours or 0 for item in own),
                # Преподаватель видит только свои записи и может их удалять:
                # это записи о его же курсах и конкурсах.
                "can_delete": True,
                "own_only": user.is_teacher,
                "can_export": True,
            }
        )
        return context


class DevelopmentCreateView(LoginRequiredMixin, CreateView):
    model = TeacherDevelopment
    form_class = TeacherDevelopmentForm
    template_name = "journal/development/form.html"
    success_url = reverse_lazy("development")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["teacher_user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        if self.request.user.is_teacher:
            form.instance.teacher = self.request.user
        form.instance.created_by = self.request.user
        messages.success(self.request, _("Запись добавлена."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Новая запись о повышении квалификации")
        context["can_delete"] = False
        return context


class DevelopmentUpdateView(LoginRequiredMixin, UpdateView):
    model = TeacherDevelopment
    form_class = TeacherDevelopmentForm
    template_name = "journal/development/form.html"
    success_url = reverse_lazy("development")

    def get_queryset(self):
        queryset = TeacherDevelopment.objects.all()
        if self.request.user.is_teacher:
            queryset = queryset.filter(teacher=self.request.user)
        return queryset

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["teacher_user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        if self.request.user.is_teacher:
            form.instance.teacher = self.request.user
        messages.success(self.request, _("Запись обновлена."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Запись · %(program)s") % {"program": self.object.program}
        context["can_delete"] = True
        context["delete_url"] = reverse("development_delete", args=[self.object.pk])
        return context


class DevelopmentDeleteView(LoginRequiredMixin, DeleteView):
    """Удаление записи. Преподаватель удаляет только свои записи, методист — любые."""

    model = TeacherDevelopment
    template_name = "journal/confirm_delete.html"
    success_url = reverse_lazy("development")

    def get_queryset(self):
        queryset = TeacherDevelopment.objects.all()
        if self.request.user.is_teacher:
            queryset = queryset.filter(teacher=self.request.user)
        return queryset

    def form_valid(self, form):
        messages.success(self.request, _("Запись удалена."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Удаление записи")
        context["object_name"] = str(self.object)
        context["cancel_url"] = reverse("development")
        context["details"] = [
            _("Педагог: %(name)s") % {"name": self.object.teacher.full_name},
            _("Программа: %(program)s") % {"program": self.object.program},
            _("Дата: %(date)s") % {"date": f"{self.object.date:%d.%m.%Y}"},
        ]
        return context


class TopicJournalView(
    StaffRequiredMixin, ActivePeriodMixin, ListPaginationMixin, ListView
):
    """Журнал тем занятий: что заполнили преподаватели, с выгрузкой в Excel."""

    model = LessonTopic
    template_name = "journal/topics/list.html"
    context_object_name = "topics"

    def get_queryset(self):
        queryset = LessonTopic.objects.select_related(
            "schedule__group__subject", "schedule__group__term", "schedule__teacher",
            "schedule__room", "schedule__branch", "teacher",
        )
        # Тема, попавшая в период отсутствия преподавателя, относится к занятию,
        # которое не состоялось: её часы в сводку не входят (см. «отменено» в шапке).
        queryset = queryset.annotate(
            cancelled=Exists(
                TeacherAbsence.objects.filter(
                    teacher=OuterRef("schedule__teacher"),
                    date_from__lte=OuterRef("date"),
                    date_to__gte=OuterRef("date"),
                )
            )
        )
        if self.period_term is not None:
            queryset = queryset.filter(schedule__group__term=self.period_term)
        if self.period_branch is not None:
            queryset = queryset.filter(schedule__branch=self.period_branch)

        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(topic__icontains=query)
                | Q(schedule__group__subject__name__icontains=query)
                | Q(schedule__group__name__icontains=query)
            )
        group_id = self.request.GET.get("group", "")
        if group_id.isdigit():
            queryset = queryset.filter(schedule__group_id=int(group_id))
        teacher_id = self.request.GET.get("teacher", "")
        if teacher_id.isdigit():
            queryset = queryset.filter(schedule__teacher_id=int(teacher_id))
        date_from = parse_iso_date(self.request.GET.get("date_from"))
        date_to = parse_iso_date(self.request.GET.get("date_to"))
        if date_from:
            queryset = queryset.filter(date__gte=date_from)
        if date_to:
            queryset = queryset.filter(date__lte=date_to)
        return queryset.order_by("-date", "schedule__start_time")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "title": _("Журнал тем занятий"),
                "term": self.period_term,
                "branch": self.period_branch,
                "groups": Group.objects.filter(
                    term=self.period_term, subject__branch=self.period_branch
                ).select_related("subject")
                if self.period_branch is not None
                else Group.objects.filter(term=self.period_term),
                "teachers": User.objects.filter(
                    role=User.Role.TEACHER, is_active=True
                ).order_by(
                    "last_name", "first_name"
                ),
                "query": self.request.GET.get("q", ""),
                "selected_group": self.request.GET.get("group", ""),
                "selected_teacher": self.request.GET.get("teacher", ""),
                "date_from": self.request.GET.get("date_from", ""),
                "date_to": self.request.GET.get("date_to", ""),
                # Часы считаем по всем найденным записям, а не только по странице.
                # Часы отменённых занятий показываются отдельной строкой «отменено».
                "hours_total": self.get_queryset()
                .filter(cancelled=False)
                .aggregate(total=Sum("hours"))["total"]
                or 0,
                "cancelled_hours": self.get_queryset()
                .filter(cancelled=True)
                .aggregate(total=Sum("hours"))["total"]
                or 0,
                "export_query": self.request.GET.urlencode(),
            }
        )
        return context


# --- Демо-режим (без авторизации) --------------------------------------------
class DemoModeMixin:
    """Демо-страницы доступны только при DEMO_MODE=1."""

    def dispatch(self, request, *args, **kwargs):
        if not settings.DEMO_MODE:
            raise Http404(_("Демо-режим выключен."))
        return super().dispatch(request, *args, **kwargs)


class DemoLandingView(DemoModeMixin, TemplateView):
    template_name = "journal/demo/landing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["demo_ready"] = demo_tools.demo_data_ready()
        context["demo_database"] = settings.DATABASES["demo"]["NAME"]
        return context


class DemoLoginView(DemoModeMixin, View):
    """Вход без пароля: подставляем готовую демо-учётку из демонстрационной базы."""

    ROLES = {
        "methodist": (demo_tools.DEMO_METHODIST, _("методист УМО")),
        "teacher": (demo_tools.DEMO_TEACHER, _("преподаватель")),
    }

    def get(self, request, role):
        if role not in self.ROLES:
            raise Http404(_("Неизвестная демо-роль."))
        if not demo_tools.demo_database_ready():
            messages.error(
                request,
                _("Демонстрационная база ещё не готова. Администратору нужно выполнить "
                  "«python manage.py demo_setup» (или перезапустить контейнер с DEMO_MODE=1)."),
            )
            return redirect("demo")
        username, role_title = self.ROLES[role]
        # Из обычной сессии выходим: иначе демо-пользователь «подхватится» поверх рабочего.
        if request.user.is_authenticated and not request.session.get("demo"):
            logout(request)

        created = demo_tools.prepare_demo_data()
        # Признак ставим до входа (чтобы запросы шли в демо-базу) …
        demo_tools.mark_demo_session(request, role, created)
        user = User.objects.using(demo_router.DEMO_ALIAS).filter(username=username).first()
        if user is None:
            messages.error(request, _("Не удалось подготовить демо-данные."))
            return redirect("login")

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        # … и после входа: при смене роли Django очищает сессию вместе с признаком.
        demo_tools.mark_demo_session(request, role, created)
        security_tools.record_event(
            VisitLog.Kind.LOGIN,
            request=request,
            # Учётная запись взята из демо-базы, а её номер совпадает с номером
            # совсем другого человека в рабочей базе. Поэтому связь с пользователем
            # не записываем — только логин и пометку, что это демонстрация
            # (так же, как в journal/middleware.py).
            user=None,
            username=f"{getattr(user, 'username', '')} (демонстрация)",
            path=request.path,
            detail=_("демо-режим"),
        )
        messages.success(
            request,
            _("Демо-режим: вы вошли как %(role)s (%(name)s). "
              "Это отдельная демонстрационная база — рабочий журнал она не затрагивает. "
              "Данные можно свободно менять.")
            % {"role": role_title, "name": user.full_name},
        )
        return redirect("home")


class DemoExitView(View):
    """Выход из демо-режима: сессия снова работает с рабочей базой."""

    def get(self, request):
        demo_tools.leave_demo(request)
        logout(request)
        messages.success(
            request,
            _("Вы вышли из демонстрационного режима.")
        )
        return redirect("login")


class DemoManageView(AdminRequiredMixin, TemplateView):
    """Администратору: состояние демо-режима и его очистка."""

    template_name = "journal/demo/manage.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Демонстрационный режим")
        context["demo_enabled"] = settings.DEMO_MODE
        context["demo_database"] = settings.DATABASES["demo"]["NAME"]
        context["demo_ready"] = demo_tools.demo_database_ready()
        context["demo_counts"] = demo_tools.demo_database_counts()
        return context


class DemoWipeView(AdminRequiredMixin, View):
    """Очистка демонстрационной базы (рабочих данных не касается)."""

    def post(self, request):
        if not demo_tools.demo_database_ready():
            messages.error(request, _("Демонстрационная база недоступна."))
            return redirect("demo_manage")
        removed = demo_tools.wipe_demo_data()
        messages.success(
            request,
            _("Демонстрационная база очищена: ")
            + ", ".join(f"{label} — {count}" for label, count in removed.items() if count)
            + _(". Рабочий журнал не затронут."),
        )
        left = demo_tools.demo_branches_with_data()
        if left:
            messages.warning(
                request,
                _("Не тронуты адреса со связанными данными: ") + ", ".join(left),
            )
        return redirect("demo_manage")


# --- Выгрузка в Excel --------------------------------------------------------
def _filtered_attendance(request, queryset, term=None):
    group_id = request.GET.get("group")
    if group_id and group_id.isdigit():
        queryset = queryset.filter(schedule__group_id=int(group_id))
    subject_id = request.GET.get("subject")
    if subject_id and subject_id.isdigit():
        queryset = queryset.filter(schedule__group__subject_id=int(subject_id))
    dates_given = False
    for field, lookup in (("date_from", "date__gte"), ("date_to", "date__lte")):
        raw = request.GET.get(field)
        if raw:
            try:
                queryset = queryset.filter(**{lookup: date_cls.fromisoformat(raw)})
                dates_given = True
            except ValueError:
                pass
    if not dates_given and term is not None:
        # По умолчанию — период активного полугодия.
        queryset = queryset.filter(
            date__gte=term.start_date, date__lte=term.end_date
        )
    return queryset


class ExportStudentsView(StaffRequiredMixin, View):
    def get(self, request):
        return excel.as_response(excel.students_workbook(), "students")


class ExportSubjectsView(StaffRequiredMixin, ActivePeriodMixin, View):
    def get(self, request):
        return excel.as_response(
            excel.subjects_workbook(self.period_term, self.period_branch),
            "subjects_groups",
        )


class ExportContestsView(LoginRequiredMixin, View):
    """Выгрузка участия детей в конкурсах (преподаватель — только свои записи)."""

    def get(self, request):
        queryset = ContestResult.objects.select_related("student")
        if request.user.is_teacher:
            queryset = queryset.filter(created_by=request.user)
        query = request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(student__last_name__icontains=query)
                | Q(student__first_name__icontains=query)
            )
        status = request.GET.get("status", "")
        if status:
            queryset = queryset.filter(status=status)
        date_from = parse_iso_date(request.GET.get("date_from"))
        date_to = parse_iso_date(request.GET.get("date_to"))
        if date_from:
            queryset = queryset.filter(date__gte=date_from)
        if date_to:
            queryset = queryset.filter(date__lte=date_to)
        return excel.as_response(excel.contests_workbook(queryset), "contests")


class ExportDevelopmentView(LoginRequiredMixin, View):
    """Выгрузка повышения квалификации педагогов (преподаватель — только свои записи)."""

    def get(self, request):
        queryset = TeacherDevelopment.objects.select_related("teacher")
        if request.user.is_teacher:
            queryset = queryset.filter(teacher=request.user)
        kind = request.GET.get("kind", "")
        if kind:
            queryset = queryset.filter(kind=kind)
        teacher_id = request.GET.get("teacher", "")
        if teacher_id.isdigit():
            queryset = queryset.filter(teacher_id=int(teacher_id))
        query = request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(program__icontains=query)
                | Q(organization__icontains=query)
                | Q(teacher__last_name__icontains=query)
            )
        return excel.as_response(excel.development_workbook(queryset), "development")


class ExportScheduleView(StaffRequiredMixin, ActivePeriodMixin, View):
    def get(self, request):
        return excel.as_response(
            excel.schedule_workbook(self.period_term, self.period_branch), "schedule"
        )


class ExportScheduleImageView(StaffRequiredMixin, ActivePeriodMixin, View):
    """Расписание картинкой: вертикальный или горизонтальный PNG."""

    def get(self, request):
        orientation = request.GET.get("orientation", "vertical")
        if orientation not in {"vertical", "horizontal"}:
            orientation = "vertical"
        stream, filename = image_export.schedule_image(
            self.period_term, self.period_branch, orientation
        )
        response = HttpResponse(stream.getvalue(), content_type="image/png")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ExportTopicsView(StaffRequiredMixin, ActivePeriodMixin, View):
    """Выгрузка тем занятий с датами и академическими часами."""

    def get(self, request):
        queryset = Schedule.objects.filter(
            group__term=self.period_term, branch=self.period_branch
        )
        group_id = request.GET.get("group")
        if group_id and group_id.isdigit():
            queryset = queryset.filter(group_id=int(group_id))
        subject_id = request.GET.get("subject")
        if subject_id and subject_id.isdigit():
            queryset = queryset.filter(group__subject_id=int(subject_id))
        teacher_id = request.GET.get("teacher")
        if teacher_id and teacher_id.isdigit():
            queryset = queryset.filter(teacher_id=int(teacher_id))
        return excel.as_response(
            excel.topics_workbook(
                self.period_term, self.period_branch, queryset=queryset
            ),
            "topics",
        )


class ExportAttendanceView(StaffRequiredMixin, ActivePeriodMixin, View):
    def get(self, request):
        queryset = Attendance.objects.filter(
            schedule__group__term=self.period_term,
            schedule__branch=self.period_branch,
        )
        queryset = _filtered_attendance(request, queryset, term=self.period_term)
        return excel.as_response(
            excel.attendance_workbook(
                queryset, term=self.period_term, branch=self.period_branch
            ),
            "attendance",
        )


class ExportUsersView(AdminRequiredMixin, View):
    def get(self, request):
        return excel.as_response(excel.users_workbook(), "users")


class TeacherExportView(TeacherRequiredMixin, ActivePeriodMixin, View):
    """Преподаватель выгружает только свою посещаемость за активную полугодие."""

    def get(self, request):
        queryset = Attendance.objects.filter(
            schedule__teacher=request.user, schedule__group__term=self.period_term
        )
        queryset = _filtered_attendance(request, queryset, term=self.period_term)
        return excel.as_response(
            excel.attendance_workbook(
                queryset, title=_("Моя посещаемость"), term=self.period_term
            ),
            "my_attendance",
        )


class ExportIndexView(StaffRequiredMixin, ActivePeriodMixin, TemplateView):
    template_name = "journal/export/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = self.period_term
        branch = self.period_branch
        context["term"] = term
        context["branch"] = branch
        context["groups"] = Group.objects.filter(term=term).select_related("subject")
        context["subjects"] = self.get_subjects()
        context["counts"] = {
            "students": Student.objects.count(),
            "subjects": self.get_subjects().count(),
            "groups": Group.objects.filter(term=term).count(),
            "schedules": Schedule.objects.filter(
                group__term=term, branch=branch
            ).count(),
            "attendance": Attendance.objects.filter(
                schedule__group__term=term, schedule__branch=branch
            ).count(),
        }
        return context

    def get_subjects(self):
        """Программы активного полугодия и активного адреса."""
        queryset = Subject.objects.select_related("teacher", "branch", "term")
        if self.period_term is not None:
            queryset = queryset.filter(term=self.period_term)
        if self.period_branch is not None:
            queryset = queryset.filter(branch=self.period_branch)
        return queryset


# --- Учебные годы и полугодия -------------------------------------------------
class PeriodSwitchView(LoginRequiredMixin, View):
    """Переключение активного полугодия и адреса: выбор хранится в сессии пользователя."""

    def get(self, request):
        term = None
        year = None
        branch = None

        branch_id = request.GET.get("branch")
        if branch_id and branch_id.isdigit():
            branch = Branch.objects.filter(pk=int(branch_id)).first()

        term_id = request.GET.get("term")
        year_id = request.GET.get("year")
        if term_id and term_id.isdigit():
            term = Term.objects.filter(pk=int(term_id)).select_related("year").first()
        if year_id and year_id.isdigit():
            year = AcademicYear.objects.filter(pk=int(year_id)).first()
        if year is not None and term is not None and term.year_id != year.pk:
            # Учебный год переключили — полугодие берём из него.
            term = None
        if term is None and year is not None:
            term = year.terms.order_by("number").first()
            if term is None:
                term = periods_tools.current_term()

        if branch is None and year is None and term is None:
            messages.error(request, _("Не удалось переключить учебный период."))
            return redirect("home")

        if branch is not None:
            periods_tools.set_active_branch(request, branch)
        if year is not None or term is not None:
            periods_tools.set_active_period(request, year=year, term=term)

        parts = []
        if term is not None:
            parts.append(
                _("полугодие: %(term)s (%(range)s)")
                % {"term": term.full_title, "range": term.date_range}
            )
        elif year is not None:
            parts.append(_("учебный год: %(year)s") % {"year": year.title})
        if branch is not None:
            parts.append(_("адрес: %(branch)s") % {"branch": branch.name})
        messages.success(request, _("Выбрано — ") + ", ".join(parts) + ".")

        target = request.GET.get("next") or ""
        if not (target.startswith("/") and not target.startswith("//")):
            target = reverse("home")
        return redirect(target)


class PeriodListView(StaffRequiredMixin, ActivePeriodMixin, ListView):
    """Учебные годы и полугодия: даты, активное полугодие, создание года.

    Годы выводятся одним списком без постраничного вывода: год живёт долго, но
    список нужен целиком (по нему ищут и переключаются), поэтому прокрутка —
    на стороне страницы, а высоту списка ограничивает правило ``[data-year-list]``
    в ``journal/static/journal/app.css``. ``paginate_by = None`` — намеренно:
    так список годов не режется на страницы общим приёмом ``ListPaginationMixin``.
    """

    model = AcademicYear
    template_name = "journal/periods/list.html"
    context_object_name = "years"
    paginate_by = None

    def get_queryset(self):
        # Свежие годы — первыми: с ними работают чаще всего.
        return AcademicYear.objects.order_by("-start_date")

    def get_year(self, request):
        year_id = request.GET.get("year") or request.POST.get("year")
        year = None
        if year_id and year_id.isdigit():
            year = AcademicYear.objects.filter(pk=int(year_id)).first()
        return year or self.period_year

    def term_rows(self, year):
        """Строки таблицы полугодий выбранного года — с количеством данных."""
        terms = []
        if year is not None:
            today = timezone.localdate()
            for term in year.terms.all():
                subjects = Subject.objects.filter(term=term)
                if self.period_branch is not None:
                    subjects = subjects.filter(branch=self.period_branch)
                terms.append(
                    {
                        "term": term,
                        "subjects": subjects.count(),
                        "groups": term.groups.count(),
                        "schedules": Schedule.objects.filter(group__term=term).count(),
                        "attendance": Attendance.objects.filter(
                            schedule__group__term=term
                        ).count(),
                        "is_active": bool(
                            self.period_term is not None
                            and term.pk == self.period_term.pk
                        ),
                        "is_current": term.contains(today),
                    }
                )
        return terms

    def get_context_data(self, **kwargs):
        # super() добавляет к контексту список годов (object_list → years):
        # постраничного вывода у него нет, в шаблоне выводится всё сразу.
        context = super().get_context_data(**kwargs)
        year = self.get_year(self.request)
        form = kwargs.get("form")
        if form is None:
            start, end = periods_tools.academic_year_bounds(
                timezone.localdate().replace(year=timezone.localdate().year + 1)
            )
            form = AcademicYearForm(initial={"start_date": start, "end_date": end})
        context.update(
            {
                "title": _("Учебные периоды"),
                "year": year,
                "terms": self.term_rows(year),
                "form": form,
                "branch": self.period_branch,
                "copy_url": reverse("period_copy"),
                "periods_url": f"{reverse('periods')}?year={year.pk}" if year else reverse("periods"),
            }
        )
        return context

    def post(self, request):
        # Список годов нужен и при показе страницы после ошибки формы:
        # get_context_data берёт его из object_list (см. ListView).
        self.object_list = self.get_queryset()
        action = request.POST.get("action")

        if action == "create_year":
            form = AcademicYearForm(request.POST)
            if not form.is_valid():
                for errors in form.errors.values():
                    for error in errors:
                        messages.error(request, error)
                return self.render_to_response(
                    self.get_context_data(form=form)
                )
            year, created = periods_tools.create_year(
                form.cleaned_data["start_date"], form.cleaned_data["end_date"]
            )
            messages.success(
                request,
                _("Учебный год %(year)s ")
                % {"year": year.title}
                + (
                    _("создан вместе с двумя полугодиями.")
                    if created
                    else _("уже существует.")
                ),
            )
            return redirect(f"{reverse('periods')}?year={year.pk}")

        if action == "save_terms":
            updated = 0
            errors = []
            for term_id in request.POST.getlist("term_id"):
                if not term_id.isdigit():
                    continue
                term = Term.objects.filter(pk=int(term_id)).first()
                if term is None:
                    continue
                raw_number = request.POST.get(f"number_{term.pk}", "").strip()
                raw_start = request.POST.get(f"start_{term.pk}", "").strip()
                raw_end = request.POST.get(f"end_{term.pk}", "").strip()
                try:
                    number = int(raw_number)
                    start = date_cls.fromisoformat(raw_start)
                    end = date_cls.fromisoformat(raw_end)
                except (TypeError, ValueError):
                    errors.append(
                        _("%(term)s: даты и номер должны быть заполнены.")
                        % {"term": term.title}
                    )
                    continue
                if end <= start:
                    errors.append(
                        _("%(term)s: окончание должно быть позже начала.")
                        % {"term": term.title}
                    )
                    continue
                if (
                    Term.objects.filter(year=term.year, number=number)
                    .exclude(pk=term.pk)
                    .exists()
                ):
                    errors.append(
                        _("Номер %(number)s уже занят другой полугодиею.")
                        % {"number": number}
                    )
                    continue
                if (term.number, term.start_date, term.end_date) != (number, start, end):
                    term.number = number
                    term.start_date = start
                    term.end_date = end
                    # Правила полугодий (границы учебного года, пересечения, порядок
                    # номеров) проверяет приёмник модели и поднимает ValidationError.
                    # Ловим его и показываем понятное сообщение вместо пятисотой ошибки:
                    # в базе при этом ничего не меняется.
                    try:
                        term.save(update_fields=["number", "start_date", "end_date"])
                    except ValidationError as error:
                        errors.append(" ".join(error.messages))
                        continue
                    updated += 1
            for error in errors:
                messages.error(request, error)
            if updated:
                messages.success(
                    request,
                    _("Полугодия сохранены (изменено: %(count)s).") % {"count": updated},
                )
            elif not errors:
                messages.info(request, _("Изменений в полугодиях нет."))
            year = self.get_year(request)
            return redirect(f"{reverse('periods')}?year={year.pk}" if year else reverse("periods"))

        if action == "delete_year":
            # Учебные годы и полугодия не удаляем никому, кроме администратора:
            # вместе с годом уходят группы, занятия и отметки.
            if not getattr(request.user, "is_admin", False):
                messages.error(
                    request,
                    _("Удалять учебные годы может только администратор: вместе с годом "
                      "удаляются его полугодия, группы, занятия и отметки."),
                )
                return redirect("periods")
            year = AcademicYear.objects.filter(
                pk=request.POST.get("year_id") or 0
            ).first()
            if year is None:
                messages.error(request, _("Учебный год не найден."))
                return redirect("periods")
            report = periods_tools.year_deletion_report(year)
            blockers = periods_tools.year_delete_blockers(year)
            if blockers:
                for blocker in blockers:
                    messages.error(request, blocker)
                return redirect(f"{reverse('periods')}?year={year.pk}")
            # Удаляем только после честного подтверждения: год с программами без групп
            # раньше удалялся молча вместе с ними. В подтверждении показываем, что
            # именно уйдёт вместе с годом (программы, группы, занятия, отметки).
            has_content = any(
                report[key] for key in ("subjects", "groups", "schedules", "attendance")
            )
            if has_content and request.POST.get("confirm_delete") != "1":
                messages.warning(
                    request,
                    _("Учебный год %(year)s: %(report)s "
                      "Нажмите «Удалить год» ещё раз, чтобы подтвердить удаление.")
                    % {"year": year.title, "report": report["message"]},
                )
                return redirect(f"{reverse('periods')}?year={year.pk}&confirm_delete=1")
            title = year.title
            year.delete()
            message = _("Учебный год %(year)s удалён.") % {"year": title}
            if has_content:
                message += " " + report["message"][:1].upper() + report["message"][1:]
            messages.success(request, message)
            return redirect("periods")

        messages.error(request, _("Неизвестное действие."))
        return redirect("periods")


class PeriodCopyView(StaffRequiredMixin, ActivePeriodMixin, View):
    """Перенос программ, групп (с составом) и расписания из другого полугодия или адреса."""

    template_name = "journal/periods/copy.html"

    def get_source_term(self, request):
        source_id = request.GET.get("source") or request.POST.get("source")
        if source_id and source_id.isdigit():
            term = (
                Term.objects.filter(pk=int(source_id)).select_related("year").first()
            )
            if term is not None:
                return term
        return self.previous_term()

    def previous_term(self):
        if self.period_term is None:
            return None
        return (
            Term.objects.filter(start_date__lt=self.period_term.start_date)
            .select_related("year")
            .order_by("-start_date")
            .first()
        )

    def get_source_branch(self, request):
        raw = request.GET.get("source_branch") or request.POST.get("source_branch")
        if raw and raw.isdigit():
            branch = Branch.objects.filter(pk=int(raw)).first()
            if branch is not None:
                return branch
        return self.period_branch or Branch.objects.order_by("order", "name").first()

    def get_context(self, request, source=None, report=None, source_branch=None):
        source = source if source is not None else self.get_source_term(request)
        source_branch = (
            source_branch
            if source_branch is not None
            else self.get_source_branch(request)
        )
        target = self.period_term
        target_branch = self.period_branch
        groups = []
        subjects_count = 0
        if source is not None:
            queryset = (
                Group.objects.filter(term=source)
                .select_related("subject", "subject__teacher", "subject__branch")
                .prefetch_related("students", "schedules")
                .order_by("subject__name", "name")
            )
            if source_branch is not None:
                queryset = queryset.filter(subject__branch=source_branch)
            groups_list = list(queryset)
            subjects_count = len({group.subject_id for group in groups_list})
            for group in groups_list:
                already = None
                if target is not None:
                    already = Group.objects.filter(
                        term=target,
                        subject__name__iexact=group.subject.name,
                        subject__branch=target_branch,
                        name=group.name,
                    ).first()
                groups.append(
                    {
                        "group": group,
                        "students": group.students.count(),
                        "lessons": group.schedules.count(),
                        "already": already,
                        "already_students": already.students.count() if already else 0,
                    }
                )
        return {
            "title": _("Перенос из другого полугодия"),
            "source": source,
            "source_branch": source_branch,
            "target": target,
            "target_branch": target_branch,
            "report": report,
            "groups": groups,
            "subjects_count": subjects_count,
            "terms": Term.objects.select_related("year").order_by(
                "-year__start_date", "number"
            ),
            "branches": Branch.objects.order_by("order", "name"),
            "rooms_note": _(
                "Кабинеты переносятся по названию, если такой кабинет есть в целевом адресе — "
                "иначе занятие переносится без кабинета."
            ),
        }

    def get(self, request):
        return render(request, self.template_name, self.get_context(request))

    def post(self, request):
        source = self.get_source_term(request)
        source_branch = self.get_source_branch(request)
        target = self.period_term
        target_branch = self.period_branch or Branch.objects.order_by("order", "name").first()
        if source is None or target is None:
            messages.error(request, _("Не выбрана полугодие-источник или активное полугодие."))
            return redirect("period_copy")
        if source.pk == target.pk and source_branch == target_branch:
            messages.error(request, _("Источник и активное полугодие с адресом совпадают."))
            return redirect("period_copy")

        group_ids = [
            int(value) for value in request.POST.getlist("groups") if value.isdigit()
        ]
        if not group_ids:
            messages.error(request, _("Отметьте хотя бы одну группу для переноса."))
            return render(
                request,
                self.template_name,
                self.get_context(
                    request, source=source, source_branch=source_branch
                ),
            )

        with_students = request.POST.get("with_students") == "on"
        with_schedule = request.POST.get("with_schedule") == "on"
        report = periods_tools.copy_term_data(
            source,
            target,
            group_ids=group_ids,
            with_students=with_students,
            with_schedule=with_schedule,
            source_branch=source_branch,
            target_branch=target_branch,
        )
        notifications.notify(
            "accounts",
            _("Перенос данных в полугодие"),
            f"Пользователь: {request.user.full_name} ({request.user.username})\n"
            f"Откуда: {source.full_title} ({source_branch.name if source_branch else '—'})\n"
            f"Куда: {target.full_title} ({target_branch.name if target_branch else '—'})\n"
            f"Программ создано: {report['subjects_created']}, групп: "
            f"{report['groups_created']}, занятий: {report['lessons_created']}",
        )
        messages.success(
            request,
            _("Перенос в «%(term)s» выполнен: программ создано %(subjects)s, "
              "групп создано %(groups)s, использовано существующих %(reused)s, "
              "добавлено учеников %(students)s, создано занятий %(lessons)s, "
              "пропущено занятий %(skipped)s.")
            % {
                "term": target.full_title,
                "subjects": report["subjects_created"],
                "groups": report["groups_created"],
                "reused": report["groups_reused"],
                "students": report["students_added"],
                "lessons": report["lessons_created"],
                "skipped": report["lessons_skipped"],
            },
        )
        return render(
            request,
            self.template_name,
            self.get_context(
                request, source=source, report=report, source_branch=source_branch
            ),
        )

# --- Безопасность (только главный админ) -------------------------------------
class SecurityView(AdminRequiredMixin, View):
    """Настройки почты, доступ по IP и журнал заходов."""

    template_name = "journal/security/index.html"

    def get_context(self, request, mail_form=None, rule_form=None, test_result=None):
        log_kind = request.GET.get("kind") or ""
        logs = VisitLog.objects.select_related("user")
        if log_kind:
            logs = logs.filter(kind=log_kind)
        ip_filter = (request.GET.get("ip") or "").strip()
        if ip_filter:
            logs = logs.filter(ip=ip_filter)

        # Подозрительные запросы: их оставляют сканеры уязвимостей.
        security_tools.prune_expired_rules()
        probe_since = timezone.now() - timedelta(days=7)
        probes = VisitLog.objects.filter(
            kind=VisitLog.Kind.PROBE, created_at__gte=probe_since
        )
        probe_ips = list(
            probes.exclude(ip=None)
            .values("ip")
            .annotate(count=Count("id"), last=Count("id"))
            .order_by("-count")[:8]
        )
        probe_paths = list(
            probes.values("path").annotate(count=Count("id")).order_by("-count")[:8]
        )
        blocked_ips = {rule.address for rule in IpRule.objects.all()}
        for item in probe_ips:
            item["blocked"] = item["ip"] in blocked_ips
        stats = {
            "visits": VisitLog.objects.filter(kind=VisitLog.Kind.VISIT).count(),
            "ips": VisitLog.objects.exclude(ip=None).values("ip").distinct().count(),
            "failed": VisitLog.objects.filter(kind=VisitLog.Kind.LOGIN_FAILED).count(),
            "probes": probes.count(),
            "blocked": VisitLog.objects.filter(kind=VisitLog.Kind.BLOCKED).count(),
        }

        return {
            "title": _("Безопасность"),
            "mail_form": mail_form or MailSettingsForm(instance=MailSettings.load()),
            "rule_form": rule_form or IpRuleForm(),
            "rules": IpRule.objects.all(),
            "logs": logs[:150],
            "stats": stats,
            "probe_ips": probe_ips,
            "probe_paths": probe_paths,
            "probe_days": 7,
            "auto_block_threshold": getattr(settings, "AUTO_BLOCK_THRESHOLD", 5),
            "auto_block_hours": getattr(settings, "AUTO_BLOCK_HOURS", 24),            "log_kind": log_kind,
            "ip_filter": ip_filter,
            "kinds": VisitLog.Kind.choices,
            "locked_ips": [
                {
                    "ip": address,
                    "attempts": security_tools.failed_attempts(address),
                }
                for address in VisitLog.objects.filter(
                    kind=VisitLog.Kind.LOGIN_FAILED
                )
                .values_list("ip", flat=True)
                .distinct()[:50]
                if address and security_tools.is_login_locked(address)
            ],
            "locked_accounts": [
                {
                    "ip": address,
                    "username": username,
                    "attempts": security_tools.failed_attempts(address, username),
                }
                for address, username in VisitLog.objects.filter(
                    kind=VisitLog.Kind.LOGIN_FAILED
                )
                .exclude(username="")
                .values_list("ip", "username")
                .distinct()[:100]
                if address and security_tools.is_login_locked(address, username)
            ],
            "current_ip": security_tools.get_client_ip(request),
            "allow_rules_exist": security_tools.allow_rules_exist(),
            "failed_total": VisitLog.objects.filter(
                kind=VisitLog.Kind.LOGIN_FAILED
            ).count(),
            "blocked_total": VisitLog.objects.filter(
                kind=VisitLog.Kind.BLOCKED
            ).count(),
            "test_result": test_result,
            "settings_flags": {
                "debug": settings.DEBUG,
                "https": settings.SECURE_SSL_REDIRECT,
                "secure_cookies": settings.SESSION_COOKIE_SECURE,
                "hsts": settings.SECURE_HSTS_SECONDS,
                "trust_proxy": settings.TRUST_PROXY_HEADERS,
            },
        }

    def get(self, request):
        return render(request, self.template_name, self.get_context(request))

    def post(self, request):
        action = request.POST.get("action")

        if action == "mail":
            form = MailSettingsForm(
                request.POST, instance=MailSettings.load()
            )
            if form.is_valid():
                form.save()
                messages.success(request, _("Настройки почты сохранены."))
                return redirect("security")
            messages.error(request, _("Настройки почты не сохранены: проверьте поля."))
            return render(
                request, self.template_name, self.get_context(request, mail_form=form)
            )

        if action == "test_mail":
            address = (request.POST.get("test_address") or "").strip()
            if "@" not in address:
                messages.error(request, _("Укажите адрес для тестового письма."))
                return redirect("security")
            ok, text = notifications.send_test_email(address)
            if ok:
                messages.success(request, text)
                security_tools.record_event(
                    VisitLog.Kind.EVENT, request=request, user=request.user,
                    path=request.path, detail=_("тестовое письмо отправлено"),
                )
            else:
                messages.error(request, text)
            return redirect("security")

        if action == "add_rule":
            form = IpRuleForm(request.POST)
            if form.is_valid():
                rule = form.save()
                messages.success(
                    request,
                    _("Правило для %(address)s добавлено.") % {"address": rule.address},
                )
            else:
                messages.error(request, _("Правило не добавлено: проверьте адрес."))
                return render(
                    request, self.template_name, self.get_context(request, rule_form=form)
                )
            security_tools.record_event(
                VisitLog.Kind.EVENT, request=request, user=request.user,
                path=request.path, detail=_("изменён список IP"),
            )
            return redirect("security")

        if action == "delete_rule":
            rule = IpRule.objects.filter(pk=request.POST.get("rule_id") or 0).first()
            if rule is not None:
                address = rule.address
                rule.delete()
                messages.success(
                    request,
                    _("Правило для %(address)s удалено.") % {"address": address},
                )
            return redirect("security")

        if action == "block_ip":
            address = (request.POST.get("address") or "").strip()
            if not security_tools.is_valid_ip(address):
                messages.error(request, _("Не похоже на IP-адрес."))
                return redirect("security")
            # Две ручные блокировки: «на сутки» и «навсегда».
            # hours=0 — бессрочно. Раньше ноль ограничивался единицей,
            # поэтому «навсегда» превращалось в блокировку на 1 час.
            raw_hours = (request.POST.get("hours") or "").strip()
            try:
                hours = int(raw_hours)
            except (TypeError, ValueError):
                hours = 24
            hours = max(0, min(hours, 24 * 365))
            if hours:
                comment = _(
                    "Заблокирован вручную на %(hours)s ч. из журнала безопасности"
                ) % {"hours": hours}
                detail = _("адрес заблокирован вручную на %(hours)s ч.") % {
                    "hours": hours
                }
            else:
                comment = _("Заблокирован вручную навсегда из журнала безопасности")
                detail = _("адрес заблокирован вручную навсегда")
            security_tools.block_address(
                address,
                comment=comment,
                hours=hours,
                automatic=False,
            )
            security_tools.record_event(
                VisitLog.Kind.EVENT, request=request, user=request.user,
                path=request.path, address=address,
                detail=detail,
            )
            messages.success(
                request,
                _("Адрес %(address)s заблокирован ") % {"address": address}
                + (
                    _("на %(hours)s ч.") % {"hours": hours}
                    if hours
                    else _("навсегда.")
                ),
            )
            return redirect("security")

        if action == "unblock_ip":
            address = (request.POST.get("address") or "").strip()
            removed = IpRule.objects.filter(address=address).delete()[0]
            if removed:
                security_tools.record_event(
                    VisitLog.Kind.EVENT, request=request, user=request.user,
                    path=request.path, address=address,
                    detail=_("блокировка снята вручную"),
                )
                messages.success(
                    request,
                    _("Блокировка для %(address)s снята.") % {"address": address},
                )
            else:
                messages.info(request, _("Такого правила уже нет."))
            return redirect("security")

        if action == "clear_log":
            VisitLog.objects.filter(kind=VisitLog.Kind.VISIT).delete()
            messages.success(request, _("Журнал заходов очищен (входы и события остались)."))
            return redirect("security")

        messages.error(request, _("Неизвестное действие."))
        return redirect("security")


# --- Резервная копия (только главный админ) ---------------------------------
COUNT_LABELS = {
    "academic_years": _("учебных годов"),
    "terms": _("полугодий"),
    "branches": _("адресов"),
    "rooms": _("кабинетов"),
    "users": _("пользователей"),
    "students": _("учеников"),
    "subjects": _("программ"),
    "groups": _("групп"),
    "schedules": _("занятий"),
    "attendance": _("отметок посещаемости"),
    "topics": _("тем занятий"),
    "contests": _("конкурсов"),
    "development": _("курсов педагогов"),
    "skipped": _("пропущено записей"),
}

#: Разделы, по которым при восстановлении показываем сравнение «в копии N, было M».
COMPARE_SECTIONS = (
    "students",
    "subjects",
    "groups",
    "schedules",
    "attendance",
    "topics",
    "contests",
    "development",
)


def comparison_text(in_copy, before):
    """Строка «занятий: в копии N, было M» — общая для подтверждения и отчёта."""
    return "; ".join(
        _("%(label)s: в копии %(copy)s, было %(before)s")
        % {
            "label": COUNT_LABELS.get(key, key),
            "copy": int(in_copy.get(key) or 0),
            "before": int(before.get(key) or 0),
        }
        for key in COMPARE_SECTIONS
    )


def restore_comparison(counts):
    """Готовое сравнение из отчёта restore(): содержимое копии и база до замены."""
    report = counts.get("report") or {}
    return comparison_text(
        report.get("backup_sections") or {}, report.get("db_before") or {}
    )


class BackupIndexView(SuperadminRequiredMixin, TemplateView):
    template_name = "journal/backup/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Только счётчики: десять дешёвых COUNT вместо полной сборки копии.
        # Сама копия собирается при скачивании (BackupDownloadView).
        context["counts"] = {
            "academic_years": AcademicYear.objects.count(),
            "terms": Term.objects.count(),
            "branches": Branch.objects.count(),
            "rooms": Room.objects.count(),
            "users": User.objects.count(),
            "students": Student.objects.count(),
            "subjects": Subject.objects.count(),
            "groups": Group.objects.count(),
            "schedules": Schedule.objects.count(),
            "attendance": Attendance.objects.count(),
        }
        context["form"] = RestoreBackupForm()
        return context


class BackupDownloadView(SuperadminRequiredMixin, View):
    """Скачивание копии. Можно зашифровать её парольной фразой."""

    def get(self, request):
        return self._download(request, request.GET.get("passphrase", ""))

    def post(self, request):
        return self._download(request, request.POST.get("passphrase", ""))

    def _download(self, request, passphrase):
        payload = backup_tools.dumps(backup_tools.build_backup()).encode("utf-8")
        stamp = timezone.localtime().strftime("%Y%m%d_%H%M")
        encrypted = bool(passphrase)
        if encrypted:
            payload = crypto.encrypt_bytes(payload, passphrase)
            filename = f"school_journal_backup_{stamp}.json.enc"
            content_type = "application/octet-stream"
        else:
            filename = f"school_journal_backup_{stamp}.json"
            content_type = "application/json; charset=utf-8"
        security_tools.record_event(
            VisitLog.Kind.EVENT,
            request=request,
            user=request.user,
            path=request.path,
            detail=_("скачана резервная копия")
            + (" " + _("(зашифрованная)") if encrypted else ""),
        )
        notifications.notify(
            "backup",
            _("Скачана резервная копия"),
            f"Пользователь: {request.user.full_name} ({request.user.username})\n"
            f"Шифрование: {'да' if encrypted else 'нет'}\n"
            f"IP-адрес: {security_tools.get_client_ip(request)}",
        )
        response = HttpResponse(payload, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        messages.success(
            request,
            _("Резервная копия скачана")
            + (
                _(" и зашифрована парольной фразой.")
                if encrypted
                else _(".")
            )
            + (
                _(" Сохраните фразу: без неё копию не восстановить.")
                if encrypted
                else ""
            ),
        )
        return response


class BackupRestoreView(SuperadminRequiredMixin, View):
    template_name = "journal/backup/index.html"

    def post(self, request):
        form = RestoreBackupForm(request.POST, request.FILES)
        if not form.is_valid():
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{error}")
            return redirect("backup")

        uploaded = form.cleaned_data["file"]
        raw = uploaded.read()
        passphrase = form.cleaned_data.get("passphrase") or ""
        if crypto.is_encrypted_blob(raw):
            try:
                raw = crypto.decrypt_bytes(raw, passphrase)
            except ValueError as error:
                messages.error(
                    request,
                    _("Зашифрованная копия не открыта: %(error)s") % {"error": error},
                )
                return redirect("backup")
        elif passphrase:
            messages.warning(
                request,
                _("Копия не зашифрована — парольная фраза не потребовалась."),
            )
        try:
            data = backup_tools.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            messages.error(
                request,
                _("Не удалось прочитать копию: %(error)s") % {"error": error},
            )
            return redirect("backup")

        mode = form.cleaned_data["mode"]
        # Что именно заменится или добавится: сравнение копии с текущей базой
        # показываем до восстановления — это подтверждение для администратора.
        copy_sections = backup_tools.section_counts(data)
        db_now = backup_tools.db_totals()
        messages.info(
            request,
            _("К восстановлению (%(mode)s). Всего записей в копии: %(copy)s, "
              "в базе сейчас: %(now)s. Сравнение по разделам — %(comparison)s.")
            % {
                "mode": _("полная замена")
                if mode == "replace"
                else _("добавить и обновить"),
                "copy": sum(int(value or 0) for value in copy_sections.values()),
                "now": sum(int(value or 0) for value in db_now.values()),
                "comparison": comparison_text(copy_sections, db_now),
            },
        )

        try:
            counts = backup_tools.restore(
                data, mode=form.cleaned_data["mode"], actor=request.user
            )
        except Exception as error:  # noqa: BLE001 - показываем причину администратору
            messages.error(
                request,
                _("Восстановление не выполнено: %(error)s") % {"error": error},
            )
            return redirect("backup")

        summary = ", ".join(
            f"{COUNT_LABELS.get(key, key)}: {value}"
            for key, value in counts.items()
            # report — вложенный словарь-сравнение, его показываем отдельно ниже.
            if value and key not in ("superadmin_skipped", "report")
        )
        superadmin_skipped = counts.get("superadmin_skipped", 0)
        security_tools.record_event(
            VisitLog.Kind.EVENT,
            request=request,
            user=request.user,
            path=request.path,
            detail=(
                _("восстановление из копии (%(mode)s)")
                % {"mode": form.cleaned_data["mode"]}
                + (
                    " " + _("— часть с главным администратором пропущена")
                    if superadmin_skipped
                    else ""
                )
            ),
        )
        notifications.notify(
            "backup",
            _("Восстановление из резервной копии"),
            f"Пользователь: {request.user.full_name} ({request.user.username})\n"
            f"Режим: {form.cleaned_data['mode']}\n"
            f"IP-адрес: {security_tools.get_client_ip(request)}\n"
            f"Восстановлено — {summary or 'в копии не было данных'}\n"
            f"Сравнение с базой до восстановления — {restore_comparison(counts)}"
            + (
                "\nВнимание: в копии был главный администратор — эта часть пропущена."
                if superadmin_skipped
                else ""
            ),
        )
        messages.success(
            request,
            _("Восстановление завершено — %(summary)s.")
            % {"summary": summary or _("в копии не было данных")},
        )
        # Сравнение с тем, что было в базе до восстановления: администратор видит,
        # что именно заменила «полная замена» (см. journal/backup.py, report).
        report = counts.get("report") or {}
        comparison = restore_comparison(counts)
        if comparison:
            in_copy_total = int(report.get("backup_records") or 0)
            before_total = sum(int(value or 0) for value in (report.get("db_before") or {}).values())
            if form.cleaned_data["mode"] == "replace":
                prefix = _("Полная замена: данные журнала удалены и загружены из копии.")
            else:
                prefix = _("Из копии добавлены и обновлены записи.")
            messages.info(
                request,
                _("%(prefix)s Всего записей в копии: %(copy)s, в базе было: "
                  "%(before)s. Сравнение по разделам — %(comparison)s.")
                % {
                    "prefix": prefix,
                    "copy": in_copy_total,
                    "before": before_total,
                    "comparison": comparison,
                },
            )
        if superadmin_skipped:
            # Роль главного администратора из копии восстанавливает только он сам.
            messages.warning(request, backup_tools.SUPERADMIN_SKIP_MESSAGE)
        return redirect("backup")


class StaffMemberUpdateView(AdminRequiredMixin, UpdateView):
    """Карточка сотрудника УМО: роль, выключение учётной записи и сброс пароля."""

    model = User
    form_class = StaffMemberEditForm
    template_name = "journal/staff/form.html"
    context_object_name = "member"

    def get_queryset(self):
        return User.objects.filter(
            role__in=[User.Role.SUPERADMIN, User.Role.ADMIN, User.Role.METHODIST]
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["viewer"] = self.request.user
        return kwargs

    def dispatch(self, request, *args, **kwargs):
        # Данные главного администратора правит только он сам.
        target = self.get_object()
        if target.is_superadmin and not getattr(request.user, "is_superadmin", False):
            messages.error(request, _("Править главного администратора может только он сам."))
            return redirect("staff")
        return super().dispatch(request, *args, **kwargs)

    def get_context(self, password_form=None):
        return {
            "title": _("Сотрудник · %(name)s") % {"name": self.object.full_name},
            "password_form": password_form or StyledSetPasswordForm(user=self.object),
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_context(context.get("password_form")))
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.POST.get("form") == "password":
            password_form = StyledSetPasswordForm(user=self.object, data=request.POST)
            if password_form.is_valid():
                password_form.save()
                remember_password(
                    request, self.object, password_form.cleaned_data["new_password1"]
                )
                notify_account_change(
                    request,
                    _("Сменён пароль сотрудника"),
                    f"Сотруднику {self.object.full_name} ({self.object.username}) "
                    "задан новый пароль.",
                )
                messages.success(
                    request,
                    _("Пароль сотрудника %(name)s изменён.")
                    % {"name": self.object.full_name},
                )
                return redirect("staff")
            messages.error(request, _("Пароль не изменён: исправьте ошибки в форме."))
            context = self.get_context_data()
            context["password_form"] = password_form
            return self.render_to_response(context)
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        messages.success(
            self.request,
            _("Данные сотрудника «%(name)s» сохранены.")
            % {"name": self.object.full_name},
        )
        return reverse("staff")

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.cleaned_data.get("new_password"):
            security_tools.record_event(
                VisitLog.Kind.EVENT,
                request=self.request,
                user=self.request.user,
                path=self.request.path,
                detail=_("сброшен пароль сотруднику %(username)s")
                % {"username": self.object.username},
            )
        return response


class CustomizationView(AdminRequiredMixin, UpdateView):
    """Кастомизация журнала: название, логотип, фавикон. Только главный администратор."""

    form_class = SiteSettingsForm
    template_name = "journal/customization/form.html"
    success_url = reverse_lazy("customization")

    def dispatch(self, request, *args, **kwargs):
        # Раздел доступен только главному администратору.
        if not getattr(request.user, "is_superadmin", False):
            messages.error(request, _("Кастомизация доступна только главному администратору."))
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return SiteSettings.load()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Кастомизация журнала")
        # Предпросмотр показывает стандартный знак и фавикон, только если файлы
        # действительно есть в статике. На собранной статике файл мог не
        # собраться: тогда честнее показать текстовую заглушку, чем битую картинку.
        context["default_logo_available"] = bool(
            finders.find("journal/logo-square.png")
        )
        context["default_favicon_available"] = bool(
            finders.find("journal/favicon.png")
        )
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Настройки журнала сохранены."))
        return response
