/* Скрипты страницы: перенесены из шаблона. */
(function () {
"use strict";

    (function () {
        function generatePassword(length) {
            const lower = "abcdefghijkmnpqrstuvwxyz";
            const upper = "ABCDEFGHJKLMNPQRSTUVWXYZ";
            const digits = "23456789";
            const symbols = "!@#%*+-";
            const all = lower + upper + digits + symbols;
            const pick = (set) => set[Math.floor(Math.random() * set.length)];
            const chars = [pick(lower), pick(upper), pick(digits), pick(symbols)];
            while (chars.length < length) {
                chars.push(pick(all));
            }
            for (let i = chars.length - 1; i > 0; i--) {
                const j = Math.floor(Math.random() * (i + 1));
                const swap = chars[i];
                chars[i] = chars[j];
                chars[j] = swap;
            }
            return chars.join("");
        }

        document.querySelectorAll("[data-password-tools]").forEach(function (root) {
            if (root.dataset.ready) {
                return;
            }
            root.dataset.ready = "1";

            const form = root.closest("form");
            const names = ["password1", "password2", "new_password1", "new_password2"];
            const fields = form
                ? names
                      .map((name) => form.querySelector('input[name="' + name + '"]'))
                      .filter(Boolean)
                : [];
            const output = root.querySelector("[data-password]");
            const copyButton = root.querySelector("[data-copy]");
            const hint = root.querySelector("[data-hint]");
            const generateButton = root.querySelector("[data-generate]");

            generateButton.addEventListener("click", function () {
                const password = generatePassword(12);
                fields.forEach(function (field) {
                    field.value = password;
                    field.dispatchEvent(new Event("input", { bubbles: true }));
                });
                output.textContent = password;
                output.classList.remove("hidden");
                copyButton.classList.remove("hidden");
                hint.classList.remove("hidden");
            });

            copyButton.addEventListener("click", function () {
                const text = output.textContent || "";
                const done = function () {
                    copyButton.textContent = "Скопировано";
                    window.setTimeout(function () {
                        copyButton.textContent = "Скопировать";
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
        });
    })();
})();
