/* Подмены на весь период отсутствия: живая подсветка конфликтов и сводка.
 *
 * Данные приходят из шаблона (json_script #substitution-conflicts): по каждой
 * строке плана — причины конфликтов для каждого возможного заменяющего. Так
 * подсветка пересчитывается без запроса к серверу, а сам сервер всё равно
 * проверяет конфликты при сохранении: без JavaScript страница работает кнопкой
 * «Проверить конфликты».
 */
(function () {
"use strict";

    const form = document.getElementById("substitutions-form");
    const dataNode = document.getElementById("substitution-conflicts");
    if (!form || !dataNode) {
        return;
    }
    let data;
    try {
        data = JSON.parse(dataNode.textContent);
    } catch (error) {
        return;   // данных нет — оставляем пометки, которые нарисовал сервер
    }

    const labels = data.labels || {};
    const rowsData = data.rows || {};
    const rows = Array.prototype.slice.call(form.querySelectorAll("[data-row]"));
    const master = form.querySelector("[data-role='master']");
    const counters = {
        create: form.querySelector("[data-role='count-create']"),
        skipped: form.querySelector("[data-role='count-skipped']"),
        conflicts: form.querySelector("[data-role='count-conflicts']"),
    };

    function escape(text) {
        const node = document.createElement("span");
        node.textContent = text === null || text === undefined ? "" : String(text);
        return node.innerHTML;
    }

    function badge(classes, text) {
        return '<span class="rounded-full px-2 py-0.5 text-xs font-medium '
            + classes + '">' + escape(text) + "</span>";
    }

    function stateOf(row, key) {
        const apply = row.querySelector("[data-role='apply']").checked;
        const ignore = row.querySelector("[data-role='ignore']").checked;
        const select = row.querySelector("[data-role='teacher']");
        // Пустой выбор строки заменяется общим («один заменяющий на все дни»),
        // но только если он ведёт программу этой строки: в списке строки есть
        // лишь преподаватели программы, поэтому проверяем само наличие варианта.
        let teacherId = select.value;
        if (!teacherId && master && master.value) {
            if (select.querySelector('option[value="' + master.value + '"]')) {
                teacherId = master.value;
            }
        }
        const info = rowsData[key] || {};
        if (!apply) {
            return {state: "skipped", note: labels.skipped};
        }
        if (!teacherId) {
            // Общий заменяющий сюда не подходит — строке нужен свой.
            if (info.foreign) {
                return {state: "foreign", note: info.foreign};
            }
            return {state: "empty", note: labels.empty};
        }
        if (info.already) {
            return {state: "already", note: info.already};
        }
        const conflict = info[teacherId];
        if (conflict && conflict.hard) {
            return {state: "conflict", note: conflict.hard};
        }
        if (conflict && conflict.soft && !ignore) {
            return {state: "conflict", note: conflict.soft + " " + labels.hint};
        }
        return {state: "create", note: info.in_place ? labels.replace : labels.create};
    }

    function render() {
        const counts = {create: 0, skipped: 0, conflicts: 0};
        rows.forEach(function (row) {
            const found = stateOf(row, row.getAttribute("data-row"));
            const cell = row.querySelector("[data-role='status']");
            cell.className = "px-4 py-3";
            if (found.state === "create") {
                counts.create += 1;
                cell.innerHTML = badge("bg-emerald-50 text-emerald-700", found.note);
            } else if (found.state === "conflict") {
                counts.conflicts += 1;
                cell.className = "px-4 py-3 bg-rose-50";
                cell.innerHTML = badge("bg-rose-100 text-rose-700", labels.conflictBadge)
                    + '<span class="mt-1 block text-xs text-rose-700">'
                    + escape(found.note) + "</span>";
            } else if (found.state === "already") {
                counts.skipped += 1;
                cell.innerHTML = badge("bg-slate-100 text-slate-600", labels.alreadyBadge)
                    + '<span class="mt-1 block text-xs text-slate-500">'
                    + escape(found.note) + "</span>";
            } else if (found.state === "empty") {
                counts.skipped += 1;
                cell.className = "px-4 py-3 bg-amber-50";
                cell.innerHTML = badge("bg-amber-100 text-amber-800", found.note);
            } else if (found.state === "foreign") {
                counts.skipped += 1;
                cell.className = "px-4 py-3 bg-amber-50";
                cell.innerHTML = badge(
                    "bg-amber-100 text-amber-800", labels.foreignBadge
                ) + '<span class="mt-1 block text-xs text-amber-800">'
                    + escape(found.note) + "</span>";
            } else {
                counts.skipped += 1;
                cell.innerHTML = badge("bg-slate-100 text-slate-500", found.note);
            }
        });
        if (counters.create) {
            counters.create.textContent = String(counts.create);
        }
        if (counters.skipped) {
            counters.skipped.textContent = String(counts.skipped);
        }
        if (counters.conflicts) {
            counters.conflicts.textContent = String(counts.conflicts);
        }
    }

    if (master) {
        master.addEventListener("change", function () {
            // Типичный случай: один заменяющий на все дни — заполняем строки.
            // Строку чужой программы не трогаем: занятие ведёт только
            // преподаватель программы, там заменяющего выбирают отдельно.
            rows.forEach(function (row) {
                const select = row.querySelector("[data-role='teacher']");
                const allowed = select.querySelector(
                    'option[value="' + master.value + '"]'
                );
                if (!master.value || allowed) {
                    select.value = master.value;
                }
            });
            render();
        });
    }
    form.addEventListener("change", function (event) {
        if (event.target !== master) {
            render();
        }
    });

    render();
})();
