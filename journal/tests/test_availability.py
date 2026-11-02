"""Автотесты занятости: разовое занятие видят все проверки конфликтов и панель.

Баг заказчика: занятие, поставленное на конкретную дату (разовое), не мешало
новому занятию на то же время. Проверки без конкретной даты смотрели только на
обычные занятия (``lesson_date__isnull=True``) и разовых не видели. Обычное
занятие повторяется каждую неделю, поэтому в дату разового они совпадают: это
настоящий конфликт, и форма обязана его показать.

Второй баг: в панели «Свободное время преподавателя» у разового занятия не было
видно его даты — по занятому окну нельзя было понять, к какому дню оно относится.
Теперь подпись та же, что в сообщениях о конфликте: «(разовое · 13.01.2025)».

Разовые занятия проверяются через боевой путь — POST на ``/schedule/add/``
(``ScheduleCreateView`` + ``ScheduleForm``), как в ``test_absences.py``, откуда
взяты помощники ``make_user``/``make_admin``. Панель проверяется тем же запросом,
который делает страница (``journal/static/journal/schedule-form.js``, функция
``query``): ``/schedule/add/?group=..&teacher=..&day=..&lesson_date=..``.
Данные детерминированные: полугодие 2024/2025, 13 января 2025 — понедельник.

Важная особенность журнала: пока в базе нет ни одного активного администратора,
``journal.middleware.FirstRunSetupMiddleware`` уводит ЛЮБУЮ страницу на ``/setup/``.
Поэтому администратор создаётся в каждом наборе данных.
"""
import re
from datetime import date, time
from urllib.parse import urlencode

from django.test import TestCase
from django.urls import reverse

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

#: Пароль тестовых учётных записей (вход идёт через ``force_login``).
PASSWORD = "TestPass-2024-for-tests"

#: Границы тестового учебного года и полугодия.
TERM_START = date(2024, 9, 1)
TERM_END = date(2025, 6, 30)

#: Понедельник с разовым занятием и пятница той же недели (другой день недели).
MONDAY = date(2025, 1, 13)
FRIDAY = date(2025, 1, 17)

#: Подписи занятых окон в панели «Свободное время преподавателя».
TEACHER_BUSY = re.compile(
    r'<span class="rounded-full bg-slate-100[^"]*">\s*(.*?)\s*</span>', re.S
)
TAGS = re.compile(r"<[^>]+>")


def teacher_panel_badges(response):
    """Подписи занятых окон из панели «Свободное время преподавателя».

    Из страницы берём только блок этой панели: в блоке кабинетов разметка другая,
    и его подписи сюда не попадают.
    """
    html = response.content.decode("utf-8")
    panel = html.split("Свободное время преподавателя", 1)[-1]
    panel = panel.split("Занятость кабинетов", 1)[0]
    return [
        " ".join(TAGS.sub("", item).split()) for item in TEACHER_BUSY.findall(panel)
    ]


def make_user(username, role, is_superuser=False):
    """Создать пользователя нужной роли (первый администратор настраивает журнал)."""
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


class AvailabilityTestCase(TestCase):
    """Общий набор данных: разовое занятие по понедельникам 10:00–11:30.

    Разовое занятие стоит у первой группы в первом кабинете. Второй
    преподаватель ведёт обе программы и свободен в это время — им проверяем
    конфликты кабинета и группы отдельно от занятости первого.
    """

    def setUp(self):
        make_admin()
        self.teacher = make_user("teacher-oneoff", User.Role.TEACHER)
        self.other_teacher = make_user("teacher-second", User.Role.TEACHER)
        self.year = AcademicYear.objects.create(
            start_date=TERM_START, end_date=TERM_END
        )
        self.term = Term.objects.create(
            year=self.year, number=1, start_date=TERM_START, end_date=TERM_END
        )
        self.branch = Branch.objects.create(name="Основной адрес")
        self.room = Room.objects.create(branch=self.branch, name="Кабинет 1")
        self.other_room = Room.objects.create(branch=self.branch, name="Кабинет 2")
        self.free_room = Room.objects.create(branch=self.branch, name="Кабинет 3")
        self.subject = Subject.objects.create(
            name="Программа", branch=self.branch, term=self.term, teacher=self.teacher
        )
        # Второй преподаватель тоже ведёт программу: иначе форма отклонила бы
        # занятие по другой причине («не ведёт программу»).
        self.subject.teachers.add(self.other_teacher)
        self.group = Group.objects.create(
            name="Группа 1", subject=self.subject, term=self.term
        )
        self.second_subject = Subject.objects.create(
            name="Вторая программа",
            branch=self.branch,
            term=self.term,
            teacher=self.teacher,
        )
        self.second_subject.teachers.add(self.other_teacher)
        self.second_group = Group.objects.create(
            name="Группа 2", subject=self.second_subject, term=self.term
        )
        self.third_subject = Subject.objects.create(
            name="Третья программа",
            branch=self.branch,
            term=self.term,
            teacher=self.teacher,
        )
        self.third_subject.teachers.add(self.other_teacher)
        self.third_group = Group.objects.create(
            name="Группа 3", subject=self.third_subject, term=self.term
        )
        # Разовое занятие: понедельник 13.01.2025, 10:00–11:30, кабинет 1.
        self.one_off = Schedule.objects.create(
            group=self.group,
            branch=self.branch,
            room=self.room,
            lesson_date=MONDAY,
            start_time=time(10, 0),
            end_time=time(11, 30),
            teacher=self.teacher,
        )

    def login(self, user):
        """Войти и включить тестовое полугодие и адрес."""
        self.client.force_login(user)
        session = self.client.session
        session[SESSION_YEAR] = self.year.pk
        session[SESSION_TERM] = self.term.pk
        session[SESSION_BRANCH] = self.branch.pk
        session.save()

    def make_absence(self, date_from=MONDAY, date_to=MONDAY, teacher=None):
        return TeacherAbsence.objects.create(
            teacher=teacher or self.teacher,
            date_from=date_from,
            date_to=date_to,
            kind=TeacherAbsence.Kind.SICK,
        )

    def one_off_form(self, lesson_date=MONDAY, group=None, room=None, teacher=None,
                     **extra):
        """Данные формы РАЗОВОГО занятия (дата, без дня недели)."""
        data = {
            "group": (group or self.second_group).pk,
            "teacher": (teacher or self.teacher).pk,
            "room": (room or self.other_room).pk,
            "day_of_week": "",
            "lesson_date": lesson_date.isoformat(),
            "start_time": "10:00",
            "end_time": "11:30",
        }
        data.update(extra)
        return data

    def weekly_form(self, day_of_week=Schedule.Weekday.MONDAY, group=None, room=None,
                    teacher=None, **extra):
        """Данные формы ОБЫЧНОГО занятия (день недели, без даты)."""
        data = {
            "group": (group or self.second_group).pk,
            "teacher": (teacher or self.teacher).pk,
            "room": (room or self.other_room).pk,
            "day_of_week": str(day_of_week),
            "start_time": "10:00",
            "end_time": "11:30",
        }
        data.update(extra)
        return data

    def assert_rejected(self, response, *markers):
        """Форма вернулась с ошибкой и занятие не сохранилось."""
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Schedule.objects.count(), 1)
        for marker in markers:
            self.assertContains(response, marker, status_code=200, html=False)

    def assert_created(self, response, **lookup):
        """Занятие сохранено (форма ушла на список расписания)."""
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Schedule.objects.filter(**lookup).exists())

    def panel_url(self, **params):
        """Адрес панели — тот же запрос, что делает ``schedule-form.js``."""
        query = {"group": self.second_group.pk, "teacher": self.teacher.pk}
        query.update(
            {
                key: value
                for key, value in params.items()
                if value not in (None, "")
            }
        )
        return f"{reverse('schedule_add')}?{urlencode(query)}"


class OneOffConflictTests(AvailabilityTestCase):
    """Разовое занятие мешает новому занятию на то же время."""

    def setUp(self):
        super().setUp()
        self.login(make_user("methodist-plan", User.Role.METHODIST))

    def test_one_off_blocks_second_one_off_for_teacher(self):
        """Второе разовое на ту же дату и время у того же преподавателя — отказ.

        Первая группа и кабинет 2 второму занятию не мешают: конфликт именно
        по преподавателю, у которого в это время уже стоит разовое занятие.
        """
        response = self.client.post(
            reverse("schedule_add"), self.one_off_form()
        )
        self.assert_rejected(response, "уже занятие")
        self.assertFalse(
            Schedule.objects.filter(
                group=self.second_group, lesson_date=MONDAY
            ).exists()
        )

    def test_one_off_blocks_room(self):
        """Разовое занятие занимает кабинет: и для разового, и для обычного.

        Второй преподаватель свободен, поэтому отказ возможен только из-за кабинета.
        Обычное занятие проверяем следом: кабинет занят разовым в тот же день
        недели, значит обычное занятие совпало бы с ним в дату разового.
        """
        one_off = self.client.post(
            reverse("schedule_add"),
            self.one_off_form(room=self.room, teacher=self.other_teacher),
        )
        self.assert_rejected(one_off, "занят")

        weekly = self.client.post(
            reverse("schedule_add"),
            self.weekly_form(room=self.room, teacher=self.other_teacher),
        )
        self.assert_rejected(weekly, "занят")

    def test_one_off_blocks_group(self):
        """Разовое занятие занимает группу: и для разового, и для обычного."""
        one_off = self.client.post(
            reverse("schedule_add"),
            self.one_off_form(group=self.group, room=self.free_room,
                              teacher=self.other_teacher),
        )
        self.assert_rejected(one_off, "У группы в это время уже есть занятие")

        weekly = self.client.post(
            reverse("schedule_add"),
            self.weekly_form(group=self.group, room=self.free_room,
                             teacher=self.other_teacher),
        )
        self.assert_rejected(weekly, "У группы в это время уже есть занятие")

    def test_weekly_lesson_conflicts_with_one_off_on_same_weekday(self):
        """Обычное занятие в день недели разового — конфликт, в другой день — нет.

        Это и есть исправленный баг: обычное занятие повторяется каждую неделю,
        поэтому в дату разового занятия оно проходит в то же время. Отказ связан
        именно с разовым занятием: перенеся его на другой день недели, то же
        занятие сохраняется.
        """
        monday = self.client.post(
            reverse("schedule_add"), self.weekly_form()
        )
        self.assert_rejected(monday, "уже занятие")

        tuesday = self.client.post(
            reverse("schedule_add"),
            self.weekly_form(day_of_week=Schedule.Weekday.TUESDAY),
        )
        self.assert_created(
            tuesday,
            group=self.second_group,
            day_of_week=Schedule.Weekday.TUESDAY,
            lesson_date__isnull=True,
        )

        # Разовое занятие переехало на пятницу: понедельник снова свободен.
        self.one_off.lesson_date = FRIDAY
        self.one_off.save()
        moved = self.client.post(
            reverse("schedule_add"), self.weekly_form()
        )
        self.assert_created(
            moved,
            group=self.second_group,
            room=self.other_room,
            day_of_week=Schedule.Weekday.MONDAY,
            lesson_date__isnull=True,
        )

    def test_absence_on_date_waives_teacher_conflict(self):
        """Отсутствие преподавателя на дату конфликт не создаёт.

        В это время его занятие отменено, поэтому подмену другого педагога
        поставить можно: кабинет и группа свободны. Обычное занятие в тот же
        день недели тоже проходит: его разовое занятие отменено и времени
        не занимает.
        """
        self.make_absence(MONDAY, MONDAY)
        response = self.client.post(
            reverse("schedule_add"),
            self.one_off_form(group=self.third_group),
        )
        self.assert_created(
            response, group=self.third_group, lesson_date=MONDAY
        )

        weekly = self.client.post(
            reverse("schedule_add"),
            self.weekly_form(room=self.free_room),
        )
        self.assert_created(
            weekly,
            group=self.second_group,
            room=self.free_room,
            day_of_week=Schedule.Weekday.MONDAY,
            lesson_date__isnull=True,
        )

    def test_ignore_teacher_conflict_allows_saving(self):
        """Галочка «Не учитывать занятость преподавателя» снимает конфликт.

        Кабинет и группа при этом свободны — иначе занятие не сохранилось бы:
        галочка снимает проверку только по времени преподавателя.
        """
        response = self.client.post(
            reverse("schedule_add"),
            self.one_off_form(ignore_teacher_conflict="on"),
        )
        self.assert_created(
            response, group=self.second_group, lesson_date=MONDAY
        )


class AvailabilityPanelTests(AvailabilityTestCase):
    """Панель «Свободное время преподавателя»: разовое занятие и его дата."""

    def setUp(self):
        super().setUp()
        self.login(make_user("methodist-panel", User.Role.METHODIST))

    def test_panel_with_date_shows_one_off_and_its_date(self):
        """Запрос с датой разового: время занято и видно, что занятие разовое.

        Запрос тот же, что делает страница: дата разового уходит в ``lesson_date``,
        день недели — в ``day``. Время 10:00–11:30 не должно предлагаться как
        свободное окно, а в подписи занятого окна должна стоять дата разового.
        """
        response = self.client.get(
            self.panel_url(
                day=Schedule.Weekday.MONDAY, lesson_date=MONDAY.isoformat()
            )
        )
        self.assertEqual(response.status_code, 200)
        badges = teacher_panel_badges(response)
        self.assertEqual(len(badges), 1)
        self.assertIn("10:00–11:30", badges[0])
        self.assertIn("(разовое · 13.01.2025)", badges[0])
        # Занятое время не предложено как свободное: окна 08:00–10:00 и 11:30–21:00.
        self.assertNotContains(response, 'data-window-start="10:00"', html=False)
        self.assertContains(response, 'data-window-start="11:30"', html=False)

        # Другая дата: в этот день у преподавателя занятий нет — панель свободна,
        # и дата разового занятия в подписях не появляется.
        free = self.client.get(
            self.panel_url(
                day=Schedule.Weekday.MONDAY, lesson_date=FRIDAY.isoformat()
            )
        )
        self.assertEqual(teacher_panel_badges(free), [])
        self.assertNotContains(free, "разовое · 13.01.2025", html=False)
        self.assertContains(free, 'data-window-start="08:00"', html=False)

    def test_panel_without_date_shows_weekday_busy_as_before(self):
        """Без даты панель, как раньше, считает по дню недели.

        Разовое занятие этого дня видно вместе со своей датой, а обычное недельное
        занятие — с прежней подписью: времени и места достаточно, признака
        «разовое» у него нет.
        """
        Schedule.objects.create(
            group=self.group,
            branch=self.branch,
            room=self.room,
            day_of_week=Schedule.Weekday.MONDAY,
            start_time=time(12, 0),
            end_time=time(13, 30),
            teacher=self.teacher,
        )
        response = self.client.get(self.panel_url(day=Schedule.Weekday.MONDAY))
        badges = teacher_panel_badges(response)
        self.assertEqual(len(badges), 2)
        one_off = next(item for item in badges if "10:00–11:30" in item)
        regular = next(item for item in badges if "12:00–13:30" in item)
        self.assertIn("(разовое · 13.01.2025)", one_off)
        self.assertNotIn("разовое", regular)
        # Оба занятия занимают своё время: как свободные они не предлагаются.
        self.assertNotContains(response, 'data-window-start="10:00"', html=False)
        self.assertNotContains(response, 'data-window-start="12:00"', html=False)
