(function () {
    'use strict';

    const config = window.libraryImportPageData || {};

    const POLL_INTERVAL_MS = 1000;
    const ACTIVE_STATES = new Set(['scanning', 'processing', 'importing', 'cancelling']);

    const state = {
        sessionId: null,
        session: null,
        pollTimer: null,
        activeFolderId: null,
        searchResults: []
    };

    const elements = {};

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        cacheElements();
        bindEvents();
        if (new URLSearchParams(window.location.search).get('scan') === '1') {
            window.history.replaceState(null, '', window.location.pathname);
            handleStartScan();
        }
    }

    function cacheElements() {
        elements.startScanBtn = document.getElementById('startScanBtn');
        elements.scanStatus = document.getElementById('scanStatus');
        elements.scanStatusText = document.getElementById('scanStatusText');
        elements.scanError = document.getElementById('scanError');

        elements.toolbar = document.getElementById('libimportToolbar');
        elements.selectAllCheckbox = document.getElementById('selectAllCheckbox');
        elements.bulkMonitorCheckbox = document.getElementById('bulkMonitorCheckbox');
        elements.applyBulkMonitorBtn = document.getElementById('applyBulkMonitorBtn');
        elements.startProcessingBtn = document.getElementById('startProcessingBtn');
        elements.cancelProcessingBtn = document.getElementById('cancelProcessingBtn');
        elements.importSelectedBtn = document.getElementById('importSelectedBtn');
        elements.importSelectedCount = document.getElementById('importSelectedCount');
        elements.scanErrorsDropdown = document.getElementById('scanErrorsDropdown');
        elements.scanErrorsList = document.getElementById('scanErrorsList');
        elements.scanErrorCount = document.getElementById('scanErrorCount');

        elements.summaryBar = document.getElementById('libimportSummary');
        elements.summaryTotal = document.getElementById('summaryTotal');
        elements.summaryMatched = document.getElementById('summaryMatched');
        elements.summaryNeedsReview = document.getElementById('summaryNeedsReview');
        elements.summaryUnmatched = document.getElementById('summaryUnmatched');
        elements.summaryImported = document.getElementById('summaryImported');

        elements.gridEmptyState = document.getElementById('gridEmptyState');
        elements.gridTableWrap = document.getElementById('gridTableWrap');
        elements.folderTableBody = document.getElementById('folderTableBody');

        elements.matchSearchModal = document.getElementById('matchSearchModal');
        elements.matchSearchFolderName = document.getElementById('matchSearchFolderName');
        elements.matchSearchForm = document.getElementById('matchSearchForm');
        elements.matchSearchQuery = document.getElementById('matchSearchQuery');
        elements.matchSearchAuthor = document.getElementById('matchSearchAuthor');
        elements.matchSearchAsin = document.getElementById('matchSearchAsin');
        elements.matchSearchStatus = document.getElementById('matchSearchStatus');
        elements.matchSearchResults = document.getElementById('matchSearchResults');
    }

    function bindEvents() {
        elements.startScanBtn?.addEventListener('click', handleStartScan);
        elements.startProcessingBtn?.addEventListener('click', handleStartScan);
        elements.cancelProcessingBtn?.addEventListener('click', handleCancelScan);
        elements.selectAllCheckbox?.addEventListener('change', handleSelectAll);
        elements.applyBulkMonitorBtn?.addEventListener('click', handleApplyBulkMonitor);
        elements.importSelectedBtn?.addEventListener('click', handleImportSelected);
        elements.matchSearchForm?.addEventListener('submit', handleMatchSearchSubmit);
    }

    // --- Scan lifecycle ---

    async function handleStartScan() {
        if (!config.scanStartEndpoint) {
            showNotification('Library import endpoint is not configured.', 'error');
            return;
        }

        setButtonLoading(elements.startScanBtn, true);
        hideError();
        stopPolling();

        try {
            const librarySelect = document.getElementById('scanLibrary');
            const response = await fetch(config.scanStartEndpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(librarySelect ? { library_id: Number(librarySelect.value) } : {})
            });
            const payload = await response.json();

            if (response.status === 409 && payload.running_session_id) {
                state.sessionId = payload.running_session_id;
                showNotification('A scan is already running - showing its progress.', 'warning');
                await pollSession();
                return;
            }

            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Unable to start library scan');
            }

            state.sessionId = payload.session_id;
            applySession(payload);
            showNotification('Scanning library - matching folders now.', 'info');

            if (ACTIVE_STATES.has(payload.state)) {
                schedulePoll();
            }
        } catch (error) {
            showNotification(error.message || 'Failed to start scan', 'error');
        } finally {
            setButtonLoading(elements.startScanBtn, false);
        }
    }

    async function handleCancelScan() {
        if (!state.sessionId || !config.scanCancelEndpointTemplate) return;
        try {
            const url = buildUrl(config.scanCancelEndpointTemplate, { '__session__': state.sessionId });
            const response = await fetch(url, { method: 'POST' });
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Unable to cancel scan');
            }
            applySession(payload);
            showNotification('Cancelling - matching will stop shortly.', 'info');
        } catch (error) {
            showNotification(error.message || 'Failed to cancel scan', 'error');
        }
    }

    function schedulePoll() {
        stopPolling();
        state.pollTimer = setTimeout(pollSession, POLL_INTERVAL_MS);
    }

    function stopPolling() {
        if (state.pollTimer) {
            clearTimeout(state.pollTimer);
            state.pollTimer = null;
        }
    }

    async function pollSession() {
        if (!state.sessionId || !config.scanGetEndpointTemplate) return;
        try {
            const url = buildUrl(config.scanGetEndpointTemplate, { '__session__': state.sessionId });
            const response = await fetch(url);
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Unable to fetch scan status');
            }
            applySession(payload);
        } catch (error) {
            showNotification(error.message || 'Lost track of scan session', 'error');
            stopPolling();
            return;
        }

        if (state.session && ACTIVE_STATES.has(state.session.state)) {
            schedulePoll();
        }
    }

    function applySession(session) {
        state.session = session;
        if (session.error) {
            showError(session.error);
        } else {
            hideError();
        }
        renderScanStatus();
        renderSummary();
        renderToolbar();
        renderGrid();
    }

    // --- Rendering ---

    function renderScanStatus() {
        const session = state.session;
        if (!session) {
            elements.scanStatus?.classList.add('hidden');
            return;
        }
        const active = ACTIVE_STATES.has(session.state);
        elements.scanStatus?.classList.toggle('hidden', !active);
        if (active && elements.scanStatusText) {
            const remaining = (session.summary?.unsearched || 0) + (session.summary?.processing || 0);
            elements.scanStatusText.textContent = session.state === 'importing'
                ? 'Importing selected books…'
                : `Matching folders… ${remaining} remaining`;
        }
    }

    function renderSummary() {
        const summary = state.session?.summary;
        if (!summary) {
            elements.summaryBar?.classList.add('hidden');
            return;
        }
        elements.summaryBar?.classList.remove('hidden');
        elements.summaryTotal.textContent = summary.total ?? 0;
        elements.summaryMatched.textContent = summary.matched ?? 0;
        elements.summaryNeedsReview.textContent = summary.needs_review ?? 0;
        elements.summaryUnmatched.textContent = summary.unmatched ?? 0;
        elements.summaryImported.textContent = summary.imported ?? 0;
    }

    function renderToolbar() {
        const session = state.session;
        if (!session || !session.folders || session.folders.length === 0) {
            elements.toolbar?.classList.add('hidden');
            return;
        }
        elements.toolbar?.classList.remove('hidden');

        const active = ACTIVE_STATES.has(session.state);
        elements.startProcessingBtn?.classList.toggle('hidden', active || session.state !== 'cancelled');
        elements.cancelProcessingBtn?.classList.toggle('hidden', !active);

        const selectedCount = session.folders.filter((f) => f.selected).length;
        elements.importSelectedCount.textContent = selectedCount;
        elements.importSelectedBtn.disabled = selectedCount === 0 || session.state === 'importing';

        const failedMessages = [];
        session.folders.forEach((folder) => {
            if (folder.state === 'failed' || folder.state === 'unmatched') {
                (folder.messages || []).forEach((msg) => failedMessages.push(`${folder.name}: ${msg}`));
            }
        });
        if (failedMessages.length) {
            elements.scanErrorsDropdown.style.display = '';
            elements.scanErrorCount.textContent = failedMessages.length;
            elements.scanErrorsList.innerHTML = failedMessages
                .map((msg) => `<li class="py-1 border-b border-base-content/10 last:border-none">${escapeHtml(msg)}</li>`)
                .join('');
        } else {
            elements.scanErrorsDropdown.style.display = 'none';
        }
    }

    const rowElements = new Map();
    const rowSignatures = new Map();
    let renderedSessionId = null;

    function renderGrid() {
        const session = state.session;
        const folders = session?.folders || [];

        if (!folders.length) {
            elements.gridEmptyState?.classList.remove('hidden');
            elements.gridTableWrap?.classList.add('hidden');
            rowElements.clear();
            rowSignatures.clear();
            renderedSessionId = session?.session_id ?? null;
            return;
        }

        elements.gridEmptyState?.classList.add('hidden');
        elements.gridTableWrap?.classList.remove('hidden');

        if (session.session_id !== renderedSessionId || rowElements.size !== folders.length) {
            buildGridRows(folders);
            renderedSessionId = session.session_id;
        } else {
            patchGridRows(folders);
        }

        updateSelectAllState(folders);
    }

    function buildGridRows(folders) {
        elements.folderTableBody.innerHTML = folders.map(renderFolderRow).join('');
        rowElements.clear();
        rowSignatures.clear();

        elements.folderTableBody.querySelectorAll('tr[data-folder-id]').forEach((tr) => {
            rowElements.set(tr.dataset.folderId, tr);
        });
        folders.forEach((folder) => rowSignatures.set(folder.folder_id, folderSignature(folder)));
        bindRowEvents(elements.folderTableBody);
    }

    function patchGridRows(folders) {
        folders.forEach((folder) => {
            const signature = folderSignature(folder);
            if (rowSignatures.get(folder.folder_id) === signature) {
                return;
            }
            rowSignatures.set(folder.folder_id, signature);

            const tr = rowElements.get(folder.folder_id);
            if (!tr) return;
            updateRowContent(tr, folder);
            flashRow(tr);
        });
    }

    function folderSignature(folder) {
        return [
            folder.state,
            folder.selected,
            folder.monitored,
            folder.book?.title,
            folder.book?.author,
            folder.book?.cover_image,
            (folder.messages || []).length
        ].join('|');
    }

    function flashRow(tr) {
        tr.classList.remove('row-flash');
        void tr.offsetWidth;
        tr.classList.add('row-flash');
    }

    function bindRowEvents(scope) {
        scope.querySelectorAll('[data-action]').forEach((el) => {
            el.addEventListener('click', handleRowAction);
        });
        scope.querySelectorAll('[data-role="row-select"]').forEach((el) => {
            el.addEventListener('change', handleRowSelectToggle);
        });
        scope.querySelectorAll('[data-role="row-monitor"]').forEach((el) => {
            el.addEventListener('change', handleRowMonitorToggle);
        });
    }

    function updateRowContent(tr, folder) {
        const canSelect = folder.state === 'matched' || folder.state === 'needs_review';

        const matchHost = tr.querySelector('[data-role="match-cell"]');
        if (matchHost) {
            matchHost.innerHTML = renderMatchCellInner(folder);
            bindRowEvents(matchHost);
        }

        const authorCell = tr.querySelector('[data-role="author-cell"]');
        if (authorCell) authorCell.textContent = folder.book?.author || '—';

        const statusCell = tr.querySelector('[data-role="status-cell"]');
        if (statusCell) statusCell.innerHTML = renderStatusBadge(folder.state);

        const selectInput = tr.querySelector('[data-role="row-select"]');
        if (selectInput) {
            selectInput.checked = !!folder.selected;
            selectInput.disabled = !canSelect;
        }

        const monitorInput = tr.querySelector('[data-role="row-monitor"]');
        if (monitorInput) monitorInput.checked = !!folder.monitored;
    }

    function renderMatchCellInner(folder) {
        const book = folder.book;
        if (book) {
            return `<button type="button" class="match-cell-btn" data-action="reassign" data-folder-id="${folder.folder_id}">
                 ${book.cover_image
                    ? `<img src="${escapeHtml(book.cover_image)}" class="match-cell-cover" alt="" />`
                    : `<div class="match-cell-cover"></div>`}
                 <span class="match-cell-title">${escapeHtml(book.title || 'Untitled')}</span>
               </button>`;
        }
        return `<button type="button" class="match-cell-btn" data-action="reassign" data-folder-id="${folder.folder_id}">
                 <span class="match-cell-empty">No match - click to search</span>
               </button>`;
    }

    function renderFolderRow(folder) {
        const canSelect = folder.state === 'matched' || folder.state === 'needs_review';
        const checked = folder.selected ? 'checked' : '';
        const disabled = canSelect ? '' : 'disabled';

        return `
            <tr data-folder-id="${folder.folder_id}">
                <td>
                    <input type="checkbox" class="checkbox checkbox-xs" data-role="row-select"
                           data-folder-id="${folder.folder_id}" ${checked} ${disabled} />
                </td>
                <td>
                    <div class="folder-name">${escapeHtml(folder.name)}</div>
                    <div class="folder-path">${escapeHtml(folder.rel_path || folder.path)}</div>
                </td>
                <td data-role="match-cell">${renderMatchCellInner(folder)}</td>
                <td class="text-sm" data-role="author-cell">${escapeHtml(folder.book?.author || '—')}</td>
                <td>
                    <input type="checkbox" class="checkbox checkbox-xs" data-role="row-monitor"
                           data-folder-id="${folder.folder_id}" ${folder.monitored ? 'checked' : ''} />
                </td>
                <td data-role="status-cell">${renderStatusBadge(folder.state)}</td>
                <td>
                    <button type="button" class="btn btn-ghost btn-xs" data-action="reassign" data-folder-id="${folder.folder_id}"
                            title="Search / reassign match">
                        <i class="fas fa-magnifying-glass text-xs"></i>
                    </button>
                </td>
            </tr>`;
    }

    function renderStatusBadge(stateName) {
        const labels = {
            unsearched: 'Queued',
            processing: 'Matching…',
            matched: 'Matched',
            needs_review: 'Needs Review',
            unmatched: 'Unmatched',
            importing: 'Importing…',
            imported: 'Imported',
            failed: 'Failed'
        };
        const label = labels[stateName] || stateName;
        const spinner = stateName === 'processing' || stateName === 'importing'
            ? '<span class="loading loading-spinner loading-xs"></span>'
            : '';
        return `<span class="libimport-status-badge state-${stateName}">${spinner}${escapeHtml(label)}</span>`;
    }

    // --- Row interactions ---

    function handleRowAction(event) {
        const target = event.currentTarget;
        const action = target.dataset.action;
        const folderId = target.dataset.folderId;
        if (action === 'reassign') {
            openMatchSearchModal(folderId);
        }
    }

    async function handleRowSelectToggle(event) {
        const folderId = event.target.dataset.folderId;
        await updateFolderFlags(folderId, { selected: event.target.checked });
    }

    async function handleRowMonitorToggle(event) {
        const folderId = event.target.dataset.folderId;
        await updateFolderFlags(folderId, { monitored: event.target.checked });
    }

    async function updateFolderFlags(folderId, flags) {
        if (!state.sessionId || !config.folderFlagsEndpointTemplate) return;
        try {
            const url = buildUrl(config.folderFlagsEndpointTemplate, {
                '__session__': state.sessionId,
                '__folder__': folderId
            });
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(flags)
            });
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Unable to update folder');
            }
            patchFolder(payload.folder);
            renderToolbar();
        } catch (error) {
            showNotification(error.message || 'Failed to update folder', 'error');
        }
    }

    function patchFolder(updatedFolder) {
        if (!state.session || !updatedFolder) return;
        const idx = state.session.folders.findIndex((f) => f.folder_id === updatedFolder.folder_id);
        if (idx !== -1) {
            state.session.folders[idx] = updatedFolder;
            renderGrid();
            renderSummary();
        }
    }

    function handleSelectAll(event) {
        const checked = event.target.checked;
        const folders = state.session?.folders || [];
        folders
            .filter((f) => f.state === 'matched' || f.state === 'needs_review')
            .forEach((f) => updateFolderFlags(f.folder_id, { selected: checked }));
    }

    function updateSelectAllState(folders) {
        const selectable = folders.filter((f) => f.state === 'matched' || f.state === 'needs_review');
        const allSelected = selectable.length > 0 && selectable.every((f) => f.selected);
        if (elements.selectAllCheckbox) {
            elements.selectAllCheckbox.checked = allSelected;
            elements.selectAllCheckbox.disabled = selectable.length === 0;
        }
    }

    function handleApplyBulkMonitor() {
        const monitored = elements.bulkMonitorCheckbox?.checked ?? true;
        const folders = state.session?.folders || [];
        folders
            .filter((f) => f.selected)
            .forEach((f) => updateFolderFlags(f.folder_id, { monitored }));
    }

    // --- Match search modal ---

    function openMatchSearchModal(folderId) {
        const folder = state.session?.folders.find((f) => f.folder_id === folderId);
        if (!folder) return;

        state.activeFolderId = folderId;
        elements.matchSearchFolderName.textContent = `Folder: ${folder.rel_path || folder.name}`;
        elements.matchSearchQuery.value = folder.book?.title || folder.name || '';
        elements.matchSearchAuthor.value = folder.book?.author || '';
        elements.matchSearchAsin.value = folder.book?.asin || '';
        elements.matchSearchStatus.textContent = 'Enter a query to search for matches.';
        elements.matchSearchResults.innerHTML = '';
        elements.matchSearchModal?.showModal();
    }

    async function handleMatchSearchSubmit(event) {
        event.preventDefault();
        if (!config.metadataSearchEndpoint) return;

        const params = new URLSearchParams();
        if (elements.matchSearchQuery.value.trim()) params.set('query', elements.matchSearchQuery.value.trim());
        if (elements.matchSearchAuthor.value.trim()) params.set('author', elements.matchSearchAuthor.value.trim());
        if (elements.matchSearchAsin.value.trim()) params.set('asin', elements.matchSearchAsin.value.trim());

        elements.matchSearchStatus.textContent = 'Searching…';
        elements.matchSearchResults.innerHTML = '';

        try {
            const response = await fetch(`${config.metadataSearchEndpoint}?${params.toString()}`);
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Search failed');
            }
            state.searchResults = payload.results || [];
            if (!state.searchResults.length) {
                elements.matchSearchStatus.textContent = 'No matches found. Try different keywords.';
                return;
            }
            elements.matchSearchStatus.textContent = `${state.searchResults.length} result(s) found.`;
            elements.matchSearchResults.innerHTML = state.searchResults.map(renderSearchResultCard).join('');
            elements.matchSearchResults.querySelectorAll('[data-result-index]').forEach((el) => {
                el.addEventListener('click', handleApplySearchResult);
            });
        } catch (error) {
            elements.matchSearchStatus.textContent = error.message || 'Search failed';
        }
    }

    function renderSearchResultCard(result, index) {
        const metadata = result.metadata || {};
        const title = metadata.Title || metadata.title || 'Untitled';
        const author = metadata.Author || metadata.author || 'Unknown Author';
        const cover = metadata['Cover Image'] || metadata.cover_image || '';
        const confidence = result.match?.confidence != null
            ? `${Math.round(result.match.confidence * 100)}%`
            : '—';

        return `
            <button type="button" class="text-left p-3 rounded-lg border border-base-content/10 hover:border-primary flex gap-3 items-center"
                    data-result-index="${index}">
                ${cover ? `<img src="${escapeHtml(cover)}" class="w-10 h-10 rounded object-cover flex-shrink-0" alt="" />` : '<div class="w-10 h-10 rounded bg-base-300 flex-shrink-0"></div>'}
                <div class="min-w-0">
                    <div class="font-semibold text-sm truncate">${escapeHtml(title)}</div>
                    <div class="text-xs text-base-content/50 truncate">${escapeHtml(author)}</div>
                    <div class="text-xs text-base-content/40">Confidence: ${confidence} · ${escapeHtml(result.source || '')}</div>
                </div>
            </button>`;
    }

    async function handleApplySearchResult(event) {
        const index = Number(event.currentTarget.dataset.resultIndex);
        const result = state.searchResults[index];
        if (!result || !state.activeFolderId || !state.sessionId) return;

        try {
            const url = buildUrl(config.folderMatchEndpointTemplate, {
                '__session__': state.sessionId,
                '__folder__': state.activeFolderId
            });
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ metadata: result.metadata })
            });
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Unable to assign match');
            }
            patchFolder(payload.folder);
            renderToolbar();
            elements.matchSearchModal?.close();
            showNotification('Match updated.', 'success');
        } catch (error) {
            showNotification(error.message || 'Failed to assign match', 'error');
        }
    }

    // --- Commit ---

    async function handleImportSelected() {
        if (!state.sessionId || !config.scanImportEndpointTemplate) return;

        const folderIds = (state.session?.folders || [])
            .filter((f) => f.selected)
            .map((f) => f.folder_id);
        if (!folderIds.length) {
            showNotification('Select at least one matched book to import.', 'warning');
            return;
        }

        setButtonLoading(elements.importSelectedBtn, true);
        try {
            const url = buildUrl(config.scanImportEndpointTemplate, { '__session__': state.sessionId });
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ folder_ids: folderIds })
            });
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Import failed');
            }

            const { successful, failed } = payload.summary || {};
            showNotification(`Imported ${successful || 0} book(s)${failed ? `, ${failed} failed` : ''}.`,
                failed ? 'warning' : 'success');

            await pollSession();
        } catch (error) {
            showNotification(error.message || 'Import failed', 'error');
        } finally {
            setButtonLoading(elements.importSelectedBtn, false);
        }
    }

    // --- Helpers ---

    function buildUrl(template, replacements) {
        if (!template) return '';
        return Object.entries(replacements || {}).reduce((acc, [token, value]) => (
            acc.replace(token, encodeURIComponent(value))
        ), template);
    }

    function showError(message) {
        if (!elements.scanError) return;
        elements.scanError.textContent = message;
        elements.scanError.classList.remove('hidden');
    }

    function hideError() {
        elements.scanError?.classList.add('hidden');
    }

    function setButtonLoading(button, isLoading) {
        if (!button) return;
        button.disabled = isLoading;
        button.classList.toggle('opacity-60', isLoading);
    }

    function escapeHtml(value) {
        if (value == null) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
})();
