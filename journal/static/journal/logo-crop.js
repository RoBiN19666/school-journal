/* Обрезка логотипа: квадратная рамка, перетаскивание и масштаб.
 *
 * Скрипт не отправляет картинку и не режет её сам: он только определяет, какая
 * квадратная область исходного файла должна попасть в логотип, и кладёт её
 * координаты (в пикселях исходника) в скрытые поля формы logo_crop_x,
 * logo_crop_y, logo_crop_size. Режет и приводит к 256 × 256 сервер (Pillow,
 * см. journal/branding.py) — браузеру холст нужен только для показа.
 *
 * 100 % масштаба — картинка ровно заполняет рамку. Начальный масштаб — «fit»:
 * картинка целиком вписана в рамку (для широкого логотипа это заметно меньше
 * 100 %). Уменьшать можно до 25 % (или до fit, если картинка очень вытянутая):
 * тогда вокруг картинки видны поля, и они же попадут в логотип — что видно в
 * рамке, то и сохраняется. Увеличивать — до 400 %: двигать картинку можно по
 * обеим осям независимо.
 *
 * Если JavaScript выключен или файл не открылся, координаты остаются пустыми
 * и сервер обрезает картинку по центру: загрузка не падает.
 *
 * Файл читается как data:-URL через FileReader, а не через URL.createObjectURL.
 * Так окно обрезки работает и под строгой Content-Security-Policy: на боевом
 * сервере заголовок разрешает data:, но не blob:, и картинка с blob:-адресом
 * просто не загружалась — рамка не открывалась. Data:-URL не создаёт объектных
 * URL, поэтому отзывать через URL.revokeObjectURL нечего: утечки памяти нет.
 * canvas с data:-картинкой не «отравлен» (схема считается своим источником),
 * поэтому предпросмотр и canvas.toDataURL работают как раньше.
 */
(function () {
    "use strict";

    /** Пределы масштаба: 0.25 — сильно уменьшить, 4 — вчетверо крупнее рамки. */
    const MIN_ZOOM = 0.25;
    const MAX_ZOOM = 4;
    /** Сторона готового логотипа — столько же получается на сервере. */
    const RESULT = 256;
    /** Сторона рамки: не больше 288 px и всегда влезает в узкий экран. */
    const MAX_FRAME = 288;
    /** Имена скрытых полей с выбранной областью. */
    const FIELDS = ["logo_crop_x", "logo_crop_y", "logo_crop_size"];
    /** Форматы, которые принимает сервер: остальные не открываем даже в рамке. */
    const ALLOWED = ["png", "jpg", "jpeg", "ico", "webp"];

    /* Стили пишем прямо в элементах: окно обрезки не зависит от пересборки
       Tailwind и работает даже офлайн, где классы появляются только после
       разбора документа. */
    const STYLE = {
        overlay: "position:fixed;inset:0;z-index:60;display:flex;align-items:center;" +
            "justify-content:center;padding:16px;background:rgba(15,23,42,0.55);",
        panel: "width:100%;max-width:360px;background:#ffffff;border-radius:16px;" +
            "padding:16px;box-shadow:0 20px 50px rgba(15,23,42,0.35);",
        head: "display:flex;align-items:center;justify-content:space-between;gap:12px;" +
            "margin-bottom:12px;",
        title: "margin:0;font-size:14px;font-weight:600;color:#0f172a;",
        close: "border:0;background:transparent;color:#64748b;font-size:16px;" +
            "line-height:1;cursor:pointer;padding:4px;border-radius:8px;",
        crop: "position:relative;margin:0 auto;border-radius:12px;overflow:hidden;" +
            "background:#ffffff;cursor:grab;touch-action:none;",
        canvas: "display:block;width:100%;height:100%;",
        frame: "position:absolute;inset:0;pointer-events:none;border-radius:12px;" +
            "box-shadow:inset 0 0 0 1px rgba(148,163,184,0.65);",
        hint: "margin:10px 0 0;font-size:12px;line-height:1.35;color:#64748b;",
        zoom: "display:flex;align-items:center;gap:8px;margin-top:10px;" +
            "font-size:12px;color:#475569;",
        slider: "flex:1;accent-color:#4f46e5;",
        foot: "display:flex;align-items:center;justify-content:space-between;" +
            "gap:12px;margin-top:14px;",
        preview: "width:72px;height:72px;border-radius:10px;border:1px solid #e2e8f0;" +
            "background:#ffffff;flex:none;",
        buttons: "display:flex;gap:8px;",
        cancel: "border:1px solid #cbd5e1;background:#ffffff;color:#334155;" +
            "font-size:13px;font-weight:500;padding:8px 14px;border-radius:10px;" +
            "cursor:pointer;",
        apply: "border:0;background:#4f46e5;color:#ffffff;font-size:13px;" +
            "font-weight:600;padding:8px 14px;border-radius:10px;cursor:pointer;",
    };

    const DEFAULT_TEXT = {
        title: "Логотип",
        hint: "Потяните картинку мышью и задайте масштаб ползунком — " +
            "в логотип попадёт квадрат в центре рамки.",
        zoom: "Масштаб",
        apply: "Применить",
        cancel: "Отмена",
        close: "Закрыть",
    };

    /** Подписи приходят из шаблона ({% trans %}), поэтому их можно перевести. */
    const CONFIG = document.querySelector("[data-logo-crop-config]");

    function text(name, fallback) {
        const value = CONFIG && CONFIG.getAttribute("data-" + name);
        return value || fallback;
    }

    function label(name) {
        return text(name, DEFAULT_TEXT[name]);
    }

    function clamp(value, low, high) {
        return Math.min(high, Math.max(low, value));
    }

    function node(tag, attrs, children) {
        const element = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (key) {
            const value = attrs[key];
            if (key === "text") {
                element.textContent = value;
            } else if (key === "class") {
                element.className = value;
            } else {
                element.setAttribute(key, value);
            }
        });
        (children || []).forEach(function (child) {
            element.appendChild(child);
        });
        return element;
    }

    /** Окно обрезки одного поля загрузки. */
    function Editor(input) {
        this.input = input;
        this.form = input.form;
        this.fields = {};
        this.overlay = null;
        this.image = null;
        this.source = "";      // data:-URL выбранного файла (не object URL)
        this.token = 0;        // номер попытки: ответы FileReader старше него игнорируем
        this.frameSide = MAX_FRAME;
        this.cover = 1;        // масштаб, при котором картинка ровно заполняет рамку (100 %)
        this.fitZoom = 1;      // масштаб, при котором картинка целиком вписана в рамку
        this.minZoom = MIN_ZOOM;
        this.zoom = 1;         // доля от cover: 1 — рамка заполнена
        this.offsetX = 0;      // сдвиг картинки от центра рамки, CSS px
        this.offsetY = 0;
        this.drag = null;
        this.frameCanvas = null;
        this.previewCanvas = null;
        this.slider = null;
        this.zoomLabel = null;
        this.boundKey = null;
        this.buildFields();
    }

    Editor.prototype.buildFields = function () {
        const form = this.form;
        const fields = this.fields;
        FIELDS.forEach(function (name) {
            let field = form.querySelector('input[type="hidden"][name="' + name + '"]');
            if (!field) {
                field = node("input", { type: "hidden", name: name, value: "" });
                form.appendChild(field);
            }
            fields[name] = field;
        });
    };

    Editor.prototype.resetFields = function () {
        const fields = this.fields;
        FIELDS.forEach(function (name) {
            fields[name].value = "";
        });
    };

    Editor.prototype.release = function () {
        // Новый номер попытки: медленный FileReader прошлого выбора уже не откроет
        // рамку поверх отменённой или поверх следующего выбранного файла.
        this.token += 1;
        // Объектных URL скрипт не создаёт (см. open), отзывать нечего: очищаем
        // только ссылки, чтобы выбранная картинка не держалась в памяти.
        this.source = "";
        this.image = null;
    };

    /** Файл выбран: открываем рамку (или молча ждём сервер, если это не картинка). */
    Editor.prototype.choose = function () {
        const file = this.input.files && this.input.files[0];
        this.resetFields();
        if (!file) {
            return;
        }
        if (file.type && file.type.indexOf("image/") !== 0) {
            return; // сервер объяснит, что за формат и почему нельзя
        }
        const match = /\.([a-z0-9]+)$/i.exec(file.name || "");
        if (match && ALLOWED.indexOf(match[1].toLowerCase()) < 0) {
            return; // SVG и GIF сервер не принимает — рамку для них не показываем
        }
        this.open(file);
    };

    /** Файл читаем как data:-URL: blob: на бою запрещён политикой CSP. */
    Editor.prototype.open = function (file) {
        const self = this;
        this.release();
        const token = this.token;
        const reader = new FileReader();
        reader.onload = function () {
            const url = typeof reader.result === "string" ? reader.result : "";
            if (self.token !== token || !url) {
                return; // за время чтения выбрали другой файл или закрыли рамку
            }
            self.source = url;
            const image = new Image();
            image.onload = function () {
                if (self.token !== token) {
                    return; // картинка от прошлого выбора: рамку не открываем
                }
                self.image = image;
                self.frameSide = Math.max(
                    180,
                    Math.min(MAX_FRAME, (window.innerWidth || MAX_FRAME + 64) - 64)
                );
                // cover — «100 %»: картинка ровно заполняет рамку.
                self.cover = Math.max(
                    self.frameSide / image.naturalWidth,
                    self.frameSide / image.naturalHeight
                );
                // fit — картинка целиком внутри рамки: начальный масштаб и предел
                // уменьшения. Для квадратной картинки fit = cover (100 %), для
                // широкой — заметно меньше, поэтому по вертикали тоже будет что
                // двигать, когда пользователь увеличит масштаб.
                const fit = Math.min(
                    self.frameSide / image.naturalWidth,
                    self.frameSide / image.naturalHeight
                );
                self.fitZoom = self.cover > 0 ? fit / self.cover : 1;
                // Очень вытянутая картинка (уже 1:4) не влезает в 25 %: тогда нижняя
                // граница ползунка — сама fit, чтобы всю картинку всё равно было видно.
                self.minZoom = Math.min(MIN_ZOOM, self.fitZoom);
                self.zoom = self.fitZoom;
                self.offsetX = 0;
                self.offsetY = 0;
                self.mount();
            };
            image.onerror = function () {
                if (self.token !== token) {
                    return;
                }
                self.release();
                self.resetFields();
            };
            image.src = url;
        };
        reader.onerror = function () {
            // Файл не прочитался: форма уходит без координат, сервер обрежет по центру.
            if (self.token !== token) {
                return;
            }
            self.release();
            self.resetFields();
        };
        reader.readAsDataURL(file);
    };

    Editor.prototype.mount = function () {
        const self = this;
        const side = this.frameSide;
        const ratio = window.devicePixelRatio || 1;

        this.frameCanvas = node("canvas", {
            width: String(Math.round(side * ratio)),
            height: String(Math.round(side * ratio)),
            style: STYLE.canvas,
        });
        this.previewCanvas = node("canvas", {
            width: String(RESULT),
            height: String(RESULT),
            style: STYLE.preview,
        });
        this.slider = node("input", {
            type: "range",
            min: String(this.minZoom),
            max: String(MAX_ZOOM),
            step: "0.01",
            value: String(this.zoom),
            "aria-label": label("zoom"),
            style: STYLE.slider,
        });
        this.zoomLabel = node("span", {
            text: Math.round(this.zoom * 100) + " %",
            style: "min-width:44px;text-align:right;",
        });

        const close = node("button", {
            type: "button",
            "aria-label": label("close"),
            style: STYLE.close,
            text: "✕",
        });
        close.addEventListener("click", function () {
            self.cancel();
        });

        const cancel = node("button", {
            type: "button",
            style: STYLE.cancel,
            text: label("cancel"),
        });
        cancel.addEventListener("click", function () {
            self.cancel();
        });

        const apply = node("button", {
            type: "button",
            style: STYLE.apply,
            text: label("apply"),
        });
        apply.addEventListener("click", function () {
            self.accept();
        });

        const frame = node("div", {
            style: STYLE.crop + "width:" + side + "px;height:" + side + "px;",
        }, [this.frameCanvas, node("div", { style: STYLE.frame })]);
        frame.addEventListener("pointerdown", function (event) {
            self.pointerDown(event);
        });
        frame.addEventListener("pointermove", function (event) {
            self.pointerMove(event);
        });
        frame.addEventListener("pointerup", function (event) {
            self.pointerUp(event);
        });
        frame.addEventListener("pointercancel", function (event) {
            self.pointerUp(event);
        });
        frame.addEventListener("wheel", function (event) {
            self.wheel(event);
        }, { passive: false });

        const panel = node("div", {
            role: "dialog",
            "aria-modal": "true",
            "aria-label": label("title"),
            style: STYLE.panel,
        }, [
            node("div", { style: STYLE.head }, [
                node("p", { style: STYLE.title, text: label("title") }),
                close,
            ]),
            frame,
            node("p", { style: STYLE.hint, text: label("hint") }),
            node("label", { style: STYLE.zoom }, [
                node("span", { text: label("zoom") }),
                this.slider,
                this.zoomLabel,
            ]),
            node("div", { style: STYLE.foot }, [
                this.previewCanvas,
                node("div", { style: STYLE.buttons }, [cancel, apply]),
            ]),
        ]);

        this.overlay = node("div", { style: STYLE.overlay }, [panel]);
        this.overlay.addEventListener("click", function (event) {
            if (event.target === self.overlay) {
                self.cancel();
            }
        });
        document.body.appendChild(this.overlay);

        this.slider.addEventListener("input", function () {
            self.setZoom(parseFloat(self.slider.value));
        });

        this.boundKey = function (event) {
            if (event.key === "Escape") {
                event.preventDefault();
                self.cancel();
            }
        };
        document.addEventListener("keydown", this.boundKey);

        this.clampOffsets();
        this.draw();
        apply.focus();
    };

    /** Границы сдвига: картинка всегда покрывает рамку целиком.
     *
     * Оси считаем отдельно: широкая картинка (950 × 259) при 100 % заполняет
     * рамку по высоте, поэтому по вертикали двигать нечего, а по горизонтали
     * запас есть. После увеличения запас появляется по обеим осям.
     */
    Editor.prototype.clampOffsets = function () {
        if (!this.image) {
            return;
        }
        const scale = this.cover * this.zoom;
        const spareX = Math.max(0, this.image.naturalWidth * scale - this.frameSide);
        const spareY = Math.max(0, this.image.naturalHeight * scale - this.frameSide);
        this.offsetX = clamp(this.offsetX, -spareX / 2, spareX / 2);
        this.offsetY = clamp(this.offsetY, -spareY / 2, spareY / 2);
    };

    /** Область исходника, которая сейчас видна в рамке: x, y и сторона квадрата.
     *
     * При масштабе меньше fit-границы квадрат выходит за картинку — сервер
     * дорисует там белые поля (см. journal/branding.py), поэтому координаты не
     * обрезаем: что видно в рамке, то и попадёт в логотип.
     */
    Editor.prototype.rect = function () {
        const image = this.image;
        const scale = this.cover * this.zoom;
        const width = image.naturalWidth;
        const height = image.naturalHeight;
        const size = this.frameSide / scale;
        const x = width / 2 - (this.frameSide / 2 + this.offsetX) / scale;
        const y = height / 2 - (this.frameSide / 2 + this.offsetY) / scale;
        return { x: x, y: y, size: size };
    };

    Editor.prototype.draw = function () {
        if (!this.image || !this.frameCanvas) {
            return;
        }
        const side = this.frameSide;
        const ratio = window.devicePixelRatio || 1;
        const scale = this.cover * this.zoom;
        const context = this.frameCanvas.getContext("2d");
        context.setTransform(1, 0, 0, 1, 0, 0);
        context.clearRect(0, 0, this.frameCanvas.width, this.frameCanvas.height);
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, this.frameCanvas.width, this.frameCanvas.height);
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = "high";
        context.setTransform(
            scale * ratio, 0, 0, scale * ratio,
            (side / 2 + this.offsetX) * ratio,
            (side / 2 + this.offsetY) * ratio
        );
        context.drawImage(
            this.image,
            -this.image.naturalWidth / 2,
            -this.image.naturalHeight / 2
        );
        this.drawPreview();
    };

    Editor.prototype.drawPreview = function () {
        if (!this.image || !this.previewCanvas) {
            return;
        }
        const box = this.rect();
        const context = this.previewCanvas.getContext("2d");
        context.setTransform(1, 0, 0, 1, 0, 0);
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, RESULT, RESULT);
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = "high";
        context.drawImage(
            this.image,
            box.x, box.y, box.size, box.size,
            0, 0, RESULT, RESULT
        );
    };

    Editor.prototype.setZoom = function (value) {
        if (!isFinite(value)) {
            return;
        }
        this.zoom = clamp(value, this.minZoom, MAX_ZOOM);
        if (this.slider && parseFloat(this.slider.value) !== this.zoom) {
            this.slider.value = String(this.zoom);
        }
        if (this.zoomLabel) {
            this.zoomLabel.textContent = Math.round(this.zoom * 100) + " %";
        }
        this.clampOffsets();
        this.draw();
    };

    Editor.prototype.wheel = function (event) {
        event.preventDefault();
        const step = event.deltaY < 0 ? 1.08 : 1 / 1.08;
        this.setZoom(this.zoom * step);
    };

    Editor.prototype.pointerDown = function (event) {
        if (!this.image) {
            return;
        }
        event.preventDefault();
        this.drag = {
            id: event.pointerId,
            x: event.clientX,
            y: event.clientY,
            offsetX: this.offsetX,
            offsetY: this.offsetY,
        };
        event.currentTarget.style.cursor = "grabbing";
        if (event.currentTarget.setPointerCapture) {
            try {
                event.currentTarget.setPointerCapture(event.pointerId);
            } catch (error) {
                /* без захвата указателя тоже работает */
            }
        }
    };

    Editor.prototype.pointerMove = function (event) {
        if (!this.drag || this.drag.id !== event.pointerId) {
            return;
        }
        event.preventDefault();
        this.offsetX = this.drag.offsetX + (event.clientX - this.drag.x);
        this.offsetY = this.drag.offsetY + (event.clientY - this.drag.y);
        this.clampOffsets();
        this.draw();
    };

    Editor.prototype.pointerUp = function (event) {
        if (this.drag && this.drag.id === event.pointerId) {
            this.drag = null;
        }
        event.currentTarget.style.cursor = "grab";
    };

    /** Готово: раскладываем выбранную область по скрытым полям формы. */
    Editor.prototype.accept = function () {
        if (!this.image) {
            this.close();
            return;
        }
        const box = this.rect();
        if (!isFinite(box.size) || box.size <= 0) {
            this.close();
            return;
        }
        this.fields.logo_crop_x.value = String(Math.round(box.x));
        this.fields.logo_crop_y.value = String(Math.round(box.y));
        this.fields.logo_crop_size.value = String(Math.round(box.size));
        this.updatePreview(box);
        this.close();
    };

    /** Предпросмотр на странице: сразу видно, что попадёт в логотип. */
    Editor.prototype.updatePreview = function (box) {
        const holder = document.querySelector("[data-logo-preview-frame]");
        if (!holder || !this.image) {
            return;
        }
        const canvas = document.createElement("canvas");
        canvas.width = RESULT;
        canvas.height = RESULT;
        const context = canvas.getContext("2d");
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, RESULT, RESULT);
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = "high";
        context.drawImage(
            this.image,
            box.x, box.y, box.size, box.size,
            0, 0, RESULT, RESULT
        );
        let url;
        try {
            url = canvas.toDataURL("image/png");
        } catch (error) {
            return; // например, картинка с чужого домена
        }
        holder.style.background = "#ffffff";
        holder.innerHTML = "";
        holder.appendChild(node("img", {
            src: url,
            alt: "",
            style: "display:block;width:100%;height:100%;object-fit:cover;",
        }));
    };

    Editor.prototype.cancel = function () {
        this.resetFields();
        this.input.value = ""; // отмена — значит и файл не выбран
        this.close();
    };

    Editor.prototype.close = function () {
        if (this.boundKey) {
            document.removeEventListener("keydown", this.boundKey);
            this.boundKey = null;
        }
        if (this.overlay && this.overlay.parentNode) {
            this.overlay.parentNode.removeChild(this.overlay);
        }
        this.overlay = null;
        this.frameCanvas = null;
        this.previewCanvas = null;
        this.slider = null;
        this.zoomLabel = null;
        this.drag = null;
        this.release();
    };

    function attach(input) {
        if (input.getAttribute("data-logo-crop-ready") === "1" || !input.form) {
            return;
        }
        input.setAttribute("data-logo-crop-ready", "1");
        const editor = new Editor(input);
        // Очищаем поле ДО выбора файла: браузер не присылает change, если выбран
        // тот же самый файл (значение поля не изменилось), и рамка во второй раз
        // не открывалась. Чистим именно до выбора, а не после: выбранный файл
        // должен остаться в поле, иначе форма отправится без картинки. Отмена и
        // закрытие рамки чистят поле сами (см. cancel); двойной привязки нет —
        // повторный attach отсекает отметка data-logo-crop-ready.
        input.addEventListener("click", function () {
            input.value = "";
        });
        input.addEventListener("change", function () {
            editor.choose();
        });
        // Саму отправку формы не перехватываем: если рамку не открывали,
        // координат нет и сервер обрежет картинку по центру.
    }

    function start() {
        const inputs = document.querySelectorAll('input[type="file"][name="logo"]');
        Array.prototype.forEach.call(inputs, attach);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
