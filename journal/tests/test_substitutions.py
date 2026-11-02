"""Автотесты волны 4: подмены сразу на весь период отсутствия.

Проверяют страницу ``/absences/<pk>/substitutions/``: сколько строк она
показывает на период в две недели (обычное занятие — по каждому вхождению дня
недели, плюс разовое), что создание даёт ровно выбранные разовые занятия, что
повторный запуск не плодит дубли, что конфликтующая строка помечена и не
создаётся без явного разрешения, что снятая галочка «ставить подмену» пропускает
строку, а недельный журнал рядом с отменённым занятием пишет, кто его проведёт.

Волна 5 — карточки недели и отметки на подмене: у отменённого занятия с уже
стоящей подменой кнопки «Поставить подмену» нет (только короткая подпись, кто
проведёт), карточка самой подмены отвечает за своего преподавателя (заменяющего)
и подписывает, для кого она и на какой период, дубль на то же место не создаётся
ни кнопкой, ни формой, а отметки на подмене доступны сотруднику УМО и самому
заменяющему.

Данные детерминированные: полугодие 2024/2025, 13 января 2025 — понедельник,
период отсутствия 13.01–26.01 (две недели). Администратор создаётся всегда:
``journal.middleware.FirstRunSetupMiddleware`` уводит любую страницу на ``/setup/``,
пока в базе нет ни одного активного администратора.
"""
import re
from datetime import date, time, timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from journal.forms import ScheduleForm
from journal.models import (
    AcademicYear,
    Attendance,
    Branch,
    Group,
    Room,
    Schedule,
    Student,
    Subject,
    TeacherAbsence,
    Term,
    User,
)
from journal.periods import SESSION_BRANCH, SESSION_TERM, SESSION_YEAR
from journal.substitutions import absence_occurrences, row_key
from journal.views import substitution_url

PASSWORD = "TestPass-2024-for-tests"

TERM_START = date(2024, 9, 1)
TERM_END = date(2025, 6, 30)

#: Понедельники тестового периода (13.01 и 20.01) и разовое занятие в среду.
MONDAY = date(2025, 1, 13)
NEXT_MONDAY = date(2025, 1, 20)
WEDNESDAY = date(2025, 1, 15)
PERIOD_END = date(2025, 1, 26)

DENIED_STATUSES = (302, 403, 404)

#: Разметка карточки занятия в недельном журнале: по ней карточки и делим.
CARD_RE = re.compile(
    r'<div class="rounded-xl border border-slate-200 p-4">(.*?)\n                    </div>',
    re.S,
)

#: Метка строки таблицы в разделе «Подмены»: по ней считаем строки страницы.
LIST_ROW_MARK = "hover:bg-indigo-50/40"


def make_user(username, role, is_superuser=False):
    """Пользователь нужной роли (первый администратор настраивает журнал)."""
    return User.objects.create_user(
        username=username,
        password=PASSWORD,
        first_name="Тест",
        last_name=username.title(),
        role=role,
        is_active=True,
        is_superuser=is_superuser,
    )


class SubstitutionTestCase(TestCase):
    """Общий набор данных: две группы отсутствующего и занятый час у третьего.

    У отсутствующего преподавателя обычное занятие по понедельникам 10:00–11:30 и
    разовое в среду 15.01 12:00–13:30. Занятый преподаватель ведёт свою группу в
    понедельник ровно в то же время, но в другом кабинете: он годится в
    заменяющие на среду и конфликтует по занятости в понедельник.
    """

    def setUp(self):
        make_user("boss-admin", User.Role.ADMIN)
        self.teacher = make_user("teacher-period", User.Role.TEACHER)
        self.replacement = make_user("teacher-replacement", User.Role.TEACHER)
        self.busy = make_user("teacher-busy", User.Role.TEACHER)
        self.year = AcademicYear.objects.create(
            start_date=TERM_START, end_date=TERM_END
        )
        self.term = Term.objects.create(
            year=self.year, number=1, start_date=TERM_START, end_date=TERM_END
        )
        self.branch = Branch.objects.create(name="Основной адрес")
        self.room = Room.objects.create(branch=self.branch, name="Кабинет 1")
        self.other_room = Room.objects.create(branch=self.branch, name="Кабинет 2")
        self.third_room = Room.objects.create(branch=self.branch, name="Кабинет 3")

        self.subject = Subject.objects.create(
            name="Программа", branch=self.branch, term=self.term, teacher=self.teacher
        )
        self.group = Group.objects.create(
            name="Группа 1", subject=self.subject, term=self.term
        )
        self.student = Student.objects.create(
            last_name="Учеников", first_name="Ученик", birth_date=date(2010, 5, 5)
        )
        self.group.students.add(self.student)
        self.schedule = Schedule.objects.create(
            group=self.group,
            branch=self.branch,
            room=self.room,
            day_of_week=Schedule.Weekday.MONDAY,
            start_time=time(10, 0),
            end_time=time(11, 30),
            teacher=self.teacher,
        )

        self.second_subject = Subject.objects.create(
            name="Вторая программа",
            branch=self.branch,
            term=self.term,
            teacher=self.teacher,
        )
        self.second_group = Group.objects.create(
            name="Группа 2", subject=self.second_subject, term=self.term
        )
        self.one_off = Schedule.objects.create(
            group=self.second_group,
            branch=self.branch,
            room=self.other_room,
            day_of_week=Schedule.Weekday.WEDNESDAY,
            lesson_date=WEDNESDAY,
            start_time=time(12, 0),
            end_time=time(13, 30),
            teacher=self.teacher,
        )

        self.third_subject = Subject.objects.create(
            name="Третья программа",
            branch=self.branch,
            term=self.term,
            teacher=self.busy,
        )
        self.third_group = Group.objects.create(
            name="Группа 3", subject=self.third_subject, term=self.term
        )
        Schedule.objects.create(
            group=self.third_group,
            branch=self.branch,
            room=self.third_room,
            day_of_week=Schedule.Weekday.MONDAY,
            start_time=time(10, 0),
            end_time=time(11, 30),
            teacher=self.busy,
        )

        self.absence = TeacherAbsence.objects.create(
            teacher=self.teacher,
            date_from=MONDAY,
            date_to=PERIOD_END,
            kind=TeacherAbsence.Kind.SICK,
        )

        # Преподаватели программ: заменяющий и «занятый» закреплены за программами
        # (иначе занятие вести нельзя), а `foreign` не закреплён нигде — им и
        # проверяется правило «занятие ведёт только преподаватель программы».
        self.subject.teachers.add(self.replacement, self.busy)
        self.second_subject.teachers.add(self.replacement, self.busy)
        self.foreign = make_user("teacher-foreign", User.Role.TEACHER)

    def login(self, user):
        """Вход и активные полугодие, год и адрес в сессии."""
        self.client.force_login(user)
        session = self.client.session
        session[SESSION_YEAR] = self.year.pk
        session[SESSION_TERM] = self.term.pk
        session[SESSION_BRANCH] = self.branch.pk
        session.save()

    def url(self):
        return reverse("absence_substitutions", args=[self.absence.pk])

    def keys(self):
        """Ключи строк плана по датам: {дата: ключ строки формы}."""
        return {
            day: row_key(schedule, day)
            for schedule, day in absence_occurrences(self.absence)
        }

    def form_data(self, master=None, apply_dates=None, ignore_dates=(), action="save"):
        """Поля формы подмен: общий заменяющий, галочки «ставить» и «не учитывать».

        ``apply_dates`` — даты строк с галочкой «ставить подмену» (``None`` — все
        строки), ``ignore_dates`` — даты строк с галочкой «не учитывать занятость».
        """
        data = {"action": action}
        if master is not None:
            data["master_teacher"] = master.pk
        for day, key in self.keys().items():
            if apply_dates is None or day in apply_dates:
                data[f"apply_{key}"] = "on"
            if master is not None:
                data[f"teacher_{key}"] = master.pk
            if day in ignore_dates:
                data[f"ignore_{key}"] = "on"
        return data


class SubstitutionPlanTests(SubstitutionTestCase):
    """Страница плана: строки периода, доступ и разметка."""

    def test_page_lists_every_occurrence_of_period(self):
        """Две недели дают две строки обычного занятия и одну строку разового.

        Занятие уже отменено отсутствием — оно всё равно в списке: именно его и
        заменяют. Преподавателю страница закрыта, как и остальные действия
        раздела «Отсутствия».
        """
        self.login(make_user("methodist-plan", User.Role.METHODIST))
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        self.assertEqual(
            [row["date"] for row in rows], [MONDAY, WEDNESDAY, NEXT_MONDAY]
        )
        self.assertEqual(rows[0]["weekday"], "Понедельник")
        self.assertEqual(rows[0]["time"], "10:00–11:30")
        self.assertEqual(rows[0]["group"], "Группа 1")
        self.assertEqual(rows[0]["subject"], "Программа")
        self.assertEqual(rows[0]["room"], "Кабинет 1")
        self.assertTrue(rows[1]["one_off"])
        self.assertEqual(response.context["counts"]["create"], 0)
        self.assertEqual(response.context["counts"]["skipped"], 3)

        body = response.content.decode()
        self.assertIn("13.01.2025", body)
        self.assertIn("15.01.2025", body)
        self.assertIn("20.01.2025", body)
        self.assertIn("разовое", body)

        # Преподавателю раздел закрыт.
        self.login(self.teacher)
        denied = self.client.get(self.url())
        self.assertIn(denied.status_code, DENIED_STATUSES)


class SubstitutionCreateTests(SubstitutionTestCase):
    """Создание подмен, защита от дублей и поведение при конфликте."""

    def setUp(self):
        super().setUp()
        self.login(make_user("methodist-create", User.Role.METHODIST))

    def test_create_makes_one_off_lessons_for_whole_period(self):
        """Три строки периода дают три подмены: две новые и замену у разового.

        У разового занятия своя дата, поэтому преподаватель заменяется у самого
        занятия: второе разовое занятие на то же место база не примет
        (``unique_group_one_off_start``).
        """
        response = self.client.post(
            self.url(), self.form_data(master=self.replacement), follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Создано подмен: 3, пропущено: 0")

        created = list(
            Schedule.objects.filter(is_substitution=True).order_by("lesson_date")
        )
        self.assertEqual(
            [lesson.lesson_date for lesson in created],
            [MONDAY, WEDNESDAY, NEXT_MONDAY],
        )
        self.assertEqual(
            {lesson.teacher_id for lesson in created}, {self.replacement.pk}
        )
        self.assertFalse(any(lesson.ignore_teacher_conflict for lesson in created))

        for lesson in created:
            self.assertTrue(lesson.is_one_off)
            if lesson.lesson_date == WEDNESDAY:
                self.assertEqual(lesson.pk, self.one_off.pk)
                continue
            self.assertEqual(lesson.group_id, self.group.pk)
            self.assertEqual(lesson.start_time, time(10, 0))
            self.assertEqual(lesson.end_time, time(11, 30))
            self.assertEqual(lesson.room_id, self.room.pk)
            self.assertEqual(lesson.branch_id, self.branch.pk)
            self.assertEqual(lesson.day_of_week, Schedule.Weekday.MONDAY)

        # Прежнее обычное занятие не тронуто: оно по-прежнему за отсутствующим.
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.teacher_id, self.teacher.pk)
        self.assertFalse(self.schedule.is_substitution)

    def test_repeat_run_does_not_create_duplicates(self):
        """Повторный запуск сообщает «уже стоит» и не создаёт новых занятий."""
        self.client.post(self.url(), self.form_data(master=self.replacement))
        self.assertEqual(Schedule.objects.filter(is_substitution=True).count(), 3)

        response = self.client.post(
            self.url(), self.form_data(master=self.replacement), follow=True
        )
        self.assertContains(response, "Создано подмен: 0, пропущено: 2")
        self.assertContains(response, "Уже стояли подмены (2)")
        self.assertEqual(Schedule.objects.filter(is_substitution=True).count(), 3)
        self.assertEqual(
            Schedule.objects.filter(
                is_substitution=True, teacher=self.replacement
            ).count(),
            3,
        )

    def test_period_page_stores_replaced_teacher(self):
        """Страница периода пишет, кого заменяют: и обычному, и разовому занятию.

        У разового занятия преподаватель меняется прямо в записи, поэтому прежний
        сохраняется в поле ``replaced_teacher`` — раньше он терялся.
        """
        self.client.post(self.url(), self.form_data(master=self.replacement))
        created = Schedule.objects.filter(is_substitution=True).order_by("lesson_date")
        self.assertEqual(
            [lesson.replaced_teacher_id for lesson in created],
            [self.teacher.pk, self.teacher.pk, self.teacher.pk],
        )
        in_place = Schedule.objects.get(pk=self.one_off.pk)
        self.assertEqual(in_place.replaced_teacher_id, self.teacher.pk)
        self.assertEqual(in_place.teacher_id, self.replacement.pk)

    def test_conflicting_row_is_marked_and_not_created_without_allowance(self):
        """Строка с занятым заменяющим помечена и не сохраняется без разрешения.

        Занятый преподаватель ведёт свою группу в понедельник в то же время,
        поэтому две понедельничные строки — конфликт, а среда свободна. Пока у
        строки не отмечено «не учитывать занятость», подмена не создаётся.
        """
        page = self.client.get(self.url(), {"teacher": self.busy.pk})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["counts"]["conflicts"], 2)
        self.assertEqual(page.context["counts"]["create"], 1)
        self.assertContains(page, "конфликт")
        self.assertContains(page, "Занят:")

        keys = self.keys()
        # Строку разового занятия снимаем галочкой: проверяем именно конфликт.
        data = self.form_data(
            master=self.busy, apply_dates=(MONDAY, NEXT_MONDAY)
        )
        response = self.client.post(self.url(), data, follow=True)
        self.assertContains(response, "Пропущены из-за конфликтов (2)")
        self.assertEqual(Schedule.objects.filter(is_substitution=True).count(), 0)
        self.assertFalse(
            Schedule.objects.filter(
                group=self.group, lesson_date=MONDAY, is_substitution=True
            ).exists()
        )
        self.assertTrue(keys)  # ключи строк те же, что у формы

        # Осознанное решение: галочка «не учитывать занятость» именно у строк.
        allowed = self.form_data(
            master=self.busy,
            apply_dates=(MONDAY, NEXT_MONDAY),
            ignore_dates=(MONDAY, NEXT_MONDAY),
        )
        self.client.post(self.url(), allowed, follow=True)
        created = Schedule.objects.filter(
            group=self.group, is_substitution=True
        ).order_by("lesson_date")
        self.assertEqual(
            [lesson.lesson_date for lesson in created], [MONDAY, NEXT_MONDAY]
        )
        self.assertEqual(
            {lesson.teacher_id for lesson in created}, {self.busy.pk}
        )
        self.assertTrue(all(lesson.ignore_teacher_conflict for lesson in created))

    def test_unchecked_row_is_skipped(self):
        """Без галочки «ставить подмену» строка пропускается и не создаётся."""
        response = self.client.post(
            self.url(),
            self.form_data(master=self.replacement, apply_dates=()),
            follow=True,
        )
        self.assertContains(response, "Создано подмен: 0, пропущено: 3")
        self.assertEqual(Schedule.objects.filter(is_substitution=True).count(), 0)


class SubstitutionJournalTests(SubstitutionTestCase):
    """Недельный журнал показывает, кто проведёт отменённое занятие."""

    def setUp(self):
        super().setUp()
        self.login(make_user("methodist-week", User.Role.METHODIST))

    def test_week_journal_names_replacement_teacher(self):
        """Рядом с отменённым занятием видно заменяющего — одним запросом.

        Ищем подмену по группе, дате и времени начала, поэтому имя подставляется
        без запроса на каждое занятие недели.
        """
        self.client.post(self.url(), self.form_data(master=self.replacement))
        response = self.client.get(
            reverse("week_journal"), {"week": MONDAY.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"Занятие проведёт заменяющий: {self.replacement.full_name}.",
        )

        cancelled = [
            item
            for day in response.context["days"]
            for item in day["lessons"]
            if item["cancelled"] and item["schedule"].group_id == self.group.pk
        ]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(
            cancelled[0]["substitution_teacher"].pk, self.replacement.pk
        )

        # До подмены подписи о заменяющем нет.
        Schedule.objects.filter(is_substitution=True).delete()
        page = self.client.get(reverse("week_journal"), {"week": MONDAY.isoformat()})
        self.assertNotContains(page, "Занятие проведёт заменяющий")


class SubstitutionCardTests(SubstitutionTestCase):
    """Карточки недели: подпись подмены, отсутствие и кнопка «Поставить подмену»."""

    def setUp(self):
        super().setUp()
        self.login(make_user("methodist-cards", User.Role.METHODIST))

    def make_substitution(self, day=MONDAY, teacher=None):
        """Подмена на место обычного занятия группы 1 (10:00–11:30)."""
        return Schedule.objects.create(
            group=self.group,
            branch=self.branch,
            room=self.room,
            day_of_week=day.weekday(),
            lesson_date=day,
            start_time=time(10, 0),
            end_time=time(11, 30),
            teacher=teacher or self.replacement,
            is_substitution=True,
        )

    def week_page(self, week=MONDAY):
        return self.client.get(reverse("week_journal"), {"week": week.isoformat()})

    def cards(self, response):
        return CARD_RE.findall(response.content.decode())

    def original_card(self, response):
        """Карточка обычного (отменённого) занятия группы 1 — без метки «разовое»."""
        found = [
            card
            for card in self.cards(response)
            if self.teacher.full_name in card and "разовое ·" not in card
        ]
        self.assertEqual(len(found), 1, f"карточек исходного занятия: {len(found)}")
        return found[0]

    def substitution_card(self, response, day=MONDAY):
        """Карточка подмены на указанную дату — по метке разового занятия."""
        found = [
            card
            for card in self.cards(response)
            if f"разовое · {day:%d.%m.%Y}" in card
        ]
        self.assertEqual(len(found), 1, f"карточек подмены: {len(found)}")
        return found[0]

    def test_card_with_substitution_hides_button_and_names_replacement(self):
        """Где подмена уже стоит, кнопки нет — только подпись, кто проведёт."""
        # Пока подмены нет, исходное занятие предлагает её поставить.
        page = self.week_page()
        original = self.original_card(page)
        self.assertIn("Поставить подмену", original)
        self.assertIn("Занятие отменено: отметки и часы не учитываются.", original)

        self.make_substitution()
        page = self.week_page()
        original = self.original_card(page)
        self.assertNotIn("Поставить подмену", original)
        self.assertIn(
            f"Занятие проведёт заменяющий: {self.replacement.full_name}.", original
        )
        self.assertIn("Отметки и часы — за заменяющим преподавателем.", original)

        # У самой карточки подмены кнопки тоже нет: вторую база не примет.
        self.assertNotIn("Поставить подмену", self.substitution_card(page))

    def test_substitution_card_shows_replacement_for_and_own_absence(self):
        """Подмена подписана «для кого», чужих отсутствий на ней нет.

        У исходного преподавателя два периода отсутствия — подпись берёт тот,
        что накрывает дату занятия (январский, а не декабрьский).
        """
        # Второй период у заменяемого: проверяем выбор подходящего периода.
        TeacherAbsence.objects.create(
            teacher=self.teacher,
            date_from=date(2024, 12, 2),
            date_to=date(2024, 12, 6),
            kind=TeacherAbsence.Kind.TRIP,
        )
        # Заменяющий сам отсутствует на дату подмены.
        replacement_absence = TeacherAbsence.objects.create(
            teacher=self.replacement,
            date_from=MONDAY,
            date_to=date(2025, 1, 17),
            kind=TeacherAbsence.Kind.SICK,
        )
        self.make_substitution()
        page = self.week_page()

        substitution = self.substitution_card(page)
        self.assertIn(
            f"Подмена для {self.teacher.full_name} · Больничный с 13.01.2025 по 26.01.2025",
            substitution,
        )
        self.assertNotIn("02.12.2024", substitution)
        self.assertIn(
            f"Заменяющий тоже отсутствует: {replacement_absence.title}", substitution
        )
        # Ни чужого отсутствия строкой «Отсутствие:», ни своей подписи «Проведёт»,
        # ни пометки «занятие отменено» (она про отмену по СВОЕМУ преподавателю).
        self.assertNotIn("Отсутствие:", substitution)
        self.assertNotIn("Занятие проведёт заменяющий:", substitution)
        self.assertNotIn("занятие отменено", substitution)
        self.assertIn("заменяющий отсутствует", substitution)

        # Исходное занятие, наоборот, показывает своё отсутствие.
        original = self.original_card(page)
        self.assertIn("Отсутствие: Больничный с 13.01.2025 по 26.01.2025", original)

        # Заменяющий поправился: подмена становится обычным занятием с отметками.
        TeacherAbsence.objects.filter(pk=replacement_absence.pk).delete()
        page = self.week_page()
        substitution = self.substitution_card(page)
        self.assertNotIn("заменяющий отсутствует", substitution)
        self.assertNotIn("отменено:", substitution)
        self.assertIn("Отметить", substitution)

    def test_caption_uses_stored_replaced_teacher_when_place_changed(self):
        """Подпись берётся из поля, даже если исходное занятие перенесли.

        Раньше заменяемый искался только по совпадению группы, даты и времени:
        стоило перенести исходное занятие — подпись «Подмена для …» пропадала.
        """
        substitution = self.make_substitution()
        substitution.replaced_teacher = self.teacher
        substitution.save(update_fields=["replaced_teacher"])
        # Переносим исходное занятие на другой день и время: пары по месту нет.
        self.schedule.day_of_week = Schedule.Weekday.FRIDAY
        self.schedule.start_time = time(14, 0)
        self.schedule.end_time = time(15, 30)
        self.schedule.save()
        page = self.week_page()
        card = self.substitution_card(page)
        self.assertIn(f"Подмена для {self.teacher.full_name}", card)
        self.assertIn("Больничный с 13.01.2025 по 26.01.2025", card)

        # В списке «Подмены» подпись тоже на месте.
        self.login(make_user("methodist-field", User.Role.METHODIST))
        listed = self.client.get(reverse("substitution_list"))
        item = listed.context["substitutions"][0]
        self.assertEqual(item.replaced_person.pk, self.teacher.pk)
        self.assertContains(listed, "Больничный с 13.01.2025 по 26.01.2025")

    def test_caption_falls_back_to_place_for_old_records(self):
        """Старые подмены без поля по-прежнему подписываются поиском по месту."""
        substitution = self.make_substitution()
        self.assertIsNone(substitution.replaced_teacher_id)
        page = self.week_page()
        card = self.substitution_card(page)
        self.assertIn(f"Подмена для {self.teacher.full_name}", card)
        self.assertIn("Больничный с 13.01.2025 по 26.01.2025", card)

    def test_form_takes_replaced_teacher_from_link_and_ignores_garbage(self):
        """Форма подставляет заменяемого из ссылки; мусор и отключённый игнорирует."""
        self.login(make_user("methodist-link", User.Role.METHODIST))
        url = reverse("schedule_add")
        page = self.client.get(url, {"replaced_teacher": self.teacher.pk})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(
            page.context["form"].initial.get("replaced_teacher"), self.teacher.pk
        )
        # Ссылка «Поставить подмену» несёт того, кого заменяют.
        self.assertIn(
            f"replaced_teacher={self.teacher.pk}",
            substitution_url(self.schedule, MONDAY),
        )

        inactive = make_user("teacher-inactive", User.Role.TEACHER)
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        for value in ("abc", "999999", str(inactive.pk), "-1"):
            with self.subTest(value=value):
                broken = self.client.get(url, {"replaced_teacher": value})
                self.assertEqual(broken.status_code, 200)
                self.assertIsNone(
                    broken.context["form"].initial.get("replaced_teacher")
                )

    def test_form_rejects_substitution_for_absent_teacher(self):
        """Подмену нельзя поставить тому, кто сам отсутствует в этот день."""
        response = self.client.post(
            reverse("schedule_add"),
            {
                "group": self.group.pk,
                "teacher": self.teacher.pk,
                "room": self.room.pk,
                "lesson_date": MONDAY.isoformat(),
                "start_time": "10:00",
                "end_time": "11:30",
                "is_substitution": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "отсутствует 13.01.2025")
        self.assertContains(response, "Подмену проведёт другой преподаватель")
        self.assertFalse(
            Schedule.objects.filter(group=self.group, lesson_date=MONDAY).exists()
        )

    def test_form_rejects_duplicate_slot_without_crash(self):
        """Второе занятие на то же место отклоняет форма, а не ошибка базы."""
        self.make_substitution()
        response = self.client.post(
            reverse("schedule_add"),
            {
                "group": self.group.pk,
                "teacher": self.teacher.pk,
                "room": self.room.pk,
                "lesson_date": MONDAY.isoformat(),
                "start_time": "10:00",
                "end_time": "11:30",
                "is_substitution": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже есть занятие")
        self.assertContains(response, "(подмена)")
        self.assertEqual(
            Schedule.objects.filter(group=self.group, lesson_date=MONDAY).count(), 1
        )

    def test_database_constraint_is_reported_not_500(self):
        """Гонка: если форма пропустила дубль, база отказывает — страница объясняет.

        Обе проверки формы (``clean`` и ``_post_clean``, где Django проверяет
        ограничения модели) отключаем нарочно: так воспроизводится одновременное
        сохранение двух методистов, когда ограничение
        ``unique_group_one_off_start`` срабатывает уже при вставке.
        """
        self.make_substitution()
        data = {
            "group": self.group.pk,
            "teacher": self.teacher.pk,
            "room": self.room.pk,
            "day_of_week": MONDAY.weekday(),
            "lesson_date": MONDAY.isoformat(),
            "start_time": "10:00",
            "end_time": "11:30",
            "is_substitution": "on",
        }
        with mock.patch.object(
            ScheduleForm, "clean", lambda form: form.cleaned_data
        ), mock.patch.object(ScheduleForm, "_post_clean", lambda form: None):
            response = self.client.post(reverse("schedule_add"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже есть занятие")
        self.assertEqual(
            Schedule.objects.filter(group=self.group, lesson_date=MONDAY).count(), 1
        )


class SubstitutionAttendanceTests(SubstitutionTestCase):
    """Отметки на подмене: доступ у УМО и заменяющего, часы — заменяющему."""

    def setUp(self):
        super().setUp()
        self.methodist = make_user("methodist-marks", User.Role.METHODIST)

    def make_substitution(self, day=MONDAY, group=None):
        return Schedule.objects.create(
            group=group or self.group,
            branch=self.branch,
            room=self.room,
            day_of_week=day.weekday(),
            lesson_date=day,
            start_time=time(10, 0),
            end_time=time(11, 30),
            teacher=self.replacement,
            is_substitution=True,
        )

    def test_methodist_marks_attendance_on_substitution(self):
        """Сотрудник УМО отмечает подмену; исходное отменённое занятие — нет."""
        substitution = self.make_substitution()
        self.login(self.methodist)
        response = self.client.get(
            reverse("week_journal"), {"week": MONDAY.isoformat()}
        )
        card = [
            item
            for item in CARD_RE.findall(response.content.decode())
            if f"разовое · {MONDAY:%d.%m.%Y}" in item
        ][0]
        self.assertIn("Отметить", card)

        url = reverse("attendance", args=[substitution.pk])
        page = self.client.get(url, {"date": MONDAY.isoformat()})
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            url,
            {
                "date": MONDAY.isoformat(),
                f"status_{self.student.pk}": Attendance.Status.PRESENT,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Attendance.objects.filter(
                schedule=substitution, date=MONDAY, status=Attendance.Status.PRESENT
            ).count(),
            1,
        )

        # Исходное занятие отменено: отметка на него не сохраняется.
        response = self.client.post(
            reverse("attendance", args=[self.schedule.pk]),
            {
                "date": MONDAY.isoformat(),
                f"status_{self.student.pk}": Attendance.Status.PRESENT,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Attendance.objects.filter(schedule=self.schedule, date=MONDAY).count(), 0
        )

    def test_hours_go_to_replacement_not_to_absent_teacher(self):
        """Часы подмены считаются у заменяющего, у отсутствующего — «отменено».

        В наборе данных: занятие группы 1 (2 акад. ч.) у отсутствующего отменено,
        его разовое занятие в среду (2 ч.) тоже отменено, у занятого
        преподавателя своя пара (2 ч.). Подмена добавляет заменяющему 2 ч.
        """
        self.make_substitution()
        self.login(self.methodist)
        response = self.client.get(
            reverse("week_journal"), {"week": MONDAY.isoformat()}
        )
        self.assertEqual(str(response.context["hours_total"]), "4.00")
        self.assertEqual(str(response.context["cancelled_hours"]), "4.00")
        self.assertEqual(response.context["cancelled_lessons"], 2)

    def test_replacement_sees_lesson_and_marks_it_absent_teacher_does_not(self):
        """Заменяющий видит подмену с подписью и отмечает её; отсутствующий — нет."""
        # Неделя в журнале листается только внутри полугодия, поэтому подпись на
        # странице недели проверяем на подмене 13.01, а «Мои занятия» показывают
        # разовое занятие лишь в его неделю — для дашборда нужна текущая неделя.
        substitution = self.make_substitution(day=MONDAY)
        today = timezone.localdate()
        current_monday = today - timedelta(days=today.weekday())
        TeacherAbsence.objects.create(
            teacher=self.teacher,
            date_from=current_monday,
            date_to=current_monday + timedelta(days=6),
            kind=TeacherAbsence.Kind.TRIP,
        )
        self.make_substitution(day=current_monday)

        self.login(self.replacement)
        dashboard = self.client.get(reverse("teacher_dashboard"))
        week = self.client.get(reverse("teacher_week"), {"week": MONDAY.isoformat()})
        self.assertContains(
            dashboard,
            f"Подмена для {self.teacher.full_name} · Командировка с "
            f"{current_monday:%d.%m.%Y} по {current_monday + timedelta(days=6):%d.%m.%Y}",
        )
        self.assertContains(
            week,
            f"Подмена для {self.teacher.full_name} · Больничный с 13.01.2025",
        )
        self.assertContains(week, "Отметки и тема")

        url = reverse("attendance", args=[substitution.pk])
        self.assertEqual(
            self.client.get(url, {"date": MONDAY.isoformat()}).status_code, 200
        )
        response = self.client.post(
            url,
            {
                "date": MONDAY.isoformat(),
                f"status_{self.student.pk}": Attendance.Status.LATE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Attendance.objects.filter(
                schedule=substitution, date=MONDAY, status=Attendance.Status.LATE
            ).count(),
            1,
        )

        # У отсутствующего преподавателя этой подмены нет и открыть её нельзя.
        self.login(self.teacher)
        dashboard = self.client.get(reverse("teacher_dashboard"))
        week = self.client.get(reverse("teacher_week"), {"week": MONDAY.isoformat()})
        self.assertNotContains(dashboard, "Подмена для")
        self.assertNotContains(week, "Подмена для")
        self.assertEqual(
            self.client.get(url, {"date": MONDAY.isoformat()}).status_code, 404
        )


class SubstitutionListTests(SubstitutionTestCase):
    """Раздел «Подмены»: только подмены, фильтры, подпись «кого заменяют», доступ."""

    def setUp(self):
        super().setUp()
        self.methodist = make_user("methodist-list", User.Role.METHODIST)

    def make_substitution(
        self, day=MONDAY, group=None, teacher=None, start=time(10, 0)
    ):
        """Подмена: по умолчанию вместо обычного занятия группы 1."""
        return Schedule.objects.create(
            group=group or self.group,
            branch=self.branch,
            room=self.room,
            day_of_week=day.weekday(),
            lesson_date=day,
            start_time=start,
            end_time=time(11, 30),
            teacher=teacher or self.replacement,
            is_substitution=True,
        )

    def listed(self, response):
        """Сколько строк таблицы отдала страница."""
        return response.content.decode().count(LIST_ROW_MARK)

    def test_list_contains_only_substitutions(self):
        """В списке только подмены: обычные занятия не попадают."""
        substitution = self.make_substitution()
        self.login(self.methodist)
        response = self.client.get(reverse("substitution_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item.pk for item in response.context["substitutions"]],
            [substitution.pk],
        )
        self.assertEqual(response.context["total_count"], 1)
        self.assertEqual(self.listed(response), 1)
        self.assertContains(response, self.group.name)
        # Обычные занятия в базе есть, но в списке подмен их нет.
        self.assertEqual(Schedule.objects.filter(is_substitution=False).count(), 3)

    def test_list_filters_by_replacement_dates_and_group(self):
        """Фильтры: заменяющий, кого заменяют, период дат и группа."""
        self.make_substitution()
        self.make_substitution(
            day=WEDNESDAY,
            group=self.second_group,
            teacher=self.busy,
            start=time(14, 0),
        )
        self.login(self.methodist)
        url = reverse("substitution_list")
        self.assertEqual(self.listed(self.client.get(url)), 2)
        self.assertEqual(
            self.listed(self.client.get(url, {"teacher": self.replacement.pk})), 1
        )
        self.assertEqual(
            self.listed(self.client.get(url, {"teacher": self.busy.pk})), 1
        )
        self.assertEqual(
            self.listed(self.client.get(url, {"date_from": WEDNESDAY.isoformat()})), 1
        )
        self.assertEqual(
            self.listed(self.client.get(url, {"date_to": MONDAY.isoformat()})), 1
        )
        self.assertEqual(
            self.listed(
                self.client.get(
                    url,
                    {
                        "date_from": MONDAY.isoformat(),
                        "date_to": WEDNESDAY.isoformat(),
                    },
                )
            ),
            2,
        )
        self.assertEqual(
            self.listed(self.client.get(url, {"group": self.second_group.pk})), 1
        )
        # «Кого заменяют» — связь с исходным занятием: пара есть только у первой.
        self.assertEqual(
            self.listed(self.client.get(url, {"replaced": self.teacher.pk})), 1
        )
        empty = self.client.get(url, {"replaced": self.busy.pk})
        self.assertEqual(self.listed(empty), 0)
        self.assertContains(empty, "По выбранным условиям подмен нет")

    def test_list_shows_replaced_teacher_absence_and_warning(self):
        """У подмены видно, кого заменяют и по какому отсутствию; есть предупреждение."""
        self.make_substitution()
        self.login(self.methodist)
        url = reverse("substitution_list")
        response = self.client.get(url)
        item = response.context["substitutions"][0]
        self.assertEqual(item.replaced_person.pk, self.teacher.pk)
        self.assertEqual(
            item.replaced_caption, "Больничный с 13.01.2025 по 26.01.2025"
        )
        self.assertContains(response, "Больничный с 13.01.2025 по 26.01.2025")
        self.assertNotContains(response, "заменяющий тоже отсутствует")

        # Заменяющий сам отсутствует на дату подмены — предупреждение в строке.
        TeacherAbsence.objects.create(
            teacher=self.replacement,
            date_from=MONDAY,
            date_to=WEDNESDAY,
            kind=TeacherAbsence.Kind.TRIP,
        )
        response = self.client.get(url)
        self.assertContains(
            response, "заменяющий тоже отсутствует: Командировка с 13.01.2025 по 15.01.2025"
        )

        # Подмена, у которой пары нет: честно «—», а не выдуманное имя.
        self.make_substitution(
            day=WEDNESDAY,
            group=self.second_group,
            teacher=self.busy,
            start=time(14, 0),
        )
        response = self.client.get(url)
        self.assertContains(response, "кого заменяют, определить не удалось")

    def test_teacher_cannot_open_substitution_list(self):
        """Преподавателю раздел «Подмены» закрыт, как и остальные разделы УМО."""
        self.login(self.teacher)
        response = self.client.get(reverse("substitution_list"))
        self.assertIn(response.status_code, DENIED_STATUSES)

    def test_list_attendance_link_opens_lesson(self):
        """Ссылка «Отметки» ведёт на рабочую страницу отметок этого занятия."""
        substitution = self.make_substitution()
        self.login(self.methodist)
        href = f'{reverse("attendance", args=[substitution.pk])}?date={MONDAY.isoformat()}'
        response = self.client.get(reverse("substitution_list"))
        self.assertContains(response, href)
        self.assertContains(response, reverse("schedule_edit", args=[substitution.pk]))
        page = self.client.get(href)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.group.name)


class ProgramTeacherRuleTests(SubstitutionTestCase):
    """Занятие ведёт только преподаватель программы: основной или дополнительный."""

    def setUp(self):
        super().setUp()
        self.methodist = make_user("methodist-program", User.Role.METHODIST)
        self.login(self.methodist)

    def lesson_data(self, **overrides):
        """Данные формы нового обычного занятия группы 1 (свободное время)."""
        data = {
            "group": self.group.pk,
            "teacher": self.teacher.pk,
            "room": self.other_room.pk,
            "day_of_week": Schedule.Weekday.MONDAY,
            "start_time": "14:00",
            "end_time": "15:30",
        }
        data.update(overrides)
        return data

    def test_form_rejects_teacher_outside_program(self):
        """Преподаватель не из программы: понятная ошибка, записи нет."""
        response = self.client.post(
            reverse("schedule_add"), self.lesson_data(teacher=self.foreign.pk)
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "не ведёт программу")
        self.assertContains(response, "Преподаватели программы")
        self.assertFalse(
            Schedule.objects.filter(group=self.group, teacher=self.foreign).exists()
        )
        # И в списке поля этого преподавателя нет: там только преподаватели программы.
        options = response.context["form"].fields["teacher"].queryset
        self.assertNotIn(self.foreign, options)
        self.assertIn(self.replacement, options)

    def test_form_accepts_main_and_extra_program_teachers(self):
        """Основной и дополнительный преподаватели программы занятие ведут."""
        main = self.client.post(
            reverse("schedule_add"),
            self.lesson_data(teacher=self.teacher.pk, start_time="14:00", end_time="15:30"),
        )
        self.assertEqual(main.status_code, 302)
        self.assertTrue(
            Schedule.objects.filter(
                group=self.group, teacher=self.teacher, start_time=time(14, 0)
            ).exists()
        )
        extra = self.client.post(
            reverse("schedule_add"),
            self.lesson_data(
                teacher=self.replacement.pk, start_time="16:00", end_time="17:30"
            ),
        )
        self.assertEqual(extra.status_code, 302)
        self.assertTrue(
            Schedule.objects.filter(
                group=self.group, teacher=self.replacement, start_time=time(16, 0)
            ).exists()
        )

    def test_period_page_lists_only_program_teachers(self):
        """В строке плана — только преподаватели программы этой строки."""
        page = self.client.get(self.url())
        self.assertEqual(page.status_code, 200)
        rows = {row["group"]: row for row in page.context["rows"]}
        program_row = rows[self.group.name]
        self.assertEqual(
            {teacher.pk for teacher in program_row["teachers"]},
            {self.replacement.pk, self.busy.pk},
        )
        self.assertNotIn(
            self.foreign.pk, {teacher.pk for teacher in program_row["teachers"]}
        )
        # Общее поле «Один заменяющий на все дни» — объединение по строкам,
        # и чужого преподавателя там тоже нет.
        master_ids = {teacher.pk for teacher in page.context["teachers"]}
        self.assertEqual(master_ids, {self.replacement.pk, self.busy.pk})
        self.assertNotIn(self.foreign.pk, master_ids)

        # Фрагмент списка строки: только преподаватели программы.
        body = page.content.decode()
        select = re.search(
            r'<select name="teacher_%s".*?</select>' % re.escape(program_row["key"]),
            body,
            re.S,
        ).group(0)
        values = set(re.findall(r'<option value="(\d+)"', select))
        self.assertEqual(values, {str(self.replacement.pk), str(self.busy.pk)})
        self.assertNotIn(str(self.foreign.pk), values)

    def test_master_replacement_applies_only_where_allowed(self):
        """«Один заменяющий на все дни» не заполняет строки чужой программы."""
        foreign_subject = Subject.objects.create(
            name="Четвёртая программа",
            branch=self.branch,
            term=self.term,
            teacher=self.teacher,
        )
        foreign_subject.teachers.add(self.busy)
        foreign_group = Group.objects.create(
            name="Группа 4", subject=foreign_subject, term=self.term
        )
        Schedule.objects.create(
            group=foreign_group,
            branch=self.branch,
            room=self.third_room,
            day_of_week=Schedule.Weekday.MONDAY,
            start_time=time(16, 0),
            end_time=time(17, 30),
            teacher=self.teacher,
        )

        page = self.client.get(self.url(), {"teacher": self.replacement.pk})
        rows = {(row["group"], row["date"]): row for row in page.context["rows"]}
        foreign_rows = [
            row for (group, _day), row in rows.items() if group == foreign_group.name
        ]
        self.assertEqual(len(foreign_rows), 2)
        for row in foreign_rows:
            self.assertEqual(row["state"], "foreign")
            self.assertIn("нужен свой заменяющий", str(page.content.decode()))
            self.assertEqual(
                {teacher.pk for teacher in row["teachers"]}, {self.busy.pk}
            )
        self.assertEqual(page.context["counts"]["create"], 3)
        self.assertEqual(page.context["counts"]["skipped"], 2)

        # Без JS: в форме только общий заменяющий — строки чужой программы
        # остаются без него и просто пропускаются.
        data = {"action": "save", "master_teacher": self.replacement.pk}
        for (_group, _day), row in rows.items():
            data[f"apply_{row['key']}"] = "on"
        response = self.client.post(self.url(), data, follow=True)
        self.assertContains(response, "Создано подмен: 3, пропущено: 2")
        self.assertNotContains(response, "Пропущены из-за конфликтов")
        self.assertFalse(
            Schedule.objects.filter(
                is_substitution=True, group=foreign_group
            ).exists()
        )

        # А если чужого заменяющего прислать явно — это неустранимый конфликт.
        # Сначала возвращаем данные к исходным: разовое занятие снова за
        # отсутствующим, созданные подмены убраны (иначе список строк изменится).
        Schedule.objects.filter(is_substitution=True).exclude(pk=self.one_off.pk).delete()
        self.one_off.refresh_from_db()
        self.one_off.teacher = self.teacher
        self.one_off.is_substitution = False
        self.one_off.replaced_teacher = None
        self.one_off.save()
        for (_group, _day), row in rows.items():
            data[f"teacher_{row['key']}"] = self.replacement.pk
        response = self.client.post(self.url(), data, follow=True)
        self.assertContains(response, "Создано подмен: 3, пропущено: 2")
        self.assertContains(response, "Пропущены из-за конфликтов (2)")
        self.assertFalse(
            Schedule.objects.filter(
                is_substitution=True, group=foreign_group
            ).exists()
        )

    def test_legacy_lesson_with_foreign_teacher_still_saves(self):
        """Старое занятие с «неправильным» преподавателем сохраняется как было."""
        legacy = Schedule.objects.create(
            group=self.group,
            branch=self.branch,
            room=self.other_room,
            day_of_week=Schedule.Weekday.MONDAY,
            start_time=time(18, 0),
            end_time=time(19, 30),
            teacher=self.foreign,
        )
        response = self.client.post(
            reverse("schedule_edit", args=[legacy.pk]),
            {
                "group": self.group.pk,
                "teacher": self.foreign.pk,
                "room": self.other_room.pk,
                "day_of_week": Schedule.Weekday.MONDAY,
                "start_time": "18:00",
                "end_time": "19:30",
            },
        )
        self.assertEqual(response.status_code, 302)
        legacy.refresh_from_db()
        self.assertEqual(legacy.teacher_id, self.foreign.pk)
        self.assertIsNone(legacy.replaced_teacher_id)
