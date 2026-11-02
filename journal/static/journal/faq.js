/* Скрипты страницы: перенесены из шаблона. */
(function () {
"use strict";

    // Подсветка текущего раздела в содержании при прокрутке.
    (function () {
        const toc = document.getElementById("faq-toc");
        const body = document.getElementById("faq-body");
        if (!toc || !body) {
            return;
        }
        const links = Array.from(toc.querySelectorAll('a[href^="#"]'));
        const pairs = links
            .map(function (link) {
                const section = document.getElementById(link.getAttribute("href").slice(1));
                return section ? { link: link, section: section } : null;
            })
            .filter(Boolean);
        if (!pairs.length) {
            return;
        }

        function highlight() {
            // Порог берём от реальной высоты шапки: она закреплена сверху и закрывает
            // часть экрана. Иначе подсветка «отстаёт» и отмечает пункт выше нужного.
            const header = document.querySelector("header.sticky") ||
                document.querySelector("header");
            const headerHeight = header ? header.getBoundingClientRect().height : 72;
            const padding = parseFloat(
                getComputedStyle(document.documentElement).scrollPaddingTop
            ) || 88;
            const threshold = Math.max(headerHeight, padding) + 24;
            let current = pairs[0];
            pairs.forEach(function (pair) {
                if (pair.section.getBoundingClientRect().top <= threshold) {
                    current = pair;
                }
            });
            if (window.scrollY + window.innerHeight >= document.body.scrollHeight - 4) {
                current = pairs[pairs.length - 1];
            }
            pairs.forEach(function (pair) {
                const active = pair === current;
                pair.link.classList.toggle("text-indigo-700", active);
                pair.link.classList.toggle("font-medium", active);
                pair.link.classList.toggle("underline", active);
                pair.link.setAttribute("aria-current", active ? "true" : "false");
            });
        }

        // Прошлый обработчик снимаем: страница могла прийти динамической подменой.
        if (window.__faqSpy) {
            window.removeEventListener("scroll", window.__faqSpy);
        }
        let waiting = false;
        window.__faqSpy = function () {
            if (waiting) {
                return;
            }
            waiting = true;
            window.requestAnimationFrame(function () {
                waiting = false;
                highlight();
            });
        };
        window.addEventListener("scroll", window.__faqSpy, { passive: true });
        window.addEventListener("resize", window.__faqSpy);
        highlight();
    })();
})();
