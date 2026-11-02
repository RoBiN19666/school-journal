/* Скрипты страницы: перенесены из шаблона. */
(function () {
"use strict";

    (function () {
        const selectAll = document.getElementById("select-all");
        if (selectAll) {
            selectAll.addEventListener("change", function () {
                document.querySelectorAll(".student-checkbox").forEach(function (box) {
                    box.checked = selectAll.checked;
                });
            });
        }
    })();

    /*
     * Поиск учеников по всей базе ищется сам, без кнопки «Найти».
     * Поле лежит внутри формы сохранения состава, но привязано к отдельной
     * GET-форме поиска (form="group-search"), поэтому отправляется только она —
     * галочки при этом ничего не сохраняют.
     *
     * Правила:
     *  - ждём 300 мс после последнего нажатия клавиши и только потом отправляем,
     *    иначе форма уходила бы на каждую букву;
     *  - очистка поля тоже отправляет форму — так снова показываются все ученики;
     *  - Enter отправляет сразу, не дожидаясь задержки (как раньше работала кнопка).
     */
    (function () {
        const SEARCH_DELAY = 300;
        const inputs = document.querySelectorAll("[data-group-search]");

        inputs.forEach(function (input) {
            const form = input.form || document.getElementById(input.getAttribute("form"));
            if (!form) {
                return;
            }
            let timer = null;

            function submitNow() {
                if (timer !== null) {
                    window.clearTimeout(timer);
                    timer = null;
                }
                // requestSubmit запускает обычную отправку формы: её перехватывает
                // каркас журнала и обновляет страницу динамически.
                if (form.requestSubmit) {
                    form.requestSubmit();
                } else {
                    form.submit();
                }
            }

            function submitLater() {
                if (timer !== null) {
                    window.clearTimeout(timer);
                }
                timer = window.setTimeout(submitNow, SEARCH_DELAY);
            }

            input.addEventListener("input", submitLater);
            // Enter — отправка сразу; отправку по умолчанию не трогаем:
            // форма поиска делает то же самое.
            input.addEventListener("keydown", function (event) {
                if (event.key === "Enter") {
                    submitNow();
                }
            });
        });
    })();
})();
