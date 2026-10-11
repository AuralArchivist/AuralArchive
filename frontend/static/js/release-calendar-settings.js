// Settings › Discovery › Release Calendar: watches, look-ahead/look-back and refresh
(function () {
    "use strict";

    const card = document.getElementById("releaseCalendarSettings");
    if (!card) return;

    const esc = (value) => AA.escapeHtml(value == null ? "" : String(value));
    const list = document.getElementById("rcWatchList");
    const kind = document.getElementById("rcKind");
    const query = document.getElementById("rcQuery");
    const tagSearch = document.getElementById("rcTagSearch");
    const tagResults = document.getElementById("rcTagResults");
    const addButton = document.getElementById("rcAdd");
    const addStatus = document.getElementById("rcAddStatus");
    const lookahead = document.getElementById("rcLookahead");
    const lookback = document.getElementById("rcLookback");
    const status = document.getElementById("rcStatus");
    let selectedTag = null;

    function setStatus(el, text, tone = "") {
        el.textContent = text;
        el.className = `${el.id === "rcStatus" ? "aa-helper" : "aa-hint"} ${tone}`;
    }

    async function api(url, method = "GET", body) {
        const options = { method, headers: { "Content-Type": "application/json" } };
        if (body !== undefined) options.body = JSON.stringify(body);
        const resp = await AA.apiFetch(url, options);
        const data = resp.data || {};
        if (!resp.ok || data.success === false) throw new Error(data.error || "Request failed");
        return data;
    }

    function when(value) {
        return value ? new Date(value.replace(" ", "T")).toLocaleString() : "never";
    }

    function renderWatches(watches) {
        if (!watches.length) {
            list.innerHTML = '<span class="aa-helper">No watches yet. Add a tag or keyword below.</span>';
            return;
        }
        list.innerHTML = watches.map((w) => `
            <div class="rcs-watch" data-watch="${w.id}">
                <span class="rcs-kind">${w.kind === "tag" ? "Tag" : "Keyword"}</span>
                <div class="rcs-watch-body">
                    <div class="rcs-watch-label">${esc(w.label)}</div>
                    <div class="aa-hint">${w.titles} title${w.titles === 1 ? "" : "s"} · last refreshed ${esc(when(w.last_synced_at))}</div>
                </div>
                <input type="checkbox" class="toggle toggle-success toggle-sm" data-watch-toggle ${w.enabled ? "checked" : ""} aria-label="Enabled">
                <button type="button" class="btn btn-ghost btn-xs" data-watch-delete aria-label="Delete watch"><i class="fas fa-trash"></i></button>
            </div>`).join("");
    }

    async function load() {
        try {
            const [watches, settings] = await Promise.all([api("/api/calendar/watches"), api("/api/calendar/settings")]);
            renderWatches(watches.watches);
            lookahead.value = settings.settings.lookahead_days;
            lookback.value = settings.settings.lookback_days;
            const job = settings.job || {};
            if (settings.running) {
                setStatus(status, "Refreshing now…", "text-info");
            } else {
                const next = job.next_run ? ` · next ${when(job.next_run)}` : "";
                setStatus(status, `Last refresh ${when(job.last_run)}${next}. Schedule: Settings › Metadata.`);
            }
        } catch (err) {
            list.innerHTML = `<span class="aa-helper text-error">${esc(err.message)}</span>`;
        }
    }

    function updateKind() {
        const isKeyword = kind.value === "keyword";
        query.classList.toggle("hidden", !isKeyword);
        tagSearch.placeholder = isKeyword ? "Within tag (optional)" : "Search tags, e.g. Space Opera";
    }

    const searchTags = AA.debounce(async () => {
        const term = tagSearch.value.trim();
        selectedTag = null;
        if (term.length < 2) {
            tagResults.classList.add("hidden");
            return;
        }
        try {
            const data = await api(`/api/discovery/categories/search?q=${encodeURIComponent(term)}`);
            const results = (data.results || []).slice(0, 20);
            tagResults.innerHTML = results.length
                ? results.map((r) => `<button type="button" class="rcs-result" data-tag-id="${esc(r.id)}" data-tag-name="${esc(r.name)}">
                        <b>${esc(r.name)}</b><span>${esc(r.path)}</span></button>`).join("")
                : '<div class="rcs-result-empty">No matching tags. Run Sync Now above if the list is empty.</div>';
            tagResults.classList.remove("hidden");
        } catch (err) {
            tagResults.classList.add("hidden");
        }
    }, 250);

    tagResults.addEventListener("click", (event) => {
        const pick = event.target.closest("[data-tag-id]");
        if (!pick) return;
        selectedTag = { id: pick.dataset.tagId, name: pick.dataset.tagName };
        tagSearch.value = selectedTag.name;
        tagResults.classList.add("hidden");
    });

    document.addEventListener("click", (event) => {
        if (!event.target.closest(".rcs-tag")) tagResults.classList.add("hidden");
    });

    function pendingWatch() {
        return (kind.value === "keyword" && query.value.trim()) || tagSearch.value.trim();
    }

    // Returns true when a watch was added (or nothing was pending), false on error
    async function addWatch() {
        const body = { kind: kind.value };
        if (kind.value === "keyword") body.query = query.value.trim();
        if (selectedTag) body.category_id = selectedTag.id;
        else if (tagSearch.value.trim()) {
            setStatus(addStatus, "Pick a tag from the list", "text-error");
            return false;
        }
        addButton.disabled = true;
        try {
            const data = await api("/api/calendar/watches", "POST", body);
            query.value = "";
            tagSearch.value = "";
            selectedTag = null;
            // Start filling the calendar now rather than waiting for the daily refresh
            await api("/api/calendar/refresh", "POST").catch(() => null);
            setStatus(addStatus, `Added "${data.watch.label}". Finding its releases now…`, "text-success");
            await load();
            setTimeout(load, 8000);
            return true;
        } catch (err) {
            setStatus(addStatus, err.message, "text-error");
            return false;
        } finally {
            addButton.disabled = false;
        }
    }

    addButton.addEventListener("click", addWatch);
    [query, tagSearch].forEach((input) => input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            tagResults.classList.add("hidden");
            addWatch();
        }
    }));

    list.addEventListener("change", async (event) => {
        const toggle = event.target.closest("[data-watch-toggle]");
        if (!toggle) return;
        const id = toggle.closest("[data-watch]").dataset.watch;
        try {
            await api(`/api/calendar/watches/${id}`, "PUT", { enabled: toggle.checked });
        } catch (err) {
            toggle.checked = !toggle.checked;
            setStatus(status, err.message, "text-error");
        }
    });

    list.addEventListener("click", async (event) => {
        const del = event.target.closest("[data-watch-delete]");
        if (!del) return;
        const row = del.closest("[data-watch]");
        const label = row.querySelector(".rcs-watch-label").textContent;
        const ok = await AA.showConfirmDialog(`Delete "${label}" and remove its titles from the calendar?`, "Delete watch", "Delete");
        if (!ok) return;
        try {
            await api(`/api/calendar/watches/${row.dataset.watch}`, "DELETE");
            load();
        } catch (err) {
            setStatus(status, err.message, "text-error");
        }
    });

    document.getElementById("rcSave").addEventListener("click", async () => {
        if (pendingWatch() && !(await addWatch())) return;
        try {
            await api("/api/calendar/settings", "POST", {
                lookahead_days: Number(lookahead.value),
                lookback_days: Number(lookback.value),
            });
            setStatus(status, "Saved.", "text-success");
        } catch (err) {
            setStatus(status, err.message, "text-error");
        }
    });

    document.getElementById("rcRefresh").addEventListener("click", async () => {
        try {
            await api("/api/calendar/refresh", "POST");
            setStatus(status, "Refresh started. Titles appear as each watch finishes.", "text-info");
            setTimeout(load, 8000);
        } catch (err) {
            setStatus(status, err.message, "text-error");
        }
    });

    kind.addEventListener("change", updateKind);
    tagSearch.addEventListener("input", searchTags);
    updateKind();
    load();
})();
