(function () {
    "use strict";

    const form = document.getElementById("metadataRefreshForm");
    if (!form) {
        return;
    }

    const API = "/settings/api/metadata-refresh";
    const JOBS = ["library", "series", "books", "authors", "hardcover", "calendar"];
    const esc = (value) => AA.escapeHtml(value == null ? "" : String(value));
    const statusEl = document.getElementById("mrStatus");
    const saveButton = document.getElementById("mrSave");
    const fields = Array.from(form.querySelectorAll("[data-mr-field]"));
    const enabledInput = document.getElementById("mr_enabled");

    let saved = null;
    let pollTimer = null;

    function setStatus(text, cls) {
        statusEl.textContent = text || "";
        statusEl.className = `aa-hint ${cls || ""}`;
    }

    function setSelect(select, value) {
        const str = String(value);
        if (!Array.from(select.options).some((o) => o.value === str)) {
            select.add(new Option(`Every ${str} days`, str));
        }
        select.value = str;
    }

    function currentValues() {
        const values = { enabled: enabledInput.checked };
        fields.forEach((el) => { values[el.dataset.mrField] = Number(el.value); });
        return values;
    }

    function isDirty() {
        if (!saved) {
            return false;
        }
        const now = currentValues();
        return Object.keys(now).some((key) => now[key] !== saved[key]);
    }

    function refreshSave() {
        const dirty = isDirty();
        saveButton.disabled = !dirty;
        if (dirty) {
            setStatus("Unsaved changes", "text-warning");
        } else if (statusEl.textContent === "Unsaved changes") {
            setStatus("");
        }
    }

    function renderJob(job, info, data) {
        const row = form.querySelector(`[data-mr-job="${job}"]`);
        const meta = row.querySelector('[data-role="meta"]');
        const flags = row.querySelector('[data-role="flags"]');
        const runButton = row.querySelector("[data-mr-run]");

        const bits = [];
        const last = AA.relativeTime(info.last_run, false);
        bits.push(`<span>${last ? `Last run ${esc(last)}` : "Never run"}</span>`);
        if (data.settings.enabled && info.interval_days > 0 && info.next_run) {
            bits.push(`<span>Next ${esc(AA.relativeTime(info.next_run, true))}</span>`);
        } else if (!data.settings.enabled || info.interval_days === 0) {
            bits.push(`<span>Not scheduled</span>`);
        }
        const result = info.last_result;
        if (job === "library" && result && result.details && !result.skipped) {
            bits.push(`<span>${esc(librarySummary(result.details))}</span>`);
        } else if (job === "hardcover" && result && result.total != null) {
            bits.push(`<span>${esc(result.updated)} of ${esc(result.total)} linked</span>`);
        } else if (result && result.total != null) {
            bits.push(`<span>${esc(result.updated)} of ${esc(result.total)} updated${result.failed ? `, ${esc(result.failed)} failed` : ""}</span>`);
        }
        meta.innerHTML = bits.join("");

        const notes = [];
        const blocked = job === "library" && result && result.details && (result.details.missing_blocked || []);
        const unreachable = job === "library" && result && result.details && (result.details.unreachable || []);
        if (unreachable && unreachable.length) {
            notes.push(["warning", `Skipped ${unreachable.map((u) => u.name).join(", ")}: not reachable. `
                + `${result.details.held || 0} books in ${unreachable.length === 1 ? "it were" : "them were"} left as they are.`]);
        }
        if (blocked && blocked.length) {
            notes.push(["warning", `Held back: ${blocked.length} books would be marked missing at once (limit ${result.details.missing_limit}). `
                + "Check the library folder is mounted and correct, then apply if these really are gone.", true]);
        }
        if (job === "hardcover" && !data.hardcover_connected) {
            notes.push(["warning", "Needs Hardcover - connect your account in Settings › Connected Services"]);
        } else if (result && result.skipped) {
            notes.push(["warning", result.skipped]);
        }
        if (job === "authors" && data.authors_missing) {
            notes.push(["info", `${data.authors_missing} author${data.authors_missing === 1 ? "" : "s"} not fetched yet - done automatically in the background`]);
        }
        flags.innerHTML = notes.map(([level, text, applyButton]) =>
            `<div class="aa-store-flag ${level}">`
            + `<span>${esc(text)}${applyButton ? ` <button type="button" class="btn btn-warning btn-xs" data-mr-apply-missing>Apply anyway</button>` : ""}</span></div>`
        ).join("");

        const running = data.running === job;
        runButton.disabled = running || info.queued || (job === "hardcover" && !data.hardcover_connected);
        runButton.innerHTML = running ? "Running…" : info.queued
            ? "Queued"
            : "Run now";
    }

    function librarySummary(d) {
        const parts = [`${d.checked || 0} checked`];
        [["relinked", "relinked"], ["missing", "missing"], ["recovered", "found again"],
         ["adopted", "new imported"], ["review", "need review"]].forEach(([key, label]) => {
            if (d[key]) {
                parts.push(`${d[key]} ${label}`);
            }
        });
        return parts.join(" · ");
    }

    function renderRunning(data) {
        const box = document.getElementById("mrRunning");
        const running = Boolean(data.running);
        box.classList.toggle("hidden", !running);
        if (!running) {
            return;
        }
        const { done, total } = data.progress || {};
        document.getElementById("mrRunningLabel").textContent = `Refreshing ${data.running_label || data.running}`;
        document.getElementById("mrRunningCount").textContent = total ? `${done} / ${total}` : "";
        const bar = document.getElementById("mrProgress");
        bar.max = total || 1;
        bar.value = done || 0;
    }

    function render(data, { keepEdits = false } = {}) {
        if (!keepEdits || !isDirty()) {
            saved = { enabled: Boolean(data.settings.enabled) };
            fields.forEach((el) => { saved[el.dataset.mrField] = data.settings[el.dataset.mrField]; });
            enabledInput.checked = saved.enabled;
            fields.forEach((el) => setSelect(el, saved[el.dataset.mrField]));
        }
        JOBS.forEach((job) => renderJob(job, data.jobs[job], data));
        renderRunning(data);
        refreshSave();
        schedulePoll(Boolean(data.running) || JOBS.some((job) => data.jobs[job].queued));
    }

    function schedulePoll(active) {
        clearTimeout(pollTimer);
        pollTimer = window.setTimeout(() => {
            if (document.hidden) {
                schedulePoll(active);
                return;
            }
            load({ keepEdits: true });
        }, active ? 3000 : 30000);
    }

    async function load(options = {}) {
        const { ok, data } = await AA.apiFetch(API, { method: "GET" });
        if (!ok || !data || data.success === false) {
            setStatus((data && (data.error || data.message)) || "Couldn't load metadata settings", "text-error");
            schedulePoll(false);
            return;
        }
        render(data, options);
    }

    async function post(url, body) {
        const { ok, data } = await AA.apiFetch(url, { method: "POST", body: JSON.stringify(body || {}) });
        if (!ok || !data || data.success === false) {
            throw new Error((data && (data.message || data.error)) || "Request failed");
        }
        return data;
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        saveButton.disabled = true;
        saveButton.classList.add("loading");
        setStatus("Saving…");
        try {
            render(await post(API, currentValues()));
            setStatus("Saved", "text-success");
        } catch (error) {
            setStatus(error.message, "text-error");
            saveButton.disabled = false;
        } finally {
            saveButton.classList.remove("loading");
        }
    });

    form.querySelectorAll("[data-mr-run]").forEach((button) => {
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                render(await post(`${API}/run`, { job: button.dataset.mrRun }), { keepEdits: true });
            } catch (error) {
                setStatus(error.message, "text-error");
                button.disabled = false;
            }
        });
    });

    form.addEventListener("click", async (event) => {
        const apply = event.target.closest("[data-mr-apply-missing]");
        if (!apply) {
            return;
        }
        const confirmed = typeof AA.showConfirmDialog === "function"
            ? await AA.showConfirmDialog("Mark all of these books as missing? Only do this if their files really are gone.",
                "Apply library check", "Mark missing", "btn-warning")
            : window.confirm("Mark all of these books as missing?");
        if (!confirmed) {
            return;
        }
        apply.disabled = true;
        try {
            render(await post(`${API}/library/apply-missing`), { keepEdits: true });
            setStatus("Library check re-running without the limit…");
        } catch (error) {
            setStatus(error.message, "text-error");
            apply.disabled = false;
        }
    });

    const stopButton = document.getElementById("mrStop");
    stopButton.addEventListener("click", async () => {
        stopButton.disabled = true;
        try {
            render(await post(`${API}/stop`), { keepEdits: true });
            setStatus("Stopping after the current item…");
        } catch (error) {
            setStatus(error.message, "text-error");
        } finally {
            stopButton.disabled = false;
        }
    });

    enabledInput.addEventListener("change", refreshSave);
    fields.forEach((el) => el.addEventListener("change", refreshSave));
    window.addEventListener("beforeunload", (event) => {
        if (isDirty()) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
    load();
})();
