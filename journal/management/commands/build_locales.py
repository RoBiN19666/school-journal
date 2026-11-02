"""Сборка файлов перевода (.mo) без GNU gettext.

Штатная команда Django ``compilemessages`` требует установленных утилит gettext,
которых в контейнере может не быть. Эта команда собирает ``.mo`` из ``.po``
средствами Python и заодно проверяет, что результат читается библиотекой gettext.

Использование:
    python manage.py build_locales            # собрать все языки, кроме исходного
    python manage.py build_locales --check    # только проверить уже собранные файлы
"""
from __future__ import annotations

import gettext
import struct
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

#: Язык исходных текстов: его каталог перевода не нужен.
SOURCE_LANGUAGE = "ru"


def read_text(path: Path) -> tuple[str, str]:
    """Читает .po, определяя кодировку (utf-8, иначе cp1251)."""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8/replace"


def unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    text = (text.replace("\\n", "\n").replace("\\t", "\t")
            .replace('\\"', '"').replace("\\\\", "\\"))
    # %% — экранирование gettext; Django ищет в каталоге одиночный %.
    return text.replace("%%", "%")


def parse_po(path: Path) -> dict[str, str]:
    """Пары msgid/msgstr из .po, включая многострочные значения."""
    text, _ = read_text(path)
    # Windows-переводы строк и остатки \r портили файл: нормализуем перед разбором.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    entries: dict[str, str] = {}
    current: list[str | None] | None = None
    field = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("msgid "):
            if current and current[0] is not None and current[1] is not None:
                entries[current[0]] = current[1]
            current, field = [unquote(line[6:]), None], "id"
        elif line.startswith("msgstr "):
            if current:
                current[1] = unquote(line[7:])
            field = "str"
        elif line.startswith('"') and current:
            index = 0 if field == "id" else 1
            current[index] = (current[index] or "") + unquote(line)
    if current and current[0] is not None and current[1] is not None:
        entries[current[0]] = current[1]
    return entries


def write_mo(path: Path, entries: dict[str, str]) -> None:
    """Собирает .mo: заголовок, две таблицы и блоки строк с абсолютными смещениями."""
    items = sorted(entries.items(), key=lambda pair: pair[0].encode("utf-8"))
    count = len(items)
    originals_table = 28
    translations_table = 28 + count * 8
    ids_blob_offset = 28 + count * 16
    ids_blob = b""
    strs_blob = b""
    ids_table: list[tuple[int, int]] = []
    for key, _ in items:
        encoded = key.encode("utf-8")
        ids_table.append((len(encoded), ids_blob_offset + len(ids_blob)))
        ids_blob += encoded + b"\x00"
    strs_blob_offset = ids_blob_offset + len(ids_blob)
    strs_table: list[tuple[int, int]] = []
    written = 0
    for _, value in items:
        encoded = value.encode("utf-8")
        strs_table.append((len(encoded), strs_blob_offset + written))
        written += len(encoded) + 1
        strs_blob += encoded + b"\x00"
    with path.open("wb") as handle:
        handle.write(struct.pack("<7I", 0x950412DE, 0, count, originals_table,
                                 translations_table, 0, 0))
        for length, offset in ids_table:
            handle.write(struct.pack("<2I", length, offset))
        for length, offset in strs_table:
            handle.write(struct.pack("<2I", length, offset))
        handle.write(ids_blob)
        handle.write(strs_blob)


def locale_directories() -> list[Path]:
    roots = [Path(item) for item in getattr(settings, "LOCALE_PATHS", [])]
    if not roots:
        roots = [Path(settings.BASE_DIR) / "locale"]
    return [item for item in roots if item.exists()]


class Command(BaseCommand):
    help = "Builds translation files (.mo) from .po without GNU gettext"

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true",
                            help="only check that the built files can be read")

    def handle(self, *args, **options):
        check_only = options["check"]
        built = 0
        broken = 0
        for directory in locale_directories():
            for po_path in sorted(directory.rglob("*.po")):
                language = po_path.parent.parent.name
                if language == SOURCE_LANGUAGE:
                    continue
                mo_path = po_path.with_suffix(".mo")
                if check_only:
                    if not mo_path.exists():
                        self.stderr.write(f"no compiled file: {mo_path}")
                        broken += 1
                        continue
                else:
                    text, encoding = read_text(po_path)
                    if encoding != "utf-8":
                        po_path.write_text(text, encoding="utf-8", newline="\n")
                        self.stdout.write(f"{po_path}: re-encoded from {encoding} to utf-8")
                    entries = parse_po(po_path)
                    # Пустой перевод в .mo означает пустую строку в интерфейсе,
                    # поэтому в каталог попадают только заполненные строки;
                    # заголовок с кодировкой оставляем всегда.
                    header = entries.get("", "")
                    if "charset" not in header:
                        header = ("Content-Type: text/plain; charset=UTF-8\n"
                                  "Language: %s\n" % language)
                    filled = {key: value for key, value in entries.items() if value}
                    filled[""] = header
                    write_mo(mo_path, filled)
                try:
                    with mo_path.open("rb") as handle:
                        catalog = gettext.GNUTranslations(handle)
                    catalog.gettext("test")
                except Exception as error:  # noqa: BLE001 - сообщаем и продолжаем
                    self.stderr.write(f"{mo_path}: file cannot be read - {error}")
                    broken += 1
                    continue
                translated = len(catalog._catalog)
                self.stdout.write(self.style.SUCCESS(
                    f"{mo_path.relative_to(settings.BASE_DIR)}: strings {translated}"))
                built += 1
        if broken:
            self.stderr.write(self.style.ERROR(f"files with problems: {broken}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(
            f"Done: translation files - {built}" + (" (check only)" if check_only else "")))
