// Settings › Audiobookshelf: connection, library mapping, features, backfill and status
(function () {
    "use strict";

    const panel = document.getElementById("settingsPanel-audiobookshelf");
    if (!panel) return;

    const esc = (value) => AA.escapeHtml(value == null ? "" : String(value));
    const $ = (id) => document.getElementById(id);
    const state = { settings: {}, mapping: [], libraries: [], absLibraries: [], suggestions: {}, status: {} };
    let statusTimer = null;

    async function api(url, method = "GET", body) {
        const options = { method };
        if (body !== undefined) options.body = JSON.stringify(body);
        const { ok, data } = await AA.apiFetch(url, options);
        if (!ok || !data || data.success === false) throw new Error((data && data.error) || "Request failed");
        return data;
    }

    function setStatus(el, text, tone = "") {
        el.textContent = text || "";
        el.className = `aa-helper ${tone}`;
    }

    function when(value) {
        return value ? new Date(value).toLocaleString() : "never";
    }

    // --- Connection -------------------------------------------------------------

    function fillConnection() {
        const s = state.settings;
        $("abs_url").value = s.url || "";
        $("abs_api_key").value = "";
        $("abs_api_key").placeholder = s.has_key ? "Saved - leave blank to keep it" : "Paste the key";
        $("abs_enabled").checked = Boolean(s.enabled);
        $("abs_verify_tls").checked = s.verify_tls !== false;
    }

    function renderTest(result) {
        const box = $("absTestResult");
        box.classList.remove("hidden");
        if (!result.success) {
            $("absTestLine").innerHTML = `<span class="text-error">${esc(result.error)}</span>`;
            $("absCaps").innerHTML = "";
            $("absWarnings").innerHTML = "";
            return;
        }
        const caps = result.capabilities || {};
        $("absTestLine").innerHTML = `Connected to Audiobookshelf <b>${esc(result.version)}</b> as <b>${esc(caps.user)}</b> (${esc(caps.type)})`;
        const rows = [
            [true, "Read books and your listening progress"],
            [caps.is_admin, "Read other users' progress"],
            [caps.is_admin, "Tell Audiobookshelf about new books"],
            [caps.can_update, "Match metadata by ASIN"],
            [caps.is_admin, "Match many books at once"],
        ];
        $("absCaps").innerHTML = rows.map(([ok, text]) =>
            `<div><i class="fas ${ok ? "fa-check text-success" : "fa-xmark text-error"}"></i><span>${esc(text)}</span></div>`).join("");
        $("absWarnings").innerHTML = (result.warnings || []).map((w) =>
            `<div class="aa-store-flag warning"><span>${esc(w)}</span></div>`).join("");
        state.absLibraries = result.libraries || [];
        state.suggestions = result.suggestions || {};
        renderMapping();
    }

    async function testConnection() {
        setStatus($("absConnectionStatus"), "Testing…");
        try {
            const { data } = await AA.apiFetch("/api/abs/test", {
                method: "POST",
                body: JSON.stringify({ url: $("abs_url").value.trim(), api_key: $("abs_api_key").value.trim(),
                                       verify_tls: $("abs_verify_tls").checked }),
            });
            renderTest(data || { success: false, error: "No response" });
            setStatus($("absConnectionStatus"), data && data.success ? "Connection Successful" : "", data && data.success ? "text-success" : "");
        } catch (err) {
            renderTest({ success: false, error: err.message });
            setStatus($("absConnectionStatus"), "");
        }
    }

    async function saveConnection(event) {
        event.preventDefault();
        setStatus($("absConnectionStatus"), "Saving…");
        try {
            const data = await api("/api/abs/settings", "POST", {
                url: $("abs_url").value.trim(), api_key: $("abs_api_key").value.trim(),
                enabled: $("abs_enabled").checked, verify_tls: $("abs_verify_tls").checked,
            });
            state.settings = data.settings;
            fillConnection();
            setStatus($("absConnectionStatus"), "Saved", "text-success");
            if (state.settings.url && state.settings.has_key) testConnection();
            refreshStatus();
        } catch (err) {
            setStatus($("absConnectionStatus"), err.message, "text-error");
        }
    }

    // --- Mapping ----------------------------------------------------------------

    function absOptions(selectedLib, selectedFolder) {
        const opts = ['<option value="">Not mapped</option>'];
        state.absLibraries.forEach((lib) => {
            (lib.folders || []).forEach((folder) => {
                const value = `${lib.id}|${folder.id}`;
                const sel = lib.id === selectedLib && (!selectedFolder || folder.id === selectedFolder) ? "selected" : "";
                const label = lib.folders.length > 1 ? `${lib.name} - ${folder.path}` : `${lib.name} (${folder.path})`;
                opts.push(`<option value="${esc(value)}" ${sel}>${esc(label)}</option>`);
            });
        });
        return opts.join("");
    }

    function renderMapping() {
        const box = $("absMapping");
        if (!state.absLibraries.length) {
            box.innerHTML = '<div class="aa-hint">Test the connection to load Audiobookshelf\'s libraries.</div>';
            $("absSaveMapping").disabled = true;
            return;
        }
        box.innerHTML = state.libraries.map((lib) => {
            const current = state.mapping.find((m) => m.library_id === lib.id);
            const suggestion = state.suggestions[String(lib.id)];
            const selLib = current ? current.abs_library_id : (suggestion && suggestion.abs_library_id);
            const selFolder = current ? current.abs_folder_id : (suggestion && suggestion.abs_folder_id);
            const hint = suggestion ? `${suggestion.matches} of ${suggestion.of} books match` : "";
            return `<div class="abs-map-row" data-map="${lib.id}">
                <div><b>${esc(lib.name)}</b><div class="aa-hint font-mono" style="font-size:10.5px;">${esc(lib.root)}</div></div>
                <select class="select select-bordered select-sm w-full">${absOptions(selLib, selFolder)}</select>
                <span class="abs-sugg">${esc(hint)}</span>
            </div>`;
        }).join("");
        $("absSaveMapping").disabled = false;
        const flags = [];
        state.absLibraries.forEach((lib) => {
            const order = lib.metadata_precedence || [];
            if (order.indexOf("folderStructure") > order.indexOf("absMetadata") && order.includes("absMetadata")) {
                flags.push(`In Audiobookshelf, the ${lib.name} library reads folder names last, so they override everything - titles corrected by a match go back to the folder name on the next scan. Fix it in Audiobookshelf under the library's Settings › Metadata order of precedence.`);
            }
            if (lib.disable_watcher) {
                flags.push(`The ${lib.name} library's folder watcher is off, so new books are announced with a full library scan.`);
            }
        });
        $("absMappingFlags").innerHTML = flags.map((f) => `<div class="aa-store-flag warning"><span>${esc(f)}</span></div>`).join("");
    }

    async function saveMapping() {
        const entries = Array.from(document.querySelectorAll("[data-map]")).map((row) => {
            const [abs_library_id, abs_folder_id] = (row.querySelector("select").value || "|").split("|");
            return { library_id: Number(row.dataset.map), abs_library_id, abs_folder_id };
        });
        setStatus($("absMappingStatus"), "Saving…");
        try {
            const data = await api("/api/abs/mapping", "POST", { entries });
            state.mapping = data.mapping;
            setStatus($("absMappingStatus"), "Saved - linking books now", "text-success");
            refreshStatus();
        } catch (err) {
            setStatus($("absMappingStatus"), err.message, "text-error");
        }
    }

    // --- Features ----------------------------------------------------------------

    function fillFeatures() {
        const s = state.settings;
        const users = s.progress_users || "me";
        const custom = users !== "me" && users !== "all";
        $("abs_progress_users").value = custom ? "custom" : users;
        $("abs_progress_custom").classList.toggle("hidden", !custom);
        $("abs_progress_custom").value = custom ? users : "";
        $("abs_next_in_series").checked = Boolean(s.next_in_series);
        $("abs_notify_on_import").checked = Boolean(s.notify_on_import);
        $("abs_hints").checked = Boolean(s.hints);
        $("abs_metadata_mode").value = s.metadata_mode || "off";
        $("abs_metadata_aa_authority").checked = Boolean(s.metadata_aa_authority);
        $("abs_sync_interval_hours").value = s.sync_interval_hours ?? 6;
        $("absBackfill").classList.toggle("hidden", !s.enabled || (s.metadata_mode || "off") === "off");
    }

    async function saveFeatures(event) {
        event.preventDefault();
        const choice = $("abs_progress_users").value;
        const mode = $("abs_metadata_mode").value;
        if (mode === "match_replace" && state.settings.metadata_mode !== "match_replace") {
            const ok = await AA.showConfirmDialog(
                "Match and replace overwrites details and covers in Audiobookshelf, including edits made there. Continue?",
                "Replace metadata in Audiobookshelf", "Use replace", "btn-warning");
            if (!ok) return;
        }
        setStatus($("absFeaturesStatus"), "Saving…");
        try {
            const data = await api("/api/abs/settings", "POST", {
                progress_users: choice === "custom" ? $("abs_progress_custom").value.trim() || "me" : choice,
                next_in_series: $("abs_next_in_series").checked,
                notify_on_import: $("abs_notify_on_import").checked,
                hints: $("abs_hints").checked,
                metadata_mode: mode,
                metadata_aa_authority: $("abs_metadata_aa_authority").checked,
                sync_interval_hours: Number($("abs_sync_interval_hours").value || 0),
            });
            state.settings = data.settings;
            fillFeatures();
            setStatus($("absFeaturesStatus"), "Saved", "text-success");
        } catch (err) {
            setStatus($("absFeaturesStatus"), err.message, "text-error");
        }
    }

    async function preview() {
        $("absBackfillState").textContent = "Checking Audiobookshelf…";
        try {
            const data = await api("/api/abs/metadata/preview", "POST");
            const g = data.gaps;
            const labels = [["series_number", "series numbers"], ["narrator", "narrators"], ["publisher", "publishers"],
                            ["description", "descriptions"], ["genres", "genres"], ["asin", "ASINs"]];
            $("absBackfillPreview").innerHTML = `<span><b>${data.books}</b> linked books will be matched with ${esc(data.provider)}.</span>`
                + labels.filter(([k]) => g[k]).map(([k, label]) => `<span>Missing ${esc(label)}: <b>${g[k]}</b></span>`).join("");
            $("absRunBackfill").classList.toggle("hidden", !data.books || !data.can_update);
            $("absBackfillState").textContent = data.can_update ? (data.batch ? "" : "One book per second (the key isn't an admin)") : "The key's user can't edit items";
        } catch (err) {
            $("absBackfillState").textContent = err.message;
        }
    }

    async function runBackfill() {
        const replace = state.settings.metadata_mode === "match_replace";
        const ok = await AA.showConfirmDialog(
            replace ? "Match every linked book and replace its details and cover in Audiobookshelf?"
                : "Match every linked book in Audiobookshelf? Only empty fields and missing covers are filled.",
            "Match linked books", "Start", replace ? "btn-warning" : "btn-primary");
        if (!ok) return;
        try {
            await api("/api/abs/metadata/backfill", "POST");
            pollBackfill();
        } catch (err) {
            $("absBackfillState").textContent = err.message;
        }
    }

    async function pollBackfill() {
        try {
            const data = await api("/api/abs/metadata/backfill");
            const b = data.backfill || {};
            $("absBackfillState").textContent = b.running
                ? `${b.message || "Matching"}: ${b.done || 0} of ${b.total || 0}`
                : (b.message ? `${b.message}${b.errors ? ` (${b.errors} failed)` : ""}` : "");
            if (b.running) setTimeout(pollBackfill, 2000);
        } catch (err) { /* status card shows errors */ }
    }

    // --- Status -----------------------------------------------------------------

    function renderStatus(data) {
        const s = data.status || {};
        const r = s.last_sync_result || {};
        $("absLastSync").textContent = s.syncing ? "Syncing…" : `Last sync ${when(s.last_sync)}`;
        const jobs = s.jobs || {};
        const tiles = [
            [s.linked || 0, "linked books"],
            [s.unlinked_count || 0, "not found in Audiobookshelf"],
            [s.orphans_count || 0, "only in Audiobookshelf"],
            [r.progress_entries ?? "-", "progress entries"],
            [jobs.pending || 0, "background jobs waiting"],
        ];
        $("absStats").innerHTML = tiles.map(([n, label]) => `<div class="abs-stat"><b>${esc(n)}</b><span>${esc(label)}</span></div>`).join("");
        const flags = [];
        if (!s.enabled) flags.push(["info", "The connection is off."]);
        if (s.last_error) flags.push(["error", `Last error (${when(s.last_error_at)}): ${s.last_error}`]);
        if (r.note) flags.push(["warning", r.note]);
        (s.failed_jobs || []).slice(0, 3).forEach((j) => flags.push(["warning", `${j.action} failed for ${j.title || "a book"}: ${j.error || ""}`]));
        $("absStatusFlags").innerHTML = flags.map(([lv, t]) => `<div class="aa-store-flag ${lv}"><span>${esc(t)}</span></div>`).join("");
        $("absRetryJobs").classList.toggle("hidden", !(jobs.failed > 0));
        $("absProvider").textContent = s.provider || "audible";

        const orphans = data.orphans || [];
        $("absOrphanCount").textContent = s.orphans_count || 0;
        $("absOrphans").innerHTML = orphans.map((o) =>
            `<div><span>${esc(o.title || "?")}${o.author ? ` <span class="aa-muted">· ${esc(o.author)}</span>` : ""}</span><span class="aa-muted font-mono">${esc(o.rel_path || "")}</span></div>`).join("")
            || '<div><span class="aa-muted">None</span></div>';
        const options = orphans.map((o) => `<option value="${esc(o.abs_item_id)}">${esc(o.title || o.rel_path)}</option>`).join("");
        $("absUnlinkedCount").textContent = s.unlinked_count || 0;
        $("absUnlinked").innerHTML = (data.unlinked || []).map((u) =>
            `<div data-unlinked="${u.book_id}"><span>${esc(u.title || "?")}${u.reason ? ` <span class="aa-muted">· ${esc(u.reason)}</span>` : ""}</span>
                ${options ? `<span class="flex gap-1"><select class="select select-bordered select-xs" style="max-width:220px;">${options}</select><button type="button" class="btn btn-outline btn-xs" data-link>Link</button></span>` : ""}
            </div>`).join("") || '<div><span class="aa-muted">None</span></div>';
        if (s.syncing) {
            clearTimeout(statusTimer);
            statusTimer = setTimeout(refreshStatus, 3000);
        }
    }

    async function refreshStatus() {
        try {
            renderStatus(await api("/api/abs/status"));
        } catch (err) {
            $("absStatusFlags").innerHTML = `<div class="aa-store-flag error"><span>${esc(err.message)}</span></div>`;
        }
    }

    // --- Load and events ------------------------------------------------------------

    async function load() {
        try {
            const data = await api("/api/abs/settings");
            state.settings = data.settings;
            state.mapping = data.mapping || [];
            state.libraries = data.libraries || [];
            fillConnection();
            fillFeatures();
            renderStatus({ status: data.status });
            refreshStatus();
            if (state.settings.enabled && state.settings.url && state.settings.has_key) testConnection();
            if ((data.status.backfill || {}).running) pollBackfill();
        } catch (err) {
            setStatus($("absConnectionStatus"), err.message, "text-error");
        }
    }

    $("absConnectionForm").addEventListener("submit", saveConnection);
    $("absTest").addEventListener("click", testConnection);
    $("absSaveMapping").addEventListener("click", saveMapping);
    $("absFeaturesForm").addEventListener("submit", saveFeatures);
    $("abs_progress_users").addEventListener("change", (e) => $("abs_progress_custom").classList.toggle("hidden", e.target.value !== "custom"));
    $("absPreview").addEventListener("click", preview);
    $("absRunBackfill").addEventListener("click", runBackfill);
    $("absSyncNow").addEventListener("click", async () => {
        try {
            await api("/api/abs/sync", "POST");
            $("absLastSync").textContent = "Syncing…";
            clearTimeout(statusTimer);
            statusTimer = setTimeout(refreshStatus, 2000);
        } catch (err) {
            $("absStatusFlags").innerHTML = `<div class="aa-store-flag error"><span>${esc(err.message)}</span></div>`;
        }
    });
    $("absRetryJobs").addEventListener("click", async () => {
        await api("/api/abs/jobs/retry", "POST").catch(() => null);
        refreshStatus();
    });
    $("absUnlinked").addEventListener("click", async (event) => {
        const button = event.target.closest("[data-link]");
        if (!button) return;
        const row = button.closest("[data-unlinked]");
        try {
            await api("/api/abs/link", "POST", { book_id: Number(row.dataset.unlinked), abs_item_id: row.querySelector("select").value });
            row.innerHTML = '<span class="text-success">Linked</span>';
        } catch (err) {
            button.textContent = "Failed";
        }
    });

    load();
})();
