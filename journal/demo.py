"""Демо-режим: готовые данные и вход без пароля для быстрой проверки функционала.

Набор намеренно большой и разнообразный: несколько адресов, программ, групп,
преподавателей, отметки посещаемости за четыре недели, темы занятий, конкурсы
детей и повышение квалификации педагогов. Всё считается детерминированно —
случайных чисел нет, поэтому демонстрация всегда выглядит одинаково и создаётся
за пару секунд.

Единственное, что зависит от даты запуска, — «сегодня»: отметки идут назад от
текущего дня, а разовые занятия привязаны к текущей неделе, чтобы их было видно
в журнале по неделям и в кабинете преподавателя сразу после входа в демо-режим.
"""
from datetime import date, time, timedelta

from django.db import transaction

from . import demo_router, periods as periods_tools
from .models import (
    Attendance,
    Branch,
    ContestResult,
    Group,
    LessonTopic,
    Room,
    Schedule,
    Student,
    Subject,
    TeacherDevelopment,
    User,
)

DEMO_METHODIST = "demo_methodist"
DEMO_TEACHER = "demo_teacher"
DEMO_TEACHER_SECOND = "demo_teacher_2"
DEMO_TEACHER_THIRD = "demo_teacher_3"
DEMO_TEACHER_FOURTH = "demo_teacher_4"
DEMO_TEACHER_FIFTH = "demo_teacher_5"
DEMO_TEACHER_SIXTH = "demo_teacher_6"
DEMO_ADMIN = "demo_admin"

#: Версия набора демо-данных. Меняйте её, когда набор меняется настолько, что
#: старые данные в демо-базе надо пересобрать: ``demo_data_current`` тогда
#: перестанет считать такой набор свежим.
#: 1 — ученики, программы и группы; 2 — добавлены разовые занятия, темы, конкурсы
#: и повышение квалификации (проверяем по наличию разовых занятий).
DEMO_DATA_VERSION = 2

#: Сколько дней назад от сегодняшнего дня расставляются отметки посещаемости.
DEMO_DAYS_BACK = 28

# --- Ученики ----------------------------------------------------------------
# Фамилии и имена выбраны так, чтобы сочетания не повторялись.
DEMO_STUDENTS = [
    ("Абрамов", "Илья", "Сергеевич", "2010-03-14", "ЕО"),
    ("Белова", "Мария", "Андреевна", "2010-07-02", "ЕН"),
    ("Ветров", "Артём", "Павлович", "2009-11-21", "Н"),
    ("Гусева", "София", "Игоревна", "2011-01-30", "ЕО"),
    ("Дорохов", "Максим", "Олегович", "2010-05-19", "Н"),
    ("Ершова", "Полина", "Дмитриевна", "2010-09-08", "ЕН"),
    ("Жуков", "Никита", "Романович", "2009-12-05", "ЕО"),
    ("Зимина", "Алиса", "Максимовна", "2011-04-17", "Н"),
    ("Королёв", "Егор", "Витальевич", "2010-08-23", "ЕН"),
    ("Лебедева", "Дарья", "Сергеевна", "2010-02-11", "ЕО"),
    ("Мельников", "Тимофей", "Андреевич", "2011-06-27", "Н"),
    ("Носова", "Ева", "Алексеевна", "2009-09-15", "ЕН"),
    ("Орлов", "Кирилл", "Денисович", "2012-01-08", "ЕО"),
    ("Панова", "Виктория", "Романовна", "2010-12-19", "Н"),
    ("Родионов", "Лев", "Игоревич", "2011-10-03", "ЕН"),
    ("Соколова", "Анна", "Павловна", "2010-04-25", "ЕО"),
    ("Титов", "Савелий", "Максимович", "2009-07-11", "Н"),
    ("Устинова", "Ксения", "Олеговна", "2011-03-02", "ЕН"),
    ("Фомин", "Марк", "Сергеевич", "2010-11-28", "ЕО"),
    ("Хромова", "Варвара", "Дмитриевна", "2012-05-06", "Н"),
    ("Царёв", "Даниил", "Артёмович", "2010-06-14", "ЕН"),
    ("Чернова", "Милана", "Ильинична", "2011-08-21", "ЕО"),
    ("Шаповалов", "Глеб", "Николаевич", "2009-10-09", "Н"),
    ("Щербакова", "Елизавета", "Тимофеевна", "2010-01-23", "ЕН"),
    ("Юдин", "Роман", "Владимирович", "2011-11-16", "ЕО"),
    ("Яковлева", "Пелагея", "Егоровна", "2010-09-30", "Н"),
    ("Быков", "Арсений", "Петрович", "2012-02-18", "ЕН"),
    ("Волкова", "Таисия", "Семёновна", "2011-07-05", "ЕО"),
    ("Григорьев", "Степан", "Юрьевич", "2010-10-22", "Н"),
    ("Дементьева", "Аглая", "Львовна", "2009-08-13", "ЕН"),
    ("Ефимов", "Матвей", "Борисович", "2011-02-27", "ЕО"),
    ("Жданова", "Дарья", "Олеговна", "2010-05-31", "Н"),
    ("Зайцев", "Иван", "Фёдорович", "2012-09-04", "ЕН"),
    ("Игнатьева", "Софья", "Марковна", "2010-12-07", "ЕО"),
    ("Кузнецов", "Платон", "Глебович", "2011-04-11", "Н"),
    ("Ларина", "Алёна", "Викторовна", "2010-03-26", "ЕН"),
    ("Миронов", "Фёдор", "Анатольевич", "2009-06-02", "ЕО"),
    ("Никитина", "Ульяна", "Захаровна", "2011-09-19", "Н"),
    ("Осипов", "Ярослав", "Трофимович", "2010-07-24", "ЕН"),
    ("Петухова", "Арина", "Валерьевна", "2012-03-12", "ЕО"),
    ("Румянцев", "Мирон", "Савельевич", "2010-08-05", "Н"),
    ("Савельева", "Злата", "Григорьевна", "2011-12-21", "ЕН"),
    ("Трофимов", "Елисей", "Игнатьевич", "2010-02-09", "ЕО"),
    ("Фёдорова", "Агата", "Родионовна", "2009-05-17", "Н"),
    ("Цветков", "Семён", "Аркадьевич", "2011-01-14", "ЕН"),
    ("Черкасова", "Лидия", "Матвеевна", "2010-10-28", "ЕО"),
    ("Шилов", "Константин", "Ефимович", "2012-07-30", "Н"),
    ("Щукина", "Вера", "Степановна", "2010-04-03", "ЕН"),
    ("Эрлих", "Тимур", "Давидович", "2011-05-24", "ЕО"),
    ("Юрьева", "Ника", "Платоновна", "2010-11-11", "Н"),
]

# --- Педагоги и другие сотрудники -------------------------------------------
# (логин, фамилия, имя, отчество, роль). Логин demo_teacher_2 оставлен прежним:
# на нём завязан вход в демо-режим как преподаватель и проверки готовности базы.
DEMO_STAFF = (
    (DEMO_METHODIST, "Соколова", "Ольга", "Николаевна", User.Role.METHODIST),
    (DEMO_ADMIN, "Ковалёв", "Дмитрий", "Андреевич", User.Role.ADMIN),
    (DEMO_TEACHER, "Иванов", "Пётр", "Сергеевич", User.Role.TEACHER),
    (DEMO_TEACHER_SECOND, "Сидорова", "Анна", "Викторовна", User.Role.TEACHER),
    (DEMO_TEACHER_THIRD, "Морозова", "Елена", "Павловна", User.Role.TEACHER),
    (DEMO_TEACHER_FOURTH, "Кузнецов", "Андрей", "Игоревич", User.Role.TEACHER),
    (DEMO_TEACHER_FIFTH, "Соколов", "Михаил", "Юрьевич", User.Role.TEACHER),
    (DEMO_TEACHER_SIXTH, "Крылова", "Наталья", "Сергеевна", User.Role.TEACHER),
)

# --- Адреса и кабинеты -------------------------------------------------------
# (адрес, порядок, кабинеты). Первые два совпадают со стандартными филиалами
# журнала, третий добавлен только для демонстрации.
DEMO_BRANCHES = (
    (
        "ул. Примерная, 1",
        0,
        (
            "Учебный класс (2 этаж)",
            "Класс информатики и математики (1 этаж)",
            "Лаборатория физики",
            "Конференц-зал",
        ),
    ),
    (
        "ул. Вторая, 10",
        1,
        ("№13", "№1", "№2"),
    ),
    (
        "ул. Третья, 20",
        2,
        ("Мастерская робототехники", "Химическая лаборатория", "Арт-студия"),
    ),
)

# --- Программы и группы ------------------------------------------------------
# (название программы, логин основного преподавателя, подменяющие, адрес, состав).
# Состав: (название группы, (индексы учеников из DEMO_STUDENTS)).
# Первые три названия программ и их группы — из прошлых версий демо-набора:
# их не переименовывали, чтобы демонстрация выглядела привычно.
DEMO_SUBJECTS = [
    (
        "Математика", DEMO_TEACHER, (DEMO_TEACHER_SECOND, DEMO_TEACHER_FOURTH), "ул. Примерная, 1",
        [
            ("Поток 1", (0, 3, 6, 9, 12, 15, 18, 21, 24, 27)),
            ("Поток 2", (30, 33, 36, 39, 42, 45, 48, 1, 4, 7, 10, 13)),
        ],
    ),
    (
        "Физика", DEMO_TEACHER_SECOND, (DEMO_TEACHER,), "ул. Примерная, 1",
        [
            ("Смена июль", (16, 19, 22, 25, 28, 31, 34, 37, 40, 43, 46, 49, 2)),
        ],
    ),
    (
        "Русский язык", DEMO_TEACHER, (DEMO_TEACHER_SECOND,), "ул. Вторая, 10",
        [
            ("Группа выходного дня", (5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35, 38, 41, 44)),
            ("Группа буднего дня", (1, 7, 13, 19, 25, 31, 37, 43, 46, 0, 6, 12)),
        ],
    ),
    (
        "Робототехника и программирование", DEMO_TEACHER_THIRD, (), "ул. Третья, 20",
        [
            ("Юниоры", (1, 5, 9, 13, 17, 21, 25, 29)),
            ("Профи", (33, 37, 41, 45, 49, 3, 7, 11, 15, 19)),
        ],
    ),
    (
        "Английский язык", DEMO_TEACHER_FOURTH, (DEMO_TEACHER_FIFTH,), "ул. Вторая, 10",
        [
            ("Начальный уровень", (0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48)),
            ("Продвинутый уровень", (2, 6, 10, 14, 18, 22, 26, 30, 34)),
        ],
    ),
    (
        "Химия: практикум", DEMO_TEACHER_FIFTH, (DEMO_TEACHER_THIRD,), "ул. Третья, 20",
        [
            ("Лаборатория 1", (3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43, 47)),
        ],
    ),
    (
        "Изобразительное искусство", DEMO_TEACHER_SIXTH, (), "ул. Третья, 20",
        [
            ("Студия", (2, 6, 10, 14, 18, 22, 26, 30, 34, 38, 42, 46, 0, 4, 8)),
        ],
    ),
]

# --- Расписание --------------------------------------------------------------
# (программа, группа, день недели, начало, окончание, адрес, кабинет, кто ведёт).
# Восьмой элемент — логин преподавателя: None — основной у программы, другой
# логин — подмена или второй преподаватель программы.
# lesson_date добавляется отдельно: DEMO_ONE_OFF — разовые занятия.
DEMO_SCHEDULE = [
    # ул. Примерная, 1
    ("Математика", "Поток 1", 0, "10:00", "11:30", "ул. Примерная, 1", "Учебный класс (2 этаж)", None),
    ("Математика", "Поток 1", 3, "10:00", "11:30", "ул. Примерная, 1", "Учебный класс (2 этаж)", None),
    ("Математика", "Поток 1", 5, "10:00", "11:30", "ул. Примерная, 1", "Лаборатория физики", DEMO_TEACHER_SECOND),
    ("Математика", "Поток 2", 0, "15:00", "16:30", "ул. Примерная, 1", "Учебный класс (2 этаж)", None),
    ("Математика", "Поток 2", 2, "15:00", "16:30", "ул. Примерная, 1", "Учебный класс (2 этаж)", DEMO_TEACHER_SECOND),
    ("Физика", "Смена июль", 1, "09:00", "10:30", "ул. Примерная, 1", "Лаборатория физики", None),
    ("Физика", "Смена июль", 3, "09:00", "10:30", "ул. Примерная, 1", "Лаборатория физики", None),
    ("Физика", "Смена июль", 5, "12:00", "13:30", "ул. Примерная, 1", "Лаборатория физики", None),
    # ул. Вторая, 10
    ("Русский язык", "Группа выходного дня", 3, "16:00", "17:30", "ул. Вторая, 10", "№1", None),
    ("Русский язык", "Группа выходного дня", 5, "11:00", "12:30", "ул. Вторая, 10", "№2", DEMO_TEACHER_SECOND),
    ("Русский язык", "Группа буднего дня", 1, "14:40", "16:10", "ул. Вторая, 10", "№2", None),
    ("Русский язык", "Группа буднего дня", 4, "14:40", "16:10", "ул. Вторая, 10", "№2", None),
    ("Английский язык", "Начальный уровень", 1, "16:00", "17:30", "ул. Вторая, 10", "№13", None),
    ("Английский язык", "Начальный уровень", 4, "16:00", "17:30", "ул. Вторая, 10", "№13", None),
    ("Английский язык", "Продвинутый уровень", 1, "17:45", "19:15", "ул. Вторая, 10", "№13", DEMO_TEACHER_FIFTH),
    ("Английский язык", "Продвинутый уровень", 4, "17:45", "19:15", "ул. Вторая, 10", "№13", None),
    # ул. Третья, 20
    ("Робототехника и программирование", "Юниоры", 0, "16:00", "17:30", "ул. Третья, 20", "Мастерская робототехники", None),
    ("Робототехника и программирование", "Юниоры", 2, "16:00", "17:30", "ул. Третья, 20", "Мастерская робототехники", None),
    ("Робототехника и программирование", "Профи", 2, "17:45", "19:15", "ул. Третья, 20", "Мастерская робототехники", None),
    ("Робототехника и программирование", "Профи", 4, "17:45", "19:15", "ул. Третья, 20", "Мастерская робототехники", None),
    ("Химия: практикум", "Лаборатория 1", 1, "15:00", "16:30", "ул. Третья, 20", "Химическая лаборатория", None),
    ("Химия: практикум", "Лаборатория 1", 3, "15:00", "16:30", "ул. Третья, 20", "Химическая лаборатория", None),
    ("Изобразительное искусство", "Студия", 2, "14:00", "15:30", "ул. Третья, 20", "Арт-студия", None),
]

#: Начало недели, к которой привязываются разовые занятия: 0 — понедельник.
DEMO_WEEK_ANCHOR = (10, 6, 5)

#: Разовые занятия: (программа, группа, сдвиг даты в днях от понедельника текущей
#: недели, начало, окончание, адрес, кабинет, кто ведёт, тема).
#: Ведёт подменяющий: он закреплён за программой в списке «Преподаватели программы»
#: (см. DEMO_SUBJECTS) — занятие может вести только преподаватель программы.
DEMO_ONE_OFF = [
    (
        "Математика", "Поток 1", DEMO_WEEK_ANCHOR[2], "17:00", "18:30",
        "ул. Примерная, 1", "Конференц-зал", DEMO_TEACHER_FOURTH,
        "Открытый урок: разбор олимпиадных задач",
    ),
    (
        "Химия: практикум", "Лаборатория 1", DEMO_WEEK_ANCHOR[1], "14:30", "16:00",
        "ул. Третья, 20", "Химическая лаборатория", DEMO_TEACHER_THIRD,
        "Практикум «Качественный анализ»",
    ),
    (
        "Изобразительное искусство", "Студия", DEMO_WEEK_ANCHOR[0] + 6, "12:30", "14:00",
        "ул. Третья, 20", "Арт-студия", None, "Выездной пленэр: городской пейзаж",
    ),
]

# --- Темы занятий ------------------------------------------------------------
# По каждой программе — свой список тем: в журнале тем видно, что программы разные.
DEMO_TOPIC_BANK = {
    "Математика": (
        "Действия с рациональными числами",
        "Линейные уравнения с одной переменной",
        "Системы линейных уравнений",
        "Координатная плоскость и графики",
        "Одночлены и многочлены",
        "Формулы сокращённого умножения",
        "Разложение многочленов на множители",
        "Квадратные уравнения",
        "Теорема Виета",
        "Неравенства и их системы",
        "Текстовые задачи на движение",
        "Элементы теории вероятностей",
        "Подготовка к олимпиаде: комбинаторика",
        "Итоговое повторение курса",
    ),
    "Физика": (
        "Механическое движение. Относительность движения",
        "Скорость и ускорение",
        "Законы Ньютона",
        "Сила тяжести и вес тела",
        "Лабораторная работа: измерение жёсткости пружины",
        "Импульс тела. Закон сохранения импульса",
        "Механическая работа и мощность",
        "Кинетическая и потенциальная энергия",
        "Простые механизмы. КПД",
        "Давление твёрдых тел и жидкостей",
        "Сила Архимеда. Плавание тел",
        "Решение задач на законы сохранения",
    ),
    "Русский язык": (
        "Повторение орфографии: безударные гласные",
        "Правописание приставок",
        "Сложные слова и дефис в наречиях",
        "Обособленные определения",
        "Обособленные обстоятельства",
        "Однородные члены предложения",
        "Сложносочинённое предложение",
        "Сложноподчинённое предложение",
        "Виды подчинительной связи",
        "Текст: тема и основная мысль",
        "Сочинение-рассуждение по прочитанному тексту",
        "Изложение с элементами сочинения",
    ),
    "Робототехника и программирование": (
        "Знакомство с контроллером и средой разработки",
        "Цифровые и аналоговые входы: кнопки и датчики",
        "ШИМ: управление яркостью и скоростью",
        "Сервоприводы и механика поворота",
        "Датчик расстояния: объезд препятствий",
        "Сборка шасси и калибровка моторов",
        "Циклы и условия в программе робота",
        "Датчик линии: движение по трассе",
        "Проектная работа: робот-манипулятор",
        "Защита проектов: презентация модели",
    ),
    "Английский язык": (
        "Present Simple и Present Continuous",
        "Past Simple: правильные и неправильные глаголы",
        "Степени сравнения прилагательных",
        "Модальные глаголы can, must, should",
        "Мои увлечения: монолог по теме",
        "Present Perfect: опыт и результат",
        "Условные предложения I и II типа",
        "Аудирование: диалоги в аэропорту",
        "Письмо другу: структура неформального письма",
        "Подготовка к устной части экзамена",
    ),
    "Химия: практикум": (
        "Правила безопасности в химической лаборатории",
        "Классы неорганических соединений",
        "Растворы и массовая доля вещества",
        "Лабораторная работа: получение кислорода",
        "Кислоты, основания и индикаторы",
        "Реакции ионного обмена",
        "Окислительно-восстановительные реакции",
        "Качественный анализ катионов",
        "Решение расчётных задач по уравнениям",
    ),
    "Изобразительное искусство": (
        "Основы композиции: центр и равновесие",
        "Линейная перспектива",
        "Цветовой круг и тёплые-холодные цвета",
        "Натюрморт: построение и светотень",
        "Портрет: пропорции лица",
        "Пейзаж по памяти и с натуры",
        "Графика: штрих и линия",
        "Работа с гуашью: фактура",
        "Эскиз плаката",
        "Итоговая выставка работ",
    ),
}

# --- Конкурсы детей ----------------------------------------------------------
# (индекс ученика, сколько дней назад от сегодняшнего дня, мероприятие, результат,
#  направление, кто внёс). Отрицательное число дней — прошедшая дата.
DEMO_CONTESTS = [
    (0, 35, "Городская олимпиада по математике", "winner", "Математика", DEMO_TEACHER),
    (3, 35, "Городская олимпиада по математике", "prize", "Математика", DEMO_TEACHER),
    (12, 35, "Городская олимпиада по математике", "participant", "Математика", DEMO_TEACHER),
    (16, 28, "Конкурс проектных работ «Старт»", "prize", "Физика", DEMO_TEACHER_SECOND),
    (31, 28, "Конкурс проектных работ «Старт»", "participant", "Физика", DEMO_TEACHER_SECOND),
    (5, 21, "Олимпиада по русскому языку «Слово»", "winner", "Русский язык", DEMO_TEACHER),
    (20, 21, "Олимпиада по русскому языку «Слово»", "participant", "Русский язык", DEMO_TEACHER),
    (1, 14, "Региональный турнир по робототехнике", "winner", "Робототехника", DEMO_TEACHER_THIRD),
    (33, 14, "Региональный турнир по робототехнике", "prize", "Робототехника", DEMO_TEACHER_THIRD),
    (13, 14, "Региональный турнир по робототехнике", "participant", "Робототехника", DEMO_TEACHER_THIRD),
    (2, 7, "Конкурс чтецов на английском языке", "prize", "Английский язык", DEMO_TEACHER_FOURTH),
    (30, 7, "Конкурс чтецов на английском языке", "participant", "Английский язык", DEMO_TEACHER_FOURTH),
    (7, 4, "Выставка детского рисунка «Зимний город»", "winner", "Изобразительное искусство", DEMO_TEACHER_SIXTH),
    (8, 4, "Выставка детского рисунка «Зимний город»", "prize", "Изобразительное искусство", DEMO_TEACHER_SIXTH),
    (22, 4, "Выставка детского рисунка «Зимний город»", "participant", "Изобразительное искусство", DEMO_TEACHER_SIXTH),
]

# --- Повышение квалификации педагогов ----------------------------------------
# (логин педагога, сколько дней назад, вид, программа, организация, часов, результат).
DEMO_DEVELOPMENT = [
    (DEMO_TEACHER, 300, "course", "Методика преподавания математики в основной школе",
     "Институт развития образования", 36, "Удостоверение"),
    (DEMO_TEACHER_SECOND, 265, "course", "Современные лабораторные практикумы по физике",
     "Институт развития образования", 24, "Удостоверение"),
    (DEMO_TEACHER_THIRD, 240, "course", "Основы образовательной робототехники",
     "Академия цифровых технологий", 72, "Сертификат"),
    (DEMO_TEACHER_FOURTH, 210, "course", "Коммуникативная методика обучения английскому языку",
     "Педагогический университет", 48, "Диплом о переподготовке"),
    (DEMO_TEACHER_FIFTH, 185, "course", "Проектная деятельность на уроках химии",
     "Институт развития образования", 32, "Удостоверение"),
    (DEMO_TEACHER_SIXTH, 150, "other", "Летняя школа педагогического мастерства",
     "Дом детского творчества", 18, "Благодарственное письмо"),
    (DEMO_TEACHER, 120, "contest", "Конкурс педагогического мастерства «Учитель года»",
     "Министерство образования", None, "Призёр городского этапа"),
    (DEMO_TEACHER_THIRD, 95, "contest", "Фестиваль педагогических идей «Открытый урок»",
     "Центр развития образования", None, "Победитель в номинации"),
    (DEMO_TEACHER_SECOND, 60, "course", "Оказание первой помощи в образовательной организации",
     "Учебно-методический центр", 16, "Сертификат"),
    (DEMO_TEACHER_FOURTH, 30, "other", "Стажировка в международной языковой школе",
     "Language Centre", 40, "Свидетельство о стажировке"),
]

#: Как расставлять отметки: в основном «присутствовал», но с заметной долей
#: пропусков и опозданий, чтобы статистика и выгрузки были наглядными.
STATUS_PATTERN = ("P", "P", "P", "A", "P", "L", "P", "A", "P", "P", "L", "P")
LESSON_DAYS_BACK = 28
ONE_OFF_DAYS_BACK = 7
#: Сколько последних недель расставлять темы занятий.
TOPIC_WEEKS = 4
#: Каждое второе занятие остаётся без темы — так видно и заполненные, и пустые.
TOPIC_STEP = 2
#: Часть тем помечена как практикум: в журнале тем сразу видно, что они разные.
LESSON_TOPIC_PREFIX = "Практикум: "

DEMO_USERNAMES = tuple(item[0] for item in DEMO_STAFF)


def demo_users():
    return {
        "methodist": DEMO_METHODIST,
        "teacher": DEMO_TEACHER,
        "teacher_second": DEMO_TEACHER_SECOND,
    }


def demo_dataset_summary():
    """Сколько чего должно получиться: удобно и для справки, и для проверок."""
    groups = sum(len(item[4]) for item in DEMO_SUBJECTS)
    return {
        "Ученики": len(DEMO_STUDENTS),
        "Педагоги": sum(
            1 for item in DEMO_STAFF if item[4] == User.Role.TEACHER
        ),
        "Сотрудники УМО": sum(
            1 for item in DEMO_STAFF if item[4] != User.Role.TEACHER
        ),
        "Адреса": len(DEMO_BRANCHES),
        "Программы": len(DEMO_SUBJECTS),
        "Группы": groups,
        "Занятия по дням недели": len(DEMO_SCHEDULE),
        "Разовые занятия": len(DEMO_ONE_OFF),
        "Конкурсы": len(DEMO_CONTESTS),
        "Повышение квалификации": len(DEMO_DEVELOPMENT),
    }


def demo_database_ready():
    """Доступна ли отдельная демонстрационная база (таблицы созданы)."""
    from django.db import connections

    try:
        connection = connections[demo_router.DEMO_ALIAS]
        connection.ensure_connection()
    except Exception:  # noqa: BLE001 - демо может быть просто не настроено
        return False
    try:
        return User.objects.using(demo_router.DEMO_ALIAS).exists()
    except Exception:  # noqa: BLE001 - таблиц ещё нет
        return False


def demo_database_counts():
    """Сколько данных сейчас в демонстрационной базе."""
    alias = demo_router.DEMO_ALIAS
    if not demo_database_ready():
        return None
    counts = {}
    for label, queryset in (
        ("Ученики", Student.objects.using(alias)),
        ("Программы", Subject.objects.using(alias)),
        ("Группы", Group.objects.using(alias)),
        ("Занятия", Schedule.objects.using(alias)),
        # Разовые занятия — те, у которых есть дата: они проходят один раз.
        ("Разовые занятия", Schedule.objects.using(alias).filter(lesson_date__isnull=False)),
        ("Отметки", Attendance.objects.using(alias)),
        ("Темы", LessonTopic.objects.using(alias)),
        ("Конкурсы", ContestResult.objects.using(alias)),
        ("Педагоги", TeacherDevelopment.objects.using(alias)),
        ("Пользователи", User.objects.using(alias)),
    ):
        try:
            counts[label] = queryset.count()
        except Exception:  # noqa: BLE001
            counts[label] = 0
    return counts


def wipe_demo_data():
    """Полностью очищает демонстрационную базу (рабочая не затрагивается)."""
    alias = demo_router.DEMO_ALIAS
    removed = {}
    with demo_router.activate():
        for label, model in (
            ("Отметки", Attendance),
            ("Темы", LessonTopic),
            ("Конкурсы", ContestResult),
            ("Педагоги", TeacherDevelopment),
            ("Занятия", Schedule),
            ("Группы", Group),
            ("Программы", Subject),
            ("Ученики", Student),
        ):
            queryset = model.objects.using(alias)
            removed[label] = queryset.count()
            queryset.delete()
        users = User.objects.using(alias).filter(username__in=DEMO_USERNAMES)
        removed["Пользователи"] = users.count()
        users.delete()
    return removed


def prepare_demo_data():
    """Готовит демонстрационные данные в демо-базе. Рабочая база не затрагивается."""
    with demo_router.activate():
        return ensure_demo_data()


def mark_demo_session(request, role, created=False):
    """Ставит в сессии признак демо-режима.

    Вызывать нужно и до, и после ``login()``: при смене пользователя Django
    очищает сессию, и признак демо-режима иначе теряется — посетителя выбрасывает
    на страницу входа.
    """
    request.session["demo"] = True
    request.session["demo_role"] = role
    request.session["demo_is_new"] = created
    # Демонстрация живёт, пока открыт браузер: изменения посетителя не должны
    # переживать закрытие окна (каждый заходящий работает в своей копии базы).
    request.session.set_expiry(0)


def leave_demo(request):
    """Выключает демо-режим: дальше снова рабочая база."""
    request.session.pop("demo", None)
    request.session.pop("demo_role", None)
    request.session.pop("demo_is_new", None)


def demo_data_ready(term=None):
    """Заполнена ли демонстрационная база (смотрим именно её, не рабочую)."""
    queryset = Schedule.objects.using(demo_router.DEMO_ALIAS).filter(
        teacher__username=DEMO_TEACHER
    )
    if term is not None:
        queryset = queryset.filter(group__term=term)
    try:
        return queryset.exists()
    except Exception:  # noqa: BLE001 - демо-база может быть ещё не готова
        return False


def demo_data_current():
    """Соответствует ли набор в демо-базе текущей версии демо-данных.

    Набор версии 2 пересобирается, если разовых занятий нет вовсе: их добавили
    именно в этой версии, а в базе, созданной раньше, их быть не может.
    """
    if DEMO_DATA_VERSION < 2:
        return True
    alias = demo_router.DEMO_ALIAS
    try:
        return (
            Schedule.objects.using(alias)
            .filter(lesson_date__isnull=False)
            .exists()
        )
    except Exception:  # noqa: BLE001 - таблиц ещё нет
        return False


def _today():
    from django.utils import timezone

    return timezone.localdate()


def _make_user(username, last_name, first_name, role, middle_name=""):
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "last_name": last_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "role": role,
            "is_staff": False,
        },
    )
    if created:
        user.set_unusable_password()
        user.save()
        return user
    changed = []
    if middle_name and not user.middle_name:
        user.middle_name = middle_name
        changed.append("middle_name")
    if user.role != role:
        user.role = role
        changed.append("role")
    if changed:
        user.save(update_fields=changed)
    return user


@transaction.atomic
def ensure_demo_data(term=None):
    """Готовит демо-данные. Возвращает True, если набор создан заново."""
    with demo_router.activate():
        # Полугодие берём именно из демонстрационной базы: объекты рабочей базы
        # к её записям не подходят (маршрутизатор запрещает такие связи).
        periods_tools.ensure_periods()
        periods_tools.seed_branches()
        if term is None:
            _, term = periods_tools.resolve_period()
        rebuilt = False
        if not demo_data_ready(term) or not demo_data_current():
            # Набор другой версии (или база пуста) — собираем заново, чтобы не смешивать
            # данные двух версий демо-режима.
            _rebuild_demo_data(term)
            rebuilt = True
        # Время, адрес и кабинет приводим к демо-расписанию: у старых наборов их могло не быть.
        _apply_demo_schedule(term)
        # И снимаем старую связку «подмена = занятость не учитывается».
        _drop_legacy_conflict_flags()
    return rebuilt


def _drop_legacy_conflict_flags():
    """Снимает у демо-занятий старую связку «подмена → занятость не учитывается».

    Наборы, собранные до того, как «Подмена» стала чистой пометкой, хранят
    ``ignore_teacher_conflict=True`` у занятий-подмен (в том числе у разовых:
    их цикл ``_apply_demo_schedule`` не разбирает). Форма занятия такие занятия
    больше не создаёт, поэтому флаг снимаем — демо-данные должны вести себя
    так же, как настоящая форма. Возвращает число исправленных занятий.
    """
    return (
        Schedule.objects.using(demo_router.DEMO_ALIAS)
        .filter(is_substitution=True, ignore_teacher_conflict=True)
        .update(ignore_teacher_conflict=False)
    )


def _apply_demo_schedule(term):
    """Приводит время, адрес, кабинет и преподавателя демо-занятий к DEMO_SCHEDULE."""
    changed = 0
    for (
        subject_name,
        group_name,
        day_of_week,
        start,
        end,
        branch_name,
        room_name,
        replacement_username,
    ) in DEMO_SCHEDULE:
        schedule = Schedule.objects.filter(
            group__subject__name=subject_name,
            group__name=group_name,
            group__term=term,
            day_of_week=day_of_week,
            lesson_date__isnull=True,
        ).first()
        if schedule is None:
            continue
        branch = Branch.objects.filter(name=branch_name).first()
        room = (
            Room.objects.filter(branch=branch, name=room_name).first()
            if branch is not None
            else None
        )
        start_time = time.fromisoformat(start)
        end_time = time.fromisoformat(end)
        # Заменяющий преподаватель — подмена, а «подмена» это только пометка
        # (см. forms.ScheduleForm.clean): занятость заменяющего проверяется как
        # обычно, поэтому ignore_teacher_conflict здесь всегда False.
        teacher = schedule.group.subject.teacher
        if replacement_username:
            teacher = User.objects.filter(username=replacement_username).first() or teacher
        if (
            schedule.start_time == start_time
            and schedule.end_time == end_time
            and schedule.branch_id == (branch.pk if branch else None)
            and schedule.room_id == (room.pk if room else None)
            and (teacher is None or schedule.teacher_id == teacher.pk)
            and not schedule.ignore_teacher_conflict
        ):
            continue
        # Не занимаем время, которое уже стоит у другого занятия этой группы.
        if Schedule.objects.filter(
            group_id=schedule.group_id,
            day_of_week=day_of_week,
            start_time=start_time,
            lesson_date__isnull=True,
        ).exclude(pk=schedule.pk).exists():
            continue
        schedule.start_time = start_time
        schedule.end_time = end_time
        if branch is not None:
            schedule.branch = branch
        if room is not None:
            schedule.room = room
        if teacher is not None:
            schedule.teacher = teacher
        schedule.ignore_teacher_conflict = False
        schedule.save(
            update_fields=[
                "start_time",
                "end_time",
                "branch",
                "room",
                "teacher",
                "ignore_teacher_conflict",
            ]
        )
        changed += 1
    return changed


def _rebuild_demo_data(term, today=None):
    """Полностью пересобирает демо-набор в демо-базе.

    Сначала убираем всё прежнее, потом создаём заново: так демо-база никогда не
    смешивает данные двух версий демо-режима и в ней нет дублей.
    """
    today = today or _today()
    with demo_router.activate():
        wipe_demo_data()
        with transaction.atomic(using=demo_router.DEMO_ALIAS):
            users = _seed_users()
            branches = _seed_branches()
            students = _seed_students()
            groups = _seed_subjects_groups(term, branches, users, students)
            schedules = _seed_schedule(term, branches, users, groups, today)
            _seed_attendance(schedules, today)
            _seed_topics(schedules, today)
            _seed_contests(students, users, today)
            _seed_development(users, today)
    return True


def _seed_users():
    """Создаёт демо-учётки: методист, администратор и педагоги."""
    users = {}
    for username, last_name, first_name, middle_name, role in DEMO_STAFF:
        users[username] = _make_user(
            username, last_name, first_name, role, middle_name=middle_name
        )
    return users


def _seed_branches():
    """Адреса и кабинеты демо-набора. Стандартные филиалы журнала не трогаем."""
    branches = {}
    for name, order, room_names in DEMO_BRANCHES:
        branch, _ = Branch.objects.get_or_create(name=name, defaults={"order": order})
        changed = []
        if branch.order != order:
            branch.order = order
            changed.append("order")
        if changed:
            branch.save(update_fields=changed)
        branches[name] = branch
        for room_order, room_name in enumerate(room_names):
            Room.objects.get_or_create(
                branch=branch, name=room_name, defaults={"order": room_order}
            )
    return branches


def _seed_students():
    """Создаёт учеников с датами рождения и статусами заявления."""
    students = []
    for last_name, first_name, middle_name, birth_date, dismissal in DEMO_STUDENTS:
        student, _ = Student.objects.get_or_create(
            last_name=last_name,
            first_name=first_name,
            middle_name=middle_name,
            defaults={
                "birth_date": date.fromisoformat(birth_date),
                "dismissal_status": dismissal,
            },
        )
        changed = []
        if student.birth_date != date.fromisoformat(birth_date):
            student.birth_date = date.fromisoformat(birth_date)
            changed.append("birth_date")
        if student.dismissal_status != dismissal:
            student.dismissal_status = dismissal
            changed.append("dismissal_status")
        if changed:
            student.save(update_fields=changed)
        students.append(student)
    return students


def _seed_subjects_groups(term, branches, users, students):
    """Программы с преподавателями и группы с составом.

    Один ученик попадает не больше чем в одну группу каждой программы: иначе
    состав групп выглядел бы неправдоподобно (модель это не запрещает).
    """
    groups = {}
    for (
        subject_name,
        teacher_username,
        extra_usernames,
        branch_name,
        subject_groups,
    ) in DEMO_SUBJECTS:
        branch = branches.get(branch_name) or Branch.objects.order_by("order", "name").first()
        subject, _ = Subject.objects.get_or_create(
            name=subject_name,
            branch=branch,
            term=term,
            defaults={"teacher": users[teacher_username]},
        )
        # Ведующие преподаватели программы: основной и подменяющие.
        subject.teachers.add(users[teacher_username])
        for username in extra_usernames:
            subject.teachers.add(users[username])
        for group_name, student_indexes in subject_groups:
            group, _ = Group.objects.get_or_create(
                term=term, subject=subject, name=group_name
            )
            # Один ученик попадает не больше чем в одну группу этой программы.
            group.students.set([students[index] for index in student_indexes])
            groups[(subject_name, group_name)] = group
    return groups


def _create_schedule(group, branch, room, day_of_week, start, end, teacher,
                     lesson_date=None, is_substitution=False):
    """Создаёт занятие расписания: обычное или разовое (с датой).

    Строку всегда создаём целиком: у занятия преподаватель обязателен, поэтому
    «создать и потом дополнить» здесь нельзя — база не примет запись без него.
    """
    if lesson_date is not None:
        day_of_week = lesson_date.weekday()
    start_time = time.fromisoformat(start)
    # Ищем по тем полям, которые составляют уникальность занятия: у обычного это
    # день недели и время, у разового — дата и время.
    schedule = Schedule.objects.filter(
        group=group,
        start_time=start_time,
        lesson_date=lesson_date,
        **({"day_of_week": day_of_week} if lesson_date is None else {}),
    ).first()
    if schedule is None:
        schedule = Schedule(
            group=group,
            day_of_week=day_of_week,
            start_time=start_time,
            lesson_date=lesson_date,
        )
    schedule.end_time = time.fromisoformat(end)
    schedule.branch = branch
    schedule.room = room
    schedule.teacher = teacher
    # «Подмена» — только пометка «занятие ведёт заменяющий преподаватель»
    # (см. forms.ScheduleForm.clean): занятость заменяющего проверяется как у любого
    # преподавателя, поэтому из подмены ignore_teacher_conflict не выставляется.
    # Демо-данные должны вести себя так же, как настоящая форма.
    schedule.ignore_teacher_conflict = False
    schedule.is_substitution = bool(is_substitution)
    schedule.save()
    return schedule


def _seed_schedule(term, branches, users, groups, today=None):
    """Занятия по дням недели и разовые занятия."""
    today = today or _today()
    schedules = []
    for (
        subject_name,
        group_name,
        day_of_week,
        start,
        end,
        branch_name,
        room_name,
        teacher_username,
    ) in DEMO_SCHEDULE:
        group = groups[(subject_name, group_name)]
        branch = branches.get(branch_name) or Branch.objects.order_by("order", "name").first()
        room = Room.objects.filter(branch=branch, name=room_name).first()
        teacher = group.subject.teacher
        if teacher_username:
            teacher = users.get(teacher_username) or teacher
        # Подмена — это преподаватель, не заявленный у программы. Если занятие ведёт
        # второй преподаватель программы, подмены нет: он ведёт её наравне с основным.
        declared = {item.pk for item in group.subject.teacher_options}
        is_substitution = bool(teacher is not None and teacher.pk not in declared)
        schedules.append(
            _create_schedule(
                group, branch, room, day_of_week, start, end, teacher,
                is_substitution=is_substitution,
            )
        )

    monday = _week_monday(today)
    for (
        subject_name,
        group_name,
        day_offset,
        start,
        end,
        branch_name,
        room_name,
        teacher_username,
        _topic,
    ) in DEMO_ONE_OFF:
        group = groups[(subject_name, group_name)]
        branch = branches.get(branch_name) or Branch.objects.order_by("order", "name").first()
        room = Room.objects.filter(branch=branch, name=room_name).first()
        teacher = group.subject.teacher
        if teacher_username:
            teacher = users.get(teacher_username) or teacher
        # Подмена — это преподаватель, не заявленный у программы: он ведёт занятие
        # вместо основного. Помечаем его подменой; занятость при этом считается
        # как обычно (см. _create_schedule).
        declared = {item.pk for item in group.subject.teacher_options}
        is_substitution = bool(teacher is not None and teacher.pk not in declared)
        schedules.append(
            _create_schedule(
                group, branch, room, (monday + timedelta(days=day_offset)).weekday(),
                start, end, teacher, lesson_date=monday + timedelta(days=day_offset),
                is_substitution=is_substitution,
            )
        )
    return schedules


def _week_monday(day):
    """Понедельник недели, к которой относится дата."""
    return day - timedelta(days=day.weekday())


def _student_key(student):
    """Устойчивый ключ ученика для узора отметок.

    Считаем от ФИО, а не от ``pk``: у пересобранной демо-базы номера учеников другие,
    а узор отметок обязан остаться прежним — повторная сборка даёт те же отметки.
    """
    name = " ".join(
        part for part in (student.last_name, student.first_name, student.middle_name) if part
    ).lower()
    return sum((index + 1) * ord(symbol) for index, symbol in enumerate(name))


def _attendance_status(student, lesson_date):
    """Отметка ученика на дату: детерминированно, без случайных чисел."""
    if lesson_date.weekday() >= 5:
        # По субботам и воскресеньям дети приходят реже — пропусков больше.
        padding = 3
    else:
        padding = 0
    key = _student_key(student) * 7 + lesson_date.toordinal() + padding
    return STATUS_PATTERN[key % len(STATUS_PATTERN)]


def _seed_attendance(schedules, today):
    """Отметки посещаемости за последние недели: обычные и разовые занятия.

    Отметки ставятся на каждый день, когда занятие действительно было, и только на
    прошедшие дни и сегодняшний: на будущее отметок не бывает. В день разового
    занятия учитываются и обычные занятия этого дня недели — иначе они остаются
    без отметок. Повторный запуск на ту же дату даёт те же числа: выбор занятия
    из ``schedules`` детерминированный, случайных чисел нет.
    """
    first = today - timedelta(days=DEMO_DAYS_BACK - 1)
    last = today
    by_weekday = {}
    by_date = {}
    for schedule in schedules:
        if schedule.lesson_date is None:
            by_weekday.setdefault(schedule.day_of_week, []).append(schedule)
        else:
            by_date.setdefault(schedule.lesson_date, []).append(schedule)

    rows = []
    day = first
    while day <= last:
        # В один день бывают и разовые занятия, и обычные: разовое не вытесняет обычное.
        lessons = []
        seen = set()
        for schedule in [*by_date.get(day, ()), *by_weekday.get(day.weekday(), ())]:
            # Одну и ту же группу в одно время отмечаем один раз: у разового занятия
            # в базе своя запись, но отметки у него те же ученики и тот же день.
            key = (schedule.group_id, schedule.start_time)
            if key in seen:
                continue
            seen.add(key)
            lessons.append(schedule)
        for schedule in lessons:
            for student in schedule.group.students.all():
                rows.append(
                    Attendance(
                        student=student,
                        schedule=schedule,
                        date=day,
                        status=_attendance_status(student, day),
                    )
                )
        day += timedelta(days=1)
    if rows:
        Attendance.objects.bulk_create(rows, batch_size=500)


def _lesson_dates(schedule, today, weeks=TOPIC_WEEKS):
    """Даты занятия за последние недели (включая сегодняшний день), поздние — первыми.

    Разовое занятие бывает один раз — у него ровно одна дата. У обычного берём те же
    дни недели, что и в журнале по неделям, поэтому число тем не зависит от того,
    в какой день недели создали демо-набор.
    """
    if schedule.lesson_date is not None:
        # У разового занятия ровно одна дата. Она может быть уже на этой неделе —
        # тогда у занятия есть и тема, и отметки.
        return [schedule.lesson_date]
    first = _week_monday(today) - timedelta(days=7 * (weeks - 1))
    dates = []
    for week in range(weeks):
        day = first + timedelta(days=7 * week + schedule.day_of_week)
        # Дни вперёд не забегаем: тема ставится на уже прошедшее занятие.
        if day <= today:
            dates.append(day)
    dates.reverse()
    return dates


def _seed_topics(schedules, today):
    """Темы занятий и академические часы за последние недели.

    Часть занятий оставляем без темы: в журнале видно и заполненные темы,
    и пустые поля, которые методист может заполнить сам.
    """
    topics = []
    for schedule in schedules:
        bank = DEMO_TOPIC_BANK.get(schedule.group.subject.name, ())
        if not bank:
            continue
        for date_index, lesson_date in enumerate(_lesson_dates(schedule, today)):
            # Самое свежее занятие темы всегда имеет — на него смотрят в первую
            # очередь, — а часть прошлых остаётся незаполненной.
            if date_index and (schedule.pk + date_index) % TOPIC_STEP == 0:
                continue
            number = (schedule.pk + lesson_date.toordinal()) % len(bank)
            topics.append(
                LessonTopic(
                    schedule=schedule,
                    date=lesson_date,
                    topic=f"{LESSON_TOPIC_PREFIX}{bank[number]}",
                    hours=schedule.academic_hours,
                    teacher=schedule.teacher,
                )
            )
    if topics:
        LessonTopic.objects.bulk_create(topics, batch_size=200)


def _contest_date(today, days_back):
    return today - timedelta(days=days_back)


def _seed_contests(students, users, today):
    """Конкурсы детей: победители, призёры и участники по разным программам."""
    for index, days_back, title, status, subject_name, author in DEMO_CONTESTS:
        contest, _ = ContestResult.objects.get_or_create(
            student=students[index],
            date=_contest_date(today, days_back),
            title=title,
            defaults={
                "status": status,
                "subject": subject_name,
                "created_by": users.get(author),
            },
        )
        if contest.created_by_id is None:
            contest.created_by = users.get(author)
            contest.save(update_fields=["created_by"])


def _seed_development(users, today):
    """Повышение квалификации педагогов: курсы, конкурсы и стажировки."""
    for username, days_back, kind, program, organization, hours, result in DEMO_DEVELOPMENT:
        teacher = users.get(username)
        if teacher is None:
            continue
        development, _ = TeacherDevelopment.objects.get_or_create(
            teacher=teacher,
            date=_contest_date(today, days_back),
            program=program,
            defaults={
                "kind": kind,
                "organization": organization,
                "hours": hours,
                "result": result,
                "created_by": teacher,
            },
        )
        if development.created_by_id is None:
            development.created_by = teacher
            development.save(update_fields=["created_by"])
