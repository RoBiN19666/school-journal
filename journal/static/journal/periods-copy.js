/* Скрипты страницы: перенесены из шаблона. */
(function () {
"use strict";

    (function () {
        const selectAll = document.getElementById("select-all-groups");
        if (selectAll) {
            selectAll.addEventListener("change", function () {
                document.querySelectorAll(".group-checkbox").forEach(function (box) {
                    box.checked = selectAll.checked;
                });
            });
        }
    })();
})();
