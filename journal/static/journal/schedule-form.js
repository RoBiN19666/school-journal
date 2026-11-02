/* Скрипты страницы расписания: перенесены из шаблона. */
(function () {
"use strict";

    (function () {
        const groupSelect = document.getElementById("id_group");
        const teacherSelect = document.getElementById("id_teacher");
        const roomSelect = document.getElementById("id_room");
        const daySelect = document.getElementById("id_day_of_week");
        const dateInput = document.getElementById("id_lesson_date");
        const startInput = document.getElementById("id_start_time");
        const endInput = document.getElementById("id_end_time");
        const duration = window.__APP_CONFIG.scheduleDuration;

        // День недели по дате разового занятия: понедельник — 0, как в журнале.
        function weekdayFromDate(value) {
            const parts = String(value || "").split("-");
            if (parts.length !== 3) {
                return null;
            }
            const day = new Date(
                Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])
            );
            if (isNaN(day.getTime())) {
                return null;
            }
            return (day.getDay() + 6) % 7;
        }

        function addMinutes(value, minutes) {
            const parts = value.split(":");
            if (parts.length < 2) {
                return value;
            }
            const total = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10) + minutes;
            const capped = Math.min(total, 23 * 60 + 59);
            const hours = String(Math.floor(capped / 60)).padStart(2, "0");
            const mins = String(capped % 60).padStart(2, "0");
            return hours + ":" + mins;
        }

        function query() {
            const params = new URLSearchParams();
            if (groupSelect && groupSelect.value) {
                params.set("group", groupSelect.value);
            }
            if (teacherSelect && teacherSelect.value) {
                params.set("teacher", teacherSelect.value);
            }
            if (roomSelect && roomSelect.value) {
                params.set("room", roomSelect.value);
            }
            if (daySelect && daySelect.value !== "") {
                params.set("day", daySelect.value);
            }
            if (dateInput && dateInput.value) {
                params.set("lesson_date", dateInput.value);
            }
            if (startInput && startInput.value) {
                params.set("start", startInput.value);
            }
            if (endInput && endInput.value) {
                params.set("end", endInput.value);
            }
            return window.location.pathname + "?" + params.toString();
        }

        // Свободные промежутки: клик подставляет день и время в форму.
        function bindFreeWindows() {
            document.querySelectorAll(".free-window").forEach(function (badge) {
                if (badge.dataset.ready) {
                    return;
                }
                badge.dataset.ready = "1";
                badge.addEventListener("click", function () {
                    if (daySelect) {
                        daySelect.value = badge.dataset.day;
                    }
                    if (startInput) {
                        startInput.value = badge.dataset.windowStart;
                        startInput.dispatchEvent(new Event("input", { bubbles: true }));
                        startInput.dispatchEvent(new Event("change", { bubbles: true }));
                    }
                    if (endInput) {
                        endInput.value = badge.dataset.windowEnd;
                        delete endInput.dataset.auto;
                        endInput.dispatchEvent(new Event("input", { bubbles: true }));
                        endInput.dispatchEvent(new Event("change", { bubbles: true }));
                    }
                    if (startInput) {
                        startInput.scrollIntoView({ behavior: "smooth", block: "center" });
                        startInput.focus();
                    }
                });
            });
        }

        // Панели занятости обновляем без перезагрузки страницы.
        // Смена группы меняет список кабинетов в самой форме, поэтому там перезагружаем.
        function reloadPanels() {
            const url = query();
            const panels = document.getElementById("schedule-panels");
            if (!panels || typeof window.fetch !== "function") {
                window.location = url;
                return;
            }
            if (window.history && window.history.replaceState) {
                window.history.replaceState({ schedule: true }, "", url);
            }
            panels.classList.add("opacity-50");
            fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error("не удалось обновить панели");
                    }
                    return response.text();
                })
                .then(function (html) {
                    const parsed = new DOMParser().parseFromString(html, "text/html");
                    const fresh = parsed.getElementById("schedule-panels");
                    if (!fresh) {
                        throw new Error("нет панелей в ответе");
                    }
                    panels.replaceChildren.apply(panels, Array.from(fresh.childNodes));
                    bindFreeWindows();
                })
                .catch(function () {
                    window.location = url;
                })
                .then(function () {
                    panels.classList.remove("opacity-50");
                });
        }

        function reloadForm() {
            window.location = query();
        }

        // Группа определяет адрес с кабинетами, преподаватель — панель свободного времени.
        if (groupSelect) {
            groupSelect.addEventListener("change", reloadForm);
        }
        if (teacherSelect) {
            teacherSelect.addEventListener("change", reloadPanels);
        }
        if (daySelect) {
            daySelect.addEventListener("change", reloadPanels);
        }
        // Дата разового занятия сама подставляет день недели — вручную выбирать не нужно.
        if (dateInput) {
            dateInput.addEventListener("change", function () {
                const value = weekdayFromDate(dateInput.value);
                if (value === null) {
                    reloadPanels();
                    return;
                }
                if (daySelect && String(value) !== daySelect.value) {
                    daySelect.value = String(value);
                }
                reloadPanels();
            });
        }

        // Окончание подставляем автоматически, пока его не поправили вручную.
        if (startInput && endInput) {
            startInput.addEventListener("change", function () {
                if (!startInput.value) {
                    return;
                }
                if (endInput.value && !endInput.dataset.auto) {
                    return;
                }
                endInput.value = addMinutes(startInput.value, duration);
                endInput.dataset.auto = "1";
                // Сообщаем виджету времени: значение изменилось не пользователем.
                // Только change: событие input сбросило бы признак ручной правки,
                // и следующая смена начала перезаписала бы выбранное время.
                endInput.dispatchEvent(new Event("change", { bubbles: true }));
            });
            endInput.addEventListener("input", function () {
                delete endInput.dataset.auto;
            });
        }

        // Клик по свободному промежутку подставляет день и время.
        bindFreeWindows();
    })();
})();
