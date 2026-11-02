"""Автотесты расписания: показывается одна неделя (по умолчанию текущая).

Проверяют, что разовые занятия и пометки об отсутствии относятся именно к
показанной неделе — прошлой, текущей или будущей: на прошлой неделе видно её
собственные подмены и больничный (методист смотрит назад, что было), на текущей —
только текущее (плюс предупреждение о ближайшем будущем периоде), а листание
ограничено полугодием и сохраняет вид (сетка/список).

Дата не подменяется: данные строятся относительно `timezone.localdate()`, поэтому
тесты одинаково проходят в любой день.
"""
import re
from datetime import date, time, timedelta
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils import translation
from openpyxl import load_workbook

from journal.models import (
    AcademicYear,
    Branch,
    Group,
    Room,
    Schedule,
    Subject,
    TeacherAbsence,
    Term,
    User,
)
from journal.periods import SESSION_BRANCH, SESSION_TERM, SESSION_YEAR

PASSWORD = "TestPass-2024-for-tests"


def make_user(username, role):
    return User.objects.create_user(
        username=username,
        password=PASSWORD,
        first_name="Тест",
        last_name=username.title(),
        role=role,
        is_active=True,
    )


class ScheduleWeekTestCase(TestCase):
    """Полугодие вокруг сегодняшнего дня, три периода отсутствия и три разовых.

    Текущая неделя — та, в которую попадает сегодня; прошлая и следующая — соседи.
    У отсутствующего преподавателя по периоду на каждую из недель, у второго —
    прошедший больничный (нужен для проверки колонки в выгрузке).
    """

    def setUp(self):
        make_user("boss-admin", User.Role.ADMIN)
        self.methodist = make_user("methodist-schedule", User.Role.METHODIST)
        self.absent = make_user("teacher-absent-week", User.Role.TEACHER)
        self.past_teacher = make_user("teacher-past-week", User.Role.TEACHER)

        self.today = timezone.localdate()
        self.monday = self.today - timedelta(days=self.today.weekday())
        self.sunday = self.monday + timedelta(days=6)
        self.prev_monday = self.monday - timedelta(days=7)
        self.next_monday = self.monday + timedelta(days=7)

        self.year = AcademicYear.objects.create(
            start_date=self.monday - timedelta(days=60),
            end_date=self.monday + timedelta(days=60),
        )
        self.term = Term.objects.create(
            year=self.year,
            number=1,
            start_date=self.monday - timedelta(days=60),
            end_date=self.monday + timedelta(days=60),
        )
        self.branch = Branch.objects.create(name="Основной адрес")
        self.room = Room.objects.create(branch=self.branch, name="Кабинет 1")

        self.subject = Subject.objects.create(
            name="Программа", branch=self.branch, term=self.term, teacher=self.absent
        )
        self.group = Group.objects.create(
            name="Группа 1", subject=self.subject, term=self.term
        )
        self.weekly = Schedule.objects.create(
            group=self.group,
            branch=self.branch,
            room=self.room,
            day_of_week=Schedule.Weekday.MONDAY,
            start_time=time(10, 0),
            end_time=time(11, 30),
            teacher=self.absent,
        )
        # Разовое занятие в каждой из трёх недель: время разное, чтобы не мешать
        # друг другу по ограничению (группа, дата, начало).
        self.prev_one_off = self.make_one_off(self.prev_monday, time(12, 0))
        self.one_off = self.make_one_off(self.monday, time(13, 0))
        self.next_one_off = self.make_one_off(self.next_monday, time(14, 0))

        # Отсутствия: прошедшее, текущее и будущее — по неделе каждое.
        self.prev_absence = self.make_absence(
            self.prev_monday, self.prev_monday + timedelta(days=6), TeacherAbsence.Kind.SICK
        )
        self.absence = self.make_absence(
            self.monday, self.sunday, TeacherAbsence.Kind.TRIP
        )
        self.next_absence = self.make_absence(
            self.next_monday,
            self.next_monday + timedelta(days=6),
            TeacherAbsence.Kind.OTHER,
        )

        # Второй преподаватель с прошедшим больничным: для выгрузки расписания.
        self.second_subject = Subject.objects.create(
            name="Вторая программа",
            branch=self.branch,
            term=self.term,
            teacher=self.past_teacher,
        )
        self.second_group = Group.objects.create(
            name="Группа 2", subject=self.second_subject, term=self.term
        )
        self.second_weekly = Schedule.objects.create(
            group=self.second_group,
            branch=self.branch,
            room=self.room,
            day_of_week=Schedule.Weekday.TUESDAY,
            start_time=time(10, 0),
            end_time=time(11, 30),
            teacher=self.past_teacher,
        )
        self.past_absence = self.make_absence(
            self.prev_monday,
            self.prev_monday + timedelta(days=6),
            TeacherAbsence.Kind.SICK,
            teacher=self.past_teacher,
        )

    def make_one_off(self, day, start):
        return Schedule.objects.create(
            group=self.group,
            branch=self.branch,
            room=self.room,
            day_of_week=day.weekday(),
            lesson_date=day,
            start_time=start,
            end_time=time(start.hour + 1, start.minute),
            teacher=self.absent,
            is_substitution=True,
        )

    def make_absence(self, date_from, date_to, kind, teacher=None):
        return TeacherAbsence.objects.create(
            teacher=teacher or self.absent,
            date_from=date_from,
            date_to=date_to,
            kind=kind,
        )

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session[SESSION_YEAR] = self.year.pk
        session[SESSION_TERM] = self.term.pk
        session[SESSION_BRANCH] = self.branch.pk
        session.save()

    def week(self, monday=None, **params):
        params.setdefault("week", (monday or self.monday).isoformat())
        return self.client.get(reverse("schedule"), params)

    def listed(self, response):
        """Занятия страницы (и в сетке, и в списке — один и тот же набор)."""
        return [
            lesson
            for day in response.context["day_list"]
            for lesson in day["lessons"]
        ]

    def one_offs(self, response):
        return {item.pk for item in self.listed(response) if item.is_one_off}

    def label_for(self, response, schedule):
        for item in self.listed(response):
            if item.pk == schedule.pk:
                return item.absence
        return None


class ScheduleSelectedWeekTests(ScheduleWeekTestCase):
    """Разовые занятия и пометки об отсутствии — по показанной неделе."""

    def test_one_offs_belong_to_following_selected_week(self):
        """На каждой неделе — только её разовые занятия."""
        self.login(self.methodist)
        for monday, expected in (
            (self.prev_monday, self.prev_one_off),
            (self.monday, self.one_off),
            (self.next_monday, self.next_one_off),
        ):
            with self.subTest(week=monday):
                response = self.week(monday)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.one_offs(response), {expected.pk})

    def test_absence_label_follows_selected_week(self):
        """Пометка об отсутствии — период показанной недели (и прошлой тоже)."""
        self.login(self.methodist)
        for monday, expected in (
            (self.prev_monday, self.prev_absence),
            (self.monday, self.absence),
            (self.next_monday, self.next_absence),
        ):
            with self.subTest(week=monday):
                response = self.week(monday)
                label = self.label_for(response, self.weekly)
                self.assertIsNotNone(label)
                self.assertEqual(label.pk, expected.pk)

    def test_current_week_hides_other_weeks(self):
        """На текущей неделе нет прошлого больничного и чужого разового."""
        self.login(self.methodist)
        response = self.week()
        body = response.content.decode()
        self.assertIn(f"{self.absence.date_from:%d.%m.%Y}", body)
        self.assertNotIn(f"{self.prev_absence.date_from:%d.%m.%Y}", body)
        self.assertNotIn(self.prev_one_off.date_label, body)
        self.assertNotIn(self.next_one_off.date_label, body)
        self.assertNotIn(f"{self.next_absence.date_from:%d.%m.%Y}", body)
        # Будущий период на текущей неделе показывается как предупреждение,
        # но только когда текущего нет: у нас он есть, поэтому подписи нет.

    def test_excel_absence_column_follows_selected_week(self):
        """В выгрузке расписания — отсутствия текущей недели, прошлые скрыты."""
        self.login(self.methodist)
        self.prev_absence.delete()
        self.next_absence.delete()
        expected = "отсутствует: Командировка с %s по %s" % (
            self.absence.date_from.strftime("%d.%m.%Y"),
            self.absence.date_to.strftime("%d.%m.%Y"),
        )
        with translation.override("ru"):
            response = self.client.get(reverse("export_schedule"))
            book = load_workbook(BytesIO(response.content), read_only=True)
            try:
                rows = [list(row) for row in book["Расписание"].iter_rows(values_only=True)]
            finally:
                book.close()
        header = next(row for row in rows if row[0] == "День недели")
        body = rows[rows.index(header) + 1 :]
        current_row = next(row for row in body if row[8] == self.absent.full_name)
        past_row = next(row for row in body if row[8] == self.past_teacher.full_name)
        self.assertEqual(current_row[9], expected)
        self.assertFalse(past_row[9])

    def test_future_absence_warns_on_current_week(self):
        """Период, который начнётся позже, виден на текущей неделе как предупреждение."""
        self.login(self.methodist)
        self.absence.delete()
        response = self.week()
        self.assertEqual(self.label_for(response, self.weekly).pk, self.next_absence.pk)
        # На прошлой неделе такого предупреждения нет: там показывается её период.
        previous = self.week(self.prev_monday)
        self.assertEqual(
            self.label_for(previous, self.weekly).pk, self.prev_absence.pk
        )


class ScheduleWeekNavigationTests(ScheduleWeekTestCase):
    """Листание недель в расписании: границы полугодия и сохранение вида."""

    def test_week_navigation_is_clamped_to_term(self):
        self.login(self.methodist)
        term_start = self.term.start_date
        term_end = self.term.end_date
        first_monday = term_start - timedelta(days=term_start.weekday())
        last_monday = term_end - timedelta(days=term_end.weekday())

        early = self.week(date(2000, 1, 3))
        self.assertEqual(early.status_code, 200)
        self.assertEqual(early.context["monday"], first_monday)
        self.assertFalse(early.context["prev_allowed"])
        self.assertTrue(early.context["next_allowed"])
        self.assertContains(early, "cursor-not-allowed")

        late = self.week(date(2100, 1, 4))
        self.assertEqual(late.status_code, 200)
        self.assertEqual(late.context["monday"], last_monday)
        self.assertFalse(late.context["next_allowed"])
        self.assertTrue(late.context["prev_allowed"])

    def test_week_navigation_keeps_view_and_filter(self):
        self.login(self.methodist)
        response = self.week(view="list")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Ссылки листания несут текущий вид, а переключатель вида — неделю.
        self.assertIn(
            f"?week={(self.monday - timedelta(days=7)).isoformat()}&amp;view=list", body
        )
        self.assertIn(f"?week={self.monday.isoformat()}&amp;view=grid", body)
        grid = self.week(view="grid", subject=self.subject.pk)
        self.assertIn(
            f"?week={self.monday.isoformat()}&amp;subject={self.subject.pk}&amp;view=grid",
            grid.content.decode(),
        )

    def test_week_label_follows_navigation_buttons(self):
        """Подпись о неделе — справа от кнопок листания, в обоих видах."""
        self.login(self.methodist)
        label = f"{self.monday:%d.%m.%Y} — {self.sunday:%d.%m.%Y}"
        for view in ("grid", "list"):
            with self.subTest(view=view):
                body = self.week(view=view).content.decode()
                # Кнопки слева, подпись справа: в разметке подпись идёт после кнопок.
                self.assertIn("Предыдущая неделя", body)
                self.assertIn("Следующая неделя", body)
                self.assertIn("Показана неделя:", body)
                self.assertIn(label, body)
                self.assertGreater(
                    body.index("Показана неделя:"), body.index("Следующая неделя")
                )
                # Кнопки — отдельная группа, подпись — второй элемент той же строки
                # с переносом на узком экране (w-full + flex-wrap). sm:flex-1 обязателен:
                # с одним w-auto длинное пояснение занимает всю ширину (max-content) и
                # вытесняет подпись под кнопки даже на широком экране.
                self.assertIn(
                    "flex flex-wrap items-center justify-between", body
                )
                self.assertIn(
                    "w-full min-w-0 max-w-full text-left sm:w-auto sm:flex-1 "
                    "sm:min-w-[16rem] sm:text-right",
                    body,
                )
                # Сама неделя крупнее пояснения под ней.
                self.assertIn(
                    "text-base font-semibold leading-tight text-slate-900 sm:text-lg", body
                )
                self.assertIn("text-xs leading-snug text-slate-400", body)

