"""Резервная копия данных журнала и восстановление из неё (JSON).

Копия содержит данные журнала и настройки сайта и почты. Логотип, фавикон и
пароль SMTP в неё не входят. Копии прежнего формата без раздела ``settings``
восстанавливаются как раньше — этот раздел необязателен.
"""
import json
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from . import periods as periods_tools
from .models import (
    AcademicYear,
    Attendance,
    Branch,
    ContestResult,
    Group,
    LessonTopic,
    MailSettings,
    Room,
    Schedule,
    SiteSettings,
    Student,
    Subject,
    TeacherDevelopment,
    Term,
    User,
)

FORMAT = "school_journal.backup"
VERSION = 10

#: Разделы, без которых копия не считается копией журнала. В старых копиях
#: каких-то из них могло не быть — такие разделы перечислены в LEGACY_SECTIONS.
REQUIRED_SECTIONS = (
    "academic_years",
    "terms",
    "users",
    "students",
    "subjects",
    "groups",
    "schedules",
    "attendance",
)
#: Разделы, которые появились позже и в старых копиях отсутствуют.
LEGACY_SECTIONS = ("branches", "rooms", "topics", "contests", "development")
#: Разделы, которые копия обязана содержать. Пустая копия (все разделы пусты)
#: не должна иметь права обнулять журнал.
DATA_SECTIONS = REQUIRED_SECTIONS + LEGACY_SECTIONS

#: Сколько отметок посещаемости пишем в базу за один запрос.
ATTENDANCE_BATCH_SIZE = 2000

#: Роли администраторов журнала: главный администратор распоряжается главным
#: администратором — в том числе его восстановлением из копии.
ADMIN_ROLES = {User.Role.SUPERADMIN, User.Role.ADMIN}

#: Что сообщаем администратору, если копия содержала главного администратора.
SUPERADMIN_SKIP_MESSAGE = (
    "В копии есть главный администратор — эта часть пропущена. "
    "Восстановить её может только главный администратор."
)

VALID_STATUSES = {value for value, _ in Attendance.Status.choices}
VALID_DISMISSAL = {value for value, _ in Student.Dismissal.choices}
VALID_CONTEST_STATUSES = {value for value, _ in ContestResult.Status.choices}
VALID_DEVELOPMENT_KINDS = {value for value, _ in TeacherDevelopment.Kind.choices}

#: Настройки сайта, которые входят в копию. Логотип и фавикон — файлы в
#: ``media_data``: они в копию не попадают (как и описано в README).
SITE_SETTINGS_FIELDS = (
    "organization_name",
    "product_name",
    "short_logo",
    "login_hint",
    "remember_days",
    "session_hours",
)

#: Настройки почты в копии. Пароль SMTP (``password_encrypted``) не входит
#: в копию ни в каком виде.
MAIL_SETTINGS_FIELDS = (
    "host",
    "port",
    "username",
    "use_tls",
    "use_ssl",
    "from_email",
    "recipients",
    "notify_logins",
    "notify_failed_logins",
    "notify_ip_blocked",
    "notify_backup",
    "notify_accounts",
)

# Как восстановить время занятия из старой копии, где был только номер урока.
LEGACY_DAY_START = time(9, 0)
LEGACY_LESSON_LENGTH = timedelta(minutes=90)


# --- Создание копии ----------------------------------------------------------
def build_backup():
    """Собирает все данные журнала в один словарь."""
    years = list(AcademicYear.objects.order_by("start_date"))
    terms = list(
        Term.objects.select_related("year").order_by("year__start_date", "number")
    )
    branches = list(Branch.objects.order_by("order", "name"))
    rooms = list(Room.objects.select_related("branch").order_by("branch__order", "order", "name"))
    students = list(Student.objects.order_by("pk"))
    subjects = list(
        Subject.objects.select_related("teacher").prefetch_related("teachers").order_by("pk")
    )
    groups = list(
        Group.objects.select_related("subject")
        .prefetch_related("students")
        .order_by("pk")
    )
    schedules = list(
        Schedule.objects.select_related("teacher", "group").order_by("pk")
    )
    attendance = list(Attendance.objects.order_by("pk"))
    topics = list(LessonTopic.objects.order_by("pk"))
    contests = list(ContestResult.objects.order_by("pk"))
    development = list(TeacherDevelopment.objects.order_by("pk"))
    # Настройки читаем без get_or_create: сборка копии не должна менять базу.
    site = SiteSettings.objects.filter(pk=1).first() or SiteSettings()
    mail = MailSettings.objects.filter(pk=1).first() or MailSettings()

    student_index = {item.pk: index for index, item in enumerate(students)}
    subject_index = {item.pk: index for index, item in enumerate(subjects)}
    group_index = {item.pk: index for index, item in enumerate(groups)}
    schedule_index = {item.pk: index for index, item in enumerate(schedules)}
    year_index = {item.pk: index for index, item in enumerate(years)}
    term_index = {item.pk: index for index, item in enumerate(terms)}
    branch_index = {item.pk: index for index, item in enumerate(branches)}
    room_index = {item.pk: index for index, item in enumerate(rooms)}

    backup = {
        "format": FORMAT,
        "version": VERSION,
        "created_at": timezone.localtime().isoformat(timespec="seconds"),
        "academic_years": [
            {
                "start_date": item.start_date.isoformat(),
                "end_date": item.end_date.isoformat(),
            }
            for item in years
        ],
        "terms": [
            {
                "year": year_index.get(item.year_id),
                "number": item.number,
                "start_date": item.start_date.isoformat(),
                "end_date": item.end_date.isoformat(),
            }
            for item in terms
        ],
        "branches": [
            {"name": item.name, "order": item.order} for item in branches
        ],
        "rooms": [
            {
                "branch": branch_index.get(item.branch_id),
                "name": item.name,
                "order": item.order,
            }
            for item in rooms
        ],
        "users": [
            {
                "username": user.username,
                "last_name": user.last_name,
                "first_name": user.first_name,
                "middle_name": user.middle_name,
                "role": user.role,
                "is_active": user.is_active,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "password": user.password,
            }
            for user in User.objects.order_by("pk")
        ],
        "students": [
            {
                # Устойчивый идентификатор: по нему однофамильцы не склеиваются.
                "key": item.pk,
                "last_name": item.last_name,
                "first_name": item.first_name,
                "middle_name": item.middle_name,
                "birth_date": item.birth_date.isoformat() if item.birth_date else None,
                "dismissal_status": item.dismissal_status,
            }
            for item in students
        ],
        "subjects": [
            {
                "name": item.name,
                "teacher": item.teacher.username if item.teacher else None,
                "teachers": [user.username for user in item.teachers.all() if user.username],
                "branch": branch_index.get(item.branch_id),
                "term": term_index.get(item.term_id),
            }
            for item in subjects
        ],
        "groups": [
            {
                "name": item.name,
                "subject": subject_index.get(item.subject_id),
                "term": term_index.get(item.term_id),
                "students": sorted(
                    student_index[student.pk]
                    for student in item.students.all()
                    if student.pk in student_index
                ),
            }
            for item in groups
        ],
        "schedules": [
            {
                "group": group_index.get(item.group_id),
                "day_of_week": item.day_of_week,
                "lesson_date": (
                    item.lesson_date.isoformat() if item.lesson_date else None
                ),
                "start_time": item.start_time.strftime("%H:%M"),
                "end_time": item.end_time.strftime("%H:%M"),
                "teacher": item.teacher.username if item.teacher else None,
                "branch": branch_index.get(item.branch_id),
                "room": room_index.get(item.room_id),
                "ignore_teacher_conflict": bool(item.ignore_teacher_conflict),
                "is_substitution": bool(item.is_substitution),
            }
            for item in schedules
        ],
        "attendance": [
            {
                "schedule": schedule_index.get(item.schedule_id),
                "student": student_index.get(item.student_id),
                "date": item.date.isoformat(),
                "status": item.status,
            }
            for item in attendance
        ],
        "topics": [
            {
                "schedule": schedule_index.get(item.schedule_id),
                "date": item.date.isoformat(),
                "topic": item.topic,
                "hours": float(item.hours),
                "teacher": item.teacher.username if item.teacher else None,
            }
            for item in topics
        ],
        "contests": [
            {
                "student": student_index.get(item.student_id),
                "date": item.date.isoformat(),
                "title": item.title,
                "status": item.status,
                "subject": item.subject,
                "comment": item.comment,
            }
            for item in contests
        ],
        "development": [
            {
                "teacher": item.teacher.username if item.teacher else None,
                "kind": item.kind,
                "date": item.date.isoformat(),
                "program": item.program,
                "organization": item.organization,
                "hours": item.hours,
                "result": item.result,
            }
            for item in development
        ],
        # Настройки сайта и почты. Пароль SMTP сюда не попадает ни в каком виде:
        # в копии только поля, перечисленные в MAIL_SETTINGS_FIELDS.
        "settings": {
            "site": {name: getattr(site, name) for name in SITE_SETTINGS_FIELDS},
            "mail": {name: getattr(mail, name) for name in MAIL_SETTINGS_FIELDS},
        },
    }
    backup["counts"] = {
        "academic_years": len(backup["academic_years"]),
        "terms": len(backup["terms"]),
        "branches": len(backup["branches"]),
        "rooms": len(backup["rooms"]),
        "users": len(backup["users"]),
        "students": len(backup["students"]),
        "subjects": len(backup["subjects"]),
        "groups": len(backup["groups"]),
        "schedules": len(backup["schedules"]),
        "attendance": len(backup["attendance"]),
        "topics": len(backup["topics"]),
        "contests": len(backup["contests"]),
        "development": len(backup["development"]),
    }
    return backup


def dumps(backup):
    return json.dumps(backup, ensure_ascii=False, indent=2)


def _section_counts(data):
    """Сколько записей в каждом разделе копии."""
    counts = {}
    for name in DATA_SECTIONS:
        value = data.get(name)
        counts[name] = len(value) if isinstance(value, list) else 0
    return counts


def _backup_total(data):
    return sum(_section_counts(data).values())


def _db_totals():
    """Сколько записей сейчас в базе — чтобы отчёт сравнивался с копией."""
    return {
        "academic_years": AcademicYear.objects.count(),
        "terms": Term.objects.count(),
        "branches": Branch.objects.count(),
        "rooms": Room.objects.count(),
        "users": User.objects.count(),
        "students": Student.objects.count(),
        "subjects": Subject.objects.count(),
        "groups": Group.objects.count(),
        "schedules": Schedule.objects.count(),
        "attendance": Attendance.objects.count(),
        "topics": LessonTopic.objects.count(),
        "contests": ContestResult.objects.count(),
        "development": TeacherDevelopment.objects.count(),
    }


def section_counts(data):
    """Публичный доступ к счётчикам разделов копии (для отчёта в интерфейсе)."""
    return _section_counts(data)


def db_totals():
    """Публичный доступ к текущим счётчикам базы (для отчёта в интерфейсе)."""
    return _db_totals()


def validate(data, require_data=False):
    """Проверяет копию перед восстановлением.

    require_data=True (режим «полная замена») дополнительно требует, чтобы копия
    была непустой: иначе даже формально корректный файл обнулил бы журнал.
    """
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(
            "Файл не похож на резервную копию журнала "
            "(ожидается JSON, созданный кнопкой «Скачать копию»)."
        )

    version = data.get("version")
    if version != VERSION:
        if isinstance(version, int) and version < VERSION:
            raise ValueError(
                f"Копия версии {version} не поддерживается: текущий формат — "
                f"{VERSION}. Скачайте свежую копию или обновите её вручную."
            )
        raise ValueError(
            "Не удалось определить версию копии: ожидается формат "
            f"{VERSION}. Восстановление отменено, данные не изменены."
        )

    sections = _section_counts(data)
    broken = [name for name in REQUIRED_SECTIONS if not isinstance(data.get(name), list)]
    if broken:
        raise ValueError(
            "Копия повреждена или неполна: нет обязательных разделов "
            f"({', '.join(broken)}). Восстановление отменено, данные не изменены."
        )

    total = sum(sections.values())
    if require_data and total == 0:
        raise ValueError(
            "Копия пуста: в ней нет ни одной записи. Полная замена отменена, "
            "данные журнала не изменены."
        )
    return sections


def loads(text):
    """Проверяет файл копии: формат, версия, обязательные разделы."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"Файл не является корректным JSON: {error}") from error
    validate(data)
    return data



# --- Восстановление ----------------------------------------------------------
def _at(items, index):
    if isinstance(index, int) and 0 <= index < len(items):
        return items[index]
    return None


def _parse_date(value):
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_time(value):
    """Время из копии: «10:00», «10:00:00» или пусто."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        return None


def _schedule_times(item):
    """Время занятия из копии. В старых копиях был только номер урока."""
    start = _parse_time(item.get("start_time"))
    end = _parse_time(item.get("end_time"))
    if start is None:
        number = item.get("lesson_number")
        if isinstance(number, int) and number > 0:
            start = (
                datetime.combine(date(2000, 1, 1), LEGACY_DAY_START)
                + (number - 1) * LEGACY_LESSON_LENGTH
            ).time()
    if start is None:
        return None, None
    if start.hour >= 23:
        return None, None
    if end is None or end <= start:
        end = (datetime.combine(date(2000, 1, 1), start) + LEGACY_LESSON_LENGTH).time()
    return start, end


def _find_user(username):
    if not username:
        return None
    return User.objects.filter(username=username).first()


# --- Настройки при восстановлении -------------------------------------------
def _copy_text(value, limit=None):
    """Строка из копии; None — пустая строка, limit — предел длины поля."""
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit] if limit else text


def _copy_number(value, current, maximum=None):
    """Неотрицательное число из копии; битое значение — оставляем текущее."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return current
    if number < 0:
        return current
    if maximum is not None and number > maximum:
        return maximum
    return number


def _copy_bool(value, current):
    """Да/нет из копии; не bool (например, строка) — оставляем текущее."""
    return value if isinstance(value, bool) else current


def _restore_site_settings(payload):
    """Применяет настройки сайта. Логотип и фавикон копия не содержит."""
    if not isinstance(payload, dict):
        return
    site = SiteSettings.load()
    changed = False
    # (предел длины, можно ли записать пустое значение)
    text_fields = {
        "organization_name": (200, False),
        "product_name": (100, False),
        "short_logo": (4, False),
        "login_hint": (200, True),
    }
    for name, (limit, allow_empty) in text_fields.items():
        if name not in payload:
            continue
        value = _copy_text(payload[name], limit)
        if value or allow_empty:
            setattr(site, name, value)
            changed = True
    if "remember_days" in payload:
        site.remember_days = _copy_number(
            payload["remember_days"], site.remember_days
        )
        changed = True
    if "session_hours" in payload:
        site.session_hours = _copy_number(
            payload["session_hours"], site.session_hours
        )
        changed = True
    if changed:
        site.save()


def _restore_mail_settings(payload):
    """Применяет настройки почты. Пароль SMTP не читается и не затирается."""
    if not isinstance(payload, dict):
        return
    # Без get_or_create: если в копии нет ни одного известного поля, лишняя
    # запись в базе не появляется.
    mail = MailSettings.objects.filter(pk=1).first() or MailSettings()
    changed = False
    for name, limit in (("host", 200), ("username", 200), ("from_email", 200)):
        if name in payload:
            setattr(mail, name, _copy_text(payload[name], limit))
            changed = True
    if "recipients" in payload:
        mail.recipients = _copy_text(payload["recipients"])
        changed = True
    if "port" in payload:
        mail.port = _copy_number(payload["port"], mail.port, maximum=65535)
        changed = True
    for name in (
        "use_tls",
        "use_ssl",
        "notify_logins",
        "notify_failed_logins",
        "notify_ip_blocked",
        "notify_backup",
        "notify_accounts",
    ):
        if name in payload:
            setattr(mail, name, _copy_bool(payload[name], getattr(mail, name)))
            changed = True
    if changed:
        # password_encrypted намеренно не трогаем: пароль SMTP в копию не
        # попадает, а уже заданный в системе должен остаться как был.
        mail.save()


def restore(backup, mode="merge", actor=None):
    """Восстанавливает данные. mode: merge (добавить/обновить) или replace."""
    # Защита от битой копии: до удаления данных проверяем разделы и версию.
    # Для «полной замены» пустая копия — отказ, а не обнуление журнала.
    sections = validate(backup, require_data=(mode == "replace"))

    # Сколько записей в базе сейчас: попадает в отчёт рядом с содержимым копии —
    # администратор видит, что именно заменяется или добавляется.
    db_before = _db_totals()

    counts = {
        "academic_years": 0,
        "terms": 0,
        "branches": 0,
        "rooms": 0,
        "users": 0,
        "students": 0,
        "subjects": 0,
        "groups": 0,
        "schedules": 0,
        "attendance": 0,
        "topics": 0,
        "contests": 0,
        "development": 0,
        "skipped": 0,
        "superadmin_skipped": 0,
    }

    # Главного администратора из копии восстанавливает только сам главный
    # администратор: иначе роль из файла подняла бы права обычному администратору.
    actor_is_superadmin = bool(getattr(actor, "is_superadmin", False))
    backup_totals = dict(sections)
    backup_total = sum(sections.values())

    with transaction.atomic():
        if mode == "replace":
            LessonTopic.objects.all().delete()
            ContestResult.objects.all().delete()
            TeacherDevelopment.objects.all().delete()
            Attendance.objects.all().delete()
            Schedule.objects.all().delete()
            Group.objects.all().delete()
            Subject.objects.all().delete()
            Student.objects.all().delete()
            # Учётные записи при «полной замене» НЕ удаляем осознанно: иначе
            # преподаватель или методист, которого в копии нет, пропадал бы
            # вместе со своими занятиями и курсами (каскад). Данные журнала
            # заменяются полностью, а люди только добавляются и обновляются
            # из копии — см. блок «Пользователи» ниже.

        # Пользователи
        for item in backup.get("users") or []:
            username = (item.get("username") or "").strip()
            if not username:
                counts["skipped"] += 1
                continue
            user = User.objects.filter(username=username).first()
            if actor is not None and user is not None and user.pk == actor.pk:
                continue  # свою учётную запись восстановление не меняет
            role = item.get("role")
            if role not in {choice.value for choice in User.Role}:
                role = User.Role.TEACHER.value
            if role == User.Role.SUPERADMIN.value and not actor_is_superadmin:
                # Часть копии про главного администратора пропускаем целиком:
                # ни создать, ни повысить такого пользователя нельзя.
                counts["superadmin_skipped"] += 1
                counts["skipped"] += 1
                continue
            # Роль, пароль и включённость уже существующего администратора копия
            # не переписывает: иначе администратор отобрал бы доступ у главного.
            protected_admin = user is not None and (
                user.is_superuser or user.role in ADMIN_ROLES
            )
            if user is None:
                user = User(username=username)
                password = item.get("password")
                if password:
                    user.password = password
                else:
                    user.set_unusable_password()
                user.is_superuser = False  # суперпользователя создают отдельно
            # Имя и фамилия обязательны. В копии старого образца их может не
            # быть — тогда берём логин, чтобы учётная запись осталась узнаваемой
            # и её можно было сохранить из карточки.
            user.last_name = item.get("last_name") or username
            user.first_name = item.get("first_name") or username
            user.middle_name = (item.get("middle_name") or "").strip()[:100]
            if not protected_admin:
                user.role = role
                user.is_active = bool(item.get("is_active", True))
                user.is_staff = (
                    bool(item.get("is_staff")) or role == User.Role.ADMIN.value
                )
            user.save()
            counts["users"] += 1

        # Учебные годы и полугодия
        years = []
        for item in backup.get("academic_years") or []:
            start = _parse_date(item.get("start_date"))
            end = _parse_date(item.get("end_date"))
            if start is None or end is None:
                years.append(None)
                counts["skipped"] += 1
                continue
            year, _ = AcademicYear.objects.get_or_create(
                start_date=start, end_date=end
            )
            years.append(year)
            counts["academic_years"] += 1

        terms = []
        for item in backup.get("terms") or []:
            year = _at(years, item.get("year"))
            start = _parse_date(item.get("start_date"))
            end = _parse_date(item.get("end_date"))
            number = item.get("number")
            if (
                year is None
                or start is None
                or end is None
                or not isinstance(number, int)
            ):
                terms.append(None)
                counts["skipped"] += 1
                continue
            # Даты уже существующего полугодия копия не меняет: merge не должен
            # переписывать границы периода, к которому уже привязаны занятия.
            term, _ = Term.objects.get_or_create(
                year=year,
                number=number,
                defaults={"start_date": start, "end_date": end},
            )
            terms.append(term)
            counts["terms"] += 1

        # Адреса и кабинеты
        branches = []
        for item in backup.get("branches") or []:
            name = (item.get("name") or "").strip()
            if not name:
                branches.append(None)
                counts["skipped"] += 1
                continue
            branch, _ = Branch.objects.get_or_create(
                name=name, defaults={"order": item.get("order") or 0}
            )
            branches.append(branch)
            counts["branches"] += 1

        if not branches:
            # Копии старых версий без адресов — берём текущие.
            periods_tools.seed_branches()
            branches = list(Branch.objects.order_by("order", "name"))

        rooms = []
        for item in backup.get("rooms") or []:
            branch = _at(branches, item.get("branch"))
            name = (item.get("name") or "").strip()
            if branch is None or not name:
                rooms.append(None)
                counts["skipped"] += 1
                continue
            room, _ = Room.objects.get_or_create(
                branch=branch, name=name, defaults={"order": item.get("order") or 0}
            )
            rooms.append(room)
            counts["rooms"] += 1

        fallback_branch = branches[0] if branches else None

        # Копии старых версий не содержат периодов — группы попадут в текущую полугодие.
        fallback_term = terms[0] if terms else None
        if not fallback_term:
            periods_tools.ensure_periods()
            _, fallback_term = periods_tools.resolve_period()

        # В копиях версии 5 и старше у программы не было полугодия: берём его у его групп.
        subject_term_hint = {}
        for item in backup.get("groups") or []:
            index = item.get("subject")
            group_term = _at(terms, item.get("term"))
            if index is not None and group_term is not None:
                subject_term_hint.setdefault(index, group_term)

        # Ученики. Сначала ищем по устойчивому ключу из копии (pk ученика) —
        # иначе однофамильцы склеивались бы в одного и отметки терялись.
        # По ФИО ищем только как запасной вариант: когда ключа в копии нет
        # (старый формат) или когда такого ключа в этой базе не оказалось —
        # например, копию восстанавливают на другом сервере, где pk другие.
        # Если в копии несколько полных тёзок, по ФИО не сопоставляем вовсе:
        # иначе два разных ученика снова слились бы в одного.
        students = []
        used_student_pks = set()
        twin_names = {}
        for item in backup.get("students") or []:
            full_name = (
                (item.get("last_name") or ""),
                (item.get("first_name") or ""),
                (item.get("middle_name") or ""),
            )
            twin_names[full_name] = twin_names.get(full_name, 0) + 1
        for item in backup.get("students") or []:
            birth_date = _parse_date(item.get("birth_date"))
            dismissal = item.get("dismissal_status")
            if dismissal not in VALID_DISMISSAL:
                dismissal = Student.Dismissal.NONE.value
            if not item.get("last_name"):
                students.append(None)
                counts["skipped"] += 1
                continue
            key = item.get("key")
            full_name = (
                (item.get("last_name") or ""),
                (item.get("first_name") or ""),
                (item.get("middle_name") or ""),
            )
            student = None
            if isinstance(key, int) and key not in used_student_pks:
                student = Student.objects.filter(pk=key).first()
            if student is None and twin_names.get(full_name) == 1:
                # Копия без ключа или ключ не нашёлся: прежнее поведение —
                # сопоставление по ФИО.
                student = Student.objects.filter(
                    last_name=full_name[0],
                    first_name=full_name[1],
                    middle_name=full_name[2],
                ).first()
            if student is None:
                student = Student(
                    last_name=full_name[0],
                    first_name=full_name[1],
                    middle_name=full_name[2],
                    birth_date=birth_date,
                    dismissal_status=dismissal,
                )
                student.save()
            else:
                used_student_pks.add(student.pk)
            changed = []
            if student.birth_date is None and birth_date is not None:
                student.birth_date = birth_date
                changed.append("birth_date")
            if (
                student.dismissal_status != dismissal
                and student.dismissal_status == Student.Dismissal.NONE.value
            ):
                student.dismissal_status = dismissal
                changed.append("dismissal_status")
            if changed:
                student.save(update_fields=changed)
            students.append(student)
            counts["students"] += 1

        # Программы. В копиях версии 5 и старше адреса и полугодия не было —
        # такие программы попадают в полугодие своих групп и в первый адрес.
        subjects = []
        for index, item in enumerate(backup.get("subjects") or []):
            name = (item.get("name") or "").strip()
            if not name:
                subjects.append(None)
                counts["skipped"] += 1
                continue
            subject_branch = _at(branches, item.get("branch")) or fallback_branch
            subject_term = (
                _at(terms, item.get("term"))
                or subject_term_hint.get(index)
                or fallback_term
            )
            if subject_branch is None or subject_term is None:
                subjects.append(None)
                counts["skipped"] += 1
                continue
            teacher = _find_user(item.get("teacher"))
            subject, _ = Subject.objects.get_or_create(
                name=name,
                branch=subject_branch,
                term=subject_term,
                defaults={"teacher": teacher},
            )
            if teacher is not None and subject.teacher_id != teacher.pk:
                subject.teacher = teacher
                subject.save(update_fields=["teacher"])
            # Преподаватели программы, включая подменяющих.
            for username in item.get("teachers") or []:
                person = _find_user(username)
                if person is not None:
                    subject.teachers.add(person)
            if teacher is not None:
                subject.teachers.add(teacher)
            subjects.append(subject)
            counts["subjects"] += 1

        # Группы
        groups = []
        for item in backup.get("groups") or []:
            subject = _at(subjects, item.get("subject"))
            # Группа всегда в полугодии своего программы.
            term = (subject.term if subject is not None else None) or _at(
                terms, item.get("term")
            ) or fallback_term
            name = (item.get("name") or "").strip()
            if subject is None or term is None or not name:
                groups.append(None)
                counts["skipped"] += 1
                continue
            group, _ = Group.objects.get_or_create(
                term=term, subject=subject, name=name
            )
            for index in item.get("students") or []:
                student = _at(students, index)
                if student is not None:
                    group.students.add(student)
            groups.append(group)
            counts["groups"] += 1

        # Расписание
        schedules = []
        for item in backup.get("schedules") or []:
            group = _at(groups, item.get("group"))
            try:
                day_of_week = int(item.get("day_of_week"))
            except (TypeError, ValueError):
                schedules.append(None)
                counts["skipped"] += 1
                continue
            start_time, end_time = _schedule_times(item)
            # Разовое занятие восстанавливаем вместе с его датой; пустая или битая
            # дата — значит обычное занятие, повторяющееся каждую неделю.
            raw_date = item.get("lesson_date")
            lesson_date = _parse_date(raw_date) if raw_date else None
            teacher = _find_user(item.get("teacher"))
            if teacher is None and group is not None:
                teacher = group.subject.teacher
            if group is None or teacher is None or start_time is None:
                schedules.append(None)
                counts["skipped"] += 1
                continue

            branch = _at(branches, item.get("branch")) or fallback_branch
            room = _at(rooms, item.get("room"))
            if branch is None:
                schedules.append(None)
                counts["skipped"] += 1
                continue
            if room is not None and room.branch_id != branch.pk:
                room = None

            ignore_conflict = bool(item.get("ignore_teacher_conflict"))
            is_substitution = bool(item.get("is_substitution"))
            schedule, _ = Schedule.objects.get_or_create(
                group=group,
                day_of_week=day_of_week,
                start_time=start_time,
                lesson_date=lesson_date,
                defaults={
                    "end_time": end_time,
                    "teacher": teacher,
                    "branch": branch,
                    "room": room,
                    "ignore_teacher_conflict": ignore_conflict,
                    "is_substitution": is_substitution,
                },
            )
            changed = False
            if schedule.teacher_id != teacher.pk:
                schedule.teacher = teacher
                changed = True
            if schedule.end_time != end_time:
                schedule.end_time = end_time
                changed = True
            if schedule.branch_id != branch.pk:
                schedule.branch = branch
                changed = True
            if room is not None and schedule.room_id != room.pk:
                schedule.room = room
                changed = True
            if bool(schedule.ignore_teacher_conflict) != ignore_conflict:
                schedule.ignore_teacher_conflict = ignore_conflict
                changed = True
            if bool(schedule.is_substitution) != is_substitution:
                schedule.is_substitution = is_substitution
                changed = True
            if changed:
                schedule.save()
            schedules.append(schedule)
            counts["schedules"] += 1

        # Посещаемость
        # Посещаемость. Пишем пачками через bulk_create: на 100 000 отметок
        # update_or_create по одной записи не укладывался в таймаут запроса.
        # update_conflicts обновляет статус уже существующей отметки — результат
        # тот же, что у прежнего update_or_create.
        attendance_rows = []
        seen_attendance = set()

        def flush_attendance():
            if not attendance_rows:
                return
            Attendance.objects.bulk_create(
                attendance_rows,
                batch_size=ATTENDANCE_BATCH_SIZE,
                update_conflicts=True,
                update_fields=["status"],
                unique_fields=["student", "schedule", "date"],
            )
            attendance_rows.clear()
            seen_attendance.clear()

        for item in backup.get("attendance") or []:
            schedule = _at(schedules, item.get("schedule"))
            student = _at(students, item.get("student"))
            lesson_date = _parse_date(item.get("date"))
            if schedule is None or student is None or lesson_date is None:
                counts["skipped"] += 1
                continue
            status = item.get("status")
            if status not in VALID_STATUSES:
                status = Attendance.Status.PRESENT.value
            identity = (student.pk, schedule.pk, lesson_date)
            if identity in seen_attendance:
                # Дубль внутри одной пачки: в SQLite это нарушило бы уникальность.
                counts["skipped"] += 1
                continue
            seen_attendance.add(identity)
            attendance_rows.append(
                Attendance(
                    student=student,
                    schedule=schedule,
                    date=lesson_date,
                    status=status,
                )
            )
            counts["attendance"] += 1
            if len(attendance_rows) >= ATTENDANCE_BATCH_SIZE:
                flush_attendance()
        flush_attendance()

        # Темы занятий и академические часы
        for item in backup.get("topics") or []:
            schedule = _at(schedules, item.get("schedule"))
            lesson_date = _parse_date(item.get("date"))
            topic_text = (item.get("topic") or "").strip()[:300]
            if schedule is None or lesson_date is None or not topic_text:
                counts["skipped"] += 1
                continue
            topic, _ = LessonTopic.objects.update_or_create(
                schedule=schedule,
                date=lesson_date,
                defaults={
                    "topic": topic_text,
                    "teacher": _find_user(item.get("teacher")) or schedule.teacher,
                },
            )
            counts["topics"] += 1

        # Конкурсы детей
        for item in backup.get("contests") or []:
            student = _at(students, item.get("student"))
            contest_date = _parse_date(item.get("date"))
            title = (item.get("title") or "").strip()[:250]
            if student is None or contest_date is None or not title:
                counts["skipped"] += 1
                continue
            status = item.get("status")
            if status not in VALID_CONTEST_STATUSES:
                status = ContestResult.Status.PARTICIPANT.value
            ContestResult.objects.get_or_create(
                student=student,
                date=contest_date,
                title=title,
                defaults={
                    "status": status,
                    "subject": (item.get("subject") or "")[:150],
                    "comment": (item.get("comment") or "")[:250],
                },
            )
            counts["contests"] += 1

        # Повышение квалификации педагогов
        for item in backup.get("development") or []:
            teacher = _find_user(item.get("teacher"))
            dev_date = _parse_date(item.get("date"))
            program = (item.get("program") or "").strip()[:250]
            if teacher is None or dev_date is None or not program:
                counts["skipped"] += 1
                continue
            kind = item.get("kind")
            if kind not in VALID_DEVELOPMENT_KINDS:
                kind = TeacherDevelopment.Kind.COURSE.value
            hours = item.get("hours")
            try:
                hours = int(hours) if hours not in (None, "") else None
            except (TypeError, ValueError):
                hours = None
            TeacherDevelopment.objects.get_or_create(
                teacher=teacher,
                date=dev_date,
                program=program,
                defaults={
                    "kind": kind,
                    "organization": (item.get("organization") or "")[:250],
                    "hours": hours,
                    "result": (item.get("result") or "")[:200],
                },
            )
            counts["development"] += 1

        # Настройки сайта и почты. В старых копиях раздела «settings» нет —
        # такие копии восстанавливаются как раньше, просто без настроек.
        settings = backup.get("settings")
        if isinstance(settings, dict):
            _restore_site_settings(settings.get("site"))
            _restore_mail_settings(settings.get("mail"))

    # Отчёт: сколько записей было в копии и сколько стало в базе.
    counts["report"] = {
        "mode": mode,
        "version": backup.get("version"),
        "backup_records": backup_total,
        "backup_sections": backup_totals,
        #: Состояние базы ДО восстановления — для сравнения «в копии N, было M».
        "db_before": db_before,
        "db_records": _db_totals(),
    }
    return counts
