"""Предпросмотр оформления на странице «Кастомизация».

Заказчик просил, чтобы блок «Как это выглядит» показывал то же, что человек
видит в шапке: загруженный логотип, затем стандартный знак проекта и фавикон,
а текстовые заглушки «ЭЖ» и «по умолчанию» — только если картинок нет вовсе.
Тесты проверяют все три состояния и то, что картинки находятся именно в блоке
предпросмотра.
"""
import re
import shutil
from io import BytesIO
from pathlib import Path
from unittest import mock
from uuid import uuid4

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from journal.models import SiteSettings, User

PASSWORD = "TestPass-2024-for-tests"


def make_superadmin():
    return User.objects.create_user(
        username="preview-admin",
        password=PASSWORD,
        first_name="Проверка",
        last_name="Превью",
        role=User.Role.SUPERADMIN,
        is_active=True,
        is_superuser=True,
    )


def png(side=16, color=(20, 60, 220)):
    image = Image.new("RGB", (side, side), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def form_data(**extra):
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


class CustomizationPreviewTests(TestCase):
    """Блок «Как это выглядит»: знак проекта и фавикон по умолчанию."""

    def setUp(self):
        self.media = Path(settings.MEDIA_ROOT) / f"test-{uuid4().hex}"
        self.media.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.media, True)
        self.override = override_settings(MEDIA_ROOT=str(self.media))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.client.force_login(make_superadmin())

    @staticmethod
    def block(html, marker):
        """Внутренняя разметка блока предпросмотра по его data-атрибуту."""
        match = re.search(
            r'data-' + marker + r'[^>]*>(.*?)</div>', html, re.DOTALL
        )
        return match.group(1) if match else ""

    def test_preview_shows_project_logo_and_favicon_by_default(self):
        """Без своих файлов: квадратный знак проекта и favicon.png с подписью."""
        page = self.client.get(reverse("customization"))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()

        logo = self.block(html, "logo-preview-frame")
        self.assertIn("/static/journal/logo-square.png", logo)
        self.assertNotIn("ЭЖ", logo, "текстовый бейдж больше не рисуется")

        favicon = self.block(html, "favicon-preview")
        self.assertIn("/static/journal/favicon.png", favicon)
        self.assertIn("по умолчанию", favicon)

    def test_preview_shows_uploaded_logo_instead_of_default(self):
        """После загрузки в предпросмотре стоит свой логотип, а не знак проекта."""
        response = self.client.post(
            reverse("customization"),
            form_data(
                logo=SimpleUploadedFile(
                    "logo.png", png(), content_type="image/png"
                )
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SiteSettings.load().logo)

        page = self.client.get(reverse("customization"))
        html = page.content.decode()
        logo = self.block(html, "logo-preview-frame")
        self.assertIn("/media/branding/", logo)
        self.assertNotIn("/static/journal/logo-square.png", logo)

    def test_text_fallbacks_only_when_images_are_missing(self):
        """Файлов нет — только тогда «ЭЖ» и «по умолчанию» без картинок."""
        with mock.patch("journal.views.finders.find", return_value=None):
            page = self.client.get(reverse("customization"))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()

        logo = self.block(html, "logo-preview-frame")
        self.assertIn("ЭЖ", logo)
        self.assertNotIn("/static/journal/logo-square.png", logo)

        favicon = self.block(html, "favicon-preview")
        self.assertIn("по умолчанию", favicon)
        self.assertNotIn("/static/journal/favicon.png", favicon)
