'use strict';

(function () {
    const readJson = (elementId, fallback) => {
        const node = document.getElementById(elementId);
        if (!node) {
            return fallback;
        }
        try {
            const text = node.textContent || node.innerText || '';
            return text ? JSON.parse(text) : fallback;
        } catch (error) {
            return fallback;
        }
    };

    // Libraries, loaded once; the library chip only shows with more than one
    const libraries = { list: [] };
    AA.apiFetch('/api/libraries', { method: 'GET' }).then(({ ok, data }) => {
        if (ok && data && data.libraries) {
            libraries.list = data.libraries.filter((l) => l.enabled);
            if (libraries.list.length > 1 && typeof renderQueue === 'function') renderQueue();
        }
    }).catch(() => {});

    function chooseLibrary(downloadId, currentId) {
        let dialog = document.getElementById('dlLibraryDialog');
        if (!dialog) {
            dialog = document.createElement('dialog');
            dialog.id = 'dlLibraryDialog';
            dialog.className = 'modal';
            dialog.innerHTML = `<div class="modal-box max-w-sm space-y-3">
                <h3 class="font-semibold">Import into library</h3>
                <select class="select select-bordered w-full" id="dlLibrarySelect"></select>
                <p class="text-xs opacity-70" id="dlLibraryError"></p>
                <div class="modal-action"><button type="button" class="btn btn-ghost btn-sm" data-close>Cancel</button>
                <button type="button" class="btn btn-primary btn-sm" data-save>Save</button></div></div>
                <form method="dialog" class="modal-backdrop"><button aria-label="Close"></button></form>`;
            document.body.appendChild(dialog);
            dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
        }
        const select = dialog.querySelector('#dlLibrarySelect');
        select.innerHTML = libraries.list.map((l) => `<option value="${l.id}">${escapeHtml(l.name)}${l.is_default ? ' (default)' : ''}</option>`).join('');
        const fallback = libraries.list.find((l) => l.is_default);
        select.value = currentId || (fallback ? fallback.id : '');
        dialog.querySelector('#dlLibraryError').textContent = '';
        const save = dialog.querySelector('[data-save]');
        save.onclick = async () => {
            const { ok, data } = await AA.apiFetch(`/api/libraries/downloads/${downloadId}/target`, {
                method: 'POST', body: JSON.stringify({ library_id: Number(select.value) }),
            });
            if (!ok || !data || data.success === false) {
                dialog.querySelector('#dlLibraryError').textContent = (data && data.error) || 'Could not change the library';
                return;
            }
            const item = state.queue.find((q) => String(q.id ?? q.ID) === String(downloadId));
            if (item) item.target_library_id = Number(select.value);
            dialog.close();
            renderQueue();
        };
        dialog.showModal();
    }

    document.addEventListener('click', (event) => {
        const chip = event.target.closest('[data-library-target]');
        if (chip) chooseLibrary(chip.dataset.libraryTarget, chip.dataset.libraryId);
    });

    const state = {
        queue: readJson('initialQueueData', []),
        status: readJson('initialStatusData', {}),
        history: readJson('initialHistoryData', []),
        activeTab: 'downloads'
    };

    const elements = {
        pipelineContainer: document.getElementById('pipelineContainer'),
        pipelineEmpty: document.getElementById('pipelineEmpty'),
        downloadsPanel: document.getElementById('downloadsPanel'),
        seedingPanel: document.getElementById('seedingPanel'),
        seedingContainer: document.getElementById('seedingContainer'),
        seedingEmpty: document.getElementById('seedingEmpty'),
        historyContainer: document.getElementById('historyContainer'),
        historyEmpty: document.getElementById('historyEmpty'),
        statActive: document.getElementById('stat-active'),
        statDownloading: document.getElementById('stat-downloading'),
        statConverting: document.getElementById('stat-converting'),
        statSeeding: document.getElementById('stat-seeding'),
        badgeMonitorState: document.getElementById('badgeMonitorState'),
        statPolling: document.getElementById('stat-polling'),
        refreshPipelineBtn: document.getElementById('refreshPipeline'),
        refreshHistoryBtn: document.getElementById('refreshHistory'),
        clearQueueBtn: document.getElementById('clearQueue'),
        downloadsRoot: document.getElementById('downloadsRoot'),
        tabsWrapper: document.getElementById('queueTabs'),
        tabDownloads: document.getElementById('tabDownloads'),
        tabSeeding: document.getElementById('tabSeeding')
    };

    if (!elements.pipelineContainer) {
        return;
    }

    const MIN_POLL_SECONDS = 2;
    const MAX_POLL_SECONDS = 30;
    const DEFAULT_SEEDING_GOAL_HOURS = 72;
    const DEFAULT_SEEDING_GOAL_SECONDS = DEFAULT_SEEDING_GOAL_HOURS * 3600;
    let lastPollSeconds = 0;
    let queuePollTimer = null;
    let historyPollTimer = null;
    const seedingCountdowns = new Map();
    let seedingCountdownTimer = null;

    const ACTIONS_BY_STATUS = {
        QUEUED: ['cancel'],
        SEARCHING: ['cancel'],
        FOUND: ['cancel'],
        DOWNLOADING: ['pause', 'cancel'],
        AUDIBLE_DOWNLOADING: ['cancel'],
        AUDIBLE_DOWNLOAD_FAILED: ['retry', 'remove'],
        DOWNLOAD_COMPLETE: ['cancel'],
        COMPLETE: ['cancel'],
        CONVERTING: ['cancel'],
        CONVERTED: ['cancel'],
        PROCESSING: ['cancel'],
        PROCESSED: ['cancel'],
        IMPORTING: ['cancel'],
        IMPORTED: [],
        SEEDING: ['cancel'],
        SEEDING_COMPLETE: [],
        PAUSED: ['resume', 'cancel'],
        SEARCH_FAILED: ['retry', 'remove'],
        DOWNLOAD_FAILED: ['retry', 'remove'],
        CONVERSION_FAILED: ['retry', 'remove'],
        IMPORT_FAILED: ['retry', 'remove'],
        FAILED: ['retry', 'remove'],
        ERROR: ['remove'],
        CANCELLED: ['remove']
    };

    const toNumber = (value) => {
        if (value === undefined || value === null || value === '') {
            return null;
        }
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    };

    const escapeHtml = (value) => {
        if (value === undefined || value === null) {
            return '';
        }
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    };

    function isSeedingStatus(status) {
        const normalized = (status || '').toUpperCase();
        return normalized === 'SEEDING' || normalized === 'SEEDING_COMPLETE';
    }

    const formatDuration = (seconds) => AA.formatDuration(seconds);

    const getSeedingGoalSeconds = (item) => {
        const candidateFields = [
            item && item.seed_time_limit_seconds,
            item && item.seeding_goal_seconds,
            item && item.seed_time_limit,
            state.status && state.status.seed_time_limit_seconds,
            state.status && state.status.seeding_goal_seconds
        ];
        for (const field of candidateFields) {
            const value = toNumber(field);
            if (value && value > 0) {
                return value;
            }
        }
        const configHours = toNumber(state.status && state.status.seeding_goal_hours);
        if (configHours && configHours > 0) {
            return configHours * 3600;
        }
        return DEFAULT_SEEDING_GOAL_SECONDS;
    };

    const formatContributors = (value) => {
        if (!value) {
            return '';
        }
        if (Array.isArray(value)) {
            const names = value
                .map((entry) => {
                    if (!entry) {
                        return '';
                    }
                    if (typeof entry === 'string') {
                        return entry;
                    }
                    if (typeof entry === 'object') {
                        return entry.name || entry.preferred || '';
                    }
                    return String(entry);
                })
                .filter(Boolean);
            return names.join(', ');
        }
        if (typeof value === 'object') {
            if (value.name) {
                return value.name;
            }
            const nestedNames = Object.values(value)
                .map((entry) => {
                    if (!entry) {
                        return '';
                    }
                    if (typeof entry === 'string') {
                        return entry;
                    }
                    if (typeof entry === 'object' && entry.name) {
                        return entry.name;
                    }
                    return '';
                })
                .filter(Boolean);
            if (nestedNames.length) {
                return nestedNames.join(', ');
            }
        }

        const raw = String(value).trim();
        if (!raw) {
            return '';
        }

        const regex = /['"]name['"]\s*:\s*['"]([^'"}]+)['"]/g;
        const foundNames = [];
        let match;
        while ((match = regex.exec(raw))) {
            if (match[1]) {
                foundNames.push(match[1]);
            }
        }
        if (foundNames.length) {
            return foundNames.join(', ');
        }

        if (raw.includes('{') && raw.includes('}')) {
            const pseudoJson = `[${raw.replace(/'/g, '"').replace(/None/g, 'null')}]`;
            try {
                const parsed = JSON.parse(pseudoJson);
                if (Array.isArray(parsed)) {
                    const parsedNames = parsed
                        .map((entry) => (entry && (entry.name || entry.preferred)) || '')
                        .filter(Boolean);
                    if (parsedNames.length) {
                        return parsedNames.join(', ');
                    }
                }
            } catch (error) {
            }
        }

        return raw;
    };

    const STATUS_ORDER = [
        'QUEUED',
        'SEARCHING',
        'FOUND',
        'DOWNLOADING',
        'AUDIBLE_DOWNLOADING',
        'AUDIBLE_DOWNLOAD_FAILED',
        'DOWNLOAD_COMPLETE',
        'COMPLETE',
        'CONVERTING',
        'CONVERTED',
        'PROCESSING',
        'PROCESSED',
        'IMPORTING',
        'IMPORTED',
        'SEEDING',
        'SEEDING_COMPLETE',
        'PAUSED'
    ];
    const STATUS_META = {
        QUEUED: {
            label: 'Queued',
            icon: 'fa-inbox',
            description: 'Waiting for download pipeline slot.'
        },
        SEARCHING: {
            label: 'Searching',
            icon: 'fa-magnifying-glass',
            description: 'Locating the best source.'
        },
        FOUND: {
            label: 'Source Found',
            icon: 'fa-link',
            description: 'Preparing the client to start downloading.'
        },
        DOWNLOADING: {
            label: 'Downloading',
            icon: 'fa-download',
            description: 'Transfer in progress via client integration.'
        },
        AUDIBLE_DOWNLOADING: {
            label: 'Audible Downloading',
            icon: 'fa-headphones',
            description: 'Fetching directly from Audible.'
        },
        AUDIBLE_DOWNLOAD_FAILED: {
            label: 'Audible Failed',
            icon: 'fa-triangle-exclamation',
            description: 'Audible transfer failed; will retry if allowed.'
        },
        DOWNLOAD_COMPLETE: {
            label: 'Downloaded',
            icon: 'fa-circle-check',
            description: 'Download complete, waiting on next stage.'
        },
        COMPLETE: {
            label: 'Downloaded',
            icon: 'fa-circle-check',
            description: 'Download complete, awaiting processing.'
        },
        CONVERTING: {
            label: 'Converting',
            icon: 'fa-wand-magic-sparkles',
            description: 'Transcoding audio to library format (M4B).'
        },
        CONVERTED: {
            label: 'Converted',
            icon: 'fa-wand-magic-sparkles',
            description: 'Conversion finished, ready to import.'
        },
        PROCESSING: {
            label: 'Processing',
            icon: 'fa-gears',
            description: 'Post-processing the downloaded files.'
        },
        PROCESSED: {
            label: 'Processed',
            icon: 'fa-check-double',
            description: 'Processing complete, awaiting import.'
        },
        IMPORTING: {
            label: 'Importing',
            icon: 'fa-share-from-square',
            description: 'Moving into your library.'
        },
        IMPORTED: {
            label: 'Imported',
            icon: 'fa-books',
            description: 'Completed and added to the library.'
        },
        SEEDING: {
            label: 'Seeding',
            icon: 'fa-seedling',
            description: 'Sharing torrent until seeding goals are met.'
        },
        SEEDING_COMPLETE: {
            label: 'Seeding Complete',
            icon: 'fa-seedling',
            description: 'Seeding targets reached, finalizing cleanup.'
        },
        PAUSED: {
            label: 'Paused',
            icon: 'fa-pause',
            description: 'Download paused by user.'
        },
        SEARCH_FAILED: {
            label: 'Search Failed',
            icon: 'fa-triangle-exclamation',
            description: 'No matching source could be found.'
        },
        DOWNLOAD_FAILED: {
            label: 'Download Failed',
            icon: 'fa-triangle-exclamation',
            description: 'Download failed and could not be retried.'
        },
        CONVERSION_FAILED: {
            label: 'Conversion Failed',
            icon: 'fa-triangle-exclamation',
            description: 'Audio conversion failed.'
        },
        IMPORT_FAILED: {
            label: 'Import Failed',
            icon: 'fa-triangle-exclamation',
            description: 'Import into the library failed.'
        },
        FAILED: {
            label: 'Failed',
            icon: 'fa-triangle-exclamation',
            description: 'Download failed.'
        },
        ERROR: {
            label: 'Error',
            icon: 'fa-triangle-exclamation',
            description: 'An error occurred.'
        },
        CANCELLED: {
            label: 'Cancelled',
            icon: 'fa-ban',
            description: 'Cancelled by user.'
        }
    };

    const parseDate = (value) => {
        if (!value) {
            return null;
        }
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? null : date;
    };

    const formatRelativeTime = (value) => {
        const date = parseDate(value);
        if (!date) {
            return '';
        }
        const now = Date.now();
        const diffMs = now - date.getTime();
        if (diffMs < 0) {
            return 'just now';
        }
        const diffSeconds = Math.floor(diffMs / 1000);
        if (diffSeconds < 60) {
            return `${diffSeconds}s ago`;
        }
        const diffMinutes = Math.floor(diffSeconds / 60);
        if (diffMinutes < 60) {
            return `${diffMinutes}m ago`;
        }
        const diffHours = Math.floor(diffMinutes / 60);
        if (diffHours < 24) {
            return `${diffHours}h ago`;
        }
        const diffDays = Math.floor(diffHours / 24);
        if (diffDays < 30) {
            return `${diffDays}d ago`;
        }
        const diffMonths = Math.floor(diffDays / 30);
        if (diffMonths < 12) {
            return `${diffMonths}mo ago`;
        }
        const diffYears = Math.floor(diffMonths / 12);
        return `${diffYears}y ago`;
    };

    const formatDateTime = (value) => {
        const date = parseDate(value);
        if (!date) {
            return '';
        }
        return date.toLocaleString();
    };

    const formatEta = (seconds) => {
        const value = toNumber(seconds);
        if (value === null || value < 0) {
            return '';
        }
        if (value < 60) {
            return `${Math.round(value)}s remaining`;
        }
        if (value < 3600) {
            const minutes = Math.floor(value / 60);
            const secs = Math.round(value % 60);
            return `${minutes}m ${secs}s remaining`;
        }
        const hours = Math.floor(value / 3600);
        const minutes = Math.floor((value % 3600) / 60);
        return `${hours}h ${minutes}m remaining`;
    };

    const formatSpeed = (bytesPerSecond) => {
        const value = toNumber(bytesPerSecond);
        if (value === null || value <= 0) {
            return '';
        }
        if (value < 1024) {
            return `${value.toFixed(0)} B/s`;
        }
        if (value < 1024 * 1024) {
            return `${(value / 1024).toFixed(1)} KB/s`;
        }
        if (value < 1024 * 1024 * 1024) {
            return `${(value / (1024 * 1024)).toFixed(1)} MB/s`;
        }
        return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB/s`;
    };

    const liveProgress = new Map();

    const applyLiveProgress = (item) => {
        const lp = liveProgress.get(Number(item.id));
        if (!lp) {
            return item;
        }
        if (lp.progress !== undefined && lp.progress !== null) {
            item.download_progress = lp.progress;
        }
        if (lp.eta_seconds !== undefined) {
            item.eta_seconds = lp.eta_seconds;
        }
        if (lp.speed_bytes !== undefined) {
            item.download_speed_bytes = lp.speed_bytes;
        }
        if (lp.message) {
            item.progress_message = lp.message;
        }
        return item;
    };

    const getProgressValue = (item) => {
        const candidates = [
            item.download_progress,
            item.progress,
            item.progress_percentage,
            item.conversion_progress,
            item.import_progress
        ];
        for (const candidate of candidates) {
            const value = toNumber(candidate);
            if (value !== null) {
                const clamped = Math.max(0, Math.min(100, value));
                if (clamped > 0) {
                    return clamped;
                }
            }
        }
        return null;
    };

    const getStatusBadgeClass = (status) => {
        const normalized = (status || '').toUpperCase();
        const map = {
            QUEUED: 'badge-ghost',
            SEARCHING: 'badge-info',
            FOUND: 'badge-info',
            DOWNLOADING: 'badge-info',
            AUDIBLE_DOWNLOADING: 'badge-info',
            AUDIBLE_DOWNLOAD_FAILED: 'badge-error',
            DOWNLOAD_COMPLETE: 'badge-success',
            COMPLETE: 'badge-success',
            CONVERTING: 'badge-warning',
            CONVERTED: 'badge-warning',
            PROCESSING: 'badge-warning',
            PROCESSED: 'badge-warning',
            IMPORTING: 'badge-warning',
            IMPORTED: 'badge-success',
            SEEDING: 'badge-secondary',
            SEEDING_COMPLETE: 'badge-success',
            PAUSED: 'badge-ghost',
            SEARCH_FAILED: 'badge-error',
            DOWNLOAD_FAILED: 'badge-error',
            CONVERSION_FAILED: 'badge-error',
            IMPORT_FAILED: 'badge-error',
            FAILED: 'badge-error',
            ERROR: 'badge-error',
            CANCELLED: 'badge-ghost'
        };
        return map[normalized] || 'badge-ghost';
    };

    const TONE_MAP = {
        'badge-info': 'info', 'badge-warning': 'wa', 'badge-error': 'er',
        'badge-secondary': 'seed', 'badge-success': 'ok'
    };

    const getStatusMeta = (status) => {
        const normalized = (status || '').toUpperCase();
        return {
            status: normalized,
            meta: STATUS_META[normalized] || {
                label: normalized || 'Unknown',
                icon: 'fa-circle-notch',
                description: 'Status update pending.'
            }
        };
    };

    const getStatusOrder = (status) => {
        const normalized = (status || '').toUpperCase();
        const index = STATUS_ORDER.indexOf(normalized);
        return index === -1 ? STATUS_ORDER.length : index;
    };

    const sortQueueItems = (items) => {
        if (!Array.isArray(items)) {
            return [];
        }
        return [...items].sort((a, b) => {
            const statusDiff = getStatusOrder(a.status) - getStatusOrder(b.status);
            if (statusDiff !== 0) {
                return statusDiff;
            }
            const priorityDiff = (toNumber(b.priority) || 0) - (toNumber(a.priority) || 0);
            if (priorityDiff !== 0) {
                return priorityDiff;
            }
            const dateA = parseDate(a.started_at || a.queued_at || a.created_at);
            const dateB = parseDate(b.started_at || b.queued_at || b.created_at);
            const timeA = dateA ? dateA.getTime() : 0;
            const timeB = dateB ? dateB.getTime() : 0;
            return timeA - timeB;
        });
    };

    const formatDownloadType = (value) => {
        if (!value) {
            return '';
        }
        const normalized = String(value).toLowerCase();
        if (normalized === 'audible') {
            return 'Audible';
        }
        if (normalized === 'torrent') {
            return 'Torrent';
        }
        if (normalized === 'nzb') {
            return 'NZB';
        }
        return value;
    };

    const getQueueActions = (status) => {
        const normalized = (status || '').toUpperCase();
        return ACTIONS_BY_STATUS[normalized] || [];
    };

    const setButtonLoading = (button, isLoading) => {
        if (!button) {
            return;
        }
        if (isLoading) {
            button.classList.add('loading');
            button.setAttribute('disabled', 'disabled');
        } else {
            button.classList.remove('loading');
            button.removeAttribute('disabled');
        }
    };

    const fetchJson = async (url, options = {}) => {
        const response = await fetch(url, options);
        const text = await response.text();
        let payload = null;
        try {
            payload = text ? JSON.parse(text) : {};
        } catch (error) {
            throw new Error('Failed to parse server response');
        }
        if (!response.ok || (payload && payload.success === false)) {
            const message = (payload && (payload.error || payload.message)) || response.statusText || 'Request failed';
            throw new Error(message);
        }
        return payload;
    };

    const notify = (message, type = 'info') => {
        if (typeof window.showNotification === 'function') {
            window.showNotification(message, type);
        } else {
        }
    };

    const renderQueue = () => {
        const items = Array.isArray(state.queue) ? [...state.queue] : [];
        const downloads = [];
        const seeding = [];

        for (const item of items) {
            if (isSeedingStatus(item.status)) {
                seeding.push(item);
            } else {
                downloads.push(item);
            }
        }

        renderDownloadCards(downloads);
        renderSeedingCards(seeding);
        updateTabsVisibility(downloads.length, seeding.length);
    };

    const buildDownloadCard = (item) => {
        applyLiveProgress(item);
        const downloadId = escapeHtml(item.id ?? item.ID ?? '');
        const title = escapeHtml(item.book_title || item.title || 'Untitled');
        const author = escapeHtml(item.book_author || item.author || '');
        const indexer = escapeHtml(item.indexer || item.source || '');
        const priority = item.priority !== undefined && item.priority !== null ? `Priority ${escapeHtml(item.priority)}` : '';
        const downloadType = formatDownloadType(item.download_type || item.source_type || item.download_client);
        const queuedAt = item.started_at || item.queued_at || item.created_at;
        const retryCount = toNumber(item.retry_count);
        const eta = formatEta(item.eta_seconds);
        const speed = formatSpeed(item.download_speed_bytes || item.download_speed);
        const { status, meta } = getStatusMeta(item.status);
        const progress = getProgressValue(item);

        const badgeClass = getStatusBadgeClass(status);
        const tone = TONE_MAP[badgeClass] || '';
        const coverUrl = escapeHtml(item.cover_url || '');
        const coverHtml = coverUrl
            ? `<div class="dl-cover"><img src="${coverUrl}" alt="" onerror="this.remove()"></div>`
            : `<div class="dl-cover"><i class="fas fa-headphones"></i></div>`;

        const chips = [];
        if (downloadType) chips.push(`<span class="chip">${escapeHtml(downloadType)}</span>`);
        if (indexer) chips.push(`<span class="chip">${escapeHtml(indexer)}</span>`);
        if (priority) chips.push(`<span class="chip">${priority}</span>`);
        if (retryCount && retryCount > 0) chips.push(`<span class="chip wa">Retries ${retryCount}</span>`);
        if (libraries.list.length > 1) {
            const lib = libraries.list.find((l) => String(l.id) === String(item.target_library_id));
            const name = lib ? lib.name : 'Default library';
            const locked = ['IMPORTING', 'IMPORTED', 'SEEDING', 'SEEDING_COMPLETE'].includes(status);
            chips.push(locked
                ? `<span class="chip"><i class="fas fa-book"></i> ${escapeHtml(name)}</span>`
                : `<button type="button" class="chip chip-btn" data-library-target="${downloadId}" data-library-id="${escapeHtml(item.target_library_id ?? '')}" title="Change the library this book is imported into"><i class="fas fa-book"></i> ${escapeHtml(name)}</button>`);
        }

        const actions = getQueueActions(status).map((action) => {
            const actionIcons = {
                pause: 'fa-pause',
                resume: 'fa-play',
                cancel: 'fa-xmark',
                retry: 'fa-rotate-right',
                remove: 'fa-trash'
            };
            const actionLabels = {
                pause: 'Pause',
                resume: 'Resume',
                cancel: 'Cancel',
                retry: 'Retry',
                remove: 'Remove'
            };
            return `
                <button type="button" class="btn btn-ghost btn-xs px-2" data-action="${action}" data-id="${downloadId}">
                    <i class="fas ${actionIcons[action] || 'fa-ellipsis'}"></i>
                    ${actionLabels[action] || action}
                </button>
            `;
        }).join('');

        const progressBlock = progress !== null ? `
            <div class="prog-wrap">
                <div class="prog-meta"><span>${progress.toFixed(1)}%</span><span>${eta || speed || '&nbsp;'}</span></div>
                <div class="aa-progress"><div class="aa-progress-fill ${tone}" style="width:${progress}%"></div></div>
            </div>
        ` : '';

        const lastMessage = escapeHtml(item.last_error || item.error_message || item.last_message || '');

        const ACTIVE_STATUSES = ['DOWNLOADING', 'CONVERTING', 'IMPORTING'];
        const liveMessage = (ACTIVE_STATUSES.includes(status) && item.progress_message)
            ? `<div class="dl-live"><span class="sp"></span> ${escapeHtml(item.progress_message)}</div>`
            : '';

        return `
            <div class="dl-card">
                ${coverHtml}
                <div class="dl-body">
                    <div class="dl-top">
                        <div style="min-width:0;">
                            <div class="dl-status">
                                <span class="dl-badge ${tone}"><i class="fas ${meta.icon}"></i> ${escapeHtml(meta.label)}</span>
                                <span class="truncate">${escapeHtml(meta.description)}</span>
                            </div>
                            <h3 class="dl-title">${title}</h3>
                            ${author ? `<p class="dl-auth">${author}</p>` : ''}
                        </div>
                        <div style="text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:7px;flex:none;">
                            ${downloadId ? `<span class="dl-id">#${downloadId}</span>` : ''}
                            ${actions ? `<div class="dl-actions">${actions}</div>` : ''}
                        </div>
                    </div>
                    ${chips.length ? `<div class="chips">${chips.join('')}</div>` : ''}
                    ${liveMessage}
                    ${lastMessage ? `<div class="dl-err"><i class="fas fa-circle-exclamation"></i> ${lastMessage}</div>` : ''}
                    ${progressBlock}
                </div>
            </div>
        `;
    };

    const buildSeedingCard = (item) => {
    const rawDownloadId = item.id ?? item.ID ?? '';
    const downloadId = escapeHtml(rawDownloadId);
        const title = escapeHtml(item.book_title || item.title || 'Untitled');
        const author = escapeHtml(item.book_author || item.author || '');
        const coverUrl = escapeHtml(item.cover_url || '');
        const coverHtml = coverUrl
            ? `<div class="dl-cover"><img src="${coverUrl}" alt="" onerror="this.remove()"></div>`
            : `<div class="dl-cover"><i class="fas fa-headphones"></i></div>`;
        const ratio = toNumber(item.seeding_ratio);
        const elapsed = Math.max(0, toNumber(item.seeding_time_seconds) || 0);
        const goalSeconds = Math.max(getSeedingGoalSeconds(item), elapsed || DEFAULT_SEEDING_GOAL_SECONDS);
        const remaining = Math.max(goalSeconds - elapsed, 0);
        const percentElapsed = goalSeconds > 0 ? Math.min(100, (elapsed / goalSeconds) * 100) : 0;
        const startedAt = item.completed_at || item.started_at || item.queued_at;
        const status = (item.status || 'SEEDING').toUpperCase();
        const actions = getQueueActions(status).map((action) => {
            const icons = { cancel: 'fa-xmark', remove: 'fa-trash' };
            const labels = { cancel: 'Cancel', remove: 'Remove' };
            const icon = icons[action] || 'fa-ellipsis';
            const label = labels[action] || action;
            return `
                <button type="button" class="btn btn-ghost btn-xs px-2" data-action="${action}" data-id="${downloadId}">
                    <i class="fas ${icon}"></i>
                    ${label}
                </button>
            `;
        }).join('');

        const ratioText = Number.isFinite(ratio) && ratio >= 0 ? `${ratio.toFixed(2)}x` : 'Tracking…';
    const remainingText = `${formatDuration(remaining)} remaining`;
        const goalText = `Goal ${formatDuration(goalSeconds)}`;
    const countdownAttrId = escapeHtml(String(rawDownloadId || `timer-${Math.random().toString(36).slice(2)}`));
    const countdownAttrs = `data-seeding-timer data-download-id="${countdownAttrId}" data-remaining="${Math.round(remaining)}"`;

        return `
            <div class="dl-card">
                ${coverHtml}
                <div class="dl-body">
                    <div class="dl-top">
                        <div style="min-width:0;">
                            <div class="dl-status">
                                <span class="dl-badge seed"><i class="fas fa-seedling"></i> Seeding</span>
                                <span class="truncate" ${countdownAttrs}>${remainingText}</span>
                            </div>
                            <h3 class="dl-title">${title}</h3>
                            ${author ? `<p class="dl-auth">${author}</p>` : ''}
                        </div>
                        <div style="text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:7px;flex:none;">
                            ${downloadId ? `<span class="dl-id">#${downloadId}</span>` : ''}
                            ${actions ? `<div class="dl-actions">${actions}</div>` : ''}
                        </div>
                    </div>
                    <div class="chips">
                        <span class="chip">Ratio ${ratioText}</span>
                        <span class="chip">Elapsed ${formatDuration(elapsed)}</span>
                        <span class="chip">${goalText}</span>
                    </div>
                    <div class="prog-wrap"><div class="aa-progress"><div class="aa-progress-fill seed" style="width:${percentElapsed}%"></div></div></div>
                </div>
            </div>
        `;
    };

    const renderDownloadCards = (items) => {
        const container = elements.pipelineContainer;
        if (!container) {
            syncSeedingCountdowns(true);
            return;
        }
        if (!items.length) {
            container.innerHTML = '';
            if (elements.pipelineEmpty) {
                elements.pipelineEmpty.classList.remove('hidden');
            }
            return;
        }
        if (elements.pipelineEmpty) {
            elements.pipelineEmpty.classList.add('hidden');
        }
        const sorted = sortQueueItems(items);
        container.innerHTML = sorted.map((item) => buildDownloadCard(item)).join('');
    };

    const renderSeedingCards = (items) => {
        const container = elements.seedingContainer;
        if (!container) {
            return;
        }
        if (!items.length) {
            container.innerHTML = '';
            if (elements.seedingEmpty) {
                elements.seedingEmpty.classList.remove('hidden');
            }
            syncSeedingCountdowns(true);
            return;
        }
        if (elements.seedingEmpty) {
            elements.seedingEmpty.classList.add('hidden');
        }
        const sorted = sortQueueItems(items);
        container.innerHTML = sorted.map((item) => buildSeedingCard(item)).join('');
        syncSeedingCountdowns();
    };

    const tickSeedingCountdowns = () => {
        seedingCountdowns.forEach((entry, key) => {
            entry.remaining = Math.max(0, entry.remaining - 1);
            entry.node.textContent = `${formatDuration(entry.remaining)} remaining`;
            entry.node.setAttribute('data-remaining', entry.remaining);
            if (entry.remaining === 0) {
                seedingCountdowns.delete(key);
            }
        });
        if (seedingCountdowns.size === 0 && seedingCountdownTimer) {
            clearInterval(seedingCountdownTimer);
            seedingCountdownTimer = null;
        }
    };

    const syncSeedingCountdowns = (clearOnly = false) => {
        if (!elements.seedingContainer) {
            return;
        }
        if (clearOnly) {
            seedingCountdowns.clear();
            if (seedingCountdownTimer) {
                clearInterval(seedingCountdownTimer);
                seedingCountdownTimer = null;
            }
            return;
        }
        seedingCountdowns.clear();
        const timerNodes = elements.seedingContainer.querySelectorAll('[data-seeding-timer]');
        timerNodes.forEach((node) => {
            const key = node.getAttribute('data-download-id') || node.getAttribute('data-countdown-key') || `timer-${Math.random().toString(36).slice(2)}`;
            node.setAttribute('data-countdown-key', key);
            const remaining = Math.max(0, toNumber(node.getAttribute('data-remaining')) || 0);
            seedingCountdowns.set(key, { node, remaining });
        });
        if (seedingCountdowns.size > 0 && !seedingCountdownTimer) {
            seedingCountdownTimer = setInterval(tickSeedingCountdowns, 1000);
        }
        if (seedingCountdowns.size === 0 && seedingCountdownTimer) {
            clearInterval(seedingCountdownTimer);
            seedingCountdownTimer = null;
        }
    };

    const syncTabState = (hasSeeding) => {
        if (elements.tabDownloads) {
            const shouldHighlightDownloads = state.activeTab === 'downloads' || !hasSeeding;
            elements.tabDownloads.classList.toggle('tab-active', shouldHighlightDownloads);
            elements.tabDownloads.setAttribute('aria-selected', String(shouldHighlightDownloads));
        }
        if (elements.tabSeeding) {
            const seedingActive = state.activeTab === 'seeding' && hasSeeding;
            elements.tabSeeding.classList.toggle('tab-active', seedingActive);
            elements.tabSeeding.setAttribute('aria-selected', String(seedingActive));
        }
        if (elements.downloadsPanel) {
            elements.downloadsPanel.classList.toggle('hidden', state.activeTab !== 'downloads');
        }
        if (elements.seedingPanel) {
            const showSeeding = hasSeeding && state.activeTab === 'seeding';
            elements.seedingPanel.classList.toggle('hidden', !showSeeding);
        }
    };

    const updateTabsVisibility = (downloadsCount, seedingCount) => {
        const hasSeeding = seedingCount > 0;
        if (elements.tabsWrapper) {
            elements.tabsWrapper.classList.toggle('hidden', !hasSeeding);
        }
        if (elements.tabSeeding) {
            elements.tabSeeding.classList.toggle('hidden', !hasSeeding);
        }
        if (!hasSeeding && state.activeTab === 'seeding') {
            state.activeTab = 'downloads';
        }
        syncTabState(hasSeeding);
    };

    const setActiveTab = (tabName) => {
        const hasSeeding = Array.isArray(state.queue) && state.queue.some((item) => isSeedingStatus(item.status));
        if (tabName === 'seeding' && !hasSeeding) {
            state.activeTab = 'downloads';
        } else {
            state.activeTab = tabName === 'seeding' ? 'seeding' : 'downloads';
        }
        syncTabState(hasSeeding);
    };

    const removeQueueItem = (downloadId) => {
        if (downloadId === undefined || downloadId === null) {
            return false;
        }
        const targetId = String(downloadId);
        const currentQueue = Array.isArray(state.queue) ? state.queue : [];
        const nextQueue = currentQueue.filter((item) => String(item.id ?? item.download_id) !== targetId);
        const wasRemoved = nextQueue.length !== currentQueue.length;
        if (wasRemoved) {
            state.queue = nextQueue;
            renderQueue();
            updateStats();
        }
        return wasRemoved;
    };

    const renderHistory = () => {
        const container = elements.historyContainer;
        if (!container) {
            return;
        }
        const items = Array.isArray(state.history) ? [...state.history] : [];
        if (!items.length) {
            container.innerHTML = '';
            if (elements.historyEmpty) {
                elements.historyEmpty.classList.remove('hidden');
            }
            return;
        }
        if (elements.historyEmpty) {
            elements.historyEmpty.classList.add('hidden');
        }
        items.sort((a, b) => {
            const dateA = parseDate(a.updated_at || a.completed_at || a.created_at);
            const dateB = parseDate(b.updated_at || b.completed_at || b.created_at);
            const timeA = dateA ? dateA.getTime() : 0;
            const timeB = dateB ? dateB.getTime() : 0;
            return timeB - timeA;
        });
        const html = items.slice(0, 6).map((item) => {
            const title = escapeHtml(item.book_title || item.title || 'Imported Item');
            const authorText = formatContributors(item.book_author || item.author || '');
            const author = escapeHtml(authorText);
            const finishedAt = item.updated_at || item.completed_at || item.created_at;
            const relative = formatRelativeTime(finishedAt);
            const absolute = formatDateTime(finishedAt);
            const downloadType = formatDownloadType(item.download_type);
            const coverUrl = escapeHtml(item.cover_url || '');
            const cover = coverUrl
                ? `<div class="hc"><img src="${coverUrl}" alt="" onerror="this.remove()"></div>`
                : `<div class="hc"><i class="fas fa-headphones"></i></div>`;
            const sub = `${author ? author + ' · ' : ''}${relative}${downloadType ? ' · ' + escapeHtml(downloadType) : ''}`;
            return `
                <div class="hist" title="${escapeHtml(absolute)}">
                    ${cover}
                    <div style="min-width:0;flex:1;">
                        <div class="hn">${title}</div>
                        <div class="hm">${sub}</div>
                    </div>
                    <i class="fas fa-circle-check" style="color:var(--su);font-size:12px;"></i>
                </div>`;
        }).join('');
        container.innerHTML = html;
    };

    const updateStats = () => {
        const statistics = state.status && state.status.queue_statistics ? state.status.queue_statistics : {};
        if (elements.statActive) {
            elements.statActive.textContent = statistics.total_active ?? 0;
        }
        if (elements.statDownloading) {
            elements.statDownloading.textContent = statistics.DOWNLOADING ?? statistics.downloading ?? 0;
        }
        if (elements.statConverting) {
            elements.statConverting.textContent = statistics.CONVERTING ?? statistics.converting ?? 0;
        }
        if (elements.statSeeding) {
            const queueSeedingCount = Array.isArray(state.queue)
                ? state.queue.filter((item) => (item.status || '').toUpperCase() === 'SEEDING').length
                : 0;
            const seedingValue = statistics.SEEDING ?? statistics.seeding ?? queueSeedingCount;
            elements.statSeeding.textContent = seedingValue;
        }
        if (elements.badgeMonitorState) {
            const running = Boolean(state.status && state.status.monitor_running);
            elements.badgeMonitorState.textContent = `Monitor ${running ? 'active' : 'stopped'}`;
            elements.badgeMonitorState.classList.toggle('badge-success', running);
            elements.badgeMonitorState.classList.toggle('badge-outline', !running);
        }
    };

    const updatePollingInterval = () => {
        const serviceSeconds = toNumber(state.status && state.status.polling_interval);
        let desiredSeconds = serviceSeconds && serviceSeconds > 0 ? serviceSeconds : 6;
        desiredSeconds = Math.min(Math.max(desiredSeconds, MIN_POLL_SECONDS), MAX_POLL_SECONDS);
        if (elements.statPolling) {
            elements.statPolling.textContent = desiredSeconds;
        }
        if (lastPollSeconds === desiredSeconds) {
            return;
        }
        lastPollSeconds = desiredSeconds;
        if (queuePollTimer) {
            clearInterval(queuePollTimer);
        }
        queuePollTimer = setInterval(() => {
            refreshQueue({ silent: true });
        }, desiredSeconds * 1000);
        if (!historyPollTimer) {
            historyPollTimer = setInterval(() => {
                refreshHistory({ silent: true });
            }, Math.max(desiredSeconds * 1000 * 4, 60000));
        }
    };

    const refreshQueue = async ({ silent = false } = {}) => {
        try {
            if (!silent && elements.refreshPipelineBtn) {
                setButtonLoading(elements.refreshPipelineBtn, true);
            }
            const [queueResponse, statusResponse] = await Promise.all([
                fetchJson('/api/downloads/queue?limit=50'),
                fetchJson('/api/downloads/status')
            ]);
            state.queue = queueResponse.downloads || [];
            state.status = statusResponse.status || {};
            const activeIds = new Set(state.queue.map((i) => Number(i.id)));
            for (const id of Array.from(liveProgress.keys())) {
                if (!activeIds.has(id)) {
                    liveProgress.delete(id);
                }
            }
            renderQueue();
            updateStats();
            updatePollingInterval();
        } catch (error) {
            if (!silent) {
                notify(error.message || 'Unable to refresh queue', 'error');
            }
        } finally {
            if (!silent && elements.refreshPipelineBtn) {
                setButtonLoading(elements.refreshPipelineBtn, false);
            }
        }
    };

    const refreshHistory = async ({ silent = false } = {}) => {
        try {
            if (!silent && elements.refreshHistoryBtn) {
                setButtonLoading(elements.refreshHistoryBtn, true);
            }
            const response = await fetchJson('/api/downloads/history?limit=10');
            state.history = response.downloads || [];
            renderHistory();
        } catch (error) {
            if (!silent) {
                notify(error.message || 'Unable to refresh completed downloads', 'error');
            }
        } finally {
            if (!silent && elements.refreshHistoryBtn) {
                setButtonLoading(elements.refreshHistoryBtn, false);
            }
        }
    };

    const handleQueueAction = async (action, downloadId, button) => {
        if (!downloadId) {
            return;
        }
        if (action === 'cancel') {
            const confirmed = await AA.showConfirmDialog(
                'Cancel this download and remove it from the queue?',
                'Cancel Download',
                'Cancel Download',
                'btn-error'
            );
            if (!confirmed) {
                return;
            }
        } else if (action === 'remove') {
            const confirmed = await AA.showConfirmDialog(
                'Remove this download from the queue? This cannot be undone.',
                'Remove Download',
                'Remove',
                'btn-error'
            );
            if (!confirmed) {
                return;
            }
        }
        try {
            setButtonLoading(button, true);
            let url = `/api/downloads/queue/${downloadId}`;
            let method = 'POST';
            if (action === 'cancel') {
                method = 'DELETE';
            } else if (action === 'remove') {
                url += '/remove';
                method = 'DELETE';
            } else if (action === 'pause') {
                url += '/pause';
            } else if (action === 'resume') {
                url += '/resume';
            } else {
                url += `/${action}`;
            }

            const options = { method };
            if (method === 'POST') {
                options.headers = { 'Content-Type': 'application/json' };
            }

            const response = await fetchJson(url, options);
            if (response && response.message) {
                notify(response.message, 'success');
            }
            if (action === 'cancel' || action === 'remove') {
                removeQueueItem(downloadId);
            }
            await refreshQueue({ silent: true });
        } catch (error) {
            notify(error.message || `Unable to ${action} download`, 'error');
        } finally {
            setButtonLoading(button, false);
        }
    };

    if (elements.refreshPipelineBtn) {
        elements.refreshPipelineBtn.addEventListener('click', () => refreshQueue({ silent: false }));
    }
    if (elements.tabDownloads) {
        elements.tabDownloads.addEventListener('click', () => setActiveTab('downloads'));
    }
    if (elements.tabSeeding) {
        elements.tabSeeding.addEventListener('click', () => setActiveTab('seeding'));
    }
    if (elements.refreshHistoryBtn) {
        elements.refreshHistoryBtn.addEventListener('click', () => refreshHistory({ silent: false }));
    }
    if (elements.clearQueueBtn) {
        elements.clearQueueBtn.addEventListener('click', async () => {
            const confirmed = await AA.showConfirmDialog(
                'Clear the download queue? Active downloads will be cancelled and removed.',
                'Clear Queue',
                'Clear',
                'btn-error'
            );
            if (!confirmed) {
                return;
            }
            try {
                setButtonLoading(elements.clearQueueBtn, true);
                const response = await fetchJson('/api/downloads/queue/clear', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ include_active: true })
                });
                if (response && response.message) {
                    notify(response.message, 'success');
                } else {
                    notify('Download queue cleared', 'success');
                }
                await refreshQueue({ silent: true });
                await refreshHistory({ silent: true });
            } catch (error) {
                notify(error.message || 'Unable to clear queue', 'error');
            } finally {
                setButtonLoading(elements.clearQueueBtn, false);
            }
        });
    }

    if (elements.downloadsRoot) {
        elements.downloadsRoot.addEventListener('click', (event) => {
            const button = event.target.closest('[data-action]');
            if (!button) {
                return;
            }
            if (!elements.downloadsRoot.contains(button)) {
                return;
            }
            const action = button.getAttribute('data-action');
            const downloadId = button.getAttribute('data-id');
            handleQueueAction(action, downloadId, button);
        });
    }


    const socket = window._appSocket || null;
    if (socket) {
        socket.on('download:progress', (event) => {
            if (!event || event.download_id === undefined) {
                return;
            }
            const downloadId = Number(event.download_id);
            const prev = liveProgress.get(downloadId) || {};
            liveProgress.set(downloadId, {
                progress: event.progress !== undefined ? event.progress : prev.progress,
                eta_seconds: event.eta_seconds !== undefined ? event.eta_seconds : prev.eta_seconds,
                speed_bytes: event.speed_bytes !== undefined ? event.speed_bytes : prev.speed_bytes,
                message: event.message || prev.message,
            });
            const existing = state.queue.find((item) => Number(item.id) === downloadId);
            if (existing) {
                applyLiveProgress(existing);
                renderQueue();
                updateStats();
            }
        });

        socket.on('download:state_changed', () => {
            refreshQueue({ silent: true });
        });

        socket.on('download:completed', () => {
            refreshQueue({ silent: true });
            refreshHistory({ silent: true });
        });

        socket.on('queue:updated', () => {
            refreshQueue({ silent: true });
        });
    }

    renderQueue();
    renderHistory();
    updateStats();
    updatePollingInterval();
})();
