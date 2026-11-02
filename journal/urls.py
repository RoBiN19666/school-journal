"""Маршруты приложения journal."""
from django.urls import include, path, re_path

from . import views

urlpatterns = [
    # Смена языка интерфейса: штатный механизм Django.
    # Даёт POST /i18n/setlang/ (поле language + необязательное next) —
    # на него отправляют форму переключатели языка в шапке, на странице входа
    # и на экране первой настройки. Страницы при этом НЕ получают префикс языка:
    # язык хранится в cookie/сессии (см. LANGUAGE_COOKIE_NAME в settings.py).
    path("i18n/", include("django.conf.urls.i18n")),
    path("", views.HomeView.as_view(), name="home"),
    path("about/", views.AboutView.as_view(), name="about"),
    # Правка названия организации со страницы «О программе» (главный администратор).
    path(
        "about/organization/",
        views.OrganizationNameView.as_view(),
        name="about_organization",
    ),
    path("faq/", views.FaqView.as_view(), name="faq"),
    path("login/", views.JournalLoginView.as_view(), name="login"),
    path("logout/", views.JournalLogoutView.as_view(), name="logout"),
    # Первоначальная настройка: пока в базе нет главного администратора,
    # все страницы журнала ведут сюда (см. journal/middleware.py).
    path("setup/", views.SetupView.as_view(), name="setup"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    # Ученики
    path("students/", views.StudentListView.as_view(), name="students"),
    path("students/add/", views.StudentCreateView.as_view(), name="student_add"),
    path(
        "students/<int:pk>/edit/",
        views.StudentUpdateView.as_view(),
        name="student_edit",
    ),
    path(
        "students/<int:pk>/delete/",
        views.StudentDeleteView.as_view(),
        name="student_delete",
    ),
    path("students/import/", views.StudentImportView.as_view(), name="student_import"),
    path(
        "students/import/confirm/",
        views.StudentImportConfirmView.as_view(),
        name="student_import_confirm",
    ),
    # Программы и группы
    path("subjects/", views.SubjectListView.as_view(), name="subjects"),
    path("subjects/add/", views.SubjectCreateView.as_view(), name="subject_add"),
    path("subjects/<int:pk>/", views.SubjectDetailView.as_view(), name="subject_detail"),
    path(
        "subjects/<int:pk>/edit/",
        views.SubjectUpdateView.as_view(),
        name="subject_edit",
    ),
    path(
        "subjects/<int:pk>/delete/",
        views.SubjectDeleteView.as_view(),
        name="subject_delete",
    ),
    path(
        "subjects/<int:subject_id>/groups/add/",
        views.GroupCreateView.as_view(),
        name="group_add",
    ),
    path("groups/<int:pk>/edit/", views.GroupUpdateView.as_view(), name="group_edit"),
    path(
        "groups/<int:pk>/delete/",
        views.GroupDeleteView.as_view(),
        name="group_delete",
    ),
    path(
        "groups/<int:pk>/students/",
        views.GroupStudentsView.as_view(),
        name="group_students",
    ),
    # Расписание
    path("schedule/", views.ScheduleListView.as_view(), name="schedule"),
    path("schedule/add/", views.ScheduleCreateView.as_view(), name="schedule_add"),
    path(
        "schedule/<int:pk>/edit/",
        views.ScheduleUpdateView.as_view(),
        name="schedule_edit",
    ),
    path(
        "schedule/<int:pk>/delete/",
        views.ScheduleDeleteView.as_view(),
        name="schedule_delete",
    ),
    # Преподаватели
    path("teachers/", views.TeacherListView.as_view(), name="teachers"),
    path("teachers/add/", views.TeacherCreateView.as_view(), name="teacher_add"),
    path(
        "teachers/<int:pk>/edit/",
        views.TeacherUpdateView.as_view(),
        name="teacher_edit",
    ),
    # Отсутствия (командировка, больничный) — отдельный раздел для УМО.
    path("absences/", views.AbsenceListView.as_view(), name="absence_list"),
    path("absences/add/", views.TeacherAbsenceCreateView.as_view(), name="absence_add"),
    path(
        "absences/<int:pk>/edit/",
        views.TeacherAbsenceUpdateView.as_view(),
        name="absence_edit",
    ),
    path(
        "absences/<int:pk>/substitutions/",
        views.AbsenceSubstitutionsView.as_view(),
        name="absence_substitutions",
    ),
    path(
        "absences/<int:pk>/delete/",
        views.TeacherAbsenceDeleteView.as_view(),
        name="absence_delete",
    ),
    # Подмены: список занятий заменяющих — просмотр и поиск (создание в разделе
    # «Отсутствия» и в недельном журнале).
    path(
        "substitutions/",
        views.SubstitutionListView.as_view(),
        name="substitution_list",
    ),
    # Свой профиль: данные и пароль
    path("profile/", views.ProfileView.as_view(), name="profile"),
    # Безопасность (только администратор)
    path("customization/", views.CustomizationView.as_view(), name="customization"),
    path("security/", views.SecurityView.as_view(), name="security"),
    # Учебные годы и полугодия
    path("periods/", views.PeriodListView.as_view(), name="periods"),
    path("periods/switch/", views.PeriodSwitchView.as_view(), name="period_switch"),
    path("periods/copy/", views.PeriodCopyView.as_view(), name="period_copy"),
    # Сотрудники УМО (только администратор)
    path("staff/", views.StaffMemberListView.as_view(), name="staff"),
    path("staff/add/", views.StaffMemberCreateView.as_view(), name="staff_add"),
    path("staff/<int:pk>/edit/", views.StaffMemberUpdateView.as_view(), name="staff_edit"),
    # Выгрузка в Excel
    path("export/", views.ExportIndexView.as_view(), name="export"),
    path(
        "export/students.xlsx",
        views.ExportStudentsView.as_view(),
        name="export_students",
    ),
    path(
        "export/subjects.xlsx",
        views.ExportSubjectsView.as_view(),
        name="export_subjects",
    ),
    path(
        "export/schedule.xlsx",
        views.ExportScheduleView.as_view(),
        name="export_schedule",
    ),
    path(
        "export/schedule.png",
        views.ExportScheduleImageView.as_view(),
        name="export_schedule_image",
    ),
    path(
        "export/attendance.xlsx",
        views.ExportAttendanceView.as_view(),
        name="export_attendance",
    ),
    path("export/topics.xlsx", views.ExportTopicsView.as_view(), name="export_topics"),
    path("export/contests.xlsx", views.ExportContestsView.as_view(), name="export_contests"),
    path(
        "export/development.xlsx",
        views.ExportDevelopmentView.as_view(),
        name="export_development",
    ),
    path("export/users.xlsx", views.ExportUsersView.as_view(), name="export_users"),
    # Резервная копия (только администратор)
    path("backup/", views.BackupIndexView.as_view(), name="backup"),
    path(
        "backup/download/",
        views.BackupDownloadView.as_view(),
        name="backup_download",
    ),
    path(
        "backup/restore/",
        views.BackupRestoreView.as_view(),
        name="backup_restore",
    ),
    # Демо-режим
    path("demo/", views.DemoLandingView.as_view(), name="demo"),
    path("demo/exit/", views.DemoExitView.as_view(), name="demo_exit"),
    path("demo/manage/", views.DemoManageView.as_view(), name="demo_manage"),
    path("demo/manage/wipe/", views.DemoWipeView.as_view(), name="demo_wipe"),
    path("demo/<str:role>/", views.DemoLoginView.as_view(), name="demo_login"),
    # Кабинет преподавателя
    path("teacher/", views.TeacherDashboardView.as_view(), name="teacher_dashboard"),
    path("teacher/week/", views.TeacherWeekView.as_view(), name="teacher_week"),
    path(
        "teacher/lesson/<int:pk>/",
        views.AttendanceView.as_view(),
        name="attendance",
    ),
    path(
        "teacher/export.xlsx",
        views.TeacherExportView.as_view(),
        name="teacher_export",
    ),
    # Темы занятий и посещаемость по неделям
    path("weeks/", views.WeekJournalView.as_view(), name="week_journal"),
    path("topics/", views.TopicJournalView.as_view(), name="topics"),
    path("topics/save/", views.TopicSaveView.as_view(), name="topic_save"),
    # Конкурсы детей и повышение квалификации педагогов
    path("contests/", views.ContestListView.as_view(), name="contests"),
    path("contests/add/", views.ContestCreateView.as_view(), name="contest_add"),
    path("contests/<int:pk>/edit/", views.ContestUpdateView.as_view(), name="contest_edit"),
    path("contests/<int:pk>/delete/", views.ContestDeleteView.as_view(), name="contest_delete"),
    path("development/", views.DevelopmentListView.as_view(), name="development"),
    path("development/add/", views.DevelopmentCreateView.as_view(), name="development_add"),
    path(
        "development/<int:pk>/edit/",
        views.DevelopmentUpdateView.as_view(),
        name="development_edit",
    ),
    path(
        "development/<int:pk>/delete/",
        views.DevelopmentDeleteView.as_view(),
        name="development_delete",
    ),
]


# Загруженные логотип и фавикон отдаём всегда, независимо от режима отладки.
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", views.branding_file, name="branding_file"),
]
