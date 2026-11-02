"""Отсутствия преподавателей: пометки в отчётах, часы и снятие конфликта.

Волна 2: период отсутствия (командировка, больничный) влияет на отчётные экраны.
Здесь собраны запросы по датам, чтобы недельный журнал, кабинет преподавателя,
страница отметок и расписание считали одно и то же одинаково.

Формула попадания дня в период — та же, что и в ``TeacherAbsence.clean``::

    TeacherAbsence.objects.filter(teacher=..., date_from__lte=день, date_to__gte=день)

Обе даты входят в период, поэтому сравнение нестрогое с обеих сторон.
"""
from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import TeacherAbsence


def pick_absence(absences, day):
    """Первый период из готового списка, накрывающий день (или None).

    Списки периодов заранее выбираются одним запросом (``absences_by_teacher``):
    так недельный журнал и расписание обходятся без запроса на каждое занятие.
    """
    if day is None:
        return None
    for absence in absences or ():
        if absence.date_from <= day <= absence.date_to:
            return absence
    return None


def absence_on(teacher, day, absences=None):
    """Отсутствие преподавателя в конкретный день или None.

    ``teacher`` — пользователь или его идентификатор. ``absences`` — уже
    выбранные периоды этого преподавателя: если список передан, запрос в базу
    не делается (пустой список означает «периодов нет»).
    """
    if day is None:
        return None
    if absences is None:
        teacher_id = getattr(teacher, "pk", teacher)
        if not teacher_id:
            return None
        absences = TeacherAbsence.objects.filter(
            teacher_id=teacher_id, date_from__lte=day, date_to__gte=day
        )
    return pick_absence(absences, day)


def absences_by_teacher(teacher_ids, start=None, end=None):
    """Периоды отсутствия нескольких преподавателей: {id: [периоды по порядку]}.

    ``start``/``end`` — границы отрезка, с которым периоды должны пересекаться.
    Пересечение, а не вхождение: отсутствие «с 12.10 по 20.10» относится и к
    неделе 14–19 октября, хотя её границы внутрь периода не попадают.
    """
    ids = {int(item) for item in teacher_ids if item}
    if not ids:
        return {}
    queryset = TeacherAbsence.objects.filter(teacher_id__in=ids)
    if start is not None:
        queryset = queryset.filter(date_to__gte=start)
    if end is not None:
        queryset = queryset.filter(date_from__lte=end)
    found = {}
    for absence in queryset.order_by("date_from", "pk"):
        found.setdefault(absence.teacher_id, []).append(absence)
    return found


def current_week_bounds(today=None):
    """Понедельник и воскресенье недели, в которую попадает сегодняшний день.

    Расписание показывает **текущую неделю**: разовые занятия других недель в
    него не попадают, а пометка об отсутствии — только если период ещё не
    закончился (см. ``_absence_for_schedule``).
    """
    if today is None:
        today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def _absence_visible(absence, today, week_start, week_end):
    """Относится ли период отсутствия к показанной неделе.

    Правило простое: показываем то, что относится к выбранной неделе, — какой бы
    она ни была. На прошлой неделе видно прошлый больничный и прошлые подмены
    (методист смотрит назад, чтобы понять, что было), на текущей — только
    текущее. Дополнительно на текущей неделе показываем период, который ещё не
    начался: это полезное предупреждение на будущее.
    """
    if absence.date_from <= week_end and absence.date_to >= week_start:
        # Период пересекается с показанной неделей.
        return True
    # Ещё не начался: предупреждаем о нём только на текущей неделе.
    return week_start <= today <= week_end and absence.date_from > week_end


def _absence_for_schedule(absences, schedule, today, week_start, week_end):
    """Период для пометки занятия в расписании.

    Разовое занятие знает свою дату — помечаем, только если дата внутри периода.
    У обычного занятия даты нет, поэтому берём ближайший период внутри полугодия
    группы. Показываем то, что относится к выбранной неделе (см.
    ``_absence_visible``): на прошлой неделе — прошлый больничный, на текущей —
    текущий и ближайший будущий.
    """
    if not absences:
        return None
    if schedule.is_one_off:
        absence = pick_absence(absences, schedule.lesson_date)
        if absence is None or not _absence_visible(
            absence, today, week_start, week_end
        ):
            return None
        return absence
    term = getattr(schedule.group, "term", None)
    start = getattr(term, "start_date", None)
    end = getattr(term, "end_date", None)
    inside = [
        absence
        for absence in absences
        if (start is None or absence.date_to >= start)
        and (end is None or absence.date_from <= end)
        and _absence_visible(absence, today, week_start, week_end)
    ]
    if not inside:
        return None
    # Период самой показанной недели важнее остальных: на прошлой неделе это
    # прошлый больничный, на текущей — текущий.
    on_week = [
        absence
        for absence in inside
        if absence.date_from <= week_end and absence.date_to >= week_start
    ]
    if on_week:
        return on_week[0]
    # Остались только будущие: показываем ближайший как предупреждение.
    upcoming = [absence for absence in inside if absence.date_to >= today]
    return upcoming[0] if upcoming else inside[-1]


def absence_caption(absence, compact=False):
    """Период отсутствия в виде «Больничный 13.01–17.01» (или с годами).

    ``compact`` — короткая запись без годов: она идёт в подписи картинки
    расписания, где строка узкая. Без ``compact`` даты печатаются полностью —
    так подпись понятна в выгрузках и в списках.
    """
    if compact:
        period = _("%(start)s–%(end)s") % {
            "start": f"{absence.date_from:%d.%m}",
            "end": f"{absence.date_to:%d.%m}",
        }
    else:
        period = _("с %(start)s по %(end)s") % {
            "start": f"{absence.date_from:%d.%m.%Y}",
            "end": f"{absence.date_to:%d.%m.%Y}",
        }
    return f"{absence.get_kind_display()} {period}"


def absence_captions_by_teacher(teacher_ids, start=None, end=None, compact=False):
    """{id преподавателя: подпись периода отсутствия} на отрезке полугодия.

    Подпись собирается через ``absence_caption``: одна и та же строка идёт в
    картинку расписания и в выгрузки. Если у преподавателя несколько периодов на
    отрезке, показываются все — через запятую.
    """
    found = absences_by_teacher(teacher_ids, start, end)
    return {
        teacher_id: ", ".join(
            str(absence_caption(absence, compact=compact)) for absence in absences
        )
        for teacher_id, absences in found.items()
    }


def schedule_absence_labels(schedules, today=None, week=None):
    """{pk занятия: период отсутствия} для пометок в расписании.

    График недельный и конкретной даты у обычного занятия нет — см.
    ``_absence_for_schedule``. ``week`` — показанная неделя ``(понедельник,
    воскресенье)``; по умолчанию текущая. Прошедшие периоды отсутствия не
    показываются: расписание — про показанную неделю и ближайшее будущее.
    """
    schedules = list(schedules)
    if today is None:
        today = timezone.localdate()
    week_start, week_end = week or current_week_bounds(today)
    by_teacher = absences_by_teacher({item.teacher_id for item in schedules})
    labels = {}
    for item in schedules:
        absence = _absence_for_schedule(
            by_teacher.get(item.teacher_id), item, today, week_start, week_end
        )
        if absence is not None:
            labels[item.pk] = absence
    return labels
