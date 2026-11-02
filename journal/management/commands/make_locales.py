"""Извлечение строк перевода в ``locale/en/LC_MESSAGES/django.po`` без GNU gettext.

Зачем эта команда
=================
Штатный ``django-admin makemessages`` — это обёртка над утилитой ``xgettext`` из
пакета GNU gettext. В окружении, где журнал разворачивается (тонкий образ Python,
Windows-разработка, CI без gettext), этой утилиты может не быть, и тогда
``makemessages`` падает с «Can't find msguniq / xgettext». Поэтому строки
извлекаются здесь собственными средствами: шаблоны разбираются тем же
токенизатором Django, что и при рендеринге, а Python-код — через ``ast``.
Собранный ``.po`` затем превращает в ``.mo`` команда ``build_locales``
(она также не требует gettext).

Что извлекается
===============
Шаблоны (``journal/templates/**/*.html``):

* ``{% trans "Текст" %}`` / ``{% translate "Текст" %}`` — во всех формах:
  с фильтрами (``{% trans "Текст"|upper %}``), ``noop``, ``as var``,
  ``context "…"``;
* ``{% blocktrans %}…{% endblocktrans %}`` — содержимое целиком, как msgid,
  вместе с HTML внутри. Строка ``with key=value`` (а также ``count``,
  ``context``, ``trimmed``, ``asvar``) в msgid не попадает. Подстановки
  ``{{ key }}`` попадают в msgid в виде ``%(key)s`` — ровно в таком виде
  строку ищет в каталоге сам шаблонизатор Django (проверено подстановкой
  перехватчика в ``gettext``: ``{% blocktrans with total=total %}Всего:
  {{ total }}{% endblocktrans %}`` ищет ``"Всего: %(total)s"``);
* ``default:_("…")`` — и внутри блоков, и внутри переменных, и в строке
  ``with``/``count``/``context`` (вариант из документации Django для значений
  по умолчанию).

Python-код (``journal/**/*.py``):

* вызовы ``_()``, ``gettext()``, ``gettext_lazy()``, ``gettext_noop()``,
  ``ngettext()``, ``ngettext_lazy()``, ``pgettext()``, ``pgettext_lazy()``,
  ``npgettext()``, ``npgettext_lazy()``;
* у ``ngettext`` берутся оба варианта (единственное и множественное число);
* разбор идёт по ``ast``, а не регулярками; берутся только одноуровневые
  строковые литералы — f-строки, конкатенация, ``%``-форматирование и вызовы
  с переменными пропускаются и попадают в отчёт «пропущено».

Что команда НЕ делает
=====================
Не переводит (``msgstr`` остаются пустыми — их заполняет переводчик) и не
собирает ``.mo``: за сборку отвечает ``build_locales``. Файл ``.po`` всегда
пишется в UTF-8 без BOM: файл в cp1251 однажды уже ломал журнал.

Сохранение работы переводчика
=============================
Перед записью прежний файл копируется рядом в ``django.po.bak``. Заполненные
``msgstr`` переносятся по совпадению msgid. Строки, которых больше нет в
исходниках, не удаляются: они уезжают в конец файла под комментарием ``#~``
(как это делает gettext), вместе с их переводами — если строка вернётся,
перевод найдётся.

Использование
=============
    python manage.py make_locales            # извлечь строки и обновить .po
    python manage.py make_locales --check    # ничего не писать, код 1 если есть пропуски
    python manage.py make_locales --locale en
"""
from __future__ import annotations

import ast
import datetime as dt
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.template.base import Lexer, TokenType
from django.utils.translation import trim_whitespace

#: Язык исходных текстов: для него перевода не нужно.
SOURCE_LANGUAGE = "ru"
#: Каталог перевода по умолчанию (относительно BASE_DIR).
DEFAULT_LOCALE_DIR = Path("locale")
#: Имя файла каталога, как у Django.
PO_FILENAME = "django.po"

#: Имена функций перевода, вызовы которых ищутся в Python-коде.
SINGULAR_FUNCTIONS = {
    "_",
    "gettext",
    "gettext_lazy",
    "gettext_noop",
    "ugettext",
    "ugettext_lazy",
    "ugettext_noop",
    "pgettext",
    "pgettext_lazy",
}
PLURAL_FUNCTIONS = {"ngettext", "ngettext_lazy", "ungettext", "ungettext_lazy", "npgettext", "npgettext_lazy"}
#: Функции, у которых первый аргумент — контекст, а строка идёт второй.
CONTEXT_FIRST_FUNCTIONS = {"pgettext", "pgettext_lazy", "npgettext", "npgettext_lazy"}

#: Теги шаблонов, помечающие строку для перевода.
TRANS_TAGS = {"trans", "translate"}
BLOCKTRANS_TAGS = {"blocktrans", "blocktranslate"}

#: Варианты написания «константной» строки в шаблоне: _"…"_ или default:_("…").
DEFAULT_MARKER = ":_("

_QUOTES = "\"'"


# ---------------------------------------------------------------------------
# Разбор строковых литералов
# ---------------------------------------------------------------------------
def unquote_literal(literal: str) -> str | None:
    """Возвращает содержимое строкового литерала из шаблона или None.

    Понимает и двойные, и одинарные кавычки, а также escape-последовательности
    (``\\n``, ``\\"``, ``\\\\``).
    """
    text = literal.strip()
    if len(text) < 2 or text[0] not in _QUOTES or text[-1] != text[0]:
        return None
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        # Экранирование, которое Python не понимает: снимаем кавычки как есть.
        return text[1:-1]


def iter_template_constants(text: str):
    """Находит в куске шаблона пометки ``_("…")`` и ``default:_("…")``.

    Возвращает пары (строка, позиция начала литерала). Используется для
    переменных шаблона (``{{ x|default:_("…") }}``) и для строки
    ``with key=value|default:_("…")`` у блоков перевода.
    """
    index = 0
    length = len(text)
    while True:
        found = text.find(DEFAULT_MARKER, index)
        if found < 0:
            return
        pos = found + len(DEFAULT_MARKER)
        index = pos
        if pos >= length or text[pos] not in _QUOTES:
            continue
        quote = text[pos]
        end = pos + 1
        while end < length:
            char = text[end]
            if char == "\\":
                end += 2
                continue
            if char == quote:
                break
            end += 1
        if end >= length:
            return
        value = unquote_literal(text[pos:end + 1])
        if value is not None:
            yield value, pos
        index = end + 1


def tokenize_template(source: str):
    """Токены шаблона через лексер Django (тот же, что при рендеринге)."""
    return list(Lexer(source).tokenize())


# ---------------------------------------------------------------------------
# Шаблоны
# ---------------------------------------------------------------------------
def split_tag_args(contents: str, tag: str) -> str:
    """Аргументы тега без его имени и без обрамляющих ``%}``."""
    body = contents.strip()
    for prefix in ("{%-", "{%"):
        if body.startswith(prefix):
            body = body[len(prefix):]
            break
    if body.endswith("%}"):
        body = body[:-2]
    body = body.strip()
    if body.startswith(tag):
        body = body[len(tag):]
    return body.strip()


def first_literal(args: str) -> str | None:
    """Первый строковый литерал в начале аргументов тега перевода.

    После литерала могут идти фильтр (``"Текст"|upper``), признаки ``noop`` и
    ``context "…"``, а также ``as var``: значение тега от них не зависит,
    поэтому всё это отбрасывается. Форма ``{% trans "…" as var %}`` кладёт
    строку в переменную шаблона, но в каталог она попадает так же, как обычная,
    иначе страницы, собранные через ``as``, оставались бы непереведёнными.
    """
    text = args.lstrip()
    if not text or text[0] not in _QUOTES:
        return None
    quote = text[0]
    index = 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return unquote_literal(text[: index + 1])
        index += 1
    return None


def extract_trans(tag_contents: str, tag: str) -> str | None:
    """Строка из ``{% trans "…" %}`` (в том числе ``as var``, ``noop``, ``context``)."""
    args = split_tag_args(tag_contents, tag)
    if not args:
        return None
    return first_literal(args)


def blocktrans_options(tag_contents: str, tag: str) -> dict:
    """Опции ``{% blocktrans … %}``: with/count/context/trimmed/asvar."""
    options: dict[str, object] = {"trimmed": False}
    bits = split_tag_args(tag_contents, tag).split()
    index = 0
    while index < len(bits):
        word = bits[index]
        index += 1
        if word in ("with", "count"):
            while index < len(bits) and bits[index] not in (
                "with", "count", "context", "trimmed", "asvar", "plural",
            ):
                index += 1
        elif word == "context":
            if index < len(bits):
                options["context"] = bits[index]
                index += 1
        elif word == "trimmed":
            options["trimmed"] = True
        elif word == "asvar":
            if index < len(bits):
                options["asvar"] = bits[index]
                index += 1
        elif word == "plural":
            options["has_plural"] = True
    return options


def blocktrans_block(tokens, start: int, tag: str):
    """Тело ``{% blocktrans %}`` как msgid (плюс вариант множественного числа).

    Возвращает (singular, plural, next_index).

    Подстановки ``{{ key }}`` превращаются в ``%(key)s``. Это не украшательство:
    шаблонизатор Django перед поиском в каталоге перевода заменяет каждую
    переменную на ``%(имя)s`` (``BlockTranslateNode.render_token_list``), и в
    каталоге строка лежит именно в таком виде. Если записать в msgid ``{{ key }}``,
    перевод блока просто не найдётся. Фильтры в подстановке (``{{ x|default:… }}``)
    на msgid не влияют — в каталог уходит имя переменной.
    """
    singular: list[str] = []
    plural: list[str] = []
    target = singular
    end_tag = "end" + tag
    index = start
    plural_seen = False
    while index < len(tokens):
        token = tokens[index]
        if token.token_type == TokenType.BLOCK:
            inner = token.contents.strip()
            if inner in (end_tag, "endblocktranslate"):
                index += 1
                break
            if inner == "plural":
                plural_seen = True
                target = plural
                index += 1
                continue
            # Внутри blocktrans других блочных тегов быть не должно (Django
            # тоже это запрещает) — на всякий случай пропускаем.
            index += 1
            continue
        if token.token_type == TokenType.VAR:
            name = token.contents.split("|", 1)[0].strip()
            target.append("%%(%s)s" % name)
        elif token.token_type == TokenType.TEXT:
            target.append(token.contents)
        elif token.token_type == TokenType.COMMENT:
            target.append("{# %s #}" % token.contents)
        else:
            target.append(token.contents)
        index += 1
    singular_text = "".join(singular)
    plural_text = "".join(plural) if plural_seen else ""
    return singular_text, plural_text, index


def extract_templates(root: Path, base_dir: Path, report: dict):
    """Обходит ``journal/templates/**/*.html`` и собирает строки перевода."""
    found: dict[str, set[str]] = {}
    skipped: list[str] = []
    pattern = "journal/templates/**/*.html"
    for path in sorted(root.glob(pattern)):
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            skipped.append(f"{path}: not UTF-8 ({error})")
            continue
        relative = path.relative_to(base_dir).as_posix()
        try:
            tokens = tokenize_template(source)
        except Exception as error:  # noqa: BLE001 - сообщаем и продолжаем
            skipped.append(f"{relative}: cannot parse the template ({error})")
            continue
        for index, token in enumerate(tokens):
            if token.token_type == TokenType.BLOCK:
                head = token.contents.strip().lstrip("-").strip()
                tag = head.split()[0] if head.split() else ""
                if tag in TRANS_TAGS:
                    value = extract_trans(token.contents, tag)
                    if value:
                        found.setdefault(value, set()).add(relative)
                    continue
                if tag in BLOCKTRANS_TAGS:
                    options = blocktrans_options(token.contents, tag)
                    singular, plural, _ = blocktrans_block(tokens, index + 1, tag)
                    if options.get("trimmed"):
                        singular = trim_whitespace(singular)
                        plural = trim_whitespace(plural) if plural else ""
                    if singular:
                        found.setdefault(singular, set()).add(relative)
                    if plural:
                        found.setdefault(plural, set()).add(relative)
                    # default:_("…") в строке with/count/context.
                    for value, _pos in iter_template_constants(token.contents):
                        found.setdefault(value, set()).add(relative)
                    continue
                # Любой другой блочный тег может содержать default:_("…").
                for value, _pos in iter_template_constants(token.contents):
                    found.setdefault(value, set()).add(relative)
            elif token.token_type == TokenType.VAR:
                for value, _pos in iter_template_constants(token.contents):
                    found.setdefault(value, set()).add(relative)
    report["template_occurrences"] = sum(len(v) for v in found.values())
    report["template_skipped"] = skipped
    return found


# ---------------------------------------------------------------------------
# Python-код
# ---------------------------------------------------------------------------
def literal_string(node) -> str | None:
    """Строка из узла ast, если это ровно один строковый литерал.

    f-строки, конкатенация соседних литералов, ``"a" + "b"`` и переменные
    возвращают None: такие случаи попадают в отчёт как пропущенные.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def extract_python(root: Path, base_dir: Path, report: dict):
    """Обходит ``journal/**/*.py`` и собирает вызовы функций перевода (через ast)."""
    found: dict[str, set[str]] = {}
    plurals: dict[str, str] = {}
    skipped: list[str] = []
    files = sorted(path for path in root.glob("journal/**/*.py") if path.is_file())
    for path in files:
        relative = path.relative_to(base_dir).as_posix()
        try:
            # utf-8-sig: некоторые файлы начинаются с BOM, ast его не принимает.
            source = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            skipped.append(f"{relative}: not UTF-8 ({error})")
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as error:
            skipped.append(f"{relative}: syntax error ({error})")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            else:
                continue
            is_plural = name in PLURAL_FUNCTIONS
            if name not in SINGULAR_FUNCTIONS and not is_plural:
                continue
            report["python_calls"] = report.get("python_calls", 0) + 1
            args = list(node.args)
            if name in CONTEXT_FIRST_FUNCTIONS:
                # Первый аргумент — контекст, строка идёт следующими.
                args = args[1:]
            if is_plural:
                singular = plural = None
                if len(args) > 0:
                    singular = literal_string(args[0])
                elif len(args) > 1:
                    # Контекст мог быть переменной: ищем литерал рядом.
                    for node_arg in node.args[:3]:
                        singular = literal_string(node_arg)
                        if singular is not None:
                            break
                if len(args) > 1:
                    plural = literal_string(args[1])
                if singular is None:
                    skipped.append(
                        f"{relative}:{node.lineno}: {name}() — the singular form is not "
                        f"a plain string literal"
                    )
                    continue
                found.setdefault(singular, set()).add(relative)
                if plural is None:
                    skipped.append(
                        f"{relative}:{node.lineno}: {name}() — the plural form is not "
                        f"a plain string literal"
                    )
                    continue
                found.setdefault(plural, set()).add(relative)
                plurals.setdefault(singular, plural)
                continue
            if not args and not node.keywords:
                skipped.append(f"{relative}:{node.lineno}: {name}() without arguments")
                continue
            value = None
            if args:
                value = literal_string(args[0])
            if value is None and args and name in CONTEXT_FIRST_FUNCTIONS:
                # Контекст — переменная, а строка — следующий аргумент.
                for node_arg in args[1:3]:
                    value = literal_string(node_arg)
                    if value is not None:
                        break
            if value is None and not args:
                for keyword in node.keywords:
                    value = literal_string(keyword.value)
                    if value is not None:
                        break
            if value is None:
                skipped.append(
                    f"{relative}:{node.lineno}: {name}() — the argument is not a "
                    f"plain string literal"
                )
                continue
            found.setdefault(value, set()).add(relative)
    report["python_occurrences"] = sum(len(v) for v in found.values())
    report["python_files"] = [str(p.relative_to(base_dir).as_posix()) for p in files]
    report["python_skipped"] = skipped
    return found, plurals


# ---------------------------------------------------------------------------
# Чтение и запись .po
# ---------------------------------------------------------------------------
def escape_po(value: str) -> str:
    """Экранирование строки для .po (обратная к ``unescape_po``)."""
    return "\\n".join(_escape_piece(part) for part in value.split("\n"))


def unescape_po(value: str) -> str:
    """Снятие экранирования .po (обратная к ``escape_po``)."""
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "r":
                out.append("\r")
            elif nxt == '"':
                out.append('"')
            elif nxt == "\\":
                out.append("\\")
            else:
                out.append(nxt)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def split_po_line(line: str):
    """Возвращает (поле, строка) для строки .po вида ``msgid "…"``."""
    stripped = line.strip()
    if stripped.startswith("msgid_plural "):
        return "msgid_plural", stripped[len("msgid_plural "):].strip()
    for field in ("msgid", "msgstr"):
        if stripped.startswith(field + " "):
            return field, stripped[len(field) + 1:].strip()
    if stripped.startswith("msgstr["):
        bracket = stripped.find("]")
        if bracket > 0:
            return "msgstr[]", stripped[bracket + 1:].strip()
    if stripped.startswith('"'):
        return "continuation", stripped
    if stripped.startswith("#~"):
        return "obsolete", stripped
    return None, stripped


#: Любой перенос строки: LF, CRLF или одиночный CR.
_LINE_SPLIT = re.compile(r"\r\n|\n|\r")


def _split_lines(text: str) -> list[str]:
    """Делит текст на строки независимо от того, чем они разделены."""
    return _LINE_SPLIT.split(text)


def _strip_quotes(value: str) -> str:
    """Убирает внешние кавычки поля .po, сохраняя экранирование внутри.

    ``"он сказал \\"да\\""`` -> ``он сказал \\"да\\"``. Именно так, потому что
    ``unescape_po`` вызывается после и должен увидеть escape-последовательности.
    """
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


class PoEntry:
    """Запись каталога: msgid (+ msgid_plural) и переводы."""

    __slots__ = ("msgid", "msgid_plural", "msgstr", "msgstr_plural", "references")

    def __init__(self, msgid: str, msgid_plural: str = ""):
        self.msgid = msgid
        self.msgid_plural = msgid_plural
        self.msgstr = ""
        self.msgstr_plural: dict[int, str] = {}
        self.references: set[str] = set()

    @property
    def key(self) -> tuple[str, str]:
        return (self.msgid, self.msgid_plural)

    @property
    def translated(self) -> bool:
        if self.msgstr:
            return True
        return any(value for value in self.msgstr_plural.values())


def parse_po(path: Path):
    """Читает .po: обычные записи, устаревшие (``#~``) и заголовок.

    Возвращает (entries, obsolete, header_values).

    Переносы строк разбираются в любом виде (``\\n``, ``\\r\\n``, одиночный
    ``\\r``). Это важная подробность: ``build_locales`` перекодирует ``.po``
    через ``Path.write_text`` и на Windows превращает все переводы строк в
    ``\\r\\n``. Чтение в текстовом режиме съедает ``\\n``, а ``\\r`` остаётся
    приклеенным к значению, и на следующей сборке он попадает в каталог как
    настоящий символ возврата каретки. Поэтому здесь строки режутся
    регуляркой, а из значений убираются остатки ``\\r``.
    """
    text = path.read_bytes().decode("utf-8-sig")
    entries: dict[tuple[str, str], PoEntry] = {}
    obsolete: dict[tuple[str, str], PoEntry] = {}
    header: dict[str, str] = {}
    current: PoEntry | None = None
    current_field = None
    current_plural_index = 0
    entry_obsolete = False
    pending_obsolete = False

    def flush():
        nonlocal current, entry_obsolete
        if current is None:
            return
        if current.msgid == "" and current.msgstr and not current.msgid_plural:
            for line in _split_lines(current.msgstr):
                if ":" in line:
                    name, _, value = line.partition(":")
                    header[name.strip()] = value.strip()
        else:
            target = obsolete if entry_obsolete else entries
            target[current.key] = current
        current = None
        entry_obsolete = False

    for raw in _split_lines(text):
        line = raw
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#~"):
            if current is None:
                pending_obsolete = True
            stripped = stripped[2:].strip()
            if not stripped:
                continue
            line = stripped
        elif stripped.startswith("#"):
            continue

        field, value = split_po_line(line)
        # Остатки возврата каретки (следствие перезаписи .po командой
        # build_locales) в значение не пускаем: в каталоге им делать нечего.
        value = _strip_quotes(value).replace("\r", "")
        if field == "msgid":
            flush()
            current = PoEntry(unescape_po(value))
            entry_obsolete = pending_obsolete
            pending_obsolete = False
            current_field = "id"
            current_plural_index = 0
        elif field == "msgid_plural":
            if current is not None:
                current.msgid_plural = unescape_po(value)
            current_field = "id_plural"
        elif field == "msgstr":
            if current is not None:
                current.msgstr = unescape_po(value)
            current_field = "str"
        elif field == "msgstr[]":
            if current is not None:
                current.msgstr_plural[current_plural_index] = unescape_po(value)
                current_plural_index += 1
            current_field = "str_plural"
        elif field == "continuation":
            piece = unescape_po(value)
            if current is None:
                continue
            if current_field == "id":
                current.msgid += piece
            elif current_field == "id_plural":
                current.msgid_plural += piece
            elif current_field == "str":
                current.msgstr += piece
            elif current_field == "str_plural":
                current.msgstr_plural[current_plural_index] = (
                    current.msgstr_plural.get(current_plural_index, "") + piece
                )
    flush()
    return entries, obsolete, header


def po_lines(msgid: str, msgid_plural: str, msgstr: str, msgstr_plural: dict[int, str],
             references: set[str], obsolete: bool = False) -> list[str]:
    """Строки одной записи .po; многострочные значения — с продолжениями."""
    prefix = "#~ " if obsolete else ""
    lines: list[str] = []
    if references and not obsolete:
        for reference in sorted(references):
            lines.append(f"#: {reference}")
    lines.extend(_po_value(prefix, "msgid", msgid))
    if msgid_plural:
        lines.extend(_po_value(prefix, "msgid_plural", msgid_plural))
        indices = sorted(msgstr_plural) or [0, 1]
        for index in indices:
            lines.extend(_po_value(prefix, f"msgstr[{index}]",
                                   msgstr_plural.get(index, "")))
    else:
        lines.extend(_po_value(prefix, "msgstr", msgstr))
    return lines


def _po_value(prefix: str, field: str, value: str) -> list[str]:
    """Строки одного поля .po: многострочное значение — с продолжениями в кавычках."""
    if "\n" not in value:
        return [f'{prefix}{field} "{_escape_piece(value)}"']
    pieces = [_escape_piece(part) for part in value.split("\n")]
    out = [f'{prefix}{field} ""']
    for piece in pieces[:-1]:
        out.append(f'{prefix}"{piece}\\n"')
    out.append(f'{prefix}"{pieces[-1]}"')
    return out


def _escape_piece(value: str) -> str:
    """Экранирование фрагмента строки .po без переводов строк."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def build_po_text(header: dict[str, str], entries: list[PoEntry],
                  obsolete: list[PoEntry]) -> str:
    """Полный текст .po: комментарий, заголовок, записи, устаревшие."""
    out: list[str] = []
    out.append("# English translation of the school journal.")
    out.append("# Generated by: python manage.py make_locales (no GNU gettext needed).")
    out.append("# Only strings marked with {{% trans %}} / {{% blocktrans %}} / _().")
    out.append("# The file is always UTF-8: changing the encoding once broke the journal.")
    out.append("#")
    out.append('msgid ""')
    out.append('msgstr ""')
    for name in (
        "Project-Id-Version",
        "Report-Msgid-Bugs-To",
        "POT-Creation-Date",
        "PO-Revision-Date",
        "Last-Translator",
        "Language-Team",
        "Language",
        "MIME-Version",
        "Content-Type",
        "Content-Transfer-Encoding",
        "Plural-Forms",
    ):
        value = header.get(name, "")
        out.append(f'"{name}: {value}\\n"')
    for entry in entries:
        out.append("")
        out.extend(po_lines(entry.msgid, entry.msgid_plural, entry.msgstr,
                            entry.msgstr_plural, entry.references))
    if obsolete:
        out.append("")
        out.append("#~ Obsolete strings: gone from the sources, translations kept.")
        for entry in obsolete:
            out.append("#~")
            out.extend(po_lines(entry.msgid, entry.msgid_plural, entry.msgstr,
                                entry.msgstr_plural, set(), obsolete=True))
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Команда
# ---------------------------------------------------------------------------
class Command(BaseCommand):
    help = ("Extracts translatable strings from templates and Python code into "
            "django.po without GNU gettext")

    def add_arguments(self, parser):
        parser.add_argument(
            "--locale", default="en",
            help="language code whose catalog is updated (en by default)",
        )
        parser.add_argument(
            "--check", action="store_true",
            help="write nothing: only report whether every string is already in "
                 "the .po (exit code 1 if some are missing)",
        )
        parser.add_argument(
            "--no-backup", action="store_true",
            help="do not keep a backup copy django.po.bak",
        )

    # -- вспомогательное ---------------------------------------------------
    def locale_path(self, language: str) -> Path:
        roots = [Path(item) for item in getattr(settings, "LOCALE_PATHS", [])]
        root = roots[0] if roots else Path(settings.BASE_DIR) / DEFAULT_LOCALE_DIR
        return root / language / "LC_MESSAGES" / PO_FILENAME

    def base_dir(self) -> Path:
        return Path(settings.BASE_DIR)

    # -- основной сценарий -------------------------------------------------
    def handle(self, *args, **options):
        language = options["locale"]
        check_only = options["check"]
        base_dir = self.base_dir()
        po_path = self.locale_path(language)
        if language == SOURCE_LANGUAGE:
            raise CommandError(
                f"{SOURCE_LANGUAGE} is the source language: its own catalog is not needed."
            )

        report: dict = {}
        template_found = extract_templates(base_dir, base_dir, report)
        python_found, plurals = extract_python(base_dir, base_dir, report)

        # Строки из шаблонов и Python объединяются: msgid уникален.
        merged: dict[tuple[str, str], set[str]] = {}
        from_templates: set[str] = set()
        for msgid, references in template_found.items():
            plural = plurals.get(msgid, "")
            merged.setdefault((msgid, plural), set()).update(references)
            from_templates.add(msgid)
        for msgid, references in python_found.items():
            plural = plurals.get(msgid, "")
            merged.setdefault((msgid, plural), set()).update(references)

        if check_only:
            return self.run_check(po_path, merged)

        previous, obsolete_previous, old_header = (
            parse_po(po_path) if po_path.exists() else ({}, {}, {})
        )

        # Заголовок: сохраняем прежние значения, обновляем дату и язык.
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M+0000")
        header = {
            "Project-Id-Version": old_header.get("Project-Id-Version") or "school_journal",
            "Report-Msgid-Bugs-To": old_header.get("Report-Msgid-Bugs-To", ""),
            "POT-Creation-Date": now,
            "PO-Revision-Date": old_header.get("PO-Revision-Date", ""),
            "Last-Translator": old_header.get("Last-Translator", ""),
            "Language-Team": old_header.get("Language-Team", "English"),
            "Language": language,
            "MIME-Version": old_header.get("MIME-Version") or "1.0",
            "Content-Type": "text/plain; charset=UTF-8",
            "Content-Transfer-Encoding": old_header.get("Content-Transfer-Encoding") or "8bit",
            "Plural-Forms": old_header.get("Plural-Forms") or "nplurals=2; plural=(n != 1);",
        }

        entries: list[PoEntry] = []
        kept = 0
        new = 0
        for key in sorted(merged, key=lambda item: item[0]):
            msgid, msgid_plural = key
            entry = PoEntry(msgid, msgid_plural)
            entry.references = merged[key]
            old = previous.get(key)
            if old is not None and old.translated:
                entry.msgstr = old.msgstr
                entry.msgstr_plural = dict(old.msgstr_plural)
                kept += 1
            elif old is not None:
                entry.msgstr = old.msgstr
                entry.msgstr_plural = dict(old.msgstr_plural)
                new += 1
            else:
                new += 1
            entries.append(entry)

        # Устаревшие: всего, что не попало в текущий набор.
        live_keys = {entry.key for entry in entries}
        obsolete: list[PoEntry] = []
        for key in sorted(obsolete_previous):
            if key not in live_keys:
                obsolete.append(obsolete_previous[key])
        for key in sorted(previous):
            if key not in live_keys:
                entries_stale = previous[key]
                entries_stale.references = set()
                obsolete.append(entries_stale)

        text = build_po_text(header, entries, obsolete)
        data = text.encode("utf-8")

        # Резервная копия прежнего файла — до записи нового.
        backup_path = po_path.with_suffix(".po.bak")
        if po_path.exists() and not options["no_backup"]:
            po_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(po_path, backup_path)

        changed = True
        if po_path.exists():
            changed = po_path.read_bytes() != data
        po_path.parent.mkdir(parents=True, exist_ok=True)
        po_path.write_bytes(data)

        # Отчёт.
        try:
            shown_path = po_path.relative_to(base_dir)
        except ValueError:
            shown_path = po_path
        self.stdout.write(self.style.SUCCESS(f"{shown_path} — written in UTF-8"))
        self.stdout.write(
            f"  unique msgids from templates: {len(from_templates)} "
            f"({report.get('template_occurrences', 0)} occurrences)"
        )
        self.stdout.write(
            f"  unique msgids from Python:    {len(python_found)} "
            f"(out of {report.get('python_calls', 0)} calls)"
        )
        self.stdout.write(f"  unique msgids in total:       {len(entries)}")
        self.stdout.write(f"  new strings:                  {new}")
        self.stdout.write(f"  translations kept:            {kept}")
        self.stdout.write(f"  obsolete (at the end, #~):    {len(obsolete)}")
        self.stdout.write(f"  without a translation:        {len(entries) - kept}")
        if po_path.exists() and not changed:
            self.stdout.write("  the content did not change")
        if options["no_backup"]:
            self.stdout.write("  no backup was made (--no-backup)")
        elif backup_path.exists():
            self.stdout.write(f"  backup copy: {backup_path.name}")
        skipped = report.get("template_skipped", []) + report.get("python_skipped", [])
        if skipped:
            self.stdout.write(f"  skipped while parsing: {len(skipped)}")
            for item in skipped[:20]:
                self.stdout.write(f"    - {item}")

    def run_check(self, po_path: Path, merged: dict) -> None:
        """``--check``: ничего не пишет, только сообщает о непокрытых строках."""
        if not po_path.exists():
            self.stderr.write(self.style.ERROR(f"no translation file: {po_path}"))
            raise SystemExit(1)
        existing, _obsolete, _header = parse_po(po_path)
        missing = [key for key in merged if key not in existing]
        if missing:
            self.stderr.write(self.style.ERROR(
                f"strings not covered: {len(missing)} of {len(merged)}"
            ))
            for msgid, _plural in sorted(missing)[:25]:
                self.stderr.write(f"  - {msgid[:120]!r}")
            if len(missing) > 25:
                self.stderr.write(f"  … and {len(missing) - 25} more")
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(
            f"{po_path.name}: every string is in place ({len(merged)} msgids)"
        ))
