"""Логотип журнала: обрезка выбранной области и приведение к одному размеру.

Логотип в шапке — квадратный, поэтому загруженную картинку обрезаем до квадрата
на сервере через Pillow: браузер присылает только прямоугольник (x, y, сторона)
в пикселях исходного файла, а сюда попадает уже готовый квадрат 256 × 256 PNG.

Квадрат может выходить за картинку: в рамке обрезки масштаб можно уменьшить так,
что картинка целиком впишется в квадрат, и по краям останутся поля. Их
закрашиваем белым — ровно так же, как их видно в браузере (что видно, то и
сохраняется).

Если координаты не пришли (JavaScript отключён, чужой клиент, старый браузер),
берём квадрат по центру картинки: загрузка не падает и логотип всё равно
получается ровно того размера, который ждёт шаблон.
"""
from io import BytesIO

from django.core.files.base import ContentFile

#: Pillow нужен только здесь: без него Django всё равно не сохранит ImageField,
#: поэтому импорт не делаем на уровне модуля — иначе ломались бы все команды.
try:  # pragma: no cover - зависит от окружения
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None
    ImageOps = None

#: Сторона сохраняемого логотипа в пикселях.
LOGO_SIZE = 256

#: Имена полей формы, в которых браузер присылает выбранную область обрезки.
CROP_X_FIELD = "logo_crop_x"
CROP_Y_FIELD = "logo_crop_y"
CROP_SIZE_FIELD = "logo_crop_size"

#: Все поля области обрезки по порядку: x, y, сторона квадрата.
CROP_FIELDS = (CROP_X_FIELD, CROP_Y_FIELD, CROP_SIZE_FIELD)

#: Цвет подложки: прозрачные края логотипа не должны стать чёрными.
BACKGROUND = (255, 255, 255)

#: Имя файла результата: расширение важно для проверки формата при сохранении.
LOGO_FILENAME = "logo.png"

#: Во сколько раз сторона области может превышать исходник. При уменьшенном
#: масштабе в рамку попадает вся картинка с полями, поэтому сторона области
#: больше самой картинки; предел защищает от мусора в POST.
MAX_CROP_FACTOR = 8


class LogoProcessingError(Exception):
    """Картинку не удалось прочитать, обрезать или уменьшить."""


def _as_int(raw):
    """Терпеливо превращает значение из формы в целое; мусор — ``None``.

    Значения приходят из браузера, поэтому приводим и «12.4», и « 12 », и
    пустую строку — к числу или к ``None``, без исключений.
    """
    try:
        return int(float(str(raw).strip().replace(",", ".")))
    except (AttributeError, TypeError, ValueError):
        return None


def center_rect(width, height):
    """Квадрат по центру картинки: запасной вариант, когда координат нет."""
    side = min(width, height)
    if side <= 0:
        raise LogoProcessingError("у картинки нет размера")
    return (width - side) // 2, (height - side) // 2, side


def parse_rect(data, width, height):
    """Область обрезки из POST: ``(x, y, сторона)`` в пикселях исходника.

    Координатам браузера не доверяем: округляем до целых и держим в разумных
    пределах. Если пришло не всё (или пришёл мусор) — обрезаем по центру.

    Область намеренно может выходить за картинку: в рамке обрезки масштаб можно
    уменьшить так, что картинка целиком впишется в квадрат (fit), и вокруг неё
    останутся белые поля. Требуем только, чтобы внутри области была хоть одна
    точка картинки, — иначе сохранялся бы пустой белый квадрат.
    """
    if not data:
        return center_rect(width, height)
    values = [_as_int(data.get(name)) for name in CROP_FIELDS]
    if any(value is None for value in values):
        return center_rect(width, height)
    x, y, side = values
    side = min(side, MAX_CROP_FACTOR * max(width, height))
    if side <= 0:
        return center_rect(width, height)
    x = min(max(x, -side + 1), width - 1)
    y = min(max(y, -side + 1), height - 1)
    return x, y, side


def render_square(image, x, y, side, size):
    """Вписать квадрат исходника ``(x, y, side)`` в картинку ``size × size``.

    Квадрат вырезаем из исходника, уменьшаем до ``size × size`` и подкладываем
    под него белый фон: прозрачные пиксели и поля за пределами картинки не
    должны стать чёрными. Всё, что выходит за картинку, остаётся белым — так же,
    как это видно в рамке обрезки.
    """
    canvas = Image.new("RGB", (size, size), BACKGROUND)
    left = max(x, 0)
    top = max(y, 0)
    right = min(x + side, image.width)
    bottom = min(y + side, image.height)
    if right <= left or bottom <= top:
        return canvas
    region = image.crop((left, top, right, bottom))
    # Сколько пикселей результата приходится на пиксель исходника.
    factor = size / side
    target = (
        max(1, round((right - left) * factor)),
        max(1, round((bottom - top) * factor)),
    )
    if target != region.size:
        algorithm = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        region = region.resize(target, algorithm)
    canvas.paste(
        region,
        (round((left - x) * factor), round((top - y) * factor)),
        region,
    )
    return canvas


def square_logo(upload, data=None, size=LOGO_SIZE):
    """Обрезать загруженную картинку до квадрата и вернуть PNG нужного размера.

    ``upload`` — файл из формы (``UploadedFile``), ``data`` — данные POST с
    полями ``logo_crop_*``. Результат — ``ContentFile`` с именем ``logo.png``,
    его можно сразу присвоить полю модели.
    """
    if Image is None or ImageOps is None:
        raise LogoProcessingError("Pillow не установлен")

    try:
        with Image.open(upload) as opened:
            # Телефоны любят хранить поворот в EXIF: без этого логотип «лежит».
            image = ImageOps.exif_transpose(opened).convert("RGBA")
            x, y, side = parse_rect(data, image.width, image.height)
            canvas = render_square(image, x, y, side, size)
            buffer = BytesIO()
            canvas.save(buffer, format="PNG", optimize=True)
    except LogoProcessingError:
        raise
    except Exception as error:  # noqa: BLE001 - любая ошибка Pillow = «плохой файл»
        raise LogoProcessingError(str(error)) from error
    finally:
        if hasattr(upload, "seek"):
            upload.seek(0)
    return ContentFile(buffer.getvalue(), name=LOGO_FILENAME)
