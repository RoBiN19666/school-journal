"""Выгрузка расписания картинкой (PNG): вертикальный и горизонтальный формат."""
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from .models import Schedule

FONT_REGULAR_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
)
FONT_BOLD_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)

ROW_COLORS = ("#DCEBD8", "#E7DDF3", "#FBD9E3", "#DFE7F5", "#F3E4D3", "#E3F1F0")
GRID_COLOR = "#9AA0A6"
HEADER_FILL = "#F1F3F4"
TEXT_COLOR = "#1F2328"
MUTED_COLOR = "#5F6368"
TITLE_COLOR = "#111111"
BRANCH_COLOR = "#C98A2B"

LAYOUTS = {
    "vertical": {
        "width": 1340,
        "min_height": 1700,
        "margin": 40,
        "label_width": 380,
        "day_width": 150,
        "title_size": 56,
        "branch_size": 40,
        "subtitle_size": 22,
        "header_size": 22,
        "label_size": 22,
        "small_size": 17,
        "cell_size": 19,
        "line_height": 27,
        "row_padding": 12,
        "min_row": 70,
    },
    "horizontal": {
        "width": 2560,
        "min_height": 1240,
        "margin": 40,
        "label_width": 560,
        "day_width": 320,
        "title_size": 76,
        "branch_size": 56,
        "subtitle_size": 30,
        "header_size": 30,
        "label_size": 30,
        "small_size": 24,
        "cell_size": 27,
        "line_height": 40,
        "row_padding": 18,
        "min_row": 96,
    },
}


def _font(paths, size):
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _split_long(draw, word, font, max_width):
    """Режет слишком длинное слово: по дефису (если он есть), иначе по буквам."""
    cut = len(word)
    while cut > 1 and draw.textlength(word[:cut], font=font) > max_width:
        cut -= 1
    if cut < len(word):
        for separator in ("-", "/", "·", ",", "."):
            position = word.rfind(separator, 0, cut + 1)
            if position > 0 and draw.textlength(
                word[: position + 1], font=font
            ) <= max_width:
                return word[: position + 1], word[position + 1 :]
    return word[:cut], word[cut:]


def _wrap(draw, text, font, max_width):
    """Разбивает текст по словам так, чтобы он точно влез в колонку.

    Длинные слова (например, «Класс-информатики-и-математики») режутся по дефису
    или по буквам, чтобы ничего не вылезало за границы ячейки.
    """
    max_width = max(int(max_width) - 6, 20)  # небольшой запас по краям
    words = str(text).split()
    if not words:
        return [""]

    lines = []
    current = ""
    for word in words:
        while draw.textlength(word, font=font) > max_width and len(word) > 1:
            head, tail = _split_long(draw, word, font, max_width)
            if not tail or not head:
                break
            if current:
                lines.append(current)
                current = ""
            lines.append(head)
            word = tail
        if not current:
            current = word
        elif draw.textlength(f"{current} {word}", font=font) <= max_width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _fit_font(paths, text, base_size, max_width, draw, min_size=11):
    """Подбирает размер шрифта, при котором текст влезает в колонку."""
    size = base_size
    while size > min_size:
        font = _font(paths, size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 1
    return _font(paths, min_size)


def collect_rows(term, branch):
    """Строки таблицы: по программе, с преподавателем, кабинетами и занятиями по дням."""
    lessons = (
        Schedule.objects.filter(group__term=term, branch=branch)
        .select_related("group__subject", "teacher", "room")
        .order_by("group__subject__name", "day_of_week", "start_time")
    )
    rows = {}
    for lesson in lessons:
        row = rows.setdefault(
            lesson.group.subject_id,
            {
                "subject": lesson.group.subject.name,
                "teacher": lesson.teacher.full_name if lesson.teacher else "",
                "rooms": [],
                "cells": {value: [] for value, _ in Schedule.Weekday.choices},
            },
        )
        if lesson.room and lesson.room.name not in row["rooms"]:
            row["rooms"].append(lesson.room.name)
        # У разового занятия рядом с группой пишем дату: оно бывает только в этот день.
        name = lesson.group.name
        if lesson.lesson_date is not None:
            name = f"{name} (разовое {lesson.lesson_date:%d.%m.%Y})"
        row["cells"][lesson.day_of_week].append((name, lesson.time_range))
    return sorted(rows.values(), key=lambda item: item["subject"])


def schedule_image(term, branch, orientation="vertical"):
    """Рисует расписание и возвращает (BytesIO с PNG, имя файла)."""
    layout = LAYOUTS.get(orientation) or LAYOUTS["vertical"]
    # Подписи дней недели — ленивые строки перевода (gettext_lazy). Pillow умеет
    # работать только с обычным str, иначе падает с ошибкой в ImageDraw
    # («'in <string>' requires string as left operand, not bytes»), поэтому
    # приводим подписи к str сразу, ещё до расчёта ширины и рисования.
    days = [(value, str(label)) for value, label in Schedule.Weekday.choices[:6]]
    rows = collect_rows(term, branch)

    font_title = _font(FONT_BOLD_PATHS, layout["title_size"])
    font_branch = _font(FONT_BOLD_PATHS, layout["branch_size"])
    font_subtitle = _font(FONT_REGULAR_PATHS, layout["subtitle_size"])
    font_header = _font(FONT_BOLD_PATHS, layout["header_size"])
    font_label = _font(FONT_BOLD_PATHS, layout["label_size"])
    font_small = _font(FONT_REGULAR_PATHS, layout["small_size"])
    font_cell = _font(FONT_REGULAR_PATHS, layout["cell_size"])

    margin = layout["margin"]
    label_width = layout["label_width"]
    day_width = layout["day_width"]
    line_height = layout["line_height"]
    row_padding = layout["row_padding"]
    table_width = label_width + day_width * len(days)
    width = max(layout["width"], table_width + margin * 2)

    measure = ImageDraw.Draw(Image.new("RGB", (10, 10), "white"))

    # Заголовок страницы
    title_lines = [
        ("РАСПИСАНИЕ", font_title, TITLE_COLOR),
        (str(branch.name) if branch else "все адреса", font_branch, BRANCH_COLOR),
    ]
    subtitle = f"Расписание на {term.year.title} учебный год · {term.title}" if term else "Расписание"
    if term is not None:
        subtitle += f" ({term.date_range})"
    title_lines.append((subtitle, font_subtitle, MUTED_COLOR))

    # Тексты таблицы (с переносами) — считаем и высоты строк
    header_lines = _wrap(measure, "Программа · преподаватель · кабинет", font_header, label_width - 20)

    # Названия дней недели подгоняем по ширине колонки, чтобы «Понедельник» влезал.
    day_headers = []
    for _, label in days:
        font = _fit_font(
            FONT_BOLD_PATHS, label, layout["header_size"], day_width - 16, measure
        )
        lines = (
            [label]
            if measure.textlength(label, font=font) <= day_width - 16
            else _wrap(measure, label, font, day_width - 16)
        )
        day_headers.append((font, lines))
    header_height = (
        max(
            line_height,
            len(header_lines) * line_height,
            max(len(lines) for _, lines in day_headers) * line_height,
        )
        + 16
    )

    prepared = []
    for row in rows:
        label_lines = [
            (line, font_label, TEXT_COLOR)
            for line in _wrap(measure, row["subject"], font_label, label_width - 32)
        ]
        if row["teacher"]:
            label_lines.extend(
                (line, font_small, MUTED_COLOR)
                for line in _wrap(measure, row["teacher"], font_small, label_width - 32)
            )
        if row["rooms"]:
            label_lines.extend(
                (line, font_small, MUTED_COLOR)
                for line in _wrap(
                    measure, f"Каб. {', '.join(row['rooms'])}", font_small, label_width - 32
                )
            )

        cells = {}
        cell_lines = 0
        for value, _ in days:
            lines = []
            for index, (group_name, time_range) in enumerate(row["cells"].get(value) or []):
                if index:
                    lines.append(None)
                lines.extend(
                    (line, font_cell, TEXT_COLOR)
                    for line in _wrap(measure, group_name, font_cell, day_width - 28)
                )
                lines.append((time_range, font_cell, MUTED_COLOR))
            cells[value] = lines
            cell_lines = max(cell_lines, len(lines))

        total_lines = max(len(label_lines), cell_lines)
        prepared.append(
            {
                "row": row,
                "label_lines": label_lines,
                "cells": cells,
                "height": max(
                    layout["min_row"], total_lines * line_height + row_padding * 2
                ),
            }
        )

    title_height = sum(
        int(size * 1.4) for size in (layout["title_size"], layout["branch_size"])
    ) + int(layout["subtitle_size"] * 2.4)
    total_height = max(
        layout["min_height"],
        title_height + 20 + header_height + sum(item["height"] for item in prepared) + margin,
    )

    image = Image.new("RGB", (width, total_height), "white")
    draw = ImageDraw.Draw(image)

    y = 24
    for text, font, color in title_lines:
        draw.text(
            ((width - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=color
        )
        y += int(font.size * 1.45)

    y = title_height + 20

    # Шапка таблицы
    x = margin
    draw.rectangle([x, y, x + table_width, y + header_height], fill=HEADER_FILL, outline=GRID_COLOR)
    text_y = y + 8
    for line in header_lines:
        draw.text((x + 12, text_y), line, font=font_header, fill=TEXT_COLOR)
        text_y += line_height
    x += label_width
    for font, lines in day_headers:
        draw.rectangle([x, y, x + day_width, y + header_height], outline=GRID_COLOR)
        text_y = y + 8 + max(
            0, (header_height - 16 - len(lines) * line_height) / 2
        )
        for line in lines:
            line_px = draw.textlength(line, font=font)
            draw.text(
                (x + (day_width - line_px) / 2, text_y),
                line,
                font=font,
                fill=TEXT_COLOR,
            )
            text_y += line_height
        x += day_width
    y += header_height

    # Строки таблицы
    for index, item in enumerate(prepared):
        row_height = item["height"]
        fill = ROW_COLORS[index % len(ROW_COLORS)]
        x = margin
        draw.rectangle([x, y, x + table_width, y + row_height], fill=fill, outline=GRID_COLOR)

        text_y = y + row_padding
        for line, font, color in item["label_lines"]:
            draw.text((x + 12, text_y), line, font=font, fill=color)
            text_y += line_height

        x += label_width
        for value, _ in days:
            draw.rectangle([x, y, x + day_width, y + row_height], outline=GRID_COLOR)
            cell_y = y + row_padding
            for line in item["cells"].get(value) or []:
                if line is None:
                    cell_y += line_height // 2
                    continue
                text, font, color = line
                draw.text((x + 8, cell_y), text, font=font, fill=color)
                cell_y += line_height
            x += day_width
        y += row_height

    if not rows:
        draw.text(
            (margin + 12, y + 24),
            "На этом адресе занятий в выбранном полугодии пока нет.",
            font=font_label,
            fill=MUTED_COLOR,
        )

    stream = BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"schedule_{orientation}_{stamp}.png"
    return stream, filename
