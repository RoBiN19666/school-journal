"""Автотесты волны 2 и 3: отсутствия преподавателей.

Проверяют модель (перевёрнутые даты и пересечение периодов), страницы раздела
«Отсутствия» (методист добавляет и удаляет, преподавателю нельзя), снятие
конфликта занятости на дату отсутствия и пометки/часы в недельном журнале.

Волна 3 добавляет выгрузки: посещаемость (``/export/attendance.xlsx``),
темы и часы (``/export/topics.xlsx``), расписание Excel и PNG, а также «Мою
посещаемость» преподавателя. Отменённое занятие в них помечено и не искажает
сводку, часы отменённых занятий не входят в итог, картинка расписания
отдаётся PNG и открывается Pillow (пометка отсутствия на картинке не выводится —
выгрузка возвращена к простому варианту, см. ``docs/NEXT-STEPS.md``).

Тесты работают только на ``django.test.TestCase`` и штатном тестовом клиенте —
как ``test_permissions.py``, откуда взяты помощники ``make_user``/``make_admin``.
Данные детерминированные: полугодие 2024/2025, 13 января 2025 — понедельник.

Важная особенность журнала: пока в базе нет ни одного активного администратора,
``journal.middleware.FirstRunSetupMiddleware`` уводит ЛЮБУЮ страницу на ``/setup/``.
Поэтому администратор создаётся в каждом наборе данных.
"""
from datetime import date, time, timedelta
from io import BytesIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils import translation
from openpyxl import load_workbook
from PIL import Image

from journal.models import (
    AcademicYear,
    Attendance,
    Branch,
    Group,
    LessonTopic,
    Room,
    Schedule,
    Student,
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

#: Понедельник с отсутствием и понедельник без отсутствия.
MONDAY = date(2025, 1, 13)
FRIDAY = date(2025, 1, 17)
NEXT_MONDAY = date(2025, 1, 20)

#: Коды, которыми представления отказывают в доступе (см. test_permissions).
DENIED_STATUSES = (302, 403, 404)


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


class AbsenceTestCase(TestCase):
    """Общий набор данных: полугодие, адрес, преподаватель и две группы.

    Две группы нужны для проверки конфликта: занятие ставится второй группе в то
    же время, когда у того же преподавателя идёт занятие первой.
    """

    def setUp(self):
        make_admin()
        self.teacher = make_user("teacher-absence", User.Role.TEACHER)
        self.year = AcademicYear.objects.create(
            start_date=TERM_START, end_date=TERM_END
        )
        self.term = Term.objects.create(
            year=self.year, number=1, start_date=TERM_START, end_date=TERM_END
        )
        self.branch = Branch.objects.create(name="Основной адрес")
        self.room = Room.objects.create(branch=self.branch, name="Кабинет 1")
        self.other_room = Room.objects.create(branch=self.branch, name="Кабинет 2")
        self.student = Student.objects.create(
            last_name="Учеников", first_name="Ученик", birth_date=date(2010, 5, 5)
        )
        self.subject = Subject.objects.create(
            name="Программа", branch=self.branch, term=self.term, teacher=self.teacher
        )
        self.group = Group.objects.create(
            name="Группа 1", subject=self.subject, term=self.term
        )
        self.group.students.add(self.student)
        # Занятие преподавателя по понедельникам 10:00–11:30.
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

    def make_absence(self, date_from=MONDAY, date_to=FRIDAY, teacher=None, **kwargs):
        return TeacherAbsence.objects.create(
            teacher=teacher or self.teacher,
            date_from=date_from,
            date_to=date_to,
            kind=kwargs.pop("kind", TeacherAbsence.Kind.SICK),
            **kwargs,
        )

    def login(self, user):
        """Войти и включить тестовое полугодие и адрес.

        Активный период берётся из сессии (``ActivePeriodMixin``), а в свежей базе
        миграция ``0025_default_academic_years`` уже создала годы вокруг текущей
        даты. Без этой настройки недельный журнал смотрел бы другое полугодие.
        """
        self.client.force_login(user)
        session = self.client.session
        session[SESSION_YEAR] = self.year.pk
        session[SESSION_TERM] = self.term.pk
        session[SESSION_BRANCH] = self.branch.pk
        session.save()

    def lesson_form(self, lesson_date, group=None, room=None, teacher=None):
        """Данные формы занятия на конкретную дату."""
        return {
            "group": (group or self.second_group).pk,
            "teacher": (teacher or self.teacher).pk,
            "room": (room or self.other_room).pk,
            "lesson_date": lesson_date.isoformat(),
            "start_time": "10:00",
            "end_time": "11:30",
        }


class TeacherAbsenceModelTests(AbsenceTestCase):
    """Период отсутствия: даты по порядку и без пересечений у преподавателя."""

    def test_reversed_dates_rejected(self):
        """Дата «по» раньше даты «с» — ошибка формы и модели."""
        absence = TeacherAbsence(
            teacher=self.teacher,
            date_from=MONDAY,
            date_to=date(2025, 1, 10),
            kind=TeacherAbsence.Kind.SICK,
        )
        with self.assertRaises(ValidationError) as caught:
            absence.full_clean()
        self.assertIn("date_to", caught.exception.message_dict)
        self.assertEqual(TeacherAbsence.objects.count(), 0)

    def test_overlapping_periods_rejected(self):
        """Пересечение периодов у одного преподавателя отклоняется, вплотную — нет."""
        self.make_absence(MONDAY, FRIDAY)
        overlapping = TeacherAbsence(
            teacher=self.teacher,
            date_from=date(2025, 1, 15),
            date_to=date(2025, 1, 25),
            kind=TeacherAbsence.Kind.TRIP,
        )
        with self.assertRaises(ValidationError):
            overlapping.full_clean()

        # Вплотную к прежнему периоду — не пересечение.
        touching = TeacherAbsence(
            teacher=self.teacher,
            date_from=date(2025, 1, 18),
            date_to=NEXT_MONDAY,
            kind=TeacherAbsence.Kind.TRIP,
        )
        touching.full_clean()
        # Тот же период у другого преподавателя никому не мешает.
        other = make_user("teacher-other-absence", User.Role.TEACHER)
        TeacherAbsence(
            teacher=other,
            date_from=MONDAY,
            date_to=FRIDAY,
            kind=TeacherAbsence.Kind.TRIP,
        ).full_clean()


class TeacherAbsencePageTests(AbsenceTestCase):
    """Страницы раздела «Отсутствия»: добавление и удаление периода."""

    def test_methodist_adds_and_deletes_absence(self):
        """Методист вносит и удаляет период, а карточка показывает только строку.

        Отсутствия живут в отдельном разделе: в карточке преподавателя нет ни
        формы добавления, ни таблицы периодов — только одна строка о текущем или
        ближайшем отсутствии (``TeacherUpdateView.current_absence``).
        """
        methodist = make_user("methodist-absence", User.Role.METHODIST)
        self.login(methodist)

        # Отсутствия вносят в отдельном разделе «Отсутствия»: выбранный педагог
        # приходит в адресе параметром ?teacher=<pk> и полем формы.
        response = self.client.post(
            reverse("absence_add") + f"?teacher={self.teacher.pk}",
            {
                "teacher": self.teacher.pk,
                "date_from": MONDAY.isoformat(),
                "date_to": FRIDAY.isoformat(),
                "kind": TeacherAbsence.Kind.SICK,
                "comment": "справка из поликлиники",
            },
        )
        self.assertEqual(response.status_code, 302)
        absence = TeacherAbsence.objects.get(teacher=self.teacher)
        self.assertEqual((absence.date_from, absence.date_to), (MONDAY, FRIDAY))
        self.assertEqual(absence.kind, TeacherAbsence.Kind.SICK)
        self.assertEqual(absence.created_by, methodist)

        # Блока «Отсутствия» в карточке больше нет: ни полей формы добавления,
        # ни ссылки на неё. Прошедший период строку в карточке не заполняет.
        # Проверяем именно разметку карточки, а не текст сообщения о добавлении.
        card = self.client.get(reverse("teacher_edit", args=[self.teacher.pk]))
        self.assertEqual(card.status_code, 200)
        card_body = card.content.decode()
        self.assertNotIn('name="date_from"', card_body)
        self.assertNotIn('name="date_to"', card_body)
        self.assertNotIn(reverse("absence_add"), card_body)
        self.assertNotIn("Отсутствует:", card_body)

        response = self.client.post(
            reverse("absence_delete", args=[absence.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TeacherAbsence.objects.count(), 0)

        # Незакончившийся период карточка показывает одной компактной строкой со
        # ссылкой на раздел «Отсутствия», отфильтрованный по этому преподавателю.
        today = timezone.localdate()
        current = self.make_absence(today, today + timedelta(days=6))
        card = self.client.get(reverse("teacher_edit", args=[self.teacher.pk]))
        card_body = card.content.decode()
        self.assertIn("Отсутствует:", card_body)
        self.assertIn(
            f"с {current.date_from:%d.%m.%Y} по {current.date_to:%d.%m.%Y}",
            card_body,
        )
        self.assertIn(
            f'{reverse("absence_list")}?teacher={self.teacher.pk}', card_body
        )

    def test_teacher_cannot_manage_absences(self):
        """Преподавателю закрыты и добавление, и удаление отсутствий."""
        absence = self.make_absence()
        self.login(self.teacher)

        add_url = reverse("absence_add") + f"?teacher={self.teacher.pk}"
        delete_url = reverse("absence_delete", args=[absence.pk])
        for method, url in (
            ("get", add_url),
            ("post", add_url),
            ("get", delete_url),
            ("post", delete_url),
        ):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(
                    url,
                    {
                        "date_from": NEXT_MONDAY.isoformat(),
                        "date_to": NEXT_MONDAY.isoformat(),
                        "kind": TeacherAbsence.Kind.OTHER,
                    },
                )
                self.assertIn(response.status_code, DENIED_STATUSES)
        self.assertEqual(TeacherAbsence.objects.count(), 1)


class ScheduleAbsenceConflictTests(AbsenceTestCase):
    """Занятие на дату отсутствия не блокируется, на обычную дату — блокируется."""

    def setUp(self):
        super().setUp()
        self.login(make_user("methodist-plan", User.Role.METHODIST))

    def test_conflict_is_skipped_on_absence_day_only(self):
        """На дату отсутствия подмена ставится, на дату без отсутствия — конфликт."""
        self.make_absence(MONDAY, FRIDAY)

        # 13.01.2025: преподаватель отсутствует — занятость по нему не мешает,
        # хотя в это же время у него идёт занятие первой группы.
        response = self.client.post(
            reverse("schedule_add"), self.lesson_form(MONDAY)
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Schedule.objects.filter(
                group=self.second_group, lesson_date=MONDAY
            ).exists()
        )

        # 20.01.2025: отсутствия нет — то же занятие отбивается конфликтом.
        response = self.client.post(
            reverse("schedule_add"), self.lesson_form(NEXT_MONDAY)
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Schedule.objects.filter(
                group=self.second_group, lesson_date=NEXT_MONDAY
            ).exists()
        )
        self.assertContains(
            response, "уже занятие", status_code=200, html=False
        )


class AbsenceJournalTests(AbsenceTestCase):
    """Недельный журнал и страница отметок: пометка, часы и запрет отметок."""

    def setUp(self):
        super().setUp()
        self.login(make_user("methodist-journal", User.Role.METHODIST))
        self.absence = self.make_absence(MONDAY, FRIDAY)

    def test_absent_lesson_is_marked_and_not_counted(self):
        """Недельный журнал помечает отсутствие; отметка на отменённое занятие не идёт.

        Часы (10:00–11:30 — 2 акад. ч.) в план/факт не входят и показываются
        отдельной строкой «отменено». Отметка сохраняется только на дату без
        отсутствия: 20.01.2025 — обычный понедельник.
        """
        response = self.client.get(
            reverse("week_journal"), {"week": MONDAY.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        # Пометка периода видна рядом с занятием.
        self.assertContains(response, "Больничный с 13.01.2025 по 17.01.2025")
        self.assertContains(response, "занятие отменено")
        # Часы занятия 10:00–11:30 (2 акад. ч.) в итог не входят.
        self.assertContains(response, "академических часов: 0")
        # Отдельная строка «отменено: N ч» (в русском интерфейсе разделитель — запятая).
        self.assertContains(response, "отменено: 2,00 ч")

        # Страница отметок предупреждает об отсутствии.
        page = self.client.get(
            reverse("attendance", args=[self.schedule.pk]),
            {"date": MONDAY.isoformat()},
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Отсутствие: Больничный с 13.01.2025")

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

        # Дата без отсутствия отмечается как обычно.
        response = self.client.post(
            reverse("attendance", args=[self.schedule.pk]),
            {
                "date": NEXT_MONDAY.isoformat(),
                f"status_{self.student.pk}": Attendance.Status.PRESENT,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Attendance.objects.filter(
                schedule=self.schedule, date=NEXT_MONDAY
            ).count(),
            1,
        )


class AbsenceExportTests(AbsenceTestCase):
    """Выгрузки волны 3: отменённые занятия помечены и не искажают итоги.

    Данные: занятие по понедельникам 10:00–11:30 (2 акад. ч.) у преподавателя,
    отсутствие с 13.01 по 17.01.2025 и две отметки — «отсутствовал» на
    отменённое занятие 13.01 (её мог поставить кто угодно до больничного) и
    «присутствовал» на обычное занятие 20.01.
    """

    def setUp(self):
        super().setUp()
        # Администратор уже создан в общем наборе данных (он же снимает
        # первоначальную настройку журнала) — входим под ним.
        self.login(User.objects.get(username="boss-admin"))
        self.absence = self.make_absence(MONDAY, FRIDAY)
        self.topic = LessonTopic.objects.create(
            schedule=self.schedule, date=MONDAY, topic="Отменённая тема"
        )
        Attendance.objects.create(
            schedule=self.schedule,
            student=self.student,
            date=MONDAY,
            status=Attendance.Status.ABSENT,
        )
        Attendance.objects.create(
            schedule=self.schedule,
            student=self.student,
            date=NEXT_MONDAY,
            status=Attendance.Status.PRESENT,
        )

    def download(self, name):
        """Скачивает выгрузку и возвращает ответ клиента (проверяя код 200)."""
        response = self.client.get(reverse(name))
        self.assertEqual(response.status_code, 200)
        return response

    def workbook(self, name):
        """Книга выгрузки: ключи — имена листов, значения — строки значений.

        Лист читается целиком до закрытия книги: у ``read_only``-режима openpyxl
        держит временный файл, а закрытие должно быть явным — иначе на Windows
        временный файл не удаляется.
        """
        response = self.download(name)
        book = load_workbook(BytesIO(response.content), read_only=True)
        try:
            return {
                sheet.title: [list(row) for row in sheet.iter_rows(values_only=True)]
                for sheet in book.worksheets
            }
        finally:
            book.close()

    def header_row(self, rows, first_header):
        """Строка заголовков таблицы (над ней — строки-заголовки выгрузки)."""
        found = [row for row in rows if row[0] == first_header]
        self.assertEqual(len(found), 1, f"шапка «{first_header}» не найдена")
        return found[0]

    def key_row(self, rows, title):
        """Единственная строка ТАБЛИЦЫ, в которой встречается ``title``.

        Строки-заголовки над таблицей пропускаются: отсчёт идёт от шапки.
        """
        body = rows[rows.index(self.header_row(rows, rows[1][0])) + 1:]
        found = [row for row in body if title in row]
        self.assertEqual(
            len(found), 1, f"строк с «{title}» — {len(found)}, ожидалась одна"
        )
        return found[0]

    def test_attendance_export_marks_cancelled_lesson(self):
        """Отменённое занятие помечено и не даёт ложного «отсутствовал» в сводке."""
        with translation.override("ru"):
            sheets = self.workbook("export_attendance")

        rows = sheets["Посещаемость"]
        header = self.header_row(rows, "Фамилия")
        self.assertEqual(header[14], "Статус")
        self.assertEqual(header[15], "Отсутствие преподавателя")
        cancelled = self.key_row(rows, "Отменённая тема")
        normal = self.key_row(rows, "20.01.2025")
        self.assertEqual(cancelled[14], "занятие отменено")
        self.assertEqual(
            cancelled[15], "отсутствует: Больничный с 13.01.2025 по 17.01.2025"
        )
        self.assertEqual(normal[14], "Присутствовал")
        # У обычного занятия пометки нет: пустая ячейка читается как None.
        self.assertFalse(normal[15])

        summary = sheets["Сводка"]
        self.assertEqual(summary[0][0], "Ученик")
        self.assertIn("Отменено", summary[0])
        self.assertEqual(len(summary), 2, "в сводке должна быть одна строка ученика")
        counts = dict(zip(summary[0], summary[1]))
        # Опасная отметка «отсутствовал» стоит на отменённом занятии: в сводке
        # она не пропуск, а отмена, и часов посещения не даёт.
        self.assertEqual(counts["Отсутствовал"], 0)
        self.assertEqual(counts["Опоздал"], 0)
        self.assertEqual(counts["Отменено"], 1)
        self.assertEqual(counts["Присутствовал"], 1)
        self.assertEqual(counts["Всего отметок"], 2)
        self.assertEqual(counts["Часов посещено"], 2)

    def test_topics_export_excludes_cancelled_hours(self):
        """Часы отменённого занятия не в итоге и вынесены строкой «отменено»."""
        with translation.override("ru"):
            sheets = self.workbook("export_topics")

        rows = sheets["Темы занятий"]
        self.assertEqual(self.header_row(rows, "Дата")[9], "Академических часов")
        # Строка-заголовок над таблицей тоже сообщает об отменённых часах.
        self.assertIn("отменено: 2.00 ч", rows[0][0])
        # Тема отменённого занятия заменена пометкой, поэтому строку ищем по дате.
        topic_row = self.key_row(rows, "13.01.2025")
        self.assertEqual(topic_row[8], "занятие отменено")
        # Часы занятия видны в строке, но в «Итого часов» их нет: там 0.
        self.assertEqual(float(topic_row[9]), 2.0)
        total = [row for row in rows if row[8] == "Итого часов"]
        self.assertEqual(len(total), 1)
        self.assertEqual(float(total[0][9]), 0.0)
        cancelled = [row for row in rows if str(row[8]).startswith("отменено:")]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0][8], "отменено: 2.00 ч")

    def test_schedule_exports_mark_absence(self):
        """Расписание Excel помечает отсутствие, картинка расписания отдаётся.

        Выгрузку смотрим «на дату внутри периода»: колонка «Отсутствие
        преподавателя» показывает период, относящийся к показанной неделе (по
        умолчанию — текущей), поэтому прошедший период в неё не попадает.
        """
        week = date(2025, 1, 14)  # вторник недели 13–19.01.2025
        with translation.override("ru"), mock.patch(
            "journal.absences.timezone.localdate", return_value=week
        ):
            sheets = self.workbook("export_schedule")
            rows = sheets["Расписание"]
            self.assertEqual(
                self.header_row(rows, "День недели")[9], "Отсутствие преподавателя"
            )
            lesson = self.key_row(rows, "Группа 1")
            self.assertEqual(
                lesson[9], "отсутствует: Больничный с 13.01.2025 по 17.01.2025"
            )

        # Позже, когда период уже прошёл, колонка пустая: прошедшее не показываем.
        with translation.override("ru"), mock.patch(
            "journal.absences.timezone.localdate", return_value=date(2025, 3, 3)
        ):
            rows = self.workbook("export_schedule")["Расписание"]
            self.assertFalse(self.key_row(rows, "Группа 1")[9])

        with translation.override("ru"):
            # Картинка расписания вернулась к простому варианту (решение заказчика),
            # подпись об отсутствии на ней не выводится — см. docs/NEXT-STEPS.md.
            response = self.download("export_schedule_image")
        self.assertEqual(response["Content-Type"], "image/png")
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.format, "PNG")
            image.load()
            self.assertGreater(image.size[0], 0)

    def test_teacher_own_export_marks_cancelled_lesson(self):
        """«Моя посещаемость» преподавателя: отменённое занятие помечено, сводка чистая."""
        self.login(self.teacher)
        with translation.override("ru"):
            sheets = self.workbook("teacher_export")

        sheet_name = "Моя посещаемость"
        self.assertIn(sheet_name, sheets)
        header = self.header_row(sheets[sheet_name], "Фамилия")
        self.assertEqual(header[14], "Статус")
        self.assertEqual(header[15], "Отсутствие преподавателя")
        cancelled = self.key_row(sheets[sheet_name], "Отменённая тема")
        self.assertEqual(cancelled[14], "занятие отменено")
        self.assertEqual(
            cancelled[15], "отсутствует: Больничный с 13.01.2025 по 17.01.2025"
        )
        summary = sheets["Сводка"]
        counts = dict(zip(summary[0], summary[1]))
        self.assertEqual(counts["Отсутствовал"], 0)
        self.assertEqual(counts["Отменено"], 1)
        self.assertEqual(counts["Часов посещено"], 2)
