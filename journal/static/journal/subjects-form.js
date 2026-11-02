/* Скрипты страницы программы: панель преподавателей с галочками.
   Настоящий select multiple остаётся в форме и уходит на сервер как раньше,
   а галочки лишь показывают и меняют его выбранные значения. */
(function () {
"use strict";

    function normalize(value) {
        return (value || "").toString().toLowerCase().replace(/ё/g, "е").trim();
    }

    function optionByValue(select, value) {
        for (let index = 0; index < select.options.length; index += 1) {
            if (select.options[index].value === value) {
                return select.options[index];
            }
        }
        return null;
    }

    function initTeacherChecks(panel) {
        const select = panel.querySelector("select");
        const list = panel.querySelector("[data-teacher-list]");
        if (!select || !list || panel.dataset.teacherChecksReady) {
            return;
        }
        panel.dataset.teacherChecksReady = "1";

        const search = panel.querySelector("[data-teacher-search]");
        const counter = panel.querySelector("[data-teacher-count]");
        const empty = panel.querySelector("[data-teacher-empty]");
        // Основного преподавателя в панели помечаем подписью «основной».
        const mainSelect = select.dataset.mainTeacherField
            ? document.querySelector(select.dataset.mainTeacherField)
            : null;
        const rows = Array.prototype.slice.call(
            list.querySelectorAll("[data-teacher-item]")
        );
        const boxes = rows.map(function (row) {
            return row.querySelector("[data-teacher-check]");
        });
        const total = rows.length;

        function updateCount() {
            if (!counter) {
                return;
            }
            let checked = 0;
            boxes.forEach(function (box) {
                if (box && box.checked) {
                    checked += 1;
                }
            });
            counter.textContent = "Отмечено: " + checked + " из " + total;
        }

        // Источник истины — настоящий select: панель показывает его выбор.
        function syncFromSelect() {
            rows.forEach(function (row, index) {
                const box = boxes[index];
                if (!box) {
                    return;
                }
                const option = optionByValue(select, box.value);
                const checked = Boolean(option && option.selected);
                box.checked = checked;
                row.classList.toggle("bg-indigo-50", checked);
                row.classList.toggle("font-medium", checked);
                row.classList.toggle("text-indigo-700", checked);
            });
            updateCount();
        }

        // Отмеченные поднимаем вверх: выбранное видно сразу.
        function order() {
            const checked = [];
            const rest = [];
            rows.forEach(function (row, index) {
                const box = boxes[index];
                if (box && box.checked) {
                    checked.push(row);
                } else {
                    rest.push(row);
                }
            });
            checked.concat(rest).forEach(function (row) {
                list.insertBefore(row, empty || null);
            });
        }

        // Поиск по фамилии и подпись «основной» пересчитываются при каждом изменении.
        function apply() {
            const query = normalize(search ? search.value : "");
            const main = mainSelect ? String(mainSelect.value || "") : "";
            let visible = 0;
            rows.forEach(function (row, index) {
                const box = boxes[index];
                const match = !query ||
                    normalize(row.dataset.teacherText || "").indexOf(query) !== -1;
                row.classList.toggle("hidden", !match);
                if (match) {
                    visible += 1;
                }
                const mark = row.querySelector("[data-teacher-main]");
                if (mark) {
                    const isMain = Boolean(main) && Boolean(box) && box.value === main;
                    mark.classList.toggle("hidden", !isMain);
                }
            });
            if (empty) {
                empty.classList.toggle("hidden", visible !== 0);
            }
            order();
        }

        rows.forEach(function (row, index) {
            const box = boxes[index];
            if (!box) {
                return;
            }
            box.addEventListener("change", function () {
                const option = optionByValue(select, box.value);
                if (option) {
                    option.selected = box.checked;
                }
                syncFromSelect();
                order();
                select.dispatchEvent(new Event("change", { bubbles: true }));
            });
        });

        if (search) {
            search.addEventListener("input", apply);
            // Enter в строке поиска не должен отправлять форму целиком.
            search.addEventListener("keydown", function (event) {
                if (event.key === "Enter") {
                    event.preventDefault();
                }
            });
        }
        if (mainSelect) {
            mainSelect.addEventListener("change", apply);
        }
        const form = select.form || panel.closest("form");
        if (form) {
            // Перед отправкой переносим галочки в настоящий select.
            form.addEventListener("submit", function () {
                rows.forEach(function (row, index) {
                    const box = boxes[index];
                    const option = box ? optionByValue(select, box.value) : null;
                    if (option) {
                        option.selected = box.checked;
                    }
                });
            });
        }

        // Настоящий список прячем так же, как одиночные: данные уходят из него,
        // а показывает и меняет выбор панель с галочками.
        select.classList.add("sr-only");
        select.setAttribute("tabindex", "-1");
        syncFromSelect();
        apply();
    }

    function init() {
        document.querySelectorAll("[data-teacher-checks]").forEach(initTeacherChecks);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    window.dshInitTeacherChecks = init;
})();
