"""Выгрузка данных журнала в Excel (openpyxl)."""
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from django.db.models import (
    Case,
    Count,
    DecimalField,
    Exists,
    F,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import (
    Cast,
    Coalesce,
    ExtractHour,
    ExtractMinute,
    Round,
)
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .absences import (
    absence_caption,
    absences_by_teacher,
    pick_absence,
    schedule_absence_labels,
)
from .models import (
    Attendance,
    ContestResult,
    Group,
    Schedule,
    Student,
    Subject,
    TeacherAbsence,
    TeacherDevelopment,
    User,
)

#: Строки пометок в выгрузках. Как и подписи статусов, это ленивые строки
#: перевода: openpyxl принимает только обычные значения, поэтому перед записью
#: в ячейку их приводит к str() тот же код, что и раньше (см. start_stream_sheet).
ABSENCE_HEADER = _("Отсутствие преподавателя")
CANCELLED_HEADER = _("Отменено")
CANCELLED_MARK = _("занятие отменено")
CANCELLED_HOURS_LABEL = _("отменено: %(hours)s ч")
ABSENCE_SCHEDULE_LABEL = _("отсутствует: %(caption)s")


HEADER_FILL = PatternFill("solid", fgColor="4F46E5")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=13)
TITLE_FILL = PatternFill("solid", fgColor="EEF2FF")
STATUS_LABELS = dict(Attendance.Status.choices)


def build_workbook(sheets):
    """Собирает книгу Excel из описаний листов.

    У листа могут быть title_lines — строки-заголовки над таблицей
    (например, адрес и учебный год).
    """
    workbook = Workbook()
    workbook.remove(workbook.active)

    for sheet in sheets:
        worksheet = workbook.create_sheet(title=sheet["title"][:31])
        headers = sheet["headers"]
        title_lines = sheet.get("title_lines") or []

        for line in title_lines:
            worksheet.append([line])
        for index in range(1, len(title_lines) + 1):
            worksheet.merge_cells(
                start_row=index,
                start_column=1,
                end_row=index,
                end_column=len(headers),
            )
            cell = worksheet.cell(row=index, column=1)
            cell.font = TITLE_FONT
            cell.fill = TITLE_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")

        header_row = len(title_lines) + 1
        worksheet.append(headers)
        for cell in worksheet[header_row]:
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(vertical="center")

        for row in sheet["rows"]:
            worksheet.append(row)

        worksheet.freeze_panes = f"A{header_row + 1}"
        widths = sheet.get("widths") or []
        for index, header in enumerate(headers, start=1):
            if index <= len(widths) and widths[index - 1]:
                width = widths[index - 1]
            else:
                values = [len(str(header))]
                for row in sheet["rows"]:
                    if len(row) >= index:
                        values.append(len(str(row[index - 1])))
                width = min(max(max(values) + 2, 10), 45)
            worksheet.column_dimensions[get_column_letter(index)].width = width

        if sheet["rows"]:
            worksheet.auto_filter.ref = (
                f"A{header_row}:{get_column_letter(len(headers))}{worksheet.max_row}"
            )

    return workbook


def start_stream_sheet(
    workbook, title, headers, title_lines=(), widths=(), rows_total=0
):
    """Готовит лист write_only-книги и возвращает его вместе с номером строки заголовков.

    В режиме write_only строки уходят в файл сразу и не хранятся в памяти: так
    выгрузка на сотни тысяч отметок не съедает гигабайты. Оформление ячеек и
    ширины задаются до добавления первой строки — перечитать лист потом нельзя.
    """
    # Заголовок, шапка и строки над таблицей приходят из разметки перевода и
    # могут быть ленивыми строками (gettext_lazy). openpyxl такие объекты не
    # принимает — «Cannot convert ... to Excel», а срез title[:31] по ленивому
    # объекту считается неверно. Приводим к обычной строке заранее: перевод при
    # этом вычисляется в языке текущего запроса, а не при импорте модуля.
    title = str(title)
    headers = [str(header) for header in headers]
    title_lines = [str(line) for line in title_lines]

    worksheet = workbook.create_sheet(title=title[:31])
    last_column = get_column_letter(len(headers))
    for index, header in enumerate(headers, start=1):
        if index <= len(widths) and widths[index - 1]:
            width = widths[index - 1]
        else:
            width = min(max(len(str(header)) + 2, 10), 45)
        worksheet.column_dimensions[get_column_letter(index)].width = width

    header_row = len(title_lines) + 1
    worksheet.freeze_panes = f"A{header_row + 1}"
    # У write_only-листа openpyxl не пишет размер листа сам: подсказываем его,
    # чтобы файл не отличался от собранного обычным способом.
    last_row = header_row + rows_total
    worksheet.calculate_dimension = lambda: f"A1:{last_column}{last_row}"

    for line in title_lines:
        cell = WriteOnlyCell(worksheet, value=line)
        cell.font = TITLE_FONT
        cell.fill = TITLE_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        worksheet.append([cell])
    for index in range(1, len(title_lines) + 1):
        worksheet.merged_cells.add(f"A{index}:{last_column}{index}")

    header_cells = []
    for text in headers:
        cell = WriteOnlyCell(worksheet, value=text)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        header_cells.append(cell)
    worksheet.append(header_cells)

    return worksheet, header_row


def stream_filter(worksheet, header_row, headers, rows_written):
    """Ставит автофильтр так же, как у обычного листа (или не ставит его вовсе)."""
    if rows_written:
        last_column = get_column_letter(len(headers))
        worksheet.auto_filter.ref = (
            f"A{header_row}:{last_column}{header_row + rows_written}"
        )


def as_response(workbook, prefix):
    """Отдаёт книгу как файл для скачивания."""
    stream = BytesIO()
    workbook.save(stream)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"{prefix}_{stamp}.xlsx"
    response = HttpResponse(
        stream.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def students_workbook():
    rows = []
    for student in Student.objects.prefetch_related("groups__subject"):
        groups = "; ".join(
            f"{group.subject.name} / {group.name}" for group in student.groups.all()
        )
        rows.append(
            [
                student.last_name,
                student.first_name,
                student.middle_name,
                student.birth_date.strftime("%d.%m.%Y") if student.birth_date else "",
                student.groups.count(),
                student.dismissal_short,
                student.dismissal_title,
                groups,
            ]
        )
    return build_workbook(
        [
            {
                "title": "Ученики",
                "title_lines": [
                    "Ученики · код статуса БП: Н — нет заявления, "
                    "ЕН — есть заявление, не отпускать, ЕО — можно отпускать"
                ],
                "headers": [
                    "Фамилия",
                    "Имя",
                    "Отчество",
                    "Дата рождения",
                    "Групп",
                    "Код БП",
                    "Отпускать при беспилотной опасности",
                    "Группы",
                ],
                "rows": rows,
                "widths": [20, 15, 20, 16, 8, 10, 40, 45],
            }
        ]
    )


def contests_workbook(queryset=None):
    """Участие детей в конкурсах: дата, мероприятие, ФИО, статус."""
    records = queryset if queryset is not None else ContestResult.objects.all()
    records = records.select_related("student").order_by("-date", "student__last_name")
    rows = []
    summary = {}
    for item in records:
        rows.append(
            [
                item.date.strftime("%d.%m.%Y"),
                item.title,
                item.student.full_name,
                item.get_status_display(),
                item.subject,
                item.comment,
            ]
        )
        # Ключ сводки — отображаемая подпись статуса, а не его код: счётчики
        # собираются по тем же подписям, что стоят в колонке «Статус».
        status_label = str(item.get_status_display())
        summary[status_label] = summary.get(status_label, 0) + 1

    # choices даёт пары (значение, подпись): перебираем их именно так, иначе в
    # сводку попадали коды ('winner'/'prize'/'participant') с нулевыми счётчиками.
    # str() обязателен: подписи — ленивые строки перевода, openpyxl принимает
    # только обычные значения (в write_only-режиме такое значение даёт 500).
    summary_rows = [
        [str(label), summary.get(str(label), 0)]
        for value, label in ContestResult.Status.choices
    ]
    return build_workbook(
        [
            {
                "title": "Участие в конкурсах",
                "title_lines": [
                    f"Участие детей в конкурсах и мероприятиях · всего записей: {len(rows)}"
                ],
                "headers": [
                    "Дата",
                    "Название мероприятия",
                    "ФИО",
                    "Статус",
                    "Направление",
                    "Комментарий",
                ],
                "rows": rows,
                "widths": [12, 45, 32, 16, 24, 30],
            },
            {
                "title": "Сводка",
                "headers": ["Статус", "Записей"],
                "rows": summary_rows,
                "widths": [20, 12],
            },
        ]
    )


def development_workbook(queryset=None):
    """Повышение квалификации и конкурсы педагогов."""
    records = queryset if queryset is not None else TeacherDevelopment.objects.all()
    records = records.select_related("teacher").order_by("-date", "teacher__last_name")
    rows = []
    hours_total = 0
    for item in records:
        hours_total += item.hours or 0
        rows.append(
            [
                item.date.strftime("%d.%m.%Y"),
                item.get_kind_display(),
                item.teacher.full_name,
                item.program,
                item.organization,
                item.hours if item.hours else "",
                item.result,
            ]
        )
    rows.append(["", "", "", "", "Итого часов", hours_total, ""])
    return build_workbook(
        [
            {
                "title": "Педагоги",
                "title_lines": [
                    "Повышение квалификации и конкурсы педагогов · всего записей: "
                    f"{len(rows) - 1}"
                ],
                "headers": [
                    "Дата",
                    "Вид",
                    "Педагог",
                    "Название программы или мероприятия",
                    "Образовательная организация",
                    "Часов",
                    "Результат",
                ],
                "rows": rows,
                "widths": [12, 24, 28, 45, 40, 10, 26],
            }
        ]
    )


def subjects_workbook(term=None, branch=None):
    """Программы и группы активного полугодия и активного адреса."""
    subject_rows = []
    group_rows = []
    subjects = Subject.objects.select_related("teacher", "branch", "term").prefetch_related(
        "groups__students"
    )
    if term is not None:
        subjects = subjects.filter(term=term)
    if branch is not None:
        subjects = subjects.filter(branch=branch)

    title_parts = ["Программы и группы"]
    if term is not None:
        title_parts.append(f"{term.year.title} учебный год, {term.title}")
    if branch is not None:
        title_parts.append(branch.name)

    for subject in subjects:
        groups = list(subject.groups.all())
        subject_rows.append(
            [
                subject.name,
                subject.teacher.full_name if subject.teacher else "не назначен",
                len(groups),
            ]
        )
        for group in groups:
            group_rows.append(
                [
                    subject.name,
                    group.name,
                    group.term.title if group.term else "",
                    group.students.count(),
                    "; ".join(student.full_name for student in group.students.all()),
                ]
            )
    return build_workbook(
        [
            {
                "title": "Программы",
                "title_lines": [" · ".join(title_parts)],
                "headers": ["Программа", "Преподаватель", "Групп"],
                "rows": subject_rows,
                "widths": [28, 28, 10],
            },
            {
                "title": "Группы",
                "headers": ["Программа", "Группа", "Полугодие", "Учеников", "Состав"],
                "rows": group_rows,
                "widths": [28, 24, 14, 12, 60],
            },
        ]
    )


def schedule_workbook(term=None, branch=None):
    rows = []
    schedules = Schedule.objects.select_related(
        "group__subject", "group__term", "teacher", "room", "branch"
    )
    if term is not None:
        schedules = schedules.filter(group__term=term)
    if branch is not None:
        schedules = schedules.filter(branch=branch)
    # Учеников считаем одним запросом на всю выгрузку: обращение к
    # schedule.group.students.count() в цикле давало запрос на каждое занятие.
    # Подзапрос, а не annotate с join: join добавляет GROUP BY и меняет порядок строк.
    schedules = schedules.annotate(
        students_total=Subquery(
            Group.objects.filter(pk=OuterRef("group_id"))
            .annotate(total=Count("students"))
            .values("total")[:1]
        )
    )
    # Отсутствия преподавателей: у занятия по дням недели конкретной даты нет,
    # поэтому берётся период, пересекающийся с полугодием группы (волна 2 —
    # ``schedule_absence_labels``). Запрос один на всю выгрузку.
    items = list(schedules)
    absence_labels = schedule_absence_labels(items)
    for schedule in items:
        absence = absence_labels.get(schedule.pk)
        rows.append(
            [
                schedule.get_day_of_week_display(),
                # У разового занятия указываем дату: оно проходит только в этот день.
                schedule.lesson_date.strftime("%d.%m.%Y") if schedule.lesson_date else "",
                schedule.start_time.strftime("%H:%M"),
                schedule.end_time.strftime("%H:%M"),
                schedule.group.subject.name,
                schedule.group.name,
                schedule.room.name if schedule.room else "",
                schedule.group.term.title if schedule.group.term else "",
                schedule.teacher.full_name if schedule.teacher else "не назначен",
                # Пометка периода отсутствия рядом с ФИО преподавателя.
                str(ABSENCE_SCHEDULE_LABEL) % {"caption": absence_caption(absence)}
                if absence is not None
                else "",
                schedule.students_total,
            ]
        )

    title_parts = []
    if term is not None:
        title_parts.append(f"Расписание на {term.year.title} учебный год ({term.title})")
    else:
        title_parts.append("Расписание")
    if branch is not None:
        title_parts.append(branch.name)
    return build_workbook(
        [
            {
                "title": "Расписание",
                "title_lines": [" · ".join(title_parts)],
                "headers": [
                    "День недели",
                    "Дата разового занятия",
                    "Начало",
                    "Окончание",
                    "Программа",
                    "Группа",
                    "Кабинет",
                    "Полугодие",
                    "Преподаватель",
                    str(ABSENCE_HEADER),
                    "Учеников",
                ],
                "rows": rows,
                "widths": [16, 16, 10, 11, 28, 24, 26, 14, 28, 34, 12],
            }
        ]
    )


def topics_workbook(term=None, branch=None, queryset=None):
    """Темы занятий: дата, время, программа, группа, преподаватель, тема, акад. часы.

    Часы занятия, попавшего в период отсутствия преподавателя, в итог не входят:
    занятие не состоялось. Такие часы показываются отдельной строкой «отменено»,
    а в строке занятия стоит пометка «занятие отменено».
    """
    from .models import LessonTopic

    lessons = LessonTopic.objects.select_related(
        "schedule__group__subject",
        "schedule__group__term",
        "schedule__teacher",
        "schedule__room",
        "schedule__branch",
    ).order_by("date", "schedule__start_time")
    if queryset is not None:
        lessons = lessons.filter(schedule__in=queryset)
    if term is not None:
        lessons = lessons.filter(schedule__group__term=term)
    if branch is not None:
        lessons = lessons.filter(schedule__branch=branch)

    # Признак отмены считает база тем же условием, что и недельный журнал:
    # дата темы внутри периода отсутствия её преподавателя. Один EXISTS на всю
    # выгрузку — обращения к базе на каждую строку не будет.
    lessons = lessons.annotate(
        cancelled=Exists(
            TeacherAbsence.objects.filter(
                teacher=OuterRef("schedule__teacher"),
                date_from__lte=OuterRef("date"),
                date_to__gte=OuterRef("date"),
            )
        )
    )

    rows = []
    total_hours = Decimal("0")
    cancelled_hours = Decimal("0")
    cancelled_lessons = 0
    for item in lessons:
        schedule = item.schedule
        if item.cancelled:
            cancelled_lessons += 1
            cancelled_hours += item.hours
            topic = str(CANCELLED_MARK)
        else:
            total_hours += item.hours
            topic = item.topic
        rows.append(
            [
                item.date.strftime("%d.%m.%Y"),
                schedule.get_day_of_week_display(),
                schedule.start_time.strftime("%H:%M"),
                schedule.end_time.strftime("%H:%M"),
                schedule.group.subject.name,
                schedule.group.name,
                schedule.teacher.full_name if schedule.teacher else "",
                schedule.room.name if schedule.room else "",
                topic,
                float(item.hours),
            ]
        )
    rows.append(["", "", "", "", "", "", "", "", "Итого часов", float(total_hours)])
    if cancelled_lessons:
        # Часы отменённых занятий — отдельной строкой: в «Итого часов» их нет.
        rows.append(
            [
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                str(CANCELLED_HOURS_LABEL) % {"hours": f"{cancelled_hours:.2f}"},
                float(cancelled_hours),
            ]
        )

    title_parts = ["Темы занятий и академические часы"]
    if term is not None:
        title_parts.append(f"{term.year.title} учебный год, {term.title}")
    if branch is not None:
        title_parts.append(branch.name)
    title_parts.append("1 академический час = 45 минут")
    if cancelled_lessons:
        title_parts.append(str(CANCELLED_HOURS_LABEL) % {"hours": f"{cancelled_hours:.2f}"})

    return build_workbook(
        [
            {
                "title": "Темы занятий",
                "title_lines": [" · ".join(title_parts)],
                "headers": [
                    "Дата",
                    "День недели",
                    "Начало",
                    "Окончание",
                    "Программа",
                    "Группа",
                    "Преподаватель",
                    "Кабинет",
                    "Тема занятия",
                    "Академических часов",
                ],
                "rows": rows,
                "widths": [12, 15, 10, 11, 28, 22, 26, 22, 60, 18],
            }
        ]
    )


def attendance_workbook(queryset, title="Посещаемость", term=None, branch=None):
    """Лист с отметками (с темой занятия и часами) и лист со сводкой по ученикам.

    Книга собирается в режиме write_only: строки уходят в файл по мере чтения из
    базы, поэтому выгрузка не держит в памяти все отметки сразу. Тема занятия
    приходит подзапросом, сводку по ученикам считает база.

    Отметка, попавшая на занятие в период отсутствия преподавателя, помечается
    «занятие отменено» и НЕ считается ни пропуском, ни опозданием: в сводке по
    ученику такие отметки видны отдельной колонкой «Отменено», а в «Отсутствовал»
    и «Опоздал» не попадают. Так же считаются и часы посещения.
    """
    from .models import LessonTopic

    topic_match = LessonTopic.objects.filter(
        schedule_id=OuterRef("schedule_id"), date=OuterRef("date")
    ).order_by()
    topic_hours = Subquery(
        topic_match.values("hours")[:1],
        output_field=DecimalField(max_digits=5, decimal_places=2),
    )
    topic_title = Subquery(topic_match.values("topic")[:1])
    # Отмена занятия: период отсутствия преподавателя накрывает дату отметки.
    # EXISTS, а не join: join размножил бы строки выгрузки.
    cancelled_match = Exists(
        TeacherAbsence.objects.filter(
            teacher=OuterRef("schedule__teacher"),
            date_from__lte=OuterRef("date"),
            date_to__gte=OuterRef("date"),
        )
    )

    rows_queryset = queryset.select_related(
        "student",
        "schedule__group__subject",
        "schedule__group__term",
        "schedule__teacher",
        "schedule__room",
    ).annotate(topic_hours=topic_hours, topic_title=topic_title, cancelled=cancelled_match)

    # Часы считаем в сотых долях целым числом: сумма в базе тогда совпадает с
    # прежним сложением Decimal в Python до последнего знака.
    topic_cents = Cast(Round(topic_hours * 100, 0), IntegerField())
    minutes = (
        ExtractHour("schedule__end_time") * 60
        + ExtractMinute("schedule__end_time")
        - ExtractHour("schedule__start_time") * 60
        - ExtractMinute("schedule__start_time")
    )
    # Повторяет Schedule.duration_minutes: отрицательную длительность считаем нулём.
    lesson_minutes = Case(
        When(Q(schedule__end_time__lt=F("schedule__start_time")), then=Value(0.0)),
        default=minutes,
        output_field=IntegerField(),
    )
    # Без темы берём Schedule.academic_hours: 1 академический час = 45 минут.
    lesson_cents = Cast(Round(lesson_minutes * 100.0 / 45.0, 0), IntegerField())
    present_cents = Case(
        When(
            Q(status=Attendance.Status.PRESENT) & ~Q(cancelled=True),
            then=Coalesce(topic_cents, lesson_cents, output_field=IntegerField()),
        ),
        default=Value(0),
        output_field=IntegerField(),
    )
    # Сводка — отдельный агрегатный запрос. Сортировку снимаем: иначе она
    # попадает в GROUP BY (у срезанного queryset менять её нельзя).
    summary_source = queryset if queryset.query.is_sliced else queryset.order_by()
    summary = (
        summary_source.annotate(cancelled=cancelled_match)
        .values(
            "student__last_name",
            "student__first_name",
            "student__middle_name",
        )
        .annotate(
            present=Count(
                "pk", filter=Q(status=Attendance.Status.PRESENT, cancelled=False)
            ),
            absent=Count(
                "pk", filter=Q(status=Attendance.Status.ABSENT, cancelled=False)
            ),
            late=Count("pk", filter=Q(status=Attendance.Status.LATE, cancelled=False)),
            cancelled_count=Count("pk", filter=Q(cancelled=True)),
            marks=Count("pk"),
            hours_cents=Sum(present_cents),
        )
    )

    title_parts = []
    if term is not None:
        title_parts.append(f"{term.year.title} учебный год, {term.title}")
    if branch is not None:
        title_parts.append(f"Адрес: {branch.name}")

    headers = [
        "Фамилия",
        "Имя",
        "Отчество",
        "Дата",
        "День недели",
        "Время занятия",
        "Программа",
        "Группа",
        "Кабинет",
        "Полугодие",
        "Преподаватель",
        "Код БП",
        "Тема занятия",
        "Академических часов",
        "Статус",
        str(ABSENCE_HEADER),
    ]
    # Сводка приходит из базы одним запросом: в Python остаётся только свести
    # однофамильцев и посчитать общее число отметок (оно же — размер листа).
    # В корзине пять счётчиков, как и было: [присутствовал, отсутствовал,
    # опоздал, всего отметок, часы], плюс шестой — отменённые занятия.
    merged = {}
    for row in summary:
        name = " ".join(
            part
            for part in (
                row["student__last_name"],
                row["student__first_name"],
                row["student__middle_name"],
            )
            if part
        )
        bucket = merged.setdefault(name, [0, 0, 0, 0, 0, 0])
        bucket[0] += row["present"]
        bucket[1] += row["absent"]
        bucket[2] += row["late"]
        bucket[3] += row["marks"]
        bucket[4] += row["hours_cents"] or 0
        bucket[5] += row["cancelled_count"]
    summary_sorted = sorted(merged.items())

    workbook = Workbook(write_only=True)
    worksheet, header_row = start_stream_sheet(
        workbook,
        title,
        headers,
        title_lines=([f"{title} — " + " · ".join(title_parts)] if title_parts else []),
        widths=[20, 15, 20, 12, 16, 16, 24, 20, 22, 14, 24, 10, 50, 18, 18, 34],
        rows_total=sum(counts[3] for _, counts in summary_sorted),
    )
    rows_written = 0
    # Отсутствия преподавателей выгрузки — одним запросом: иначе каждая
    # отменённая отметка делала бы свой запрос. Список периодов ищется по датам
    # отметок, а попадание дня в период считает ``pick_absence`` — то же условие,
    # что и у признака ``cancelled`` в базе.
    teacher_ids = {
        row["schedule__teacher"] for row in rows_queryset.values("schedule__teacher")
    }
    starts = rows_queryset.aggregate(first=Min("date"), last=Max("date"))
    by_teacher = absences_by_teacher(teacher_ids, starts["first"], starts["last"])
    # iterator(chunk_size=...) читает отметки порциями вместо list() на всю выгрузку.
    for item in rows_queryset.iterator(chunk_size=2000):
        hours = (
            item.topic_hours
            if item.topic_hours is not None
            else item.schedule.academic_hours
        )
        cancelled = bool(item.cancelled)
        # Отменённое занятие: статус отметки не показываем — занятие не
        # состоялось, а причина видна в колонке «Отсутствие преподавателя».
        status_label = (
            str(CANCELLED_MARK)
            if cancelled
            else str(STATUS_LABELS.get(item.status, item.status))
        )
        absence_label = ""
        if cancelled:
            absence = pick_absence(
                by_teacher.get(item.schedule.teacher_id), item.date
            )
            if absence is not None:
                absence_label = str(ABSENCE_SCHEDULE_LABEL) % {
                    "caption": absence_caption(absence)
                }
        worksheet.append(
            [
                item.student.last_name,
                item.student.first_name,
                item.student.middle_name,
                item.date.strftime("%d.%m.%Y"),
                item.schedule.get_day_of_week_display(),
                item.schedule.time_range,
                item.schedule.group.subject.name,
                item.schedule.group.name,
                item.schedule.room.name if item.schedule.room else "",
                item.schedule.group.term.title if item.schedule.group.term else "",
                item.schedule.teacher.full_name if item.schedule.teacher else "",
                item.student.dismissal_short,
                item.topic_title if item.topic_title is not None else "",
                float(hours),
                # Метка статуса — ленивая строка перевода: openpyxl умеет
                # принимать только обычные значения, поэтому str() обязателен.
                status_label,
                absence_label,
            ]
        )
        rows_written += 1
    stream_filter(worksheet, header_row, headers, rows_written)

    summary_headers = [
        "Ученик",
        "Присутствовал",
        "Отсутствовал",
        "Опоздал",
        str(CANCELLED_HEADER),
        "Всего отметок",
        "Часов посещено",
    ]
    summary_sheet, summary_header_row = start_stream_sheet(
        workbook,
        "Сводка",
        summary_headers,
        widths=[32, 16, 15, 12, 12, 16, 16],
        rows_total=len(summary_sorted),
    )
    summary_written = 0
    for name, counts in summary_sorted:
        summary_sheet.append(
            [
                name,
                counts[0],
                counts[1],
                counts[2],
                # Отменённые занятия — отдельной колонкой: в «Отсутствовал» и
                # «Опоздал» они не попадают, но в «Всего отметок» видны.
                counts[5],
                counts[3],
                float(Decimal(counts[4]) / 100),
            ]
        )
        summary_written += 1
    stream_filter(
        summary_sheet, summary_header_row, summary_headers, summary_written
    )

    return workbook


def users_workbook():
    rows = []
    for user in User.objects.all().order_by("role", "last_name"):
        rows.append(
            [
                user.username,
                user.last_name,
                user.first_name,
                user.get_role_display(),
                "да" if user.is_active else "нет",
                "да" if user.is_staff else "нет",
                user.date_joined.strftime("%d.%m.%Y"),
            ]
        )
    return build_workbook(
        [
            {
                "title": "Пользователи",
                "headers": [
                    "Логин",
                    "Фамилия",
                    "Имя",
                    "Роль",
                    "Активен",
                    "Доступ в админку",
                    "Создан",
                ],
                "rows": rows,
                "widths": [20, 20, 15, 20, 10, 20, 14],
            }
        ]
    )
