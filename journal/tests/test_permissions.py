"""Автотесты прав доступа: роли, разделы и изоляция преподавателей.

Тесты работают только на ``django.test.TestCase`` и штатном тестовом клиенте —
новых зависимостей нет. Учётные записи создаются прямо здесь, вход выполняется
через ``force_login``.

Важная особенность журнала: пока в базе нет ни одного активного администратора,
``journal.middleware.FirstRunSetupMiddleware`` уводит ЛЮБУЮ страницу на ``/setup/``.
Поэтому в каждом тесте сначала создаётся действующий администратор, иначе
проверять доступ было бы не к чему.
"""
from datetime import date, time

from django.test import TestCase
from django.urls import reverse

from journal.models import (
    AcademicYear,
    Attendance,
    Branch,
    Group,
    Schedule,
    Student,
    Subject,
    Term,
    User,
)

#: Пароль тестовых учётных записей. Требования валидаторов не проверяются,
#: но пароль держим осмысленным: вход идёт через ``force_login``.
PASSWORD = "TestPass-2024-for-tests"

#: Страницы, закрытые для преподавателя (только администратор или методист).
STAFF_ONLY_PATHS = ("/students/", "/staff/", "/backup/", "/security/", "/customization/")

#: Страницы, закрытые даже для обычного администратора.
SUPERADMIN_ONLY_PATHS = ("/customization/",)

#: Коды, которыми представления отказывают в доступе: ``RoleRequiredMixin``
#: возвращает 302 на домашнюю страницу, остальные — 403/404.
DENIED_STATUSES = (302, 403, 404)


def make_user(username, role, is_superuser=False):
    """Создать пользователя нужной роли.

    ``first_name`` и ``last_name`` обязательны, логин — только латиницей.
    Первая учётная запись создаётся как активный администратор: без него
    журнал считает себя ненастроенным и уводит все страницы на ``/setup/``.
    """
    return User.objects.create_user(
        username=username,
        password=PASSWORD,
        first_name="Тест",
        last_name=username.title(),
        role=role,
        is_active=True,
        is_superuser=is_superuser,
    )


def make_admin():
    """Действующий администратор журнала — «журнал уже настроен»."""
    return make_user("boss-admin", User.Role.ADMIN)


def make_schedule(teacher, day_of_week=0):
    """Минимальное занятие: учебный год, полугодие, адрес, программа, группа."""
    year = AcademicYear.objects.create(
        start_date=date(2024, 9, 1), end_date=date(2025, 6, 30)
    )
    term = Term.objects.create(
        year=year, number=1, start_date=date(2024, 9, 1), end_date=date(2024, 12, 31)
    )
    branch = Branch.objects.create(name="Основной адрес")
    student = Student.objects.create(
        last_name="Учеников", first_name="Ученик", birth_date=date(2010, 5, 5)
    )
    subject = Subject.objects.create(name="Программа", branch=branch, term=term)
    group = Group.objects.create(name="Группа 1", subject=subject, term=term)
    group.students.add(student)
    schedule = Schedule.objects.create(
        group=group,
        branch=branch,
        day_of_week=day_of_week,
        start_time=time(10, 0),
        end_time=time(11, 30),
        teacher=teacher,
    )
    return schedule, student


class AnonymousAccessTests(TestCase):
    """Не вошедший посетитель не попадает в журнал."""

    def test_anonymous_dashboard_redirects_to_login(self):
        """Аноним на /dashboard/ получает редирект на страницу входа."""
        make_admin()
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class FirstRunSetupRedirectTests(TestCase):
    """Пока в журнале нет активного администратора, доступ проверять негде."""

    def test_setup_blocks_empty_journal(self):
        """На пустой базе даже /dashboard/ уводит на /setup/."""
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("setup"), response["Location"])

    def test_setup_redirect_stops_after_admin_created(self):
        """Появился активный администратор — /dashboard/ ведёт на вход, а не на /setup/."""
        make_admin()
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class StaffSectionAccessTests(TestCase):
    """Матрица разделов: преподаватель, методист, администратор, главный администратор."""

    def setUp(self):
        make_admin()
        self.staff_paths = STAFF_ONLY_PATHS

    def test_teacher_allowed_sections(self):
        """Преподаватель открывает свой кабинет /teacher/ и страницу занятий /teacher/week/."""
        make_user("teacher-two", User.Role.TEACHER)
        self.client.force_login(User.objects.get(username="teacher-two"))
        for path in ("/teacher/", "/teacher/week/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_methodist_denied_admin_sections(self):
        """Методист НЕ открывает /customization/ — раздел главного администратора."""
        make_user("methodist-two", User.Role.METHODIST)
        self.client.force_login(User.objects.get(username="methodist-two"))
        response = self.client.get("/customization/")
        self.assertIn(response.status_code, DENIED_STATUSES)

    def test_teacher_denied_staff_sections(self):
        """Преподаватель НЕ открывает /students/, /staff/, /backup/, /security/, /customization/."""
        make_user("teacher-one", User.Role.TEACHER)
        self.client.force_login(User.objects.get(username="teacher-one"))
        for path in self.staff_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(response.status_code, DENIED_STATUSES)

    def test_methodist_allowed_and_denied_sections(self):
        """Методист открывает /students/, /schedule/, /periods/, но НЕ /staff/, /backup/, /security/, /customization/."""
        make_user("methodist-one", User.Role.METHODIST)
        self.client.force_login(User.objects.get(username="methodist-one"))
        allowed = ("/students/", "/schedule/", "/periods/")
        denied = ("/staff/", "/backup/", "/security/", "/customization/")
        with self.subTest(role="methodist", kind="allowed"):
            self.assertEqual(
                [path for path in allowed if self.client.get(path).status_code != 200],
                [],
            )
        with self.subTest(role="methodist", kind="denied"):
            self.assertEqual(
                [
                    path
                    for path in denied
                    if self.client.get(path).status_code not in DENIED_STATUSES
                ],
                [],
            )

    def test_admin_allowed_and_denied_sections(self):
        """Администратор открывает /staff/, /backup/, /security/, но НЕ /customization/."""
        make_user("admin-two", User.Role.ADMIN)
        self.client.force_login(User.objects.get(username="admin-two"))
        allowed = ("/staff/", "/backup/", "/security/")
        denied = SUPERADMIN_ONLY_PATHS
        with self.subTest(role="admin", kind="allowed"):
            self.assertEqual(
                [path for path in allowed if self.client.get(path).status_code != 200],
                [],
            )
        with self.subTest(role="admin", kind="denied"):
            self.assertEqual(
                [
                    path
                    for path in denied
                    if self.client.get(path).status_code not in DENIED_STATUSES
                ],
                [],
            )

    def test_superadmin_allowed_customization(self):
        """Главный администратор открывает /customization/."""
        make_user("root-admin", User.Role.SUPERADMIN, is_superuser=True)
        self.client.force_login(User.objects.get(username="root-admin"))
        response = self.client.get("/customization/")
        self.assertEqual(response.status_code, 200)


class TeacherIsolationTests(TestCase):
    """Преподаватель видит и отмечает только свои занятия."""

    def setUp(self):
        make_admin()
        self.teacher = make_user("teacher-own", User.Role.TEACHER)
        self.other = make_user("teacher-other", User.Role.TEACHER)
        # Занятие принадлежит другому преподавателю.
        self.foreign_schedule, self.student = make_schedule(self.other)
        self.client.force_login(self.teacher)

    def test_foreign_lesson_page_is_404(self):
        """Страница отметок чужого занятия отдаёт 404."""
        response = self.client.get(
            reverse("attendance", args=[self.foreign_schedule.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_foreign_lesson_mark_is_not_saved(self):
        """Отметка на чужое занятие не создаёт запись в базе."""
        self.client.raise_request_exception = False
        response = self.client.post(
            reverse("attendance", args=[self.foreign_schedule.pk]),
            {f"status_{self.student.pk}": Attendance.Status.PRESENT},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            Attendance.objects.filter(schedule=self.foreign_schedule).count(), 0
        )
