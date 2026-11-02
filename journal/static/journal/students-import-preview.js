/* Скрипты страницы: перенесены из шаблона. */
(function () {
"use strict";

    (function () {
        const selectAll = document.getElementById("select-all-rows");
        if (selectAll) {
            selectAll.addEventListener("change", function () {
                document.querySelectorAll(".import-row").forEach(function (box) {
                    box.checked = selectAll.checked;
                });
            });
        }
    })();
})();
