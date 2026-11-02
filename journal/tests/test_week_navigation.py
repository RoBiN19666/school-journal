"""Автотесты листания недель: за границы полугодия уйти нельзя.

«Посещаемость» (``/weeks/``) и «Мои занятия по неделям»
(``/teacher/week/``) показывают неделю внутри активного полугодия. Неделя из
адреса вне границ приводится к ближайшей допустимой (404 не отдаётся), а на
границах кнопки «Предыдущая»/«Следующая» показываются неактивными — без ссылки.

Данные детерминированные: полугодие 01.09.2024–30.06.2025; первая допустимая
неделя — 26.08.2024 (в неё входит 1 сентября, воскресенье), последняя —
30.06.2025 (в неё входит 30 июня, понедельник).
"""
from datetime import date
from types import SimpleNamespace

from django.test import TestCase
from django.urls import reverse

from journal.models import AcademicYear, Branch, Term, User
from journal.periods import SESSION_BRANCH, SESSION_TERM, SESSION_YEAR
from journal.views import clamp_week, term_week_limits, week_navigation

PASSWORD = "TestPass-2024-for-tests"

TERM_START = date(2024, 9, 1)
TERM_END = date(2025, 6, 30)
FIRST_MONDAY = date(2024, 8, 26)
LAST_MONDAY = date(2025, 6, 30)
MIDDLE_MONDAY = date(2024, 12, 9)


def make_user(username, role):
    return User.objects.create_user(
        username=username,
        password=PASSWORD,
        first_name="Тест",
        last_name=username.title(),
        role=role,
        is_active=True,
    )


class WeekNavigationTestCase(TestCase):
    """Полугодие с датами и учётные записи методиста и преподавателя."""

    def setUp(self):
        make_user("boss-admin", User.Role.ADMIN)
        self.methodist = make_user("methodist-weeks", User.Role.METHODIST)
        self.teacher = make_user("teacher-weeks", User.Role.TEACHER)
        self.year = AcademicYear.objects.create(
            start_date=TERM_START, end_date=TERM_END
        )
        self.term = Term.objects.create(
            year=self.year, number=1, start_date=TERM_START, end_date=TERM_END
        )
        self.branch = Branch.objects.create(name="Основной адрес")

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session[SESSION_YEAR] = self.year.pk
        session[SESSION_TERM] = self.term.pk
        session[SESSION_BRANCH] = self.branch.pk
        session.save()

    def navigation(self, response):
        """Фрагмент HTML с кнопками листания недель — без остальной страницы.

        Всю страницу целиком проверять нельзя: запрошенный адрес попадает в
        скрытые поля переключателя языка, поэтому ищем ссылки только в блоке
        кнопок недель.
        """
        body = response.content.decode()
        start = body.find("Предыдущая неделя")
        end = body.find("Выгрузить", start)
        return body[max(start - 400, 0) : end if end != -1 else start + 800]

    def check_bounds(self, url):
        """Общие проверки обеих страниц недель: прижимание и кнопки-границы."""
        self.login(self.methodist)

        # Неделя до начала полугодия — показывается первая допустимая.
        before = self.client.get(url, {"week": "2024-07-01"})
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.context["monday"], FIRST_MONDAY)
        self.assertFalse(before.context["prev_allowed"])
        self.assertTrue(before.context["next_allowed"])
        nav = self.navigation(before)
        self.assertIn("26.08.2024", before.content.decode())
        self.assertIn("cursor-not-allowed", nav)
        self.assertIn("первая неделя полугодия", nav)
        self.assertNotIn("?week=2024-07-01", nav)

        # Неделя после окончания — показывается последняя допустимая.
        after = self.client.get(url, {"week": "2025-09-01"})
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.context["monday"], LAST_MONDAY)
        self.assertTrue(after.context["prev_allowed"])
        self.assertFalse(after.context["next_allowed"])
        nav = self.navigation(after)
        self.assertIn("30.06.2025", after.content.decode())
        self.assertIn("cursor-not-allowed", nav)
        self.assertNotIn("?week=2025-07-07", nav)

        # Середина полугодия: листать можно в обе стороны.
        middle = self.client.get(url, {"week": MIDDLE_MONDAY.isoformat()})
        self.assertEqual(middle.status_code, 200)
        self.assertEqual(middle.context["monday"], MIDDLE_MONDAY)
        self.assertTrue(middle.context["prev_allowed"])
        self.assertTrue(middle.context["next_allowed"])
        nav = self.navigation(middle)
        self.assertIn("?week=2024-12-02", nav)
        self.assertIn("?week=2024-12-16", nav)
        self.assertNotIn("cursor-not-allowed", nav)

        # «Текущая неделя» тоже внутри полугодия.
        first, last = term_week_limits(self.term)
        this_week = date.fromisoformat(middle.context["this_week"])
        self.assertLessEqual(first, this_week)
        self.assertLessEqual(this_week, last)

    def test_methodist_week_is_limited_to_term(self):
        self.check_bounds(reverse("week_journal"))

    def test_teacher_week_is_limited_to_term(self):
        self.login(self.teacher)
        url = reverse("teacher_week")
        after = self.client.get(url, {"week": "2025-09-01"})
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.context["monday"], LAST_MONDAY)
        self.assertFalse(after.context["next_allowed"])
        self.assertNotIn("?week=2025-07-07", self.navigation(after))
        self.assertIn("cursor-not-allowed", self.navigation(after))

        before = self.client.get(url, {"week": "2024-07-01"})
        self.assertEqual(before.context["monday"], FIRST_MONDAY)
        self.assertFalse(before.context["prev_allowed"])
        self.assertNotIn("?week=2024-08-19", self.navigation(before))
        self.assertIn("cursor-not-allowed", self.navigation(before))

        middle = self.client.get(url, {"week": MIDDLE_MONDAY.isoformat()})
        self.assertTrue(middle.context["prev_allowed"])
        self.assertTrue(middle.context["next_allowed"])
        self.assertIn("?week=2024-12-02", self.navigation(middle))
        self.assertNotIn("cursor-not-allowed", self.navigation(middle))

    def test_week_limits_without_term_dates(self):
        """Без дат у полугодия ограничения нет: страница не падает."""
        empty = SimpleNamespace(start_date=None, end_date=None)
        self.assertEqual(term_week_limits(empty), (None, None))
        self.assertEqual(clamp_week(MIDDLE_MONDAY, empty), (MIDDLE_MONDAY, False))
        navigation = week_navigation(empty, MIDDLE_MONDAY)
        self.assertTrue(navigation["prev_allowed"])
        self.assertTrue(navigation["next_allowed"])

        # Заполнено только начало: ограничение только снизу.
        only_start = SimpleNamespace(start_date=TERM_START, end_date=None)
        self.assertEqual(term_week_limits(only_start), (FIRST_MONDAY, None))
        self.assertEqual(
            clamp_week(date(2024, 7, 1), only_start), (FIRST_MONDAY, True)
        )
        self.assertTrue(week_navigation(only_start, LAST_MONDAY)["next_allowed"])
