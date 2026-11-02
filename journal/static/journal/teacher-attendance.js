/* Скрипты страницы: перенесены из шаблона. */
(function () {
"use strict";

    // Календарь сворачивается плавно, а выбранное состояние запоминается
    // и восстанавливается при переключении дня, недели и месяцев.
    (function () {
        const root = document.querySelector("[data-calendar]");
        if (!root) {
            return;
        }
        const toggle = root.querySelector("[data-calendar-toggle]");
        const body = root.querySelector("[data-calendar-body]");
        const caret = root.querySelector("[data-calendar-caret]");
        const STORAGE_KEY = "journal.calendar.collapsed";
        const reduced = window.matchMedia
            && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

        function stored() {
            try {
                return window.localStorage.getItem(STORAGE_KEY) === "1";
            } catch (error) {
                return false;   // приватный режим и запрет хранилища — просто не запоминаем
            }
        }

        function apply(collapsed, animate) {
            if (!animate) {
                body.classList.add("calendar-body-static");
            }
            body.classList.toggle("is-collapsed", collapsed);
            if (caret) {
                caret.textContent = collapsed ? "▾" : "▴";
                caret.classList.toggle("rotate-180", collapsed);
            }
            if (toggle) {
                toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
                toggle.setAttribute("title", collapsed ? "Развернуть календарь" : "Свернуть календарь");
            }
            if (!animate) {
                window.requestAnimationFrame(function () {
                    body.classList.remove("calendar-body-static");
                });
            }
        }

        apply(stored(), false);

        if (toggle) {
            toggle.addEventListener("click", function () {
                const collapsed = !body.classList.contains("is-collapsed");
                apply(collapsed, !reduced);
                try {
                    window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
                } catch (error) {
                    /* хранилище недоступно — работаем без запоминания */
                }
            });
        }
    })();
})();
