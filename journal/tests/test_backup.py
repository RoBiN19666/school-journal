"""Автотесты резервного копирования и восстановления журнала.

Проверяют ``journal/backup.py`` (сборку копии, восстановление в режимах
``merge``/``replace``, шифрование ``.json.enc``), страницы раздела ``/backup/``
(права, скачивание, загрузка, отказы при битом JSON и неверной парольной фразе)
и команду ``manage.py backup``.

Тесты работают только на ``django.test.TestCase`` и штатном тестовом клиенте.
Данные детерминированные: полугодие 2024/2025, занятие 13 января 2025
(понедельник), фиксированные ФИО и даты. Сеть не используется: уведомления
отправляются через локальный бэкенд, а не по SMTP.

Важная особенность журнала (как в ``test_absences.py``): пока в базе нет
активного администратора, ``journal.middleware.FirstRunSetupMiddleware`` уводит
любую страницу на ``/setup/``. Поэтому в каждом наборе данных создаётся
действующий администратор.
"""
import io
import json
import shutil
import uuid
from datetime import date, time
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from journal import backup as backup_tools
from journal import crypto
from journal.models import (
    AcademicYear,
    Attendance,
    Branch,
    ContestResult,
    Group,
    LessonTopic,
    MailSettings,
    Room,
    Schedule,
    SiteSettings,
    Student,
    Subject,
    TeacherDevelopment,
    Term,
    User,
)

#: Пароль тестовых учётных записей (вход идёт через ``force_login``).
PASSWORD = "TestPass-2024-for-tests"

#: Пароль SMTP: он не должен попадать в копию ни в каком виде.
MAIL_PASSWORD = "smtp-secret-2025-do-not-copy"

#: Границы тестового учебного года и полугодия.
TERM_START = date(2024, 9, 1)
TERM_END = date(2025, 6, 30)

#: Понедельник, на который ставится занятие и отметки.
LESSON_DATE = date(2025, 1, 13)

#: Разделы, которые обязана содержать копия (см. ``backup.DATA_SECTIONS``).
EXPECTED_SECTIONS = (
    "academic_years",
    "terms",
    "branches",
    "rooms",
    "users",
    "students",
    "subjects",
    "groups",
    "schedules",
    "attendance",
    "topics",
    "contests",
    "development",
)

#: Данные журнала, которые «полная замена» удаляет и загружает заново.
JOURNAL_MODELS = (
    Attendance,
    LessonTopic,
    ContestResult,
    TeacherDevelopment,
    Schedule,
    Group,
    Subject,
    Student,
)


def make_user(username, role, is_superuser=False):
    """Создать пользователя нужной роли (первый администратор настраивает журнал)."""
    return User.objects.create_user(
        username=username,
        password=PASSWORD,
        first_name="Тест",
        last_name=username.title(),
        role=role,
        is_active=True,
        is_superuser=is_superuser,
    )


class BackupDataTestCase(TestCase):
    """Общий набор данных: администратор, главный администратор, преподаватель,
    полугодие, адрес, кабинет, два ученика, программа, группа, занятие, отметки,
    тема, конкурс и курс педагога.
    """

    def setUp(self):
        # Почта в тестах настроена, но письма не должны уходить в сеть:
        # для уведомлений подменяем SMTP-бэкенд на локальный.
        mail_backend = override_settings(
            NOTIFY_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
        )
        mail_backend.enable()
        self.addCleanup(mail_backend.disable)

        # Обычный администратор нужен, чтобы журнал считался настроенным.
        self.admin = make_user("backup-admin", User.Role.ADMIN)
        self.superadmin = make_user(
            "backup-root", User.Role.SUPERADMIN, is_superuser=True
        )
        self.teacher = make_user("backup-teacher", User.Role.TEACHER)

        self.year = AcademicYear.objects.create(
            start_date=TERM_START, end_date=TERM_END
        )
        self.term = Term.objects.create(
            year=self.year, number=1, start_date=TERM_START, end_date=TERM_END
        )
        self.branch = Branch.objects.create(name="Основной адрес")
        self.room = Room.objects.create(branch=self.branch, name="Кабинет 1")
        self.student = Student.objects.create(
            last_name="Учеников",
            first_name="Ученик",
            middle_name="Ученикович",
            birth_date=date(2010, 5, 5),
        )
        self.second_student = Student.objects.create(
            last_name="Петрова",
            first_name="Анна",
            middle_name="Сергеевна",
            birth_date=date(2011, 2, 2),
        )
        self.subject = Subject.objects.create(
            name="Математика",
            branch=self.branch,
            term=self.term,
            teacher=self.teacher,
        )
        self.subject.teachers.add(self.teacher)
        self.group = Group.objects.create(
            name="Группа 1", subject=self.subject, term=self.term
        )
        self.group.students.add(self.student, self.second_student)
        self.schedule = Schedule.objects.create(
            group=self.group,
            branch=self.branch,
            room=self.room,
            day_of_week=Schedule.Weekday.MONDAY,
            start_time=time(10, 0),
            end_time=time(11, 30),
            teacher=self.teacher,
        )
        self.first_attendance = Attendance.objects.create(
            student=self.student,
            schedule=self.schedule,
            date=LESSON_DATE,
            status=Attendance.Status.PRESENT,
        )
        self.second_attendance = Attendance.objects.create(
            student=self.second_student,
            schedule=self.schedule,
            date=LESSON_DATE,
            status=Attendance.Status.ABSENT,
        )
        self.topic = LessonTopic.objects.create(
            schedule=self.schedule,
            date=LESSON_DATE,
            topic="Обыкновенные дроби",
            teacher=self.teacher,
        )
        self.contest = ContestResult.objects.create(
            student=self.student,
            date=date(2025, 2, 1),
            title="Школьная олимпиада",
            status=ContestResult.Status.WINNER,
            subject="Математика",
            comment="1 место",
        )
        self.development = TeacherDevelopment.objects.create(
            teacher=self.teacher,
            kind=TeacherDevelopment.Kind.COURSE,
            date=date(2025, 3, 10),
            program="Методика преподавания математики",
            organization="Институт развития образования",
            hours=36,
            result="Сертификат",
        )
        # Настройки сайта и почты: входят в копию, кроме пароля SMTP.
        self.site = SiteSettings(
            pk=1,
            organization_name="Тестовая организация",
            product_name="Тестовый журнал",
            short_logo="ТЖ",
            login_hint="Подсказка для входа",
            remember_days=14,
            session_hours=8,
        )
        self.site.save()
        self.mail = MailSettings.load()
        self.mail.host = "smtp.example.test"
        self.mail.port = 587
        self.mail.username = "journal@example.test"
        self.mail.from_email = "journal@example.test"
        self.mail.recipients = "admin@example.test"
        self.mail.notify_logins = False
        self.mail.set_password(MAIL_PASSWORD)
        self.mail.save()

    # --- Помощники ---------------------------------------------------------
    def build_payload(self):
        """Копия журнала в том виде, в каком её скачивает кнопка «Скачать копию»."""
        return backup_tools.dumps(backup_tools.build_backup()).encode("utf-8")

    def wipe_journal(self):
        """Удалить данные журнала так, как это делает режим «полная замена»."""
        for model in JOURNAL_MODELS:
            model.objects.all().delete()

    def post_restore(self, content, filename, mode="merge", passphrase=""):
        """Загрузить файл копии на страницу восстановления и вернуть ответ."""
        data = {"file": SimpleUploadedFile(filename, content), "mode": mode}
        if passphrase:
            data["passphrase"] = passphrase
        if mode == "replace":
            data["confirm"] = "on"
        return self.client.post(reverse("backup_restore"), data, follow=True)


class BackupCreationTests(BackupDataTestCase):
    """Копия собирается и содержит те же числа, что и база."""

    def test_backup_contains_expected_sections_and_counts(self):
        """build_backup(): формат, версия, разделы, счётчики и настройки."""
        data = backup_tools.build_backup()

        self.assertEqual(data["format"], backup_tools.FORMAT)
        self.assertEqual(data["version"], backup_tools.VERSION)
        self.assertIsInstance(data["created_at"], str)
        for name in EXPECTED_SECTIONS:
            with self.subTest(section=name):
                self.assertIsInstance(data.get(name), list)
                self.assertEqual(
                    data["counts"][name],
                    len(data[name]),
                    f"счётчик раздела {name} не совпадает с его длиной",
                )

        # Числа в копии — ровно то, что лежит в базе.
        self.assertEqual(data["counts"], backup_tools.db_totals())
        self.assertEqual(data["counts"]["students"], 2)
        self.assertEqual(data["counts"]["schedules"], 1)
        self.assertEqual(data["counts"]["attendance"], 2)
        self.assertEqual(data["counts"]["topics"], 1)
        self.assertEqual(data["counts"]["contests"], 1)
        self.assertEqual(data["counts"]["development"], 1)

        # Содержимое разделов: ФИО, отметка и тема на месте.
        self.assertIn("Учеников", [item["last_name"] for item in data["students"]])
        self.assertIn(
            {
                "schedule": 0,
                "student": 0,
                "date": LESSON_DATE.isoformat(),
                "status": Attendance.Status.PRESENT,
            },
            data["attendance"],
        )
        self.assertEqual(data["topics"][0]["topic"], "Обыкновенные дроби")

        # Пароль пользователя в копии — хеш, а не открытый текст.
        passwords = [item["password"] for item in data["users"]]
        self.assertTrue(all(password != PASSWORD for password in passwords))

        # Настройки сайта и почты в копии есть, а пароля SMTP нет.
        self.assertEqual(
            data["settings"]["site"]["organization_name"], "Тестовая организация"
        )
        self.assertEqual(data["settings"]["site"]["remember_days"], 14)
        self.assertEqual(data["settings"]["mail"]["host"], "smtp.example.test")
        self.assertNotIn("password_encrypted", data["settings"]["mail"])
        self.assertNotIn(MAIL_PASSWORD, backup_tools.dumps(data))


class BackupDownloadViewTests(BackupDataTestCase):
    """Страница скачивания отдаёт файл с теми же данными."""

    def test_download_returns_json_with_same_counts(self):
        """GET /backup/download/ под главным администратором отдаёт JSON-копию."""
        self.client.force_login(self.superadmin)

        index = self.client.get(reverse("backup"))
        self.assertEqual(index.status_code, 200)

        response = self.client.get(reverse("backup_download"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response["Content-Type"])
        self.assertIn(".json", response["Content-Disposition"])

        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["format"], backup_tools.FORMAT)
        self.assertEqual(data["counts"], backup_tools.db_totals())


class BackupRestoreTests(BackupDataTestCase):
    """Восстановление из копии: полный цикл и права доступа."""

    def test_full_cycle_restore_as_superadmin(self):
        """Данные → копия → очистка базы → восстановление → числа совпадают."""
        before = backup_tools.db_totals()
        payload = self.build_payload()
        self.wipe_journal()
        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(Attendance.objects.count(), 0)

        self.client.force_login(self.superadmin)
        response = self.post_restore(payload, "backup.json", mode="replace")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.redirect_chain[-1][0], reverse("backup")
        )
        self.assertContains(response, "Восстановление завершено")
        self.assertEqual(backup_tools.db_totals(), before)
        self.assertEqual(Student.objects.count(), 2)
        self.assertEqual(Attendance.objects.count(), 2)
        # Восстановилась именно отметка «отсутствовал», а не значение по умолчанию.
        self.assertEqual(
            Attendance.objects.get(student__last_name="Петрова").status,
            Attendance.Status.ABSENT,
        )


class BackupPermissionTests(BackupDataTestCase):
    """Раздел /backup/ — только главный администратор: страница, файл, восстановление."""

    def assert_section_denied(self, user):
        """Роль получает отказ на всех трёх страницах, данные не меняются."""
        payload = self.build_payload()
        Attendance.objects.all().delete()
        self.assertEqual(Attendance.objects.count(), 0)

        self.client.force_login(user)
        for name in ("backup", "backup_download"):
            with self.subTest(view=name, role=user.role):
                response = self.client.get(reverse(name))
                self.assertIn(response.status_code, (302, 403, 404))
                self.assertNotEqual(response.status_code, 200)

        # Восстановление тоже отклонено: форма не обрабатывается вовсе.
        response = self.post_restore(payload, "backup.json", mode="merge")
        self.assertEqual(response.status_code, 200)  # редирект на домашнюю, не 500
        self.assertEqual(response.redirect_chain[-1][0], reverse("dashboard"))
        self.assertEqual(Attendance.objects.count(), 0)

    def test_regular_admin_is_denied(self):
        """Обычный администратор не открывает раздел и не восстанавливает копию."""
        self.assert_section_denied(self.admin)

    def test_methodist_is_denied(self):
        """Методист не открывает раздел и не восстанавливает копию."""
        methodist = make_user("backup-methodist", User.Role.METHODIST)
        self.assert_section_denied(methodist)

    def test_superadmin_has_access(self):
        """Главный администратор по-прежнему видит раздел и скачивает копию."""
        self.client.force_login(self.superadmin)
        self.assertEqual(self.client.get(reverse("backup")).status_code, 200)
        response = self.client.get(reverse("backup_download"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response["Content-Type"])


class BackupSettingsTests(BackupDataTestCase):
    """Настройки сайта и почты: входят в копию, пароль SMTP — нет."""

    def test_settings_are_restored_and_mail_password_is_kept(self):
        """Копия → правка настроек → восстановление: значения вернулись, пароль цел."""
        data = backup_tools.build_backup()
        password_before = self.mail.password_encrypted
        # Пароля SMTP в копии нет ни в открытом виде, ни шифротекстом.
        serialized = backup_tools.dumps(data)
        self.assertNotIn(MAIL_PASSWORD, serialized)
        self.assertNotIn("password_encrypted", serialized)

        # Меняем и настройки сайта, и настройки почты после снятия копии.
        site = SiteSettings.load()
        site.organization_name = "Изменённая организация"
        site.product_name = "Изменённый журнал"
        site.login_hint = "Изменённая подсказка"
        site.remember_days = 1
        site.session_hours = 1
        site.save()
        mail = MailSettings.load()
        mail.host = "changed.example.test"
        mail.port = 25
        mail.recipients = ""
        mail.notify_logins = True
        mail.save()

        backup_tools.restore(data, mode="merge", actor=self.superadmin)

        site = SiteSettings.load()
        self.assertEqual(site.organization_name, "Тестовая организация")
        self.assertEqual(site.product_name, "Тестовый журнал")
        self.assertEqual(site.short_logo, "ТЖ")
        self.assertEqual(site.login_hint, "Подсказка для входа")
        self.assertEqual(site.remember_days, 14)
        self.assertEqual(site.session_hours, 8)

        mail = MailSettings.load()
        self.assertEqual(mail.host, "smtp.example.test")
        self.assertEqual(mail.port, 587)
        self.assertEqual(mail.recipients, "admin@example.test")
        self.assertFalse(mail.notify_logins)
        # Пароль не затёрт и не обнулён: копия его не содержала.
        self.assertEqual(mail.password_encrypted, password_before)
        self.assertEqual(mail.password, MAIL_PASSWORD)

    def test_old_backup_without_settings_restores_without_errors(self):
        """Копия прежнего формата (без settings) восстанавливается как раньше."""
        data = backup_tools.build_backup()
        data.pop("settings")
        self.assertNotIn("settings", data)

        # Настройки в базе уже другие: старая копия не должна их трогать.
        site = SiteSettings.load()
        site.organization_name = "Настройки остались прежними"
        site.save()
        mail = MailSettings.load()
        mail.host = "keep.example.test"
        mail.save()

        Attendance.objects.all().delete()
        self.client.force_login(self.superadmin)
        response = self.post_restore(
            backup_tools.dumps(data).encode("utf-8"), "old-backup.json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], reverse("backup"))
        self.assertContains(response, "Восстановление завершено")
        self.assertEqual(Attendance.objects.count(), 2)
        self.assertEqual(SiteSettings.load().organization_name, "Настройки остались прежними")
        self.assertEqual(MailSettings.load().host, "keep.example.test")


class BackupCommandTests(BackupDataTestCase):
    """Команда ``manage.py backup``: файл, шифрование и тихий режим."""

    def temp_dir(self):
        """Каталог для файла копии: в проекте, чтобы его можно было удалить."""
        folder = Path(".vtmp") / f"backup-test-{uuid.uuid4().hex}"
        folder.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        return folder

    def test_command_writes_valid_backup(self):
        """Команда пишет валидную копию с настройками и без пароля SMTP."""
        folder = self.temp_dir()
        target = folder / "journal-backup.json"
        output = io.StringIO()
        call_command(
            "backup", "--output", str(target), "--quiet", stdout=output
        )

        self.assertEqual(output.getvalue(), "")
        self.assertTrue(target.exists())
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["format"], backup_tools.FORMAT)
        self.assertEqual(data["counts"], backup_tools.db_totals())
        self.assertEqual(
            data["settings"]["site"]["organization_name"], "Тестовая организация"
        )
        self.assertEqual(data["settings"]["mail"]["host"], "smtp.example.test")
        self.assertNotIn("password_encrypted", data["settings"]["mail"])
        self.assertNotIn(MAIL_PASSWORD, target.read_text(encoding="utf-8"))

    def test_command_encrypts_with_password(self):
        """С ``--password`` файл зашифрован и читается только с этой фразой."""
        passphrase = "Командная фраза 2025"
        folder = self.temp_dir()
        target = folder / "journal-backup.json.enc"
        call_command(
            "backup",
            "--output",
            str(target),
            "--password",
            passphrase,
            "--quiet",
        )

        blob = target.read_bytes()
        self.assertTrue(crypto.is_encrypted_blob(blob))
        self.assertNotIn(b"school_journal.backup", blob)
        data = json.loads(
            crypto.decrypt_bytes(blob, passphrase).decode("utf-8")
        )
        self.assertEqual(data["format"], backup_tools.FORMAT)
        self.assertIn("settings", data)
        with self.assertRaises(ValueError):
            crypto.decrypt_bytes(blob, "другая фраза")


class EncryptedBackupTests(BackupDataTestCase):
    """Зашифрованная копия ``.json.enc``: верная и неверная парольная фраза."""

    def test_encrypted_backup_roundtrip(self):
        """Скачивание с паролем → восстановление с тем же паролем → данные те же."""
        before = backup_tools.db_totals()
        self.client.force_login(self.superadmin)

        downloaded = self.client.post(
            reverse("backup_download"), {"passphrase": "Секретная фраза 2025"}
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn(".json.enc", downloaded["Content-Disposition"])
        blob = downloaded.content
        self.assertTrue(crypto.is_encrypted_blob(blob))
        self.assertNotIn(b"school_journal.backup", blob)

        self.wipe_journal()
        response = self.post_restore(
            blob,
            "school_journal_backup.json.enc",
            mode="replace",
            passphrase="Секретная фраза 2025",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], reverse("backup"))
        self.assertContains(response, "Восстановление завершено")
        self.assertEqual(backup_tools.db_totals(), before)

    def test_encrypted_backup_wrong_passphrase_is_rejected(self):
        """Неверная фраза: понятная ошибка, без 500, данные не тронуты."""
        payload = self.build_payload()
        blob = crypto.encrypt_bytes(payload, "правильная-фраза")
        before = backup_tools.db_totals()

        self.client.force_login(self.superadmin)
        response = self.post_restore(
            blob,
            "school_journal_backup.json.enc",
            mode="replace",
            passphrase="неправильная-фраза",
        )

        self.assertEqual(response.status_code, 200)  # редирект на /backup/, не 500
        self.assertEqual(response.redirect_chain[-1][0], reverse("backup"))
        self.assertContains(response, "Зашифрованная копия не открыта")
        self.assertContains(response, "Неверная парольная фраза")
        self.assertEqual(backup_tools.db_totals(), before)
        self.assertEqual(Student.objects.count(), 2)
        self.assertEqual(Attendance.objects.count(), 2)


class CorruptedBackupTests(BackupDataTestCase):
    """Битый файл копии не разрушает базу и не даёт 500."""

    def test_corrupted_json_restore_fails_without_500(self):
        """Мусор вместо JSON: ошибка на странице, база не изменилась.

        Проверяются оба режима: «добавить и обновить» и «полная замена».
        Во втором случае особенно важно, что проверка копии идёт ДО удаления
        данных (``journal/backup.py:416`` — ``validate`` до ``transaction.atomic``):
        иначе битый файл обнулил бы журнал.
        """
        before = backup_tools.db_totals()
        self.client.force_login(self.superadmin)

        response = self.post_restore(
            b"<html>this is not a backup at all", "backup.json"
        )
        self.assertEqual(response.status_code, 200)  # редирект на /backup/, не 500
        self.assertEqual(response.redirect_chain[-1][0], reverse("backup"))
        self.assertContains(response, "Не удалось прочитать копию")
        self.assertEqual(backup_tools.db_totals(), before)

        # JSON корректный, но это не копия журнала: нет обязательных разделов.
        response = self.post_restore(
            b'{"format": "school_journal.backup", "version": 10}',
            "broken.json",
            mode="replace",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], reverse("backup"))
        self.assertContains(response, "Не удалось прочитать копию")
        self.assertContains(response, "нет обязательных разделов")
        self.assertEqual(backup_tools.db_totals(), before)
        self.assertEqual(Student.objects.count(), 2)
        self.assertEqual(Attendance.objects.count(), 2)
