"use strict";

(function () {
    const CLIENT_DEFINITIONS = {
        qbittorrent: {
            label: "qBittorrent",
            defaults: {
                enabled: true,
                auto_download: false,
                host: "127.0.0.1",
                port: 8080,
                auth_mode: "api_key",
                username: "",
                password: "",
                api_key: "",
                category: "auralarchive",
                path_mappings: [],
                download_path_remote: "",
                download_path_local: ""
            }
        },
        sabnzbd: {
            label: "SABnzbd",
            defaults: {
                enabled: true,
                auto_download: false,
                host: "127.0.0.1",
                port: 8080,
                api_key: "",
                url_base: "/api",
                use_ssl: false,
                verify_cert: true,
                category: ""
            }
        },
        nzbget: {
            label: "NZBGet",
            defaults: {
                enabled: true,
                auto_download: false,
                host: "127.0.0.1",
                port: 6789,
                username: "nzbget",
                password: "",
                use_ssl: false,
                verify_cert: true,
                category: ""
            }
        }
    };

    const CLIENT_ORDER = Object.keys(CLIENT_DEFINITIONS);
    const DEFAULT_CLIENT_TYPE = CLIENT_ORDER[0];
    const MAX_PATH_MAPPINGS = 5;

    const INDEXER_TYPE_LABELS = {
        jackett: "Jackett",
        prowlarr: "Prowlarr",
        nzbhydra2: "NZBHydra2",
        direct: "Direct"
    };

    const DEFAULT_INDEXER_PRIORITY = 100;
    const VALID_MAM_SEARCH_TYPES = ["all", "default", "fl", "fl-vip", "vip", "nvip", "active", "inactive", "nmeta"];

    const state = {
        config: {},
        downloadClients: {},
        indexers: {},
        namingTemplates: [],
        mediaSettings: {},
        downloadSettings: {},
        selectedClientKey: null,
        selectedIndexerKey: null,
        addingIndexer: false,
        lastLoaded: null
    };

    function maskApiKey(value) {
        const apiKey = (value || "").trim();
        if (!apiKey) {
            return "";
        }
        if (apiKey.length <= 4) {
            return apiKey;
        }
        return `${apiKey.slice(0, 4)}${"*".repeat(apiKey.length - 4)}`;
    }

    function isDirectIndexerType(type) {
        return (type || "").toLowerCase() === "direct";
    }

    function isNzbhydra2Type(type) {
        return (type || "").toLowerCase() === "nzbhydra2";
    }

    function isProwlarrType(type) {
        return (type || "").toLowerCase() === "prowlarr";
    }

    function isMamIndexerKey(indexerKey) {
        return (indexerKey || "").toLowerCase() === "myanonamouse";
    }

    function updateIndexerFieldVisibility(typeValue, indexerKey) {
        const isDirect = isDirectIndexerType(typeValue);
        const isNzbhydra2 = isNzbhydra2Type(typeValue);
        const isProwlarr = isProwlarrType(typeValue);
        const isMam = isDirect && isMamIndexerKey(indexerKey);

        document.querySelectorAll('[data-indexer-field="standard"]').forEach((element) => {
            element.classList.toggle("hidden", isDirect || isNzbhydra2 || isProwlarr);
        });
        document.querySelectorAll('[data-indexer-field="api-key"]').forEach((element) => {
            element.classList.toggle("hidden", isDirect);
        });
        document.querySelectorAll('[data-indexer-field="base-url"]').forEach((element) => {
            element.classList.toggle("hidden", !isDirect && !isNzbhydra2 && !isProwlarr);
        });
        document.querySelectorAll('[data-indexer-field="direct"]').forEach((element) => {
            element.classList.toggle("hidden", !isDirect);
        });
        document.querySelectorAll('[data-indexer-field="mam"]').forEach((element) => {
            element.classList.toggle("hidden", !isMam);
        });
        if (isMam) {
            loadMamAccountWidget();
        }
    }

    async function loadMamAccountWidget() {
        const statusEl = document.getElementById("mamAccountStatus");
        const ratioEl = document.getElementById("mamAccountRatio");
        const classEl = document.getElementById("mamAccountClass");
        const bonusEl = document.getElementById("mamAccountBonus");
        const wedgesEl = document.getElementById("mamAccountWedges");
        if (!statusEl || !ratioEl || !classEl || !bonusEl || !wedgesEl) {
            return;
        }

        statusEl.textContent = "Loading account info…";
        try {
            const response = await fetch("/api/profile/mam/stats");
            const data = await response.json();

            if (!data.linked) {
                statusEl.textContent = "MAM account not linked. Connect via Settings → Connected Services.";
                [ratioEl, classEl, bonusEl, wedgesEl].forEach((el) => { el.textContent = "—"; });
                return;
            }
            if (data.expired || !data.account) {
                statusEl.textContent = data.error || "Session cookie may have expired - reconnect via Profile.";
                return;
            }

            const account = data.account;
            ratioEl.textContent = account.ratio != null ? account.ratio : "—";
            classEl.textContent = account.class_name || "—";
            bonusEl.textContent = account.bonus_points != null ? account.bonus_points : "—";
            wedgesEl.textContent = account.wedge_count != null ? account.wedge_count : "—";
            statusEl.textContent = "";
        } catch (err) {
            statusEl.textContent = "Could not load MAM account info.";
        }
    }

    async function handleMamBuyWedge() {
        const button = document.getElementById("mamBuyWedgeButton");
        const statusEl = document.getElementById("mamAccountStatus");
        if (!button) {
            return;
        }

        const confirmed = await AA.showConfirmDialog(
            "Spend bonus points on a freeleech wedge? This cannot be undone.",
            "Buy Freeleech Wedge",
            "Buy Wedge",
            "btn-primary"
        );
        if (!confirmed) {
            return;
        }

        button.disabled = true;
        try {
            const response = await fetch("/api/profile/mam/buy-wedges", { method: "POST" });
            const data = await response.json();
            if (data.success) {
                if (statusEl) {
                    statusEl.textContent = "Wedge purchased.";
                }
                loadMamAccountWidget();
            } else if (statusEl) {
                statusEl.textContent = data.error || "Purchase failed.";
            }
        } catch (err) {
            if (statusEl) {
                statusEl.textContent = "Purchase request failed.";
            }
        } finally {
            button.disabled = false;
        }
    }

    function parseCategoriesList(rawValue) {
        if (!rawValue) {
            return [];
        }
        if (Array.isArray(rawValue)) {
            return rawValue.map((entry) => entry.toString().trim()).filter(Boolean);
        }
        return rawValue
            .toString()
            .split(',')
            .map((entry) => entry.trim())
            .filter(Boolean);
    }

    function sanitizeSearchType(value) {
        const normalized = (value || "").toString().trim().toLowerCase();
        if (normalized === "default") {
            return "all";
        }
        return VALID_MAM_SEARCH_TYPES.includes(normalized) ? normalized : "all";
    }

    function hydrateIndexersFromConfig() {
        if (!state.config || typeof state.config !== "object") {
            return;
        }

        Object.entries(state.config).forEach(([section, values]) => {
            if (!section.toLowerCase().startsWith("indexer:") || !values || typeof values !== "object") {
                return;
            }

            const key = section.split(':', 2)[1];
            if (!key || state.indexers[key]) {
                return;
            }

            const name = values.name || key.replace(/_/g, ' ');
            const feedUrl = values.feed_url || values.url || "";
            const baseUrl = values.base_url || "";
            const sessionId = values.session_id || "";
            const apiKey = values.api_key || values.key || "";
            const type = (values.type || inferIndexerType(key, values.protocol)).toLowerCase();
            const protocol = (values.protocol || (isDirectIndexerType(type) ? "direct" : "torznab")).toLowerCase();
            const priority = toNumeric(values.priority, DEFAULT_INDEXER_PRIORITY);
            const categories = parseCategoriesList(values.categories);
            const searchType = sanitizeSearchType(values.search_type || values.searchType);
            const verifySsl = values.verify_ssl != null ? toBoolean(values.verify_ssl) : true;
            const timeout = toNumeric(values.timeout, 30);
            const rateLimitRps = toNumeric(values.rate_limit_requests_per_second || values["rate_limit.request_per_second"], 1);
            const rateLimitConcurrent = toNumeric(values.rate_limit_max_concurrent || values["rate_limit.max_concurrent"], 1);
            const enabled = values.enabled != null ? toBoolean(values.enabled) : false;
            const isDirect = isDirectIndexerType(type);
            const configured = isDirect ? Boolean(baseUrl && sessionId) : Boolean(feedUrl && apiKey);

            state.indexers[key] = {
                key,
                name,
                enabled,
                feed_url: feedUrl,
                base_url: baseUrl,
                api_key: apiKey,
                api_key_masked: maskApiKey(apiKey),
                session_id: sessionId,
                session_id_masked: maskApiKey(sessionId),
                type,
                protocol,
                search_type: searchType,
                priority,
                categories,
                verify_ssl: verifySsl,
                timeout,
                rate_limit: {
                    requests_per_second: rateLimitRps,
                    max_concurrent: rateLimitConcurrent
                },
                configured,
                has_api_key: Boolean(apiKey),
                has_session_id: Boolean(sessionId)
            };
        });
    }

    const unsavedTracker = (() => {
        let dirty = false;
        function set(value) {
            dirty = value;
            document.getElementById("unsavedIndicator")?.classList.toggle("hidden", !value);
        }
        window.addEventListener("beforeunload", (event) => {
            if (dirty) {
                event.preventDefault();
                event.returnValue = "";
            }
        });
        return { markDirty: () => set(true), markClean: () => set(false) };
    })();

    function attachUnsavedTracking() {
        const root = document.querySelector(".aa-settings");
        if (!root) return;
        const markIfUserEdit = (event) => {
            const el = event.target;
            const form = el.closest ? el.closest("form") : null;
            if (!form) return;
            if (el.id === "app_log_level") return; // auto-saves on change
            if ("ownDirty" in form.dataset) return; // form tracks its own changes (storage.js, metadata_refresh.js)
            unsavedTracker.markDirty();
            const statusTarget = form.dataset.statusTarget
                ? document.getElementById(form.dataset.statusTarget)
                : null;
            if (statusTarget && statusTarget.classList.contains("text-success")) {
                statusTarget.textContent = "";
                statusTarget.classList.remove("text-success");
            }
        };
        root.addEventListener("input", markIfUserEdit);
        root.addEventListener("change", markIfUserEdit);
        root.addEventListener("submit", (event) => {
            if (event.target.dataset && "ownDirty" in event.target.dataset) return;
            if (!event.target.checkValidity || event.target.checkValidity()) {
                unsavedTracker.markClean();
            }
        }, true);
    }

    document.addEventListener("DOMContentLoaded", () => {
        attachFormHandlers();
        attachUnsavedTracking();
        loadAllData();

        const typeSelect = document.getElementById("indexer_type");
        if (typeSelect) {
            updateIndexerFieldVisibility(typeSelect.value, state.selectedIndexerKey);
        }

        const refreshButton = document.getElementById("refreshSettings");
        if (refreshButton) {
            refreshButton.addEventListener("click", (event) => {
                event.preventDefault();
                unsavedTracker.markClean();
                loadAllData({ showToast: true });
            });
        }
    });

    function attachLogLevelControl() {
        const select = document.getElementById("app_log_level");
        const status = document.getElementById("logLevelStatus");
        if (!select || select.dataset.bound === "true") return;
        select.dataset.bound = "true";
        select.addEventListener("change", async () => {
            const level = select.value;
            if (status) {
                status.textContent = "Applying…";
                status.className = "aa-maint-loglevel-status is-info";
            }
            select.disabled = true;
            try {
                await fetchJson("/settings/config/update", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ updates: { application: { log_level: level } } })
                });
                if (status) {
                    status.textContent = `Log level set to ${level}`;
                    status.className = "aa-maint-loglevel-status is-ok";
                }
                showNotification(`Log level set to ${level}`, "success");
            } catch (error) {
                if (status) {
                    status.textContent = error.message || "Could not apply log level";
                    status.className = "aa-maint-loglevel-status is-err";
                }
                showNotification(`Could not apply log level: ${error.message}`, "error");
            } finally {
                select.disabled = false;
            }
        });
    }

    function attachFormHandlers() {
        safeAddFormHandler("generalForm", handleGeneralSubmit);
        attachLogLevelControl();
        safeAddFormHandler("searchMatchForm", handleSearchPreferencesSubmit);
        safeAddFormHandler("searchQualityForm", handleSearchPreferencesSubmit);

        const checkUpdatesBtn = document.getElementById("checkUpdatesBtn");
        if (checkUpdatesBtn) {
            checkUpdatesBtn.addEventListener("click", handleCheckUpdates);
        }
        safeAddFormHandler("discoveryForm", handleDiscoverySubmit);
        safeAddEventListener("syncCategoriesBtn", "click", handleSyncCategoriesNow);
        safeAddFormHandler("downloadClientForm", handleDownloadClientSubmit);
        safeAddFormHandler("downloadManagementForm", handleDownloadManagementSubmit);
        safeAddFormHandler("indexerForm", handleIndexerSubmit);
        safeAddFormHandler("mediaManagementForm", handleMediaManagementSubmit);
        setupConversionQualityControls();

        safeAddEventListener("mm_naming_template", "change", handleNamingTemplateChange);
        safeAddEventListener("download_client_type", "change", handleClientTypeChange);
        safeAddEventListener("downloadClientAddButton", "click", () => openDownloadClientEditor());
        safeAddEventListener("downloadClientDeleteButton", "click", handleDownloadClientDelete);
        safeAddEventListener("downloadClientTestButton", "click", handleDownloadClientTest);
        safeAddEventListener("indexerAddButton", "click", () => openIndexerEditor());
        safeAddEventListener("indexerDeleteButton", "click", handleIndexerDelete);
        safeAddEventListener("indexerRefreshButton", "click", refreshIndexersList);
        safeAddEventListener("indexerSyncButton", "click", handleProwlarrSync);
        safeAddEventListener("prowlarrSyncCancel", "click", closeProwlarrSyncModal);
        safeAddEventListener("prowlarrSyncConfirm", "click", handleProwlarrSyncConfirm);
        safeAddEventListener("prowlarrSyncSelectAll", "change", handleProwlarrSyncSelectAll);
        safeAddEventListener("prowlarrSaveButton", "click", () => handleAggregatorSave("prowlarr"));
        safeAddEventListener("prowlarrTestButton", "click", () => handleAggregatorTest("prowlarr"));
        safeAddEventListener("prowlarrSyncButton", "click", (e) => { e.preventDefault(); openAggregatorSyncModal("prowlarr"); });
        safeAddEventListener("nzbhydra2SaveButton", "click", () => handleAggregatorSave("nzbhydra2"));
        safeAddEventListener("nzbhydra2TestButton", "click", () => handleAggregatorTest("nzbhydra2"));
        safeAddEventListener("nzbhydra2SyncButton", "click", (e) => { e.preventDefault(); openAggregatorSyncModal("nzbhydra2"); });
        safeAddEventListener("indexerTestButton", "click", handleIndexerTest);
        safeAddEventListener("mamBuyWedgeButton", "click", handleMamBuyWedge);
        safeAddEventListener("indexer_name", "input", handleIndexerNameInput);
        safeAddEventListener("indexer_custom_key", "input", handleIndexerKeyInput);
        safeAddEventListener("indexer_type", "change", handleIndexerTypeChange);
        safeAddEventListener("qbPathMappingAdd", "click", handleQbPathMappingAdd);
        safeAddEventListener("auto_download_enabled", "change", handleAutoDownloadToggle);
        safeAddEventListener("autoDownloadConfirm", "click", handleAutoDownloadConfirm);
        safeAddEventListener("autoDownloadCancel", "click", handleAutoDownloadCancel);
        safeAddEventListener("qb_path_mapping_enabled", "change", handleQbPathMappingToggle);
        safeAddEventListener("qb_auth_mode", "change", () => {
            updateQbAuthModeVisibility(getInputValue("qb_auth_mode"));
        });
        safeAddEventListener("app_theme", "change", handleThemeSelectionPreview);

        const qbPathMappings = document.getElementById("qbPathMappings");
        if (qbPathMappings) {
            qbPathMappings.addEventListener("click", handleQbPathMappingRemove);
        }

        const clientList = document.getElementById("downloadClientsList");
        if (clientList) {
            clientList.addEventListener("click", (event) => {
                const target = event.target.closest("[data-client-key]");
                if (target) {
                    event.preventDefault();
                    openDownloadClientEditor(target.dataset.clientKey);
                }
            });
        }

        const indexerList = document.getElementById("indexersList");
        if (indexerList) {
            indexerList.addEventListener("click", (event) => {
                const target = event.target.closest("[data-indexer-key]");
                if (target) {
                    event.preventDefault();
                    openIndexerEditor(target.dataset.indexerKey);
                }
            });
        }
    }

    async function loadAllData(options = {}) {
        const { showToast = false, silent = false } = options;

        if (!silent) {
            toggleRefreshButton(true);
        }

        try {
            const [configPayload, indexersPayload, templatesPayload, mediaPayload, downloadPayload, aggsPayload] = await Promise.all([
                fetchJson("/settings/config"),
                fetchJson("/settings/api/indexers"),
                fetchJson("/settings/naming-templates"),
                fetchJson("/settings/api/media-management"),
                fetchJson("/settings/api/download-management"),
                fetchJson("/settings/api/aggregators"),
            ]);

            state.config = configPayload.config || {};
            setIndexersState(indexersPayload.indexers || {});
            state.namingTemplates = templatesPayload.templates || [];
            state.mediaSettings = mediaPayload.config || {};
            state.downloadSettings = downloadPayload.config || {};
            buildDownloadClientsState();
            populateAggregatorConnections(aggsPayload.aggregators || {});
            state.lastLoaded = new Date();
            state.addingIndexer = false;

            populateForms();

            if (showToast) {
                showNotification("Configuration refreshed", "success");
            }
        } catch (error) {
            showNotification(`Failed to load configuration: ${error.message}`, "error");
        } finally {
            if (!silent) {
                toggleRefreshButton(false);
            }
        }
    }

    function setIndexersState(indexersMap) {
        state.indexers = {};

        if (!indexersMap || typeof indexersMap !== "object") {
            hydrateIndexersFromConfig();
            return;
        }

        Object.entries(indexersMap).forEach(([key, details]) => {
            if (!details || typeof details !== "object") {
                return;
            }
            state.indexers[key] = {
                key,
                search_type: sanitizeSearchType(details.search_type || details.searchType),
                ...details
            };
        });

        if (!Object.keys(state.indexers).length) {
            hydrateIndexersFromConfig();
        }
    }

    function populateForms() {
        populateGeneralForm();
        populateDownloadManagementForm();
        populateDiscoveryForm();
        renderDownloadClientsList();
        renderIndexerList();
        populateMediaManagementForm();
        updateTimestamp();
        loadSearchPreferences();
    }

    function populateDownloadManagementForm() {
        const autoSearchConfig = state.config.auto_search || {};
        setCheckboxValue("auto_download_enabled", autoSearchConfig.auto_download_enabled);
        setCheckboxValue("schedule_window_enabled", autoSearchConfig.schedule_window_enabled);
        setNumericInput("schedule_start_hour", autoSearchConfig.schedule_start_hour, 0);
        setNumericInput("schedule_end_hour", autoSearchConfig.schedule_end_hour, 24);
    }

    async function loadSearchPreferences() {
        try {
            const response = await fetch("/settings/api/search/preferences");
            const data = await response.json();
            if (!response.ok || !data.success) return;

            const prefs = data.preferences || {};
            setSelectValue("preferred_format", prefs.preferred_format || "M4B");
            setInputValue("format_priority", prefs.format_priority || "M4B,M4A,MP3,FLAC");
            setNumericInput("min_bitrate", prefs.min_bitrate, 64);
            setNumericInput("preferred_bitrate", prefs.preferred_bitrate, 128);
            setNumericInput("min_match_score", prefs.min_match_score, 80);
            setNumericInput("min_quality_score", prefs.min_quality_score, 70);
        } catch (err) {
        }
    }

    async function handleSearchPreferencesSubmit(form) {
        const updates = {
            preferred_format: getInputValue("preferred_format"),
            format_priority: getInputValue("format_priority"),
            min_bitrate: toNumeric(getInputValue("min_bitrate"), 64),
            preferred_bitrate: toNumeric(getInputValue("preferred_bitrate"), 128),
            min_match_score: toNumeric(getInputValue("min_match_score"), 80),
            min_quality_score: toNumeric(getInputValue("min_quality_score"), 70)
        };

        const statusEl = document.getElementById(form.dataset.statusTarget);
        try {
            const response = await fetch("/settings/api/search/preferences", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(updates)
            });
            const data = await response.json();
            if (!response.ok || !data.success) {
                throw new Error(data.error || data.message || "Failed to save preferences");
            }
            if (statusEl) {
                statusEl.textContent = "Saved.";
                statusEl.className = "text-xs text-success h-4";
            }
        } catch (err) {
            if (statusEl) {
                statusEl.textContent = err.message || "Failed to save preferences";
                statusEl.className = "text-xs text-error h-4";
            }
        }
    }

    function populateGeneralForm() {
        const generalConfig = state.config.application || {};

        setSelectValue("app_log_level", (generalConfig.log_level || "INFO").toUpperCase());

        const themeSelect = document.getElementById("app_theme");
        if (themeSelect) {
            const storedTheme = generalConfig.theme || getStoredThemePreference();
            if (storedTheme) {
                themeSelect.value = storedTheme;
            }
        }
    }

    function populateDiscoveryForm() {
        const discoveryConfig = state.config.discovery || {};
        setInputValue("discovery_refresh_interval_days", discoveryConfig.refresh_interval_days || 30);

        const lastSyncedEl = document.getElementById("discoveryLastSynced");
        if (lastSyncedEl) {
            const lastSyncedAt = discoveryConfig.last_synced_at;
            lastSyncedEl.textContent = lastSyncedAt ? new Date(lastSyncedAt).toLocaleString() : "Never synced";
        }

        const marketplaceEl = document.getElementById("discoveryMarketplace");
        const countryCode = (state.config.audible || {}).country_code;
        if (marketplaceEl && countryCode) {
            marketplaceEl.textContent = String(countryCode).toUpperCase();
        }
        setInputValue("preferred_languages", (state.config.authors || {}).preferred_languages || "english,en");
    }

    function handleDiscoverySubmit(form) {
        const languages = getInputValue("preferred_languages")
            .split(",").map((s) => s.trim().toLowerCase()).filter(Boolean).join(",");
        const updates = {
            discovery: {
                refresh_interval_days: toNumeric(getInputValue("discovery_refresh_interval_days"), 30)
            },
            authors: {
                preferred_languages: languages || "english,en"
            }
        };
        submitConfigUpdates({ updates }, form, "Discovery settings saved.");
    }

    async function handleSyncCategoriesNow() {
        const btn = document.getElementById("syncCategoriesBtn");
        const statusTarget = document.getElementById("discoveryStatus");
        if (!btn) return;

        btn.classList.add("loading");
        btn.disabled = true;
        if (statusTarget) {
            statusTarget.textContent = "Syncing categories…";
            statusTarget.className = "aa-helper text-info";
        }

        try {
            const resp = await fetch("/api/discovery/categories/sync", { method: "POST" });
            const data = await resp.json();
            if (!resp.ok || !data.success) throw new Error(data.error || "Sync failed");

            if (statusTarget) {
                statusTarget.textContent = `Imported ${data.imported} categories.`;
                statusTarget.className = "aa-helper text-success";
            }
            const lastSyncedEl = document.getElementById("discoveryLastSynced");
            if (lastSyncedEl) {
                lastSyncedEl.textContent = new Date().toLocaleString();
            }
            if (typeof showNotification === "function") {
                showNotification(`Imported ${data.imported} Audible categories`, "success");
            }
        } catch (err) {
            if (statusTarget) {
                statusTarget.textContent = `Sync failed: ${err.message}`;
                statusTarget.className = "aa-helper text-error";
            }
            if (typeof showNotification === "function") {
                showNotification(`Category sync failed: ${err.message}`, "error");
            }
        } finally {
            btn.classList.remove("loading");
            btn.disabled = false;
        }
    }

    function buildDownloadClientsState() {
        const clients = {};

        CLIENT_ORDER.forEach((type) => {
            const section = state.config[type];
            if (!section || typeof section !== "object") {
                return;
            }

            const defaults = CLIENT_DEFINITIONS[type].defaults;
            const normalized = { ...defaults };

            switch (type) {
                case "qbittorrent":
                    normalized.enabled = toBoolean(section.enabled);
                    normalized.auto_download = toBoolean(section.auto_download);
                    normalized.host = section.qb_host || section.host || defaults.host;
                    normalized.port = toNumeric(section.qb_port, defaults.port);
                    // Saved settings without a method predate API keys, as the server assumes too
                    normalized.auth_mode = section.qb_auth_mode || "credentials";
                    normalized.username = section.qb_username || section.username || defaults.username;
                    normalized.password = stripQuotes(section.qb_password || section.password || defaults.password);
                    normalized.api_key = section.qb_api_key || defaults.api_key;
                    normalized.category = section.category || section.qb_category || defaults.category;
                    let pathMappings = extractSectionPathMappings(section);

                    if (!pathMappings.length) {
                        pathMappings = parsePathMappings(section.path_mappings);
                    }

                    if (!pathMappings.length) {
                        const legacyRemote = (
                            section.download_path ||
                            section.download_path_remote ||
                            section.save_path ||
                            ""
                        )
                            .toString()
                            .trim();
                        const legacyLocal = (section.download_path_local || section.local_save_path || "")
                            .toString()
                            .trim();

                        if (legacyRemote || legacyLocal) {
                            pathMappings = [
                                {
                                    remote: legacyRemote,
                                    local: legacyLocal
                                }
                            ];
                        }
                    }

                    normalized.path_mappings = pathMappings;
                    normalized.download_path_remote =
                        (
                            pathMappings[0]?.remote ||
                            section.download_path_remote ||
                            section.download_path ||
                            section.save_path ||
                            ""
                        )
                            .toString()
                            .trim();
                    normalized.download_path_local =
                        (pathMappings[0]?.local || section.download_path_local || section.local_save_path || "")
                            .toString()
                            .trim();
                    break;
                case "sabnzbd":
                    normalized.enabled = toBoolean(section.enabled);
                    normalized.auto_download = toBoolean(section.auto_download);
                    normalized.host = section.host || defaults.host;
                    normalized.port = toNumeric(section.port, defaults.port);
                    normalized.api_key = section.api_key || defaults.api_key;
                    normalized.url_base = section.url_base || defaults.url_base;
                    normalized.use_ssl = toBoolean(section.use_ssl);
                    normalized.verify_cert = section.verify_cert !== undefined ? toBoolean(section.verify_cert) : true;
                    normalized.category = section.category || defaults.category;
                    break;
                case "nzbget":
                    normalized.enabled = toBoolean(section.enabled);
                    normalized.auto_download = toBoolean(section.auto_download);
                    normalized.host = section.host || defaults.host;
                    normalized.port = toNumeric(section.port, defaults.port);
                    normalized.username = section.username || defaults.username;
                    normalized.password = stripQuotes(section.password || defaults.password);
                    normalized.use_ssl = toBoolean(section.use_ssl);
                    normalized.verify_cert = section.verify_cert !== undefined ? toBoolean(section.verify_cert) : true;
                    normalized.category = section.category || defaults.category;
                    break;
                default:
                    break;
            }

            clients[type] = normalized;
        });

        state.downloadClients = clients;
    }

    function renderDownloadClientsList() {
        const list = document.getElementById("downloadClientsList");
        const emptyCard = document.getElementById("downloadClientEmptyState");
        const editor = document.getElementById("downloadClientEditor");

        if (!list || !emptyCard || !editor) {
            return;
        }

        list.innerHTML = "";

        CLIENT_ORDER.forEach((type) => {
            const definition = CLIENT_DEFINITIONS[type];
            const button = document.createElement("button");
            button.type = "button";
            button.dataset.clientKey = type;
            button.className = ["btn", "btn-sm", "w-full", "justify-between",
                state.selectedClientKey === type ? "btn-primary" : "btn-outline"].join(" ");

            const name = document.createElement("span");
            name.textContent = definition.label;

            const status = document.createElement("span");
            const configured = Boolean(state.downloadClients[type]);
            status.textContent = configured ? "Configured" : "Not configured";
            status.className = `text-xs ${configured ? "text-success" : "text-base-content/60"}`;

            button.append(name, status);
            list.appendChild(button);
        });

        if (!state.selectedClientKey) {
            emptyCard.classList.remove("hidden");
            editor.classList.add("hidden");
        }
    }

    function openDownloadClientEditor(clientKey) {
        const editor = document.getElementById("downloadClientEditor");
        const emptyCard = document.getElementById("downloadClientEmptyState");
        const heading = document.getElementById("downloadClientHeading");
        const helper = document.getElementById("downloadClientHelper");
        const deleteButton = document.getElementById("downloadClientDeleteButton");
        const typeSelect = document.getElementById("download_client_type");
        const statusTarget = document.getElementById("downloadClientStatus");

        if (!editor || !emptyCard || !heading || !helper || !deleteButton || !typeSelect || !statusTarget) {
            return;
        }

        state.selectedClientKey = clientKey || null;
        statusTarget.textContent = "";
        typeSelect.disabled = Boolean(clientKey);

        if (clientKey) {
            typeSelect.value = clientKey;
            heading.textContent = CLIENT_DEFINITIONS[clientKey].label;
            helper.textContent = "Update the connection details and save to apply changes.";
            deleteButton.classList.remove("hidden");
            populateClientFields(clientKey, state.downloadClients[clientKey]);
        } else {
            const nextType = findNextAvailableClientType();
            typeSelect.disabled = false;
            typeSelect.value = nextType;
            heading.textContent = "New Client";
            helper.textContent = "Choose a client type, fill in the connection details, and save.";
            deleteButton.classList.add("hidden");
            populateClientFields(nextType, CLIENT_DEFINITIONS[nextType].defaults);
        }

        showClientPanel(typeSelect.value);
        emptyCard.classList.add("hidden");
        editor.classList.remove("hidden");
        renderDownloadClientsList();
    }

    function populateClientFields(clientType, data = {}) {
        setCheckboxValue("download_client_enabled", data.enabled);

        const mappingToggle = document.getElementById("qb_path_mapping_enabled");
        if (mappingToggle) {
            const shouldEnableMappings = clientType === "qbittorrent" && Array.isArray(data.path_mappings) && data.path_mappings.length > 0;
            setCheckboxValue("qb_path_mapping_enabled", shouldEnableMappings);
        }

        switch (clientType) {
            case "qbittorrent":
                setInputValue("qb_host", data.host);
                setNumericInput("qb_port", data.port, CLIENT_DEFINITIONS.qbittorrent.defaults.port);
                setSelectValue("qb_auth_mode", data.auth_mode || "credentials");
                setInputValue("qb_username", data.username);
                setInputValue("qb_password", data.password);
                setInputValue("qb_api_key", data.api_key);
                setInputValue("qb_category", data.category);
                updateQbAuthModeVisibility(data.auth_mode || "credentials");
                renderQbPathMappings(data.path_mappings || []);
                break;
            case "sabnzbd":
                setInputValue("sabnzbd_host", data.host);
                setNumericInput("sabnzbd_port", data.port, CLIENT_DEFINITIONS.sabnzbd.defaults.port);
                setInputValue("sabnzbd_api_key", data.api_key);
                setInputValue("sabnzbd_url_base", data.url_base || CLIENT_DEFINITIONS.sabnzbd.defaults.url_base);
                setInputValue("sabnzbd_category", data.category);
                setCheckboxValue("sabnzbd_use_ssl", data.use_ssl);
                setCheckboxValue("sabnzbd_verify_cert", data.verify_cert !== false);
                setInputValue("sabnzbd_remote_path", data.remote_path || "");
                setInputValue("sabnzbd_local_path", data.local_path || "");
                renderQbPathMappings();
                break;
            case "nzbget":
                setInputValue("nzbget_host", data.host);
                setNumericInput("nzbget_port", data.port, CLIENT_DEFINITIONS.nzbget.defaults.port);
                setInputValue("nzbget_username", data.username);
                setInputValue("nzbget_password", data.password);
                setInputValue("nzbget_category", data.category);
                setCheckboxValue("nzbget_use_ssl", data.use_ssl);
                setCheckboxValue("nzbget_verify_cert", data.verify_cert !== false);
                renderQbPathMappings();
                break;
            default:
                renderQbPathMappings();
                break;
        }

        updateQbPathMappingVisibility(clientType);
    }

    function parsePathMappings(rawValue) {
        if (!rawValue) {
            return [];
        }

        if (Array.isArray(rawValue)) {
            return rawValue
                .map((item) => ({
                    remote: (item.remote || item.qb_path || "").toString().trim(),
                    local: (item.local || item.host_path || "").toString().trim()
                }))
                .filter((item) => item.remote || item.local);
        }

        if (typeof rawValue === "string") {
            return rawValue
                .split(";")
                .map((entry) => entry.trim())
                .filter(Boolean)
                .map((entry) => {
                    const [remote = "", local = ""] = entry.split("|");
                    return { remote: remote.trim(), local: local.trim() };
                })
                .filter((item) => item.remote || item.local);
        }

        return [];
    }

    function extractSectionPathMappings(section = {}) {
        if (!section || typeof section !== "object") {
            return [];
        }

        const buckets = new Map();
        Object.entries(section).forEach(([key, value]) => {
            if (typeof key !== "string") {
                return;
            }
            const normalizedKey = key.toLowerCase();
            const match = normalizedKey.match(/^path_mapping_(\d+)_(qb_path|host_path|remote|local)$/);
            if (!match) {
                return;
            }

            const index = Number.parseInt(match[1], 10);
            if (Number.isNaN(index)) {
                return;
            }

            const bucket = buckets.get(index) || { remote: "", local: "" };
            const trimmedValue = (value || "").toString().trim();

            if (match[2] === "qb_path" || match[2] === "remote") {
                bucket.remote = trimmedValue;
            } else {
                bucket.local = trimmedValue;
            }

            buckets.set(index, bucket);
        });

        if (buckets.size) {
            return Array.from(buckets.entries())
                .sort((a, b) => a[0] - b[0])
                .map(([, mapping]) => mapping)
                .filter((mapping) => mapping.remote || mapping.local);
        }

        const legacyValue = section.path_mappings || section.path_mapping;
        return parsePathMappings(legacyValue);
    }

    function renderQbPathMappings(mappings = []) {
        const container = document.getElementById("qbPathMappings");
        if (!container) {
            updateQbPathMappingAddState(0);
            return;
        }

        let rows = Array.isArray(mappings) && mappings.length ? mappings : [{ remote: "", local: "" }];

        if ((!mappings || !mappings.length) && rows.length === 1 && !rows[0].remote && !rows[0].local) {
            const defaultLocal =
                state.downloadClients?.qbittorrent?.path_mappings?.[0]?.local ||
                state.downloadClients?.qbittorrent?.download_path_local ||
                "";
            rows = [{ remote: "", local: defaultLocal }];
        }

        container.innerHTML = "";

        rows.forEach((mapping, index) => {
            const row = document.createElement("div");
            row.className = "grid gap-2 md:grid-cols-[1fr_1fr_auto] qb-path-mapping-row";
            row.dataset.index = String(index);

            const remoteWrapper = document.createElement("div");
            remoteWrapper.className = "form-control";
            const remoteLabel = document.createElement("label");
            remoteLabel.className = "label pb-1";
            const remoteText = document.createElement("span");
            remoteText.className = "label-text text-xs uppercase tracking-wide text-base-content/60";
            remoteText.textContent = "qB path";
            remoteLabel.appendChild(remoteText);
            const remoteInput = document.createElement("input");
            remoteInput.type = "text";
            remoteInput.className = "input input-bordered w-full qb-path-remote";
            remoteInput.placeholder = "/downloads/torrent_downloads";
            remoteInput.value = mapping.remote || "";
            remoteWrapper.append(remoteLabel, remoteInput);

            const localWrapper = document.createElement("div");
            localWrapper.className = "form-control";
            const localLabel = document.createElement("label");
            localLabel.className = "label pb-1";
            const localText = document.createElement("span");
            localText.className = "label-text text-xs uppercase tracking-wide text-base-content/60";
            localText.textContent = "Host path";
            localLabel.appendChild(localText);
            const localInput = document.createElement("input");
            localInput.type = "text";
            localInput.className = "input input-bordered w-full qb-path-local";
            localInput.placeholder = "/media/...";
            localInput.value = mapping.local || "";
            localWrapper.append(localLabel, localInput);

            const actionsWrapper = document.createElement("div");
            actionsWrapper.className = "flex justify-end md:justify-center items-center mt-2 md:mt-6";
            const removeButton = document.createElement("button");
            removeButton.type = "button";
            removeButton.className = `btn btn-outline btn-error btn-xs${rows.length === 1 ? " hidden" : ""}`;
            removeButton.dataset.qbPathRemove = String(index);
            removeButton.textContent = "Remove";
            actionsWrapper.appendChild(removeButton);

            row.append(remoteWrapper, localWrapper, actionsWrapper);
            container.appendChild(row);
        });

        updateQbPathMappingAddState(rows.length);
    }

    function collectQbPathMappings(includeEmpty = false) {
        const container = document.getElementById("qbPathMappings");
        if (!container) {
            return [];
        }

        const rows = container.querySelectorAll(".qb-path-mapping-row");
        const mappings = Array.from(rows).map((row) => {
            const remote = row.querySelector(".qb-path-remote")?.value.trim() || "";
            const local = row.querySelector(".qb-path-local")?.value.trim() || "";
            return { remote, local };
        });

        if (includeEmpty) {
            return mappings;
        }

        return mappings.filter((mapping) => mapping.remote && mapping.local);
    }

    function handleQbPathMappingAdd(event) {
        event.preventDefault();

        const currentMappings = collectQbPathMappings(true);
        if (currentMappings.length >= MAX_PATH_MAPPINGS) {
            return;
        }

        currentMappings.push({ remote: "", local: "" });
        renderQbPathMappings(currentMappings);
    }

    function handleQbPathMappingRemove(event) {
        const button = event.target.closest("[data-qb-path-remove]");
        if (!button) {
            return;
        }

        event.preventDefault();
        const index = Number(button.dataset.qbPathRemove);
        if (Number.isNaN(index)) {
            return;
        }

        const currentMappings = collectQbPathMappings(true);
        currentMappings.splice(index, 1);
        renderQbPathMappings(currentMappings);
    }

    function updateQbPathMappingAddState(count) {
        const addButton = document.getElementById("qbPathMappingAdd");
        if (!addButton) {
            return;
        }

        const disabled = count >= MAX_PATH_MAPPINGS;
        addButton.disabled = disabled;
        addButton.classList.toggle("btn-disabled", disabled);
    }

    function updateQbAuthModeVisibility(mode) {
        const isApiKey = mode === "api_key";
        document.querySelectorAll("[data-qb-credentials]").forEach((el) => el.classList.toggle("hidden", isApiKey));
        document.querySelectorAll("[data-qb-api-key]").forEach((el) => el.classList.toggle("hidden", !isApiKey));
    }

    function updateQbPathMappingVisibility(clientType) {
        const mappingSection = document.querySelector("[data-qb-path-mappings]");
        const toggle = document.getElementById("qb_path_mapping_enabled");
        if (!mappingSection || !toggle) {
            return;
        }

        const isQb = clientType === "qbittorrent";
        const enabled = isQb && toggle.checked;

        mappingSection.classList.toggle("hidden", !enabled);

        const controls = mappingSection.querySelectorAll("input, button");
        controls.forEach((control) => {
            control.disabled = !enabled;
            if (control.id === "qbPathMappingAdd") {
                control.classList.toggle("btn-disabled", !enabled || control.disabled);
            }
        });

        if (enabled) {
            const currentCount = collectQbPathMappings(true).length;
            updateQbPathMappingAddState(currentCount);
        } else {
            const addButton = document.getElementById("qbPathMappingAdd");
            if (addButton) {
                addButton.disabled = true;
                addButton.classList.add("btn-disabled");
            }
        }
    }

    function handleQbPathMappingToggle() {
        const typeSelect = document.getElementById("download_client_type");
        const clientType = typeSelect ? typeSelect.value : "qbittorrent";
        updateQbPathMappingVisibility(clientType);
    }

    function handleClientTypeChange(event) {
        const newType = event.target.value;
        showClientPanel(newType);
        populateClientFields(newType, CLIENT_DEFINITIONS[newType].defaults);
    }

    function findNextAvailableClientType() {
        const firstUnconfigured = CLIENT_ORDER.find((type) => !state.downloadClients[type]);
        return firstUnconfigured || DEFAULT_CLIENT_TYPE;
    }

    async function handleDownloadClientSubmit(form) {
        const statusTarget = document.getElementById("downloadClientStatus");
        const submitButton = form.querySelector("button[type=\"submit\"]");
        const typeSelect = document.getElementById("download_client_type");

        if (!statusTarget || !submitButton || !typeSelect) {
            return;
        }

        const clientType = typeSelect.value;

        let payload;
        try {
            payload = getClientPayload(clientType);
        } catch (validationError) {
            statusTarget.textContent = validationError.message;
            statusTarget.className = "text-xs text-error";
            showNotification(validationError.message, "error");
            return;
        }

        statusTarget.textContent = "Saving client…";
        statusTarget.className = "text-xs text-info";
        submitButton.classList.add("loading");
        submitButton.disabled = true;

        try {
            const response = await fetchJson(`/settings/api/clients/${clientType}`, {
                method: "PUT",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            });

            const message = response.message || `${CLIENT_DEFINITIONS[clientType].label} saved.`;
            statusTarget.textContent = "Saved";
            statusTarget.className = "text-xs text-success";
            showNotification(message, "success");

            state.selectedClientKey = clientType;
            await loadAllData({ silent: true });
            openDownloadClientEditor(clientType);
        } catch (error) {
            statusTarget.textContent = error.message;
            statusTarget.className = "text-xs text-error";
            showNotification(`Failed to save client: ${error.message}`, "error");
        } finally {
            submitButton.classList.remove("loading");
            submitButton.disabled = false;
        }
    }

    async function handleDownloadClientDelete(event) {
        event.preventDefault();

        if (!state.selectedClientKey) {
            showNotification("Select a client before deleting.", "warning");
            return;
        }

        const clientLabel = CLIENT_DEFINITIONS[state.selectedClientKey]?.label || state.selectedClientKey;
        const confirmed = await AA.showConfirmDialog(
            `Delete the ${clientLabel} download client configuration?`,
            "Delete Download Client",
            "Delete",
            "btn-error"
        );
        if (!confirmed) {
            return;
        }

        const statusTarget = document.getElementById("downloadClientStatus");
        const deleteButton = document.getElementById("downloadClientDeleteButton");

        if (!statusTarget || !deleteButton) {
            return;
        }

        statusTarget.textContent = "Deleting client…";
        statusTarget.className = "text-xs text-info";
        deleteButton.classList.add("loading");
        deleteButton.disabled = true;

        try {
            const clientType = state.selectedClientKey;
            const response = await fetchJson(`/settings/api/clients/${clientType}`, {
                method: "DELETE"
            });

            const message = response.message || `${CLIENT_DEFINITIONS[clientType].label} deleted.`;
            showNotification(message, "success");

            state.selectedClientKey = null;
            await loadAllData({ silent: true });
            renderDownloadClientsList();
        } catch (error) {
            statusTarget.textContent = error.message;
            statusTarget.className = "text-xs text-error";
            showNotification(`Failed to delete client: ${error.message}`, "error");
        } finally {
            deleteButton.classList.remove("loading");
            deleteButton.disabled = false;
        }
    }

    async function handleDownloadClientTest(event) {
        event.preventDefault();

        const typeSelect = document.getElementById("download_client_type");
        const statusTarget = document.getElementById("downloadClientStatus");

        if (!typeSelect || !statusTarget) {
            return;
        }

        const clientType = typeSelect.value;

        let payload;
        try {
            payload = getClientPayload(clientType);
        } catch (validationError) {
            statusTarget.textContent = validationError.message;
            statusTarget.className = "text-xs text-error";
            showNotification(validationError.message, "error");
            return;
        }

        statusTarget.textContent = "Testing connection…";
        statusTarget.className = "text-xs text-info";

        try {
            const response = await fetchJson(`/settings/api/clients/${clientType}/test`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            });

            const message = response.test_result?.message || response.message || "Connection successful";
            statusTarget.textContent = message;
            statusTarget.className = "text-xs text-success";
            showNotification(message, "success");
        } catch (error) {
            statusTarget.textContent = error.message;
            statusTarget.className = "text-xs text-error";
            showNotification(`Client test failed: ${error.message}`, "error");
        }
    }

    function getClientPayload(clientType) {
        const payload = {
            enabled: getCheckboxValue("download_client_enabled"),
            auto_download: state.downloadClients[clientType]?.auto_download ?? CLIENT_DEFINITIONS[clientType]?.defaults?.auto_download ?? false
        };

        switch (clientType) {
            case "qbittorrent":
                payload.host = getInputValue("qb_host") || CLIENT_DEFINITIONS.qbittorrent.defaults.host;
                payload.port = toNumeric(getInputValue("qb_port"), CLIENT_DEFINITIONS.qbittorrent.defaults.port);
                payload.auth_mode = getInputValue("qb_auth_mode") || "credentials";
                payload.username = getInputValue("qb_username");
                payload.password = getInputValue("qb_password");
                payload.api_key = getInputValue("qb_api_key");
                payload.category = getInputValue("qb_category") || CLIENT_DEFINITIONS.qbittorrent.defaults.category;
                if (getCheckboxValue("qb_path_mapping_enabled")) {
                    const rawMappings = collectQbPathMappings(true);
                    const incompleteMapping = rawMappings.find((mapping) => (mapping.remote && !mapping.local) || (!mapping.remote && mapping.local));
                    if (incompleteMapping) {
                        throw new Error("Complete both qB and host paths for each mapping entry.");
                    }
                    payload.path_mappings = rawMappings.filter((mapping) => mapping.remote && mapping.local);
                } else {
                    payload.path_mappings = [];
                }
                break;
            case "sabnzbd":
                payload.host = getInputValue("sabnzbd_host") || CLIENT_DEFINITIONS.sabnzbd.defaults.host;
                payload.port = toNumeric(getInputValue("sabnzbd_port"), CLIENT_DEFINITIONS.sabnzbd.defaults.port);
                payload.api_key = getInputValue("sabnzbd_api_key");
                payload.url_base = getInputValue("sabnzbd_url_base") || CLIENT_DEFINITIONS.sabnzbd.defaults.url_base;
                payload.category = getInputValue("sabnzbd_category");
                payload.use_ssl = getCheckboxValue("sabnzbd_use_ssl");
                payload.verify_cert = getCheckboxValue("sabnzbd_verify_cert");
                payload.remote_path = getInputValue("sabnzbd_remote_path") || "";
                payload.local_path = getInputValue("sabnzbd_local_path") || "";
                break;
            case "nzbget":
                payload.host = getInputValue("nzbget_host") || CLIENT_DEFINITIONS.nzbget.defaults.host;
                payload.port = toNumeric(getInputValue("nzbget_port"), CLIENT_DEFINITIONS.nzbget.defaults.port);
                payload.username = getInputValue("nzbget_username");
                payload.password = getInputValue("nzbget_password");
                payload.category = getInputValue("nzbget_category");
                payload.use_ssl = getCheckboxValue("nzbget_use_ssl");
                payload.verify_cert = getCheckboxValue("nzbget_verify_cert");
                break;
            default:
                break;
        }

        return payload;
    }

    function renderIndexerList() {
        const list = document.getElementById("indexersList");
        const emptyCard = document.getElementById("indexerEmptyState");
        const editor = document.getElementById("indexerEditor");

        if (!list || !emptyCard || !editor) {
            return;
        }

        list.innerHTML = "";

        const entries = Object.entries(state.indexers || {}).sort((a, b) => {
            const priorityDiff = (a[1].priority ?? DEFAULT_INDEXER_PRIORITY) - (b[1].priority ?? DEFAULT_INDEXER_PRIORITY);
            if (priorityDiff !== 0) {
                return priorityDiff;
            }
            return a[0].localeCompare(b[0]);
        });

        if (!entries.length) {
            const placeholder = document.createElement("div");
            placeholder.className = "rounded-lg border border-dashed border-base-300 p-4 text-sm text-base-content/60";
            placeholder.textContent = "No indexers configured yet.";
            list.appendChild(placeholder);
        } else {
            entries.forEach(([key, indexer]) => {
                const button = document.createElement("button");
                button.type = "button";
                button.dataset.indexerKey = key;
                button.className = `btn btn-sm w-full justify-between ${state.selectedIndexerKey === key ? "btn-primary" : "btn-outline"}`;

                const feedUrl = typeof indexer.feed_url === "string"
                    ? indexer.feed_url.trim()
                    : String(indexer.feed_url || "").trim();
                const hasApiKey = typeof indexer.api_key === "string"
                    ? Boolean(indexer.api_key.trim())
                    : Boolean(indexer.has_api_key);
                const baseUrl = typeof indexer.base_url === "string" ? indexer.base_url.trim() : "";
                const hasSessionId = typeof indexer.session_id === "string"
                    ? Boolean(indexer.session_id.trim())
                    : Boolean(indexer.has_session_id);
                const isDirect = isDirectIndexerType(indexer.type);
                const isNzbhydra2 = isNzbhydra2Type(indexer.type);
                const isProwlarr = isProwlarrType(indexer.type);
                const isConfigured = isDirect
                    ? Boolean(baseUrl && hasSessionId)
                    : (isNzbhydra2 || isProwlarr)
                        ? Boolean(baseUrl && hasApiKey)
                        : Boolean(feedUrl && hasApiKey);

                const label = document.createElement("span");
                label.textContent = indexer.name || key;
                const tooltipBits = [];
                const typeLabel = INDEXER_TYPE_LABELS[indexer.type] || indexer.type;
                if (typeLabel) {
                    tooltipBits.push(typeLabel);
                }
                if (Number.isFinite(indexer.priority)) {
                    tooltipBits.push(`Priority ${indexer.priority}`);
                }
                if (tooltipBits.length) {
                    button.title = tooltipBits.join(" · ");
                }

                const status = document.createElement("span");
                let statusText = "Not configured";
                let statusClass = "text-xs text-base-content/60";

                if (isConfigured && indexer.enabled) {
                    statusText = "Configured";
                    statusClass = "text-xs text-success";
                } else if (isConfigured) {
                    statusText = "Disabled";
                    statusClass = "text-xs text-base-content/60";
                }

                status.textContent = statusText;
                status.className = statusClass;

                button.append(label, status);
                list.appendChild(button);
            });
        }

        if (!state.selectedIndexerKey && !state.addingIndexer) {
            emptyCard.classList.remove("hidden");
            editor.classList.add("hidden");
        }
    }

    function openIndexerEditor(indexerKey) {
        const editor = document.getElementById("indexerEditor");
        const emptyCard = document.getElementById("indexerEmptyState");
        const heading = document.getElementById("indexerHeading");
        const helper = document.getElementById("indexerHelper");
        const deleteButton = document.getElementById("indexerDeleteButton");
        const typeSelect = document.getElementById("indexer_type");
        const keyInput = document.getElementById("indexer_custom_key");
        const statusTarget = document.getElementById("indexerStatus");
        const searchTypeSelect = document.getElementById("indexer_search_type");

        if (!editor || !emptyCard || !heading || !helper || !deleteButton || !typeSelect || !keyInput || !statusTarget) {
            return;
        }

        const isNewIndexer = !indexerKey;
        state.selectedIndexerKey = isNewIndexer ? null : indexerKey;
        state.addingIndexer = isNewIndexer;
        statusTarget.textContent = "";

        if (!isNewIndexer) {
            const indexer = state.indexers[indexerKey] || {};
            heading.textContent = indexer.name || indexerKey;
            const helperBits = [];
            if (isDirectIndexerType(indexer.type)) {
                helperBits.push(indexer.base_url ? "Base URL configured" : "Base URL missing");
            } else if (isNzbhydra2Type(indexer.type) || isProwlarrType(indexer.type)) {
                helperBits.push(indexer.base_url ? "Base URL configured" : "Base URL missing");
                helperBits.push(indexer.has_api_key ? `API key set (${indexer.api_key_masked || "hidden"})` : "API key missing");
            } else {
                helperBits.push(indexer.feed_url ? "Feed URL configured" : "Feed URL missing");
                helperBits.push(indexer.has_api_key ? `API key set (${indexer.api_key_masked || "hidden"})` : "API key missing");
            }
            helper.textContent = helperBits.filter(Boolean).join(" · ") || "Update the feed URL, API key, priority, or categories and save to apply changes.";
            deleteButton.classList.remove("hidden");
            keyInput.value = indexerKey;
            keyInput.disabled = true;
            keyInput.dataset.locked = "true";
            typeSelect.value = indexer.type || inferIndexerType(indexerKey, indexer.protocol);
            setCheckboxValue("indexer_enabled", indexer.enabled);
            setCheckboxValue("indexer_private", indexer.private || false);
            setInputValue("indexer_name", indexer.name || "");
            setInputValue("indexer_feed_url", indexer.feed_url || "");
            setInputValue("indexer_api_key", indexer.api_key || "");
            setInputValue("indexer_base_url", indexer.base_url || "");
            setInputValue("indexer_session_id", indexer.session_id || "");
            setInputValue("indexer_search_type", searchTypeSelect ? sanitizeSearchType(indexer.search_type) : "all");
            setNumericInput("indexer_priority", indexer.priority, DEFAULT_INDEXER_PRIORITY);
            setInputValue("indexer_categories", (indexer.categories || []).join(", "));
            setNumericInput("indexer_min_seeders", indexer.min_seeders, 0);
            setInputValue("indexer_preferred_formats", (indexer.preferred_formats || []).join(", "));
            setInputValue("indexer_languages", (indexer.languages || []).join(", "));
            const existingRateLimit = indexer.rate_limit || {};
            setNumericInput("indexer_max_grabs_per_hour", existingRateLimit.max_grabs_per_hour, 0);
            setNumericInput("indexer_max_grabs_per_day", existingRateLimit.max_grabs_per_day, 0);
        } else {
            heading.textContent = "New Indexer";
            helper.textContent = isDirectIndexerType(typeSelect.value)
                ? "Provide the base URL and session ID for your direct provider. Priority determines search order."
                : "Provide the torznab endpoint and API key. Priority determines search order.";
            deleteButton.classList.add("hidden");
            keyInput.value = generateSuggestedIndexerKey(typeSelect.value);
            keyInput.disabled = false;
            keyInput.dataset.locked = "false";
            typeSelect.value = typeSelect.value || "jackett";
            setCheckboxValue("indexer_enabled", true);
            setCheckboxValue("indexer_private", false);
            setInputValue("indexer_name", "");
            setInputValue("indexer_feed_url", "");
            setInputValue("indexer_api_key", "");
            setInputValue("indexer_base_url", "");
            setInputValue("indexer_session_id", "");
            setInputValue("indexer_search_type", searchTypeSelect ? "all" : "");
            setNumericInput("indexer_priority", DEFAULT_INDEXER_PRIORITY, DEFAULT_INDEXER_PRIORITY);
            setInputValue("indexer_categories", "3030");
            setNumericInput("indexer_min_seeders", 0, 0);
            setInputValue("indexer_preferred_formats", "");
            setInputValue("indexer_languages", "");
            setNumericInput("indexer_max_grabs_per_hour", 0, 0);
            setNumericInput("indexer_max_grabs_per_day", 0, 0);
        }

        emptyCard.classList.add("hidden");
        editor.classList.remove("hidden");
        updateIndexerFieldVisibility(typeSelect.value, state.selectedIndexerKey);
        renderIndexerList();
    }

    function handleIndexerNameInput(event) {
        if (state.selectedIndexerKey) {
            return;
        }

        const keyInput = document.getElementById("indexer_custom_key");
        const typeSelect = document.getElementById("indexer_type");

        if (!keyInput || keyInput.disabled || keyInput.dataset.locked === "true") {
            return;
        }

        const slug = slugify(event.target.value);
        if (slug) {
            keyInput.value = slug;
        } else if (!keyInput.value) {
            keyInput.value = generateSuggestedIndexerKey(typeSelect ? typeSelect.value : "indexer");
        }
    }

    function handleIndexerKeyInput() {
        const keyInput = document.getElementById("indexer_custom_key");
        if (keyInput && !keyInput.disabled) {
            keyInput.dataset.locked = "true";
        }
    }

    function handleIndexerTypeChange(event) {
        const typeValue = event.target.value;
        updateIndexerFieldVisibility(typeValue, state.selectedIndexerKey);

        if (!state.selectedIndexerKey) {
            const helper = document.getElementById("indexerHelper");
            if (helper) {
                helper.textContent = isDirectIndexerType(typeValue)
                    ? "Provide the base URL and session ID for your direct provider. Priority determines search order."
                    : isNzbhydra2Type(typeValue)
                        ? "Provide the NZBHydra2 base URL and API key. Priority determines search order."
                        : "Provide the torznab endpoint and API key. Priority determines search order.";
            }
        }

        if (isDirectIndexerType(typeValue)) {
            setInputValue("indexer_search_type", sanitizeSearchType(getInputValue("indexer_search_type") || "all"));
        }

        if (state.selectedIndexerKey) {
            return;
        }

        const keyInput = document.getElementById("indexer_custom_key");
        if (!keyInput || keyInput.disabled || keyInput.dataset.locked === "true") {
            return;
        }

        keyInput.value = generateSuggestedIndexerKey(typeValue);
    }

    function inferIndexerType(key, protocol) {
        if (key && key.toLowerCase().includes("nzbhydra")) {
            return "nzbhydra2";
        }
        if (key && key.toLowerCase().includes("prowlarr")) {
            return "prowlarr";
        }
        return "jackett";
    }

    async function handleIndexerSubmit(form) {
        const statusTarget = document.getElementById("indexerStatus");
        const submitButton = form.querySelector("button[type=\"submit\"]");

        if (!statusTarget || !submitButton) {
            return;
        }

        const keyInput = document.getElementById("indexer_custom_key");
        const nameInput = document.getElementById("indexer_name");
        const typeSelect = document.getElementById("indexer_type");

        if (!keyInput || !nameInput || !typeSelect) {
            return;
        }

        let indexerKey = state.selectedIndexerKey;
        if (!indexerKey) {
            indexerKey = slugify(keyInput.value || nameInput.value);
        }

        if (!indexerKey) {
            statusTarget.textContent = "Indexer key cannot be empty.";
            statusTarget.className = "text-xs text-error";
            return;
        }

        const existingIndexer = state.indexers[indexerKey];

        const typeValue = (typeSelect.value || "jackett").toLowerCase();
        const payload = {
            name: nameInput.value.trim() || indexerKey,
            type: typeValue,
            enabled: getCheckboxValue("indexer_enabled"),
            private: getCheckboxValue("indexer_private"),
            priority: toNumeric(getInputValue("indexer_priority"), DEFAULT_INDEXER_PRIORITY),
            categories: getInputValue("indexer_categories").split(",").map((value) => value.trim()).filter(Boolean),
            search_type: sanitizeSearchType(getInputValue("indexer_search_type")),
            min_seeders: toNumeric(getInputValue("indexer_min_seeders"), 0),
            preferred_formats: getInputValue("indexer_preferred_formats").split(",").map((value) => value.trim()).filter(Boolean),
            languages: getInputValue("indexer_languages").split(",").map((value) => value.trim()).filter(Boolean)
        };

        if (isDirectIndexerType(typeValue)) {
            payload.base_url = getInputValue("indexer_base_url");
            payload.session_id = getInputValue("indexer_session_id");
            payload.feed_url = "";
            payload.api_key = "";
        } else if (isNzbhydra2Type(typeValue) || isProwlarrType(typeValue)) {
            payload.base_url = getInputValue("indexer_base_url");
            payload.api_key = getInputValue("indexer_api_key");
            payload.feed_url = "";
            payload.session_id = "";
        } else {
            payload.feed_url = getInputValue("indexer_feed_url");
            payload.api_key = getInputValue("indexer_api_key");
            payload.base_url = "";
            payload.session_id = "";
        }

        if (existingIndexer) {
            if (existingIndexer.protocol) {
                payload.protocol = existingIndexer.protocol === "direct" ? "direct" : existingIndexer.protocol === "newznab" ? "newznab" : "torznab";
            } else if (isDirectIndexerType(typeValue)) {
                payload.protocol = "direct";
            } else if (isNzbhydra2Type(typeValue)) {
                payload.protocol = "newznab";
            } else {
                payload.protocol = "torznab";
            }
        } else {
            if (isDirectIndexerType(typeValue)) {
                payload.protocol = "direct";
            } else if (isNzbhydra2Type(typeValue)) {
                payload.protocol = "newznab";
            } else {
                payload.protocol = "torznab";
            }
        }

        if (existingIndexer && Object.prototype.hasOwnProperty.call(existingIndexer, "verify_ssl")) {
            payload.verify_ssl = existingIndexer.verify_ssl;
        }

        if (existingIndexer && Object.prototype.hasOwnProperty.call(existingIndexer, "timeout")) {
            payload.timeout = existingIndexer.timeout;
        }

        {
            const priorRateLimit = (existingIndexer && existingIndexer.rate_limit) || {};
            payload.rate_limit = {
                requests_per_second: priorRateLimit.requests_per_second != null ? priorRateLimit.requests_per_second : 1,
                max_concurrent: priorRateLimit.max_concurrent != null ? priorRateLimit.max_concurrent : 1,
                max_grabs_per_hour: toNumeric(getInputValue("indexer_max_grabs_per_hour"), 0),
                max_grabs_per_day: toNumeric(getInputValue("indexer_max_grabs_per_day"), 0)
            };
        }

        if (existingIndexer && existingIndexer.indexer_id) {
            payload.indexer_id = existingIndexer.indexer_id;
        }

        statusTarget.textContent = "Saving indexer…";
        statusTarget.className = "text-xs text-info";
        submitButton.classList.add("loading");
        submitButton.disabled = true;

        try {
            const response = await fetchJson(`/settings/api/indexers/${indexerKey}`, {
                method: "PUT",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            });

            const message = response.message || "Indexer saved.";
            statusTarget.textContent = "Saved";
            statusTarget.className = "text-xs text-success";
            showNotification(message, "success");

            state.selectedIndexerKey = indexerKey;
            state.addingIndexer = false;
            await refreshIndexersList();
            openIndexerEditor(indexerKey);
        } catch (error) {
            statusTarget.textContent = error.message;
            statusTarget.className = "text-xs text-error";
            showNotification(`Failed to save indexer: ${error.message}`, "error");
        } finally {
            submitButton.classList.remove("loading");
            submitButton.disabled = false;
        }
    }


    function populateAggregatorConnections(aggregators) {
        for (const [agg, conn] of Object.entries(aggregators)) {
            const urlInput = document.getElementById(`${agg}_base_url`);
            const keyInput = document.getElementById(`${agg}_api_key`);
            const statusBadge = document.getElementById(`${agg}ConnectionStatus`);

            if (urlInput) urlInput.value = conn.base_url || '';

            if (keyInput) {
                keyInput.value = '';
                keyInput.placeholder = conn.has_api_key ? '••••••••  (saved)' : 'Paste API key';
                keyInput.dataset.hasSavedKey = conn.has_api_key ? '1' : '0';
            }

            if (statusBadge && conn.base_url) {
                statusBadge.textContent = 'Configured';
                statusBadge.className = 'badge badge-success badge-sm';
                statusBadge.classList.remove('hidden');
            } else if (statusBadge) {
                statusBadge.classList.add('hidden');
            }

            if (conn.base_url) {
                const toggle = document.getElementById('aggregatorConnectionsToggle');
                if (toggle) toggle.checked = true;
            }
        }
    }

    async function handleAggregatorSave(aggregator) {
        const urlInput = document.getElementById(`${aggregator}_base_url`);
        const keyInput = document.getElementById(`${aggregator}_api_key`);
        const saveBtn = document.getElementById(`${aggregator}SaveButton`);

        const base_url = (urlInput?.value || '').trim();
        const api_key_raw = (keyInput?.value || '').trim();
        const hasSavedKey = keyInput?.dataset.hasSavedKey === '1';

        if (!base_url) {
            showNotification('Base URL is required.', 'warning');
            return;
        }

        const api_key = api_key_raw || (hasSavedKey ? '***' : '');

        if (saveBtn) { saveBtn.classList.add('loading'); saveBtn.disabled = true; }

        try {
            const response = await fetchJson(`/settings/api/aggregators/${aggregator}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ base_url, api_key }),
            });

            if (response.success) {
                showNotification(response.message || 'Saved', 'success');
                const aggsPayload = await fetchJson('/settings/api/aggregators');
                populateAggregatorConnections(aggsPayload.aggregators || {});
            } else {
                showNotification(response.error || 'Save failed', 'error');
            }
        } catch (err) {
            showNotification(`Save failed: ${err.message}`, 'error');
        } finally {
            if (saveBtn) { saveBtn.classList.remove('loading'); saveBtn.disabled = false; }
        }
    }

    async function handleAggregatorTest(aggregator) {
        const urlInput = document.getElementById(`${aggregator}_base_url`);
        const keyInput = document.getElementById(`${aggregator}_api_key`);
        const testBtn = document.getElementById(`${aggregator}TestButton`);
        const statusBadge = document.getElementById(`${aggregator}ConnectionStatus`);

        const base_url = (urlInput?.value || '').trim();
        const api_key_raw = (keyInput?.value || '').trim();
        const hasSavedKey = keyInput?.dataset.hasSavedKey === '1';
        const api_key = api_key_raw || (hasSavedKey ? '***' : '');

        if (testBtn) { testBtn.classList.add('loading'); testBtn.disabled = true; }
        if (statusBadge) {
            statusBadge.textContent = 'Testing…';
            statusBadge.className = 'badge badge-info badge-sm';
            statusBadge.classList.remove('hidden');
        }

        try {
            const response = await fetchJson(`/settings/api/aggregators/${aggregator}/test`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ base_url, api_key }),
            });

            if (response.success) {
                showNotification(response.message || 'Connection OK', 'success');
                if (statusBadge) {
                    statusBadge.textContent = 'Connected';
                    statusBadge.className = 'badge badge-success badge-sm';
                }
            } else {
                showNotification(response.error || 'Connection failed', 'error');
                if (statusBadge) {
                    statusBadge.textContent = 'Failed';
                    statusBadge.className = 'badge badge-error badge-sm';
                }
            }
        } catch (err) {
            showNotification(`Test failed: ${err.message}`, 'error');
            if (statusBadge) {
                statusBadge.textContent = 'Error';
                statusBadge.className = 'badge badge-error badge-sm';
            }
        } finally {
            if (testBtn) { testBtn.classList.remove('loading'); testBtn.disabled = false; }
        }
    }


    const AGGREGATOR_LABELS = {
        prowlarr: 'Prowlarr',
        nzbhydra2: 'NZBHydra2',
    };

    function openProwlarrSyncModal() {
        const modal = document.getElementById("prowlarrSyncModal");
        if (modal) modal.showModal();
    }

    function closeProwlarrSyncModal() {
        const modal = document.getElementById("prowlarrSyncModal");
        if (modal) modal.close();
    }

    function setProwlarrSyncState(viewState) {
        document.getElementById("prowlarrSyncLoading").classList.toggle("hidden", viewState !== "loading");
        document.getElementById("prowlarrSyncError").classList.toggle("hidden", viewState !== "error");
        document.getElementById("prowlarrSyncList").classList.toggle("hidden", viewState !== "list");
        document.getElementById("prowlarrSyncConfirm").disabled = viewState !== "list";
    }

    function handleProwlarrSyncSelectAll(event) {
        const checked = event.target.checked;
        document.querySelectorAll(".prowlarr-sync-item-cb").forEach((cb) => {
            cb.checked = checked;
        });
        updateSyncConfirmButton();
    }

    function updateSyncConfirmButton() {
        const any = [...document.querySelectorAll(".prowlarr-sync-item-cb")].some((cb) => cb.checked);
        document.getElementById("prowlarrSyncConfirm").disabled = !any;
    }

    async function openAggregatorSyncModal(aggregator) {
        const label = AGGREGATOR_LABELS[aggregator] || aggregator;

        const titleSpan = document.getElementById("prowlarrSyncModalProvider");
        const loadingSpan = document.getElementById("prowlarrSyncLoadingProvider");
        if (titleSpan) titleSpan.textContent = label;
        if (loadingSpan) loadingSpan.textContent = label;

        document.getElementById("prowlarrSyncItems").innerHTML = "";
        document.getElementById("prowlarrSyncSelectAll").checked = true;
        setProwlarrSyncState("loading");
        document.getElementById("prowlarrSyncConfirm").dataset.aggregator = aggregator;
        openProwlarrSyncModal();

        const previewUrl = aggregator === 'nzbhydra2'
            ? "/settings/api/indexers/nzbhydra2-preview"
            : "/settings/api/indexers/prowlarr-preview";

        try {
            const response = await fetchJson(previewUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({}),
            });

            if (!response.success) {
                document.getElementById("prowlarrSyncErrorMsg").textContent = response.error || "Unknown error";
                setProwlarrSyncState("error");
                return;
            }

            const items = response.indexers || [];
            const container = document.getElementById("prowlarrSyncItems");
            container.innerHTML = "";

            if (items.length === 0) {
                container.innerHTML = `<p class="text-sm text-base-content/60 py-2">No indexers found in ${label}.</p>`;
                setProwlarrSyncState("list");
                return;
            }

            for (const indexer of items) {
                const protocolBadge = indexer.protocol === "newznab"
                    ? '<span class="badge badge-outline badge-xs">NZB</span>'
                    : '<span class="badge badge-outline badge-xs">Torrent</span>';
                const syncedBadge = indexer.already_synced
                    ? '<span class="badge badge-success badge-xs">Synced</span>'
                    : '';
                const disabledHint = !indexer.enabled
                    ? `<span class="badge badge-ghost badge-xs">Disabled in ${label}</span>`
                    : '';

                const row = document.createElement("label");
                row.className = "flex items-center gap-3 p-2 rounded-lg hover:bg-base-200 cursor-pointer";
                row.innerHTML = `
                    <input type="checkbox" class="checkbox checkbox-sm prowlarr-sync-item-cb" value="${indexer.id}" ${!indexer.already_synced ? 'checked' : ''} />
                    <span class="flex-1 text-sm font-medium truncate">${indexer.name}</span>
                    <span class="flex items-center gap-1 shrink-0">${protocolBadge}${syncedBadge}${disabledHint}</span>
                `;
                container.appendChild(row);
            }

            container.querySelectorAll(".prowlarr-sync-item-cb").forEach((cb) => {
                cb.addEventListener("change", updateSyncConfirmButton);
            });

            const allChecked = [...container.querySelectorAll(".prowlarr-sync-item-cb")].every((cb) => cb.checked);
            document.getElementById("prowlarrSyncSelectAll").checked = allChecked;

            setProwlarrSyncState("list");
            updateSyncConfirmButton();

            if (response.prowlarr_key) {
                document.getElementById("prowlarrSyncConfirm").dataset.prowlarrKey = response.prowlarr_key;
            }

        } catch (error) {
            document.getElementById("prowlarrSyncErrorMsg").textContent = error.message || "Failed to fetch indexers";
            setProwlarrSyncState("error");
        }
    }

    async function handleProwlarrSync(event) {
        event.preventDefault();
        const hasProwlarr = Object.values(state.indexers || {}).some(
            (idx) => isProwlarrType(idx.type) && idx.base_url && idx.has_api_key
        );
        await openAggregatorSyncModal('prowlarr');
    }

    async function handleProwlarrSyncConfirm(event) {
        event.preventDefault();

        const confirmBtn = document.getElementById("prowlarrSyncConfirm");
        const aggregator = confirmBtn.dataset.aggregator || 'prowlarr';
        const prowlarrKey = confirmBtn.dataset.prowlarrKey || "";

        const selectedIds = [...document.querySelectorAll(".prowlarr-sync-item-cb:checked")].map((cb) =>
            aggregator === 'nzbhydra2' ? cb.value : parseInt(cb.value, 10)
        );

        if (selectedIds.length === 0) {
            showNotification("Select at least one indexer to import.", "warning");
            return;
        }

        confirmBtn.classList.add("loading");
        confirmBtn.disabled = true;

        const syncUrl = aggregator === 'nzbhydra2'
            ? "/settings/api/indexers/nzbhydra2-sync"
            : "/settings/api/indexers/prowlarr-sync";

        try {
            const body = { selected_ids: selectedIds };
            if (aggregator === 'prowlarr' && prowlarrKey) body.prowlarr_key = prowlarrKey;

            const response = await fetchJson(syncUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });

            closeProwlarrSyncModal();

            const msg = response.message || `Synced ${response.total ?? 0} indexer(s)`;
            showNotification(msg, "success");

            if (response.errors && response.errors.length) {
                const errNames = response.errors.map((e) => e.name).join(", ");
                showNotification(`Some indexers failed to save: ${errNames}`, "warning");
            }

            await refreshIndexersList();

        } catch (error) {
            const label = AGGREGATOR_LABELS[aggregator] || aggregator;
            showNotification(`${label} sync failed: ${error.message}`, "error");
        } finally {
            confirmBtn.classList.remove("loading");
            confirmBtn.disabled = false;
        }
    }

    async function handleIndexerDelete(event) {
        event.preventDefault();

        if (!state.selectedIndexerKey) {
            showNotification("Select an indexer before deleting.", "warning");
            return;
        }

        const indexerName = state.indexers[state.selectedIndexerKey]?.name || state.selectedIndexerKey;
        const confirmedDelete = await AA.showConfirmDialog(
            `Delete the "${indexerName}" indexer configuration?`,
            "Delete Indexer",
            "Delete",
            "btn-error"
        );
        if (!confirmedDelete) {
            return;
        }

        const statusTarget = document.getElementById("indexerStatus");
        const deleteButton = document.getElementById("indexerDeleteButton");

        if (!statusTarget || !deleteButton) {
            return;
        }

        statusTarget.textContent = "Deleting indexer…";
        statusTarget.className = "text-xs text-info";
        deleteButton.classList.add("loading");
        deleteButton.disabled = true;

        try {
            const key = state.selectedIndexerKey;
            const response = await fetchJson(`/settings/api/indexers/${key}`, {
                method: "DELETE"
            });

            const message = response.message || "Indexer deleted.";
            showNotification(message, "success");

            state.selectedIndexerKey = null;
            state.addingIndexer = false;
            await refreshIndexersList();
        } catch (error) {
            statusTarget.textContent = error.message;
            statusTarget.className = "text-xs text-error";
            showNotification(`Failed to delete indexer: ${error.message}`, "error");
        } finally {
            deleteButton.classList.remove("loading");
            deleteButton.disabled = false;
        }
    }

    async function refreshIndexersList(event) {
        if (event) {
            event.preventDefault();
        }

        try {
            const payload = await fetchJson(`/settings/api/indexers?_=${Date.now()}`);
            setIndexersState(payload.indexers || {});
            state.addingIndexer = false;
            renderIndexerList();

            if (state.selectedIndexerKey) {
                if (state.indexers[state.selectedIndexerKey]) {
                    openIndexerEditor(state.selectedIndexerKey);
                } else {
                    state.selectedIndexerKey = null;
                }
            }

            if (!state.selectedIndexerKey && !state.addingIndexer) {
                const emptyCard = document.getElementById("indexerEmptyState");
                const editor = document.getElementById("indexerEditor");
                if (emptyCard && editor) {
                    emptyCard.classList.remove("hidden");
                    editor.classList.add("hidden");
                }
            }
        } catch (error) {
            showNotification(`Failed to reload indexers: ${error.message}`, "error");
        }
    }

    async function handleIndexerTest(event) {
        event.preventDefault();

        if (!state.selectedIndexerKey) {
            showNotification("Select an indexer before testing.", "warning");
            return;
        }

        const statusTarget = document.getElementById("indexerStatus");
        if (!statusTarget) {
            return;
        }

        statusTarget.textContent = "Testing connection…";
        statusTarget.className = "text-xs text-info";

        try {
            const response = await fetchJson(`/settings/api/indexers/${state.selectedIndexerKey}/test`, {
                method: "POST"
            });

            const message = response.message || "Indexer connection successful";
            statusTarget.textContent = message;
            statusTarget.className = "text-xs text-success";
            showNotification(message, "success");
        } catch (error) {
            statusTarget.textContent = error.message;
            statusTarget.className = "text-xs text-error";
            showNotification(`Indexer test failed: ${error.message}`, "error");
        }
    }

    function generateSuggestedIndexerKey(type) {
        const base = slugify(type || "indexer");
        if (!base) {
            return `indexer_${Date.now()}`;
        }

        let candidate = base;
        let counter = 1;

        while (state.indexers[candidate]) {
            candidate = `${base}_${counter}`;
            counter += 1;
            if (counter > 999) {
                candidate = `${base}_${Date.now()}`;
                break;
            }
        }

        return candidate;
    }

    function populateMediaManagementForm() {
        const templateSelect = document.getElementById("mm_naming_template");
        const previewList = document.getElementById("mmNamingPreviewList");

        if (templateSelect) {
            templateSelect.innerHTML = "";
            if (state.namingTemplates.length) {
                state.namingTemplates.forEach((template) => {
                    const option = document.createElement("option");
                    option.value = template.name;
                    option.textContent = template.label || template.name;
                    templateSelect.appendChild(option);
                });
                templateSelect.disabled = false;
            } else {
                const option = document.createElement("option");
                option.value = "";
                option.textContent = "No templates available";
                templateSelect.appendChild(option);
                templateSelect.disabled = true;
            }
            const currentTemplate = state.mediaSettings.naming_template || "simple";
            templateSelect.value = currentTemplate;
        }

        if (previewList) {
            previewList.innerHTML = "";
        }


        setCheckboxValue("dm_seeding_enabled", state.downloadSettings.seeding_enabled);
        setCheckboxValue("dm_delete_source", state.downloadSettings.delete_source_after_import);
        setCheckboxValue("import_overwrite_existing", state.mediaSettings.overwrite_existing_files);
        setCheckboxValue("dm_auto_start_monitoring", state.downloadSettings.auto_start_monitoring);
        setCheckboxValue("dm_monitor_seeding", state.downloadSettings.monitor_seeding);
        const conversionDefaults = state.mediaSettings.conversion || {};
        setCheckboxValue("conversion_convert_mp3", conversionDefaults.convert_mp3_to_m4b);
        setInputValue("conv_codec", conversionDefaults.codec || "aac");
        setInputValue("conv_bitrate", conversionDefaults.bitrate || "64k");
        setInputValue("conv_channels", String(conversionDefaults.channels || "1"));
        setInputValue("conv_sample_rate", String(conversionDefaults.sample_rate || "22050"));
        syncConversionPresets();

        setNumericInput("dm_max_concurrent_downloads", state.downloadSettings.max_concurrent_downloads, 3);
        setNumericInput("dm_queue_priority", state.downloadSettings.queue_priority_default, 5);
        setNumericInput("dm_monitor_interval", state.downloadSettings.monitoring_interval, 2);
        setNumericInput("dm_retry_search", state.downloadSettings.retry_search_max, 3);
        setNumericInput("dm_retry_download", state.downloadSettings.retry_download_max, 2);
        setNumericInput("dm_retry_conversion", state.downloadSettings.retry_conversion_max, 1);
        setNumericInput("dm_retry_import", state.downloadSettings.retry_import_max, 2);
        setNumericInput("dm_retry_backoff", state.downloadSettings.retry_backoff_minutes, 30);
        setNumericInput("dm_minimum_free_space", state.downloadSettings.minimum_free_space, 100);
        setInputValue("dm_free_space_unit", state.downloadSettings.free_space_unit || "MB");
        setCheckboxValue("dm_skip_free_space_check", state.downloadSettings.skip_free_space_check);
        setCheckboxValue("dm_ratio_protection_enabled", state.downloadSettings.ratio_protection_enabled);
        setNumericInput("dm_ratio_floor", state.downloadSettings.ratio_floor, 1.0);
        setCheckboxValue("dm_prefer_freeleech", state.downloadSettings.prefer_freeleech);
        setCheckboxValue("dm_hold_non_freeleech", state.downloadSettings.hold_non_freeleech);
        setNumericInput("dm_freeleech_hold_days", state.downloadSettings.freeleech_hold_days, 14);
        setCheckboxValue("dm_duplicate_grab_protection", state.downloadSettings.duplicate_grab_protection);

        let currentTemplate = templateSelect ? templateSelect.value : "simple";
        const availableTemplateNames = (state.namingTemplates || []).map((entry) => entry.name);
        if (!availableTemplateNames.includes(currentTemplate) && availableTemplateNames.length) {
            currentTemplate = availableTemplateNames[0];
            if (templateSelect) {
                templateSelect.value = currentTemplate;
            }
        }
        renderNamingPreview(currentTemplate);
    }

    function handleNamingTemplateChange(event) {
        renderNamingPreview(event.target.value);
    }

    function renderNamingPreview(templateName) {
        const previewList = document.getElementById("mmNamingPreviewList");
        if (!previewList) {
            return;
        }

        previewList.innerHTML = "";
        const template = state.namingTemplates.find((entry) => entry.name === templateName);

        if (template && Array.isArray(template.examples) && template.examples.length) {
            template.examples.forEach((example) => {
                const item = document.createElement("li");
                item.className = "text-sm";
                const title = document.createElement("span");
                title.className = "font-medium";
                title.textContent = example.title || "Sample";
                const path = document.createElement("code");
                path.className = "ml-2 text-xs";
                path.textContent = example.preview || example.path || "";
                item.append(title, document.createTextNode(" → "), path);
                previewList.appendChild(item);
            });
        } else {
            fetchNamingPreview(templateName);
        }
    }

    async function fetchNamingPreview(templateName) {
        const previewList = document.getElementById("mmNamingPreviewList");
        if (!previewList) {
            return;
        }

        const loadingItem = document.createElement("li");
        loadingItem.className = "text-sm text-base-content/60 italic";
        loadingItem.textContent = "Generating preview…";
        previewList.appendChild(loadingItem);

        try {
            const response = await fetchJson("/settings/api/media-management/preview", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ template: templateName })
            });

            previewList.innerHTML = "";
            if (Array.isArray(response.examples) && response.examples.length) {
                response.examples.forEach((example) => {
                    const item = document.createElement("li");
                    item.className = "text-sm";
                    const title = document.createElement("span");
                    title.className = "font-medium";
                    title.textContent = example.title || "Sample";
                    const path = document.createElement("code");
                    path.className = "ml-2 text-xs";
                    path.textContent = example.path || "";
                    item.append(title, document.createTextNode(" → "), path);
                    previewList.appendChild(item);
                });
            } else {
                const emptyItem = document.createElement("li");
                emptyItem.className = "text-sm text-base-content/60 italic";
                emptyItem.textContent = "No preview examples available.";
                previewList.appendChild(emptyItem);
            }
        } catch (error) {
            previewList.innerHTML = "";
            const errorItem = document.createElement("li");
            errorItem.className = "text-sm text-error";
            errorItem.textContent = `Failed to generate preview: ${error.message}`;
            previewList.appendChild(errorItem);
        }
    }

    async function handleMediaManagementSubmit(form) {
        const statusTarget = document.getElementById("mediaStatus");
        const submitButton = form.querySelector("button[type=\"submit\"]");

        if (!statusTarget || !submitButton) {
            return;
        }

        const templateValue = getInputValue("mm_naming_template") || "simple";
        const mediaPayload = {
            naming_template: templateValue
        };

        const conversionPayload = {
            convert_mp3_to_m4b: getCheckboxValue("conversion_convert_mp3"),
            codec: getInputValue("conv_codec") || "aac",
            bitrate: getInputValue("conv_bitrate") || "64k",
            channels: getInputValue("conv_channels") || "1",
            sample_rate: getInputValue("conv_sample_rate") || "22050"
        };

        statusTarget.textContent = "Saving media management settings…";
        statusTarget.className = "text-xs text-info";
        submitButton.classList.add("loading");
        submitButton.disabled = true;

        try {
            await fetchJson("/settings/api/media-management", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    ...mediaPayload,
                    conversion: conversionPayload
                })
            });

            statusTarget.textContent = "Saved";
            statusTarget.className = "text-xs text-success";
            showNotification("Media management settings saved.", "success");

            await loadAllData({ silent: true });
        } catch (error) {
            statusTarget.textContent = error.message;
            statusTarget.className = "text-xs text-error";
            showNotification(`Failed to save media settings: ${error.message}`, "error");
        } finally {
            submitButton.classList.remove("loading");
            submitButton.disabled = false;
        }
    }

    async function handleDownloadManagementSubmit(form) {
        const statusTarget = document.getElementById("downloadManagementStatus");
        const submitButton = form.querySelector("button[type=\"submit\"]");

        if (statusTarget) {
            statusTarget.textContent = "Saving download management settings…";
            statusTarget.className = "text-xs text-info";
        }
        if (submitButton) {
            submitButton.classList.add("loading");
            submitButton.disabled = true;
        }

        const downloadPayload = {
            seeding_enabled: getCheckboxValue("dm_seeding_enabled"),
            delete_source_after_import: getCheckboxValue("dm_delete_source"),
            auto_start_monitoring: getCheckboxValue("dm_auto_start_monitoring"),
            monitor_seeding: getCheckboxValue("dm_monitor_seeding"),
            max_concurrent_downloads: toNumeric(getInputValue("dm_max_concurrent_downloads"), state.downloadSettings.max_concurrent_downloads || 3),
            queue_priority_default: toNumeric(getInputValue("dm_queue_priority"), state.downloadSettings.queue_priority_default || 5),
            monitoring_interval: toNumeric(getInputValue("dm_monitor_interval"), state.downloadSettings.monitoring_interval || 2),
            retry_search_max: toNumeric(getInputValue("dm_retry_search"), state.downloadSettings.retry_search_max || 3),
            retry_download_max: toNumeric(getInputValue("dm_retry_download"), state.downloadSettings.retry_download_max || 2),
            retry_conversion_max: toNumeric(getInputValue("dm_retry_conversion"), state.downloadSettings.retry_conversion_max || 1),
            retry_import_max: toNumeric(getInputValue("dm_retry_import"), state.downloadSettings.retry_import_max || 2),
            retry_backoff_minutes: toNumeric(getInputValue("dm_retry_backoff"), state.downloadSettings.retry_backoff_minutes || 30),
            minimum_free_space: toNumeric(getInputValue("dm_minimum_free_space"), state.downloadSettings.minimum_free_space || 100),
            free_space_unit: getInputValue("dm_free_space_unit") || state.downloadSettings.free_space_unit || "MB",
            skip_free_space_check: getCheckboxValue("dm_skip_free_space_check"),
            ratio_protection_enabled: getCheckboxValue("dm_ratio_protection_enabled"),
            ratio_floor: toNumeric(getInputValue("dm_ratio_floor"), state.downloadSettings.ratio_floor || 1.0),
            prefer_freeleech: getCheckboxValue("dm_prefer_freeleech"),
            hold_non_freeleech: getCheckboxValue("dm_hold_non_freeleech"),
            freeleech_hold_days: toNumeric(getInputValue("dm_freeleech_hold_days"), state.downloadSettings.freeleech_hold_days || 14),
            duplicate_grab_protection: getCheckboxValue("dm_duplicate_grab_protection")
        };

        const configUpdates = {
            auto_search: {
                auto_download_enabled: getCheckboxValue("auto_download_enabled"),
                schedule_window_enabled: getCheckboxValue("schedule_window_enabled"),
                schedule_start_hour: toNumeric(getInputValue("schedule_start_hour"), 0),
                schedule_end_hour: toNumeric(getInputValue("schedule_end_hour"), 24)
            },
            import: {
                overwrite_existing_files: getCheckboxValue("import_overwrite_existing")
            }
        };

        try {
            await Promise.all([
                fetchJson("/settings/api/download-management", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(downloadPayload)
                }),
                fetchJson("/settings/config/update", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ updates: configUpdates })
                })
            ]);

            if (statusTarget) {
                statusTarget.textContent = "Saved";
                statusTarget.className = "text-xs text-success";
            }
            showNotification("Download management settings saved.", "success");
            await loadAllData({ silent: true });
        } catch (error) {
            if (statusTarget) {
                statusTarget.textContent = error.message;
                statusTarget.className = "text-xs text-error";
            }
            showNotification(`Failed to save download settings: ${error.message}`, "error");
        } finally {
            if (submitButton) {
                submitButton.classList.remove("loading");
                submitButton.disabled = false;
            }
        }
    }

    function updateTimestamp() {
        const target = document.getElementById("configUpdatedAt");
        if (!target) {
            return;
        }

        if (!state.lastLoaded) {
            target.textContent = "Configuration not yet loaded";
            return;
        }

        target.textContent = `Configuration loaded ${state.lastLoaded.toLocaleString()}`;
    }

    function handleAutoDownloadToggle(event) {
        const checkbox = event.target;
        if (!checkbox || !checkbox.checked) {
            return;
        }

        checkbox.checked = false;
        openModal("autoDownloadConfirmModal");
    }

    function handleAutoDownloadConfirm() {
        const checkbox = document.getElementById("auto_download_enabled");
        if (checkbox) {
            checkbox.checked = true;
        }
        closeModal("autoDownloadConfirmModal");
    }

    function handleAutoDownloadCancel() {
        const checkbox = document.getElementById("auto_download_enabled");
        if (checkbox) {
            checkbox.checked = false;
        }
        closeModal("autoDownloadConfirmModal");
    }

    async function handleCheckUpdates() {
        const btn = document.getElementById("checkUpdatesBtn");
        const msg = document.getElementById("updateResultMsg");
        if (!btn || !msg) return;

        btn.classList.add("loading");
        btn.disabled = true;
        msg.textContent = "Checking…";
        msg.className = "text-xs mt-1";
        msg.classList.remove("hidden");

        try {
            const resp = await fetch("/settings/api/system/check-updates", { method: "POST" });
            const data = await resp.json();
            if (!resp.ok || !data.success) throw new Error(data.error || "Request failed");

            if (data.updates_available) {
                msg.innerHTML = `<span class="text-warning">${data.message}</span>`
                    + (data.release_url ? ` &mdash; <a href="${data.release_url}" target="_blank" rel="noopener noreferrer" class="link link-primary">View release</a>` : "");
            } else {
                msg.innerHTML = `<span class="text-success">${data.message}</span>`;
            }
        } catch (err) {
            msg.innerHTML = `<span class="text-error">Could not check for updates: ${err.message}</span>`;
        } finally {
            btn.classList.remove("loading");
            btn.disabled = false;
        }
    }

    async function handleGeneralSubmit(form) {
        const selectedTheme = getInputValue("app_theme") || "dark";
        const updates = {
            application: {
                theme: selectedTheme
            }
        };

        const wasSaved = await submitConfigUpdates({ updates }, form, "General settings saved.");
        if (wasSaved) {
            applyThemePreference(selectedTheme);
        }
    }

    async function submitConfigUpdates(payload, form, successMessage) {
        const statusTarget = form.dataset.statusTarget ? document.getElementById(form.dataset.statusTarget) : null;
        const submitButton = form.querySelector("button[type=\"submit\"]");

        if (statusTarget) {
            statusTarget.textContent = "Saving…";
            statusTarget.className = "text-xs text-info";
        }

        if (submitButton) {
            submitButton.classList.add("loading");
            submitButton.disabled = true;
        }

        try {
            const response = await fetchJson("/settings/config/update", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            });

            if (statusTarget) {
                const message = response.message || "Saved";
                statusTarget.textContent = message;
                statusTarget.className = "text-xs text-success";
            }

            showNotification(successMessage || "Settings saved", "success");
            await loadAllData({ silent: true });
            return true;
        } catch (error) {
            if (statusTarget) {
                statusTarget.textContent = error.message;
                statusTarget.className = "text-xs text-error";
            }
            showNotification(`Unable to save settings: ${error.message}`, "error");
            return false;
        } finally {
            if (submitButton) {
                submitButton.classList.remove("loading");
                submitButton.disabled = false;
            }
        }
    }

    function handleThemeSelectionPreview(event) {
        const theme = event && event.target ? event.target.value : null;
        if (!theme) {
            return;
        }
        if (typeof setTheme === "function") {
            setTheme(theme, { persist: false });
        } else {
            document.documentElement.setAttribute("data-theme", theme);
        }
    }

    function applyThemePreference(theme) {
        if (typeof setTheme === "function") {
            setTheme(theme);
        } else {
            document.documentElement.setAttribute("data-theme", theme || "dark");
            try {
                localStorage.setItem("theme", theme || "dark");
            } catch (storageError) {
            }
        }
    }

    function getStoredThemePreference() {
        try {
            return localStorage.getItem("theme") || "dark";
        } catch (_err) {
            return "dark";
        }
    }

    function showClientPanel(clientType) {
        const sections = document.querySelectorAll("[data-client-panel]");
        sections.forEach((section) => {
            if (section.dataset.clientPanel === clientType) {
                section.classList.remove("hidden");
            } else {
                section.classList.add("hidden");
            }
        });

        updateQbPathMappingVisibility(clientType);
    }

    function toggleRefreshButton(isLoading) {
        const button = document.getElementById("refreshSettings");
        if (!button) {
            return;
        }

        if (isLoading) {
            button.classList.add("loading");
            button.disabled = true;
        } else {
            button.classList.remove("loading");
            button.disabled = false;
        }
    }

    async function fetchJson(url, options = {}) {
        const response = await fetch(url, options);
        const payload = await response.json();

        if (!response.ok || payload.success === false) {
            const message = payload.error || payload.message || `Request failed (${response.status})`;
            throw new Error(message);
        }

        return payload;
    }

    function safeAddFormHandler(id, handler) {
        const form = document.getElementById(id);
        if (form) {
            form.addEventListener("submit", (event) => {
                event.preventDefault();
                if (!form.reportValidity()) {
                    return;
                }
                handler(form);
            });
        }
    }

    function safeAddEventListener(targetOrId, eventName, handler) {
        const element = typeof targetOrId === "string" ? document.getElementById(targetOrId) : targetOrId;
        if (element) {
            element.addEventListener(eventName, handler);
        }
    }

    function setInputValue(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.value = value == null ? "" : value;
        }
    }

    const CONV_CODEC_LABELS = { copy: "copy", aac: "aac", libopus: "opus" };

    function setupConversionQualityControls() {
        document.querySelectorAll(".conv-tab").forEach((tab) => {
            tab.addEventListener("click", () => {
                const panel = tab.dataset.convPanel;
                document.querySelectorAll(".conv-tab").forEach((t) => {
                    const active = t === tab;
                    t.classList.toggle("btn-primary", active);
                    t.classList.toggle("btn-ghost", !active);
                });
                const presets = document.getElementById("convPresetPanel");
                const advanced = document.getElementById("convAdvancedPanel");
                if (presets) presets.classList.toggle("hidden", panel !== "presets");
                if (advanced) advanced.classList.toggle("hidden", panel !== "advanced");
            });
        });

        document.querySelectorAll(".conv-preset-group").forEach((group) => {
            const targetId = group.dataset.target;
            group.querySelectorAll(".conv-preset").forEach((btn) => {
                btn.addEventListener("click", () => {
                    setInputValue(targetId, btn.dataset.value);
                    syncConversionPresets();
                });
            });
        });

        ["conv_codec", "conv_bitrate", "conv_channels"].forEach((id) => {
            safeAddEventListener(id, "input", syncConversionPresets);
        });
    }

    function syncConversionPresets() {
        document.querySelectorAll(".conv-preset-group").forEach((group) => {
            const value = getInputValue(group.dataset.target);
            group.querySelectorAll(".conv-preset").forEach((btn) => {
                const active = btn.dataset.value === value;
                btn.classList.toggle("btn-primary", active);
                btn.classList.toggle("btn-ghost", !active);
            });
        });

        const codec = getInputValue("conv_codec") || "aac";
        const bitrate = getInputValue("conv_bitrate") || "64k";
        const channels = getInputValue("conv_channels") || "1";
        const channelLabel = channels === "2" ? "stereo" : channels === "1" ? "mono" : `${channels}ch`;
        const codecLabel = CONV_CODEC_LABELS[codec] || codec;
        const summary = document.getElementById("convSummary");
        if (summary) {
            summary.textContent = codec === "copy"
                ? "copy (remux, no re-encode)"
                : `${codecLabel} · ${bitrate} · ${channelLabel}`;
        }
    }

    function setSelectValue(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.value = value == null || value === "" ? element.value : value;
        }
    }

    function setNumericInput(id, value, fallback) {
        const element = document.getElementById(id);
        if (!element) {
            return;
        }

        const numeric = toNumeric(value, fallback);
        element.value = Number.isFinite(numeric) ? numeric : fallback || "";
    }

    function setCheckboxValue(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.checked = toBoolean(value);
        }
    }

    function getInputValue(id) {
        const element = document.getElementById(id);
        if (!element) {
            return "";
        }
        return element.value.trim();
    }

    function getCheckboxValue(id) {
        const element = document.getElementById(id);
        return element ? element.checked : false;
    }

    function toBoolean(value) {
        if (typeof value === "boolean") {
            return value;
        }
        if (value == null) {
            return false;
        }
        const normalized = String(value).trim().toLowerCase();
        return ["true", "1", "yes", "on"].includes(normalized);
    }

    function toNumeric(value, fallback = 0) {
        if (value === "" || value === null || value === undefined) {
            return fallback;
        }
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : fallback;
    }

    function stripQuotes(value) {
        if (typeof value !== "string") {
            return value;
        }
        return value.replace(/^"|"$/g, "");
    }

    function slugify(value) {
        return (value || "")
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "_")
            .replace(/^_+|_+$/g, "")
            .slice(0, 60);
    }

    async function handleAudibleSetupInfo(event) {
        if (event) {
            event.preventDefault();
        }

        toggleButtonLoading("audibleSetupButton", true);

        try {
            const payload = await fetchJson("/api/audible/library/setup-info");
            const info = payload.data || payload;
            state.audible.setupInfo = info;
            updateAudibleInfoPanel(renderAudibleSetupInfo(info));
            showNotification("Setup guide loaded.", "success");
        } catch (error) {
            showNotification(`Failed to load setup guide: ${error.message}`, "error");
        } finally {
            toggleButtonLoading("audibleSetupButton", false);
        }
    }

    async function handleAudibleSyncFull(event) {
        if (event) {
            event.preventDefault();
        }

        toggleButtonLoading("audibleSyncFullButton", true);
        const helper = document.getElementById("audibleStatusHelper");
        if (helper) {
            helper.textContent = "Starting full library sync…";
        }

        try {
            const payload = await fetchJson("/api/audible/library/sync/full", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({})
            });

            const data = payload.data || payload;
            const message = data.message || "Full sync started.";
            showNotification(message, "success");
            if (helper) {
                helper.textContent = `${message} (${formatDateTime(new Date())})`;
            }
            await refreshAudibleStatus({ skipButtonState: true });
        } catch (error) {
            showNotification(`Full sync failed: ${error.message}`, "error");
            if (helper) {
                helper.textContent = `Full sync failed: ${error.message}`;
            }
        } finally {
            toggleButtonLoading("audibleSyncFullButton", false);
        }
    }

    async function handleAudibleSyncQuick(event) {
        if (event) {
            event.preventDefault();
        }

        toggleButtonLoading("audibleSyncQuickButton", true);
        const helper = document.getElementById("audibleStatusHelper");
        if (helper) {
            helper.textContent = "Starting quick library sync…";
        }

        try {
            const payload = await fetchJson("/api/audible/library/sync/quick", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({})
            });

            const data = payload.data || payload;
            const message = data.message || "Quick sync started.";
            showNotification(message, "success");
            if (helper) {
                helper.textContent = `${message} (${formatDateTime(new Date())})`;
            }
            await refreshAudibleStatus({ skipButtonState: true });
        } catch (error) {
            showNotification(`Quick sync failed: ${error.message}`, "error");
            if (helper) {
                helper.textContent = `Quick sync failed: ${error.message}`;
            }
        } finally {
            toggleButtonLoading("audibleSyncQuickButton", false);
        }
    }

    async function handleAudibleLibraryRefresh(event) {
        if (event) {
            event.preventDefault();
        }

        toggleButtonLoading("audibleRefreshLibraryButton", true);
        setElementText("audibleCacheDetails", "Refreshing Audible library cache…");

        try {
            const payload = await fetchJson("/api/audible/library/refresh", {
                method: "POST"
            });

            const data = payload.data || payload;
            const message = data.message || "Library cache refreshed.";
            showNotification(message, "success");
            state.audible.serviceStatus = state.audible.serviceStatus || {};
            state.audible.serviceStatus.cached_library = {
                available: true,
                book_count: data.book_count ?? data.data?.book_count ?? 0,
                last_updated: data.last_updated || new Date().toISOString()
            };
            renderAudibleStatus();
            await refreshAudibleStatus({ skipButtonState: true });
        } catch (error) {
            setElementText("audibleCacheDetails", `Refresh failed: ${error.message}`);
            showNotification(`Library cache refresh failed: ${error.message}`, "error");
        } finally {
            toggleButtonLoading("audibleRefreshLibraryButton", false);
        }
    }

    async function handleAudibleDownloadAll(event) {
        if (event) {
            event.preventDefault();
        }

        toggleButtonLoading("audibleDownloadAllButton", true);
        setElementText("audibleBulkDownloadStatus", "Preparing to download entire library…");

        const audibleDefaults = state.mediaSettings?.audible_downloads || {};
        const format = getInputValue("audible_format") || audibleDefaults.format || "aaxc";
        const quality = getInputValue("audible_quality") || audibleDefaults.quality || "best";
        const includePdf = getCheckboxValue("audible_include_pdf");
        const includeCover = getCheckboxValue("audible_include_cover");
        const includeChapters = getCheckboxValue("audible_include_chapters");
        const concurrencyDefault = audibleDefaults.concurrent_downloads || 1;
        const jobs = Math.max(1, toNumeric(getInputValue("audible_concurrent_downloads"), concurrencyDefault || 1));

        try {
            const result = await fetchJson("/api/audible/library/download/all", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    format,
                    quality,
                    include_pdf: includePdf,
                    include_cover: includeCover,
                    include_chapters: includeChapters,
                    jobs
                })
            });

            if (!result.success) {
                throw new Error(result.error || "Bulk download failed to start");
            }

            const summaryParts = [result.message || "Bulk download started."];
            if (result.job_id) {
                summaryParts.push(`Job: ${result.job_id}`);
            } else if (result.download_id) {
                summaryParts.push(`ID: ${result.download_id}`);
            }
            if (Array.isArray(result.queued_download_ids) && result.queued_download_ids.length) {
                summaryParts.push(`Jobs queued: ${result.queued_download_ids.length}`);
            }
            if (Array.isArray(result.warnings) && result.warnings.length) {
                summaryParts.push(result.warnings.join(" "));
            }

            const statusMessage = summaryParts.join(" ");
            setElementText("audibleBulkDownloadStatus", statusMessage);
            showNotification(statusMessage, "success");
        } catch (error) {
            const message = `Failed to start bulk download: ${error.message}`;
            setElementText("audibleBulkDownloadStatus", message);
            showNotification(message, "error");
        } finally {
            toggleButtonLoading("audibleDownloadAllButton", false);
        }
    }

    async function handleAudibleExport(event) {
        if (event) {
            event.preventDefault();
        }

        toggleButtonLoading("audibleExportButton", true);
        setElementText("audibleExportSummary", "Exporting library…");

        try {
            const payload = await fetchJson("/api/audible/library/export?format=json");
            const data = payload.data || payload;
            const bookCount = data.book_count ?? data.data?.book_count ?? 0;
            const timestamp = data.last_updated || new Date().toISOString();
            setElementText("audibleExportSummary", `Export includes ${formatNumber(bookCount)} titles (generated ${formatDateTime(timestamp)}).`);
            showNotification("Library export completed.", "success");
            await refreshAudibleStatus({ skipButtonState: true });
        } catch (error) {
            setElementText("audibleExportSummary", `Export failed: ${error.message}`);
            showNotification(`Library export failed: ${error.message}`, "error");
        } finally {
            toggleButtonLoading("audibleExportButton", false);
        }
    }

    async function handleAudibleStatsLoad(event) {
        if (event) {
            event.preventDefault();
        }

        const triggerId = event?.currentTarget?.id;
        if (triggerId) {
            toggleButtonLoading(triggerId, true);
        }

        const container = document.getElementById("audibleStatsContent");
        if (container) {
            container.textContent = "Loading statistics…";
        }

        try {
            const payload = await fetchJson("/api/audible/library/stats");
            state.audible.stats = payload.data || payload;
            renderAudibleStatsContent();
            showNotification("Library statistics loaded.", "success");
        } catch (error) {
            if (container) {
                container.textContent = `Failed to load statistics: ${error.message}`;
            }
            showNotification(`Failed to load statistics: ${error.message}`, "error");
        } finally {
            if (triggerId) {
                toggleButtonLoading(triggerId, false);
            }
        }
    }

    async function handleAudibleValidate(event) {
        if (event) {
            event.preventDefault();
        }

        toggleButtonLoading("audibleValidateButton", true);

        try {
            const payload = await fetchJson("/api/audible/library/validate-credentials");
            const data = payload.data || payload;
            const isValid = Boolean(data.valid);
            const message = data.message || (isValid ? "Credentials validated." : "Credentials require attention.");
            showNotification(message, isValid ? "success" : "warning");

            if (data.instructions) {
                updateAudibleInfoPanel(renderAudibleSetupInfo(data.instructions));
            }
        } catch (error) {
            showNotification(`Credential validation failed: ${error.message}`, "error");
        } finally {
            toggleButtonLoading("audibleValidateButton", false);
        }
    }

    function updateAudibleInfoPanel(content) {
        const panel = document.getElementById("audibleInfoPanel");
        if (!panel) {
            return;
        }

        if (content) {
            panel.innerHTML = content;
        } else {
            panel.textContent = "Use the quick actions above to authenticate, run syncs, or refresh your library.";
        }
    }

    function toggleButtonLoading(buttonId, isLoading) {
        if (!buttonId) {
            return;
        }

        const button = document.getElementById(buttonId);
        if (!button) {
            return;
        }

        button.classList.toggle("loading", Boolean(isLoading));
        button.disabled = Boolean(isLoading);
    }

    function setElementText(id, text) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = text == null ? "" : text;
        }
    }

    function setBadge(id, variant, label) {
        const element = document.getElementById(id);
        if (!element) {
            return;
        }

        let variantClass = "badge-ghost";
        switch (variant) {
            case "success":
                variantClass = "badge-success";
                break;
            case "warning":
                variantClass = "badge-warning";
                break;
            case "error":
                variantClass = "badge-error";
                break;
            case "info":
                variantClass = "badge-info";
                break;
            default:
                variantClass = "badge-ghost";
                break;
        }

        element.className = `badge badge-sm ${variantClass}`;
        if (label !== undefined) {
            element.textContent = label;
        }
    }

    function openModal(id) {
        const modal = document.getElementById(id);
        if (modal && typeof modal.showModal === "function" && !modal.open) {
            modal.showModal();
        }
    }

    function closeModal(id) {
        const modal = document.getElementById(id);
        if (modal && typeof modal.close === "function" && modal.open) {
            modal.close();
        }
    }

    function escapeHtml(value) {
        if (value == null) {
            return "";
        }

        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function formatDateTime(value) {
        if (!value) {
            return "";
        }

        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime())) {
            return "";
        }

        return date.toLocaleString();
    }

    function formatNumber(value, fractionDigits = 0) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) {
            return "0";
        }

        return numeric.toLocaleString(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: fractionDigits
        });
    }

    function formatMinutesToHours(minutes) {
        const numeric = Number(minutes);
        if (!Number.isFinite(numeric) || numeric <= 0) {
            return "N/A";
        }

        const totalMinutes = Math.round(numeric);
        const hrs = Math.floor(totalMinutes / 60);
        const mins = totalMinutes % 60;

        if (hrs && mins) {
            return `${hrs}h ${mins}m`;
        }
        if (hrs) {
            return `${hrs}h`;
        }
        return `${mins}m`;
    }

    function formatCapabilityLabel(key) {
        if (!key) {
            return "";
        }

        return key
            .split("_")
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join(" ");
    }

})();

(function () {
    const logWindow   = document.getElementById('logWindow');
    const pauseBtn    = document.getElementById('logPauseButton');
    const pauseLabel  = document.getElementById('logPauseLabel');
    const clearBtn    = document.getElementById('logClearButton');
    const liveIndicator = document.getElementById('logLiveIndicator');
    if (!logWindow) return;

    const POLL_INTERVAL_MS = 3000;
    const MAX_LINES        = 500;
    let paused          = false;
    let lastTimestamp   = null; // track last seen entry to avoid duplicates
    let intervalId      = null;

    const LEVEL_CLASSES = {
        error:   'log-error',
        warning: 'log-warning',
        warn:    'log-warning',
        debug:   'log-debug',
        info:    '',
        success: 'log-success',
    };

    const LEVEL_PREFIXES = {
        error:   '✕',
        warning: '!',
        warn:    '!',
        debug:   '·',
        info:    '>',
        success: '✓',
    };

    function levelClass(lvl) {
        return LEVEL_CLASSES[(lvl || '').toLowerCase()] || 'text-base-content';
    }

    function levelPrefix(lvl) {
        return LEVEL_PREFIXES[(lvl || '').toLowerCase()] || '>';
    }

    function appendLine(entry) {
        const placeholder = logWindow.querySelector('pre[data-placeholder]');
        if (placeholder) placeholder.remove();

        const src  = entry.source ? ` [${entry.source}]` : '';
        const ts   = entry.timestamp ? entry.timestamp.split(' ').pop() : '';
        const text = `${ts}${src}  ${entry.message}`;

        const pre  = document.createElement('pre');
        pre.setAttribute('data-prefix', levelPrefix(entry.level));
        pre.className = levelClass(entry.level);
        const code = document.createElement('code');
        code.textContent = text;
        pre.appendChild(code);
        logWindow.appendChild(pre);

        const lines = logWindow.querySelectorAll('pre');
        if (lines.length > MAX_LINES) {
            for (let i = 0; i < lines.length - MAX_LINES; i++) {
                lines[i].remove();
            }
        }

        const nearBottom = logWindow.scrollHeight - logWindow.scrollTop - logWindow.clientHeight < 60;
        if (nearBottom) {
            logWindow.scrollTop = logWindow.scrollHeight;
        }
    }

    async function fetchLogs() {
        if (paused) return;
        try {
            const url  = '/settings/api/logs/latest'
                + (lastTimestamp ? ('?after=' + encodeURIComponent(lastTimestamp)) : '');
            const res  = await fetch(url);
            const data = await res.json();
            if (!data.success || !data.logs) return;

            let entries = data.logs;
            if (lastTimestamp) {
                const idx = entries.findLastIndex(e => e.timestamp === lastTimestamp);
                if (idx >= 0) entries = entries.slice(idx + 1);
            }
            if (entries.length > 0) {
                lastTimestamp = entries[entries.length - 1].timestamp;
                if (logWindow.querySelector('pre[data-placeholder]')) {
                    logWindow.innerHTML = '';
                }
                entries.forEach(appendLine);
            } else if (!lastTimestamp) {
                logWindow.innerHTML = '<pre data-placeholder style="color:#6b7280"><code>No log entries found.</code></pre>';
            }

            liveIndicator.querySelector('.status').classList.remove('status-error');
            liveIndicator.querySelector('.status').classList.add('status-success');
        } catch {
            liveIndicator.querySelector('.status').classList.remove('status-success');
            liveIndicator.querySelector('.status').classList.add('status-error');
        }
    }

    const initial = logWindow.querySelector('pre');
    if (initial) initial.setAttribute('data-placeholder', '');

    fetchLogs();
    intervalId = setInterval(fetchLogs, POLL_INTERVAL_MS);

    if (pauseBtn) {
        pauseBtn.addEventListener('click', () => {
            paused = !paused;
            pauseLabel.textContent = paused ? 'Resume' : 'Pause';
            const statusLabel = document.getElementById('logStatusLabel');
            if (paused) {
                liveIndicator.querySelector('.status').classList.remove('status-success', 'animate-pulse');
                liveIndicator.querySelector('.status').classList.add('status-warning');
                if (statusLabel) statusLabel.textContent = 'Paused';
            } else {
                liveIndicator.querySelector('.status').classList.remove('status-warning');
                liveIndicator.querySelector('.status').classList.add('status-success', 'animate-pulse');
                if (statusLabel) statusLabel.textContent = 'Live';
                fetchLogs();
            }
        });
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            logWindow.innerHTML = '<pre data-placeholder style="color:#6b7280"><code>Log cleared.</code></pre>';
            lastTimestamp = null;
        });
    }

    document.querySelectorAll('.settings-sub-link').forEach(link => {
        link.addEventListener('click', () => {
            const section = link.getAttribute('data-settings-section');
            if (section === 'general') {
                if (!intervalId) intervalId = setInterval(fetchLogs, POLL_INTERVAL_MS);
            } else {
                clearInterval(intervalId);
                intervalId = null;
            }
        });
    });
})();
