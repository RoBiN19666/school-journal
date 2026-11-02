"""Импорт списка учеников из Excel.

Ожидаются столбцы: ФИО, дата рождения и статус «отпускать при беспилотной опасности»
(Н / ЕН / ЕО). Обязателен только ФИО: если даты рождения или статуса нет, ученик
всё равно импортируется, а эти поля просто остаются пустыми (или значением по умолчанию).
"""
import re
from datetime import date, datetime

from openpyxl import load_workbook

MAX_ROWS = 2000
MAX_COLUMNS = 12

HEADER_WORDS = {
    "фио", "ф.и.о", "ф. и. о.", "имя", "фамилия", "ученик", "ученики", "список",
    "список учеников", "дети", "ребёнок", "ребенок", "№", "n", "no", "п/п",
    "номер", "фамилия имя отчество", "фамилия, имя, отчество", "фио ученика",
    "дата рождения", "дата", "др", "статус", "отпускать", "заявление",
}

# «1.», «1)», «№3», «1 -»
NUMBER_PREFIX = re.compile(r"^\s*(?:№\s*)?\d+\s*[.)\-–]?\s*")
INITIALS = re.compile(r"^[А-ЯЁA-Z]\.?$")
ONLY_LETTERS = re.compile(r"^[А-ЯЁа-яёA-Za-z\-]+$")

# Статусы: Н — нет заявления, ЕН — есть заявление, не отпускать, ЕО — можно отпускать.
DISMISSAL_ALIASES = {
    "н": "Н",
    "нет": "Н",
    "не": "Н",
    "нет заявления": "Н",
    "без заявления": "Н",
    "ен": "ЕН",
    "не отпускать": "ЕН",
    "есть заявление не отпускать": "ЕН",
    "заявление не отпускать": "ЕН",
    "ео": "ЕО",
    "отпускать": "ЕО",
    "можно отпускать": "ЕО",
    "есть заявление можно отпускать": "ЕО",
    "заявление можно отпускать": "ЕО",
}

DATE_FORMATS = (
    "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
    "%d.%m", "%d/%m", "%d-%m",
)


def normalize_token(value):
    """Приводит кусочек имени к нормальному виду: «иванов,» → «Иванов»."""
    token = str(value or "").strip().strip(",;").strip()
    if not token:
        return ""
    if INITIALS.match(token):
        return token[0].upper() + "."
    if ONLY_LETTERS.match(token):
        return token[:1].upper() + token[1:].lower()
    return token


def split_full_name(raw):
    """Разбирает строку с ФИО на фамилию, имя и отчество (отчество может отсутствовать)."""
    text = NUMBER_PREFIX.sub("", str(raw or "").strip())
    text = text.replace(",", " ")
    parts = [part for part in re.split(r"\s+", text) if part]
    parts = [normalize_token(part) for part in parts]
    parts = [part for part in parts if part and not part.isdigit()]
    if len(parts) < 2:
        return None
    last_name, first_name = parts[0], parts[1]
    middle_name = parts[2] if len(parts) > 2 else ""
    if len(last_name) < 2 or len(first_name) < 1:
        return None
    if first_name.endswith(".") and len(first_name) == 2:
        # «Иванов И.» — это инициал, а не имя целиком.
        first_name = first_name[0]
    if middle_name.endswith(".") and len(middle_name) == 2:
        middle_name = middle_name[0]
    return {
        "last_name": last_name,
        "first_name": first_name,
        "middle_name": middle_name,
        "raw": str(raw).strip(),
    }


def parse_birth_date(value):
    """Разбирает дату рождения из ячейки Excel. Не получилось — None."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt and "%y" not in fmt:
            # Год не указан — оставляем пустым, чтобы не выдумывать данные.
            return None
        return parsed.date()
    return None


def parse_dismissal(value):
    """Разбирает статус «отпускать при БП». Неизвестное значение — None."""
    if value in (None, ""):
        return None
    text = str(value).strip().lower().replace("ё", "е")
    text = NUMBER_PREFIX.sub("", text).strip()
    text = re.sub(r"[\s,.;]+", " ", text).strip()
    if text in DISMISSAL_ALIASES:
        return DISMISSAL_ALIASES[text]
    compact = text.replace(" ", "")
    if compact in DISMISSAL_ALIASES:
        return DISMISSAL_ALIASES[compact]
    if compact.startswith("ен"):
        return "ЕН"
    if compact.startswith("ео"):
        return "ЕО"
    if compact == "н":
        return "Н"
    return None


def looks_like_name(value):
    parsed = split_full_name(value)
    if parsed is None:
        return False
    text = str(value or "").strip()
    if text.strip().lower().rstrip(":") in HEADER_WORDS:
        return False
    return bool(re.search(r"[А-Яа-яЁёA-Za-z]", text))


def is_header(value):
    text = str(value or "").strip().lower().rstrip(":").strip()
    if not text:
        return False
    if text in HEADER_WORDS:
        return True
    return text.startswith("фио") or text.startswith("список")


def _pick_name_column(rows):
    """Ищет столбец, в котором больше всего похожих на ФИО значений."""
    best_index, best_score = 0, -1
    limit = min(len(rows[0]) if rows else 0, MAX_COLUMNS)
    for index in range(limit):
        score = 0
        checked = 0
        for row in rows[:200]:
            if index >= len(row):
                continue
            value = row[index]
            if value in (None, ""):
                continue
            checked += 1
            if looks_like_name(value):
                score += 1
        if checked and score / checked >= 0.5 and score > best_score:
            best_index, best_score = index, score
    return best_index


def _pick_extra_columns(rows, name_index):
    """Ищет столбцы с датой рождения и статусом: побеждает столбец с большим числом совпадений.

    Жёсткий порог здесь плохо работает на коротких списках (5–10 строк), поэтому
    выбираем лучший столбец, а не первый подходящий по проценту.
    """
    limit = min(len(rows[0]) if rows else 0, MAX_COLUMNS)
    best_date, best_date_hits = None, 0
    best_status, best_status_hits = None, 0
    for index in range(limit):
        if index == name_index:
            continue
        dates, statuses, checked = 0, 0, 0
        for row in rows[:200]:
            if index >= len(row):
                continue
            value = row[index]
            if value in (None, ""):
                continue
            if is_header(value):
                # Строку-шапку в пропорцию не берём, иначе столбец не распознаётся.
                continue
            checked += 1
            if parse_birth_date(value) is not None:
                dates += 1
            if parse_dismissal(value) is not None:
                statuses += 1
        if not checked:
            continue
        if dates and dates / checked >= 0.4 and dates > best_date_hits:
            best_date, best_date_hits = index, dates
        if statuses and statuses / checked >= 0.4 and statuses > best_status_hits:
            best_status, best_status_hits = index, statuses
    if best_date is not None and best_date == best_status:
        # Один и тот же столбец не может быть и датой, и статусом.
        best_status = None
    return best_date, best_status


def _cell(row, index):
    if index is None or index >= len(row):
        return None
    return row[index]


def parse_students(source):
    """Читает файл (.xlsx) и возвращает (список учеников, предупреждения)."""
    workbook = load_workbook(source, read_only=True, data_only=True)
    worksheet = workbook.worksheets[0]
    rows = []
    truncated = False
    for row in worksheet.iter_rows(values_only=True):
        if len(rows) >= MAX_ROWS:
            # Файл длиннее лимита: строки после MAX_ROWS не читаем, но говорим об этом
            # вслух — иначе список молча обрезается и ученики не попадают в журнал.
            truncated = True
            break
        rows.append([cell for cell in row])
    workbook.close()

    warnings = []
    if truncated:
        warnings.append(
            f"В файле больше {MAX_ROWS} строк — прочитаны первые {MAX_ROWS}. "
            "Разделите файл и загрузите остаток."
        )
    if not rows:
        return [], ["Файл пустой."]

    while rows and all(cell in (None, "") for cell in rows[0]):
        rows.pop(0)
    if not rows:
        return [], ["В файле нет данных."]

    name_column = _pick_name_column(rows)
    date_column, status_column = _pick_extra_columns(rows, name_column)

    parsed, seen = [], set()
    without_date, without_status, bad_dates, bad_statuses = 0, 0, 0, 0
    for row in rows:
        value = _cell(row, name_column)
        if value in (None, ""):
            continue
        if is_header(value):
            continue
        if isinstance(value, datetime):
            continue
        student = split_full_name(value)
        if student is None:
            text = str(value).strip()
            if text:
                warnings.append(f"Пропущена строка «{text[:60]}» — не похоже на ФИО.")
            continue
        key = (
            student["last_name"].lower(),
            student["first_name"].lower(),
            student["middle_name"].lower(),
        )
        if key in seen:
            warnings.append(f"В файле повторяется «{student['raw'][:60]}».")
            continue
        seen.add(key)

        raw_date = _cell(row, date_column)
        raw_status = _cell(row, status_column)
        birth = parse_birth_date(raw_date)
        dismissal = parse_dismissal(raw_status)
        if birth is None:
            without_date += 1
            if raw_date not in (None, "") and not is_header(raw_date):
                bad_dates += 1
        if dismissal is None:
            without_status += 1
            if raw_status not in (None, "") and not is_header(raw_status):
                bad_statuses += 1

        student["birth_date"] = birth.isoformat() if birth else ""
        student["birth_display"] = birth.strftime("%d.%m.%Y") if birth else ""
        student["dismissal"] = dismissal or ""
        student["raw_date"] = "" if raw_date in (None, "") else str(raw_date).strip()
        student["raw_status"] = "" if raw_status in (None, "") else str(raw_status).strip()
        parsed.append(student)

    if not parsed:
        warnings.append(
            "Не нашёл ни одного ФИО. Проверьте, что фамилия, имя и отчество "
            "записаны в одном столбце одной строкой."
        )
    else:
        if without_date:
            warnings.append(
                f"Без даты рождения: {without_date} — импортируются, дату можно "
                "заполнить потом в карточке ученика."
            )
        if bad_dates:
            warnings.append(
                f"Дата рождения не распознана у {bad_dates} строк — оставили пустой."
            )
        if without_status:
            warnings.append(
                f"Без статуса «отпускать при БП»: {without_status} — поставлено "
                "«Нет заявления», потом можно изменить."
            )
        if bad_statuses:
            warnings.append(
                f"Статус не распознан у {bad_statuses} строк — поставлено «Нет заявления». "
                "Ожидаются значения Н, ЕН или ЕО."
            )
    return parsed, warnings


def mark_duplicates(students):
    """Помечает строки, которые уже есть в базе."""
    from .models import Student

    existing = {}
    for student in Student.objects.all().only("last_name", "first_name", "middle_name"):
        existing[
            (
                student.last_name.lower(),
                student.first_name.lower(),
                (student.middle_name or "").lower(),
            )
        ] = student.pk
    for index, student in enumerate(students):
        key = (
            student["last_name"].lower(),
            student["first_name"].lower(),
            student["middle_name"].lower(),
        )
        student["duplicate"] = existing.get(key)
        student["index"] = index
    return students
