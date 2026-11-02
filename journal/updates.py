"""Проверка новых версий журнала по данным GitHub Releases.

Запрос идёт к публичному API GitHub. Сеть может быть недоступна (закрытый
контур, нет интернета, GitHub отвечает ошибкой) — это не авария журнала,
поэтому наружу не выходит ни одного исключения: при любой проблеме функция
возвращает ``None``, а страница «О программе» показывает спокойную строку
«Не удалось проверить обновления».

Ответ кэшируется на час: страницу «О программе» открывают нечасто, но и
дёргать GitHub на каждый заход не нужно. Кэшируется и неудачная проверка —
иначе при отсутствии сети каждый заход снова ждал бы таймаут.
"""
import json
import logging
import re
from urllib.request import Request, urlopen

from django.core.cache import cache

logger = logging.getLogger(__name__)

#: Публичный адрес последнего релиза (не черновика и не пре-релиза).
RELEASE_API_URL = (
    "https://api.github.com/repos/RoBiN19666/school-journal/releases/latest"
)
#: Страница со всеми релизами: ссылка для случая, когда проверка не удалась.
RELEASES_PAGE_URL = "https://github.com/RoBiN19666/school-journal/releases"
#: Ключ кэша. Значение — словарь ``{"release": ...}``: так можно отличить
#: «в кэше лежит неудача (None)» от «в кэше ничего нет».
CACHE_KEY = "journal:latest-release"
#: Сколько секунд живёт результат проверки.
CACHE_TIMEOUT = 60 * 60
#: Сколько ждать ответа GitHub. Страница не должна зависать из-за проверки.
REQUEST_TIMEOUT = 3
#: GitHub отклоняет запросы без User-Agent (403), поэтому заголовок обязателен.
USER_AGENT = "school-journal-update-check (+https://github.com/RoBiN19666/school-journal)"

#: Начало версии: необязательная «v» и числа через точку. Хвост после них
#: (-rc1, +build, «beta») при сравнении не учитывается.
_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)")


def _version_parts(value):
    """Числовые части версии или ``None``, если строку не разобрать."""
    if value is None:
        return None
    match = _VERSION_RE.match(str(value).strip())
    if match is None:
        return None
    return [int(part) for part in match.group(1).split(".")]


def is_newer(current, tag):
    """Новее ли версия ``tag`` установленной ``current``.

    Сравниваются числа, а не строки: ``2.42.0`` новее ``2.41.2`` (строка
    сравнила бы их наоборот). Префикс ``v`` и хвосты вроде ``2.43.0-rc1``
    не мешают; отсутствующие части считаются нулями (``2.42`` = ``2.42.0``).
    Если разобрать версии не удалось, считается, что обновления нет: лучше
    промолчать, чем предложить ложное обновление.
    """
    current_parts = _version_parts(current)
    tag_parts = _version_parts(tag)
    if current_parts is None or tag_parts is None:
        return False
    width = max(len(current_parts), len(tag_parts))
    current_parts += [0] * (width - len(current_parts))
    tag_parts += [0] * (width - len(tag_parts))
    return tag_parts > current_parts


def _parse_release(payload):
    """Достаёт из ответа GitHub нужные поля; без тега релиз бесполезен."""
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        return None
    name = str(payload.get("name") or "").strip()
    return {
        "tag": tag,
        "name": name or tag,
        "url": str(payload.get("html_url") or RELEASES_PAGE_URL),
        "published": str(payload.get("published_at") or ""),
    }


def _fetch_release():
    """Один запрос к GitHub. Любая ошибка — ``None`` и запись в журнал."""
    try:
        request = Request(
            RELEASE_API_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read()
        return _parse_release(json.loads(raw.decode("utf-8")))
    except Exception as error:  # noqa: BLE001 - сеть не должна ломать страницу
        logger.warning("Could not check for a new release: %s", error)
        return None


def latest_release():
    """Последний релиз с GitHub или ``None``, если узнать не удалось.

    Результат (в том числе неудача) кэшируется на час, поэтому повторные
    вызовы в течение часа в сеть не ходят.
    """
    try:
        cached = cache.get(CACHE_KEY)
    except Exception as error:  # noqa: BLE001 - сбой кэша не должен ломать страницу
        logger.warning("Could not read the update cache: %s", error)
        cached = None
    if isinstance(cached, dict) and "release" in cached:
        return cached["release"]
    release = _fetch_release()
    try:
        cache.set(CACHE_KEY, {"release": release}, CACHE_TIMEOUT)
    except Exception as error:  # noqa: BLE001 - без кэша страница всё равно работает
        logger.warning("Could not cache the update check: %s", error)
    return release
