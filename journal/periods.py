"""Учебные годы и полугодия: определение текущего периода, копирование данных."""
import json
import os
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, ProgrammingError, transaction
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import (
    AcademicYear,
    Attendance,
    Branch,
    Group,
    LessonTopic,
    Room,
    Schedule,
    Subject,
    Term,
)

# Границы полугодий: первое — сентябрь-декабрь, второе — январь-май.
TERM_BOUNDARIES = (
    ((9, 1), (12, 31)),
    ((1, 1), (5, 31)),
)
# Старое название оставлено, чтобы не ломать внешние вызовы.
QUARTER_BOUNDARIES = TERM_BOUNDARIES
TERMS_PER_YEAR = len(TERM_BOUNDARIES)

# Филиалы и их кабинеты: список можно менять в админке.
DEFAULT_BRANCHES = (
    (
        "ул. Примерная, 1",
        (
            "Класс информатики и математики (1 этаж)",
            "Лаборатория биологии",
            "Лаборатория химии",
            "Учебный класс (2 этаж)",
            "Лаборатория физики",
            "Конференц-зал",
        ),
    ),
    ("ул. Вторая, 10", ("№1", "№2", "№11", "№13")),
)

SESSION_YEAR = "active_year_id"
SESSION_TERM = "active_term_id"
SESSION_BRANCH = "active_branch_id"


def academic_year_bounds(day):
    """Учебный год, в который попадает дата: 1 сентября — 31 августа."""
    if day.month >= 9:
        return date(day.year, 9, 1), date(day.year + 1, 8, 31)
    return date(day.year - 1, 9, 1), date(day.year, 8, 31)


def default_term_bounds(year):
    """Границы полугодий для учебного года: сентябрь–декабрь и январь–май.

    Границы не выходят за сам учебный год: год может начинаться позже 1 сентября
    или заканчиваться раньше 31 мая, поэтому календарные границы обрезаем по датам
    года, а полугодия, которые в год совсем не попадают, не создаём. Иначе
    полугодие получало бы даты вне своего года.
    """
    start_year = year.start_date.year
    end_year = year.end_date.year
    bounds = []
    for (start_month, start_day), (end_month, end_day) in TERM_BOUNDARIES:
        first = date(
            start_year if start_month >= 9 else end_year, start_month, start_day
        )
        last = date(end_year if end_month < 9 else start_year, end_month, end_day)
        first = max(first, year.start_date)
        last = min(last, year.end_date)
        if first <= last:
            bounds.append((first, last))
    return bounds


# Старое имя функции: границы, которые использовались для полугодий.
default_quarter_bounds = default_term_bounds


# --- Проверки полугодий -------------------------------------------------------
def _term_fields(item):
    """Достаёт из записи о полугодии (объект или словарь) номер и даты."""
    if isinstance(item, dict):
        getter = item.get
    else:
        getter = lambda name: getattr(item, name, None)  # noqa: E731 - короткий доступ
    return getter("number"), getter("start_date"), getter("end_date")


def check_terms(year, terms):
    """Проверяет предлагаемый набор полугодий учебного года.

    ``terms`` — список словарей или объектов с полями ``number``, ``start_date``,
    ``end_date``. Возвращает список понятных сообщений об ошибках; пустой список
    означает, что данные корректны и их можно сохранять.

    Проверяем всё, что раньше ломало периоды: даты заполнены и идут по порядку,
    полугодие не выходит за границы своего учебного года, номера не повторяются,
    полугодия одного года не пересекаются, а порядок по номеру совпадает с
    порядком по датам.

    Те же проверки сторожат запись: перед сохранением полугодия вызывается
    ``validate_saved_term`` (см. ``guard_term_save``), поэтому неверные даты не
    попадают в базу и «текущее полугодие» с «текущим годом» не расходятся.
    """
    if year is None:
        return ["Не выбран учебный год."]

    errors = []
    items = []
    for index, item in enumerate(terms, start=1):
        number, start_date, end_date = _term_fields(item)
        title = f"№{number}" if isinstance(number, int) else f"строка {index}"
        if not isinstance(number, int) or start_date is None or end_date is None:
            errors.append(f"Полугодие {title}: заполните номер и обе даты.")
            continue
        if number < 1:
            errors.append(
                f"Полугодие {title}: номер полугодия должен быть положительным числом."
            )
            continue
        if end_date <= start_date:
            errors.append(
                f"Полугодие {title}: окончание ({end_date:%d.%m.%Y}) должно быть позже "
                f"начала ({start_date:%d.%m.%Y})."
            )
            continue
        if start_date < year.start_date or end_date > year.end_date:
            errors.append(
                f"Полугодие {title} ({start_date:%d.%m.%Y}–{end_date:%d.%m.%Y}) выходит за "
                f"границы учебного года {year.title} ({year.date_range}). "
                "Укажите даты внутри учебного года."
            )
            continue
        items.append((title, number, start_date, end_date))

    numbers = {}
    for title, number, _start, _end in items:
        if number in numbers:
            errors.append(
                f"Номер {number} указан у двух полугодий: {numbers[number]} и {title}. "
                "Внутри учебного года номера не повторяются."
            )
        else:
            numbers[number] = title

    # Пересечения проверяем по всем парам: полугодий в году немного.
    for first_index in range(len(items)):
        for second_index in range(first_index + 1, len(items)):
            first, second = items[first_index], items[second_index]
            if first[2] <= second[3] and second[2] <= first[3]:
                overlap_start = max(first[2], second[2])
                overlap_end = min(first[3], second[3])
                errors.append(
                    f"Полугодия {first[0]} ({first[2]:%d.%m.%Y}–{first[3]:%d.%m.%Y}) и "
                    f"{second[0]} ({second[2]:%d.%m.%Y}–{second[3]:%d.%m.%Y}) пересекаются: "
                    f"общие дни с {overlap_start:%d.%m.%Y} по {overlap_end:%d.%m.%Y}. "
                    "Полугодия одного учебного года не должны пересекаться."
                )

    ordered = sorted(items, key=lambda row: row[1])
    for index in range(1, len(ordered)):
        previous, current = ordered[index - 1], ordered[index]
        if current[2] <= previous[2] or current[3] <= previous[3]:
            errors.append(
                f"Порядок по номеру не совпадает с порядком по датам: {previous[0]} "
                f"({previous[2]:%d.%m.%Y}–{previous[3]:%d.%m.%Y}), {current[0]} "
                f"({current[2]:%d.%m.%Y}–{current[3]:%d.%m.%Y}). Полугодие с меньшим "
                "номером должно и начинаться, и заканчиваться раньше."
            )
    return errors


def check_term(year, number, start_date, end_date, exclude_pk=None):
    """Проверяет одно полугодие вместе с остальными полугодиями этого года.

    ``exclude_pk`` — запись, которую правим: сама с собой она не сравнивается.
    Возвращает список сообщений об ошибках (пустой — можно сохранять).
    """
    if year is None:
        return ["Не выбран учебный год."]
    items = [
        {"number": number, "start_date": start_date, "end_date": end_date},
    ]
    for other in year.terms.all():
        if exclude_pk is not None and other.pk == exclude_pk:
            continue
        items.append(
            {
                "number": other.number,
                "start_date": other.start_date,
                "end_date": other.end_date,
            }
        )
    return check_terms(year, items)


# Имена, под которыми проверки вызывают разделы журнала.
term_errors = check_term
terms_errors = check_terms


def term_validation_error(term):
    """Понятное сообщение об ошибке в сохраняемом полугодии или None.

    Проверяем по правилам ``check_term``: даты внутри своего учебного года,
    полугодия одного года не пересекаются, порядок номера совпадает с порядком дат.
    """
    year = term.year
    number = term.number
    start_date = term.start_date
    end_date = term.end_date
    if year is None or number is None or start_date is None or end_date is None:
        # Неполные данные отбивает сама модель (обязательные поля), не наша забота.
        return None
    errors = check_term(
        year, number, start_date, end_date, exclude_pk=term.pk or None
    )
    if not errors:
        return None
    return " ".join(errors)


def validate_saved_term(term):
    """Проверяет полугодие перед записью и поднимает ``ValidationError``.

    Вызывается приёмником ``guard_term_save`` (``pre_save`` у ``Term``): неверные
    даты не попадают в базу ни через ORM, ни через формы. Раздел, который правит
    полугодия, ловит ``ValidationError`` и показывает русское сообщение — без
    пятисотой ошибки (см. ``PeriodListView.post``, действие ``save_terms``).
    """
    message = term_validation_error(term)
    if message:
        raise ValidationError(message)


def _demo_alias():
    """Алиас демонстрационной базы (её проверками не мучаем) или None."""
    try:
        from . import demo_router  # локальный импорт: избегаем цикла

        return demo_router.DEMO_ALIAS
    except Exception:  # noqa: BLE001 - модуль демо-режима может быть недоступен
        return None


@receiver(pre_save, sender=Term)
def guard_term_save(sender, instance, using=None, **kwargs):
    """Не даёт сохранить полугодие, которое ломает периоды журнала.

    Проверяем только реальные полугодия рабочей базы: демонстрационную базу и
    состояние «таблиц ещё нет» (миграции, loaddata) пропускаем.
    """
    alias = _demo_alias()
    if alias is not None and using == alias:
        return
    if getattr(instance, "_skip_period_checks", False):
        return
    try:
        validate_saved_term(instance)
    except ValidationError:
        raise
    except (OperationalError, ProgrammingError):
        # Таблиц ещё нет — проверять нечего, запись делает сама база.
        return


# --- Что попадёт в удаление учебного года ------------------------------------
def year_deletion_report(year):
    """Перечисляет, что удалится вместе с учебным годом.

    Раньше год с программами без групп удалялся молча — вместе с программами.
    Возвращаем и числа, и готовую русскую строку, чтобы подтверждение удаления
    было честным.
    """
    empty = {
        "groups": 0,
        "subjects": 0,
        "schedules": 0,
        "attendance": 0,
        "message": "вместе с полугодиями удалять нечего",
    }
    if year is None:
        return empty
    try:
        groups = Group.objects.filter(term__year=year).count()
        subjects = Subject.objects.filter(term__year=year).count()
        schedules = Schedule.objects.filter(group__term__year=year).count()
        attendance = Attendance.objects.filter(schedule__group__term__year=year).count()
    except (OperationalError, ProgrammingError):  # noqa: BLE001 - таблиц ещё нет
        return empty

    parts = []
    if subjects:
        parts.append(f"программ — {subjects}")
    if groups:
        parts.append(f"групп — {groups}")
    if schedules:
        parts.append(f"занятий в расписании — {schedules}")
    if attendance:
        parts.append(f"отметок посещаемости — {attendance}")

    report = {
        "groups": groups,
        "subjects": subjects,
        "schedules": schedules,
        "attendance": attendance,
        "message": (
            "вместе с полугодиями будут удалены " + ", ".join(parts) + "."
            if parts
            else "полугодия года пусты: удалять нечего."
        ),
    }
    return report


def year_object_counts(year):
    """Числа для интерфейса: программы, группы, занятия и отметки года."""
    report = year_deletion_report(year)
    return {
        "subjects": report["subjects"],
        "groups": report["groups"],
        "schedules": report["schedules"],
        "attendance": report["attendance"],
    }


def year_delete_blockers(year):
    """Причины, по которым учебный год удалять нельзя (сейчас — группы)."""
    if year is None:
        return ["Учебный год не найден."]
    if Group.objects.filter(term__year=year).exists():
        return [
            f"Учебный год {year.title} удалить нельзя: в его полугодиях есть группы. "
            "Сначала удалите или перенесите группы — вместе с годом уйдут занятия и отметки."
        ]
    return []


def _optional(name):
    """Необязательный помощник из этого же модуля, если он заведён.

    Пометки «год удалён намеренно» (``mark_year_deleted`` / ``deleted_year_starts`` /
    ``forget_year_deleted``) есть не во всех сборках журнала: если их нет, создание
    года просто не трогает эти пометки и не падает с ``NameError``.
    """
    return globals().get(name)


@transaction.atomic
def create_year(start_date, end_date):
    """Создаёт учебный год с двумя полугодиями (границы можно потом поправить)."""
    year, created = AcademicYear.objects.get_or_create(
        start_date=start_date, end_date=end_date
    )
    # Год завели вручную — снимаем пометку «удалён намеренно»: она нужна только
    # против автоматического создания (см. mark_year_deleted).
    forget = _optional("forget_year_deleted")
    if forget is not None:
        forget(start_date)
    if created or not year.terms.exists():
        for number, (start, end) in enumerate(default_term_bounds(year), start=1):
            Term.objects.get_or_create(
                year=year,
                number=number,
                defaults={"start_date": start, "end_date": end},
            )
    return year, created


def bootstrap_periods(day=None):
    """Создаёт текущий учебный год, если его ещё нет. Безопасно при параллельных запросах."""
    if AcademicYear.objects.exists():
        return False
    day = day or timezone.localdate()
    start, end = academic_year_bounds(day)
    deleted_starts = _optional("deleted_year_starts")
    if deleted_starts is not None and start in deleted_starts():
        # Текущий год удалили намеренно — заново его не создаём.
        return False
    try:
        with transaction.atomic():
            create_year(start, end)
    except (IntegrityError, OperationalError):
        # Другой воркер успел создать год раньше нас — это нормально.
        return False
    return True


@transaction.atomic
def ensure_periods(day=None):
    """Если учебных годов нет — создаёт текущий. Возвращает True, если создал."""
    return bootstrap_periods(day)


def current_year(day=None):
    day = day or timezone.localdate()
    return AcademicYear.objects.filter(
        start_date__lte=day, end_date__gte=day
    ).first()


def current_term(day=None):
    """Полугодие, в которое попадает дата.

    Полугодие берём только из того учебного года, который содержит эту дату:
    иначе при неверно заданных полугодиях «текущее полугодие» относилось бы к
    одному году, а «текущий год» — к другому.
    """
    day = day or timezone.localdate()
    return (
        Term.objects.filter(start_date__lte=day, end_date__gte=day)
        .filter(year__start_date__lte=day, year__end_date__gte=day)
        .select_related("year")
        .first()
    )


def resolve_period(request=None, day=None):
    """Активные учебный год и полугодие: из сессии, иначе по дате сервера."""
    day = day or timezone.localdate()
    if request is None:
        ensure_periods(day)
    year = None
    term = None
    if request is not None:
        term_id = request.session.get(SESSION_TERM)
        if term_id:
            term = Term.objects.filter(pk=term_id).select_related("year").first()
        year_id = request.session.get(SESSION_YEAR)
        if year_id:
            year = AcademicYear.objects.filter(pk=year_id).first()
    if term is not None:
        year = term.year
    if term is None:
        term = current_term(day)
    if year is None:
        year = term.year if term is not None else current_year(day)
    if year is None:
        year = AcademicYear.objects.order_by("-start_date").first()
    if term is None and year is not None:
        term = (
            year.terms.filter(start_date__lte=day, end_date__gte=day).first()
            or year.terms.first()
        )
    return year, term


def set_active_period(request, year=None, term=None):
    """Запоминает выбранный период у пользователя (в сессии)."""
    if term is not None:
        year = term.year
    if year is not None:
        request.session[SESSION_YEAR] = year.pk
        request.session[SESSION_TERM] = term.pk if term is not None else None


# --- Филиалы (адреса) --------------------------------------------------------
@transaction.atomic
def seed_branches():
    """Создаёт филиалы с кабинетами, если их ещё нет."""
    for branch_order, (name, rooms) in enumerate(DEFAULT_BRANCHES):
        branch, _ = Branch.objects.get_or_create(
            name=name, defaults={"order": branch_order}
        )
        for room_order, room_name in enumerate(rooms):
            Room.objects.get_or_create(
                branch=branch, name=room_name, defaults={"order": room_order}
            )


def ensure_branches():
    if Branch.objects.exists():
        return False
    seed_branches()
    return True


def resolve_branch(request=None):
    """Активный адрес (филиал): из сессии, иначе первый по порядку."""
    ensure_branches()
    branch = None
    if request is not None:
        branch_id = request.session.get(SESSION_BRANCH)
        if branch_id:
            branch = Branch.objects.filter(pk=branch_id).first()
    if branch is None:
        branch = Branch.objects.order_by("order", "name").first()
    return branch


def set_active_branch(request, branch):
    if branch is not None:
        request.session[SESSION_BRANCH] = branch.pk


# --- Перенос данных между полугодиями и адресами ------------------------------
@transaction.atomic
def copy_term_data(
    source,
    target,
    group_ids=None,
    with_students=True,
    with_schedule=True,
    source_branch=None,
    target_branch=None,
):
    """Переносит программы, группы (с составом) и расписание в другую полугодие.

    Программы в каждом полугодии и каждом адресе свои, поэтому перенос создаёт их
    в целевом полугодии и целевом адресе. Повторный запуск ничего не дублирует:
    программа ищется по названию, группа — по программе и названию, занятие — по дню
    и времени начала.

    Перенос внутри одного полугодия (в другой адрес) не должен мешать себе сам:
    при проверке занятости преподавателя и кабинета исходное занятие исключается
    по ``exclude_pk``, иначе оно конфликтует само с собой и не переносится ни одно
    занятие. Настоящие конфликты в целевом адресе по-прежнему отбиваются.
    """
    from .availability import room_conflict, teacher_conflict  # локальный импорт: избегаем цикла

    if target_branch is None:
        target_branch = Branch.objects.order_by("order", "name").first()
    if source_branch is None:
        source_branch = target_branch

    report = {
        "subjects_created": 0,
        "groups_created": 0,
        "groups_reused": 0,
        "students_added": 0,
        "lessons_created": 0,
        "lessons_skipped": 0,
        # Разовые занятия привязаны к дате и в другое полугодие не переносятся.
        "one_off_skipped": 0,
        "conflicts": [],
    }

    groups = source.groups.select_related("subject", "subject__teacher").prefetch_related(
        "students", "schedules", "subject__teachers"
    )
    if source_branch is not None:
        groups = groups.filter(subject__branch=source_branch)
    if group_ids:
        groups = groups.filter(pk__in=group_ids)

    subjects = {}
    for group in groups:
        source_subject = group.subject
        key = source_subject.name.strip().lower()
        target_subject = subjects.get(key)
        if target_subject is None:
            target_subject = Subject.objects.filter(
                term=target, branch=target_branch, name__iexact=source_subject.name
            ).first()
            if target_subject is None:
                target_subject = Subject.objects.create(
                    name=source_subject.name,
                    teacher=source_subject.teacher,
                    term=target,
                    branch=target_branch,
                )
                report["subjects_created"] += 1
            # Переносим и подменяющих преподавателей программы.
            extra = [item.pk for item in source_subject.teacher_options]
            if extra:
                target_subject.teachers.add(*extra)
            subjects[key] = target_subject

        target_group, created = Group.objects.get_or_create(
            term=target, subject=target_subject, name=group.name
        )
        report["groups_created" if created else "groups_reused"] += 1

        if with_students:
            existing = set(target_group.students.values_list("pk", flat=True))
            for student in group.students.all():
                if student.pk not in existing:
                    target_group.students.add(student)
                    report["students_added"] += 1

        if not with_schedule:
            continue

        for lesson in group.schedules.all():
            if lesson.is_one_off:
                # Разовое занятие относится к конкретной дате: в другом полугодии
                # такой даты нет, поэтому просто пропускаем его.
                report["one_off_skipped"] += 1
                continue

            if Schedule.objects.filter(
                group=target_group,
                day_of_week=lesson.day_of_week,
                start_time=lesson.start_time,
            ).exists():
                report["lessons_skipped"] += 1
                continue

            conflict = teacher_conflict(
                lesson.teacher,
                lesson.day_of_week,
                lesson.start_time,
                lesson.end_time,
                exclude_pk=lesson.pk,
                term=target,
            )
            if conflict is not None:
                report["lessons_skipped"] += 1
                report["conflicts"].append(
                    f"{lesson.get_day_of_week_display()}, {lesson.time_range} — "
                    f"{group.subject.name} · {group.name}: у преподавателя "
                    f"{lesson.teacher.full_name} уже занятие "
                    f"{conflict.group.subject.name} · {conflict.group.name}"
                )
                continue

            # Кабинет ищем в целевом адресе: кабинеты у адресов свои.
            room = None
            if lesson.room_id:
                room = Room.objects.filter(
                    branch=target_branch, name=lesson.room.name
                ).first()

            busy_room = room_conflict(
                room,
                lesson.day_of_week,
                lesson.start_time,
                lesson.end_time,
                exclude_pk=lesson.pk,
                term=target,
            )
            if busy_room is not None:
                report["lessons_skipped"] += 1
                report["conflicts"].append(
                    f"{lesson.get_day_of_week_display()}, {lesson.time_range} — "
                    f"{group.subject.name} · {group.name}: кабинет "
                    f"{room.name} занят "
                    f"{busy_room.group.subject.name} · {busy_room.group.name}"
                )
                continue

            Schedule.objects.create(
                group=target_group,
                day_of_week=lesson.day_of_week,
                start_time=lesson.start_time,
                end_time=lesson.end_time,
                teacher=lesson.teacher,
                branch=target_branch,
                room=room,
                ignore_teacher_conflict=lesson.ignore_teacher_conflict,
            )
            report["lessons_created"] += 1

    return report


#: Дата последней проверки: годы проверяем не чаще раза в сутки на процесс.
_YEARS_CHECKED_ON = None


def ensure_academic_years(today=None, force=False):
    """Создаёт учебные годы вокруг текущего — вручную их заводить не нужно.

    Проверка дешёвая и вызывается на страницах: в 2028 году сам появится
    2028–2029, а прошлые годы остаются на месте вместе с историей.
    """
    global _YEARS_CHECKED_ON

    today = today or timezone.localdate()
    if not force and _YEARS_CHECKED_ON == today:
        return []
    _YEARS_CHECKED_ON = today

    number = today.year if today.month >= 9 else today.year - 1
    created = []
    try:
        # Два года вперёд: расписание и группы готовят заранее, поэтому следующий
        # учебный год должен существовать ещё до 1 сентября.
        for value in (number - 1, number, number + 1, number + 2):
            start = date(value, 9, 1)
            # Год мог быть заведён вручную с другими датами — ищем по году начала,
            # иначе получился бы дубль (например, 02.09 и 01.09 одного и того же года).
            year = AcademicYear.objects.filter(start_date__year=value).first()
            if year is None:
                year = AcademicYear.objects.create(
                    start_date=start, end_date=date(value + 1, 8, 31)
                )
                created.append(value)
            if not year.terms.exists():
                Term.objects.create(
                    year=year, number=1, start_date=start, end_date=date(value, 12, 31)
                )
                Term.objects.create(
                    year=year, number=2, start_date=date(value + 1, 1, 1),
                    end_date=date(value + 1, 5, 31),
                )
    except (OperationalError, IntegrityError):
        # Таблиц ещё нет или год успел создаться в другом процессе — не мешаем странице.
        return created
    return created
