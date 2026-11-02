"""График входов на сводке: столбики должны быть видны и пропорциональны числам.

Высота столбика приходит из шаблона в процентах (``style="height: 21.3%"``).
Раньше значение подставлялось без ``unlocalize``: при русской локали Django
писал «21,3», запятая делала объявление CSS невалидным, и все столбики
схлопывались в полоску ``min-height: 2px``. Тесты фиксируют точку в числе,
высоту контейнера, роли, которым блок положен, и пустой период.
"""
import re
from datetime import datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from journal.models import AcademicYear, Term, User, VisitLog

PASSWORD = "TestPass-2024-for-tests"


def make_user(username, role, superuser=False):
    return User.objects.create_user(
        username=username,
        password=PASSWORD,
        first_name="Тест",
        last_name=username.title(),
        role=role,
        is_active=True,
        is_staff=superuser,
        is_superuser=superuser,
    )


def make_logins(day, count):
    """Столько входов с датой ``day`` (created_at — auto_now_add)."""
    ids = [
        VisitLog.objects.create(kind=VisitLog.Kind.LOGIN, username="tester").pk
        for _ in range(count)
    ]
    VisitLog.objects.filter(id__in=ids).update(
        created_at=timezone.make_aware(datetime.combine(day, time(12, 0)))
    )


class DashboardChartTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        monday = self.today - timedelta(days=self.today.weekday())
        year = AcademicYear.objects.create(
            start_date=monday - timedelta(days=60), end_date=monday + timedelta(days=60)
        )
        Term.objects.create(
            year=year,
            number=1,
            start_date=monday - timedelta(days=60),
            end_date=monday + timedelta(days=60),
        )
        self.boss = make_user("chart-superadmin", User.Role.SUPERADMIN, superuser=True)
        self.admin = make_user("chart-admin", User.Role.ADMIN)
        self.methodist = make_user("chart-methodist", User.Role.METHODIST)
        # Ряд 0 / 0 / 13 / 0 / 61: максимум 61 — это 100 %, 13 — 21.3 %.
        make_logins(self.today - timedelta(days=3), 13)
        make_logins(self.today - timedelta(days=1), 61)

    def chart_styles(self, html):
        """(высота, подпись) всех столбиков в порядке вывода."""
        return [
            (match.group(1), match.group(2))
            for match in re.finditer(
                r'style="height: ([^";]+)[^"]*"[^>]*title="([^"]+)"', html
            )
        ]

    def test_chart_heights_are_valid_css_for_admin_roles(self):
        """Гл. администратор и администратор видят 14 столбиков; методист — нет."""
        for user, expects_chart in (
            (self.boss, True),
            (self.admin, True),
            (self.methodist, False),
        ):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.get("/dashboard/")
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                styles = self.chart_styles(html)
                if not expects_chart:
                    # Блок статистики — только администраторам, по замыслу.
                    self.assertEqual(styles, [])
                    self.assertNotIn('class="dsh-chart', html)
                    continue
                self.assertEqual(len(styles), 14)
                heights = [height for height, _title in styles]
                self.assertIn("100.0%", heights)  # 61 вход
                self.assertIn("21.3%", heights)  # 13 входов
                self.assertIn("0.0%", heights)
                # Запятая (русская локаль) делала CSS невалидным.
                self.assertNotIn(",", "".join(heights))
                # Контейнер с определённой высотой: без неё процент не считается.
                self.assertIn('class="dsh-chart h-32 w-full"', html)

    def test_empty_journal_renders_without_chart(self):
        """Нет входов — нет столбиков, но страница не падает."""
        VisitLog.objects.all().delete()
        self.client.force_login(self.admin)
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('class="dsh-chart', html)
        self.assertIn("входов не было", html)
