// Settings › Storage › Libraries and Settings › Media Management › Library Routing
(function () {
    "use strict";

    const esc = (value) => AA.escapeHtml(value == null ? "" : String(value));
    const card = document.getElementById("librariesCard");
    const routingCard = document.getElementById("libraryRoutingCard");
    if (!card && !routingCard) return;

    const RULE_KINDS = [["language", "Language is"], ["author", "Author contains"], ["series", "Series contains"], ["narrator", "Narrator contains"]];
    const state = { libraries: [], templates: [], settings: {}, selected: null, rules: [] };

    async function api(url, method = "GET", body) {
        const options = { method };
        if (body !== undefined) options.body = JSON.stringify(body);
        const { ok, data } = await AA.apiFetch(url, options);
        if (!ok || !data || data.success === false) throw new Error((data && data.error) || "Request failed");
        return data;
    }

    function setStatus(el, text, tone = "") {
        if (!el) return;
        el.textContent = text || "";
        el.className = `${el.className.split(" ")[0]} ${tone}`;
    }

    // --- Libraries card -------------------------------------------------------

    const list = document.getElementById("librariesList");
    const editor = document.getElementById("libraryEditor");
    const empty = document.getElementById("libraryEmpty");
    const fields = {
        name: document.getElementById("library_name"),
        root: document.getElementById("library_root"),
        template: document.getElementById("library_template"),
        enabled: document.getElementById("library_enabled"),
        isDefault: document.getElementById("library_default"),
        reassign: document.getElementById("library_reassign"),
    };
    const statusEl = document.getElementById("libraryStatus");

    function health(lib) {
        if (!lib.enabled) return ["Disabled", "aa-muted"];
        if (!lib.exists) return ["Folder not found", "text-error"];
        if (!lib.writable) return ["Can't write", "text-error"];
        if (lib.mounted === false) return ["Not mounted", "text-error"];
        return [`${lib.free_human} free`, "aa-ok"];
    }

    function renderList() {
        if (!list) return;
        list.innerHTML = state.libraries.map((lib) => {
            const [text, cls] = health(lib);
            return `<div class="aa-list-item ${state.selected && state.selected.id === lib.id ? "active" : ""}" data-library="${lib.id}" tabindex="0">
                <div class="aa-li-ic"><i class="fas fa-book"></i></div>
                <div style="min-width:0;flex:1;">
                    <div class="aa-li-nm">${esc(lib.name)}${lib.is_default ? ' <span class="lib-badge">Default</span>' : ""}</div>
                    <div class="aa-li-sb">${lib.books} book${lib.books === 1 ? "" : "s"} · <span class="${cls}">${esc(text)}</span></div>
                </div>
            </div>`;
        }).join("") || '<div class="aa-list-empty">No libraries.</div>';
    }

    function fillTemplates() {
        fields.template.innerHTML = `<option value="">Use the global template</option>`
            + state.templates.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
    }

    function openEditor(lib) {
        state.selected = lib;
        renderList();
        editor.classList.remove("hidden");
        empty.classList.add("hidden");
        setStatus(statusEl, "");
        document.getElementById("libraryReassignRow").classList.add("hidden");
        const isNew = !lib;
        document.getElementById("libraryEditorTitle").textContent = isNew ? "New Library" : lib.name;
        document.getElementById("libraryEditorSub").textContent = isNew ? "Point it at a folder for a separate set of books"
            : `${lib.books} book${lib.books === 1 ? "" : "s"}`;
        fields.name.value = isNew ? "" : lib.name;
        fields.root.value = isNew ? "" : lib.root;
        fields.root.readOnly = Boolean(lib && lib.uses_storage_path);
        document.getElementById("libraryRootHint").textContent = lib && lib.uses_storage_path
            ? "This library follows the Library folder in Media Folders above; change it there."
            : "An existing folder AuralArchive can write to. It can't overlap another library or the downloads, import and work folders.";
        fields.template.value = isNew ? "" : (lib.naming_template || "");
        fields.enabled.checked = isNew ? true : lib.enabled;
        fields.isDefault.checked = isNew ? false : lib.is_default;
        fields.isDefault.disabled = Boolean(lib && lib.is_default);
        fields.enabled.disabled = Boolean(lib && lib.is_default);
        const del = document.getElementById("libraryDelete");
        del.classList.toggle("hidden", isNew || lib.is_default || lib.uses_storage_path);
        const flags = [];
        if (lib && lib.exists && !lib.same_disk_as_downloads && !lib.uses_storage_path) {
            flags.push(["info", "On a different disk from Downloads, so books imported here are copied rather than moved."]);
        }
        if (lib && lib.mounted === false) {
            flags.push(["error", "Not a mounted folder - books here are lost when the container is recreated."]);
        }
        document.getElementById("libraryFlags").innerHTML = flags.map(([level, text]) =>
            `<div class="aa-store-flag ${level}"><span>${esc(text)}</span></div>`).join("");
        fields.name.focus();
    }

    function closeEditor() {
        state.selected = null;
        editor.classList.add("hidden");
        empty.classList.remove("hidden");
        renderList();
    }

    async function load() {
        try {
            const data = await api("/api/libraries");
            state.libraries = data.libraries || [];
            state.templates = data.templates || [];
            state.settings = data.settings || {};
            if (card) {
                fillTemplates();
                const keep = state.selected && state.libraries.find((l) => l.id === state.selected.id);
                renderList();
                if (keep) openEditor(keep);
            }
            if (routingCard) await loadRouting();
        } catch (err) {
            if (list) list.innerHTML = `<div class="aa-list-empty text-error">${esc(err.message)}</div>`;
        }
    }

    async function save(event) {
        event.preventDefault();
        const body = {
            name: fields.name.value.trim(),
            naming_template: fields.template.value,
            enabled: fields.enabled.checked,
            is_default: fields.isDefault.checked,
        };
        if (!fields.root.readOnly) body.root_path = fields.root.value.trim();
        setStatus(statusEl, "Saving…");
        try {
            const data = state.selected
                ? await api(`/api/libraries/${state.selected.id}`, "PATCH", body)
                : await api("/api/libraries", "POST", body);
            state.selected = data.library;
            await load();
            setStatus(statusEl, "Saved", "text-success");
        } catch (err) {
            setStatus(statusEl, err.message, "text-error");
        }
    }

    async function remove() {
        const lib = state.selected;
        if (!lib) return;
        let url = `/api/libraries/${lib.id}`;
        if (lib.books > 0) {
            const row = document.getElementById("libraryReassignRow");
            if (row.classList.contains("hidden")) {
                fields.reassign.innerHTML = state.libraries.filter((l) => l.id !== lib.id)
                    .map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
                row.classList.remove("hidden");
                setStatus(statusEl, "Choose where its books go, then press Delete again", "text-warning");
                return;
            }
            url += `?reassign_to=${encodeURIComponent(fields.reassign.value)}`;
        }
        const ok = await AA.showConfirmDialog(
            `Delete the ${lib.name} library? Its folder and files are not touched.`, "Delete library", "Delete");
        if (!ok) return;
        try {
            await api(url, "DELETE");
            closeEditor();
            await load();
        } catch (err) {
            setStatus(statusEl, err.message, "text-error");
        }
    }

    if (card) {
        list.addEventListener("click", (event) => {
            const item = event.target.closest("[data-library]");
            if (item) openEditor(state.libraries.find((l) => String(l.id) === item.dataset.library));
        });
        list.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                const item = event.target.closest("[data-library]");
                if (item) item.click();
            }
        });
        document.getElementById("libraryAddButton").addEventListener("click", () => openEditor(null));
        document.getElementById("libraryCancel").addEventListener("click", closeEditor);
        document.getElementById("libraryDelete").addEventListener("click", remove);
        editor.addEventListener("submit", save);
        document.getElementById("librariesRepair").addEventListener("click", async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            try {
                const data = await api("/api/libraries/repair", "POST");
                button.textContent = data.changed ? `Fixed ${data.changed}` : "All correct";
                await load();
            } catch (err) {
                button.textContent = "Repair failed";
            } finally {
                setTimeout(() => { button.textContent = "Repair assignments"; button.disabled = false; }, 3000);
            }
        });
    }

    // --- Routing card ---------------------------------------------------------

    const rulesEl = document.getElementById("routingRules");
    const routingStatus = document.getElementById("routingStatus");

    async function loadRouting() {
        const single = state.libraries.length <= 1;
        document.getElementById("routingSingle").classList.toggle("hidden", !single);
        document.getElementById("routingBody").classList.toggle("hidden", single);
        document.getElementById("routingSave").classList.toggle("hidden", single);
        document.getElementById("routingKeepSeries").checked = state.settings.keep_series_together !== false;
        const data = await api("/api/libraries/rules");
        state.rules = data.rules || [];
        renderRules();
    }

    function libraryOptions(selected) {
        return state.libraries.map((l) => `<option value="${l.id}" ${String(l.id) === String(selected) ? "selected" : ""}>${esc(l.name)}</option>`).join("");
    }

    function renderRules() {
        rulesEl.innerHTML = state.rules.length ? state.rules.map((rule, index) => `
            <div class="rt-rule" data-index="${index}">
                <span class="rt-num">${index + 1}</span>
                <select class="select select-bordered select-sm" data-field="kind">
                    ${RULE_KINDS.map(([k, label]) => `<option value="${k}" ${k === rule.kind ? "selected" : ""}>${label}</option>`).join("")}
                </select>
                <input type="text" class="input input-bordered input-sm" data-field="value" value="${esc(rule.value)}" placeholder="english">
                <span class="aa-hint">goes to</span>
                <select class="select select-bordered select-sm" data-field="library_id">${libraryOptions(rule.library_id)}</select>
                <div class="rt-actions">
                    <button type="button" class="btn btn-ghost btn-xs" data-move="-1" aria-label="Move up" ${index === 0 ? "disabled" : ""}><i class="fas fa-arrow-up"></i></button>
                    <button type="button" class="btn btn-ghost btn-xs" data-move="1" aria-label="Move down" ${index === state.rules.length - 1 ? "disabled" : ""}><i class="fas fa-arrow-down"></i></button>
                    <button type="button" class="btn btn-ghost btn-xs" data-remove aria-label="Remove rule"><i class="fas fa-trash"></i></button>
                </div>
            </div>`).join("") : '<div class="aa-hint">No rules yet. Every book goes to the default library unless its series is already elsewhere.</div>';
    }

    function readRules() {
        rulesEl.querySelectorAll(".rt-rule").forEach((row) => {
            const rule = state.rules[Number(row.dataset.index)];
            row.querySelectorAll("[data-field]").forEach((el) => { rule[el.dataset.field] = el.value; });
        });
    }

    if (routingCard) {
        document.getElementById("routingAddRule").addEventListener("click", () => {
            readRules();
            const target = state.libraries.find((l) => !l.is_default) || state.libraries[0];
            state.rules.push({ kind: "language", value: "", library_id: target ? target.id : "" });
            renderRules();
        });
        rulesEl.addEventListener("click", (event) => {
            const row = event.target.closest(".rt-rule");
            if (!row) return;
            readRules();
            const index = Number(row.dataset.index);
            const move = event.target.closest("[data-move]");
            if (move) {
                const to = index + Number(move.dataset.move);
                [state.rules[index], state.rules[to]] = [state.rules[to], state.rules[index]];
            } else if (event.target.closest("[data-remove]")) {
                state.rules.splice(index, 1);
            } else {
                return;
            }
            renderRules();
        });
        document.getElementById("routingSave").addEventListener("click", async () => {
            readRules();
            setStatus(routingStatus, "Saving…");
            try {
                await api("/api/libraries/settings", "POST", { keep_series_together: document.getElementById("routingKeepSeries").checked });
                const data = await api("/api/libraries/rules", "PUT", { rules: state.rules });
                state.rules = data.rules;
                renderRules();
                setStatus(routingStatus, "Saved", "text-success");
            } catch (err) {
                setStatus(routingStatus, err.message, "text-error");
            }
        });
        document.getElementById("routingTestButton").addEventListener("click", async () => {
            const result = document.getElementById("routingTestResult");
            const asin = document.getElementById("routingTestAsin").value.trim();
            const body = asin ? { asin } : {
                author: document.getElementById("routingTestAuthor").value.trim(),
                series: document.getElementById("routingTestSeries").value.trim(),
                language: document.getElementById("routingTestLanguage").value.trim(),
            };
            try {
                const data = await api("/api/libraries/route-test", "POST", body);
                const who = data.book && data.book.title ? `${data.book.title}: ` : "";
                result.innerHTML = `${esc(who)}<b>${esc(data.library.name)}</b> <span class="aa-muted">(${esc(data.reason)})</span>`;
                result.className = "aa-helper mt-2";
            } catch (err) {
                result.textContent = err.message;
                result.className = "aa-helper mt-2 text-error";
            }
        });
    }

    load();
})();
