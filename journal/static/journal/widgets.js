/* Скрипты журнала: вынесены из base.html, подключаются файлом.
   Конфигурация страницы доступна в window.__APP_CONFIG. */
(function () {
"use strict";

/* --- блок 1 --- */
    // Поиск по спискам и выпадающим спискам: без сторонних библиотек.
    (function () {
        function normalize(value) {
            return (value || "").toString().toLowerCase().replace(/ё/g, "е").trim();
        }

        function filterItems(input) {
            const container = document.querySelector(input.dataset.filterItems);
            if (!container) {
                return;
            }
            const query = normalize(input.value);
            let visible = 0;
            container.querySelectorAll("[data-filter-text]").forEach(function (item) {
                const text = normalize(item.dataset.filterText || item.textContent);
                const match = !query || text.indexOf(query) !== -1;
                item.classList.toggle("hidden", !match);
                if (match) {
                    visible += 1;
                }
            });
            if (input.dataset.filterCounter) {
                const counter = document.querySelector(input.dataset.filterCounter);
                if (counter) {
                    counter.textContent = visible;
                }
            }
        }

        function filterSelect(input) {
            // Можно фильтровать сразу несколько списков: data-filter-select="#a,#b".
            const selectors = (input.dataset.filterSelect || "")
                .split(",")
                .map(function (item) {
                    return item.trim();
                })
                .filter(Boolean);
            const query = normalize(input.value);
            selectors.forEach(function (selector) {
                const select = document.querySelector(selector);
                if (!select) {
                    return;
                }
                if (!select.dataset.allOptions) {
                    select.dataset.allOptions = JSON.stringify(
                        Array.from(select.options).map(function (option) {
                            return {
                                value: option.value,
                                text: option.text,
                                branch: option.dataset.branch || "",
                                disabled: option.disabled,
                            };
                        })
                    );
                }
                // Из списка можно исключить значение другого поля (например, основного преподавателя).
                const excludeSource = select.dataset.filterExclude
                    ? document.querySelector(select.dataset.filterExclude)
                    : null;
                const excluded = excludeSource ? String(excludeSource.value || "") : "";
                const chosen = Array.from(select.selectedOptions || []).map(function (option) {
                    return option.value;
                });
                const options = JSON.parse(select.dataset.allOptions);
                select.innerHTML = "";
                options.forEach(function (option) {
                    const mustKeep = Boolean(option.value) && chosen.indexOf(option.value) !== -1;
                    const matches = !query || normalize(option.text).indexOf(query) !== -1;
                    // Исключённое значение (например, основной преподаватель) не показываем
                    // никогда, даже если оно было отмечено: сервер добавит его сам.
                    if (excluded && option.value === excluded) {
                        return;
                    }
                    if (!matches && !mustKeep) {
                        return;
                    }
                    const element = document.createElement("option");
                    element.value = option.value;
                    element.textContent = option.text;
                    if (option.branch) {
                        element.dataset.branch = option.branch;
                    }
                    if (option.disabled) {
                        element.disabled = true;
                    }
                    if (mustKeep) {
                        element.selected = true;
                    }
                    select.appendChild(element);
                });
                select.dispatchEvent(new Event("filter-changed"));
            });
        }

        // Открытые панели: при открытии одной остальные закрываются.
        const OPEN_PANELS = [];

        function closeOtherPanels(except) {
            while (OPEN_PANELS.length) {
                const item = OPEN_PANELS.pop();
                if (item !== except && typeof item.close === "function") {
                    item.close();
                }
            }
        }

        function registerPanel(panel) {
            OPEN_PANELS.push(panel);
        }

        // Клик в любом свободном месте и Esc закрывают все открытые панели:
        // списки, календари и выбор учебного периода. Нажатия внутри панелей
        // и по их кнопкам сюда не доходят — они останавливают событие.
        if (!window.__panelsOutsideReady) {
            window.__panelsOutsideReady = true;
            document.addEventListener("click", function () {
                closeOtherPanels(null);
            });
            document.addEventListener("keydown", function (event) {
                if (event.key === "Escape") {
                    closeOtherPanels(null);
                }
            });
        }

        function initPickers() {
            // Панель выбора учебного периода: открывается по нажатию, как меню пользователя.
            document.querySelectorAll("[data-period-picker]").forEach(function (picker) {
                if (picker.dataset.pickerReady) {
                    return;
                }
                picker.dataset.pickerReady = "1";
                const toggle = picker.querySelector("[data-period-toggle]");
                const panel = picker.querySelector("[data-period-panel]");
                const caret = picker.querySelector("[data-period-caret]");
                if (!toggle || !panel) {
                    return;
                }
                const openClasses = (panel.dataset.openClasses || "").split(" ").filter(Boolean);
                const closedClasses = ["invisible", "scale-95", "opacity-0"];
                let isOpen = false;
                const entry = { close: function () { setOpen(false); } };
                function setOpen(value) {
                    isOpen = value;
                    openClasses.forEach(function (name) {
                        panel.classList.toggle(name, value);
                    });
                    closedClasses.forEach(function (name) {
                        panel.classList.toggle(name, !value);
                    });
                    toggle.setAttribute("aria-expanded", value ? "true" : "false");
                    if (caret) {
                        caret.classList.toggle("rotate-180", value);
                    }
                    if (value) {
                        registerPanel(entry);
                    }
                }
                toggle.addEventListener("click", function (event) {
                    event.stopPropagation();
                    if (isOpen) {
                        setOpen(false);
                        return;
                    }
                    closeOtherPanels(entry);
                    setOpen(true);
                });
                panel.addEventListener("click", function (event) {
                    event.stopPropagation();
                });
                document.addEventListener("click", function () {
                    if (isOpen) {
                        setOpen(false);
                    }
                });
                document.addEventListener("keydown", function (event) {
                    if (event.key === "Escape" && isOpen) {
                        setOpen(false);
                    }
                });
            });
        }

        function initSelects() {
            // Все выпадающие списки превращаем в свою панель — как выбор учебного периода.
            // Сам <select> остаётся в форме, поэтому отправка данных не меняется.
            document.querySelectorAll("select").forEach(function (select) {
                if (select.dataset.panelReady || select.multiple || select.size > 1) {
                    return;
                }
                if (select.closest("[data-period-picker]") || select.hasAttribute("data-native")) {
                    return;
                }
                select.dataset.panelReady = "1";

                const wrap = document.createElement("div");
                wrap.className = "relative";
                select.parentNode.insertBefore(wrap, select);
                wrap.appendChild(select);
                select.classList.add("sr-only");
                select.setAttribute("tabindex", "-1");

                const toggle = document.createElement("button");
                toggle.type = "button";
                toggle.dataset.selectToggle = "1";
                toggle.className =
                    select.className.replace("sr-only", "").trim() +
                    " flex items-center justify-between gap-2 text-left";
                toggle.setAttribute("aria-haspopup", "listbox");
                toggle.setAttribute("aria-expanded", "false");

                const value = document.createElement("span");
                value.dataset.selectValue = "1";
                value.className = "truncate";
                const caret = document.createElement("span");
                caret.className = "shrink-0 text-slate-400 transition-transform duration-150";
                caret.textContent = "\u25be";
                toggle.appendChild(value);
                toggle.appendChild(caret);
                if (/\btext-xs\b|\bpy-1\b/.test(select.className)) {
                    toggle.dataset.compact = "1";
                }
                wrap.appendChild(toggle);

                const panel = document.createElement("div");
                panel.dataset.selectPanel = "1";
                panel.className =
                    "invisible absolute left-0 z-30 mt-2 max-h-72 min-w-full w-max " +
                    "max-w-[min(22rem,90vw)] origin-top-left scale-95 " +
                    "overflow-y-auto rounded-2xl border border-slate-200 bg-white py-1.5 opacity-0 " +
                    "shadow-lg transition duration-150 ease-out";
                wrap.appendChild(panel);

                function sync() {
                    let option = null;
                    Array.prototype.forEach.call(select.options, function (item) {
                        if (item.value === select.value) {
                            option = item;
                        }
                    });
                    if (!option) {
                        option = select.options[select.selectedIndex];
                    }
                    value.textContent = option ? option.textContent.trim() : "";
                }

                let searchInput = null;
                let applyFilter = null;

                function build() {
                    panel.innerHTML = "";
                    searchInput = null;
                    applyFilter = null;
                    const options = Array.prototype.slice.call(select.options);
                    // Длинный список (ученики, педагоги) делаем с поиском по вводу.
                    if (options.length > 7) {
                        searchInput = document.createElement("input");
                        searchInput.type = "search";
                        searchInput.placeholder = "Начните вводить имя…";
                        searchInput.className =
                            "mb-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 " +
                            "text-sm outline-none transition focus:border-indigo-500";
                        searchInput.addEventListener("click", function (event) {
                            event.stopPropagation();
                        });
                        searchInput.addEventListener("input", function () {
                            if (applyFilter) {
                                applyFilter(searchInput.value);
                            }
                        });
                        searchInput.addEventListener("keydown", function (event) {
                            if (event.key === "Enter") {
                                event.preventDefault();
                                const first = panel.querySelector("[data-select-item]:not(.hidden)");
                                if (first) {
                                    first.click();
                                }
                            }
                        });
                        panel.appendChild(searchInput);
                    }
                    const items = [];
                    options.forEach(function (option) {
                        const item = document.createElement("button");
                        item.type = "button";
                        item.dataset.selectItem = "1";
                        item.dataset.selectText = option.textContent.trim().toLowerCase();
                        item.className =
                            "flex w-full items-center justify-between gap-2 whitespace-nowrap px-3 py-2 " +
                            "text-left text-sm transition hover:bg-slate-50";
                        if (option.selected || option.value === select.value) {
                            item.classList.add("bg-indigo-50", "font-medium", "text-indigo-700");
                        }
                        item.textContent = option.textContent.trim();
                        item.addEventListener("click", function () {
                            select.value = option.value;
                            sync();
                            setOpen(false);
                            select.dispatchEvent(new Event("change", { bubbles: true }));
                        });
                        items.push(item);
                        panel.appendChild(item);
                    });
                    if (!options.length) {
                        const empty = document.createElement("p");
                        empty.className = "px-3 py-2 text-sm text-slate-500";
                        empty.textContent = "Нет вариантов";
                        panel.appendChild(empty);
                    }
                    applyFilter = function (query) {
                        const needle = (query || "").trim().toLowerCase();
                        let visible = 0;
                        items.forEach(function (item) {
                            const match = !needle ||
                                item.dataset.selectText.indexOf(needle) !== -1;
                            item.classList.toggle("hidden", !match);
                            if (match) {
                                visible += 1;
                            }
                        });
                        let hint = panel.querySelector("[data-select-empty]");
                        if (options.length && !visible) {
                            if (!hint) {
                                hint = document.createElement("p");
                                hint.dataset.selectEmpty = "1";
                                hint.className = "px-3 py-2 text-sm text-slate-500";
                                hint.textContent = "Ничего не найдено";
                                panel.appendChild(hint);
                            }
                        } else if (hint) {
                            hint.remove();
                        }
                    };
                }

                let isOpen = false;
                const openClasses = ["visible", "scale-100", "opacity-100"];
                const closedClasses = ["invisible", "scale-95", "opacity-0"];

                function setOpen(next) {
                    isOpen = next;
                    openClasses.forEach(function (name) {
                        panel.classList.toggle(name, next);
                    });
                    closedClasses.forEach(function (name) {
                        panel.classList.toggle(name, !next);
                    });
                    caret.classList.toggle("rotate-180", next);
                    toggle.setAttribute("aria-expanded", next ? "true" : "false");
                    if (next && searchInput) {
                        searchInput.value = "";
                        if (applyFilter) {
                            applyFilter("");
                        }
                        searchInput.focus();
                    }
                }

                const entry = { close: function () { setOpen(false); } };
                toggle.addEventListener("click", function (event) {
                    event.stopPropagation();
                    if (isOpen) {
                        setOpen(false);
                        return;
                    }
                    closeOtherPanels(entry);
                    build();
                    setOpen(true);
                    registerPanel(entry);
                });
                panel.addEventListener("click", function (event) {
                    event.stopPropagation();
                });
                document.addEventListener("click", function () {
                    if (isOpen) {
                        setOpen(false);
                    }
                });
                document.addEventListener("keydown", function (event) {
                    if (event.key === "Escape" && isOpen) {
                        setOpen(false);
                    }
                });
                select.addEventListener("change", sync);
                if (select.disabled) {
                    toggle.disabled = true;
                    toggle.classList.add("cursor-not-allowed", "opacity-60");
                }
                sync();
                build();
            });
        }

        function initDatePickers() {
            // Поля с датой получают свой календарь — с анимацией, как панели списков.
            const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
                "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
            const WEEK = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

            document.querySelectorAll('input[type="date"]').forEach(function (input) {
                if (input.dataset.calendarReady || input.hasAttribute("data-native-date")) {
                    return;
                }
                input.dataset.calendarReady = "1";

                const wrap = document.createElement("div");
                wrap.className = "relative";
                input.parentNode.insertBefore(wrap, input);
                wrap.appendChild(input);
                input.classList.add("sr-only");
                input.setAttribute("tabindex", "-1");

                const toggle = document.createElement("button");
                toggle.type = "button";
                toggle.dataset.dateToggle = "1";
                toggle.className = input.className.replace("sr-only", "").trim() +
                    " flex items-center justify-between gap-2 text-left";
                const shown = document.createElement("span");
                shown.dataset.dateValue = "1";
                shown.className = "truncate";
                const caret = document.createElement("span");
                caret.className = "shrink-0 text-slate-400 transition-transform duration-150";
                caret.textContent = "\u25be";
                toggle.appendChild(shown);
                toggle.appendChild(caret);
                if (/\btext-xs\b|\bpy-1\b/.test(input.className)) {
                    toggle.dataset.compact = "1";
                }
                wrap.appendChild(toggle);

                const panel = document.createElement("div");
                panel.dataset.datePanel = "1";
                panel.className = "invisible absolute left-0 z-40 mt-2 w-72 origin-top-left scale-95 " +
                    "rounded-2xl border border-slate-200 bg-white p-3 opacity-0 shadow-lg " +
                    "transition duration-150 ease-out";
                wrap.appendChild(panel);

                let view = new Date();
                let isOpen = false;

                function pad(value) {
                    return String(value).padStart(2, "0");
                }

                function parse() {
                    const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(input.value || "");
                    return parts ? new Date(+parts[1], +parts[2] - 1, +parts[3]) : null;
                }

                function iso(date) {
                    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
                }

                function text(date) {
                    return pad(date.getDate()) + "." + pad(date.getMonth() + 1) + "." + date.getFullYear();
                }

                function sync() {
                    const date = parse();
                    shown.textContent = date ? text(date) : "дд.мм.гггг";
                }

                function smallButton(label) {
                    const button = document.createElement("button");
                    button.type = "button";
                    button.className = "rounded-lg border border-slate-200 px-2 py-1 text-sm " +
                        "text-slate-600 transition hover:bg-slate-100";
                    button.textContent = label;
                    return button;
                }

                function choose(date) {
                    input.value = iso(date);
                    sync();
                    setOpen(false);
                    input.dispatchEvent(new Event("change", { bubbles: true }));
                }

                function build() {
                    panel.innerHTML = "";
                    const head = document.createElement("div");
                    head.className = "flex items-center justify-between gap-2";
                    const title = document.createElement("p");
                    title.className = "text-sm font-medium text-slate-700";
                    title.textContent = MONTHS[view.getMonth()] + " " + view.getFullYear();
                    const back = smallButton("\u2039");
                    const forward = smallButton("\u203a");
                    back.addEventListener("click", function (event) {
                        event.stopPropagation();
                        view = new Date(view.getFullYear(), view.getMonth() - 1, 1);
                        build();
                    });
                    forward.addEventListener("click", function (event) {
                        event.stopPropagation();
                        view = new Date(view.getFullYear(), view.getMonth() + 1, 1);
                        build();
                    });
                    head.appendChild(back);
                    head.appendChild(title);
                    head.appendChild(forward);
                    panel.appendChild(head);

                    const grid = document.createElement("div");
                    grid.className = "mt-2 grid grid-cols-7 gap-1 text-center";
                    WEEK.forEach(function (name) {
                        const cell = document.createElement("span");
                        cell.className = "py-1 text-xs text-slate-400";
                        cell.textContent = name;
                        grid.appendChild(cell);
                    });
                    const first = new Date(view.getFullYear(), view.getMonth(), 1);
                    const offset = (first.getDay() + 6) % 7;
                    for (let index = 0; index < offset; index += 1) {
                        grid.appendChild(document.createElement("span"));
                    }
                    const total = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
                    const chosen = parse();
                    for (let day = 1; day <= total; day += 1) {
                        const date = new Date(view.getFullYear(), view.getMonth(), day);
                        const cell = document.createElement("button");
                        cell.type = "button";
                        cell.className = "rounded-lg py-1 text-sm text-slate-700 transition hover:bg-slate-100";
                        if (chosen && iso(chosen) === iso(date)) {
                            cell.classList.add("bg-indigo-600", "font-semibold", "text-white");
                        }
                        cell.textContent = String(day);
                        cell.addEventListener("click", function (event) {
                            event.stopPropagation();
                            choose(date);
                        });
                        grid.appendChild(cell);
                    }
                    panel.appendChild(grid);

                    const footer = document.createElement("div");
                    footer.className = "mt-2 flex items-center justify-between border-t border-slate-100 pt-2";
                    const today = smallButton("Сегодня");
                    const clear = smallButton("Очистить");
                    today.addEventListener("click", function (event) {
                        event.stopPropagation();
                        const now = new Date();
                        view = new Date(now.getFullYear(), now.getMonth(), 1);
                        choose(now);
                    });
                    clear.addEventListener("click", function (event) {
                        event.stopPropagation();
                        input.value = "";
                        sync();
                        setOpen(false);
                        input.dispatchEvent(new Event("change", { bubbles: true }));
                    });
                    footer.appendChild(today);
                    footer.appendChild(clear);
                    panel.appendChild(footer);
                }

                const entry = { close: function () { setOpen(false); } };
                const openClasses = ["visible", "scale-100", "opacity-100"];
                const closedClasses = ["invisible", "scale-95", "opacity-0"];

                function setOpen(next) {
                    isOpen = next;
                    openClasses.forEach(function (name) {
                        panel.classList.toggle(name, next);
                    });
                    closedClasses.forEach(function (name) {
                        panel.classList.toggle(name, !next);
                    });
                    caret.classList.toggle("rotate-180", next);
                }

                toggle.addEventListener("click", function (event) {
                    event.stopPropagation();
                    if (isOpen) {
                        setOpen(false);
                        return;
                    }
                    const date = parse();
                    view = date
                        ? new Date(date.getFullYear(), date.getMonth(), 1)
                        : new Date(new Date().getFullYear(), new Date().getMonth(), 1);
                    closeOtherPanels(entry);
                    build();
                    setOpen(true);
                    registerPanel(entry);
                });
                panel.addEventListener("click", function (event) {
                    event.stopPropagation();
                });
                if (input.disabled) {
                    toggle.disabled = true;
                    toggle.classList.add("cursor-not-allowed", "opacity-60");
                }
                // Значение может подставляться программно (например, окончание занятия
                // считается от начала) — тогда обновляем подпись на кнопке.
                input.addEventListener("input", sync);
                input.addEventListener("change", sync);
                sync();
            });
        }



        /* --- Поля времени: своя панель с часами и минутами --- */
        function initTimeInputs() {
            document.querySelectorAll('input[type="time"]').forEach(function (input) {
                if (input.dataset.timeReady || input.hasAttribute("data-native-time")) {
                    return;
                }
                input.dataset.timeReady = "1";

                const wrap = document.createElement("div");
                wrap.className = "relative";
                input.parentNode.insertBefore(wrap, input);
                wrap.appendChild(input);
                input.classList.add("sr-only");
                input.setAttribute("tabindex", "-1");

                const toggle = document.createElement("button");
                toggle.type = "button";
                toggle.dataset.timeToggle = "1";
                toggle.className = input.className.replace("sr-only", "").trim() +
                    " flex items-center justify-between gap-2 text-left";
                const shown = document.createElement("span");
                shown.dataset.timeValue = "1";
                shown.className = "truncate";
                const caret = document.createElement("span");
                caret.className = "shrink-0 text-slate-400 transition-transform duration-150";
                caret.textContent = "\u25be";
                toggle.appendChild(shown);
                toggle.appendChild(caret);
                wrap.appendChild(toggle);

                const panel = document.createElement("div");
                panel.dataset.timePanel = "1";
                panel.className = "invisible absolute left-0 z-40 mt-2 w-56 origin-top-left " +
                    "scale-95 rounded-2xl border border-slate-200 bg-white p-2 opacity-0 " +
                    "shadow-lg transition duration-150 ease-out";
                wrap.appendChild(panel);

                const entry = { close: function () { setOpen(false); } };
                const openClasses = ["visible", "scale-100", "opacity-100"];
                const closedClasses = ["invisible", "scale-95", "opacity-0"];
                let isOpen = false;

                function setOpen(next) {
                    isOpen = next;
                    openClasses.forEach(function (name) {
                        panel.classList.toggle(name, next);
                    });
                    closedClasses.forEach(function (name) {
                        panel.classList.toggle(name, !next);
                    });
                    caret.classList.toggle("rotate-180", next);
                }

                function parts() {
                    const value = (input.value || "").split(":");
                    const hours = value[0] ? parseInt(value[0], 10) : 9;
                    const minutes = value[1] ? parseInt(value[1], 10) : 0;
                    return [isNaN(hours) ? 9 : hours, isNaN(minutes) ? 0 : minutes];
                }

                function sync() {
                    const value = parts();
                    shown.textContent = String(value[0]).padStart(2, "0") + ":" +
                        String(value[1]).padStart(2, "0");
                }

                function apply(hours, minutes) {
                    input.value = String(hours).padStart(2, "0") + ":" +
                        String(minutes).padStart(2, "0");
                    sync();
                    setOpen(false);
                    input.dispatchEvent(new Event("change", { bubbles: true }));
                }

                function build() {
                    panel.innerHTML = "";
                    const title = document.createElement("p");
                    title.className = "px-1 pb-1 text-xs font-medium uppercase tracking-wide " +
                        "text-slate-400";
                    title.textContent = "Время";
                    panel.appendChild(title);

                    const columns = document.createElement("div");
                    columns.className = "grid grid-cols-2 gap-2";
                    const current = parts();

                    [["Часы", 24, current[0], 0], ["Минуты", 12, Math.floor(current[1] / 5), 1]]
                        .forEach(function (config) {
                            const column = document.createElement("div");
                            column.className = "max-h-48 overflow-y-auto rounded-xl " +
                                "border border-slate-100 p-1";
                            for (let index = 0; index < config[1]; index += 1) {
                                const value = config[3] ? index * 5 : index;
                                const item = document.createElement("button");
                                item.type = "button";
                                item.className = "block w-full rounded-lg px-2 py-1 text-center " +
                                    "text-sm transition hover:bg-slate-50";
                                item.textContent = String(value).padStart(2, "0");
                                if (value === (config[3] ? current[1] : current[0])) {
                                    item.classList.add("bg-indigo-50", "font-medium",
                                        "text-indigo-700");
                                }
                                item.addEventListener("click", function (event) {
                                    event.stopPropagation();
                                    const now = parts();
                                    if (config[3]) {
                                        apply(now[0], value);
                                    } else {
                                        apply(value, now[1]);
                                    }
                                });
                                column.appendChild(item);
                            }
                            columns.appendChild(column);
                        });
                    panel.appendChild(columns);

                    const footer = document.createElement("div");
                    footer.className = "mt-2 flex items-center justify-between border-t " +
                        "border-slate-100 pt-2";
                    const now = document.createElement("button");
                    now.type = "button";
                    now.className = "rounded-lg border border-slate-200 px-2 py-1 text-sm " +
                        "text-slate-600 transition hover:bg-slate-100";
                    now.textContent = "Сейчас";
                    now.addEventListener("click", function (event) {
                        event.stopPropagation();
                        const date = new Date();
                        apply(date.getHours(), date.getMinutes());
                    });
                    footer.appendChild(now);
                    panel.appendChild(footer);
                }

                toggle.addEventListener("click", function (event) {
                    event.stopPropagation();
                    if (isOpen) {
                        setOpen(false);
                        return;
                    }
                    closeOtherPanels(entry);
                    build();
                    setOpen(true);
                    registerPanel(entry);
                });
                panel.addEventListener("click", function (event) {
                    event.stopPropagation();
                });
                if (input.disabled) {
                    toggle.disabled = true;
                    toggle.classList.add("cursor-not-allowed", "opacity-60");
                }
                sync();
            });
        }

        /* --- Поиск по списку учебных годов --- */
        function initYearFilter() {
            document.querySelectorAll("[data-year-filter]").forEach(function (input) {
                if (input.dataset.yearFilterReady) {
                    return;
                }
                input.dataset.yearFilterReady = "1";
                const scope = input.closest("[data-year-list]")
                    ? input.closest("[data-year-list]")
                    : document;
                const list = document.querySelector("[data-year-list]") || scope;
                function apply() {
                    const query = input.value.trim().toLowerCase();
                    let visible = 0;
                    list.querySelectorAll("[data-year-item]").forEach(function (item) {
                        const text = (item.dataset.searchText || item.textContent || "").toLowerCase();
                        const match = !query || text.indexOf(query) !== -1;
                        item.classList.toggle("hidden", !match);
                        if (match) {
                            visible += 1;
                        }
                    });
                    const empty = document.querySelector("[data-year-empty]");
                    if (empty) {
                        empty.classList.toggle("hidden", visible !== 0);
                    }
                }
                input.addEventListener("input", apply);
                apply();
            });
        }

        function init() {
            initPickers();
            initSelects();
            initDatePickers();
            initTimeInputs();
            initYearFilter();
            // Мобильное меню: на телефоне панель разделов выезжает по кнопке ☰.
            document.documentElement.removeAttribute("data-mobile-nav-open");
            const navToggle = document.querySelector("[data-mobile-nav-toggle]");
            if (navToggle && !navToggle.dataset.navReady) {
                navToggle.dataset.navReady = "1";
                navToggle.addEventListener("click", function () {
                    const open = document.documentElement.hasAttribute("data-mobile-nav-open");
                    // Перед открытием уточняем, сколько футера видно: панель встанет над ним.
                    if (typeof window.dshMeasureFooter === "function") {
                        window.dshMeasureFooter();
                    }
                if (typeof window.dshMeasureHeader === "function") {
                    window.dshMeasureHeader();
                }
                if (typeof window.dshInitTableSort === "function") {
                    window.dshInitTableSort();
                }
                    document.documentElement.toggleAttribute("data-mobile-nav-open", !open);
                    if (open) {
                        // Закрываем меню: возвращаем страницу в прежнее положение.
                        if (typeof window.dshUnlockPageScroll === "function") {
                            window.dshUnlockPageScroll();
                        }
                    } else if (typeof window.dshLockPageScroll === "function") {
                        // Открываем меню: страница за ним больше не прокручивается,
                        // поэтому футер не наползает на панель.
                        window.dshLockPageScroll();
                    }
                    navToggle.setAttribute("aria-expanded", open ? "false" : "true");
                });
            }
            const navBackdrop = document.querySelector("[data-mobile-nav-backdrop]");
            if (navBackdrop && !navBackdrop.dataset.navReady) {
                navBackdrop.dataset.navReady = "1";
                navBackdrop.addEventListener("click", function () {
                    document.documentElement.removeAttribute("data-mobile-nav-open");
                    if (typeof window.dshUnlockPageScroll === "function") {
                        window.dshUnlockPageScroll();
                    }
                    const toggle = document.querySelector("[data-mobile-nav-toggle]");
                    if (toggle) {
                        toggle.setAttribute("aria-expanded", "false");
                    }
                });
            }
            if (!document.documentElement.dataset.mobileNavEscape) {
                document.documentElement.dataset.mobileNavEscape = "1";
                document.addEventListener("keydown", function (event) {
                    if (event.key === "Escape") {
                        document.documentElement.removeAttribute("data-mobile-nav-open");
                        if (typeof window.dshUnlockPageScroll === "function") {
                            window.dshUnlockPageScroll();
                        }
                    }
                });
            }
            // Списки-фильтры применяются сразу, без отдельной кнопки подтверждения.
            document.querySelectorAll("[data-autosubmit]").forEach(function (field) {
                if (field.dataset.autosubmitReady) {
                    return;
                }
                field.dataset.autosubmitReady = "1";
                const sendForm = function () {
                    const form = field.form || document.getElementById(field.getAttribute("form"));
                    if (!form) {
                        return;
                    }
                    // requestSubmit вызывает обычное событие submit, поэтому страница
                    // обновляется динамически, а не полной перезагрузкой.
                    if (form.requestSubmit) {
                        form.requestSubmit();
                    } else {
                        form.submit();
                    }
                };
                field.addEventListener("change", sendForm);
                // Текстовые поля ищут сразу по всей базе: форма отправляется сама
                // через 300 мс после того, как пользователь перестал печатать.
                if (field.type === "search" || field.type === "text") {
                    let timer = null;
                    field.addEventListener("input", function () {
                        window.clearTimeout(timer);
                        timer = window.setTimeout(sendForm, 300);
                    });
                    field.addEventListener("keydown", function (event) {
                        if (event.key === "Enter") {
                            event.preventDefault();
                            window.clearTimeout(timer);
                            sendForm();
                        }
                    });
                }
            });
            document.querySelectorAll("[data-filter-items]").forEach(function (input) {
                if (input.dataset.filterReady) {
                    return;
                }
                input.dataset.filterReady = "1";
                input.addEventListener("input", function () {
                    filterItems(input);
                });
                keepEnterInside(input);
                filterItems(input);
            });
            document.querySelectorAll("[data-filter-select]").forEach(function (input) {
                if (input.dataset.filterReady) {
                    return;
                }
                input.dataset.filterReady = "1";
                input.addEventListener("input", function () {
                    filterSelect(input);
                });
                keepEnterInside(input);
                // Сразу применяем правила: например, из списка исключаем основного преподавателя.
                filterSelect(input);
            });
        }

        // Enter в строке поиска не должен отправлять форму целиком.
        function keepEnterInside(input) {
            input.addEventListener("keydown", function (event) {
                if (event.key === "Enter") {
                    event.preventDefault();
                }
            });
        }

        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", init);
        } else {
            init();
        }

        // Перерисовка клиентских фильтров после динамического обновления страницы.
        window.dshInitFilters = init;
    })();

/* --- блок 2 --- */
    // Меню у имени пользователя: настройки и выбор темы оформления.
    (function () {
        const menu = document.querySelector("[data-user-menu]");
        if (menu) {
            const toggle = menu.querySelector("[data-user-menu-toggle]");
            const panel = menu.querySelector("[data-user-menu-panel]");
            const caret = menu.querySelector("[data-user-menu-caret]");
            const openClasses = (panel.dataset.openClasses || "").split(" ").filter(Boolean);
            const closedClasses = ["invisible", "scale-95", "opacity-0"];
            let isOpen = false;

            function setOpen(value) {
                isOpen = value;
                openClasses.forEach(function (name) {
                    panel.classList.toggle(name, value);
                });
                closedClasses.forEach(function (name) {
                    panel.classList.toggle(name, !value);
                });
                if (caret) {
                    caret.classList.toggle("rotate-180", value);
                }
                toggle.setAttribute("aria-expanded", value ? "true" : "false");
            }

            toggle.addEventListener("click", function (event) {
                event.stopPropagation();
                setOpen(!isOpen);
            });
            // Клик по пункту меню закрывает его: переход всё равно откроет нужную страницу.
            panel.addEventListener("click", function (event) {
                if (event.target && event.target.closest && event.target.closest("a")) {
                    setOpen(false);
                }
            });
            document.addEventListener("click", function (event) {
                if (isOpen && !menu.contains(event.target)) {
                    setOpen(false);
                }
            });
            document.addEventListener("keydown", function (event) {
                if (event.key === "Escape" && isOpen) {
                    setOpen(false);
                    toggle.focus();
                }
            });
            setOpen(false);
        }

        const cookieName = window.__APP_CONFIG.themeCookie || "theme";
        let currentTheme = document.documentElement.getAttribute("data-theme") || "light";

        function markActive() {
            document.querySelectorAll("[data-theme-option]").forEach(function (button) {
                const isActive = button.dataset.themeOption === currentTheme;
                button.classList.toggle("ring-2", isActive);
                button.classList.toggle("ring-indigo-500", isActive);
                const check = button.querySelector("[data-theme-check]");
                if (check) { check.textContent = isActive ? "✓" : ""; }
            });
        }

        // Выбор темы: кнопки могут прийти и после динамического обновления страницы,
        // поэтому функцию вызываем и при загрузке, и после каждой подмены содержимого.
        function initThemes() {
            document.querySelectorAll("[data-theme-option]").forEach(function (button) {
                if (button.dataset.themeReady) {
                    return;
                }
                button.dataset.themeReady = "1";
                button.addEventListener("click", function () {
                    const value = button.dataset.themeOption;
                    currentTheme = value;
                    document.documentElement.setAttribute("data-theme", value);
                    document.cookie = cookieName + "=" + value
                        + "; path=/; max-age=31536000; SameSite=Lax";
                    markActive();
                });
            });
            markActive();
        }

        window.dshInitThemes = initThemes;
        initThemes();
    })();

/* --- блок 3 --- */
    // Динамическое обновление страницы без перезагрузки: ссылки и GET-формы
    // (поиск, фильтры, календарь занятий) подгружаются и подменяют содержимое.
    // Без JavaScript всё работает как обычно — ссылки и формы обычные.
    (function () {
        const SWAP_IDS = ["page-content", "page-messages", "side-nav", "period-bar"];
        // Исключения мягкой навигации. Вход и выход, демонстрация, админка и скачивание
        // резервной копии требуют полной загрузки: там меняется сессия, база данных
        // или тип ответа (файл вместо страницы).
        const NO_SOFT = ["/backup/download", "/admin/", "/demo/", "/login/", "/logout/",
            "/periods/switch/"];
        // Ссылки на файлы нельзя подменять разметкой — их открываем обычным способом.
        const FILE_EXTENSIONS = [".xlsx", ".xls", ".csv", ".json", ".enc", ".zip", ".png",
            ".jpg", ".jpeg", ".pdf"];
        const SCRIPT_SCOPE = "page-content";
        const FRAME_VERSION = "";
        const PREFERS_REDUCED = Boolean(
            window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches
        );

        function isSoftAllowed(url) {
            const path = ((url && url.pathname) || "").toLowerCase();
            if (FILE_EXTENSIONS.some(function (extension) {
                return path.endsWith(extension);
            })) {
                return false;
            }
            if (url.origin !== window.location.origin) {
                return false;
            }
            return !NO_SOFT.some(function (prefix) {
                return url.pathname.indexOf(prefix) === 0;
            });
        }

        // Скрипты, вставленные через DOM, браузер не выполняет — пересоздаём их вручную.
        // Ищем их в уже обновлённой странице: при подмене узлы переносятся из разобранного
        // документа, поэтому там скриптов уже нет. Скрипты самого каркаса не трогаем —
        // они лежат вне области содержимого.
        function runScripts() {
            const scope = document.getElementById(SCRIPT_SCOPE);
            if (!scope) {
                return;
            }
            scope.querySelectorAll("script").forEach(function (old) {
                if (old.dataset.softRan) {
                    return;
                }
                const fresh = document.createElement("script");
                Array.from(old.attributes).forEach(function (attribute) {
                    fresh.setAttribute(attribute.name, attribute.value);
                });
                fresh.dataset.softRan = "1";
                fresh.textContent = old.textContent;
                if (old.replaceWith) {
                    old.replaceWith(fresh);
                } else {
                    scope.appendChild(fresh);
                }
            });
        }

        // Переход к якорю на странице (#раздел) — после подмены содержимого.
        function scrollToAnchor(hash, smooth) {
            if (!hash || hash === "#") {
                return false;
            }
            const target = document.getElementById(hash.slice(1));
            if (!target) {
                return false;
            }
            target.scrollIntoView(smooth === false ? true : { behavior: "smooth", block: "start" });
            return true;
        }

        function applyDocument(html, url, push) {
            const parsed = new DOMParser().parseFromString(html, "text/html");
            if (!parsed.getElementById("page-content")) {
                window.location.href = url;
                return;
            }
            // Журнал обновили — старый каркас уступает место новому обычной загрузкой.
            const version = parsed.querySelector('meta[name="app-version"]');
            if (version && version.getAttribute("content") !== FRAME_VERSION) {
                window.location.href = url;
                return;
            }
            // Запоминаем поле, в котором стоял курсор: при подмене содержимого
            // поле пересоздаётся, и без этого живой поиск терял фокус при каждом вводе.
            let caret = null;
            const active = document.activeElement;
            if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA") &&
                    active.type !== "checkbox" && active.type !== "radio") {
                caret = {
                    id: active.id,
                    name: active.getAttribute("name"),
                    start: typeof active.selectionStart === "number" ? active.selectionStart : null,
                    end: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
                };
            }
            SWAP_IDS.forEach(function (id) {
                const fresh = parsed.getElementById(id);
                const current = document.getElementById(id);
                if (fresh && current) {
                    current.replaceChildren.apply(current, Array.from(fresh.childNodes));
                }
            });
            document.title = parsed.title || document.title;
            if (push) {
                history.pushState({ soft: true }, "", url);
            }
            const hash = (url.split("#")[1] || "").trim();
            // Если открывается та же страница с другими параметрами (день, месяц, фильтр),
            // прокрутку не сбрасываем — переключение выглядит как обновление на месте.
            const samePath = url.split("?")[0].split("#")[0] === window.location.pathname;
            if (!hash && !samePath) {
                window.scrollTo({ top: 0 });
            }
            runScripts();
            if (typeof window.dshInitFilters === "function") {
                window.dshInitFilters();
            }
            if (typeof window.dshInitThemes === "function") {
                window.dshInitThemes();
            }
                if (typeof window.dshInitCollapse === "function") {
                    window.dshInitCollapse();
                }
                if (typeof window.dshRefreshCsrfTokens === "function") {
                    window.dshRefreshCsrfTokens();
                }
                // После подмены страницы пересчитываем высоту шапки и футера:
                // от них зависит положение выезжающего меню.
                if (typeof window.dshMeasureHeader === "function") {
                    window.dshMeasureHeader();
                }
                if (typeof window.dshMeasureFooter === "function") {
                    window.dshMeasureFooter();
                }
                if (typeof window.dshInitTableSort === "function") {
                    window.dshInitTableSort();
                }
                // Разблокировка после подмены страницы: если меню было открыто,
                // страница оставалась зафиксированной и прокрутка не работала.
                if (typeof window.dshUnlockPageScroll === "function") {
                    window.dshUnlockPageScroll();
                }
            // Возвращаем курсор в поле ввода: иначе живой поиск терял фокус при вводе.
            if (caret) {
                let field = caret.id ? document.getElementById(caret.id) : null;
                if (!field && caret.name) {
                    field = document.querySelector('[name="' + caret.name + '"]');
                }
                if (field && field.offsetParent !== null) {
                    field.focus();
                    if (caret.start !== null) {
                        try {
                            field.setSelectionRange(caret.start, caret.end);
                        } catch (error) {
                            // Для некоторых типов полей выделение недоступно — не критично.
                        }
                    }
                }
            }
            if (hash) {
                scrollToAnchor("#" + hash);
            }
            // Плавное появление содержимого после подмены.
            const content = document.getElementById(SCRIPT_SCOPE);
            const animated = content && !PREFERS_REDUCED;
            document.body.classList.add("dsh-swapped");
            if (animated) {
                content.style.transition = "opacity .18s ease, transform .18s ease";
                content.style.opacity = "0";
                content.style.transform = "translateY(5px)";
                window.requestAnimationFrame(function () {
                    content.style.opacity = "1";
                    content.style.transform = "none";
                });
                window.setTimeout(function () {
                    content.style.transition = "";
                    content.style.transform = "";
                }, 260);
            }
            window.setTimeout(function () {
                document.body.classList.remove("dsh-swapped");
            }, 220);
        }

        function load(url, push) {
            document.body.classList.add("is-loading");
            fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
                .then(function (response) {
                    const type = response.headers.get("Content-Type") || "";
                    if (!response.ok || type.indexOf("text/html") === -1) {
                        window.location.href = url;
                        return null;
                    }
                    return response.text();
                })
                .then(function (html) {
                    if (html !== null) {
                        applyDocument(html, url, push);
                    }
                })
                .catch(function () {
                    window.location.href = url;
                })
                .then(function () {
                    document.body.classList.remove("is-loading");
                });
        }

        // GET-формы: поиск и фильтры.
        document.addEventListener("submit", function (event) {
            const form = event.target;
            if (!(form instanceof HTMLFormElement) || form.dataset.noSoft !== undefined) {
                return;
            }
            if ((form.getAttribute("method") || "get").toLowerCase() !== "get") {
                return;  // POST сохраняет данные — обычная отправка
            }
            const action = form.getAttribute("action") || window.location.pathname;
            const target = new URL(action, window.location.origin);
            if (!isSoftAllowed(target)) {
                return;
            }
            event.preventDefault();
            const params = new URLSearchParams();
            new FormData(form).forEach(function (value, key) {
                if (String(value) !== "") {
                    params.append(key, value);
                }
            });
            const query = params.toString();
            load(target.pathname + (query ? "?" + query : ""), true);
        });

        // Ссылки внутри приложения.
        document.addEventListener("click", function (event) {
            const link = event.target.closest ? event.target.closest("a") : null;
            if (!link || link.dataset.noSoft !== undefined || link.hasAttribute("download")) {
                return;
            }
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
                return;
            }
            if (link.target && link.target !== "_self") {
                return;
            }
            const url = new URL(link.href, window.location.origin);
            if (!isSoftAllowed(url)) {
                return;
            }
            // Якорь на этой же странице (#раздел) — обычный переход браузера.
            const here = window.location.pathname.replace(/\/+$/, "");
            const target = url.pathname.replace(/\/+$/, "");
            if (url.hash && url.search === window.location.search
                && (target === here || document.getElementById(url.hash.slice(1)))) {
                return;
            }
            event.preventDefault();
            load(url.pathname + url.search + (url.hash || ""), true);
        });

        window.addEventListener("popstate", function () {
            // Возврат по истории на якорь этой же страницы — просто прокрутка, без загрузки.
            const hash = window.location.hash;
            if (hash && scrollToAnchor(hash, false)) {
                return;
            }
            load(window.location.pathname + window.location.search, false);
        });

        // Переход по якорю (#раздел): прокручиваем, ничего не перезагружая.
        window.addEventListener("hashchange", function () {
            scrollToAnchor(window.location.hash);
        });

        const style = document.createElement("style");
        style.textContent =
            "html{scrollbar-gutter:stable}" +                     // страница не «прыгает» вбок
            "body.is-loading{cursor:progress}";                   // без мигания: только курсор
        document.head.appendChild(style);
    })();
})();

/* --- Клавиатура для панелей: списки, календари, учебный период ---------------
   Обработчик навешен на документ (делегирование), поэтому работает и после
   подмены страницы мягкой навигацией. */
(function () {
    "use strict";

    const PANEL_SELECTOR = "[data-select-panel], [data-date-panel], [data-period-panel], [data-time-panel]";
    const TOGGLE_SELECTOR = "[data-select-toggle], [data-date-toggle], [data-period-toggle], [data-time-toggle]";

    function wrapOf(element) {
        return element ? element.closest(".relative") : null;
    }

    function panelOf(toggle) {
        const wrap = wrapOf(toggle);
        return wrap ? wrap.querySelector(PANEL_SELECTOR) : null;
    }

    function isOpen(panel) {
        return panel && panel.classList.contains("visible");
    }

    function itemsOf(panel) {
        return Array.prototype.slice.call(panel.querySelectorAll("button")).filter(function (item) {
            return !item.disabled;
        });
    }

    function focusItem(panel, index) {
        const items = itemsOf(panel);
        if (!items.length) {
            return;
        }
        const position = Math.max(0, Math.min(index, items.length - 1));
        items[position].focus();
    }

    document.addEventListener("keydown", function (event) {
        const target = event.target;
        if (!target || !target.closest) {
            return;
        }
        const toggle = target.closest(TOGGLE_SELECTOR);
        const panel = target.closest(PANEL_SELECTOR);

        // Клавиши на кнопке панели: открыть, закрыть, войти в список.
        if (toggle) {
            const own = panelOf(toggle);
            if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                if (!isOpen(own)) {
                    toggle.click();
                    window.setTimeout(function () {
                        focusItem(own, 0);
                    }, 0);
                } else {
                    focusItem(own, 0);
                }
                return;
            }
            if (event.key === "ArrowUp") {
                event.preventDefault();
                if (!isOpen(own)) {
                    toggle.click();
                }
                window.setTimeout(function () {
                    const items = itemsOf(own);
                    focusItem(own, items.length - 1);
                }, 0);
                return;
            }
            if (event.key === "Escape" && isOpen(own)) {
                event.preventDefault();
                toggle.click();
                return;
            }
        }

        // Клавиши внутри открытой панели.
        if (panel && isOpen(panel)) {
            const items = itemsOf(panel);
            const index = items.indexOf(document.activeElement);
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const step = event.key === "ArrowDown" ? 1 : -1;
                let next = index + step;
                if (next < 0) {
                    next = items.length - 1;
                }
                if (next >= items.length) {
                    next = 0;
                }
                focusItem(panel, next);
                return;
            }
            if (event.key === "Home" || event.key === "End") {
                event.preventDefault();
                focusItem(panel, event.key === "Home" ? 0 : items.length - 1);
                return;
            }
            if (event.key === "Escape") {
                event.preventDefault();
                const escapeToggle = wrapOf(panel)
                    ? wrapOf(panel).querySelector(TOGGLE_SELECTOR)
                    : null;
                if (escapeToggle) {
                    escapeToggle.click();
                    escapeToggle.focus();
                }
                return;
            }
            if (event.key === "Tab") {
                // Уходим из панели — закрываем её.
                const toggleForPanel = wrapOf(panel)
                    ? wrapOf(panel).querySelector(TOGGLE_SELECTOR)
                    : null;
                if (toggleForPanel) {
                    toggleForPanel.click();
                }
                return;
            }
            if (event.key === "PageUp" || event.key === "PageDown") {
                // В календаре — переключение месяца.
                const monthButton = panel.querySelector("button");
                if (monthButton) {
                    event.preventDefault();
                    const buttons = itemsOf(panel);
                    const step = event.key === "PageUp" ? -1 : 1;
                    const target2 = step < 0 ? buttons[0] : buttons[1];
                    if (target2) {
                        target2.click();
                    }
                }
            }
        }
    });
})();

        /* --- Закрытие всплывающего уведомления --- */
        document.addEventListener("click", function (event) {
            const button = event.target && event.target.closest
                ? event.target.closest("[data-toast-close]")
                : null;
            if (!button) {
                return;
            }
            const toast = button.closest("[data-toast]");
            if (toast) {
                toast.remove();
            }
        });

        /* --- Токены CSRF в разметке должны совпадать с текущей cookie ---
           Боковое меню не перерисовывается при мягкой навигации, поэтому токен
           в форме выхода мог остаться от прошлой сессии и давал ошибку 403. */
        function csrfTokenFromCookie() {
            const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
            return match ? decodeURIComponent(match[1]) : "";
        }

        function refreshCsrfTokens() {
            const token = csrfTokenFromCookie();
            if (!token) {
                return;
            }
            document.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach(function (input) {
                if (input.value !== token) {
                    input.value = token;
                }
            });
        }

        window.dshRefreshCsrfTokens = refreshCsrfTokens;
        refreshCsrfTokens();

        /* --- Высота футера как переменная CSS ---
           Её используют мобильное меню и другие элементы: панель заканчивается
           над футером, поэтому новые пункты не могут его перекрыть. */
        function measureFooter() {
            const footer = document.querySelector("footer");
            // Полная высота подписи: столько же запаса нужно странице,
            // потому что подпись закреплена у нижнего края экрана.
            const height = footer ? Math.round(footer.getBoundingClientRect().height) : 0;
            document.documentElement.style.setProperty("--app-footer-height", height + "px");
        }

        window.dshMeasureFooter = measureFooter;
        measureFooter();
        window.addEventListener("resize", measureFooter);
        if (document.fonts && document.fonts.ready) {
            document.fonts.ready.then(measureFooter).catch(function () {});
        }

        /* --- Блокировка прокрутки страницы при открытом меню ---
           Просто overflow: hidden на body в мобильных браузерах не работает:
           прокручивается сам документ, поэтому футер «наползал» на открытое меню.
           Запоминаем позицию и фиксируем body — это надёжно и на iOS. */
        function lockPageScroll() {
            const root = document.documentElement;
            if (root.dataset.scrollLocked === "1") {
                return;
            }
            root.dataset.scrollLocked = "1";
            root.dataset.scrollPosition = String(window.scrollY || window.pageYOffset || 0);
            // Прокрутку запрещаем только стилями (overflow у html и body).
            // Фиксировать body нельзя: тогда закреплённая шапка уезжает вверх,
            // и при прокрутке страницы меню оказывается поверх неё.
        }

        function unlockPageScroll() {
            const root = document.documentElement;
            if (root.dataset.scrollLocked !== "1") {
                return;
            }
            delete root.dataset.scrollLocked;
            // Стили на всякий случай снимаем: если они остались от прежней версии,
            // страница перестала бы прокручиваться.
            document.body.style.position = "";
            document.body.style.top = "";
            document.body.style.left = "";
            document.body.style.right = "";
            document.body.style.width = "";
        }

        window.dshLockPageScroll = lockPageScroll;
        window.dshUnlockPageScroll = unlockPageScroll;

        /* --- Закрываем меню при нажатии вне панели ---
           Раньше закрытие было только по тёмной подложке, но она уже панели,
           поэтому нажатие в свободное место меню не закрывало. */
        document.addEventListener("click", function (event) {
            if (!document.documentElement.hasAttribute("data-mobile-nav-open")) {
                return;
            }
            const target = event.target;
            const aside = document.querySelector("aside");
            const toggle = document.querySelector("[data-mobile-nav-toggle]");
            if (aside && aside.contains(target)) {
                return;
            }
            if (toggle && toggle.contains(target)) {
                return;
            }
            document.documentElement.removeAttribute("data-mobile-nav-open");
            if (toggle) {
                toggle.setAttribute("aria-expanded", "false");
            }
            if (typeof window.dshUnlockPageScroll === "function") {
                window.dshUnlockPageScroll();
            }
        });

        /* --- Высота шапки как переменная CSS ---
           Панель меню начинается под шапкой, поэтому на узких экранах (когда шапка
           выше из-за переноса строк) меню её не накрывает. */
        function measureHeader() {
            const header = document.querySelector("header");
            const banner = document.querySelector("[data-demo-banner]");
            const height = header ? Math.round(header.getBoundingClientRect().height) : 0;
            const bannerHeight = banner
                ? Math.round(banner.getBoundingClientRect().height)
                : 0;
            const periodBar = document.getElementById("period-bar");
            const periodHeight = periodBar
                ? Math.round(periodBar.getBoundingClientRect().height)
                : 0;
            document.documentElement.style.setProperty("--app-header-height", height + "px");
            document.documentElement.style.setProperty(
                "--app-demo-height", bannerHeight + "px"
            );
            // Вся стопка закреплённых панелей: шапка + надпись демо + панель периода.
            document.documentElement.style.setProperty(
                "--app-top-stack", height + bannerHeight + periodHeight + "px"
            );
        }

        window.dshMeasureHeader = measureHeader;
        measureHeader();
        window.addEventListener("resize", measureHeader);

        /* На всякий случай: если меню закрыто, а страница осталась зафиксированной
           (например, после перехода), возвращаем прокрутку. */
        window.addEventListener("pageshow", function () {
            if (!document.documentElement.hasAttribute("data-mobile-nav-open") &&
                    typeof window.dshUnlockPageScroll === "function") {
                window.dshUnlockPageScroll();
            }
        });

        /* --- Сортировка таблиц по клику на заголовок ---
           Работает у таблиц с атрибутом data-sortable. Числа сортируются как числа,
           даты — по скрытому значению (data-sort-value), остальное — по тексту.
           У служебных столбцов ставим data-sort-skip. */
        function initTableSort() {
            document.querySelectorAll("table[data-sortable]").forEach(function (table) {
                if (table.dataset.sortReady) {
                    return;
                }
                table.dataset.sortReady = "1";
                const headers = Array.prototype.slice.call(table.querySelectorAll("thead th"));
                headers.forEach(function (th, index) {
                    if (th.hasAttribute("data-sort-skip")) {
                        return;
                    }
                    th.classList.add("cursor-pointer", "select-none");
                    th.setAttribute("role", "button");
                    th.setAttribute("tabindex", "0");
                    th.title = "Сортировать по столбцу";

                    function sort() {
                        const tbody = table.querySelector("tbody");
                        if (!tbody) {
                            return;
                        }
                        const direction = th.dataset.sortDirection === "asc" ? "desc" : "asc";
                        headers.forEach(function (other) {
                            delete other.dataset.sortDirection;
                            other.removeAttribute("aria-sort");
                            const mark = other.querySelector("[data-sort-mark]");
                            if (mark) {
                                mark.remove();
                            }
                        });
                        th.dataset.sortDirection = direction;
                        th.setAttribute("aria-sort", direction === "asc" ? "ascending" : "descending");
                        const mark = document.createElement("span");
                        mark.dataset.sortMark = "1";
                        mark.className = "ml-1 text-slate-400";
                        mark.textContent = direction === "asc" ? "▲" : "▼";
                        th.appendChild(mark);

                        const rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
                        function cellValue(row) {
                            const cell = row.children[index];
                            if (!cell) {
                                return "";
                            }
                            if (cell.dataset.sortValue) {
                                return cell.dataset.sortValue;
                            }
                            return (cell.textContent || "").trim();
                        }
                        rows.sort(function (first, second) {
                            const left = cellValue(first);
                            const right = cellValue(second);
                            const leftNumber = Number(left.replace("\s", "").replace(",", "."));
                            const rightNumber = Number(right.replace("\s", "").replace(",", "."));
                            let result;
                            if (left !== "" && right !== "" && !isNaN(leftNumber) && !isNaN(rightNumber)) {
                                result = leftNumber - rightNumber;
                            } else {
                                result = left.localeCompare(right, "ru", { numeric: true });
                            }
                            return direction === "asc" ? result : -result;
                        });
                        rows.forEach(function (row) {
                            tbody.appendChild(row);
                        });
                    }

                    th.addEventListener("click", sort);
                    th.addEventListener("keydown", function (event) {
                        if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            sort();
                        }
                    });
                });
            });
        }

        window.dshInitTableSort = initTableSort;
        initTableSort();

        /* Выравнивание открытых панелей по экрану.
           Панели (календарь, списки выбора) раскрываются вправо от своего поля.
           Если поле стоит у правого края, панель не помещается — тогда
           прижимаем её к правому краю поля, чтобы она влезла целиком. */
        function alignOpenPanels() {
            const margin = 8;
            const viewport = document.documentElement.clientWidth || window.innerWidth;
            document.querySelectorAll("div[class*='absolute'][class*='left-0']").forEach(function (panel) {
                if (panel.classList.contains("invisible") || !panel.offsetParent) {
                    return;
                }
                panel.style.left = "";
                panel.style.right = "";
                const rect = panel.getBoundingClientRect();
                const width = rect.width || panel.offsetWidth;
                if (!width) {
                    // Панель ещё не построена — попробуем при следующем вызове.
                    return;
                }
                // Считаем положение относительно обёртки, в которой лежит панель.
                const wrap = panel.offsetParent || document.body;
                const wrapRect = wrap.getBoundingClientRect();
                let left = rect.left - wrapRect.left;
                if (rect.right > viewport - margin) {
                    left = (viewport - margin - width) - wrapRect.left;
                }
                // И не даём уехать за левый край — иначе широкая панель
                // (например, выбор полугодия на телефоне) уходит за экран.
                const leftLimit = margin - wrapRect.left;
                if (left < leftLimit) {
                    left = leftLimit;
                }
                panel.style.left = Math.round(left) + "px";
                panel.style.right = "auto";
            });
        }

        window.dshAlignOpenPanels = alignOpenPanels;

        /* Панели (календарь, списки выбора) строятся в момент клика, поэтому
           выравниваем и сразу, и через мгновение — когда размеры уже известны. */
        function scheduleAlignPanels() {
            window.requestAnimationFrame(alignOpenPanels);
            window.setTimeout(alignOpenPanels, 60);
        }

        document.addEventListener("click", scheduleAlignPanels, true);
        document.addEventListener("focusin", scheduleAlignPanels, true);

        document.addEventListener("click", function () {
            window.requestAnimationFrame(alignOpenPanels);
        }, true);
