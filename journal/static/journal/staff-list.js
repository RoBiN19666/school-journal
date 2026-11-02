/* Скрипты страницы: перенесены из шаблона. */
(function () {
"use strict";

        (function () {
            const button = document.querySelector("[data-copy-issued]");
            const source = document.querySelector("[data-issued-password]");
            if (!button || !source) {
                return;
            }
            button.addEventListener("click", function () {
                const text = source.textContent.trim();
                const done = function () {
                    button.textContent = "Скопировано";
                    window.setTimeout(function () {
                        button.textContent = "Скопировать";
                    }, 2000);
                };
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(done, function () {
                        window.prompt("Скопируйте пароль:", text);
                    });
                } else {
                    window.prompt("Скопируйте пароль:", text);
                }
            });
        })();
    
})();
