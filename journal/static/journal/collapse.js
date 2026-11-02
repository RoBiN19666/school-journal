/* Плавное раскрытие и закрытие сворачиваемых блоков (details/summary).
   Штатное поведение details меняет высоту рывком, поэтому анимируем её сами. */
(function () {
    "use strict";

    const DURATION = 240;
    const REDUCED = Boolean(
        window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );

    function toggle(details, body) {
        if (details.dataset.collapsing === "1") {
            return;
        }
        const opening = !details.open;

        if (REDUCED) {
            details.open = opening;
            return;
        }

        details.dataset.collapsing = "1";

        if (opening) {
            details.open = true;
            const target = body.scrollHeight;
            body.style.overflow = "hidden";
            body.style.height = "0px";
            body.style.transition = "height " + DURATION + "ms cubic-bezier(0.4, 0, 0.2, 1)";
            window.requestAnimationFrame(function () {
                body.style.height = target + "px";
            });
            window.setTimeout(function () {
                body.style.transition = "";
                body.style.height = "";
                details.dataset.collapsing = "0";
            }, DURATION + 30);
            return;
        }

        const current = body.scrollHeight;
        body.style.overflow = "hidden";
        body.style.height = current + "px";
        body.style.transition = "height " + DURATION + "ms cubic-bezier(0.4, 0, 0.2, 1)";
        window.requestAnimationFrame(function () {
            body.style.height = "0px";
        });
        window.setTimeout(function () {
            details.open = false;
            body.style.transition = "";
            body.style.height = "";
            details.dataset.collapsing = "0";
        }, DURATION + 30);
    }

    function prepare(details) {
        if (details.dataset.collapseReady) {
            return;
        }
        const summary = details.querySelector(":scope > summary");
        if (!summary) {
            return;
        }

        let body = details.querySelector(":scope > [data-collapse-body]");
        if (!body) {
            body = document.createElement("div");
            body.setAttribute("data-collapse-body", "1");
            body.style.overflow = "hidden";
            Array.prototype.slice.call(details.childNodes).forEach(function (node) {
                if (node !== summary) {
                    body.appendChild(node);
                }
            });
            details.appendChild(body);
        }

        details.dataset.collapseReady = "1";
        summary.addEventListener("click", function (event) {
            // Не мешаем переходу по ссылке внутри заголовка, если она есть.
            if (event.target.closest && event.target.closest("a")) {
                return;
            }
            event.preventDefault();
            toggle(details, body);
        });
    }

    function initCollapse() {
        document.querySelectorAll("details").forEach(prepare);
    }

    window.dshInitCollapse = initCollapse;
    initCollapse();
})();
