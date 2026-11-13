// Settings › Media Management › naming layouts built from blocks
(function () {
    "use strict";

    const builder = document.getElementById("nbBuilder");
    const select = document.getElementById("mm_naming_template");
    if (!builder || !select) return;

    const esc = (value) => AA.escapeHtml(value == null ? "" : String(value));
    const el = (id) => document.getElementById(id);

    const BLOCKS = [
        ["author", "Author"], ["series", "Series"], ["series_number", "Series number"], ["title", "Title"],
        ["year", "Year"], ["narrator", "Narrator"], ["asin", "ASIN"], ["separator", "Separator"], ["break", "/ New folder"],
    ];
    const NAMES = Object.fromEntries(BLOCKS);
    const DEFAULTS = {
        author: { all: true }, series: {}, series_number: { label: "", pad: 2, follow: " - " },
        title: { strip: true }, year: { brackets: false }, narrator: { braces: true },
        asin: { brackets: true }, separator: { text: " - " }, break: {},
    };
    const KIND = { separator: " nb-sep", break: " nb-break" };
    const SHOW = (text) => (text === " " ? "space" : text === "" ? "nothing" : `"${text}"`);

    const state = { layouts: [], editing: null, items: [], selected: null, timer: null };

    async function api(url, method = "GET", body) {
        const options = { method };
        if (body !== undefined) options.body = JSON.stringify(body);
        const { ok, data } = await AA.apiFetch(url, options);
        if (!ok || !data || data.success === false) throw new Error((data && data.error) || "Request failed");
        return data;
    }

    function setText(target, text, tone = "") {
        target.textContent = text || "";
        target.className = `aa-hint ${tone}`;
    }

    function copy(value) {
        return JSON.parse(JSON.stringify(value));
    }

    // The line shows folders joined by "/" blocks; layouts are stored as one row per folder
    function toItems(rows) {
        return rows.flatMap((row, i) => (i ? [{ type: "break" }, ...copy(row)] : copy(row)));
    }

    function toRows(items) {
        const rows = [[]];
        items.forEach((block) => {
            if (block.type === "break") rows.push([]);
            else rows[rows.length - 1].push(block);
        });
        return rows;
    }

    // --- Layout list -------------------------------------------------------------

    async function loadLayouts() {
        try {
            const data = await api("/api/naming/layouts");
            state.layouts = data.layouts || [];
        } catch (error) {
            setText(el("nbListStatus"), `Could not load layouts: ${error.message}`, "text-error");
        }
        syncButtons();
    }

    function current() {
        return state.layouts.find((entry) => entry.name === select.value);
    }

    function syncButtons() {
        const layout = current();
        const custom = Boolean(layout && !layout.built_in);
        el("nbEdit").disabled = !custom || !builder.hidden;
        el("nbDelete").disabled = !custom || !builder.hidden;
        el("nbNew").disabled = !builder.hidden;
    }

    function announceChange(name) {
        document.dispatchEvent(new CustomEvent("aa:naming-layouts-changed", { detail: { name } }));
    }

    // --- Opening and closing the builder ----------------------------------------

    function open(name, rows) {
        state.editing = name;
        state.items = toItems(rows);
        state.selected = null;
        el("nbName").value = name || "";
        el("nbName").disabled = Boolean(name);
        setText(el("nbStatus"), "");
        builder.hidden = false;
        syncButtons();
        render();
        if (name) el("nbPalette").querySelector("button").focus();
        else el("nbName").focus();
    }

    function close() {
        builder.hidden = true;
        state.editing = null;
        syncButtons();
        el("nbNew").focus();
    }

    el("nbNew").addEventListener("click", () => {
        const base = current();
        const rows = base ? base.layout.rows : [[{ type: "author" }], [{ type: "series" }], [{ type: "title" }]];
        open(null, rows.map((row) => row.map((block) => ({ ...DEFAULTS[block.type], ...block }))));
    });

    el("nbEdit").addEventListener("click", () => {
        const layout = current();
        if (layout && !layout.built_in) open(layout.name, layout.layout.rows);
    });

    el("nbDelete").addEventListener("click", async () => {
        const layout = current();
        if (!layout || layout.built_in) return;
        if (!window.confirm(`Delete the layout "${layout.name}"?`)) return;
        try {
            await api(`/api/naming/layouts/${encodeURIComponent(layout.name)}`, "DELETE");
            setText(el("nbListStatus"), `Deleted "${layout.name}".`, "text-success");
            await loadLayouts();
            announceChange(null);
        } catch (error) {
            setText(el("nbListStatus"), error.message, "text-error");
        }
    });

    el("nbCancel").addEventListener("click", close);

    el("nbSave").addEventListener("click", async () => {
        const name = el("nbName").value.trim();
        if (!name) {
            setText(el("nbStatus"), "Give the layout a name.", "text-error");
            el("nbName").focus();
            return;
        }
        const body = { layout: { rows: toRows(state.items) } };
        try {
            const data = state.editing
                ? await api(`/api/naming/layouts/${encodeURIComponent(state.editing)}`, "PUT", body)
                : await api("/api/naming/layouts", "POST", { name, ...body });
            await loadLayouts();
            close();
            const note = (data.warnings || []).length ? " Saved with the warnings shown in the preview." : "";
            setText(el("nbListStatus"), `Saved "${name}". Select it above and save the page to use it.${note}`, "text-success");
            announceChange(name);
        } catch (error) {
            setText(el("nbStatus"), error.message, "text-error");
        }
    });

    // --- Editing the line -----------------------------------------------------

    function addBlock(type, at = null) {
        const index = at == null ? (state.selected == null ? state.items.length : state.selected + 1) : at;
        state.items.splice(index, 0, { type, ...copy(DEFAULTS[type]) });
        state.selected = index;
        changed();
    }

    function moveBlock(from, to) {
        const [block] = state.items.splice(from, 1);
        const index = from < to ? to - 1 : to;
        state.items.splice(index, 0, block);
        state.selected = index;
        changed();
    }

    function removeBlock(index) {
        state.items.splice(index, 1);
        state.selected = null;
        changed();
    }

    function chipText(block) {
        const name = NAMES[block.type];
        if (block.type === "break") return "/";
        if (block.type === "separator") return SHOW(block.text);
        if (block.type === "series_number") {
            const label = block.label ? `${block.label} ` : "";
            const number = block.pad === 3 ? "005" : block.pad === 2 ? "05" : "5";
            return `${name}: ${label}${number}${block.follow ? `, then ${SHOW(block.follow)}` : ""}`;
        }
        if (block.type === "author") return block.all === false ? "Author (first)" : "Author";
        if (block.type === "narrator" && block.braces !== false) return "{Narrator}";
        if (block.type === "asin" && block.brackets !== false) return "[ASIN]";
        if (block.type === "year" && block.brackets) return "(Year)";
        return name;
    }

    function renderPalette() {
        el("nbPalette").innerHTML = BLOCKS.map(([type, name]) =>
            `<button type="button" class="nb-chip${KIND[type] || ""}" draggable="true" `
            + `data-new="${type}" aria-label="Add ${esc(type === "break" ? "a new folder" : name)}">${esc(name)}</button>`).join("");
    }

    function renderStrip() {
        el("nbStrip").innerHTML = state.items.map((block, i) => {
            const selected = state.selected === i;
            const name = block.type === "break" ? "folder break" : NAMES[block.type];
            return `<span class="nb-chip${KIND[block.type] || ""}${selected ? " nb-selected" : ""}" `
                + `draggable="true" data-index="${i}">`
                + `<button type="button" data-act="left" aria-label="Move left">&lsaquo;</button>`
                + `<button type="button" data-act="select" aria-pressed="${selected}">${esc(chipText(block))}</button>`
                + `<button type="button" data-act="right" aria-label="Move right">&rsaquo;</button>`
                + `<button type="button" data-act="remove" aria-label="Remove ${esc(name)}">&times;</button></span>`;
        }).join("") || '<span class="aa-hint">Empty: add blocks here.</span>';
    }

    function renderOptions() {
        const panel = el("nbOptions");
        const sel = state.selected;
        const block = sel == null ? null : state.items[sel];
        if (!block || block.type === "break") {
            panel.hidden = true;
            panel.innerHTML = "";
            return;
        }
        const radio = (key, value, text) => `<label><input type="radio" name="nb_${key}" value="${esc(JSON.stringify(value))}" `
            + `data-key="${key}"${block[key] === value ? " checked" : ""}> ${esc(text)}</label>`;
        const check = (key, text, fallback) => `<label><input type="checkbox" data-key="${key}"`
            + `${(block[key] ?? fallback) ? " checked" : ""}> ${esc(text)}</label>`;
        let fields = "";
        if (block.type === "author") {
            fields = `<fieldset><legend>Authors</legend>${radio("all", true, "All authors (A, B)")}${radio("all", false, "First author only")}</fieldset>`;
        } else if (block.type === "series_number") {
            const preset = ["", "Book", "Vol", "Volume"].includes(block.label || "");
            fields = `<fieldset><legend>Label before the number</legend>`
                + ["", "Book", "Vol", "Volume"].map((v) => radio("label", v, v || "None")).join("")
                + `<label><input type="radio" name="nb_label" data-key="label-custom"${preset ? "" : " checked"}> Custom `
                + `<input type="text" class="input input-bordered input-xs" maxlength="20" data-key="label-text" `
                + `value="${preset ? "" : esc(block.label)}" aria-label="Custom label"></label></fieldset>`
                + `<fieldset><legend>Padding</legend>${radio("pad", 0, "None (5)")}${radio("pad", 2, "Below 10 (05)")}${radio("pad", 3, "Three digits (005)")}</fieldset>`
                + `<fieldset><legend>Followed by</legend>${[" - ", ". ", " ", ""].map((v) => radio("follow", v, SHOW(v))).join("")}</fieldset>`;
        } else if (block.type === "title") {
            fields = `<fieldset><legend>Title</legend>${check("strip", "Drop marketing subtitles", true)}</fieldset>`;
        } else if (block.type === "year") {
            fields = `<fieldset><legend>Year</legend>${check("brackets", "In brackets: (2021)", false)}</fieldset>`;
        } else if (block.type === "narrator") {
            fields = `<fieldset><legend>Narrator</legend>${check("braces", "In braces: {Narrator}, which Audiobookshelf reads", true)}</fieldset>`;
        } else if (block.type === "asin") {
            fields = `<fieldset><legend>ASIN</legend>${check("brackets", "In square brackets: [ASIN], which Audiobookshelf reads", true)}</fieldset>`;
        } else if (block.type === "separator") {
            fields = `<fieldset><legend>Separator</legend>${[" - ", ". ", ", ", " "].map((v) => radio("text", v, SHOW(v))).join("")}</fieldset>`;
        } else {
            fields = `<span class="aa-hint">${esc(NAMES[block.type])} has no options.</span>`;
        }
        panel.innerHTML = `<strong style="font-size:12px;width:100%;">${esc(NAMES[block.type])} options</strong>${fields}`;
        panel.hidden = false;
    }

    el("nbOptions").addEventListener("input", (event) => {
        const block = state.items[state.selected];
        const key = event.target.dataset.key;
        if (!block || !key) return;
        if (key === "label-text" || key === "label-custom") {
            const text = el("nbOptions").querySelector('[data-key="label-text"]');
            block.label = text.value.trim();
            if (key === "label-text") el("nbOptions").querySelector('[data-key="label-custom"]').checked = true;
        } else if (event.target.type === "checkbox") {
            block[key] = event.target.checked;
        } else {
            block[key] = JSON.parse(event.target.value);
        }
        renderStrip();
        scheduleCheck();
    });

    function render() {
        renderPalette();
        renderStrip();
        renderOptions();
    }

    function changed() {
        render();
        scheduleCheck();
    }

    // --- Clicks, keyboard and drag and drop ------------------------------------

    el("nbPalette").addEventListener("click", (event) => {
        const chip = event.target.closest("[data-new]");
        if (chip) addBlock(chip.dataset.new);
    });

    el("nbStrip").addEventListener("click", (event) => {
        const button = event.target.closest("button[data-act]");
        const chip = event.target.closest(".nb-chip");
        if (!button || !chip) return;
        const i = Number(chip.dataset.index);
        const act = button.dataset.act;
        if (act === "left" && i > 0) moveBlock(i, i - 1);
        else if (act === "right" && i < state.items.length - 1) moveBlock(i, i + 2);
        else if (act === "remove") removeBlock(i);
        else if (act === "select") {
            state.selected = i;
            render();
        }
    });

    builder.addEventListener("dragstart", (event) => {
        const chip = event.target.closest(".nb-chip");
        if (!chip) return;
        const payload = chip.dataset.new ? `new:${chip.dataset.new}` : `move:${chip.dataset.index}`;
        event.dataTransfer.setData("text/plain", payload);
        event.dataTransfer.effectAllowed = "move";
    });

    el("nbStrip").addEventListener("dragover", (event) => {
        event.preventDefault();
        el("nbStrip").classList.add("nb-drop");
    });

    el("nbStrip").addEventListener("dragleave", (event) => {
        if (!el("nbStrip").contains(event.relatedTarget)) el("nbStrip").classList.remove("nb-drop");
    });

    el("nbStrip").addEventListener("drop", (event) => {
        event.preventDefault();
        el("nbStrip").classList.remove("nb-drop");
        const target = event.target.closest(".nb-chip[data-index]");
        const at = target ? Number(target.dataset.index) : state.items.length;
        const [kind, value] = event.dataTransfer.getData("text/plain").split(":");
        if (kind === "new" && DEFAULTS[value]) addBlock(value, at);
        else if (kind === "move") moveBlock(Number(value), at);
    });

    // --- Checking and preview ----------------------------------------------------

    function scheduleCheck() {
        clearTimeout(state.timer);
        state.timer = setTimeout(runCheck, 300);
    }

    function readsText(reads, keys) {
        const parts = keys.filter(([key]) => reads[key]).map(([key, name]) => `${name} ${reads[key]}`);
        return parts.length ? parts.join(" · ") : "nothing";
    }

    async function runCheck() {
        const messages = el("nbMessages");
        const preview = el("nbPreview");
        let data;
        try {
            data = await api("/api/naming/layouts/check", "POST", { layout: { rows: toRows(state.items) } });
        } catch (error) {
            messages.innerHTML = `<li class="text-error">${esc(error.message)}</li>`;
            return;
        }
        messages.innerHTML = (data.errors || []).map((text) => `<li class="text-error">${esc(text)}</li>`).join("")
            + (data.warnings || []).map((text) => `<li class="text-warning">${esc(text)}</li>`).join("");
        el("nbSave").disabled = (data.errors || []).length > 0;

        const ours = [["author", "author"], ["series", "series"], ["series_number", "book"], ["title", "title"],
            ["year", "year"], ["narrator", "narrator"], ["asin", "ASIN"]];
        const theirs = [["author", "author"], ["series", "series"], ["sequence", "book"], ["title", "title"],
            ["year", "year"], ["narrator", "narrator"], ["asin", "ASIN"]];
        preview.innerHTML = (data.previews || []).map((item) => `<li>`
            + `<div><span class="font-medium">${esc(item.title)}</span>${item.example ? ' <span class="aa-hint">(example)</span>' : ""}</div>`
            + `<code>${esc(item.path)}/${esc(item.file)}</code>`
            + `<div class="nb-reads">AuralArchive reads: ${esc(readsText(item.auralarchive_reads, ours))}</div>`
            + `<div class="nb-reads">Audiobookshelf reads: ${esc(readsText(item.audiobookshelf_reads, theirs))}</div>`
            + `</li>`).join("") || '<li class="aa-hint">Fix the problems above to see a preview.</li>';
    }

    // --- Wiring -----------------------------------------------------------------

    select.addEventListener("change", syncButtons);
    document.addEventListener("aa:naming-templates-loaded", loadLayouts);
    loadLayouts();
})();
