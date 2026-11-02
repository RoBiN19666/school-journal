"""Проверка обновлений на странице «О программе».

Заказчик просил показать главному администратору, что вышла новая версия, и не
ронять страницу, когда GitHub недоступен. Тесты проверяют сравнение версий по
числам, ответ GitHub и кэш (второй вызов не должен ходить в сеть), а также три
состояния блока: «есть новее», «последняя версия» и «проверить не удалось».
Блок видят только главные администраторы: обычному администратору и методисту
он не показывается.
"""
import json
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from journal import __version__, updates
from journal.models import User

PASSWORD = "TestPass-2024-for-tests"
RELEASE = {
    "tag": "v9.9.9",
    "name": "Release 9.9.9",
    "url": "https://github.com/RoBiN19666/school-journal/releases/tag/v9.9.9",
    "published": "2026-11-01T12:00:00Z",
}


def make_user(username, role, superuser=False):
    return User.objects.create_user(
        username=username,
        password=PASSWORD,
        first_name="Тест",
        last_name=username.title(),
        role=role,
        is_active=True,
        is_staff=superuser,
        is_superuser=superuser,
    )


class FakeResponse:
    """Минимальный ответ urllib: контекстный менеджер и read()."""

    def __init__(self, payload):
        self.payload = payload

    def read(self, size=-1):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class VersionComparisonTests(SimpleTestCase):
    """Версии сравниваются по числам, а не как строки."""

    def test_is_newer_compares_version_numbers(self):
        cases = [
            ("2.41.2", "2.42.0", True),
            ("2.41.2", "v2.42.0", True),
            ("v2.41.2", "2.42.0", True),
            ("2.42.0", "2.42.0", False),
            ("2.42.0", "v2.42", False),
            ("2.42", "2.42.0", False),
            # Хвост после чисел не учитывается: это та же версия.
            ("2.42.0", "2.42.0-rc1", False),
            ("2.42.0", "2.42.0+build.7", False),
            ("2.42.0", "2.42.1", True),
            ("2.42.0", "2.43", True),
            ("2.42.0", "garbage", False),
            ("garbage", "2.42.0", False),
            (None, "2.42.0", False),
        ]
        for current, tag, expected in cases:
            with self.subTest(current=current, tag=tag):
                self.assertIs(updates.is_newer(current, tag), expected)


class LatestReleaseTests(SimpleTestCase):
    """Запрос к GitHub: поля ответа, таймаут, User-Agent и кэш на час."""

    def setUp(self):
        cache.delete(updates.CACHE_KEY)
        self.addCleanup(cache.delete, updates.CACHE_KEY)

    def test_latest_release_is_fetched_once_and_cached(self):
        calls = []
        payload = json.dumps(
            {
                "tag_name": "v9.9.9",
                "name": "Release 9.9.9",
                "html_url": RELEASE["url"],
                "published_at": RELEASE["published"],
            }
        ).encode("utf-8")

        def fake_urlopen(request, timeout=None):
            calls.append((request, timeout))
            return FakeResponse(payload)

        with mock.patch.object(updates, "urlopen", side_effect=fake_urlopen):
            first = updates.latest_release()
            second = updates.latest_release()

        self.assertEqual(first, RELEASE)
        self.assertEqual(second, first)
        # Второй вызов берёт результат из кэша: сеть дёргается один раз.
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(request.full_url, updates.RELEASE_API_URL)
        self.assertEqual(timeout, updates.REQUEST_TIMEOUT)
        self.assertTrue(request.get_header("User-agent"))


class AboutUpdateBlockTests(TestCase):
    """Блок обновлений на /about/: только для главного администратора."""

    def setUp(self):
        self.superadmin = make_user(
            "update-owner", User.Role.SUPERADMIN, superuser=True
        )
        self.admin = make_user("update-admin", User.Role.ADMIN)
        self.methodist = make_user("update-methodist", User.Role.METHODIST)
        cache.delete(updates.CACHE_KEY)
        self.addCleanup(cache.delete, updates.CACHE_KEY)

    def about_page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("about"))

    def test_superadmin_sees_available_update_and_others_do_not(self):
        """Есть новее: версия, ссылка на релиз и команда ./update.sh."""
        with mock.patch.object(updates, "latest_release", return_value=RELEASE):
            page = self.about_page(self.superadmin)
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, "data-update-check")
            self.assertContains(page, f"Ваша версия: {__version__}")
            self.assertContains(page, "Доступна версия v9.9.9")
            self.assertContains(page, RELEASE["url"])
            self.assertContains(page, "./update.sh")
            self.assertContains(page, "руководстве администратора")

            # Обычный администратор и методист блока не видят вовсе.
            for user in (self.admin, self.methodist):
                with self.subTest(role=user.role):
                    self.client.force_login(user)
                    page = self.client.get(reverse("about"))
                    self.assertEqual(page.status_code, 200)
                    self.assertNotContains(page, "data-update-check")
                    self.assertNotContains(page, "Доступна версия")

    def test_superadmin_sees_up_to_date_message(self):
        """Установлена последняя версия: спокойное подтверждение."""
        release = dict(RELEASE, tag=__version__)
        with mock.patch.object(updates, "latest_release", return_value=release):
            page = self.about_page(self.superadmin)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "У вас последняя версия")
        self.assertNotContains(page, "Доступна версия")
        self.assertNotContains(page, "Не удалось проверить обновления")

    def test_failed_check_is_shown_calmly(self):
        """Сеть недоступна: страница 200 и понятная строка вместо ошибки."""
        with mock.patch.object(
            updates, "urlopen", side_effect=OSError("network is unreachable")
        ):
            page = self.about_page(self.superadmin)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Не удалось проверить обновления")
        self.assertContains(page, updates.RELEASES_PAGE_URL)
        self.assertNotContains(page, "Доступна версия")
