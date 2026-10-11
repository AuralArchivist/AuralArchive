(function () {
    "use strict";


    function formatDateTime(isoString) {
        if (!isoString) return "";
        try {
            return new Date(isoString).toLocaleString();
        } catch (e) {
            return isoString;
        }
    }

    function formatNumber(value, decimals) {
        if (value == null || Number.isNaN(Number(value))) return "0";
        return Number(value).toLocaleString(undefined, {
            minimumFractionDigits: decimals || 0,
            maximumFractionDigits: decimals || 0,
        });
    }

    function notifyError(message, err) {
        console.error(message, err);
        if (typeof showNotification === "function") {
            showNotification(message, "error");
        }
    }

    function showStatus(el, msg, isError) {
        if (!el) return;
        el.textContent = msg;
        el.classList.remove("hidden", "text-success", "text-error");
        el.classList.add(isError ? "text-error" : "text-success");
        setTimeout(function () {
            el.textContent = "";
            el.classList.add("hidden");
        }, 4000);
    }


    document.querySelectorAll("#serviceTabs .service-tab").forEach(function (btn) {
        btn.addEventListener("click", function () {
            document.querySelectorAll("#serviceTabs .service-tab").forEach(function (b) {
                b.classList.remove("tab-active");
            });
            btn.classList.add("tab-active");
            const svc = btn.dataset.service;
            document.querySelectorAll(".service-pane").forEach(function (p) {
                p.classList.add("hidden");
            });
            const pane = document.getElementById("servicePane-" + svc);
            if (pane) pane.classList.remove("hidden");
        });
    });


    async function loadConnectedServices() {
        try {
            const resp = await AA.apiFetch("/api/profile/me");
            if (!resp.ok) return;
            const data = resp.data || {};

            if (data.mam_linked) {
                loadMamStats();
                loadMamActiveDownloads();
            } else {
                showMamDisconnected();
            }

            if (data.hardcover_linked) {
                loadHardcoverStats();
                loadHardcoverSettings();
                loadHardcoverSyncQueue();
            } else {
                showHardcoverDisconnected();
            }

            if (data.audible_linked) {
                loadAudibleStatus();
            } else {
                showAudibleDisconnected();
            }
            loadAudibleDownloadSettings();
        } catch (err) {
            notifyError("Failed to load connected services", err);
        }
    }


    function _setMamDot(state) {
        const dot = document.getElementById("mamDot");
        if (!dot) return;
        dot.className = "w-2 h-2 rounded-full shrink-0 " + (
            state === "connected" ? "bg-success" :
            state === "expired"   ? "bg-warning"  :
                                    "bg-base-content/20"
        );
    }

    function _setMamBadge(label, cssClass) {
        const badge = document.getElementById("mamStatusBadge");
        if (!badge) return;
        badge.textContent = label;
        badge.className = "badge badge-sm shrink-0 " + cssClass;
        badge.classList.remove("hidden");
    }

    function showMamConnected(account) {
        document.getElementById("mamConnectForm").classList.add("hidden");
        document.getElementById("mamStatsWidget").classList.remove("hidden");
        document.getElementById("mamExpiredAlert").classList.add("hidden");
        _setMamDot("connected");
        if (account) applyMamStats(account);
    }

    function showMamDisconnected() {
        document.getElementById("mamConnectForm").classList.remove("hidden");
        document.getElementById("mamStatsWidget").classList.add("hidden");
        document.getElementById("mamExpiredAlert").classList.add("hidden");
        document.getElementById("mamCookieInput").value = "";
        _setMamBadge("Not connected", "badge-ghost");
        _setMamDot("disconnected");
    }

    function showMamExpired() {
        document.getElementById("mamConnectForm").classList.remove("hidden");
        document.getElementById("mamStatsWidget").classList.add("hidden");
        document.getElementById("mamExpiredAlert").classList.remove("hidden");
        _setMamBadge("Session expired", "badge-warning");
        _setMamDot("expired");
    }


    function applyMamStats(account) {
        const username = account.username || "—";

        document.getElementById("mamUsername").textContent = username;
        document.getElementById("mamClass").textContent    = account.class_name || "";

        const ratioRaw = account.ratio;
        const ratioNum = (ratioRaw != null) ? parseFloat(ratioRaw) : NaN;
        const ratioEl     = document.getElementById("mamRatio");
        const ratioLblEl  = document.getElementById("mamRatioLabel");
        const ratioStnEl  = document.getElementById("mamRatioStanding");

        if (!isNaN(ratioNum)) {
            var clr, lbl;
            if (ratioNum >= 1.0)      { clr = "text-success"; lbl = "Healthy";  }
            else if (ratioNum >= 0.5) { clr = "text-warning"; lbl = "At Risk";  }
            else                       { clr = "text-error";   lbl = "Critical"; }
            ratioEl.textContent    = ratioRaw;
            ratioEl.className      = "stat-value text-2xl " + clr;
            ratioLblEl.textContent = lbl;
            ratioLblEl.className   = "stat-desc font-medium " + clr;
            if (ratioStnEl) { ratioStnEl.textContent = lbl; ratioStnEl.className = "text-sm font-medium " + clr; }
        } else {
            ratioEl.textContent    = "N/A";
            ratioEl.className      = "stat-value text-2xl text-base-content/40";
            ratioLblEl.textContent = "";
            if (ratioStnEl) ratioStnEl.textContent = "—";
        }

        document.getElementById("mamDownloaded").textContent = account.downloaded || "—";
        document.getElementById("mamUploaded").textContent   = account.uploaded   || "—";
        document.getElementById("mamBonus").textContent      = (account.bonus_points ?? 0).toLocaleString();
        document.getElementById("mamWedges").textContent     = (account.wedge_count  ?? 0).toLocaleString();

        const snatchEl = document.getElementById("mamSnatches");
        if (snatchEl) snatchEl.textContent = (account.snatch_count != null) ? account.snatch_count.toLocaleString() : "—";

        const connEl = document.getElementById("mamConnectableStatus");
        if (connEl && account.connectable != null) {
            connEl.textContent = account.connectable ? "Yes" : "No";
            connEl.className   = "text-sm font-medium " + (account.connectable ? "text-success" : "text-error");
        }

        const hnrStatusEl = document.getElementById("mamHnrStatus");
        if (hnrStatusEl) {
            const v = account.hnr_count ?? 0;
            hnrStatusEl.textContent = v > 0 ? v + " violation" + (v !== 1 ? "s" : "") : "Clean";
            hnrStatusEl.className   = "text-sm font-medium " + (v > 0 ? "text-error" : "text-success");
        }

        const seedingVal  = account.seeding_count  ?? 0;
        const leechingVal = account.leeching_count ?? 0;
        const totalActive = seedingVal + leechingVal;

        const activeTotalEl = document.getElementById("mamActiveTotal");
        const seedingEl     = document.getElementById("mamSeeding");
        const leechingEl    = document.getElementById("mamLeeching");
        if (activeTotalEl) activeTotalEl.textContent = totalActive.toLocaleString();
        if (seedingEl)     seedingEl.textContent     = seedingVal.toLocaleString();
        if (leechingEl)    leechingEl.textContent    = leechingVal.toLocaleString();

        const unsatEl    = document.getElementById("mamUnsat");
        const unsatLimEl = document.getElementById("mamUnsatLimit");
        if (unsatEl) {
            const v = account.unsat_count ?? 0;
            unsatEl.textContent = v.toLocaleString();
            unsatEl.className = "font-semibold tabular-nums" + (v > 0 ? " text-warning" : "");
            if (unsatLimEl && account.unsat_limit != null) unsatLimEl.textContent = "/ " + account.unsat_limit;
        }
    }


    async function loadMamStats() {
        try {
            const resp = await AA.apiFetch("/api/profile/mam/stats");
            if (!resp.ok) return;
            const data = resp.data || {};

            if (!data.linked) {
                showMamDisconnected();
                return;
            }
            if (data.expired) {
                showMamExpired();
                return;
            }
            showMamConnected(data.account);
        } catch (err) {
            notifyError("Failed to load MAM stats", err);
        }
    }


    const mamConnectBtn     = document.getElementById("mamConnectBtn");
    const mamConnectStatus  = document.getElementById("mamConnectStatus");
    const mamConnectSpinner = document.getElementById("mamConnectSpinner");

    if (mamConnectBtn) {
        mamConnectBtn.addEventListener("click", async function () {
            const cookie = (document.getElementById("mamCookieInput").value || "").trim();
            if (!cookie) {
                showStatus(mamConnectStatus, "Please paste your mam_id cookie value", true);
                return;
            }

            mamConnectBtn.disabled = true;
            if (mamConnectSpinner) mamConnectSpinner.classList.remove("hidden");
            mamConnectBtn.querySelector(".mamConnectLabel").textContent = "Connecting…";
            if (mamConnectStatus) mamConnectStatus.classList.add("hidden");

            try {
                const resp = await AA.apiFetch("/api/profile/mam/connect", {
                    method: "POST",
                    body: JSON.stringify({ cookie }),
                });
                const d = resp.data || {};
                if (!resp.ok || d.success === false) {
                    showStatus(mamConnectStatus, d.error || "Connection failed", true);
                } else {
                    showMamConnected(d.account);
                    loadMamActiveDownloads();
                }
            } catch (err) {
                showStatus(mamConnectStatus, "Connection failed", true);
            } finally {
                mamConnectBtn.disabled = false;
                if (mamConnectSpinner) mamConnectSpinner.classList.add("hidden");
                mamConnectBtn.querySelector(".mamConnectLabel").textContent = "Connect";
            }
        });
    }


    const mamRefreshBtn = document.getElementById("mamRefreshBtn");
    if (mamRefreshBtn) {
        mamRefreshBtn.addEventListener("click", function () {
            mamRefreshBtn.disabled = true;
            Promise.all([loadMamStats(), loadMamActiveDownloads()]).finally(function () {
                mamRefreshBtn.disabled = false;
            });
        });
    }


    const mamDisconnectBtn = document.getElementById("mamDisconnectBtn");
    if (mamDisconnectBtn) {
        mamDisconnectBtn.addEventListener("click", async function () {
            const confirmed = await AA.showConfirmDialog(
                "Disconnect MAM?",
                "This will remove your session cookie and de-provision the MAM indexer. You can reconnect at any time.",
                "Disconnect",
                "btn-error"
            );
            if (!confirmed) return;

            mamDisconnectBtn.disabled = true;
            try {
                const resp = await AA.apiFetch("/api/profile/mam/disconnect", { method: "POST" });
                if (resp.ok) showMamDisconnected();
                else notifyError("MAM disconnect failed");
            } catch (err) {
                notifyError("MAM disconnect failed", err);
            } finally {
                mamDisconnectBtn.disabled = false;
            }
        });
    }


    function _downloadStatusLabel(status) {
        return { DOWNLOADING: "Downloading", QUEUED: "Queued", PROCESSING: "Processing", SEEDING: "Seeding" }[status] || status;
    }

    function _downloadStatusClass(status) {
        return { DOWNLOADING: "badge-info", QUEUED: "badge-ghost", PROCESSING: "badge-warning", SEEDING: "badge-success" }[status] || "badge-ghost";
    }

    function _progressColour(status) {
        return { DOWNLOADING: "info", SEEDING: "success", PROCESSING: "warning" }[status] || "primary";
    }

    function renderMamActiveDownloads(downloads) {
        const section = document.getElementById("mamActiveDownloadsSection");
        const list    = document.getElementById("mamActiveList");
        const count   = document.getElementById("mamActiveCount");
        if (!section) return;

        list.innerHTML = "";

        if (!downloads.length) {
            section.classList.add("hidden");
            return;
        }

        section.classList.remove("hidden");
        count.textContent = downloads.length;

        downloads.forEach(function (d) {
            const pct = Math.min(100, Math.max(0, d.progress || 0));
            const tr  = document.createElement("tr");
            tr.innerHTML = [
                '<td><div class="w-10 h-14 bg-base-300 rounded shrink-0"></div></td>',
                '<td class="font-medium">' + AA.escapeHtml(d.title) + '</td>',
                '<td><span class="badge badge-sm ' + _downloadStatusClass(d.status) + '">' + AA.escapeHtml(_downloadStatusLabel(d.status)) + '</span></td>',
                '<td>',
                  '<div class="flex items-center gap-3">',
                    '<progress class="progress progress-' + _progressColour(d.status) + ' w-32 sm:w-48" value="' + pct + '" max="100"></progress>',
                    '<span class="text-xs text-base-content/50 tabular-nums w-8">' + pct.toFixed(0) + '%</span>',
                  '</div>',
                '</td>',
            ].join("");
            list.appendChild(tr);
        });
    }

    async function loadMamActiveDownloads() {
        try {
            const resp = await AA.apiFetch("/api/profile/mam/active-downloads");
            if (!resp.ok) return;
            const data = resp.data || {};
            if (!data.linked) return;
            renderMamActiveDownloads(data.downloads || []);
        } catch (err) {
            notifyError("Failed to load MAM active downloads", err);
        }
    }


    function _setHardcoverDot(state) {
        const dot = document.getElementById("hardcoverDot");
        if (!dot) return;
        dot.className = "w-2 h-2 rounded-full shrink-0 " + (
            state === "connected" ? "bg-success" :
            state === "expired"   ? "bg-warning"  :
                                    "bg-base-content/20"
        );
    }

    function _setHardcoverBadge(label, cssClass) {
        const badge = document.getElementById("hardcoverStatusBadge");
        if (!badge) return;
        badge.textContent = label;
        badge.className = "badge badge-sm shrink-0 " + cssClass;
        badge.classList.remove("hidden");
    }

    function showHardcoverConnected(username, account) {
        document.getElementById("hardcoverConnectForm").classList.add("hidden");
        document.getElementById("hardcoverStatsWidget").classList.remove("hidden");
        document.getElementById("hardcoverExpiredAlert").classList.add("hidden");
        _setHardcoverDot("connected");
        document.getElementById("hardcoverUsername").textContent = username || "—";
        if (account) applyHardcoverStats(account);
    }

    function showHardcoverDisconnected() {
        const form = document.getElementById("hardcoverConnectForm");
        const widget = document.getElementById("hardcoverStatsWidget");
        if (!form || !widget) return;
        form.classList.remove("hidden");
        widget.classList.add("hidden");
        document.getElementById("hardcoverExpiredAlert").classList.add("hidden");
        document.getElementById("hardcoverTokenInput").value = "";
        _setHardcoverBadge("Not connected", "badge-ghost");
        _setHardcoverDot("disconnected");
    }

    function showHardcoverExpired() {
        document.getElementById("hardcoverConnectForm").classList.remove("hidden");
        document.getElementById("hardcoverStatsWidget").classList.add("hidden");
        document.getElementById("hardcoverExpiredAlert").classList.remove("hidden");
        _setHardcoverBadge("Token expired", "badge-warning");
        _setHardcoverDot("expired");
    }

    function applyHardcoverStats(account) {
        document.getElementById("hardcoverWant").textContent = (account.want_to_read ?? 0).toLocaleString();
        document.getElementById("hardcoverReading").textContent = (account.currently_reading ?? 0).toLocaleString();
        document.getElementById("hardcoverRead").textContent = (account.read ?? 0).toLocaleString();
        document.getElementById("hardcoverOwned").textContent = (account.owned ?? 0).toLocaleString();
        const avg = account.avg_rating;
        document.getElementById("hardcoverAvgRating").textContent = (avg != null) ? Number(avg).toFixed(2) : "—";
    }

    async function loadHardcoverStats() {
        try {
            const resp = await AA.apiFetch("/api/profile/hardcover/stats");
            if (!resp.ok) return;
            const data = resp.data || {};
            if (!data.linked) {
                showHardcoverDisconnected();
                return;
            }
            if (data.expired) {
                showHardcoverExpired();
                return;
            }
            showHardcoverConnected(data.username, data.account);
        } catch (err) {
            notifyError("Failed to load Hardcover stats", err);
        }
    }


    const hardcoverConnectBtn     = document.getElementById("hardcoverConnectBtn");
    const hardcoverConnectStatus  = document.getElementById("hardcoverConnectStatus");
    const hardcoverConnectSpinner = document.getElementById("hardcoverConnectSpinner");

    if (hardcoverConnectBtn) {
        hardcoverConnectBtn.addEventListener("click", async function () {
            const token = (document.getElementById("hardcoverTokenInput").value || "").trim();
            if (!token) {
                showStatus(hardcoverConnectStatus, "Please paste your Hardcover API token", true);
                return;
            }

            hardcoverConnectBtn.disabled = true;
            if (hardcoverConnectSpinner) hardcoverConnectSpinner.classList.remove("hidden");
            hardcoverConnectBtn.querySelector(".hardcoverConnectLabel").textContent = "Connecting…";
            if (hardcoverConnectStatus) hardcoverConnectStatus.classList.add("hidden");

            try {
                const resp = await AA.apiFetch("/api/profile/hardcover/connect", {
                    method: "POST",
                    body: JSON.stringify({ token }),
                });
                const d = resp.data || {};
                if (!resp.ok || d.success === false) {
                    showStatus(hardcoverConnectStatus, d.error || "Connection failed", true);
                } else {
                    showHardcoverConnected(d.account && d.account.username);
                    loadHardcoverStats();
                    loadHardcoverSettings();
                    loadHardcoverSyncQueue();
                }
            } catch (err) {
                showStatus(hardcoverConnectStatus, "Connection failed", true);
            } finally {
                hardcoverConnectBtn.disabled = false;
                if (hardcoverConnectSpinner) hardcoverConnectSpinner.classList.add("hidden");
                hardcoverConnectBtn.querySelector(".hardcoverConnectLabel").textContent = "Connect Account";
            }
        });
    }


    const hardcoverRefreshBtn = document.getElementById("hardcoverRefreshBtn");
    if (hardcoverRefreshBtn) {
        hardcoverRefreshBtn.addEventListener("click", function () {
            hardcoverRefreshBtn.disabled = true;
            Promise.all([loadHardcoverStats(), loadHardcoverSyncQueue()]).finally(function () {
                hardcoverRefreshBtn.disabled = false;
            });
        });
    }


    const hardcoverDisconnectBtn = document.getElementById("hardcoverDisconnectBtn");
    if (hardcoverDisconnectBtn) {
        hardcoverDisconnectBtn.addEventListener("click", async function () {
            const confirmed = await AA.showConfirmDialog(
                "Disconnect Hardcover?",
                "This will remove your stored API token. You can reconnect at any time.",
                "Disconnect",
                "btn-error"
            );
            if (!confirmed) return;

            hardcoverDisconnectBtn.disabled = true;
            try {
                const resp = await AA.apiFetch("/api/profile/hardcover/disconnect", { method: "POST" });
                if (resp.ok) showHardcoverDisconnected();
                else notifyError("Hardcover disconnect failed");
            } catch (err) {
                notifyError("Hardcover disconnect failed", err);
            } finally {
                hardcoverDisconnectBtn.disabled = false;
            }
        });
    }


    async function loadHardcoverSettings() {
        try {
            const resp = await AA.apiFetch("/api/profile/hardcover/settings");
            if (!resp.ok) return;
            const data = resp.data || {};
            document.getElementById("hcAutoPushStatus").checked = !!data.auto_push_status;
            document.getElementById("hcAutoPushOwned").checked = !!data.auto_push_owned;
            document.getElementById("hcAutoCreate").checked = !!data.auto_create_on_hardcover;
            document.getElementById("hcCacheRefreshMinutes").value = data.cache_refresh_interval_minutes || 180;
        } catch (err) {
            notifyError("Failed to load Hardcover settings", err);
        }
    }

    const hcSaveSettingsBtn = document.getElementById("hcSaveSettingsBtn");
    if (hcSaveSettingsBtn) {
        hcSaveSettingsBtn.addEventListener("click", async function () {
            const hcSettingsStatus = document.getElementById("hcSettingsStatus");
            hcSaveSettingsBtn.disabled = true;
            try {
                const resp = await AA.apiFetch("/api/profile/hardcover/settings", {
                    method: "POST",
                    body: JSON.stringify({
                        auto_push_status: document.getElementById("hcAutoPushStatus").checked,
                        auto_push_owned: document.getElementById("hcAutoPushOwned").checked,
                        auto_create_on_hardcover: document.getElementById("hcAutoCreate").checked,
                        cache_refresh_interval_minutes: parseInt(document.getElementById("hcCacheRefreshMinutes").value, 10) || 180,
                    }),
                });
                showStatus(hcSettingsStatus, resp.ok ? "Saved" : "Failed to save", !resp.ok);
            } catch (err) {
                showStatus(hcSettingsStatus, "Failed to save", true);
            } finally {
                hcSaveSettingsBtn.disabled = false;
            }
        });
    }


    function _syncStatusClass(status) {
        return { pending: "badge-info", sent: "badge-success", failed: "badge-error" }[status] || "badge-ghost";
    }

    async function loadHardcoverSyncQueue() {
        const body = document.getElementById("hcSyncQueueBody");
        if (!body) return;
        try {
            const resp = await AA.apiFetch("/api/hardcover/sync-queue");
            if (!resp.ok) return;
            const entries = (resp.data || {}).entries || [];

            body.innerHTML = "";
            if (!entries.length) {
                body.innerHTML = '<tr id="hcSyncQueueEmpty"><td colspan="4" class="text-center text-base-content/40 py-3">No pending or recent syncs</td></tr>';
                return;
            }

            entries.forEach(function (entry) {
                const tr = document.createElement("tr");
                const retryBtn = entry.status === "failed"
                    ? '<button class="btn btn-ghost btn-xs" data-retry-id="' + entry.id + '">Retry</button>'
                    : "";
                tr.innerHTML = [
                    '<td>' + AA.escapeHtml(entry.book_title || ("Book #" + entry.book_id)) + '</td>',
                    '<td class="text-xs text-base-content/50">' + AA.escapeHtml(entry.action || "") + '</td>',
                    '<td><span class="badge badge-sm ' + _syncStatusClass(entry.status) + '">' + AA.escapeHtml(entry.status) + '</span></td>',
                    '<td>' + retryBtn + '</td>',
                ].join("");
                body.appendChild(tr);
            });

            body.querySelectorAll("[data-retry-id]").forEach(function (btn) {
                btn.addEventListener("click", async function () {
                    btn.disabled = true;
                    try {
                        await AA.apiFetch("/api/hardcover/sync-queue/" + btn.dataset.retryId + "/retry", { method: "POST" });
                        loadHardcoverSyncQueue();
                    } catch (err) {
                        btn.disabled = false;
                    }
                });
            });
        } catch (err) {
            notifyError("Failed to load Hardcover sync queue", err);
        }
    }


    let _audiblePendingSessionId = null;

    function _setAudibleDot(state) {
        const dot = document.getElementById("audibleDot");
        if (!dot) return;
        dot.className = "w-2 h-2 rounded-full shrink-0 " + (
            state === "connected" ? "bg-success" :
            state === "expired"   ? "bg-warning"  :
                                    "bg-base-content/20"
        );
    }

    function _setAudibleBadge(label, cssClass) {
        const badge = document.getElementById("audibleStatusBadge");
        if (!badge) return;
        badge.textContent = label;
        badge.className = "badge badge-sm shrink-0 " + cssClass;
        badge.classList.remove("hidden");
    }

    function _showAudibleOtpStep(show) {
        const loginStep = document.getElementById("audibleLoginStep");
        const otpStep = document.getElementById("audibleOtpStep");
        if (loginStep) loginStep.classList.toggle("hidden", show);
        if (otpStep) otpStep.classList.toggle("hidden", !show);
        if (show) {
            const input = document.getElementById("audibleOtpInput");
            document.getElementById("audibleOtpBox")?.classList.remove("otp-error");
            if (input) {
                input.value = "";
                input.focus();
            }
        }
    }

    function showAudibleConnected(account) {
        document.getElementById("audibleConnectForm").classList.add("hidden");
        document.getElementById("audibleStatsWidget").classList.remove("hidden");
        document.getElementById("audibleExpiredAlert").classList.add("hidden");
        _setAudibleDot("connected");
        _audiblePendingSessionId = null;
        _showAudibleOtpStep(false);
        if (account) {
            document.getElementById("audibleAccountName").textContent = account.name || account.email || "Connected";
            document.getElementById("audibleMarketplace").textContent = account.marketplace || "";
        }
        loadAudibleLibraryStatus();
    }

    function showAudibleDisconnected() {
        const form = document.getElementById("audibleConnectForm");
        const widget = document.getElementById("audibleStatsWidget");
        if (!form || !widget) return;
        form.classList.remove("hidden");
        widget.classList.add("hidden");
        document.getElementById("audibleExpiredAlert").classList.add("hidden");
        document.getElementById("audibleEmailInput").value = "";
        document.getElementById("audiblePasswordInput").value = "";
        document.getElementById("audibleOtpInput").value = "";
        _showAudibleOtpStep(false);
        _audiblePendingSessionId = null;
        _setAudibleBadge("Not connected", "badge-ghost");
        _setAudibleDot("disconnected");
    }

    async function loadAudibleStatus() {
        try {
            const resp = await AA.apiFetch("/api/audible/auth/status");
            const data = resp.data || {};
            if (resp.ok && data.authenticated) {
                showAudibleConnected(data.account || {});
            } else {
                showAudibleDisconnected();
            }
        } catch (err) {
            console.error("Failed to load Audible status:", err);
            showAudibleDisconnected();
        }
    }

    async function loadAudibleLibraryStatus() {
        try {
            const resp = await AA.apiFetch("/api/audible/library/status");
            if (!resp.ok) return;
            const status = (resp.data || {}).data || {};
            const cache = status.cached_library || {};
            const countEl = document.getElementById("audibleCacheCount");
            const updatedEl = document.getElementById("audibleCacheUpdated");
            if (countEl) countEl.textContent = cache.available ? formatNumber(cache.book_count ?? 0) : "Not cached";
            if (updatedEl) {
                const ts = cache.last_updated || cache.lastUpdated;
                updatedEl.textContent = ts ? "Updated " + formatDateTime(ts) : "";
            }
        } catch (err) {
            notifyError("Failed to load Audible library status", err);
        }
    }


    const audibleConnectBtn = document.getElementById("audibleConnectBtn");
    if (audibleConnectBtn) {
        audibleConnectBtn.addEventListener("click", async function () {
            const statusTarget = document.getElementById("audibleConnectStatus");
            const spinner = document.getElementById("audibleConnectSpinner");
            const email = (document.getElementById("audibleEmailInput").value || "").trim();
            const password = document.getElementById("audiblePasswordInput").value || "";
            const countrySelect = document.getElementById("audibleMarketplaceSelect");
            const country = countrySelect ? countrySelect.value : "us";

            if (!email || !password) {
                showStatus(statusTarget, "Email and password are required", true);
                return;
            }

            audibleConnectBtn.disabled = true;
            if (spinner) spinner.classList.remove("hidden");
            audibleConnectBtn.querySelector(".audibleConnectLabel").textContent = "Connecting…";
            if (statusTarget) statusTarget.classList.add("hidden");

            try {
                const resp = await AA.apiFetch("/api/audible/auth/start", {
                    method: "POST",
                    body: JSON.stringify({ username: email, password, country_code: country }),
                });
                const d = resp.data || {};
                if (!resp.ok || d.success === false) {
                    showStatus(statusTarget, d.error || "Authentication failed", true);
                } else if (d.requires_otp) {
                    _audiblePendingSessionId = d.session_id;
                    _showAudibleOtpStep(true);
                } else {
                    showAudibleConnected(d.account || {});
                }
            } catch (err) {
                showStatus(statusTarget, "Authentication failed", true);
            } finally {
                audibleConnectBtn.disabled = false;
                if (spinner) spinner.classList.add("hidden");
                audibleConnectBtn.querySelector(".audibleConnectLabel").textContent = "Connect Account";
                document.getElementById("audiblePasswordInput").value = "";
            }
        });
    }

    const audibleOtpSubmitBtn = document.getElementById("audibleOtpSubmitBtn");
    if (audibleOtpSubmitBtn) {
        audibleOtpSubmitBtn.addEventListener("click", async function () {
            const statusTarget = document.getElementById("audibleOtpStatus");
            const spinner = document.getElementById("audibleOtpSpinner");
            const otpValue = (document.getElementById("audibleOtpInput").value || "").trim();

            if (!_audiblePendingSessionId) {
                showStatus(statusTarget, "Session expired. Restart authentication.", true);
                return;
            }
            if (!/^\d{6}$/.test(otpValue)) {
                showStatus(statusTarget, "Enter the 6-digit code to continue", true);
                return;
            }

            audibleOtpSubmitBtn.disabled = true;
            if (spinner) spinner.classList.remove("hidden");
            audibleOtpSubmitBtn.querySelector(".audibleOtpSubmitLabel").textContent = "Verifying…";
            if (statusTarget) statusTarget.classList.add("hidden");

            try {
                const resp = await AA.apiFetch("/api/audible/auth/submit-otp", {
                    method: "POST",
                    body: JSON.stringify({ session_id: _audiblePendingSessionId, otp_code: otpValue }),
                });
                const d = resp.data || {};
                if (!resp.ok || d.success === false) {
                    showStatus(statusTarget, d.error || "OTP verification failed", true);
                    document.getElementById("audibleOtpBox")?.classList.add("otp-error");
                } else {
                    showAudibleConnected(d.account || {});
                }
            } catch (err) {
                showStatus(statusTarget, "OTP verification failed", true);
                document.getElementById("audibleOtpBox")?.classList.add("otp-error");
            } finally {
                audibleOtpSubmitBtn.disabled = false;
                if (spinner) spinner.classList.add("hidden");
                audibleOtpSubmitBtn.querySelector(".audibleOtpSubmitLabel").textContent = "Verify";
                document.getElementById("audibleOtpInput").value = "";
            }
        });
    }

    const audibleOtpInput = document.getElementById("audibleOtpInput");
    const audibleOtpBox = document.getElementById("audibleOtpBox");
    if (audibleOtpInput && audibleOtpSubmitBtn) {
        audibleOtpInput.addEventListener("input", function () {
            const digits = audibleOtpInput.value.replace(/\D/g, "").slice(0, 6);
            if (digits !== audibleOtpInput.value) audibleOtpInput.value = digits;
            if (audibleOtpBox) audibleOtpBox.classList.remove("otp-error");
            if (digits.length === 6 && !audibleOtpSubmitBtn.disabled) audibleOtpSubmitBtn.click();
        });
        audibleOtpInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && !audibleOtpSubmitBtn.disabled) {
                event.preventDefault();
                audibleOtpSubmitBtn.click();
            }
        });
    }

    const audibleOtpCancelBtn = document.getElementById("audibleOtpCancelBtn");
    if (audibleOtpCancelBtn) {
        audibleOtpCancelBtn.addEventListener("click", function () {
            _audiblePendingSessionId = null;
            _showAudibleOtpStep(false);
        });
    }


    const audibleRefreshBtn = document.getElementById("audibleRefreshBtn");
    if (audibleRefreshBtn) {
        audibleRefreshBtn.addEventListener("click", function () {
            audibleRefreshBtn.disabled = true;
            loadAudibleLibraryStatus().finally(function () {
                audibleRefreshBtn.disabled = false;
            });
        });
    }

    const audibleDisconnectBtn = document.getElementById("audibleDisconnectBtn");
    if (audibleDisconnectBtn) {
        audibleDisconnectBtn.addEventListener("click", async function () {
            const confirmed = await AA.showConfirmDialog(
                "Revoke Audible authentication?",
                "This will remove your stored device token. You can reconnect at any time.",
                "Revoke",
                "btn-error"
            );
            if (!confirmed) return;

            audibleDisconnectBtn.disabled = true;
            try {
                const resp = await AA.apiFetch("/api/audible/auth/revoke", { method: "POST" });
                if (resp.ok) showAudibleDisconnected();
                else notifyError("Audible revoke failed");
            } catch (err) {
                notifyError("Audible revoke failed", err);
            } finally {
                audibleDisconnectBtn.disabled = false;
            }
        });
    }


    async function _runAudibleAction(button, statusElId, url, options, buildMessage) {
        const statusTarget = document.getElementById(statusElId);
        if (button) button.disabled = true;
        if (statusTarget) statusTarget.textContent = "Working…";
        try {
            const resp = await AA.apiFetch(url, options);
            const d = (resp.data && resp.data.data) || resp.data || {};
            if (!resp.ok || d.success === false) {
                throw new Error(d.error || "Request failed");
            }
            if (statusTarget) statusTarget.textContent = buildMessage ? buildMessage(d) : (d.message || "Done.");
            return d;
        } catch (err) {
            if (statusTarget) statusTarget.textContent = err.message || "Request failed";
            throw err;
        } finally {
            if (button) button.disabled = false;
        }
    }

    const audibleSyncFullBtn = document.getElementById("audibleSyncFullBtn");
    if (audibleSyncFullBtn) {
        audibleSyncFullBtn.addEventListener("click", function () {
            _runAudibleAction(audibleSyncFullBtn, "audibleSyncStatus", "/api/audible/library/sync/full",
                { method: "POST", body: JSON.stringify({}) },
                (d) => d.message || "Full sync started.")
                .then(() => loadAudibleLibraryStatus())
                .catch(() => {});
        });
    }

    const audibleSyncQuickBtn = document.getElementById("audibleSyncQuickBtn");
    if (audibleSyncQuickBtn) {
        audibleSyncQuickBtn.addEventListener("click", function () {
            _runAudibleAction(audibleSyncQuickBtn, "audibleSyncStatus", "/api/audible/library/sync/quick",
                { method: "POST", body: JSON.stringify({}) },
                (d) => d.message || "Quick sync started.")
                .then(() => loadAudibleLibraryStatus())
                .catch(() => {});
        });
    }

    const audibleRefreshCacheBtn = document.getElementById("audibleRefreshCacheBtn");
    if (audibleRefreshCacheBtn) {
        audibleRefreshCacheBtn.addEventListener("click", function () {
            _runAudibleAction(audibleRefreshCacheBtn, "audibleSyncStatus", "/api/audible/library/refresh",
                { method: "POST" },
                (d) => d.message || "Library cache refreshed.")
                .then(() => loadAudibleLibraryStatus())
                .catch(() => {});
        });
    }

    const audibleValidateBtn = document.getElementById("audibleValidateBtn");
    if (audibleValidateBtn) {
        audibleValidateBtn.addEventListener("click", function () {
            _runAudibleAction(audibleValidateBtn, "audibleSyncStatus", "/api/audible/library/validate-credentials", {},
                (d) => d.message || (d.valid ? "Credentials validated." : "Credentials require attention."))
                .catch(() => {});
        });
    }

    const audibleDownloadAllBtn = document.getElementById("audibleDownloadAllBtn");
    if (audibleDownloadAllBtn) {
        audibleDownloadAllBtn.addEventListener("click", function () {
            const format = document.getElementById("audibleFormatSelect").value || "aaxc";
            const quality = document.getElementById("audibleQualitySelect").value || "best";
            const includePdf = document.getElementById("audibleIncludePdf").checked;
            const includeCover = document.getElementById("audibleIncludeCover").checked;
            const includeChapters = document.getElementById("audibleIncludeChapters").checked;
            const jobs = Math.max(1, parseInt(document.getElementById("audibleConcurrentDownloads").value, 10) || 1);

            _runAudibleAction(audibleDownloadAllBtn, "audibleToolsStatus", "/api/audible/library/download/all", {
                method: "POST",
                body: JSON.stringify({
                    format, quality,
                    include_pdf: includePdf,
                    include_cover: includeCover,
                    include_chapters: includeChapters,
                    jobs,
                }),
            }, (d) => d.message || "Bulk download started.").catch(() => {});
        });
    }

    const audibleExportBtn = document.getElementById("audibleExportBtn");
    if (audibleExportBtn) {
        audibleExportBtn.addEventListener("click", function () {
            _runAudibleAction(audibleExportBtn, "audibleToolsStatus", "/api/audible/library/export?format=json", {},
                (d) => "Export includes " + formatNumber(d.book_count ?? 0) + " titles.")
                .catch(() => {});
        });
    }


    function _extractStatLabel(raw) {
        if (!raw) return "Unknown";
        if (typeof raw === "object") {
            return raw.name || raw.title || raw.label || raw.asin || "Unknown";
        }
        if (typeof raw === "string") {
            const trimmed = raw.trim();
            if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
                try {
                    const normalized = trimmed.includes("'") ? trimmed.replace(/'/g, '"') : trimmed;
                    const parsed = JSON.parse(normalized);
                    if (parsed && typeof parsed === "object") {
                        return parsed.name || parsed.title || parsed.label || parsed.asin || raw;
                    }
                } catch (_) { /* fall through */ }
            }
            const nameMatch = raw.match(/"name"\s*:\s*"([^"]+)"/) || raw.match(/'name'\s*:\s*'([^']+)'/);
            if (nameMatch && nameMatch[1]) return nameMatch[1];
            return raw;
        }
        return String(raw);
    }

    function _renderAudibleTopList(title, icon, entries) {
        if (!Array.isArray(entries) || !entries.length) return "";
        const items = entries.slice(0, 5).map(([raw, count]) => {
            const label = AA.escapeHtml(_extractStatLabel(raw));
            return '<div class="pkv"><span class="k">' + label + '</span><span class="v">' + formatNumber(count) + '</span></div>';
        }).join("");
        return '<div class="ptoplist"><div class="psechead"><i class="fas ' + icon + '"></i> ' + AA.escapeHtml(title) + '</div>' + items + '</div>';
    }

    function _renderAudibleStats(stats) {
        const container = document.getElementById("audibleStatsContent");
        if (!container) return;

        const kv = function (label, value) {
            return '<div class="pkv"><span class="k">' + label + '</span><span class="v">' + value + '</span></div>';
        };

        const overview =
            kv("Total titles", formatNumber(stats.total_books ?? 0)) +
            kv("Hours owned", formatNumber(stats.total_duration_hours ?? 0, 1)) +
            kv("Average rating", (stats.average_rating ? formatNumber(stats.average_rating, 2) : "N/A")) +
            kv("Titles with ratings", formatNumber(stats.books_with_ratings ?? 0));

        const diversity =
            kv("Unique authors", formatNumber(stats.unique_authors ?? 0)) +
            kv("Unique narrators", formatNumber(stats.unique_narrators ?? 0)) +
            kv("Unique genres", formatNumber(stats.unique_genres ?? 0)) +
            kv("Unique series", formatNumber(stats.unique_series ?? 0));

        let html =
            '<div class="pstats-detail">' +
                '<div class="pstat-sec"><div class="psechead"><i class="fas fa-book"></i> Overview</div>' + overview + '</div>' +
                '<div class="pstat-sec"><div class="psechead"><i class="fas fa-shapes"></i> Diversity</div>' + diversity + '</div>' +
            '</div>';

        const topLists = [
            _renderAudibleTopList("Top Authors", "fa-user-pen", stats.top_authors),
            _renderAudibleTopList("Top Narrators", "fa-microphone", stats.top_narrators),
            _renderAudibleTopList("Top Genres", "fa-tags", stats.top_genres),
        ].filter(Boolean);
        if (topLists.length) {
            html += '<div class="ptoplists">' + topLists.join("") + '</div>';
        }

        container.innerHTML = html;
    }

    const audibleStatsLoadBtn = document.getElementById("audibleStatsLoadBtn");
    if (audibleStatsLoadBtn) {
        audibleStatsLoadBtn.addEventListener("click", async function () {
            const container = document.getElementById("audibleStatsContent");
            audibleStatsLoadBtn.disabled = true;
            if (container) container.textContent = "Loading statistics…";
            try {
                const resp = await AA.apiFetch("/api/audible/library/stats");
                const payload = (resp.data && resp.data.data) || resp.data || {};
                const stats = payload.stats || payload;
                if (!resp.ok || !stats) {
                    if (container) container.textContent = "Failed to load statistics.";
                } else {
                    _renderAudibleStats(stats);
                }
            } catch (err) {
                if (container) container.textContent = "Failed to load statistics: " + err.message;
            } finally {
                audibleStatsLoadBtn.disabled = false;
            }
        });
    }


    async function loadAudibleDownloadSettings() {
        try {
            const resp = await AA.apiFetch("/settings/api/media-management");
            if (!resp.ok) return;
            const d = ((resp.data || {}).config || {}).audible_downloads || {};
            document.getElementById("audibleFormatSelect").value = d.format || "aaxc";
            document.getElementById("audibleQualitySelect").value = d.quality || "best";
            document.getElementById("audibleAaxFallback").checked = d.aax_fallback !== undefined ? !!d.aax_fallback : true;
            document.getElementById("audibleSaveVoucher").checked = d.save_voucher !== undefined ? !!d.save_voucher : true;
            document.getElementById("audibleIncludeCover").checked = !!d.include_cover;
            document.getElementById("audibleIncludeChapters").checked = !!d.include_chapters;
            document.getElementById("audibleIncludePdf").checked = !!d.include_pdf;
            document.getElementById("audibleConcurrentDownloads").value = d.concurrent_downloads || 1;
        } catch (err) {
            notifyError("Failed to load Audible download settings", err);
        }
    }

    const audibleSaveSettingsBtn = document.getElementById("audibleSaveSettingsBtn");
    if (audibleSaveSettingsBtn) {
        audibleSaveSettingsBtn.addEventListener("click", async function () {
            const statusTarget = document.getElementById("audibleSettingsStatus");
            audibleSaveSettingsBtn.disabled = true;
            try {
                const payload = {
                    audible_downloads: {
                        format: document.getElementById("audibleFormatSelect").value || "aaxc",
                        quality: document.getElementById("audibleQualitySelect").value || "best",
                        aax_fallback: document.getElementById("audibleAaxFallback").checked,
                        save_voucher: document.getElementById("audibleSaveVoucher").checked,
                        include_cover: document.getElementById("audibleIncludeCover").checked,
                        include_chapters: document.getElementById("audibleIncludeChapters").checked,
                        include_pdf: document.getElementById("audibleIncludePdf").checked,
                        concurrent_downloads: Math.max(1, parseInt(document.getElementById("audibleConcurrentDownloads").value, 10) || 1),
                    },
                };
                const resp = await AA.apiFetch("/settings/api/media-management", {
                    method: "POST",
                    body: JSON.stringify(payload),
                });
                showStatus(statusTarget, resp.ok ? "Saved" : "Failed to save", !resp.ok);
                if (resp.ok) loadAudibleDownloadSettings();
            } catch (err) {
                showStatus(statusTarget, "Failed to save", true);
            } finally {
                audibleSaveSettingsBtn.disabled = false;
            }
        });
    }

    loadConnectedServices();
})();
