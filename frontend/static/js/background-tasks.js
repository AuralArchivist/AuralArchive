(function () {
    "use strict";

    const container = document.getElementById("backgroundTaskRows");
    if (!container) {
        return;
    }

    const API = "/api/tasks";
    const REFRESH_MS = 15000;
    const esc = (value) => AA.escapeHtml(value == null ? "" : String(value));

    const UNITS = [[86400, "day"], [3600, "hour"], [60, "minute"], [1, "second"]];

    function every(seconds) {
        const [size, unit] = UNITS.find(([size]) => seconds >= size && seconds % size === 0) || [1, "second"];
        const count = seconds / size;
        return count === 1 ? `Every ${unit}` : `Every ${count} ${unit}s`;
    }

    function meta(task) {
        const bits = [];
        bits.push(`<span>${task.interval_seconds ? esc(every(task.interval_seconds)) : "On request only"}</span>`);
        const last = AA.relativeTime(task.last_finished, false);
        bits.push(`<span>${last ? `Last run ${esc(last)}` : "Never run"}</span>`);
        if (task.next_run && !task.paused) {
            bits.push(`<span>Next ${esc(AA.relativeTime(task.next_run, true))}</span>`);
        }
        return bits.join("");
    }

    function state(task) {
        if (task.running) {
            return "Running";
        }
        if (task.paused) {
            return "Paused";
        }
        return task.waiting_for_ready ? "Waiting for a connection" : "Scheduled";
    }

    function row(task) {
        const id = `task_${esc(task.name)}`;
        const failed = task.last_error
            ? `<div class="aa-store-flag warning"><span>Last run failed: ${esc(task.last_error)}</span></div>` : "";
        return `<div class="aa-store-row" data-task="${esc(task.name)}">
            <div class="aa-store-name">
                <span><b>${esc(task.label)}</b><small>${esc(task.description)}</small></span>
            </div>
            <div class="aa-store-ctl">
                <div class="aa-store-input">
                    <label class="aa-task-state" for="${id}">
                        <input id="${id}" type="checkbox" class="toggle toggle-success toggle-sm" data-task-toggle ${task.paused ? "" : "checked"} />
                        <span class="aa-hint">${state(task)}</span>
                    </label>
                    <button type="button" class="btn btn-ghost btn-sm" data-task-action="run" ${task.running ? "disabled" : ""}>${task.running ? "Running…" : "Run now"}</button>
                </div>
                <div class="aa-store-meta">${meta(task)}</div>
                <div class="aa-store-flags">${failed}</div>
            </div>
        </div>`;
    }

    async function load() {
        const { ok, data } = await AA.apiFetch(API, { method: "GET" });
        if (!ok || !data || !data.success) {
            container.innerHTML = '<span class="aa-hint text-error">Could not load background tasks</span>';
            return;
        }
        container.innerHTML = data.tasks.length ? data.tasks.map(row).join("")
            : '<span class="aa-hint">Background tasks start shortly after AuralArchive starts</span>';
    }

    async function act(control, action) {
        const name = control.closest("[data-task]").dataset.task;
        control.disabled = true;
        await AA.apiFetch(`${API}/${encodeURIComponent(name)}/${action}`, { method: "POST", body: "{}" });
        await load();
    }

    container.addEventListener("click", (event) => {
        const button = event.target.closest("[data-task-action]");
        if (button) {
            act(button, button.dataset.taskAction);
        }
    });

    container.addEventListener("change", (event) => {
        const toggle = event.target.closest("[data-task-toggle]");
        if (toggle) {
            act(toggle, toggle.checked ? "resume" : "pause");
        }
    });

    load();
    setInterval(() => {
        if (!document.hidden && container.offsetParent !== null) {
            load();
        }
    }, REFRESH_MS);
})();
