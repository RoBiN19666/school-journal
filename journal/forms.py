"""Формы электронного журнала со стилями Tailwind."""
import ipaddress
import os
from datetime import date

from django import forms
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import FileExtensionValidator
from django.db.models import Q
from django.utils import timezone

#: Подписи, подсказки и сообщения об ошибках переводятся лениво: строки вычисляются
#: при импорте модуля (label=, help_text=, choices), а язык запроса известен только
#: в момент показа формы. gettext_lazy откладывает перевод до обращения к строке.
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.forms import (
    PasswordChangeForm,
    SetPasswordForm,
    UserCreationForm,
)

#: Подписи, подсказки и сообщения об ошибках переводятся лениво: строки вычисляются
#: при импорте модуля (label=, help_text=, choices), а язык запроса известен только
#: в момент показа формы. gettext_lazy откладывает перевод до обращения к строке.

from .absences import absence_on
from .availability import (
    DEFAULT_DURATION_MINUTES,
    exclude_cancelled,
    occurs_on_condition,
    occurs_on_weekday_condition,
    room_conflict,
    teacher_availability,
    teacher_conflict,
)
from .models import (
    AcademicYear,
    Branch,
    ContestResult,
    Group,
    IMAGE_EXTENSIONS,
    IpRule,
    MailSettings,
    Room,
    Schedule,
    Student,
    Subject,
    TeacherAbsence,
    TeacherDevelopment,
    User,
    SiteSettings,
)

INPUT_CSS = (
    "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 "
    "shadow-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
)
SELECT_CSS = INPUT_CSS + " pr-8"
CHECKBOX_CSS = (
    "h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
)
FILE_CSS = (
    "block w-full text-sm text-slate-500 file:mr-3 file:rounded-lg file:border-0 "
    "file:bg-indigo-50 file:px-3 file:py-2 file:text-sm file:font-medium "
    "file:text-indigo-700 hover:file:bg-indigo-100"
)


class TailwindFormMixin:
    """Аккуратно навешивает классы Tailwind на все поля формы."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.FileInput):
                css = FILE_CSS
            elif isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
                css = CHECKBOX_CSS
            elif isinstance(widget, forms.CheckboxSelectMultiple):
                css = CHECKBOX_CSS
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css = SELECT_CSS
            else:
                css = INPUT_CSS
            current = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{current} {css}".strip()


def teacher_choice_label(teacher):
    """Подпись преподавателя в списке выбора.

    Отключённую учётную запись помечаем: её оставляют в списке только ради того,
    чтобы можно было сохранить старую запись без принудительной смены педагога.
    """
    if teacher.is_active:
        return teacher.full_name
    # Пометка «(отключён)» переводится отдельно: join даёт ровно прежний текст
    # «Фамилия Имя (отключён)» и не требует склейки ленивой строки с обычной.
    return _("(отключён)").join((teacher.full_name, ""))


def teacher_choices(*, current=(), restrict_to=None):
    """Преподаватели для списка выбора.

    Действующие показываем всегда; текущего преподавателя записи оставляем
    в списке даже с отключённой учётной записью — иначе при правке старой
    программы, занятия или курса форма потребовала бы выбрать другого.
    Новым записям достаются только действующие преподаватели.
    """
    queryset = User.objects.filter(role=User.Role.TEACHER)
    if restrict_to is not None:
        queryset = queryset.filter(pk__in=[item.pk for item in restrict_to])
    keep = [item.pk for item in current if item is not None and item.pk]
    if keep:
        queryset = queryset.filter(Q(is_active=True) | Q(pk__in=keep))
    else:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("last_name", "first_name")


class TeacherChoiceField(forms.ModelChoiceField):
    """Один преподаватель: отключённого помечаем подписью «отключён»."""

    def label_from_instance(self, obj):
        return teacher_choice_label(obj)


class TeacherMultipleChoiceField(forms.ModelMultipleChoiceField):
    """Несколько преподавателей: подпись та же, что у одиночного списка."""

    def label_from_instance(self, obj):
        return teacher_choice_label(obj)


class TeacherCheckboxWidget(forms.SelectMultiple):
    """Список преподавателей галочками.

    Настоящий <select multiple> остаётся в форме и уходит на сервер как раньше,
    а галочки показывают и меняют его выбранные значения
    (см. journal/static/journal/subjects-form.js).
    """

    template_name = "journal/widgets/teacher_checks.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        options = [
            option for group in context["widget"]["optgroups"] for option in group[1]
        ]
        context["widget"]["selected_count"] = sum(
            1 for option in options if option["selected"]
        )
        context["widget"]["total_count"] = len(options)
        return context


class StudentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Student
        fields = ("last_name", "first_name", "middle_name", "birth_date",
                  "dismissal_status")
        widgets = {
            "birth_date": forms.DateInput(
                attrs={"type": "date"}, format="%Y-%m-%d"
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["last_name"].widget.attrs["placeholder"] = _("Иванов")
        self.fields["first_name"].widget.attrs["placeholder"] = _("Иван")
        self.fields["middle_name"].widget.attrs["placeholder"] = _("Иванович")
        self.fields["middle_name"].required = False
        self.fields["birth_date"].required = False
        self.fields["birth_date"].help_text = _("Можно не заполнять")
        self.fields["dismissal_status"].label = _(
            "Отпускать ребёнка во время беспилотной опасности"
        )
        self.fields["dismissal_status"].help_text = _(
            "«Нет заявления» — заявления от родителей нет; «Есть заявление, не отпускать»; "
            "«Есть заявление, можно отпускать». Видно педагогу в журнале посещаемости."
        )


class ContestResultForm(TailwindFormMixin, forms.ModelForm):
    """Участие ребёнка в конкурсе: дата, мероприятие, результат."""

    class Meta:
        model = ContestResult
        fields = ("date", "title", "student", "status", "subject", "comment")
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, students=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].label = _("Дата")
        self.fields["title"].label = _("Название мероприятия")
        self.fields["title"].widget.attrs["placeholder"] = _(
            "Например: Олимпиада по математике"
        )
        if students is not None:
            # Преподаватель выбирает только своих учеников.
            self.fields["student"].queryset = students
        else:
            self.fields["student"].queryset = Student.objects.order_by("last_name", "first_name")
        self.fields["student"].empty_label = _("— выберите ученика —")
        self.fields["student"].label = _("ФИО ученика")
        self.fields["status"].label = _("Статус")
        self.fields["subject"].required = False
        self.fields["comment"].required = False


class TeacherDevelopmentForm(TailwindFormMixin, forms.ModelForm):
    """Повышение квалификации и конкурсы педагога."""

    teacher = TeacherChoiceField(
        queryset=User.objects.none(),
        label=_("Педагог"),
        empty_label=_("— выберите педагога —"),
    )

    class Meta:
        model = TeacherDevelopment
        fields = ("date", "kind", "teacher", "program", "organization", "hours", "result")
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, teacher_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher_user = teacher_user
        if teacher_user is not None and teacher_user.is_teacher:
            # Преподаватель вносит записи только о себе, поэтому выбор педагога
            # ему не нужен — поле убираем, а запись привязываем автоматически.
            self.fields.pop("teacher", None)
        else:
            # Отключённые (is_active=False) преподаватели в выборе не нужны, но
            # педагог старой записи остаётся в списке: иначе такую запись нельзя
            # сохранить без принудительной смены педагога.
            current = self.instance.teacher if self.instance.pk else None
            self.fields["teacher"].queryset = teacher_choices(current=[current])
            self.fields["teacher"].empty_label = _("— выберите педагога —")
        self.fields["program"].widget.attrs["placeholder"] = _(
            "Например: Методика преподавания математики"
        )
        # Подсказки и подписи полей задаём здесь: в save() этот блок был бы
        # недостижим (стоял после return) и в форме не отображался.
        self.fields["organization"].widget.attrs["placeholder"] = _(
            "Например: Институт развития образования"
        )
        self.fields["organization"].required = False
        self.fields["hours"].required = False
        self.fields["result"].required = False
        self.fields["result"].label = _("Результат (для конкурсов)")
        self.fields["kind"].label = _("Вид")

    def save(self, commit=True):
        # Преподавателю запись всегда привязываем к нему самому.
        if getattr(self, "teacher_user", None) is not None and self.teacher_user.is_teacher:
            self.instance.teacher = self.teacher_user
        return super().save(commit)


class TeacherAbsenceForm(TailwindFormMixin, forms.ModelForm):
    """Период отсутствия: преподаватель, две даты, причина и примечание.

    Форма живёт в отдельном разделе «Отсутствия» и служит и добавлению, и
    правке. Порядок дат и пересечение периодов проверяет модель
    (``TeacherAbsence.clean``) — здесь эти проверки не повторяются.
    """

    teacher = TeacherChoiceField(
        queryset=User.objects.none(),
        label=_("Преподаватель"),
        empty_label=_("— выберите преподавателя —"),
        required=True,
    )

    class Meta:
        model = TeacherAbsence
        fields = ("teacher", "date_from", "date_to", "kind", "comment")
        widgets = {
            "date_from": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_to": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            # Примечание — короткая заметка, а не полотно: две строки и ограниченная
            # ширина, высоту при необходимости тянут вниз за нижний край (resize-y).
            "comment": forms.Textarea(
                attrs={"rows": 2, "class": "resize-y max-w-md"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Новым записям достаются только действующие преподаватели. У правки в
        # списке остаётся уже выбранный педагог, даже если его учётную запись
        # отключили: иначе старую запись нельзя было бы сохранить без смены.
        current = []
        if self.instance.pk and self.instance.teacher_id:
            current.append(self.instance.teacher)
        self.fields["teacher"].queryset = teacher_choices(current=current)
        self.fields["comment"].required = False
        self.fields["comment"].widget.attrs["placeholder"] = _(
            "Например: справка из поликлиники"
        )


class SubjectForm(TailwindFormMixin, forms.ModelForm):
    """Программа создаётся внутри активного полугодия и активного адреса."""
    teacher = TeacherChoiceField(
        queryset=User.objects.none(),
        label=_("Основной преподаватель"),
        empty_label=_("— выберите основного преподавателя —"),
        required=True,
    )
    teachers = TeacherMultipleChoiceField(
        queryset=User.objects.none(),
        label=_("Преподаватели программы"),
        required=False,
        widget=TeacherCheckboxWidget(
            attrs={"size": 6, "data-main-teacher-field": "#id_teacher"}
        ),
    )

    class Meta:
        model = Subject
        fields = ("name", "teacher", "teachers")

    def __init__(self, *args, term=None, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.term = term if term is not None else getattr(self.instance, "term", None)
        self.branch = (
            branch if branch is not None else getattr(self.instance, "branch", None)
        )
        # Только действующие преподаватели: отключённую учётную запись нельзя
        # назначить ни основным преподавателем, ни подменяющим. Исключение —
        # преподаватели, уже выбранные у этой программы: старую программу нужно
        # сохранять без принудительной смены педагога.
        instance = self.instance
        current_main = instance.teacher if instance.pk else None
        current_extra = list(instance.teachers.all()) if instance.pk else []
        teachers = teacher_choices(current=[current_main, *current_extra])
        self.fields["teacher"].queryset = teachers
        self.fields["teacher"].required = True
        self.fields["teacher"].empty_label = _("— выберите основного преподавателя —")
        self.fields["teacher"].label = _("Основной преподаватель")
        self.fields["teacher"].help_text = _("Подставляется в новые занятия по умолчанию.")
        # В списке держим всех действующих преподавателей: если основного сменят,
        # прежний должен остаться доступным для выбора. Текущий основной отмечен
        # в панели подписью «основной»: сервер добавит его в список сам.
        self.fields["teachers"].queryset = teachers
        self.fields["teachers"].required = False
        self.fields["teachers"].label = _("Преподаватели программы")
        self.fields["teachers"].help_text = _(
            "Отметьте тех, кто ведёт программу или может подменять. Поиск сверху сужает "
            "список, отмеченные поднимаются вверх. Основной преподаватель входит "
            "автоматически и отмечен подписью «основной»: два преподавателя одной "
            "программы — это соведущие, а не подмена. Если смените основного, "
            "прежний останется в этом списке."
        )
        self.fields["name"].widget.attrs["placeholder"] = _("Например: Математика")
        self.fields["name"].help_text = _(
            "Программа существует только в выбранных полугодии и адресе. "
            "В другом полугодии или на другом адресе его нужно создать или перенести."
        )

    def clean_name(self):
        name = (self.cleaned_data["name"] or "").strip()
        duplicate = Subject.objects.filter(name__iexact=name)
        if self.term is not None:
            duplicate = duplicate.filter(term=self.term)
        if self.branch is not None:
            duplicate = duplicate.filter(branch=self.branch)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            where = []
            if self.branch is not None:
                where.append(self.branch.name)
            if self.term is not None:
                where.append(self.term.title)
            # Части сообщения переводим по отдельности: подстановки — это данные из
            # базы (название адреса и полугодия), их переводить нельзя. Ленивая строка
            # поддерживает оператор % — он подставляет значения и даёт прежний текст.
            location = (
                _(" в %(where)s") % {"where": ", ".join(where)} if where else ""
            )
            raise forms.ValidationError(
                _(
                    "Программа «%(name)s» уже есть%(where)s. Перенести его из другого "
                    "полугодия можно в разделе «Учебные периоды» → "
                    "«Перенести из другого полугодия»."
                )
                % {"name": name, "where": location}
            )
        return name

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.term is not None:
            instance.term = self.term
        if self.branch is not None:
            instance.branch = self.branch
        if commit:
            instance.save()
            self.save_m2m()
            # Основной преподаватель всегда входит в список тех, кто ведёт программу.
            if instance.teacher_id:
                instance.teachers.add(instance.teacher_id)
        return instance


class GroupForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Group
        fields = ("name",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs["placeholder"] = _("Например: Поток 1")

    def clean(self):
        cleaned = super().clean()
        subject = self.instance.subject
        term = self.instance.term
        if subject is not None and subject.term_id and term is not None:
            if subject.term_id != term.pk:
                raise forms.ValidationError(
                    _("Группа должна быть в том же полугодии и адресе, что и программа.")
                )
        return cleaned

    def clean_name(self):
        name = self.cleaned_data["name"]
        subject = self.instance.subject
        term = self.instance.term
        duplicate = Group.objects.filter(subject=subject, name__iexact=name)
        if term is not None:
            duplicate = duplicate.filter(term=term)
        if subject and duplicate.exists():
            raise forms.ValidationError(
                _("Группа с таким названием уже есть у этого программы в этом полугодии.")
            )
        return name


class RoomSelect(forms.Select):
    """Список кабинетов с пометкой, к какому адресу относится каждый."""

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex, attrs
        )
        if value:
            option["attrs"]["data-branch"] = str(
                getattr(self, "room_branches", {}).get(str(value), "")
            )
        return option


class ScheduleForm(TailwindFormMixin, forms.ModelForm):
    room = forms.ModelChoiceField(
        queryset=Room.objects.all(),
        label=_("Кабинет"),
        empty_label=_("— выберите кабинет —"),
        widget=RoomSelect,
        required=True,
    )
    teacher = TeacherChoiceField(
        queryset=User.objects.none(),
        label=_("Преподаватель занятия"),
        empty_label=_("— выберите преподавателя —"),
        required=False,
    )

    class Meta:
        model = Schedule
        fields = (
            "group",
            "teacher",
            "room",
            "day_of_week",
            "lesson_date",
            "start_time",
            "end_time",
            "ignore_teacher_conflict",
            "is_substitution",
            "replaced_teacher",
        )
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "lesson_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, term=None, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        groups = Group.objects.select_related("subject__teacher", "subject__branch")
        if term is not None:
            # Занятие можно поставить только группе активного полугодия.
            groups = groups.filter(term=term)
        if branch is not None:
            groups = groups.filter(subject__branch=branch)
        self.fields["group"].queryset = groups
        self.fields["group"].empty_label=_("— выберите группу —")

        # Адрес занятия определяется программой группы, кабинеты — из этого адреса.
        # Значения из адреса (initial) важнее значений занятия: форма перезагружается
        # при смене группы, и выбор не должен возвращаться к прежнему.
        chosen_group = None
        group_value = self.data.get("group") if self.is_bound else self.initial.get("group")
        if group_value and str(group_value).isdigit():
            chosen_group = Group.objects.filter(pk=int(group_value)).first()
        if chosen_group is None and self.instance.pk:
            chosen_group = self.instance.group
        self.chosen_group = chosen_group
        address = chosen_group.subject.branch if chosen_group else branch

        # Выбранная группа обязана остаться в списке: иначе браузер молча покажет первую
        # и выбор «сбросится» — например, если группа из другого адреса или полугодия.
        if chosen_group is not None and not groups.filter(pk=chosen_group.pk).exists():
            self.fields["group"].queryset = (
                Group.objects.filter(pk=chosen_group.pk) | groups
            )
            address = chosen_group.subject.branch

        # Преподавателя выбираем из тех, кто ведёт эту программу (с подменами).
        subject = chosen_group.subject if chosen_group else None
        # Преподаватель самого занятия остаётся в списке, даже если он не из
        # программы или его учётную запись отключили: иначе уже стоящее занятие
        # нельзя было бы пересохранить, не меняя преподавателя.
        current_teacher = self.instance.teacher if self.instance.pk else None
        if subject is not None:
            options = list(subject.teacher_options)
            if current_teacher is not None and current_teacher not in options:
                options.append(current_teacher)
            if options:
                teacher_queryset = teacher_choices(
                    current=[current_teacher], restrict_to=options
                )
            else:
                teacher_queryset = User.objects.none()
        else:
            teacher_queryset = teacher_choices(current=[current_teacher])
        self.fields["teacher"].queryset = teacher_queryset
        if subject is not None:
            self.fields["teacher"].help_text = _(
                "Можно выбрать любого из преподавателей программы: %(names)s"
            ) % {"names": ", ".join(item.full_name for item in subject.teacher_options)}
        else:
            self.fields["teacher"].help_text = _(
                "Сначала выберите группу — покажем преподавателей её программы"
            )
        self.fields["ignore_teacher_conflict"].label = _(
            "1. Не учитывать занятость преподавателя"
        )
        self.fields["ignore_teacher_conflict"].help_text = _(
            "Снимает проверку занятости преподавателя по времени; кабинет и группа "
            "проверяются."
        )
        self.fields["is_substitution"].label = _("2. Подмена (заменяющий преподаватель)")
        self.fields["is_substitution"].help_text = _(
            "Занятие ведёт заменяющий преподаватель: в расписании — значок «подмена», "
            "его занятость проверяется."
        )
        # Кого заменяют — необязательное поле подмены: по нему в журнале видно
        # «Подмена для …». В списке — действующие преподаватели ПРОГРАММЫ и тот,
        # кто уже записан у занятия: иначе правку старой подмены с отключённым
        # преподавателем нельзя было бы сохранить.
        current_replaced = self.instance.replaced_teacher if self.instance.pk else None
        if subject is not None:
            replaced_options = list(subject.teacher_options)
            if current_replaced is not None and current_replaced not in replaced_options:
                replaced_options.append(current_replaced)
            replaced_queryset = teacher_choices(
                current=[current_replaced], restrict_to=replaced_options
            )
        else:
            replaced_queryset = teacher_choices(current=[current_replaced])
        self.fields["replaced_teacher"].queryset = replaced_queryset
        self.fields["replaced_teacher"].label = _("3. Кого заменяют (для подмены)")
        self.fields["replaced_teacher"].empty_label = _("— не указано —")
        self.fields["replaced_teacher"].help_text = _(
            "Преподаватель программы, вместо которого ведёт занятие заменяющий. "
            "По нему показывается «Подмена для … · вид отсутствия и даты». Можно не "
            "заполнять: тогда подпись ищется по совпадению группы, даты и времени."
        )
        if not self.instance.pk and not self.is_bound and subject is not None:
            self.initial.setdefault("teacher", subject.teacher_id)

        rooms = Room.objects.select_related("branch")
        if address is not None:
            rooms = rooms.filter(branch=address)
        rooms = rooms.order_by("branch__order", "order", "name")
        self.fields["room"].queryset = rooms
        self.fields["room"].widget.room_branches = {
            str(room.pk): room.branch_id for room in rooms
        }
        self.address = address
        self.fields["room"].help_text = (
            _("Только кабинеты адреса «%(address)s»") % {"address": address.name}
            if address
            else _("Сначала выберите группу — покажем кабинеты её адреса")
        )
        self.fields["start_time"].help_text = _("Время у каждого занятия своё")
        self.fields["end_time"].help_text = _(
            "Подставляется автоматически (%(minutes)s минут), можно изменить"
        ) % {"minutes": DEFAULT_DURATION_MINUTES}
        # День недели можно не выбирать, если указана дата разового занятия:
        # в этом случае день считается по дате (см. clean).
        self.fields["day_of_week"].required = False
        self.fields["day_of_week"].help_text = _(
            "Для занятия, повторяющегося каждую неделю; при дате разового "
            "определится по ней."
        )
        self.fields["lesson_date"].label = _("Дата разового занятия")
        self.fields["lesson_date"].help_text = _(
            "Заполните для разового занятия; пусто — повторяется каждую неделю."
        )

    def foreign_teacher_error(self, group):
        """Понятная ошибка, когда прислан преподаватель не из программы.

        Поле «Преподаватель занятия» показывает только преподавателей программы,
        поэтому чужое значение отбивает сама проверка поля — с текстом «Выберите
        корректный вариант». Здесь мы заменяем его объяснением: кого именно
        нельзя выбрать и где его закрепить.
        """
        raw = self.data.get("teacher") if self.is_bound else None
        if not raw or not str(raw).isdigit():
            return None
        outside = User.objects.filter(pk=int(raw), role=User.Role.TEACHER).first()
        if outside is None:
            return None
        if outside.pk in {item.pk for item in group.subject.teacher_options}:
            # Преподаватель программы: значит, дело в другом (например, отключён).
            return None
        self._errors.pop("teacher", None)
        self.add_error(
            "teacher",
            _(
                "%(teacher)s не ведёт программу «%(subject)s»: занятие может вести "
                "только преподаватель программы. Добавьте его в карточке программы "
                "в списке «Преподаватели программы»."
            )
            % {"teacher": outside.full_name, "subject": group.subject.name},
        )
        return outside

    def clean(self):
        cleaned = super().clean()
        group = cleaned.get("group")
        teacher = cleaned.get("teacher")
        room = cleaned.get("room")
        day_of_week = cleaned.get("day_of_week")
        lesson_date = cleaned.get("lesson_date")
        start_time = cleaned.get("start_time")
        end_time = cleaned.get("end_time")
        is_substitution = bool(cleaned.get("is_substitution"))
        # Две галочки — разные вещи. Проверку занятости преподавателя по времени
        # снимает только «Не учитывать занятость преподавателя». «Подмена» — чистая
        # пометка «занятие ведёт заменяющий»: занятость заменяющего проверяется как
        # у любого преподавателя, иначе подмену можно было бы поставить в уже занятое
        # у него время (см. ``teacher_conflict``). Отменённое занятие конфликта не
        # создаёт само по себе — это отдельная проверка в ``availability``.
        # Конфликты по умолчанию включены: без галочек поведение прежнее.
        ignore_conflict = bool(cleaned.get("ignore_teacher_conflict"))
        cleaned["ignore_teacher_conflict"] = ignore_conflict
        # Значение галочки «подмена» возвращаем в cleaned_data: по нему форма сохраняет
        # занятие и рисует значок в расписании.
        cleaned["is_substitution"] = is_substitution

        # Пустой выбор в списке приходит пустой строкой, а не None.
        if day_of_week in (None, ""):
            day_of_week = None

        # Дата разового занятия сама определяет день недели: так занятие находят
        # расписание, недельный журнал и отметки посещаемости.
        if lesson_date is not None:
            day_of_week = lesson_date.weekday()
            cleaned["day_of_week"] = day_of_week
            self.instance.day_of_week = day_of_week
        elif day_of_week is None:
            self.add_error(
                "day_of_week",
                _("Выберите день недели или укажите дату разового занятия."),
            )

        if group and not group.subject.teacher_id and not group.subject.teachers.exists():
            raise forms.ValidationError(
                _(
                    "У программы «%(name)s» не указан преподаватель. "
                    "Назначьте его в разделе «Программы»."
                )
                % {"name": group.subject.name}
            )
        if group and teacher is None:
            # Преподаватель не из программы: у поля свой список вариантов, поэтому
            # «некорректный вариант» методисту ничего не объясняет — заменяем
            # ошибку поля понятным текстом (запись всё равно не сохранится).
            self.foreign_teacher_error(group)
            # Преподавателя не выбрали — берём основного у программы.
            teacher = group.subject.teacher
            if teacher is None:
                options = group.subject.teacher_options
                teacher = options[0] if options else None
            if teacher is not None:
                cleaned["teacher"] = teacher
        if group and teacher is not None:
            allowed = {item.pk for item in group.subject.teacher_options}
            # Преподавателя проверяем, когда его выбирают (новое занятие) или
            # меняют. Уже стоящее занятие с преподавателем «не из программы»
            # (такие есть в старых базах) править можно: иначе его нельзя было бы
            # даже пересохранить, не тронув преподавателя.
            unchanged_teacher = (
                self.instance.pk
                and teacher.pk == self.instance.teacher_id
                and group.pk == self.instance.group_id
            )
            if teacher.pk not in allowed and not unchanged_teacher:
                self.add_error(
                    "teacher",
                    _(
                        "%(teacher)s не ведёт программу «%(subject)s»: занятие может "
                        "вести только преподаватель программы. Добавьте его в карточке "
                        "программы в списке «Преподаватели программы»."
                    )
                    % {"teacher": teacher.full_name, "subject": group.subject.name},
                )
            # Кого заменяют — тоже только преподаватель программы.
            replaced = cleaned.get("replaced_teacher")
            if replaced is not None:
                unchanged_replaced = (
                    self.instance.pk
                    and replaced.pk == self.instance.replaced_teacher_id
                    and group.pk == self.instance.group_id
                )
                if replaced.pk not in allowed and not unchanged_replaced:
                    self.add_error(
                        "replaced_teacher",
                        _(
                            "%(teacher)s не ведёт программу «%(subject)s»: заменять "
                            "можно только преподавателя программы."
                        )
                        % {
                            "teacher": replaced.full_name,
                            "subject": group.subject.name,
                        },
                    )

        # Подмену нельзя поставить тому, кто сам отсутствует в этот день: занятие
        # он не проведёт. Правило только про подмены: обычное занятие даты не
        # имеет, а правка уже стоящего разового занятия отсутствующего
        # преподавателя (его помечают отменённым) отбиваться не должна.
        if is_substitution and lesson_date is not None and teacher is not None:
            teacher_absence = absence_on(teacher, lesson_date)
            if teacher_absence is not None:
                self.add_error(
                    "teacher",
                    _(
                        "%(teacher)s отсутствует %(date)s: %(absence)s. "
                        "Подмену проведёт другой преподаватель."
                    )
                    % {
                        "teacher": teacher.full_name,
                        "date": f"{lesson_date:%d.%m.%Y}",
                        "absence": teacher_absence.title,
                    },
                )

        # Разовое занятие на то же место (группа, дата и время начала) уже есть —
        # второе база не примет (``unique_group_one_off_start``). Отменённые при
        # этом считаем тоже: место в расписании занято, и подмена на нём уже
        # может стоять — иначе была бы ошибка базы вместо понятного текста.
        if group is not None and lesson_date is not None and start_time is not None:
            duplicate = Schedule.objects.filter(
                group=group, lesson_date=lesson_date, start_time=start_time
            ).select_related("teacher")
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            other = duplicate.first()
            if other is not None:
                self.add_error(
                    "start_time",
                    _(
                        "На это время у группы уже есть занятие: %(date)s, %(time)s — "
                        "%(teacher)s%(substitution)s. Откройте его в расписании, "
                        "чтобы изменить, или выберите другое время."
                    )
                    % {
                        "date": f"{lesson_date:%d.%m.%Y}",
                        "time": f"{start_time:%H:%M}",
                        "teacher": (
                            other.teacher.full_name if other.teacher_id else _("без преподавателя")
                        ),
                        "substitution": (
                            _(" (подмена)") if other.is_substitution else ""
                        ),
                    },
                )

        address = group.subject.branch if group else self.address
        if group and group.subject.term_id and group.term_id != group.subject.term_id:
            raise forms.ValidationError(
                _("Программа группы относится к другому полугодию. Проверьте данные.")
            )

        # Разовое занятие бывает один раз и должно попадать в полугодие своей группы:
        # иначе календарь его не покажет, а выгрузка не возьмёт — занятие потеряется.
        if lesson_date is not None and group is not None:
            term = group.term
            if term is not None and not (term.start_date <= lesson_date <= term.end_date):
                self.add_error(
                    "lesson_date",
                    _(
                        "Дата %(date)s вне полугодия группы: %(term)s идёт с %(start)s "
                        "по %(end)s. Выберите дату внутри полугодия или сделайте занятие "
                        "обычным — оно будет повторяться каждую неделю."
                    )
                    % {
                        "date": f"{lesson_date:%d.%m.%Y}",
                        "term": term.title,
                        "start": f"{term.start_date:%d.%m.%Y}",
                        "end": f"{term.end_date:%d.%m.%Y}",
                    },
                )

        if room and address and room.branch_id != address.pk:
            self.add_error(
                "room",
                _(
                    "Кабинет «%(room)s» относится к адресу «%(room_branch)s». "
                    "Для этого занятия нужны кабинеты «%(address)s»."
                )
                % {
                    "room": room.name,
                    "room_branch": room.branch.name,
                    "address": address.name,
                },
            )

        if start_time and end_time and end_time <= start_time:
            self.add_error("end_time", _("Окончание должно быть позже начала занятия."))
            end_time = None

        term = group.term if group else None
        busy = None

        def same_group_conflict():
            """Занятия этой группы в пересекающееся время в тот же день.

            Для разового занятия «тот же день» — его дата: мешают обычные занятия
            этого дня недели и другие разовые занятия на эту же дату. Отменённые
            занятия (преподаватель отсутствует в этот день) не мешают: группа в
            это время свободна, поэтому на место отменённого занятия можно
            поставить подмену (см. ``exclude_cancelled``).
            Дня нет — считаем по дню недели: мешают и обычные занятия группы в этот
            день, и её разовые занятия, попадающие на него (см.
            ``occurs_on_weekday_condition``).
            """
            queryset = Schedule.objects.filter(
                group=group,
                start_time__lt=end_time,
                end_time__gt=start_time,
            )
            if lesson_date is not None:
                queryset = queryset.filter(occurs_on_condition(lesson_date))
            else:
                # Обычное занятие повторяется каждую неделю, поэтому в дату
                # разового занятия группы оно пройдёт в то же время — это конфликт.
                queryset = queryset.filter(
                    occurs_on_weekday_condition(day_of_week)
                )
            # Отменённые занятия (преподаватель отсутствует) группу не занимают: по
            # дате это её собственный день, без даты — разовые занятия, попавшие в
            # период отсутствия (см. ``exclude_cancelled``).
            queryset = exclude_cancelled(queryset, lesson_date)
            if self.instance.pk:
                queryset = queryset.exclude(pk=self.instance.pk)
            return queryset

        # У преподавателя не может быть двух занятий в одно время — на любом адресе.
        # Для занятий-подмен проверка отключается галочкой.
        if (
            group
            and teacher
            and not ignore_conflict
            and day_of_week is not None
            and start_time
            and end_time
        ):
            busy = teacher_conflict(
                teacher,
                day_of_week,
                start_time,
                end_time,
                exclude_pk=self.instance.pk,
                term=term,
                day=lesson_date,
            )
            if busy is not None:
                free = [
                    window["label"]
                    for day in teacher_availability(
                        teacher,
                        exclude_pk=self.instance.pk,
                        term=term,
                        day=lesson_date,
                    )
                    if day["value"] == day_of_week
                    for window in day["windows"]
                ]
                hint = (
                    _(" Свободно в этот день: %(windows)s.")
                    % {"windows": ", ".join(free)}
                    if free
                    else ""
                )
                where = (
                    _(" (%(branch)s)") % {"branch": busy.branch.name}
                    if busy.branch_id
                    else ""
                )
                self.add_error(
                    "start_time",
                    _(
                        "У преподавателя %(teacher)s в это время уже занятие: "
                        "%(time)s — %(subject)s · %(group)s%(where)s."
                    )
                    % {
                        "teacher": teacher.full_name,
                        "time": busy.time_range,
                        "subject": busy.group.subject.name,
                        "group": busy.group.name,
                        "where": where,
                    }
                    + hint,
                )

            if same_group_conflict().exists() and busy is None:
                self.add_error(
                    "start_time", _("У группы в это время уже есть занятие.")
                )

        # Группа не может заниматься дважды, даже если это занятие-подмена.
        if (
            group
            and (not teacher or ignore_conflict or not start_time or not end_time)
            and day_of_week is not None
            and start_time
            and end_time
        ):
            if same_group_conflict().exists():
                self.add_error(
                    "start_time", _("У группы в это время уже есть занятие.")
                )

        # Кабинет тоже не может быть занят дважды.
        if room and address and day_of_week is not None and start_time and end_time:
            busy_room = room_conflict(
                room,
                day_of_week,
                start_time,
                end_time,
                exclude_pk=self.instance.pk,
                term=term,
                day=lesson_date,
            )
            if busy_room is not None:
                # Для разового занятия в сообщение добавляется его дата.
                date_label = (
                    _(" (%(date)s)") % {"date": busy_room.date_label}
                    if busy_room.is_one_off
                    else ""
                )
                self.add_error(
                    "room",
                    _(
                        "Кабинет «%(room)s» занят: %(time)s — %(subject)s · %(group)s"
                        "%(date)s. Выберите другой кабинет или время."
                    )
                    % {
                        "room": room.name,
                        "time": busy_room.time_range,
                        "subject": busy_room.group.subject.name,
                        "group": busy_room.group.name,
                        "date": date_label,
                    },
                )
        return cleaned


class StudentImportForm(TailwindFormMixin, forms.Form):
    """Загрузка списка учеников из Excel."""

    file = forms.FileField(
        label=_("Файл Excel (.xlsx)"),
        help_text=_("Один столбец с ФИО в строке: «Иванов Иван Иванович» или «Иванов Иван»"),
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
    )
    group = forms.ModelChoiceField(
        queryset=Group.objects.none(),
        label=_("Добавить в группу (не обязательно)"),
        required=False,
        empty_label=_("— не добавлять в группу —"),
    )
    skip_duplicates = forms.BooleanField(
        label=_("Пропускать тех, кто уже есть в базе"),
        required=False,
        initial=True,
    )

    def __init__(self, *args, term=None, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        groups = Group.objects.select_related("subject", "term")
        if term is not None:
            groups = groups.filter(term=term)
        if branch is not None:
            groups = groups.filter(subject__branch=branch)
        self.fields["group"].queryset = groups.order_by("subject__name", "name")

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if not uploaded.name.lower().endswith((".xlsx", ".xlsm")):
            raise forms.ValidationError(_("Нужен файл Excel формата .xlsx"))
        if uploaded.size > 8 * 1024 * 1024:
            raise forms.ValidationError(_("Файл больше 8 МБ — разбейте список на части."))
        return uploaded


def _role_choices(viewer=None, instance=None):
    """Роли, которые можно назначить: преподавателя в списке сотрудников нет.

    «Главного администратора» может назначить только он сам.
    """
    from django.db import models as django_models

    choices = [(User.Role.METHODIST, _("Методист")), (User.Role.ADMIN, _("Администратор"))]
    if getattr(viewer, "is_superadmin", False) or getattr(instance, "is_superadmin", False):
        choices.insert(0, (User.Role.SUPERADMIN, _("Главный администратор")))
    return choices


#: Роли, дающие доступ к управлению журналом, — «администраторы».
ADMIN_ROLE_VALUES = (User.Role.SUPERADMIN, User.Role.ADMIN)

#: Сообщение: нельзя отключить учётную запись, под которой работаешь сам.
SELF_DISABLE_ERROR = _(
    "Нельзя выключить свою учётную запись — вы потеряете доступ к журналу. "
    "Попросите другого администратора."
)

#: Сообщение: журнал нельзя оставить без единого активного администратора.
LAST_ADMIN_ERROR = _(
    "Это единственный активный администратор. Сначала назначьте другого администратора."
)

#: Понятные русские сообщения об обязательных имени и фамилии.
NAME_ERROR_MESSAGES = {
    "first_name": {"required": _("Укажите имя.")},
    "last_name": {"required": _("Укажите фамилию.")},
}


def active_admins(exclude_pk=None):
    """Активные администраторы журнала: главный администратор тоже считается."""
    admins = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(role__in=ADMIN_ROLE_VALUES)
    )
    if exclude_pk is not None:
        admins = admins.exclude(pk=exclude_pk)
    return admins


class AccountSafetyMixin:
    """Общие проверки учётной записи: себя не выключаем, журнал без администратора не оставляем.

    ``viewer`` — тот, кто правит карточку. Проверки нужны и карточке
    преподавателя, и карточке сотрудника УМО, поэтому живут в одном месте.
    """

    #: Кто правит карточку; пусто — проверка «на себя» не применяется.
    viewer = None

    def clean(self):
        cleaned = super().clean()
        instance = getattr(self, "instance", None)
        if instance is None or not instance.pk:
            return cleaned

        disabled = bool(cleaned.get("disabled"))
        if disabled and self.viewer is not None and self.viewer.pk == instance.pk:
            self.add_error("disabled", SELF_DISABLE_ERROR)
            return cleaned

        # Останется ли учётная запись активным администратором после сохранения.
        role = cleaned.get("role", instance.role)
        was_admin = instance.is_active and (
            instance.is_superuser or instance.role in ADMIN_ROLE_VALUES
        )
        stays_admin = not disabled and (
            instance.is_superuser or role in ADMIN_ROLE_VALUES
        )
        if not was_admin or stays_admin:
            return cleaned
        if active_admins(exclude_pk=instance.pk).exists():
            return cleaned

        # У отключения ошибку показываем у галочки, у смены роли — у самой роли.
        if disabled:
            field = "disabled"
        else:
            field = "role" if "role" in self.fields else None
        self.add_error(field, LAST_ADMIN_ERROR)
        return cleaned


class TeacherCreateForm(TailwindFormMixin, UserCreationForm):
    """Создание учётной записи преподавателя."""

    class Meta:
        model = User
        fields = ("username", "last_name", "first_name", "middle_name")
        labels = {
            "username": _("Логин"),
            "last_name": _("Фамилия"),
            "first_name": _("Имя"),
        }
        error_messages = NAME_ERROR_MESSAGES

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = _("Латиница, без пробелов. Например: ivanov")
        self.fields["last_name"].required = True
        self.fields["first_name"].required = True
        self.fields["password1"].label = _("Пароль")
        self.fields["password2"].label = _("Повтор пароля")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.TEACHER
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
        return user






class StaffMemberCreateForm(TailwindFormMixin, UserCreationForm):
    """Создание учётной записи сотрудника: методиста или администратора."""

    class Meta:
        model = User
        fields = ("username", "last_name", "first_name", "middle_name", "role")
        labels = {
            "username": _("Логин"),
            "last_name": _("Фамилия"),
            "first_name": _("Имя"),
            "role": _("Роль"),
        }
        error_messages = NAME_ERROR_MESSAGES

    def __init__(self, *args, viewer=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Набор ролей задаёт _role_choices: «Главного администратора» видит и
        # назначает только он сам. Безусловную перезапись списка убрали: она
        # делала _role_choices мёртвой, и второго главного администратора
        # завести штатным интерфейсом было нельзя.
        if "role" in self.fields:
            self.fields["role"].choices = [
                (
                    value,
                    _("Методист (сотрудник УМО)") if value == User.Role.METHODIST else label,
                )
                for value, label in _role_choices(viewer, self.instance)
            ]
        self.fields["role"].initial = User.Role.METHODIST.value
        self.fields["username"].help_text = _("Латиница, без пробелов. Например: umo_ivanova")
        self.fields["last_name"].required = True
        self.fields["first_name"].required = True
        self.fields["password1"].label = _("Пароль")
        self.fields["password2"].label = _("Повтор пароля")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = self.cleaned_data["role"]
        user.is_staff = user.role == User.Role.ADMIN
        user.is_superuser = False
        if commit:
            user.save()
        return user






class ProfileForm(TailwindFormMixin, forms.ModelForm):
    """Свои данные: преподаватель (или любой сотрудник) правит своё ФИО."""

    class Meta:
        model = User
        fields = ("last_name", "first_name", "middle_name")
        labels = {
            "last_name": _("Фамилия"),
            "first_name": _("Имя"),
            "middle_name": _("Отчество"),
        }
        error_messages = NAME_ERROR_MESSAGES

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["last_name"].required = True
        self.fields["first_name"].required = True
        # Отчество заполняют по желанию.
        self.fields["middle_name"].required = False


class TeacherEditForm(TailwindFormMixin, AccountSafetyMixin, forms.ModelForm):
    """Данные преподавателя — правит методист или администратор."""

    class Meta:
        model = User
        fields = ("username", "last_name", "first_name", "middle_name")
        labels = {
            "username": _("Логин"),
            "last_name": _("Фамилия"),
            "first_name": _("Имя"),
        }
        error_messages = NAME_ERROR_MESSAGES

    def __init__(self, *args, viewer=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Кто правит карточку: своя учётная запись не выключается (см. AccountSafetyMixin).
        self.viewer = viewer
        instance = getattr(self, "instance", None)
        if instance and instance.pk:
            self.fields["disabled"].initial = not instance.is_active
            # Показываем, что за преподавателем закреплено: отключение ничего не удаляет,
            # но он исчезает из списков выбора при создании расписания и программ.
            lessons = Schedule.objects.filter(teacher=instance).count()
            programs = Subject.objects.filter(
                Q(teacher=instance) | Q(teachers=instance)
            ).distinct().count()
            parts = []
            if lessons:
                parts.append(
                    _("занятий в расписании — %(count)s") % {"count": lessons}
                )
            if programs:
                parts.append(_("программ — %(count)s") % {"count": programs})
            if parts:
                self.fields["disabled"].help_text = _(
                    "Сейчас за преподавателем закреплено: %(parts)s. "
                    "Учётная запись остаётся в базе со всеми данными, но войти в журнал "
                    "нельзя, и в списках выбора при создании расписания и программ он "
                    "больше не появится."
                ) % {"parts": ", ".join(parts)}
        self.fields["username"].help_text = _("Латиница, без пробелов")
        self.fields["last_name"].required = True
        self.fields["first_name"].required = True

    def clean_username(self):
        return self.cleaned_data["username"].strip()

    disabled = forms.BooleanField(
        label=_("Выключить учётную запись"),
        required=False,
        help_text=_(
            "Учётная запись остаётся в базе со всеми данными, но войти в журнал нельзя."
        ),
    )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = not self.cleaned_data.get("disabled", False)
        if commit:
            user.save()
            self.save_m2m()
        return user


class StaffMemberEditForm(TailwindFormMixin, AccountSafetyMixin, forms.ModelForm):
    """Карточка сотрудника УМО: роль, выключение учётной записи, сброс пароля."""

    class Meta:
        model = User
        fields = ("username", "last_name", "first_name", "middle_name", "role")
        error_messages = NAME_ERROR_MESSAGES

    disabled = forms.BooleanField(
        label=_("Выключить учётную запись"),
        required=False,
        help_text=_(
            "Учётная запись остаётся в базе со всеми данными, но войти в журнал нельзя."
        ),
    )

    def __init__(self, *args, viewer=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Кто правит карточку: своя учётная запись не выключается (см. AccountSafetyMixin).
        self.viewer = viewer
        if "role" in self.fields:
            self.fields["role"].choices = _role_choices(viewer, self.instance)
        instance = getattr(self, "instance", None)
        if instance and instance.pk:
            self.fields["disabled"].initial = not instance.is_active
        self.fields["username"].help_text = _("Латиница, без пробелов")
        self.fields["last_name"].required = True
        self.fields["first_name"].required = True

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = not self.cleaned_data.get("disabled", False)
        if commit:
            user.save()
            self.save_m2m()
        return user

class StyledSetPasswordForm(TailwindFormMixin, SetPasswordForm):
    """Новый пароль без старого — сброс пароля преподавателю."""


class StyledPasswordChangeForm(TailwindFormMixin, PasswordChangeForm):
    """Смена своего пароля: нужно знать текущий."""


class AcademicYearForm(TailwindFormMixin, forms.ModelForm):
    """Новый учебный год: выбирается только год, даты считаются сами."""

    start_year = forms.TypedChoiceField(
        label=_("Начало учебного года"),
        coerce=int,
        choices=(),
        help_text=_("Год идёт с 1 сентября по 31 августа — даты подставятся сами."),
    )

    class Meta:
        model = AcademicYear
        fields = ("start_year", "start_date", "end_date")
        widgets = {
            # Даты не показываем: они всегда 1 сентября — 31 августа.
            "start_date": forms.HiddenInput(),
            "end_date": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        number = today.year if today.month >= 9 else today.year - 1
        last = AcademicYear.objects.order_by("-start_date").first()
        if last and last.end_date:
            number = max(number, last.end_date.year)
        self.fields["start_year"].choices = [
            (value, f"{value}–{value + 1}") for value in range(number, number + 6)
        ]
        # Даты подставляет форма, поэтому обязательными их не делаем.
        for name in ("start_date", "end_date"):
            if name in self.fields:
                self.fields[name].required = False
        if self.instance and self.instance.pk:
            self.initial["start_year"] = self.instance.start_date.year
        elif not self.is_bound:
            self.initial["start_year"] = number
            self.initial["start_date"] = date(number, 9, 1)
            self.initial["end_date"] = date(number + 1, 8, 31)

    def clean(self):
        cleaned = super().clean()
        year = cleaned.get("start_year")
        if year in (None, ""):
            return cleaned
        year = int(year)
        start_date = date(year, 9, 1)
        end_date = date(year + 1, 8, 31)
        # Даты считаем сами — и подставляем их туда, откуда их ждёт представление.
        cleaned["start_date"] = start_date
        cleaned["end_date"] = end_date
        self.instance.start_date = start_date
        self.instance.end_date = end_date
        duplicate = AcademicYear.objects.filter(start_date=start_date)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError(
                _("Учебный год %(year)s–%(next_year)s уже есть в журнале.")
                % {"year": year, "next_year": year + 1}
            )
        return cleaned


class BrandingFileInput(forms.ClearableFileInput):
    """Файл оформления: предпросмотр, имя файла без папки и «Очистить».

    Стандартный ``ClearableFileInput`` рисует голую строку
    «На данный момент: branding/logo_XXXX.png □ Очистить»: ссылка на файл с
    путём хранения и чекбокс без оформления выглядят в форме случайным текстом.
    Здесь то же самое, но аккуратной карточкой в стиле остальных полей:
    квадратик 40 × 40, имя файла приглушённым текстом и «Очистить» рядом.
    """

    template_name = "journal/widgets/branding_file_input.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        widget = context["widget"]
        # format_value вернул FieldFile только у уже сохранённого файла; имя
        # показываем без папки (branding/), чтобы не пугать путём хранения.
        stored = widget.get("value")
        widget["file_name"] = os.path.basename(stored.name) if stored else ""
        return context


class SiteSettingsForm(TailwindFormMixin, forms.ModelForm):
    """Настройки журнала: меняет только главный администратор."""

    class Meta:
        model = SiteSettings
        fields = ("organization_name", "product_name", "short_logo", "logo", "favicon",
                  "login_hint", "remember_days", "session_hours")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Подсказку пишем в форме, а не в модели: иначе понадобилась бы миграция.
        self.fields["logo"].help_text = _(
            "PNG, JPG, WEBP или ICO, до 1 МБ. После выбора картинки откроется "
            "квадратная рамка: выберите область и масштаб — сохранится PNG 256 × 256."
        )
        # Оба поля загрузки рисуем одним виджетом с предпросмотром и «Очистить».
        # classes уже навесил TailwindFormMixin — переносим их в новый виджет.
        for name in ("logo", "favicon"):
            field = self.fields[name]
            field.widget = BrandingFileInput(attrs=dict(field.widget.attrs))
        # Старые имена файлов запоминаем до проверки формы: _post_clean положит
        # в instance новые файлы, а прежние нужно удалить с диска при замене.
        self._old_logo = self.instance.logo.name if self.instance.logo else ""
        self._old_favicon = self.instance.favicon.name if self.instance.favicon else ""

    def clean_logo(self):
        value = self._check_image(self.cleaned_data.get("logo"), _("Логотип"))
        return self._crop_logo(value)

    def clean_favicon(self):
        value = self._check_image(self.cleaned_data.get("favicon"), _("Фавикон"))
        if not isinstance(value, UploadedFile):
            return value
        # Предсказуемое имя: значок вкладки всегда лежит как branding/favicon.png
        # (или .ico/.jpg — по исходному формату), а не со случайным суффиксом.
        # Так его проще найти в media и он не плодит копии при каждой замене.
        extension = os.path.splitext(value.name)[1].lower() or ".png"
        value.name = f"favicon{extension}"
        return value

    @staticmethod
    def _is_new_file(value):
        """Пришёл ли в cleaned_data новый файл (а не уже сохранённый)."""
        return isinstance(value, (UploadedFile, ContentFile))

    def save(self, commit=True):
        """Сохранить настройки и убрать с диска заменённые картинки.

        Фавикон удаляем ДО сохранения: тогда новому файлу достаётся ровно имя
        ``branding/favicon.<ext>``, без суффикса, который Django добавляет при
        совпадении имён. У логотипа имя остаётся уникальным (случайный суффикс
        защищает от старой картинки в кэше браузера), поэтому старый файл
        удаляем уже после успешного сохранения.
        """
        favicon_value = self.cleaned_data.get("favicon")
        logo_value = self.cleaned_data.get("logo")
        storage = SiteSettings._meta.get_field("favicon").storage
        if commit and self._old_favicon and (
            self._is_new_file(favicon_value) or favicon_value is False
        ):
            storage.delete(self._old_favicon)
            self._old_favicon = ""
        instance = super().save(commit)
        if commit and self._old_logo and (
            self._is_new_file(logo_value) or logo_value is False
        ):
            if self._old_logo != instance.logo.name:
                storage.delete(self._old_logo)
            self._old_logo = ""
        return instance

    def _crop_logo(self, value):
        """Обрезать новую картинку до квадрата 256 × 256 (логотип квадратный).

        Уже сохранённый логотип не трогаем: когда файл не перезаливают,
        ``cleaned_data`` содержит ``FieldFile``, а не загруженный файл. Координаты
        области приходят из браузера (``logo_crop_*``); если их нет — сервер сам
        обрежет картинку по центру, поэтому загрузка работает и без JavaScript.
        """
        if not isinstance(value, UploadedFile):
            return value
        # Формат проверяем по исходному имени: после обрезки файл всегда PNG, и
        # проверка расширения у модели увидела бы уже «logo.png» — тогда GIF
        # проходил бы. Запрет SVG/GIF и список разрешённых форматов остались прежними.
        FileExtensionValidator(IMAGE_EXTENSIONS)(value)
        from .branding import LogoProcessingError, square_logo

        try:
            return square_logo(value, getattr(self, "data", None))
        except LogoProcessingError:
            raise forms.ValidationError(
                _("Логотип: не удалось обрезать картинку. Попробуйте другой файл.")
            ) from None

    def _check_image(self, value, title):
        """Разрешаем только настоящие изображения подходящего размера."""
        if not value:
            return value
        if getattr(value, "size", 0) > 1024 * 1024:
            raise forms.ValidationError(
                _("%(title)s: файл больше 1 МБ.") % {"title": title}
            )
        try:
            from PIL import Image
        except ImportError:  # Pillow не установлен — проверит Django
            return value
        try:
            image = Image.open(value)
            image.verify()
        except Exception:
            raise forms.ValidationError(
                _(
                    "%(title)s: это не похоже на изображение. "
                    "Нужен PNG, JPG, WEBP или ICO."
                )
                % {"title": title}
            )
        finally:
            if hasattr(value, "seek"):
                value.seek(0)
        return value

    def clean_remember_days(self):
        value = self.cleaned_data.get("remember_days") or 30
        return max(1, min(int(value), 365))

    def clean_session_hours(self):
        value = self.cleaned_data.get("session_hours") or 12
        return max(1, min(int(value), 720))


class MailSettingsForm(TailwindFormMixin, forms.ModelForm):
    """Настройки SMTP и уведомлений. Пароль вводится заново и шифруется."""

    password = forms.CharField(
        label=_("Пароль SMTP"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Оставьте пустым, чтобы не менять сохранённый пароль"),
    )

    class Meta:
        model = MailSettings
        fields = (
            "host",
            "port",
            "username",
            "use_tls",
            "use_ssl",
            "from_email",
            "recipients",
            "notify_logins",
            "notify_failed_logins",
            "notify_ip_blocked",
            "notify_backup",
            "notify_accounts",
        )
        widgets = {
            "recipients": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["host"].widget.attrs["placeholder"] = "smtp.yandex.ru"
        self.fields["from_email"].widget.attrs["placeholder"] = "journal@example.com"
        self.fields["recipients"].help_text = _("По одному адресу в строке")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("use_tls") and cleaned.get("use_ssl"):
            raise forms.ValidationError(
                _("Выберите что-то одно: либо STARTTLS, либо SSL.")
            )
        recipients = cleaned.get("recipients") or ""
        for line in recipients.replace(",", "\n").splitlines():
            line = line.strip()
            if line and "@" not in line:
                self.add_error(
                    "recipients",
                    _("«%(line)s» не похоже на адрес электронной почты.")
                    % {"line": line},
                )
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            instance.set_password(password)
        if commit:
            instance.save()
        return instance


class IpRuleForm(TailwindFormMixin, forms.ModelForm):
    """Правило доступа по IP: адрес или подсеть."""

    class Meta:
        model = IpRule
        fields = ("address", "kind", "comment")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["address"].widget.attrs["placeholder"] = _(
            "203.0.113.10 или 203.0.113.0/24"
        )

    def clean_address(self):
        value = (self.cleaned_data["address"] or "").strip()
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as error:
            raise forms.ValidationError(
                _("Это не похоже на IP-адрес или подсеть: %(error)s")
                % {"error": error}
            ) from error
        return value


class RestoreBackupForm(TailwindFormMixin, forms.Form):
    """Загрузка файла резервной копии и выбор режима восстановления."""

    MODES = (
        ("merge", _("Добавить и обновить: текущие данные остаются, из копии добавляются новые")),
        ("replace", _("Полностью заменить: данные журнала будут удалены и восстановлены из копии")),
    )

    file = forms.FileField(
        label=_("Файл резервной копии (.json или .json.enc)"),
        widget=forms.ClearableFileInput(
            attrs={"accept": ".json,.enc,application/json,application/octet-stream"}
        ),
    )
    passphrase = forms.CharField(
        label=_("Парольная фраза (для зашифрованной копии)"),
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text=_("Заполните, если копия скачивалась с шифрованием"),
    )
    mode = forms.ChoiceField(
        label=_("Режим восстановления"),
        choices=MODES,
        initial="merge",
        widget=forms.RadioSelect,
    )
    confirm = forms.BooleanField(
        label=_("Подтверждаю полную замену данных"),
        required=False,
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mode") == "replace" and not cleaned.get("confirm"):
            raise forms.ValidationError(
                _("Для полной замены данных отметьте галочку подтверждения.")
            )
        return cleaned


# --- Первоначальная настройка (первый запуск на чистой базе) -------------------
class SetupAdminForm(TailwindFormMixin, UserCreationForm):
    """Главный администратор журнала: первая учётная запись на чистом журнале.

    Проверки штатные: логин — только латиницей (ASCIIUsernameValidator у модели),
    имя и фамилия обязательны, пароль проверяется теми же правилами Django, что и
    при обычном создании пользователя (``validate_password``: минимум 8 символов,
    не только цифры, не слишком распространённый и не похожий на логин).
    """

    #: Понятное русское сообщение вместо стандартного «два поля не совпадают».
    error_messages = {"password_mismatch": _("Пароли не совпадают.")}

    class Meta:
        model = User
        fields = ("username", "last_name", "first_name", "middle_name", "email")
        labels = {
            "username": _("Логин"),
            "last_name": _("Фамилия"),
            "first_name": _("Имя"),
            "middle_name": _("Отчество"),
            "email": _("Почта"),
        }
        error_messages = NAME_ERROR_MESSAGES

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = _(
            "Латиница, без пробелов. Например: admin. Этим логином вы будете входить."
        )
        self.fields["username"].widget.attrs["autofocus"] = True
        self.fields["last_name"].required = True
        self.fields["first_name"].required = True
        # Отчество и почту заполняют по желанию.
        self.fields["middle_name"].required = False
        self.fields["email"].required = False
        self.fields["email"].help_text = _(
            "Необязательно. Нужна для уведомлений журнала."
        )
        self.fields["password1"].label = _("Пароль")
        self.fields["password2"].label = _("Повтор пароля")

    def save(self, commit=True):
        user = super().save(commit=False)
        # Первая учётная запись — владелец журнала: полный доступ ко всем разделам.
        user.role = User.Role.SUPERADMIN
        user.is_superuser = True
        user.is_staff = True
        user.is_active = True
        if commit:
            user.save()
        return user


class OrganizationNameForm(TailwindFormMixin, forms.ModelForm):
    """Одно поле «Название организации» — правка со страницы «О программе».

    Форма меняет только название и ничего больше: остальные настройки журнала
    остаются как были, даже если администратор сохранит форму с ошибкой.
    """

    class Meta:
        model = SiteSettings
        fields = ("organization_name",)
        error_messages = {
            "organization_name": {
                "required": _("Введите название организации."),
                "max_length": _("Слишком длинное название: максимум 200 символов."),
            }
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization_name"].label = _("Название организации")
        self.fields["organization_name"].widget.attrs.update(
            {"placeholder": _("Например: Образовательная организация")}
        )

    def clean_organization_name(self):
        """Название не может быть пустым или из одних пробелов."""
        value = (self.cleaned_data.get("organization_name") or "").strip()
        if not value:
            raise forms.ValidationError(_("Введите название организации."))
        return value


class SetupSiteSettingsForm(SiteSettingsForm):
    """Внешний вид журнала на первом запуске: название, логотип и значок.

    Наследуется от формы кастомизации: проверки картинок (формат и размер до 1 МБ)
    те же самые, чтобы не заводить второй механизм оформления.
    """

    class Meta(SiteSettingsForm.Meta):
        fields = ("product_name", "organization_name", "short_logo", "logo", "favicon")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product_name"].label = _("Название журнала")
        self.fields["product_name"].help_text = _(
            "Показывается в заголовке, на странице входа и в подвале."
        )
        self.fields["organization_name"].label = _("Название организации")
        self.fields["short_logo"].required = False
        self.fields["short_logo"].help_text = _(
            "1–4 знака, если логотип загружать не хотите. Например: ЭЖ."
        )

    def clean_short_logo(self):
        """Пустой короткий знак заменяем значением по умолчанию (как у модели)."""
        return (self.cleaned_data.get("short_logo") or "").strip() or "ЭЖ"
