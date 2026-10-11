(function () {
    "use strict";

    const panel = document.getElementById("storagePanel");
    if (!panel) {
        return;
    }

    const form = document.getElementById("storageForm");
    const statusEl = document.getElementById("storageStatus");
    const saveButton = document.getElementById("storageSave");
    const isSetup = panel.dataset.mode === "setup";
    const esc = (value) => AA.escapeHtml(value == null ? "" : String(value));
    const rows = Array.from(panel.querySelectorAll("[data-storage-folder]"));

    const NOTE_CLASS = { error: "aa-err", warning: "aa-warn", info: "aa-info" };
    const APP_DATA_ROWS = [
        ["config_file", "Settings"],
        ["database", "Database"],
        ["secrets", "Credentials"],
        ["cache", "Cache"],
        ["logs", "Logs"],
        ["backups", "Backups"],
    ];

    let saved = {};

    function setStatus(text, cls) {
        if (statusEl) {
            statusEl.textContent = text || "";
            statusEl.className = `aa-hint ${cls || ""}`;
        }
    }

    function inputOf(row) {
        return row.querySelector("input");
    }

    function isDirty() {
        return rows.some((row) => {
            const input = inputOf(row);
            return !input.readOnly && input.value.trim() !== (saved[row.dataset.storageFolder] || "");
        });
    }

    function refreshControls() {
        rows.forEach((row) => {
            const input = inputOf(row);
            row.querySelector('[data-role="reset"]').classList.toggle("hidden", input.readOnly || !input.value.trim());
        });
        if (!isSetup && saveButton) {
            const dirty = isDirty();
            saveButton.disabled = !dirty;
            if (dirty) {
                setStatus("Unsaved changes", "text-warning");
            } else if (statusEl && statusEl.textContent === "Unsaved changes") {
                setStatus("");
            }
        }
    }

    function renderRow(row, folder, docker, notices) {
        const name = row.dataset.storageFolder;
        const input = inputOf(row);
        const lock = row.querySelector('[data-role="lock"]');
        const meta = row.querySelector('[data-role="meta"]');
        const flags = row.querySelector('[data-role="flags"]');

        input.value = folder.value || "";
        input.placeholder = folder.default;
        input.readOnly = Boolean(folder.locked);
        input.classList.remove("aa-invalid");
        lock.classList.toggle("hidden", !folder.locked);
        lock.title = !folder.locked ? ""
            : folder.env_var ? `Set by ${folder.env_var} where AuralArchive is started`
            : "Fixed inside the Docker container. Choose the host folder with a volume mount.";

        const bits = [];
        if (folder.exists && folder.writable) {
            bits.push(`<span class="aa-ok">Ready</span>`);
            bits.push(`<span>${esc(folder.free_human)} free</span>`);
        }
        if (docker && folder.exists) {
            bits.push(`<span>${folder.mounted ? "Mounted from host" : "Inside the container"}</span>`);
        }
        if (!folder.value && folder.exists) {
            bits.push(`<span>Built-in folder</span>`);
        }
        meta.innerHTML = bits.join("");

        flags.innerHTML = notices
            .filter((notice) => notice.folder === name)
            .map((notice) => `<div class="aa-store-flag ${esc(notice.level)}">`
                + `<span>${esc(notice.short || notice.message)}</span></div>`)
            .join("");
    }

    function renderNotices(notices, folderNames) {
        const container = document.getElementById("storageNotices");
        container.innerHTML = notices
            .filter((notice) => !folderNames.includes(notice.folder) && !String(notice.folder || "").startsWith("library:"))
            .map((notice) => `<div role="alert" class="aa-note ${NOTE_CLASS[notice.level] || "aa-info"}">`
                + `<span>${esc(notice.short || notice.message)}</span></div>`)
            .join("");
    }

    function renderAppData(appData) {
        if (!appData) {
            return;
        }
        document.getElementById("storageAppDataSummary").textContent =
            `${appData.data_dir} · ${appData.free_human} free`;
        document.getElementById("storageAppData").innerHTML = APP_DATA_ROWS.map(([key, label]) =>
            `<dt>${esc(label)}</dt><dd>${esc(appData[key])}</dd>`
        ).join("");
    }

    function render(data) {
        const notices = data.notices || [];
        const folders = data.folders || {};
        document.getElementById("storageSubtitle").textContent = data.docker
            ? "Container paths - mount host folders in your compose file first"
            : "Leave blank to use the built-in folder";

        saved = {};
        rows.forEach((row) => {
            const folder = folders[row.dataset.storageFolder];
            if (folder) {
                saved[row.dataset.storageFolder] = folder.value || "";
                renderRow(row, folder, data.docker, notices);
            }
        });
        renderNotices(notices, Object.keys(folders));
        renderAppData(data.app_data);
        refreshControls();
    }

    function showFieldError(name, message) {
        const row = rows.find((r) => r.dataset.storageFolder === name);
        if (!row) {
            return;
        }
        inputOf(row).classList.add("aa-invalid");
        const flags = row.querySelector('[data-role="flags"]');
        flags.innerHTML = `<div class="aa-store-flag error" role="alert">`
            + `<span>${esc(message)}</span></div>`;
    }

    async function load() {
        const { ok, data } = await AA.apiFetch("/settings/api/storage", { method: "GET" });
        if (!ok || !data || data.success === false) {
            setStatus((data && (data.error || data.message)) || "Couldn't load storage settings", "text-error");
            return;
        }
        render(data);
    }

    async function save(event) {
        event.preventDefault();
        const payload = {};
        rows.forEach((row) => {
            const input = inputOf(row);
            input.classList.remove("aa-invalid");
            if (!input.readOnly) {
                payload[row.dataset.storageFolder] = input.value.trim();
            }
        });

        saveButton.disabled = true;
        saveButton.classList.add("loading");
        setStatus("Saving…");
        try {
            const { ok, data } = await AA.apiFetch("/settings/api/storage", {
                method: "POST",
                body: JSON.stringify(payload),
            });
            if (!ok || !data || data.success === false) {
                const fieldErrors = (data && data.field_errors) || {};
                Object.entries(fieldErrors).forEach(([name, message]) => showFieldError(name, message));
                setStatus(Object.keys(fieldErrors).length ? "Fix the highlighted folder"
                    : ((data && data.error) || "Save failed"), "text-error");
                saveButton.disabled = false;
                return;
            }
            if (isSetup) {
                window.location.href = panel.dataset.continueUrl || "/";
                return;
            }
            render(data);
            setStatus("Saved", "text-success");
        } catch (error) {
            setStatus(error.message || "Save failed", "text-error");
            saveButton.disabled = false;
        } finally {
            saveButton.classList.remove("loading");
        }
    }

    rows.forEach((row) => {
        const input = inputOf(row);
        input.addEventListener("input", () => {
            input.classList.remove("aa-invalid");
            refreshControls();
        });
        row.querySelector('[data-role="reset"]').addEventListener("click", () => {
            input.value = "";
            input.focus();
            refreshControls();
        });
    });
    form.addEventListener("submit", save);
    window.addEventListener("beforeunload", (event) => {
        if (!isSetup && isDirty()) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
    load();
})();
