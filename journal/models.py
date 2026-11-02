"""Модели электронного журнала: роли, ученики, программы, группы, расписание, посещаемость."""
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth.validators import ASCIIUsernameValidator
from django.core.validators import FileExtensionValidator
from django.db import OperationalError, ProgrammingError, models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def validate_image_size(value):
    """Не больше 1 МБ: логотип и фавикон — маленькие картинки."""
    limit = 1024 * 1024
    if value.size > limit:
        raise ValidationError(_("Файл слишком большой: максимум 1 МБ."))


#: Разрешённые форматы изображений. SVG запрещён: в нём может быть код.
IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "ico", "webp"]


class SiteSettings(models.Model):
    """Настройки журнала: название организации, логотип, фавикон и мелочи.

    Запись всегда одна (pk=1) — это «настройки журнала», а не список.
    """

    organization_name = models.CharField(
        _("Название организации"), max_length=200, default="Образовательная организация"
    )
    product_name = models.CharField(
        _("Название журнала"), max_length=100, default="Электронный журнал"
    )
    short_logo = models.CharField(
        _("Короткий знак"), max_length=4, default="ЭЖ",
        help_text=_("Показывается, если логотип не загружен."),
    )
    logo = models.ImageField(
        _("Логотип"), upload_to="branding/", blank=True,
        validators=[FileExtensionValidator(IMAGE_EXTENSIONS), validate_image_size],
        help_text=_("PNG, JPG, WEBP или ICO, до 1 МБ. Заменяет короткий знак."),
    )
    favicon = models.ImageField(
        _("Фавикон"), upload_to="branding/", blank=True,
        validators=[FileExtensionValidator(IMAGE_EXTENSIONS), validate_image_size],
        help_text=_("Значок вкладки браузера: PNG или ICO, до 1 МБ."),
    )
    login_hint = models.CharField(
        _("Подсказка на странице входа"), max_length=200,
        default="Ставьте галочку только на своём компьютере.",
    )
    remember_days = models.PositiveIntegerField(_("Сохранять вход, дней"), default=30)
    session_hours = models.PositiveIntegerField(_("Сессия без активности, часов"), default=12)
    updated_at = models.DateTimeField(_("Обновлено"), auto_now=True)

    class Meta:
        verbose_name = _("Настройки журнала")
        verbose_name_plural = _("Настройки журнала")

    def __str__(self):
        return self.organization_name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Текущие настройки; если записи нет — значения по умолчанию."""
        instance = cls.objects.filter(pk=1).first()
        return instance or cls()


class User(AbstractUser):
    """Пользователь системы. Роль определяет доступные разделы."""

    class Role(models.TextChoices):
        SUPERADMIN = "superadmin", _("Главный администратор")
        ADMIN = "admin", _("Администратор")
        METHODIST = "methodist", _("Методист")
        TEACHER = "teacher", _("Преподаватель")

    username = models.CharField(
        _("Логин"),
        max_length=150,
        unique=True,
        blank=True,
        # Логин задаётся латиницей: кириллица и пробелы не принимаются,
        # иначе вход в журнал путается из-за раскладки и похожих букв.
        validators=[
            ASCIIUsernameValidator(
                message=_("Логин может содержать только латинские буквы, цифры "
                        "и символы @ . + - _ (без пробелов и кириллицы)"),
            )
        ],
    )

    role = models.CharField(
        _("Роль"), max_length=20, choices=Role.choices, default=Role.TEACHER
    )

    class Meta:
        verbose_name = _("Пользователь")
        verbose_name_plural = _("Пользователи")

    def save(self, *args, **kwargs):
        # Владелец журнала (superuser) — главный администратор; роль admin он может
        # выбрать сам, если хочет работать как обычный администратор.
        if self.is_superuser and self.role not in (self.Role.SUPERADMIN, self.Role.ADMIN):
            self.role = self.Role.SUPERADMIN
        super().save(*args, **kwargs)

    def clean(self):
        """Не даём оставить журнал без активного администратора.

        Проверка нужна формам Django (в том числе админке): ``full_clean``
        поднимает ``ValidationError`` до записи, и администратор видит понятную
        ошибку в форме, а не молчаливое выключение. Само сохранение и удаление
        сторожат приёмники ``pre_save`` и ``pre_delete`` (``journal/guards.py``):
        они работают и там, где формы нет, — в коде и командах.
        """
        super().clean()
        from .guards import last_admin_error  # локальный импорт: избегаем цикла

        try:
            message = last_admin_error(self)
        except (OperationalError, ProgrammingError):
            # Таблиц ещё нет (миграции, самый первый запуск) — проверять нечего.
            return
        if message:
            raise ValidationError(message)

    @property
    def is_admin(self):
        """Администратор: главный администратор или администратор."""
        return self.is_superuser or self.role in (self.Role.SUPERADMIN, self.Role.ADMIN)

    @property
    def is_superadmin(self):
        """Главный администратор: его данные правит только он сам."""
        return self.is_superuser or self.role == self.Role.SUPERADMIN

    @property
    def is_methodist(self):
        return self.role == self.Role.METHODIST

    @property
    def is_teacher(self):
        return self.role == self.Role.TEACHER

    @property
    def is_staff_member(self):
        """Админ или методист — доступ к /dashboard/ и разделам учёта."""
        return self.is_admin or self.is_methodist

    #: Имя и фамилия обязательны: без них учётную запись не узнать в списках,
    #: документах и выгрузках (вместо ФИО показывался бы только логин).
    first_name = models.CharField(
        _("Имя"),
        max_length=150,
        blank=False,
        error_messages={"blank": _("Укажите имя.")},
    )
    last_name = models.CharField(
        _("Фамилия"),
        max_length=150,
        blank=False,
        error_messages={"blank": _("Укажите фамилию.")},
    )

    #: Отчество — необязательное, но полезно в документах и выгрузках.
    middle_name = models.CharField(_("Отчество"), max_length=100, blank=True)

    @property
    def full_name(self):
        """«Иванов Пётр Сергеевич» — привычный для русских документов порядок."""
        name = " ".join(
            part for part in (self.last_name, self.first_name, self.middle_name) if part
        ).strip()
        return name or self.username

    def __str__(self):
        return self.full_name


class AcademicYear(models.Model):
    """Учебный год, например 2025–2026."""

    start_date = models.DateField(_("Начало учебного года"))
    end_date = models.DateField(_("Окончание учебного года"))

    class Meta:
        ordering = ("-start_date",)
        constraints = [
            models.UniqueConstraint(
                fields=["start_date", "end_date"], name="unique_academic_year"
            )
        ]
        verbose_name = _("Учебный год")
        verbose_name_plural = _("Учебные годы")

    @property
    def title(self):
        return f"{self.start_date.year}–{self.end_date.year}"

    @property
    def date_range(self):
        return f"{self.start_date:%d.%m.%Y}–{self.end_date:%d.%m.%Y}"

    def contains(self, day):
        return self.start_date <= day <= self.end_date

    def __str__(self):
        return self.title


class Term(models.Model):
    """Полугодие внутри учебного года. Расписание и группы живут в полугодии."""

    year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="terms",
        verbose_name=_("Учебный год"),
    )
    number = models.PositiveSmallIntegerField(_("Номер полугодия"))
    start_date = models.DateField(_("Начало полугодия"))
    end_date = models.DateField(_("Окончание полугодия"))

    class Meta:
        ordering = ("year__start_date", "number")
        constraints = [
            models.UniqueConstraint(
                fields=["year", "number"], name="unique_term_number_per_year"
            )
        ]
        verbose_name = _("Полугодие")
        verbose_name_plural = _("Полугодия")

    @property
    def title(self):
        return _("%(number)s полугодие") % {"number": self.number}

    @property
    def full_title(self):
        return f"{self.title} · {self.year.title}"

    @property
    def date_range(self):
        return f"{self.start_date:%d.%m.%Y}–{self.end_date:%d.%m.%Y}"

    def contains(self, day):
        return self.start_date <= day <= self.end_date

    def __str__(self):
        return f"{self.full_title} ({self.date_range})"


class Student(models.Model):
    """Карточка ученика. Учётная запись ученику не требуется."""

    class Dismissal(models.TextChoices):
        """Можно ли отпускать ребёнка во время беспилотной опасности."""

        NONE = "Н", _("Нет заявления")
        NOT_ALLOWED = "ЕН", _("Есть заявление, не отпускать")
        ALLOWED = "ЕО", _("Есть заявление, можно отпускать")

    last_name = models.CharField(_("Фамилия"), max_length=100)
    first_name = models.CharField(_("Имя"), max_length=100)
    middle_name = models.CharField(_("Отчество"), max_length=100, blank=True)
    birth_date = models.DateField(_("Дата рождения"), null=True, blank=True)
    dismissal_status = models.CharField(
        _("Отпускать при беспилотной опасности"),
        max_length=2,
        choices=Dismissal.choices,
        default=Dismissal.NONE,
    )

    class Meta:
        ordering = ("last_name", "first_name")
        indexes = [
            # Список учеников всегда идёт по фамилии и имени: без индекса
            # база сортирует всю таблицу.
            models.Index(
                fields=["last_name", "first_name"], name="student_name_idx"
            ),
        ]
        verbose_name = _("Ученик")
        verbose_name_plural = _("Ученики")

    @property
    def full_name(self):
        return " ".join(
            part for part in (self.last_name, self.first_name, self.middle_name) if part
        )

    @property
    def short_name(self):
        """Фамилия и инициалы — для узких колонок."""
        initials = "".join(
            f"{part[0]}." for part in (self.first_name, self.middle_name) if part
        )
        return f"{self.last_name} {initials}".strip()

    @property
    def dismissal_short(self):
        return self.dismissal_status or self.Dismissal.NONE.value

    @property
    def dismissal_title(self):
        return self.get_dismissal_status_display()

    @property
    def dismissal_allowed(self):
        return self.dismissal_status == self.Dismissal.ALLOWED

    def __str__(self):
        return self.full_name


class ContestResult(models.Model):
    """Участие ученика в конкурсе или мероприятии."""

    class Status(models.TextChoices):
        WINNER = "winner", _("Победитель")
        PRIZE = "prize", _("Призёр")
        PARTICIPANT = "participant", _("Участник")

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="contests",
        verbose_name=_("Ученик"),
    )
    date = models.DateField(_("Дата"))
    title = models.CharField(_("Название мероприятия"), max_length=250)
    status = models.CharField(
        _("Результат"), max_length=20, choices=Status.choices, default=Status.PARTICIPANT
    )
    subject = models.CharField(_("Направление или программа"), max_length=150, blank=True)
    comment = models.CharField(_("Комментарий"), max_length=250, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_contests",
        verbose_name=_("Кто внёс"),
    )
    created_at = models.DateTimeField(_("Добавлено"), auto_now_add=True)

    class Meta:
        ordering = ("-date", "student__last_name")
        indexes = [
            # Выгрузки и списки конкурсов строятся по дате (сортировка по убыванию).
            models.Index(fields=["date"], name="contest_date_idx"),
        ]
        verbose_name = _("Участие в конкурсе")
        verbose_name_plural = _("Участие детей в конкурсах")

    @property
    def status_label(self):
        return self.get_status_display()

    def __str__(self):
        return f"{self.date:%d.%m.%Y} {self.title} — {self.student.full_name}"


class TeacherDevelopment(models.Model):
    """Повышение квалификации и конкурсы педагогов."""

    class Kind(models.TextChoices):
        COURSE = "course", _("Повышение квалификации")
        CONTEST = "contest", _("Конкурс")
        OTHER = "other", _("Другое")

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="development",
        verbose_name=_("Педагог"),
        limit_choices_to={"role": User.Role.TEACHER},
    )
    kind = models.CharField(
        _("Вид"), max_length=20, choices=Kind.choices, default=Kind.COURSE
    )
    date = models.DateField(_("Дата"))
    program = models.CharField(_("Название программы или мероприятия"), max_length=250)
    organization = models.CharField(_("Образовательная организация"), max_length=250, blank=True)
    hours = models.PositiveSmallIntegerField(_("Часов"), null=True, blank=True)
    result = models.CharField(_("Результат"), max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_development",
        verbose_name=_("Кто внёс"),
    )
    created_at = models.DateTimeField(_("Добавлено"), auto_now_add=True)

    class Meta:
        ordering = ("-date", "teacher__last_name")
        indexes = [
            # Список повышения квалификации сортируется по дате.
            models.Index(fields=["date"], name="development_date_idx"),
        ]
        verbose_name = _("Повышение квалификации педагога")
        verbose_name_plural = _("Повышение квалификации педагогов")

    @property
    def kind_label(self):
        return self.get_kind_display()

    def __str__(self):
        return f"{self.date:%d.%m.%Y} {self.program} — {self.teacher.full_name}"


class TeacherAbsence(models.Model):
    """Период отсутствия преподавателя: командировка, больничный или другое.

    Отсутствие вносит сотрудник УМО в карточке преподавателя. Запись только
    хранится и показывается: пометки в расписании и выгрузках — отдельные волны,
    поэтому занятия и отметки эта модель не меняет.
    """

    class Kind(models.TextChoices):
        TRIP = "trip", _("Командировка")
        SICK = "sick", _("Больничный")
        OTHER = "other", _("Другое")

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="absences",
        verbose_name=_("Преподаватель"),
        limit_choices_to={"role": User.Role.TEACHER},
    )
    date_from = models.DateField(_("С"))
    #: Вторая дата входит в период: отсутствие «с 12.10 по 20.10» — это 9 дней.
    date_to = models.DateField(_("По"), help_text=_("Обе даты входят в период."))
    kind = models.CharField(
        _("Причина"), max_length=20, choices=Kind.choices, default=Kind.SICK
    )
    comment = models.TextField(_("Примечание"), blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_absences",
        verbose_name=_("Кто внёс"),
    )
    created_at = models.DateTimeField(_("Добавлено"), auto_now_add=True)

    class Meta:
        ordering = ("date_from", "teacher__last_name")
        indexes = [
            # Отсутствия выбирают по датам начала: список в карточке и будущие
            # пометки в расписании идут от начала периода.
            models.Index(fields=["date_from"], name="absence_date_from_idx"),
        ]
        verbose_name = _("Отсутствие преподавателя")
        verbose_name_plural = _("Отсутствия преподавателей")

    @property
    def title(self):
        """«Командировка с 12.10.2026 по 20.10.2026» — как запись видно в списках."""
        return _("%(kind)s с %(start)s по %(end)s") % {
            "kind": self.get_kind_display(),
            "start": f"{self.date_from:%d.%m.%Y}",
            "end": f"{self.date_to:%d.%m.%Y}",
        }

    def __str__(self):
        return f"{self.title} — {self.teacher.full_name}"

    def clean(self):
        """Проверяет период: даты по порядку и без пересечений у преподавателя.

        Таблицы может ещё не быть (миграции, ``loaddata``, самый первый запуск):
        тогда пересечения проверить нечем, и запись пропускаем — так же ведёт
        себя ``guard_term_save`` в ``journal/periods.py``.
        """
        super().clean()
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValidationError(
                {
                    "date_to": _("Дата «по» (%(end)s) раньше даты «с» (%(start)s).")
                    % {
                        "end": f"{self.date_to:%d.%m.%Y}",
                        "start": f"{self.date_from:%d.%m.%Y}",
                    }
                }
            )
        if not self.teacher_id or not self.date_from or not self.date_to:
            # Неполные данные отбивает сама модель (обязательные поля).
            return
        if self.date_to < self.date_from:
            # Даты уже проверены выше: второй раз про то же не сообщаем.
            return
        try:
            overlapping = TeacherAbsence.objects.filter(
                teacher_id=self.teacher_id,
                date_from__lte=self.date_to,
                date_to__gte=self.date_from,
            )
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            other = overlapping.first()
        except (OperationalError, ProgrammingError):
            # Таблиц ещё нет — проверять нечего, запись делает сама база.
            return
        if other is not None:
            raise ValidationError(
                _(
                    "У преподавателя уже отмечено отсутствие «%(kind)s» "
                    "с %(start)s по %(end)s: периоды не должны пересекаться."
                )
                % {
                    "kind": other.get_kind_display(),
                    "start": f"{other.date_from:%d.%m.%Y}",
                    "end": f"{other.date_to:%d.%m.%Y}",
                }
            )


class Branch(models.Model):
    """Адрес (филиал), где проходят занятия: «ул. Примерная, 1», «ул. Вторая, 10»."""

    name = models.CharField(_("Адрес"), max_length=150, unique=True)
    order = models.PositiveSmallIntegerField(_("Порядок"), default=0)

    class Meta:
        ordering = ("order", "name")
        verbose_name = _("Адрес (филиал)")
        verbose_name_plural = _("Адреса (филиалы)")

    @property
    def title(self):
        return self.name

    def __str__(self):
        return self.name


class Room(models.Model):
    """Кабинет (класс) внутри филиала. Список кабинетов у каждого адреса свой."""

    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="rooms", verbose_name=_("Адрес")
    )
    name = models.CharField(_("Кабинет"), max_length=150)
    order = models.PositiveSmallIntegerField(_("Порядок"), default=0)

    class Meta:
        ordering = ("branch__order", "order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "name"], name="unique_room_name_per_branch"
            )
        ]
        verbose_name = _("Кабинет")
        verbose_name_plural = _("Кабинеты")

    def __str__(self):
        return f"{self.name} ({self.branch.name})"


class IpRule(models.Model):
    """Правило доступа по IP: белый или чёрный список."""

    class Kind(models.TextChoices):
        ALLOW = "allow", _("Разрешён (белый список)")
        BLOCK = "block", _("Заблокирован (чёрный список)")

    address = models.CharField(_("IP-адрес или подсеть"), max_length=64, unique=True)
    kind = models.CharField(
        _("Правило"), max_length=8, choices=Kind.choices, default=Kind.BLOCK
    )
    comment = models.CharField(_("Комментарий"), max_length=200, blank=True)
    created_at = models.DateTimeField(_("Добавлено"), auto_now_add=True)
    expires_at = models.DateTimeField(
        _("Действует до"),
        null=True,
        blank=True,
        help_text=_("Пусто — бессрочно. Автоблокировка за подозрительные запросы ставится на сутки."),
    )
    automatic = models.BooleanField(
        _("Поставлено автоматически"),
        default=False,
        help_text=_("Такие правила создаёт защита от сканеров; можно снять вручную в любой момент."),
    )

    @property
    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def is_active_block(self):
        return self.kind == self.Kind.BLOCK and not self.is_expired

    class Meta:
        ordering = ("kind", "address")
        verbose_name = _("Правило доступа по IP")
        verbose_name_plural = _("Доступ по IP")

    def __str__(self):
        return f"{self.get_kind_display()}: {self.address}"


class VisitLog(models.Model):
    """Журнал заходов, входов и событий безопасности."""

    class Kind(models.TextChoices):
        VISIT = "visit", _("Заход на страницу")
        LOGIN = "login", _("Вход")
        LOGIN_FAILED = "login_failed", _("Неудачный вход")
        LOGOUT = "logout", _("Выход")
        BLOCKED = "blocked", _("Заблокированный доступ")
        PROBE = "probe", _("Подозрительный запрос")
        EVENT = "event", _("Событие")

    created_at = models.DateTimeField(_("Когда"), auto_now_add=True, db_index=True)
    kind = models.CharField(
        _("Тип"), max_length=20, choices=Kind.choices, default=Kind.VISIT, db_index=True
    )
    ip = models.GenericIPAddressField(_("IP-адрес"), null=True, blank=True)
    user = models.ForeignKey(
        "journal.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visits",
        verbose_name=_("Пользователь"),
    )
    username = models.CharField(_("Логин"), max_length=150, blank=True)
    path = models.CharField(_("Адрес страницы"), max_length=255, blank=True)
    method = models.CharField(_("Метод"), max_length=10, blank=True)
    status_code = models.PositiveSmallIntegerField(_("Код ответа"), null=True, blank=True)
    user_agent = models.CharField(_("Браузер"), max_length=255, blank=True)
    detail = models.CharField(_("Подробности"), max_length=255, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            # Журнал чаще всего смотрят по типу события за период.
            models.Index(
                fields=["kind", "created_at"], name="visit_log_kind_created_idx"
            ),
        ]
        verbose_name = _("Запись журнала")
        verbose_name_plural = _("Журнал доступа")

    def __str__(self):
        return f"{self.created_at:%d.%m.%Y %H:%M} {self.ip} {self.get_kind_display()}"


class MailSettings(models.Model):
    """Настройки SMTP и уведомлений на почту (одна запись)."""

    host = models.CharField(_("SMTP-сервер"), max_length=200, blank=True)
    port = models.PositiveIntegerField(_("Порт"), default=587)
    username = models.CharField(_("Логин SMTP"), max_length=200, blank=True)
    password_encrypted = models.TextField(_("Пароль SMTP (зашифрован)"), blank=True)
    use_tls = models.BooleanField("STARTTLS", default=True)
    use_ssl = models.BooleanField("SSL", default=False)
    from_email = models.CharField(_("От кого"), max_length=200, blank=True)
    recipients = models.TextField(
        _("Кому (по одному адресу в строке)"), blank=True
    )
    notify_logins = models.BooleanField(_("Входы пользователей"), default=True)
    notify_failed_logins = models.BooleanField(_("Неудачные входы"), default=True)
    notify_ip_blocked = models.BooleanField(_("Заблокированные адреса"), default=True)
    notify_backup = models.BooleanField(_("Резервные копии"), default=True)
    notify_accounts = models.BooleanField(_("Учётки и пароли"), default=True)
    updated_at = models.DateTimeField(_("Обновлено"), auto_now=True)

    class Meta:
        verbose_name = _("Настройки почты")
        verbose_name_plural = _("Настройки почты")

    def __str__(self):
        return self.host or str(_("почта не настроена"))

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def password(self):
        from .crypto import decrypt

        return decrypt(self.password_encrypted)

    def set_password(self, raw):
        from .crypto import encrypt

        self.password_encrypted = encrypt(raw)

    @property
    def recipient_list(self):
        return [
            line.strip()
            for line in (self.recipients or "").replace(",", "\n").splitlines()
            if line.strip()
        ]

    @property
    def is_configured(self):
        return bool(self.host and self.recipient_list)


class Subject(models.Model):
    """Программа в конкретном полугодии и в конкретном адресе.

    Программы не общие: у каждом полугодии и каждого адреса свои. Поэтому удаление
    программы не затрагивает другие полугодия и адреса.
    """

    name = models.CharField(_("Название"), max_length=150)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="subjects",
        verbose_name=_("Адрес"),
    )
    term = models.ForeignKey(
        Term,
        on_delete=models.CASCADE,
        related_name="subjects",
        verbose_name=_("Полугодие"),
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subjects",
        limit_choices_to={"role": User.Role.TEACHER},
        verbose_name=_("Основной преподаватель"),
    )
    # У программы может быть несколько преподавателей: они чередуются по расписанию
    # и подменяют друг друга. Конкретный преподаватель указывается у занятия.
    teachers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="subjects_extra",
        limit_choices_to={"role": User.Role.TEACHER},
        verbose_name=_("Преподаватели (кто может вести и подменять)"),
    )

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["term", "branch", "name"],
                name="unique_subject_name_per_term_branch",
            )
        ]
        verbose_name = _("Программа")
        verbose_name_plural = _("Программы")

    @property
    def teacher_options(self):
        """Кто может вести занятия программы: основной и подменяющие преподаватели."""
        teachers = list(self.teachers.all())
        if self.teacher is not None and self.teacher not in teachers:
            teachers.insert(0, self.teacher)
        return teachers

    @property
    def extra_teachers(self):
        """Только подменяющие преподаватели (без основного)."""
        return [item for item in self.teacher_options if item.pk != self.teacher_id]

    @property
    def title(self):
        parts = [self.name]
        if self.branch_id:
            parts.append(self.branch.name)
        if self.term_id:
            parts.append(self.term.title)
        return " · ".join(parts)

    def __str__(self):
        return self.title


class Group(models.Model):
    """Группа (поток, смена) внутри программы на конкретную полугодие."""

    name = models.CharField(_("Название группы"), max_length=150)
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="groups", verbose_name=_("Программа")
    )
    term = models.ForeignKey(
        Term,
        on_delete=models.CASCADE,
        related_name="groups",
        verbose_name=_("Полугодие"),
    )
    students = models.ManyToManyField(
        Student, blank=True, related_name="groups", verbose_name=_("Ученики")
    )

    class Meta:
        ordering = ("subject__name", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["term", "subject", "name"],
                name="unique_group_name_per_subject_term",
            )
        ]
        verbose_name = _("Группа")
        verbose_name_plural = _("Группы")

    def __str__(self):
        return f"{self.name} ({self.subject.name})"


class LessonTopic(models.Model):
    """Тема занятия на конкретную дату и академические часы.

    Часы считаются автоматически от времени занятия: 1 академический час = 45 минут,
    поэтому занятие 10:00–11:30 даёт 2 часа.
    """

    schedule = models.ForeignKey(
        "Schedule",
        on_delete=models.CASCADE,
        related_name="topics",
        verbose_name=_("Занятие"),
    )
    date = models.DateField(_("Дата занятия"))
    topic = models.CharField(_("Тема занятия"), max_length=300)
    hours = models.DecimalField(
        _("Академических часов"), max_digits=5, decimal_places=2, default=0
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lesson_topics",
        verbose_name=_("Кто заполнил"),
    )
    updated_at = models.DateTimeField(_("Обновлено"), auto_now=True)

    class Meta:
        ordering = ("-date", "schedule__start_time")
        indexes = [
            # Темы занятий выбирают за период: выборка идёт по дате.
            models.Index(fields=["date"], name="lesson_topic_date_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "date"], name="unique_topic_per_lesson_date"
            )
        ]
        verbose_name = _("Тема занятия")
        verbose_name_plural = _("Темы занятий")

    def save(self, *args, **kwargs):
        # Часы всегда пересчитываются от времени занятия.
        self.hours = self.schedule.academic_hours
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.date:%d.%m.%Y} {self.topic}"


class Schedule(models.Model):
    """Занятие в расписании: группа, день недели и своё время начала/окончания."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0, _("Понедельник")
        TUESDAY = 1, _("Вторник")
        WEDNESDAY = 2, _("Среда")
        THURSDAY = 3, _("Четверг")
        FRIDAY = 4, _("Пятница")
        SATURDAY = 5, _("Суббота")
        SUNDAY = 6, _("Воскресенье")

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="schedules", verbose_name=_("Группа")
    )
    # Адрес и кабинет — у каждого занятия свои: расписание филиалов независимо,
    # но время преподавателя общее для всех адресов.
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="schedules",
        verbose_name=_("Адрес"),
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.SET_NULL,
        related_name="schedules",
        verbose_name=_("Кабинет"),
        null=True,
        blank=True,
    )
    day_of_week = models.PositiveSmallIntegerField(
        _("День недели"), choices=Weekday.choices
    )
    # Разовое занятие (мероприятие, ивент) проходит только в этот день и не повторяется.
    # Пусто — обычное занятие, повторяющееся каждую неделю по дню выше.
    lesson_date = models.DateField(
        _("Дата разового занятия"),
        null=True,
        blank=True,
        help_text=_("Пусто — занятие повторяется каждую неделю"),
    )
    # Номер урока не нужен: и порядок, и ячейки таблицы определяет время занятия.
    start_time = models.TimeField(_("Начало занятия"))
    end_time = models.TimeField(_("Окончание занятия"))
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="schedules",
        verbose_name=_("Преподаватель"),
    )
    # Подмена: занятие ведёт заменяющий преподаватель, поэтому формально занятое
    # время основного преподавателя при проверке конфликтов не учитывается.
    ignore_teacher_conflict = models.BooleanField(
        _("Подмена: не учитывать занятость преподавателя"), default=False
    )
    is_substitution = models.BooleanField(
        _("Подмена"),
        default=False,
        help_text=_("Отметка, что занятие ведёт заменяющий преподаватель: в расписании появится значок «подмена»."),
    )
    # Кого заменяет подмена. Поле хранит связь явно: по одному совпадению места
    # (группа, дата, время) заменяемого не всегда видно — исходное занятие могли
    # перенести или удалить, а у разового занятия преподаватель меняется прямо в
    # записи, и прежний иначе терялся бы. Пусто у обычных занятий и у старых
    # подмен: для них подпись «Подмена для …» ищется по месту.
    replaced_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="substituted_lessons",
        verbose_name=_("Кого заменяют"),
        help_text=_(
            "Для подмены: преподаватель, вместо которого ведёт занятие заменяющий. "
            "Пусто — подпись ищется по совпадению группы, даты и времени."
        ),
    )

    class Meta:
        ordering = ("day_of_week", "start_time")
        constraints = [
            # Обычное занятие: у группы не может быть двух занятий в один день недели
            # в одно и то же время.
            models.UniqueConstraint(
                fields=["group", "day_of_week", "start_time"],
                condition=models.Q(lesson_date__isnull=True),
                name="unique_group_lesson_start",
            ),
            # Разовое занятие: то же правило, но в пределах своей даты — разовые занятия
            # на разные даты одного дня недели друг другу не мешают.
            models.UniqueConstraint(
                fields=["group", "lesson_date", "start_time"],
                condition=models.Q(lesson_date__isnull=False),
                name="unique_group_one_off_start",
            ),
        ]
        verbose_name = _("Занятие")
        verbose_name_plural = _("Расписание")

    @property
    def is_one_off(self):
        """Разовое занятие: есть дата, значит повторяется не каждую неделю."""
        return self.lesson_date is not None

    @property
    def date_label(self):
        """Пометка для интерфейса: «разовое · 12.10.2026» (для обычного — пусто)."""
        if self.lesson_date is None:
            return ""
        return _("разовое · %(date)s") % {"date": f"{self.lesson_date:%d.%m.%Y}"}

    def occurs_on(self, day):
        """Проходит ли занятие в этот день (date).

        Разовое занятие — только в свою дату, обычное — каждую неделю в свой день.
        """
        if self.lesson_date is not None:
            return day == self.lesson_date
        return day.weekday() == self.day_of_week

    def save(self, *args, **kwargs):
        # У разового занятия день недели считается от даты: все места журнала ищут
        # занятия по дню недели и продолжают работать без изменений.
        if self.lesson_date is not None:
            self.day_of_week = self.lesson_date.weekday()
        super().save(*args, **kwargs)

    @property
    def subject(self):
        return self.group.subject

    @property
    def time_range(self):
        """«10:00–11:30»."""
        return f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"

    @property
    def sort_key(self):
        """Ключ сортировки занятий внутри дня."""
        return (self.start_time, self.end_time)

    @property
    def duration_minutes(self):
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        return max(end - start, 0)

    @property
    def academic_hours(self):
        """Академические часы занятия: 1 час = 45 минут (90 минут = 2 часа)."""
        from decimal import Decimal

        return (Decimal(self.duration_minutes) / Decimal(45)).quantize(Decimal("0.01"))

    def __str__(self):
        if self.lesson_date is not None:
            return _("разовое, %(date)s, %(time)s — %(group)s") % {
                "date": f"{self.lesson_date:%d.%m.%Y}",
                "time": self.time_range,
                "group": self.group.name,
            }
        return _("%(day)s, %(time)s — %(group)s") % {
            "day": self.get_day_of_week_display(),
            "time": self.time_range,
            "group": self.group.name,
        }


class Attendance(models.Model):
    """Отметка посещаемости ученика на конкретном занятии."""

    class Status(models.TextChoices):
        PRESENT = "P", _("Присутствовал")
        ABSENT = "A", _("Отсутствовал")
        LATE = "L", _("Опоздал")

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="attendances", verbose_name=_("Ученик")
    )
    schedule = models.ForeignKey(
        Schedule, on_delete=models.CASCADE, related_name="attendances", verbose_name=_("Занятие")
    )
    date = models.DateField(_("Дата"))
    status = models.CharField(
        _("Статус"), max_length=1, choices=Status.choices, default=Status.PRESENT
    )

    class Meta:
        ordering = ("-date", "student__last_name")
        indexes = [
            # Посещаемость выбирают и сортируют по дате: без индекса база
            # делает SCAN + TEMP B-TREE FOR ORDER BY.
            models.Index(fields=["date"], name="attendance_date_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "schedule", "date"],
                name="unique_attendance_per_student_lesson_day",
            )
        ]
        verbose_name = _("Посещаемость")
        verbose_name_plural = _("Посещаемость")

    def __str__(self):
        return f"{self.student} — {self.date} — {self.get_status_display()}"
