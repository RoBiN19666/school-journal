"""Язык на экране первоначальной настройки: переключатель и его сохранение.

Пока в журнале нет администратора, ``FirstRunSetupMiddleware`` уводит все
страницы на ``/setup/``. Раньше под этот редирект попадал и штатный
``/i18n/setlang/``: форма переключателя отправлялась, но запрос не доходил до
Django, и язык не менялся. Здесь проверяем, что переключатель есть на экране,
что POST действительно ставит cookie и что выбранный язык остаётся после
завершения настройки.
"""
from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from journal.models import User

PASSWORD = "TestPass-2024-for-tests"
SETUP_TITLE_RU = "Первый запуск журнала"
SETUP_TITLE_EN = "First launch of the journal"


class SetupLanguageTests(TestCase):
    """Переключатель языка на мастере первого запуска."""

    def test_setup_page_has_language_switcher(self):
        """На /setup/ есть форма смены языка, отправленная на set_language."""
        response = self.client.get(reverse("setup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'action="/i18n/setlang/"')
        self.assertContains(response, 'name="language" value="en"')
        self.assertContains(response, 'name="next" value="/setup/"')
        self.assertContains(response, SETUP_TITLE_RU)

    def test_language_switch_from_setup_sets_cookie_and_redirects_back(self):
        """POST set_language с next=/setup/ ставит cookie и возвращает в мастер."""
        response = self.client.post(
            reverse("set_language"),
            {"language": "en", "next": reverse("setup")},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("setup"))
        cookie = response.cookies[settings.LANGUAGE_COOKIE_NAME]
        self.assertEqual(cookie.value, "en")

        page = self.client.get(reverse("setup"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["Content-Language"], "en")
        self.assertContains(page, SETUP_TITLE_EN)

    def test_language_choice_survives_setup_completion(self):
        """Язык, выбранный в мастере, остаётся после создания администратора."""
        self.client.post(
            reverse("set_language"),
            {"language": "en", "next": reverse("setup")},
        )

        response = self.client.post(
            reverse("setup"),
            {
                "username": "setup-admin",
                "last_name": "Adminov",
                "first_name": "Admin",
                "middle_name": "",
                "email": "",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "product_name": "Electronic journal",
                "organization_name": "School",
                "short_logo": "EJ",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("dashboard"))
        self.assertTrue(User.objects.filter(username="setup-admin").exists())
        self.assertEqual(
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en"
        )

        page = self.client.get(reverse("about"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["Content-Language"], "en")
        self.assertContains(page, "About the product")
        self.assertNotContains(page, "О продукте")
