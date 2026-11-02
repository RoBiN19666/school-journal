/* Скрипты страницы: перенесены из шаблона. */
(function () {
"use strict";

    (function () {
        const button = document.querySelector("[data-author-egg]");
        if (!button) {
            return;
        }
        const caption = button.querySelector("[data-egg-caption]");
        const reduced = window.matchMedia
            && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const GOLD = ["#f6d365", "#f7d774", "#e9b308", "#ffd166", "#fff3b0", "#d99b00", "#ffe9a8"];
        const PHRASES = [
            "Сделано с душой ❤️",
            "Спасибо, что пользуетесь! 🙏",
            "Ты нашёл пасхалку! 🥚",
            "Для своих — с любовью 💛",
            "А ты любопытный! 👀",
            "Секретный уровень разблокирован 🔓",
            "Осторожно, тут живёт магия ✨",
            "Hello, World! 🌍",
            "Никаких багов, только фичи 🐞",
            "Работает — не трогай 🔧",
            "Я тебя могну 💫",
            "Вжух и ты спишь 😴",
            "Ъуъ 🐱",
            "Код, который не стыдно показать",
            "Проверено на живых занятиях 🏫",
            "Один разработчик, одна система, тысячи отметок 📊",
            "Собрано вручную, работает автоматически ⚙️",
            "Посещаемость — наше всё 📚",
            "Один клик — и ребёнок учтён ✅",
            "Журнал помнит всё 🧠",
            "Отечественная разработка (почти) 🇷🇺",
            "Не Гугл, но и не 1С 😏"
        ];
        const TOTAL = 3600;   // вся анимация
        let running = false;
        let lastPhrase = -1;

        // Подпись выбирается случайно, но одна и та же не выпадает дважды подряд.
        function pickPhrase() {
            let index = Math.floor(Math.random() * PHRASES.length);
            if (PHRASES.length > 1 && index === lastPhrase) {
                index = (index + 1) % PHRASES.length;
            }
            lastPhrase = index;
            return PHRASES[index];
        }

        function showCaption(text, hold) {
            if (!caption) {
                return;
            }
            caption.textContent = text;
            caption.classList.add("is-visible");
            window.setTimeout(function () {
                caption.classList.remove("is-visible");
            }, hold);
        }

        function drawStar(ctx, size) {
            ctx.beginPath();
            for (let i = 0; i < 8; i++) {
                const radius = i % 2 === 0 ? size : size * 0.42;
                const angle = (Math.PI / 4) * i - Math.PI / 2;
                const x = Math.cos(angle) * radius;
                const y = Math.sin(angle) * radius;
                if (i === 0) {
                    ctx.moveTo(x, y);
                } else {
                    ctx.lineTo(x, y);
                }
            }
            ctx.closePath();
            ctx.fill();
        }

        function finish() {
            button.classList.remove("is-running");
            running = false;
        }

        // Спокойный вариант для «уменьшить движение»: мягкая пульсация и подпись.
        function calmRun() {
            button.classList.add("is-running");
            window.setTimeout(finish, 2000);
        }

        function fireworks() {
            const rect = button.getBoundingClientRect();
            const canvas = document.createElement("canvas");
            canvas.className = "egg-canvas";
            document.body.appendChild(canvas);
            const ctx = canvas.getContext("2d");
            const dpr = Math.min(window.devicePixelRatio || 1, 2);

            function resize() {
                canvas.width = Math.max(1, Math.floor(window.innerWidth * dpr));
                canvas.height = Math.max(1, Math.floor(window.innerHeight * dpr));
                canvas.style.width = window.innerWidth + "px";
                canvas.style.height = window.innerHeight + "px";
            }
            resize();
            window.addEventListener("resize", resize);

            const small = window.innerWidth < 560;
            const count = small ? 80 : 140;
            const originX = (rect.left + rect.width / 2) * dpr;
            const originY = (rect.top + rect.height / 2) * dpr;
            const particles = [];
            for (let i = 0; i < count; i++) {
                const angle = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.25;
                const speed = (2.2 + Math.random() * 6) * dpr;
                const roll = Math.random();
                const kind = roll < 0.32 ? "ribbon" : (roll < 0.6 ? "star" : "spark");
                particles.push({
                    x: originX,
                    y: originY,
                    vx: Math.cos(angle) * speed,
                    vy: Math.sin(angle) * speed - 2.1 * dpr,
                    size: (kind === "ribbon" ? 3.5 + Math.random() * 3.5 : 1.7 + Math.random() * 2.4) * dpr,
                    color: GOLD[Math.floor(Math.random() * GOLD.length)],
                    kind: kind,
                    rot: Math.random() * Math.PI,
                    spin: (Math.random() - 0.5) * 0.28,
                    age: 0,
                    ttl: 1.5 + Math.random() * 1.6
                });
            }

            const started = window.performance ? window.performance.now() : Date.now();
            let last = started;

            function frame(timestamp) {
                const now = timestamp || (window.performance ? window.performance.now() : Date.now());
                const delta = Math.min(2.5, Math.max(0.4, (now - last) / 16.7));  // кадронезависимо
                last = now;
                const elapsed = now - started;
                const fade = elapsed > TOTAL - 650 ? Math.max(0, (TOTAL - elapsed) / 650) : 1;

                ctx.clearRect(0, 0, canvas.width, canvas.height);
                particles.forEach(function (particle) {
                    particle.age += delta / 60;
                    particle.vy += 0.15 * dpr * delta;
                    particle.vx *= Math.pow(0.986, delta);
                    particle.vy *= Math.pow(0.992, delta);
                    particle.x += particle.vx * delta;
                    particle.y += particle.vy * delta;
                    particle.rot += particle.spin * delta;

                    const alpha = Math.max(0, 1 - particle.age / particle.ttl);
                    if (alpha <= 0) {
                        return;
                    }
                    ctx.save();
                    ctx.globalAlpha = fade * alpha;
                    ctx.translate(particle.x, particle.y);
                    ctx.rotate(particle.rot);
                    ctx.fillStyle = particle.color;
                    if (particle.kind !== "ribbon") {
                        ctx.shadowColor = "rgba(233, 179, 8, .6)";
                        ctx.shadowBlur = 9 * dpr;
                    }
                    if (particle.kind === "ribbon") {
                        ctx.fillRect(-particle.size / 2, -particle.size * 1.3,
                                     particle.size, particle.size * 2.6);
                    } else if (particle.kind === "star") {
                        drawStar(ctx, particle.size * 1.5);
                    } else {
                        ctx.beginPath();
                        ctx.arc(0, 0, particle.size, 0, Math.PI * 2);
                        ctx.fill();
                    }
                    ctx.restore();
                });

                if (elapsed < TOTAL) {
                    window.requestAnimationFrame(frame);
                    return;
                }
                window.removeEventListener("resize", resize);
                if (canvas.parentNode) {
                    canvas.parentNode.removeChild(canvas);
                }
                finish();
            }
            window.requestAnimationFrame(frame);
        }

        button.addEventListener("click", function (event) {
            event.preventDefault();
            if (running) {
                return;   // повторный клик во время анимации ничего не делает
            }
            running = true;
            showCaption(pickPhrase(), 2400);
            if (reduced) {
                calmRun();
            } else {
                button.classList.add("is-running");
                window.setTimeout(function () {
                    button.classList.remove("is-running");
                }, 2400);
                fireworks();
            }
        });
    })();

    /* Правка названия организации: карандаш открывает форму, «Отмена» закрывает.
       Без скрипта форма остаётся скрытой — права и сохранение всё равно на сервере. */
    (function () {
        const button = document.querySelector("[data-organization-edit]");
        const form = document.querySelector("[data-organization-form]");
        if (!button || !form) {
            return;
        }
        const input = form.querySelector("input[name='organization_name']");
        const cancel = form.querySelector("[data-organization-cancel]");
        const error = form.querySelector("[data-organization-error]");

        function setOpen(open) {
            form.classList.toggle("hidden", !open);
            button.setAttribute("aria-expanded", open ? "true" : "false");
            if (open && input) {
                input.focus();
                input.select();
            }
        }

        button.addEventListener("click", function (event) {
            event.preventDefault();
            setOpen(form.classList.contains("hidden"));
        });
        if (cancel) {
            cancel.addEventListener("click", function (event) {
                event.preventDefault();
                setOpen(false);
            });
        }
        // Ошибку проверки показываем сразу: форма уже открыта сервером.
        if (error && input) {
            input.setAttribute("aria-invalid", "true");
        }
    })();
})();
