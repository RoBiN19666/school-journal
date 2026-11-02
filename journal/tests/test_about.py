"""Название организации на странице «О программе»: показ и правка карандашом.

Проверяем то, что просил заказчик: название берётся из настроек журнала, рядом с
ним у главного администратора есть карандаш, форма сохраняется через POST на
отдельный адрес, пустое значение не принимается, а обычный администратор,
методист и аноним изменить название не могут — даже отправив POST напрямую.
"""
from django.test import TestCase
from django.urls import reverse

from journal.context_processors import SOURCE_REPOSITORY_URL
from journal.models import SiteSettings, User

PASSWORD = "TestPass-2024-for-tests"
NEW_NAME = "Учебный центр «Пример»"


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


class OrganizationNameTestCase(TestCase):
    """Общая обвязка: главный администратор уже есть, журнал настроен."""

    def setUp(self):
        self.superadmin = make_user("about-owner", User.Role.SUPERADMIN, superuser=True)
        self.admin = make_user("about-admin", User.Role.ADMIN)
        self.methodist = make_user("about-methodist", User.Role.METHODIST)

    def saved_name(self):
        site = SiteSettings.objects.filter(pk=1).first()
        return site.organization_name if site else SiteSettings._meta.get_field(
            "organization_name"
        ).default

    def test_superadmin_sees_pencil_and_saves_new_name(self):
        """Главный администратор: карандаш виден, POST сохраняет и уводит на /about/."""
        self.client.force_login(self.superadmin)

        page = self.client.get(reverse("about"))
        self.assertContains(page, "data-organization-edit")
        self.assertContains(page, "Образовательная организация")
        # Ссылка на исходный код: адрес берётся из одной константы контекста.
        self.assertContains(page, f'href="{SOURCE_REPOSITORY_URL}"')
        self.assertContains(page, 'target="_blank"')
        self.assertContains(page, 'rel="noopener"')

        response = self.client.post(
            reverse("about_organization"), {"organization_name": NEW_NAME}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("about"))
        self.assertEqual(
            SiteSettings.objects.get(pk=1).organization_name, NEW_NAME
        )

        page = self.client.get(reverse("about"))
        self.assertContains(page, NEW_NAME)

    def test_empty_name_is_rejected_and_old_value_kept(self):
        """Пустое название: ошибка на странице, сохранённое значение не меняется."""
        SiteSettings.objects.update_or_create(
            pk=1, defaults={"organization_name": "Первое название"}
        )
        self.client.force_login(self.superadmin)

        response = self.client.post(
            reverse("about_organization"), {"organization_name": "   "}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Введите название организации")
        self.assertEqual(
            SiteSettings.objects.get(pk=1).organization_name, "Первое название"
        )

    def test_other_roles_cannot_edit_name(self):
        """Обычный администратор и методист: нет карандаша и POST отклоняется."""
        SiteSettings.objects.update_or_create(
            pk=1, defaults={"organization_name": "Первое название"}
        )
        for user in (self.admin, self.methodist):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                page = self.client.get(reverse("about"))
                self.assertNotContains(page, "data-organization-edit")
                response = self.client.post(
                    reverse("about_organization"), {"organization_name": NEW_NAME}
                )
                self.assertIn(response.status_code, (302, 403))
                self.assertEqual(
                    SiteSettings.objects.get(pk=1).organization_name,
                    "Первое название",
                )

    def test_anonymous_is_sent_to_login(self):
        """Аноним: POST на адрес сохранения ведёт на страницу входа."""
        response = self.client.post(
            reverse("about_organization"), {"organization_name": NEW_NAME}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        self.assertFalse(SiteSettings.objects.exists())
