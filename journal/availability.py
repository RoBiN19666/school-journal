"""Свободные окна преподавателя и занятость кабинетов — для формы занятия."""
from datetime import date, datetime, time, timedelta

from django.db.models import Exists, OuterRef, Q

from .absences import absence_on
from .models import Schedule, TeacherAbsence

# Рабочее окно, внутри которого показываем свободное время.
DAY_START = time(8, 0)
DAY_END = time(21, 0)
# Длительность занятия по умолчанию для автоподстановки окончания.
DEFAULT_DURATION_MINUTES = 90


def occurs_on_condition(day):
    """Условие запроса «занятие проходит в этот день» (дата — объект date).

    Обычное занятие бывает каждую неделю в свой день, разовое — только в свою дату.
    """
    return Q(lesson_date=day) | Q(lesson_date__isnull=True, day_of_week=day.weekday())


def occurs_on_weekday_condition(day_of_week):
    """Условие запроса «занятие проходит в этот день недели» (0 — понедельник).

    Нужно, когда конкретной даты нет — например, сохраняется обычное недельное
    занятие. У разового занятия день недели считается от даты (``Schedule.save``),
    поэтому одно условие покрывает и обычные, и разовые занятия. Разовое занятие
    мешает обычному: обычное повторяется каждую неделю, значит в дату разового они
    совпадут по времени.
    """
    return Q(day_of_week=day_of_week)


def _combine(value):
    return datetime.combine(datetime(2000, 1, 1), value)


def end_after(start, minutes=DEFAULT_DURATION_MINUTES):
    """Время окончания, если начали в start и занимаемся minutes минут."""
    return (_combine(start) + timedelta(minutes=minutes)).time()


def teacher_conflict(
    teacher, day_of_week, start_time, end_time, exclude_pk=None, term=None, day=None
):
    """Занятие преподавателя, пересекающееся по времени, или None.

    Проверяем внутри одного полугодия: в другом полугодии то же время свободно.
    Занятия-подмены (галочка «не учитывать занятость преподавателя») не мешают.
    Если передана конкретная дата (day), разовые занятия других дат не считаются.
    Без даты проверка идёт по дню недели: мешают и обычные занятия этого дня, и
    разовые, попадающие на него, — недельное занятие повторяется каждую неделю и
    в дату разового совпадёт с ним по времени (см. ``occurs_on_weekday_condition``).

    Отсутствие преподавателя на проверяемую дату (командировка, больничный)
    снимает конфликт по нему: занятие в этот день он не ведёт, поэтому на его
    время можно поставить подмену другого педагога. Проверки кабинета и группы
    это не ослабляет — они считаются как обычно (``room_conflict`` и форма).
    """
    if teacher is None or not start_time or not end_time:
        return None
    if day is not None and absence_on(teacher, day) is not None:
        return None
    queryset = Schedule.objects.filter(
        teacher=teacher,
        start_time__lt=end_time,
        end_time__gt=start_time,
        ignore_teacher_conflict=False,
    ).select_related("group__subject")
    if day is not None:
        queryset = queryset.filter(occurs_on_condition(day))
    else:
        # Проверяем обычное занятие: считаем по дню недели, поэтому разовые
        # занятия этого дня тоже мешают — они проходят в свою дату, а обычное
        # занятие повторяется каждую неделю и совпадёт с ними по времени.
        queryset = queryset.filter(occurs_on_weekday_condition(day_of_week))
    if term is not None:
        queryset = queryset.filter(group__term=term)
    # Отменённое занятие (преподаватель отсутствует) времени не занимает: при
    # проверке по дате выше мы уже вернули None, а при проверке по дню недели
    # здесь отсеиваются разовые занятия, попавшие в период отсутствия.
    queryset = exclude_cancelled(queryset, day)
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.first()


def exclude_cancelled(queryset, day):
    """Убирает из занятий отменённые: преподаватель отсутствует в этот день.

    Отменённое занятие не проводится — значит, ни группа, ни кабинет в это время
    не заняты, и на место отменённого занятия можно поставить подмену. Без этого
    форма видела бы конфликт с самим отменённым занятием: у группы «уже есть
    занятие», а кабинет «занят». Проверка остальных занятий не ослабляется —
    мешают ровно те занятия, которые действительно проходят.

    Без конкретной даты (``day is None``) у обычного занятия даты нет, но у разового
    она есть: разовое занятие, попадающее в период отсутствия своего преподавателя,
    тоже не проводится и времени не занимает — иначе отменённое занятие мешало бы
    поставить обычное в тот же день недели.
    """
    if day is None:
        absent = TeacherAbsence.objects.filter(
            teacher_id=OuterRef("teacher_id"),
            date_from__lte=OuterRef("lesson_date"),
            date_to__gte=OuterRef("lesson_date"),
        )
        # Условие внутри NOT EXISTS: у обычного занятия lesson_date пуст, сравнение
        # даёт NULL, подзапрос ничего не находит — обычные занятия остаются.
        return queryset.exclude(Exists(absent))
    absent = TeacherAbsence.objects.filter(
        date_from__lte=day, date_to__gte=day
    ).values_list("teacher_id", flat=True)
    return queryset.exclude(teacher_id__in=list(absent))


def room_conflict(
    room, day_of_week, start_time, end_time, exclude_pk=None, term=None, day=None
):
    """Занятие, которое уже занимает этот кабинет в указанное время.

    Если передана конкретная дата (day), учитываются обычные занятия этого дня недели
    и разовые занятия именно на эту дату. Без даты проверка идёт по дню недели, и
    разовые занятия этого дня тоже мешают: недельное занятие совпадёт с ними в дату
    разового. Отменённые занятия (преподаватель отсутствует в этот день) кабинет не
    занимают: см. ``exclude_cancelled``.
    """
    if room is None or not start_time or not end_time:
        return None
    queryset = Schedule.objects.filter(
        room=room,
        start_time__lt=end_time,
        end_time__gt=start_time,
    ).select_related("group__subject")
    if day is not None:
        queryset = queryset.filter(occurs_on_condition(day))
    else:
        # Дня нет (сохраняем недельное занятие): считаем по дню недели, поэтому
        # разовое занятие этого дня тоже занимает кабинет в свою дату.
        queryset = queryset.filter(occurs_on_weekday_condition(day_of_week))
    if term is not None:
        queryset = queryset.filter(group__term=term)
    queryset = exclude_cancelled(queryset, day)
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.first()


#: Границы учебного дня для оценки занятости кабинета.
DAY_START_ROOM = time(8, 0)
DAY_END_ROOM = time(21, 0)
USABLE_GAP_MINUTES = 90


def room_status(busy_lessons):
    """Свободен весь день, занят частично или занят весь день."""
    if not busy_lessons:
        return "free"
    windows = []
    cursor = datetime.combine(date.today(), DAY_START_ROOM)
    for lesson in sorted(busy_lessons, key=lambda item: item.start_time):
        start = datetime.combine(date.today(), lesson.start_time)
        end = datetime.combine(date.today(), lesson.end_time)
        if (start - cursor).total_seconds() >= USABLE_GAP_MINUTES * 60:
            windows.append((cursor, start))
        cursor = max(cursor, end)
    finish = datetime.combine(date.today(), DAY_END_ROOM)
    if (finish - cursor).total_seconds() >= USABLE_GAP_MINUTES * 60:
        windows.append((cursor, finish))
    return "partial" if windows else "full"


def room_day_usage(branch, day_of_week, term=None, exclude_pk=None, day=None):
    """Занятость кабинетов филиала на выбранный день.

    Если известна конкретная дата (day) — учитываем разовые занятия только этой
    даты. Отменённые занятия кабинет не занимают — иначе подсказка на форме
    противоречила бы проверке при сохранении (см. ``exclude_cancelled``).
    """
    if branch is None:
        return []
    lessons = Schedule.objects.filter(branch=branch).select_related("group__subject")
    if day is not None:
        lessons = lessons.filter(occurs_on_condition(day))
    else:
        # Дня нет: показываем занятость по дню недели, вместе с разовыми занятиями
        # этого дня недели (у них день недели считается от даты).
        lessons = lessons.filter(occurs_on_weekday_condition(day_of_week))
    if term is not None:
        lessons = lessons.filter(group__term=term)
    lessons = exclude_cancelled(lessons, day)
    if exclude_pk:
        lessons = lessons.exclude(pk=exclude_pk)

    by_room = {}
    for lesson in lessons:
        if lesson.room_id:
            by_room.setdefault(lesson.room_id, []).append(lesson)

    usage = []
    for room in branch.rooms.order_by("order", "name"):
        busy = sorted(by_room.get(room.pk, []), key=lambda item: item.start_time)
        usage.append(
            {
                "room": room,
                "status": room_status(busy),
                "status_label": {
                    "free": "свободен весь день",
                    "partial": "занят частично",
                    "full": "занят весь день",
                }[room_status(busy)],
                "busy": [
                    {
                        "time": lesson.time_range,
                        "subject": lesson.group.subject.name,
                        "group": lesson.group.name,
                        # У разового занятия видно дату: оно занимает кабинет только в неё.
                        "date_label": lesson.date_label,
                    }
                    for lesson in busy
                ],
            }
        )
    return usage


def teacher_availability(teacher, exclude_pk=None, term=None, day=None):
    """По дням недели: занятые занятия преподавателя и свободные окна между ними.

    Учитываются только занятия указанного полугодия. Занятия-подмены
    (галочка «не учитывать занятость») в занятое время не попадают.

    У разового занятия в подписи видно его дату (``date_label``: «разовое · 13.01.2025»),
    иначе по занятому окну непонятно, к какому дню оно относится. У обычного занятия
    подпись прежняя: время, программа, группа и место.

    Если передана конкретная дата (day), панель считается по ней: видны занятия
    этой даты (обычные этого дня недели и разовые именно на неё), а разовые
    занятия других дат того же дня недели окна не занимают. Отменённое занятие
    (преподаватель отсутствует в этот день) день не занимает — как и в проверке
    при сохранении (см. ``exclude_cancelled``).
    """
    if teacher is None:
        return []

    queryset = Schedule.objects.filter(
        teacher=teacher, ignore_teacher_conflict=False
    ).select_related("group__subject", "group__term", "branch", "room")
    if term is not None:
        queryset = queryset.filter(group__term=term)
    if day is not None:
        queryset = queryset.filter(occurs_on_condition(day))
        queryset = exclude_cancelled(queryset, day)
    lessons = list(queryset)
    if exclude_pk:
        lessons = [lesson for lesson in lessons if lesson.pk != exclude_pk]

    days = []
    for value, label in Schedule.Weekday.choices:
        busy = sorted(
            (lesson for lesson in lessons if lesson.day_of_week == value),
            key=lambda lesson: lesson.start_time,
        )
        windows = []
        cursor = DAY_START
        for lesson in busy:
            if lesson.start_time > cursor:
                windows.append((cursor, min(lesson.start_time, DAY_END)))
            cursor = max(cursor, lesson.end_time)
        if cursor < DAY_END:
            windows.append((cursor, DAY_END))

        days.append(
            {
                "value": value,
                "label": label,
                "busy": [
                    {
                        "time": lesson.time_range,
                        "subject": lesson.group.subject.name,
                        "group": lesson.group.name,
                        # У разового занятия в подписи видна его дата: без неё
                        # непонятно, к какому дню относится занятое окно.
                        "date_label": lesson.date_label,
                        "place": (
                            (
                                lesson.branch.name
                                if lesson.branch_id
                                else ""
                            )
                            + (
                                (", каб. " if lesson.branch_id else "каб. ")
                                + lesson.room.name
                                if lesson.room_id
                                else ""
                            )
                        ),
                    }
                    for lesson in busy
                ],
                "windows": [
                    {
                        "start": f"{start:%H:%M}",
                        "end": f"{end:%H:%M}",
                        "label": f"{start:%H:%M}–{end:%H:%M}",
                        "minutes": int(
                            (_combine(end) - _combine(start)).total_seconds() // 60
                        ),
                    }
                    for start, end in windows
                    if start < end
                ],
                "free_minutes": sum(
                    int((_combine(end) - _combine(start)).total_seconds() // 60)
                    for start, end in windows
                    if start < end
                ),
            }
        )
    return days
