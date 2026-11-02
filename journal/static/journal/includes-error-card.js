/* Скрипты страницы: перенесены из шаблона. */
(function () {
"use strict";

    (function () {
        var button = document.querySelector("[data-error-back]");
        if (!button) {
            return;
        }
        button.addEventListener("click", function () {
            // Возвращаемся туда, откуда пришли; если истории нет — на главную.
            if (window.history.length > 1) {
                window.history.back();
            } else {
                window.location.href = "/";
            }
        });
    })();
})();
