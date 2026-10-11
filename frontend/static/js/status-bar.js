// --- Status Activity Panel - sidebar event list -----------------------------

(function () {
    'use strict';

    const panelEl = document.getElementById('sidebarActivity');
    const listEl   = document.getElementById('sidebarActivityList');
    const queueEl  = document.getElementById('sidebarActivityQueue');

    if (!listEl) return;

    const FEED_ENDPOINT = '/api/status/feed';
    const POLL_ACTIVE_MS  = 1000;
    const POLL_IDLE_MS    = 10000;
    const MAX_SHOWN       = 3;     // extras wait in the queue
    const TERMINAL_TTL_MS = 8000;
    // Hold each state briefly so fast transitions are still readable
    const MIN_STATE_DWELL_MS = 1200;

    const STATE_TTL = {
        seeding:          8 * 1000, // show seeding briefly then yield
        seeding_complete: 8 * 1000,
    };

    const IMMEDIATE_STATES = new Set([
        'failed', 'cancelled', 'completed',
        'download_failed', 'conversion_failed', 'import_failed', 'search_failed'
    ]);

    const ACTIVE_STATES = new Set([
        'queued', 'found',
        'running', 'downloading', 'audible_downloading',
        'importing', 'converting', 'processing', 'searching',
    ]);

    const CATEGORY_ICONS = {
        download:       'fa-download',
        conversion:     'fa-sync-alt',
        metadata:       'fa-tag',
        import:         'fa-file-import',
        search:         'fa-search',
        system:         'fa-server',
        auth:           'fa-user-shield',
        seeding:        'fa-seedling',
        cleanup:        'fa-broom',
        image_cache:    'fa-images',
    };

    const STATE_LABELS = {
        running:             'Running',
        queued:              'Queued',
        searching:           'Searching',
        found:               'Found',
        downloading:         'Downloading',
        audible_downloading: 'Downloading',
        paused:              'Paused',
        complete:            'Complete',
        converting:          'Converting',
        converted:           'Converted',
        processing:          'Processing',
        importing:           'Importing',
        imported:            'Imported',
        seeding:             'Seeding',
        seeding_complete:    'Seeded',
        completed:           'Done',
        failed:              'Failed',
        cancelled:           'Cancelled',
        warning:             'Warning',
        download_failed:     'Failed',
        conversion_failed:   'Conv. Failed',
        import_failed:       'Import Failed',
        search_failed:       'Search Failed',
    };

    const eventCache    = new Map();
    const expiryTimers  = new Map();
    const terminalEntry = new Map();
    const transitionTimers = new Map();
    const displayState = new Map();

    const SUPPRESS_KEY = 'aa_suppressed_events';
    function suppKey(e) { return e.id + ':' + (e.created_at || ''); }
    function loadSuppressed() {
        try {
            var raw = sessionStorage.getItem(SUPPRESS_KEY);
            return raw ? new Set(JSON.parse(raw)) : new Set();
        } catch (_) {
            try { sessionStorage.removeItem(SUPPRESS_KEY); } catch (_) {}
            return new Set();
        }
    }
    function saveSuppressed() {
        try { sessionStorage.setItem(SUPPRESS_KEY, JSON.stringify(Array.from(suppressed))); } catch (_) {}
    }
    const suppressed = loadSuppressed();

    var   allEvents     = [];
    var   currentEvents = [];
    var   pollTimer     = null;

    function parseTs(value) {
        var t = Date.parse(value || '');
        return Number.isFinite(t) ? t : 0;
    }

    function scheduleTransitionRefresh(eventId, delayMs) {
        if (transitionTimers.has(eventId)) return;
        var handle = setTimeout(function() {
            transitionTimers.delete(eventId);
            rebuild();
            render();
        }, Math.max(20, delayMs));
        transitionTimers.set(eventId, handle);
    }

    function applyStateDwell(rawEvent) {
        if (!rawEvent || typeof rawEvent.id === 'undefined') return rawEvent;

        var eventId = rawEvent.id;
        var state = String(rawEvent.state || '').toLowerCase();
        var now = Date.now();
        var entry = displayState.get(eventId);

        if (!entry) {
            displayState.set(eventId, { state: state, since: now, event: rawEvent });
            return rawEvent;
        }

        if (state === entry.state) {
            entry.event = rawEvent;
            return rawEvent;
        }

        if (ACTIVE_STATES.has(state) && !ACTIVE_STATES.has(entry.state)) {
            entry.state = state;
            entry.since = now;
            entry.event = rawEvent;
            return rawEvent;
        }

        if (IMMEDIATE_STATES.has(state)) {
            entry.state = state;
            entry.since = now;
            entry.event = rawEvent;
            return rawEvent;
        }

        var elapsed = now - entry.since;
        if (elapsed >= MIN_STATE_DWELL_MS) {
            entry.state = state;
            entry.since = now;
            entry.event = rawEvent;
            return rawEvent;
        }

        scheduleTransitionRefresh(eventId, MIN_STATE_DWELL_MS - elapsed);
        return entry.event;
    }

    function isStaleUpdate(existing, incoming) {
        if (!existing) return false;
        var existingTs = parseTs(existing.updated_at || existing.created_at);
        var incomingTs = parseTs(incoming.updated_at || incoming.created_at);
        if (!existingTs || !incomingTs) return false;
        return incomingTs < existingTs;
    }

    (function attachSocket() {
        var sock = window._appSocket;
        if (!sock) return;
        sock.on('status:updated', onStatusEvent);
        sock.on('connect', function() {
            schedulePoll(0);
        });
        sock.on('disconnect', function(reason) {
            schedulePoll(200);
        });
    })();

    function onStatusEvent(evtData) {
        if (!evtData || typeof evtData.id === 'undefined') {
            schedulePoll(100);
            return;
        }
        var existing = eventCache.get(evtData.id);
        if (isStaleUpdate(existing, evtData)) {
            return;
        }
        var isNew = !eventCache.has(evtData.id);
        eventCache.set(evtData.id, evtData);
        rebuild();
        render();
        if (isNew) schedulePoll(400);
    }

    function schedulePoll(delay) {
        if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
        pollTimer = setTimeout(runPoll, delay);
    }

    async function runPoll() {
        pollTimer = null;
        try {
            var url = FEED_ENDPOINT + (FEED_ENDPOINT.indexOf('?') === -1 ? '?' : '&') + '_ts=' + Date.now();
            var res = await fetch(url, { cache: 'no-store' });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            var data = await res.json();
            if (data.success && Array.isArray(data.events)) {
                var pollIds = new Set(data.events.map(function(e) { return e.id; }));
                eventCache.forEach(function(_, id) {
                    if (!pollIds.has(id)) { eventCache.delete(id); }
                });
                data.events.forEach(function(e) {
                    var existing = eventCache.get(e.id);
                    if (!isStaleUpdate(existing, e)) {
                        eventCache.set(e.id, e);
                    }
                });
                rebuild();
                render();
            }
        } catch (_) {
        } finally {
            var hasActive = currentEvents.some(function(e) {
                return ACTIVE_STATES.has((e.state || '').toLowerCase());
            });
            schedulePoll(hasActive ? POLL_ACTIVE_MS : POLL_IDLE_MS);
        }
    }

    function rebuild() {
        var now = Date.now();
        var visible = [];

        eventCache.forEach(function(e) {
            var displayEvent = applyStateDwell(e);
            if (!displayEvent) return;

            var state = (displayEvent.state || '').toLowerCase();
            if (ACTIVE_STATES.has(state)) {
                if (suppressed.has(suppKey(e))) { suppressed.delete(suppKey(e)); saveSuppressed(); }
                terminalEntry.delete(e.id);
                if (expiryTimers.has(e.id)) {
                    clearTimeout(expiryTimers.get(e.id));
                    expiryTimers.delete(e.id);
                }
                visible.push(displayEvent);
            } else {
                if (suppressed.has(suppKey(e))) return;
                visible.push(displayEvent);
            }
        });

        visible.sort(function(a, b) {
            var aA = ACTIVE_STATES.has((a.state || '').toLowerCase()) ? 1 : 0;
            var bA = ACTIVE_STATES.has((b.state || '').toLowerCase()) ? 1 : 0;
            if (aA !== bA) return bA - aA;
            return new Date(b.updated_at || 0) - new Date(a.updated_at || 0);
        });

        allEvents     = visible;
        currentEvents = visible.slice(0, MAX_SHOWN);

        currentEvents.forEach(function(e) {
            var state = (e.state || '').toLowerCase();
            if (ACTIVE_STATES.has(state)) return;
            var id  = e.id;
            var key = suppKey(e);
            if (expiryTimers.has(id)) return;
            if (!terminalEntry.has(id)) terminalEntry.set(id, Date.now());
            var ttl = STATE_TTL[state] !== undefined ? STATE_TTL[state] : TERMINAL_TTL_MS;
            var remaining = ttl - (Date.now() - terminalEntry.get(id));
            if (remaining > 0) {
                expiryTimers.set(id, setTimeout(function() {
                    expiryTimers.delete(id);
                    terminalEntry.delete(id);
                    suppressed.add(key);
                    saveSuppressed();
                    rebuild();
                    render();
                    schedulePoll(200);
                }, remaining));
            } else {
                suppressed.add(key);
                saveSuppressed();
                schedulePoll(200);
            }
        });

        expiryTimers.forEach(function(handle, id) {
            if (!eventCache.has(id)) {
                clearTimeout(handle);
                expiryTimers.delete(id);
                terminalEntry.delete(id);
            }
        });

        transitionTimers.forEach(function(handle, id) {
            if (!eventCache.has(id)) {
                clearTimeout(handle);
                transitionTimers.delete(id);
                displayState.delete(id);
            }
        });
    }

    function render() {
        if (!currentEvents.length) {
            panelEl.style.display = 'none';
            return;
        }
        panelEl.style.display = '';

        var waiting = allEvents.length - currentEvents.length;
        if (queueEl) {
            if (waiting > 0) {
                queueEl.textContent = '+' + waiting + ' waiting';
                queueEl.style.display = '';
            } else {
                queueEl.style.display = 'none';
            }
        }

        var existingIds = Array.from(listEl.children).map(function(n) { return n.dataset.evtId; });
        var newIds      = currentEvents.map(function(e) { return String(e.id); });

        Array.from(listEl.children).forEach(function(n) {
            if (newIds.indexOf(n.dataset.evtId) === -1) listEl.removeChild(n);
        });

        currentEvents.forEach(function(evt, i) {
            var id  = String(evt.id);
            var existing = listEl.querySelector('[data-evt-id="' + id + '"]');
            var node = buildItem(evt);
            if (existing) {
                listEl.replaceChild(node, existing);
            } else {
                var refNode = listEl.children[i] || null;
                listEl.insertBefore(node, refNode);
            }
        });
    }

    function buildItem(evt) {
        var cat      = (evt.category || '').toLowerCase();
        var state    = (evt.state    || 'running').toLowerCase();
        var isActive = ACTIVE_STATES.has(state);
        var icon     = CATEGORY_ICONS[cat] || stateIcon(state);
        var label    = STATE_LABELS[state]  || toTitleCase(state);
        var stateKey = state.replace(/_/g, '-');

        var isDownloading = state === 'downloading' || state === 'audible_downloading';
        var showsProgress = isDownloading || state === 'converting' || state === 'importing';
        var hasProgress = showsProgress && typeof evt.progress === 'number';
        var pct = hasProgress ? Math.min(100, Math.max(0, evt.progress)) : 0;

        var iconHtml;
        if (isDownloading && isActive) {
            iconHtml = '<span class="loading loading-infinity activity-icon state-' + stateKey + '"></span>';
        } else {
            iconHtml = '<i class="fas ' + escHtml(icon) + ' activity-icon state-' + stateKey + (isActive ? ' activity-icon--spin' : '') + '"></i>';
        }

        var li = document.createElement('li');
        li.className = 'sidebar-activity-item';
        li.dataset.evtId = String(evt.id);
        li.innerHTML =
            '<div class="activity-row">' +
                iconHtml +
                '<span class="activity-title">' + escHtml(evt.title || 'Task') + '</span>' +
            '</div>' +
            '<div class="activity-meta">' +
                '<span class="activity-badge state-' + stateKey + '">' + escHtml(label) + '</span>' +
                (hasProgress ? '<span class="activity-pct">' + Math.round(pct) + '%</span>' : '') +
            '</div>' +
            (hasProgress
                ? '<div class="activity-progress-track"><div class="activity-progress-fill state-' + stateKey + '" style="width:' + pct + '%"></div></div>'
                : '');
        return li;
    }

    function escHtml(str) {
        return String(str).replace(/[&<>"']/g, function(c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
        });
    }

    function stateIcon(state) {
        var MAP = {
            queued:              'fa-clock',
            searching:           'fa-search',
            found:               'fa-check',
            downloading:         'fa-download',
            audible_downloading: 'fa-headphones',
            paused:              'fa-pause-circle',
            complete:            'fa-check-circle',
            converting:          'fa-sync-alt',
            converted:           'fa-check-circle',
            processing:          'fa-cog',
            importing:           'fa-file-import',
            imported:            'fa-check-double',
            seeding:             'fa-seedling',
            seeding_complete:    'fa-leaf',
            completed:           'fa-check-circle',
            warning:             'fa-exclamation-triangle',
            search_failed:       'fa-search-minus',
            download_failed:     'fa-times-circle',
            conversion_failed:   'fa-exclamation-circle',
            import_failed:       'fa-folder-minus',
            failed:              'fa-times-circle',
            cancelled:           'fa-ban',
        };
        return MAP[state] || 'fa-circle-notch';
    }

    function toTitleCase(str) {
        return str.replace(/[_-]/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
    }

    schedulePoll(0);
})();
