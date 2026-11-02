"""Подмены сразу на весь период отсутствия преподавателя.

Обычный путь подмены — по одному занятию (кнопка «Поставить подмену» в недельном
журнале). Здесь собран пакетный сценарий раздела «Отсутствия»: на период
показываются все занятия преподавателя по конкретным датам, для каждой строки
считается занятость заменяющего, кабинета и группы, а сохранение идёт одной
транзакцией.

Правила те же, что у формы занятия (``journal.forms.ScheduleForm``) и
``journal.availability``:

* обычное занятие даёт столько строк, сколько раз его день недели попадает в
  период (внутри полугодия группы); разовое — одну строку, если его дата внутри
  периода. Отменённое отсутствием занятие тоже показывается: именно его и
  заменяют;
* отменённое занятие времени не занимает, поэтому своя же отменённая пара
  подмене не мешает (``availability.exclude_cancelled``);
* занятость заменяющего снимается галочкой «не учитывать занятость», занятость
  кабинета и группы — нет;
* дубли не создаются: подмена — разовое занятие, а у группы на одну дату и время
  может быть только одно разовое занятие (``unique_group_one_off_start``).
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .absences import absences_by_teacher, pick_absence
from .availability import occurs_on_condition
from .models import Schedule, Subject, User


def day_range(start, end):
    """Даты периода по порядку: обе границы входят в период."""
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def lesson_window(schedule, absence):
    """Отрезок, в котором занятие бывает: период отсутствия ∩ полугодие группы.

    За пределами полугодия занятий нет, поэтому «каникулярные» даты периода в
    план не попадают.
    """
    term = schedule.group.term
    start, end = absence.date_from, absence.date_to
    if term is not None:
        start = max(start, term.start_date)
        end = min(end, term.end_date)
    return start, end


def absence_occurrences(absence):
    """[(занятие, дата)] занятий отсутствующего внутри периода, по порядку дат.

    Обычное занятие разворачивается в конкретные даты — по каждому вхождению дня
    недели в период; разовое даёт одну строку со своей датой. Отменённое
    отсутствием занятие остаётся в списке: его и надо заменить.
    """
    schedules = (
        Schedule.objects.filter(teacher_id=absence.teacher_id)
        .select_related("group__subject", "group__term", "branch", "room")
        .order_by("day_of_week", "start_time", "pk")
    )
    rows = []
    for schedule in schedules:
        start, end = lesson_window(schedule, absence)
        if start > end:
            continue
        if schedule.lesson_date is not None:
            if start <= schedule.lesson_date <= end:
                rows.append((schedule, schedule.lesson_date))
            continue
        for day in day_range(start, end):
            if day.weekday() == schedule.day_of_week:
                rows.append((schedule, day))
    rows.sort(key=lambda item: (item[1], item[0].start_time, item[0].pk))
    return rows


def row_key(schedule, day):
    """Ключ строки плана: занятие и дата.

    По этому ключу приходят поля формы (``teacher_<ключ>``, ``apply_<ключ>``,
    ``ignore_<ключ>``), поэтому он один для страницы, представления и тестов.
    """
    return f"{schedule.pk}:{day.isoformat()}"


def busy_by_day(rows, teacher_ids=()):
    """Занятость по датам и полугодиям — для проверки конфликтов подмены.

    ``rows`` — пары (занятие, дата) из ``absence_occurrences``: по ним известны
    нужные даты и полугодия. ``teacher_ids`` — кандидаты в заменяющие: по ним
    дополнительно собираются периоды отсутствия, ведь заменяющий не может
    провести занятие в свой день отсутствия.

    Возвращает ``{(полугодие, дата): {"teacher": {id: [занятия]},
    "room": {...}, "group": {...}, "absent": {id: период}}}``.

    Отменённые занятия в занятость не попадают — они не проводятся
    (``availability.exclude_cancelled``). Занятие с галочкой «не учитывать
    занятость» не занимает время преподавателя, но кабинет и группу занимает:
    так же считает форма занятия.
    """
    days = sorted({day for _schedule, day in rows})
    term_ids = {
        schedule.group.term_id
        for schedule, _day in rows
        if schedule.group.term_id
    }
    if not days or not term_ids:
        return {}

    lessons = list(
        Schedule.objects.filter(group__term_id__in=term_ids).select_related(
            "teacher", "group__subject", "group__term", "branch", "room"
        )
    )
    candidates = {lesson.teacher_id for lesson in lessons if lesson.teacher_id}
    candidates.update(item for item in teacher_ids if item)
    absences = absences_by_teacher(candidates, days[0], days[-1])

    day_set = set(days)
    weekly = {}
    one_off = {}
    for lesson in lessons:
        term_id = lesson.group.term_id
        if lesson.lesson_date is not None:
            if lesson.lesson_date in day_set:
                one_off.setdefault((term_id, lesson.lesson_date), []).append(lesson)
        else:
            weekly.setdefault((term_id, lesson.day_of_week), []).append(lesson)

    absent_by_day = {
        day: {
            teacher_id: absence
            for teacher_id, periods in absences.items()
            if (absence := pick_absence(periods, day)) is not None
        }
        for day in days
    }

    busy = {}
    for day in days:
        for term_id in term_ids:
            day_map = {"teacher": {}, "room": {}, "group": {}, "absent": absent_by_day[day]}
            busy[(term_id, day)] = day_map
            lessons_today = [
                *weekly.get((term_id, day.weekday()), ()),
                *one_off.get((term_id, day), ()),
            ]
            for lesson in lessons_today:
                # Отменённое занятие времени не занимает: преподаватель в этот
                # день отсутствует, группа и кабинет свободны.
                if lesson.teacher_id in absent_by_day[day]:
                    continue
                if not lesson.ignore_teacher_conflict:
                    day_map["teacher"].setdefault(lesson.teacher_id, []).append(lesson)
                if lesson.room_id:
                    day_map["room"].setdefault(lesson.room_id, []).append(lesson)
                day_map["group"].setdefault(lesson.group_id, []).append(lesson)
    return busy


def _overlaps(lesson, schedule):
    """Пересекаются ли занятия по времени."""
    return lesson.start_time < schedule.end_time and lesson.end_time > schedule.start_time


def lesson_label(lesson):
    """«10:00–11:30 — Программа · Группа 1» — чем занято это время."""
    return _("%(time)s — %(subject)s · %(group)s") % {
        "time": lesson.time_range,
        "subject": lesson.group.subject.name,
        "group": lesson.group.name,
    }


def substitution_place(lesson):
    """Место занятия для поиска подмены: группа, дата и время начала (или None).

    Подмена — разовое занятие, а у группы на одну дату и одно время бывает
    только одно разовое занятие (``unique_group_one_off_start``), поэтому место
    однозначно определяет подмену.
    """
    if lesson.lesson_date is None:
        return None
    return (lesson.group_id, lesson.lesson_date, lesson.start_time)


def substitution_map(start, end):
    """Подмены отрезка по местам: ``{(группа, дата, начало): занятие}`` — один запрос.

    По этому ключу недельный журнал и «Мои занятия» находят, что на месте
    отменённого занятия уже стоит подмена: тогда кнопка «Поставить подмену» не
    показывается и дубль поставить нечем.
    """
    found = {}
    for lesson in Schedule.objects.filter(
        is_substitution=True, lesson_date__range=(start, end)
    ).select_related("teacher", "group__subject"):
        found[substitution_place(lesson)] = lesson
    return found


def replaced_lessons(substitutions):
    """``{pk подмены: (заменяемый преподаватель, период отсутствия)}``.

    Если у подмены заполнено поле ``replaced_teacher`` — берём его: это и есть
    тот, кого заменяют, даже если исходное занятие потом перенесли, удалили или
    преподаватель менялся прямо в записи разового занятия (см.
    ``create_substitutions``).

    Для старых записей поле пусто, и работает прежний поиск по месту: занятие
    той же группы, даты и времени, но другого преподавателя — обычное занятие
    этого дня недели или другое разовое на ту же дату. Период отсутствия в обоих
    случаях берётся у заменяемого (``absences.pick_absence``): если периодов
    несколько, в подпись попадает тот, что накрывает дату занятия.

    Без поля и без пары по месту вернётся ``(None, None)``.
    """
    substitutions = [item for item in substitutions if item.lesson_date is not None]
    if not substitutions:
        return {}
    days = {item.lesson_date for item in substitutions}
    # Явно указанные заменяемые: имена и их отсутствия — двумя запросами.
    explicit_ids = {
        item.replaced_teacher_id for item in substitutions if item.replaced_teacher_id
    }
    explicit_people = User.objects.in_bulk(explicit_ids)
    explicit_absences = (
        absences_by_teacher(explicit_ids, min(days), max(days)) if explicit_ids else {}
    )

    condition = Q()
    for day in days:
        condition |= occurs_on_condition(day)
    candidates = list(
        Schedule.objects.filter(group_id__in={item.group_id for item in substitutions})
        .filter(condition)
        .select_related("teacher", "group__subject")
    )
    absences = absences_by_teacher(
        {item.teacher_id for item in candidates}, min(days), max(days)
    )
    found = {}
    for substitution in substitutions:
        if substitution.replaced_teacher_id:
            found[substitution.pk] = (
                explicit_people.get(substitution.replaced_teacher_id),
                pick_absence(
                    explicit_absences.get(substitution.replaced_teacher_id),
                    substitution.lesson_date,
                ),
            )
            continue
        match = (None, None)
        for candidate in candidates:
            if candidate.pk == substitution.pk or candidate.group_id != substitution.group_id:
                continue
            if candidate.teacher_id == substitution.teacher_id:
                continue
            if (
                candidate.lesson_date is not None
                and candidate.lesson_date != substitution.lesson_date
            ):
                continue
            if not _overlaps(candidate, substitution):
                continue
            absence = pick_absence(
                absences.get(candidate.teacher_id), substitution.lesson_date
            )
            if absence is None:
                # Заменяемый в этот день не отсутствует: это не та пара, которую
                # заменяют (например, соседнее занятие группы в другое время).
                continue
            match = (candidate.teacher, absence)
            break
        found[substitution.pk] = match
    return found


def program_teachers(subject_ids):
    """``{id программы: набор id преподавателей программы}`` — двумя запросами.

    Это тот же список, что ``Subject.teacher_options``: основной преподаватель
    программы и дополнительные (раздел «Программы» → «Преподаватели программы»).
    Занятие может вести только кто-то из них, поэтому странице подмен нужен весь
    набор сразу: по строкам и по кандидатам в заменяющие.
    """
    ids = {item for item in subject_ids if item}
    if not ids:
        return {}
    found = {}
    for subject in Subject.objects.filter(pk__in=ids).prefetch_related("teachers"):
        allowed = {item.pk for item in subject.teachers.all()}
        if subject.teacher_id:
            allowed.add(subject.teacher_id)
        found[subject.pk] = allowed
    return found


def conflicts_for(schedule, day, teacher, busy, allowed=None):
    """Конфликты подмены на конкретную дату.

    ``schedule`` — заменяемое занятие, ``day`` — дата, ``teacher`` — выбранный
    заменяющий (может быть ``None``), ``busy`` — занятость из ``busy_by_day``,
    ``allowed`` — набор id преподавателей программы занятия (``program_teachers``):
    занятие ведёт только тот, кто за программой закреплён.

    Возвращает ``{"teacher": [...], "room": [...], "group": [...],
    "absent": период или None, "program": преподаватель или None}``. Занятость
    заменяющего — мягкий конфликт: его снимает галочка «не учитывать занятость».
    Занятость кабинета и группы, отсутствие самого заменяющего и «не из программы»
    снять нельзя.
    """
    result = {"teacher": [], "room": [], "group": [], "absent": None, "program": None}
    if teacher is None:
        return result
    if allowed is not None and teacher.pk not in allowed:
        result["program"] = teacher
    day_map = busy.get((schedule.group.term_id, day))
    if day_map is None:
        return result
    result["teacher"] = [
        lesson
        for lesson in day_map["teacher"].get(teacher.pk, ())
        if _overlaps(lesson, schedule)
    ]
    if schedule.room_id:
        result["room"] = [
            lesson
            for lesson in day_map["room"].get(schedule.room_id, ())
            if _overlaps(lesson, schedule)
        ]
    result["group"] = [
        lesson
        for lesson in day_map["group"].get(schedule.group_id, ())
        if _overlaps(lesson, schedule)
    ]
    result["absent"] = day_map["absent"].get(teacher.pk)
    return result


def hard_conflict(conflicts):
    """Конфликты, которые галочкой «не учитывать занятость» не снимаются."""
    return bool(
        conflicts["room"]
        or conflicts["group"]
        or conflicts["absent"]
        or conflicts["program"]
    )


def soft_conflict(conflicts):
    """Занятость заменяющего: снимается галочкой «не учитывать занятость»."""
    return bool(conflicts["teacher"])


def conflict_reasons(schedule, conflicts):
    """Строки о конфликтах строки плана: сначала неустранимые, потом занятость.

    Тексты те же по смыслу, что у формы занятия: методист видит, что именно
    занято и почему строку нельзя сохранить как есть.
    """
    reasons = []
    if conflicts["program"] is not None:
        reasons.append(
            _(
                "%(teacher)s не ведёт программу «%(subject)s»: занятие может вести "
                "только преподаватель программы."
            )
            % {
                "teacher": conflicts["program"].full_name,
                "subject": schedule.group.subject.name,
            }
        )
    if conflicts["absent"] is not None:
        reasons.append(
            _("Заменяющий отсутствует в этот день: %(absence)s.")
            % {"absence": conflicts["absent"].title}
        )
    for lesson in conflicts["room"]:
        reasons.append(
            _("Кабинет «%(room)s» занят: %(lesson)s.")
            % {"room": schedule.room.name, "lesson": lesson_label(lesson)}
        )
    for lesson in conflicts["group"]:
        reasons.append(
            _("У группы уже есть занятие: %(lesson)s.") % {"lesson": lesson_label(lesson)}
        )
    for lesson in conflicts["teacher"]:
        reasons.append(
            _("Занят: %(lesson)s.") % {"lesson": lesson_label(lesson)}
        )
    return reasons


def row_conflicts(schedule, day, teachers, busy, allowed=None):
    """``{id заменяющего: {"hard": [...], "soft": [...]}}`` для строки плана.

    Считается по всем кандидатам сразу: страница отдаёт готовые причины в
    шаблоне, чтобы подсветка не требовала запроса на каждую смену выбора.
    ``allowed`` — преподаватели программы занятия (``program_teachers``).
    """
    found = {}
    for teacher in teachers:
        conflicts = conflicts_for(schedule, day, teacher, busy, allowed=allowed)
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
        if hard or soft:
            found[teacher.pk] = {"hard": hard, "soft": soft}
    return found


def existing_one_offs(rows):
    """``{(группа, дата, начало): разовое занятие}`` — одним запросом.

    Подмена — разовое занятие, а у группы на одну дату и одно время может быть
    только одно разовое занятие (``unique_group_one_off_start``). Поэтому «уже
    стоит» проверяется по группе, дате и времени, а не по заменяющему: второй
    подмены на это место база не примет.
    """
    pairs = {(schedule.group_id, day) for schedule, day in rows}
    if not pairs:
        return {}
    found = {}
    for lesson in Schedule.objects.filter(
        group_id__in={group_id for group_id, _day in pairs},
        lesson_date__in={day for _group_id, day in pairs},
    ).select_related("teacher", "group__subject"):
        found[(lesson.group_id, lesson.lesson_date, lesson.start_time)] = lesson
    return found


def create_substitutions(decisions, busy, known_one_offs, allowed_by_subject=None):
    """Создаёт подмены по решениям пользователя одной транзакцией.

    ``decisions`` — список словарей ``{"schedule": занятие, "date": дата,
    "teacher": заменяющий или None, "apply": bool, "ignore": bool}`` в порядке
    дат. ``busy`` и ``known_one_offs`` — заранее посчитанные занятость и уже
    стоящие разовые занятия (см. ``busy_by_day`` и ``existing_one_offs``).
    ``allowed_by_subject`` — ``{id программы: набор id преподавателей}``
    (``program_teachers``): занятие ведёт только преподаватель программы.

    Разовое занятие не дублируется: у него уже есть своя дата и время, поэтому
    преподаватель заменяется у самого занятия (``is_substitution``), иначе
    база не примет второе разовое занятие на то же место.

    Возвращает отчёт: ``created`` (создано подмен), ``already`` (уже стояли),
    ``skipped`` (снята галочка), ``conflicts`` (мешает занятость, кабинет,
    группа или преподаватель не из программы), ``errors`` (не выбран заменяющий)
    и даты этих строк.
    """
    report = {
        "created": 0,
        "already": 0,
        "skipped": 0,
        "conflicts": 0,
        "errors": 0,
        "already_rows": [],
        "conflict_rows": [],
        "error_rows": [],
    }
    to_create = []
    to_update = []
    created_keys = set()
    for decision in decisions:
        schedule, day = decision["schedule"], decision["date"]
        label = f"{day:%d.%m.%Y}"
        if not decision["apply"]:
            report["skipped"] += 1
            continue
        teacher = decision["teacher"]
        if teacher is None:
            report["errors"] += 1
            report["error_rows"].append(label)
            continue
        key = (schedule.group_id, day, schedule.start_time)
        existing = known_one_offs.get(key)
        if key in created_keys or (existing is not None and existing.pk != schedule.pk):
            report["already"] += 1
            report["already_rows"].append(label)
            continue
        conflicts = conflicts_for(
            schedule,
            day,
            teacher,
            busy,
            allowed=(allowed_by_subject or {}).get(schedule.group.subject_id),
        )
        if hard_conflict(conflicts) or (
            soft_conflict(conflicts) and not decision["ignore"]
        ):
            report["conflicts"] += 1
            report["conflict_rows"].append(label)
            continue
        created_keys.add(key)
        report["created"] += 1
        if schedule.is_one_off:
            # Разовое занятие не дублируем: преподаватель меняется в самой записи,
            # а прежний сохраняется в «кого заменяют» — иначе он терялся бы.
            schedule.replaced_teacher = schedule.teacher
            schedule.teacher = teacher
            schedule.is_substitution = True
            schedule.ignore_teacher_conflict = bool(decision["ignore"])
            to_update.append(schedule)
        else:
            to_create.append(
                Schedule(
                    group=schedule.group,
                    branch=schedule.branch,
                    room=schedule.room,
                    day_of_week=day.weekday(),
                    lesson_date=day,
                    start_time=schedule.start_time,
                    end_time=schedule.end_time,
                    teacher=teacher,
                    # Кого заменяем — преподаватель исходного занятия.
                    replaced_teacher=schedule.teacher,
                    is_substitution=True,
                    ignore_teacher_conflict=bool(decision["ignore"]),
                )
            )
    with transaction.atomic():
        for lesson in to_update:
            lesson.save(
                update_fields=[
                    "teacher",
                    "replaced_teacher",
                    "is_substitution",
                    "ignore_teacher_conflict",
                ]
            )
        for lesson in to_create:
            lesson.save()
    return report


def report_skipped(report):
    """Сколько строк не создано: всё, кроме созданных."""
    return report["already"] + report["skipped"] + report["conflicts"] + report["errors"]


def report_text(report):
    """Подробности для сообщений: «13.01.2025, 20.01.2025» по группам причин."""
    return {
        "already": ", ".join(report["already_rows"]),
        "conflicts": ", ".join(report["conflict_rows"]),
        "errors": ", ".join(report["error_rows"]),
    }
