/* Скрипты страницы: перенесены из шаблона. */
(function () {
"use strict";

        (function () {
            var button = document.querySelector("[data-error-back]");
            if (!button) { return; }
            button.addEventListener("click", function () {
                if (window.history.length > 1) { window.history.back(); }
                else { window.location.href = "/login/"; }
            });
        })();
    
})();
