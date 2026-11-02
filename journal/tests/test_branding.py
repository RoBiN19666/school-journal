"""Тесты логотипа: загрузка через «Кастомизацию», обрезка и показ в шапке.

Проверяем то, что видит заказчик: после загрузки логотип становится квадратным
PNG 256 × 256, обрезка учитывает выбранную область (а без координат берётся
центр), в шапке загруженная картинка стоит ВМЕСТО стандартного бейджа, а
проверки формата и размера файла остались прежними.

Как и в остальных тестах, сначала создаётся действующий администратор: пока в
базе нет ни одного, ``FirstRunSetupMiddleware`` уводит все страницы на /setup/.
"""
import os
import shutil
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image, ImageDraw

from journal.models import SiteSettings, User

PASSWORD = "TestPass-2024-for-tests"
RED = (220, 20, 60)
BLUE = (20, 60, 220)
TOLERANCE = 30


def make_admin():
    """Главный администратор: раздел «Кастомизация» открыт только ему."""
    return User.objects.create_user(
        username="logo-admin",
        password=PASSWORD,
        first_name="Логотип",
        last_name="Админов",
        role=User.Role.SUPERADMIN,
        is_active=True,
        is_superuser=True,
    )


def split_png(width, height, split):
    """PNG с явной асимметрией: левая часть красная, правая — синяя."""
    image = Image.new("RGB", (width, height), BLUE)
    ImageDraw.Draw(image).rectangle([0, 0, split - 1, height], fill=RED)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def noise_png(side):
    """Большой PNG на случайных пикселях: сжатие его почти не уменьшает."""
    image = Image.frombytes("RGB", (side, side), os.urandom(side * side * 3))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def upload(name, content):
    return SimpleUploadedFile(name, content, content_type="image/png")


class LogoTestCase(TestCase):
    """Общая обвязка: временная папка media и вошедший главный администратор."""

    def setUp(self):
        # Папка внутри media: её не жалко, она в .gitignore и рядом с проектом.
        self.media = Path(settings.MEDIA_ROOT) / f"test-{uuid4().hex}"
        self.media.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.media, True)
        self.override = override_settings(MEDIA_ROOT=str(self.media))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def settings_data(self, **extra):
        """Поля формы «Кастомизация»: остальные значения — обычные настройки."""
        data = {
            "organization_name": "Образовательная организация",
            "product_name": "Электронный журнал",
            "short_logo": "ЭЖ",
            "login_hint": "Ставьте галочку только на своём компьютере.",
            "remember_days": "30",
            "session_hours": "12",
        }
        data.update(extra)
        return data

    def save_logo(self, content, name="logo.png", **extra):
        """Отправить картинку на страницу «Кастомизация» как логотип."""
        return self.client.post(
            reverse("customization"),
            self.settings_data(logo=upload(name, content), **extra),
        )

    def saved_logo(self):
        """Сохранённый логотип как картинка Pillow (плюс проверка, что файл есть)."""
        site = SiteSettings.load()
        self.assertTrue(site.logo, "логотип не сохранился")
        path = Path(site.logo.path)
        self.assertTrue(path.exists(), f"файла логотипа нет: {path}")
        with Image.open(path) as image:
            return image.convert("RGB")

    def assertColor(self, pixel, expected, message=""):
        """Цвет пикселя с запасом: PNG без потерь, но запас не мешает."""
        self.assertTrue(
            all(abs(value - want) <= TOLERANCE for value, want in zip(pixel, expected)),
            f"{message}: цвет {pixel} не совпадает с {expected}",
        )

    @staticmethod
    def header_html(response):
        """Фрагмент шапки: в нём и находится бейдж логотипа."""
        html = response.content.decode()
        return html[html.index("<header"):html.index("</header>") + len("</header>")]


class LogoUploadTests(LogoTestCase):
    """Загрузка и обрезка: сервер сам приводит картинку к квадрату 256 × 256."""

    def test_upload_crops_selected_area(self):
        """Картинка 950 × 259: выбранная правая область даёт синий квадрат 256 × 256."""
        content = split_png(950, 259, split=475)
        response = self.save_logo(
            content, logo_crop_x="660", logo_crop_y="0", logo_crop_size="259"
        )
        self.assertEqual(response.status_code, 302)

        with self.saved_logo() as image:
            self.assertEqual(image.size, (256, 256))
            self.assertEqual(image.width, image.height)
            # Область x = 660…919 целиком правая (синяя): красного быть не должно.
            for point in ((4, 4), (128, 128), (251, 4), (4, 251), (251, 251)):
                self.assertColor(image.getpixel(point), BLUE, f"точка {point}")

        # Рамка обрезки подключается своей статикой, без CDN и внешних библиотек.
        page = self.client.get(reverse("customization"))
        html = page.content.decode()
        self.assertIn('name="logo"', html)
        self.assertIn('src="/static/journal/logo-crop.js?v=3"', html)
        self.assertIn("data-logo-crop-config", html)

    def test_upload_without_coordinates_uses_center_square(self):
        """Без координат (JavaScript выключен) сервер обрезает по центру."""
        content = split_png(800, 200, split=400)
        response = self.save_logo(content)
        self.assertEqual(response.status_code, 302)

        with self.saved_logo() as image:
            self.assertEqual(image.size, (256, 256))
            # Центр исходника (x = 300…500) — ровно стык половин.
            self.assertColor(image.getpixel((20, 128)), RED, "левая половина")
            self.assertColor(image.getpixel((235, 128)), BLUE, "правая половина")

    def test_bad_files_are_rejected(self):
        """Прежние проверки не сломаны: SVG, GIF и файл больше 1 МБ не проходят."""
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        response = self.save_logo(svg, name="logo.svg")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SiteSettings.load().logo, "SVG не должен сохраняться")

        gif = BytesIO()
        Image.new("RGB", (10, 10), RED).save(gif, format="GIF")
        response = self.save_logo(gif.getvalue(), name="logo.gif")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SiteSettings.load().logo, "GIF не должен сохраняться")

        response = self.save_logo(noise_png(760))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 МБ")
        self.assertFalse(SiteSettings.load().logo, "файл больше 1 МБ не сохраняется")


class LogoDisplayTests(LogoTestCase):
    """Показ: загруженная картинка встаёт вместо бейджа, без иконки внутри."""

    def test_logo_replaces_badge_in_header_and_login(self):
        """В шапке и на входе: <img> с тем же размером и скруглением, без «ЭЖ»."""
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page.status_code, 200)
        header = self.header_html(page)
        self.assertIn(
            "/static/journal/logo-square.png",
            header,
            "без своего логотипа показывается знак проекта",
        )
        self.assertNotIn("/media/branding/", header)

        content = split_png(400, 400, split=200)
        response = self.save_logo(
            content, logo_crop_x="0", logo_crop_y="0", logo_crop_size="400"
        )
        self.assertEqual(response.status_code, 302)

        page = self.client.get(reverse("dashboard"))
        header = self.header_html(page)
        self.assertIn('<img src="/media/branding/', header)
        self.assertIn("object-contain", header)
        self.assertIn("rounded-xl", header)
        self.assertIn("h-8 w-8", header)
        self.assertIn("p-0.5", header, "у картинки белая подложка")
        self.assertNotIn("ЭЖ", header, "короткий знак не должен показываться поверх")

        self.client.logout()
        login = self.client.get(reverse("login"))
        self.assertEqual(login.status_code, 200)
        html = login.content.decode()
        self.assertIn('<img src="/media/branding/', html)
        self.assertIn("rounded-2xl", html)
        self.assertIn("object-contain", html)


class LogoFitCropTests(LogoTestCase):
    """Уменьшенный масштаб: картинка вписана в квадрат, поля белые."""

    def test_zoom_out_keeps_whole_image_with_white_margins(self):
        """При fit (950 × 259 в рамке 288 × 288) сохраняется вся картинка и поля.

        Браузер присылает квадрат шире картинки: x = 0, сторона 950, а по
        вертикали он выходит за картинку (y = −346). Сервер обязан оставить
        белые поля сверху и снизу — иначе сохранённый логотип не совпадёт с
        тем, что видно в рамке.
        """
        content = split_png(950, 259, split=475)
        response = self.save_logo(
            content, logo_crop_x="0", logo_crop_y="-346", logo_crop_size="950"
        )
        self.assertEqual(response.status_code, 302)

        with self.saved_logo() as image:
            self.assertEqual(image.size, (256, 256))
            self.assertColor(image.getpixel((128, 4)), (255, 255, 255), "верхнее поле")
            self.assertColor(image.getpixel((128, 251)), (255, 255, 255), "нижнее поле")
            # В середине — сама картинка: слева красная половина, справа синяя.
            self.assertColor(image.getpixel((20, 128)), RED, "левая половина")
            self.assertColor(image.getpixel((235, 128)), BLUE, "правая половина")

    def test_form_error_keeps_crop_dialog(self):
        """После ошибки формы страница снова содержит поле, настройки и скрипт.

        Рамку обрезки открывает logo-crop.js по change на input[name=logo];
        после перерисовки страницы он должен подключиться заново, иначе окно
        «не открывается после отправки с ошибкой».
        """
        response = self.client.post(
            reverse("customization"),
            self.settings_data(
                product_name="",  # обязательное поле пустое — форма с ошибкой
                logo=upload("logo.png", split_png(120, 80, split=60)),
            ),
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("data-logo-crop-config", html)
        self.assertIn('src="/static/journal/logo-crop.js?v=3"', html)
        self.assertIn('type="file" name="logo"', html)
        self.assertIn('name="favicon"', html)
        # Отметку «скрипт привязан» ставит JavaScript уже в браузере, сервер её не рисует.
        self.assertNotIn("data-logo-crop-ready", html)


class LogoCropScriptTests(LogoTestCase):
    """Скрипт рамки обрезки не зависит от blob:: на бою CSP разрешает только data:.

    Боевой сервер отдаёт Content-Security-Policy с ``default-src * data: …``, где
    схема ``blob:`` не перечислена. Картинка, созданная через
    ``URL.createObjectURL(file)``, в такой политике не загружается, и рамка
    обрезки не открывалась. Поэтому файл читается как data:-URL.
    """

    def crop_script(self):
        """Исходник скрипта рамки (без collectstatic, без сети)."""
        path = Path(settings.BASE_DIR) / "journal" / "static" / "journal" / "logo-crop.js"
        self.assertTrue(path.exists(), f"нет файла {path}")
        return path.read_text(encoding="utf-8")

    @staticmethod
    def code_lines(source):
        """Только строки кода: в комментариях слова blob:/createObjectURL допустимы."""
        return [
            line
            for line in source.splitlines()
            if not line.strip().startswith(("*", "/*", "//"))
        ]

    def test_reads_file_as_data_url_without_blob(self):
        """Файл читается через readAsDataURL, объектных URL скрипт не создаёт."""
        source = self.crop_script()
        self.assertIn("readAsDataURL", source, "файл должен читаться как data:-URL")
        code = "\n".join(self.code_lines(source))
        self.assertNotIn(
            "createObjectURL",
            code,
            "blob:-адрес не создаём: CSP на бою его запрещает",
        )
        self.assertNotIn("blob:", code, "в коде не должно остаться схемы blob:")
        self.assertNotIn(
            "revokeObjectURL",
            code,
            "отзывать нечего: объектных URL больше нет",
        )

    def test_script_version_in_page_is_current(self):
        """Метка версии поднята, иначе браузер возьмёт старый скрипт из кэша."""
        html = self.client.get(reverse("customization")).content.decode()
        self.assertIn('src="/static/journal/logo-crop.js?v=3"', html)
        self.assertNotIn("logo-crop.js?v=2", html)


class FaviconFileTests(LogoTestCase):
    """Фавикон: предсказуемое имя, замена без накопления файлов, очистка."""

    def save_favicon(self, content, name="icon.png", **extra):
        """Отправить картинку на страницу «Кастомизация» как фавикон."""
        return self.client.post(
            reverse("customization"),
            self.settings_data(favicon=upload(name, content), **extra),
        )

    def test_favicon_has_predictable_name(self):
        """Файл сохраняется как branding/favicon.png, а не со случайным суффиксом."""
        response = self.save_favicon(split_png(64, 64, split=32))
        self.assertEqual(response.status_code, 302)
        site = SiteSettings.load()
        self.assertEqual(site.favicon.name, "branding/favicon.png")
        path = Path(site.favicon.path)
        self.assertTrue(path.exists(), f"файла фавикона нет: {path}")
        self.assertEqual(path.name, "favicon.png")

    def test_replacing_favicon_removes_old_file(self):
        """Новый фавикон в другом формате: старый файл удалён, новый на месте."""
        self.save_favicon(split_png(64, 64, split=32), name="first.png")
        old_path = Path(SiteSettings.load().favicon.path)
        self.assertTrue(old_path.exists())

        response = self.save_favicon(split_png(32, 32, split=16), name="second.jpg")
        self.assertEqual(response.status_code, 302)

        site = SiteSettings.load()
        self.assertEqual(site.favicon.name, "branding/favicon.jpg")
        new_path = Path(site.favicon.path)
        self.assertTrue(new_path.exists(), f"файла фавикона нет: {new_path}")
        self.assertFalse(old_path.exists(), "старый favicon.png должен быть удалён")
        self.assertEqual(
            sorted(p.name for p in new_path.parent.glob("favicon.*")),
            ["favicon.jpg"],
            "в branding/ должен остаться только один фавикон",
        )

    def test_clear_removes_favicon(self):
        """«Очистить» работает как раньше и убирает файл с диска."""
        self.save_favicon(split_png(64, 64, split=32))
        path = Path(SiteSettings.load().favicon.path)
        self.assertTrue(path.exists())

        response = self.client.post(
            reverse("customization"), self.settings_data(**{"favicon-clear": "on"})
        )
        self.assertEqual(response.status_code, 302)
        site = SiteSettings.load()
        self.assertFalse(site.favicon, "фавикон должен быть очищен")
        self.assertFalse(path.exists(), "файл очищенного фавикона должен быть удалён")

    def test_current_file_block_is_styled(self):
        """Вместо голой строки — предпросмотр, имя без папки и «Очистить»."""
        self.save_logo(split_png(100, 100, split=50))
        self.save_favicon(split_png(64, 64, split=32))
        html = self.client.get(reverse("customization")).content.decode()

        # По блоку на каждый уже сохранённый файл: логотип и фавикон.
        self.assertEqual(html.count("data-branding-current"), 2)
        self.assertRegex(html, r">logo[^<]*\.png</span>")
        self.assertIn(">favicon.png</span>", html)
        # Стандартная строка Django «На данный момент: branding/…» больше не выводится.
        self.assertNotIn("На данный момент", html)
        # Чекбоксы очистки остались настоящими полями ClearableFileInput.
        self.assertIn('name="logo-clear"', html)
        self.assertIn('name="favicon-clear"', html)
