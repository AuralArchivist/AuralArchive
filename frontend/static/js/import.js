/* eslint-disable no-console */
(function () {
    const config = window.importPageData || {};
    const elements = {
        selectedFilesContainer: document.getElementById('selectedFilesContainer'),
        clearPathsBtn: document.getElementById('clearPathsBtn'),
        previewBtn: document.getElementById('previewBtn'),
        importBtn: document.getElementById('importBtn'),
        cardSelectAll: document.getElementById('cardSelectAll'),
        previewSummary: document.getElementById('previewSummary'),
        summaryFields: {
            total: document.getElementById('summaryTotal'),
            ready: document.getElementById('summaryReady'),
            pending: document.getElementById('summaryPending'),
            missing: document.getElementById('summaryMissing'),
            invalid: document.getElementById('summaryInvalid'),
            error: document.getElementById('summaryError'),
            imported: document.getElementById('summaryImported')
        },
        cardGrid: document.getElementById('cardGrid'),
        cardEmptyState: document.getElementById('cardEmptyState'),
        batchMetaInfo: document.getElementById('batchMetaInfo'),
        importResultCard: document.getElementById('importResultCard'),
        importResultsBody: document.getElementById('importResultsBody'),
        importSuccess: document.getElementById('importSuccess'),
        importFailed: document.getElementById('importFailed'),
        importTotal: document.getElementById('importTotal'),
        stagingTableBody: document.getElementById('stagingTableBody'),
        stagingStatus: document.getElementById('stagingStatus'),
        stagingEmptyState: document.getElementById('stagingEmptyState'),
        stagingRootBtn: document.getElementById('stagingRootBtn'),
        stagingRefreshBtn: document.getElementById('stagingRefreshBtn'),
        stagingBreadcrumbs: document.getElementById('stagingBreadcrumbs'),
        uploadFileInput: document.getElementById('uploadFileInput'),
        uploadFileBtn: document.getElementById('uploadFileBtn'),
        uploadStatus: document.getElementById('uploadStatus'),
        uploadStatusText: document.getElementById('uploadStatusText'),
        metadataSearchModal: document.getElementById('metadataSearchModal'),
        metadataSearchForm: document.getElementById('metadataSearchForm'),
        metadataSearchQuery: document.getElementById('metadataSearchQuery'),
        metadataSearchAuthor: document.getElementById('metadataSearchAuthor'),
        metadataSearchAsin: document.getElementById('metadataSearchAsin'),
        metadataSearchResults: document.getElementById('metadataSearchResults'),
        metadataSearchStatus: document.getElementById('metadataSearchStatus'),
        metadataSearchSubmit: document.getElementById('metadataSearchSubmit'),
        metadataApplyAsinBtn: document.getElementById('metadataApplyAsinBtn'),
        metadataSearchCardTitle: document.getElementById('metadataSearchCardTitle')
    };

    const state = {
        selectedEntries: [],
        staging: {
            root: config.importDirectory || '/',
            currentPath: '',
            entries: [],
            breadcrumbs: [],
            loading: false
        },
        batch: createBatchState(),
        metadataSearch: {
            cardId: null,
            results: []
        }
    };

    const SUMMARY_FIELDS = ['total', 'ready', 'pending', 'missing', 'invalid', 'error', 'imported'];

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        elements.clearPathsBtn?.addEventListener('click', clearSelection);
        elements.previewBtn?.addEventListener('click', handleStageBatch);
        elements.importBtn?.addEventListener('click', handleImportBatch);
        elements.cardSelectAll?.addEventListener('change', toggleSelectAllCards);
        elements.stagingTableBody?.addEventListener('click', handleStagingTableClick);
        elements.stagingRootBtn?.addEventListener('click', () => loadStagingDirectory(''));
        elements.stagingRefreshBtn?.addEventListener('click', () => loadStagingDirectory(state.staging.currentPath));
        elements.metadataSearchForm?.addEventListener('submit', handleMetadataSearchSubmit);
        elements.metadataApplyAsinBtn?.addEventListener('click', applyManualAsin);
        elements.metadataSearchModal?.addEventListener('close', resetMetadataSearchState);
        elements.uploadFileBtn?.addEventListener('click', () => elements.uploadFileInput?.click());
        elements.uploadFileInput?.addEventListener('change', handleFileUpload);

        renderSelectedFiles();
        loadStagingDirectory('');
    }

    // --- Staging directory browsing & selection ---

    function handleStagingTableClick(event) {
        const button = event.target.closest('[data-action]');
        if (!button) {
            return;
        }
        const action = button.dataset.action;
        const relativePath = button.dataset.path || '';
        const absolutePath = button.dataset.absolute || '';

        if (action === 'open') {
            loadStagingDirectory(relativePath);
            return;
        }
        if (action === 'add-file') {
            addEntryFromStaging(relativePath, absolutePath);
            return;
        }
        if (action === 'add-folder') {
            addFolderFiles(relativePath);
        }
    }

    async function loadStagingDirectory(relativePath = '') {
        if (!config.stagingListEndpoint) {
            showNotification('Server browsing is not enabled.', 'error');
            return;
        }

        state.staging.loading = true;
        toggleStagingStatus(true);

        try {
            const params = new URLSearchParams();
            if (relativePath) {
                params.set('path', relativePath);
            }
            const url = `${config.stagingListEndpoint}${params.toString() ? `?${params.toString()}` : ''}`;
            const response = await fetch(url);
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Unable to load staging directory');
            }

            state.staging.currentPath = payload.path || '';
            state.staging.entries = payload.entries || [];
            state.staging.breadcrumbs = payload.breadcrumbs || [];

            renderStagingBreadcrumbs();
            renderStagingEntries();
        } catch (error) {
            showNotification(error.message || 'Unable to load staging directory', 'error');
        } finally {
            state.staging.loading = false;
            toggleStagingStatus(false);
        }
    }

    function toggleStagingStatus(isLoading) {
        if (!elements.stagingStatus) {
            return;
        }
        if (isLoading) {
            elements.stagingStatus.classList.remove('hidden');
        } else {
            elements.stagingStatus.classList.add('hidden');
        }
    }

    function renderStagingBreadcrumbs() {
        if (!elements.stagingBreadcrumbs) {
            return;
        }
        const breadcrumbs = (state.staging.breadcrumbs || []).filter((crumb) => crumb.label !== 'Import Root');

        elements.stagingBreadcrumbs.innerHTML = '';
        breadcrumbs.forEach((crumb, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-ghost btn-xs';
            button.textContent = crumb.label;
            button.addEventListener('click', () => loadStagingDirectory(crumb.path || ''));
            if (index === breadcrumbs.length - 1) {
                button.classList.add('btn-active');
                button.disabled = true;
            }
            elements.stagingBreadcrumbs.appendChild(button);
        });
    }

    function renderStagingEntries() {
        if (!elements.stagingTableBody) {
            return;
        }

        const entries = state.staging.entries || [];
        if (!entries.length) {
            elements.stagingTableBody.innerHTML = '';
            elements.stagingEmptyState?.classList.remove('hidden');
            return;
        }
        elements.stagingEmptyState?.classList.add('hidden');

        elements.stagingTableBody.innerHTML = '';
        entries.forEach((entry) => {
            const row = document.createElement('tr');
            const typeBadge = entry.is_dir
                ? '<span class="badge badge-ghost badge-sm">Folder</span>'
                : '<span class="badge badge-primary badge-sm">File</span>';
            const sizeText = entry.is_dir ? '—' : formatBytes(entry.size_bytes);
            const modifiedText = entry.modified ? formatDate(entry.modified) : '—';

            const actionButtons = [];
            if (entry.is_dir) {
                actionButtons.push(`<button class="btn btn-ghost btn-xs" data-action="open" data-path="${escapeHtml(entry.path)}"><i class="fas fa-folder-open"></i> Open</button>`);
                actionButtons.push(`<button class="btn btn-ghost btn-xs" data-action="add-folder" data-path="${escapeHtml(entry.path)}"><i class="fas fa-plus"></i> Add Files</button>`);
            } else if (entry.can_import) {
                actionButtons.push(`<button class="btn btn-primary btn-xs" data-action="add-file" data-path="${escapeHtml(entry.path)}" data-absolute="${escapeHtml(entry.absolute_path)}"><i class="fas fa-plus"></i> Add</button>`);
            } else {
                actionButtons.push('<span class="text-xs text-base-content/50">Unsupported</span>');
            }

            row.innerHTML = `
                <td>
                    <span class="font-medium block leading-tight">${escapeHtml(entry.name)}</span>
                </td>
                <td>${sizeText}</td>
                <td class="text-right">
                    <div class="import-action-buttons">${actionButtons.join('')}</div>
                </td>
            `;

            row.querySelectorAll('[data-action]').forEach((button) => {
                if (!button.dataset.absolute && entry.absolute_path) {
                    button.dataset.absolute = entry.absolute_path;
                }
            });

            elements.stagingTableBody.appendChild(row);
        });
    }

    function addEntryFromStaging(relativePath, absolutePath) {
        if (!absolutePath) {
            const entry = (state.staging.entries || []).find((item) => (item.path || '') === relativePath);
            absolutePath = entry?.absolute_path;
        }
        if (!absolutePath) {
            showNotification('Unable to resolve file path.', 'error');
            return;
        }
        if (state.selectedEntries.some((entry) => entry.path === absolutePath)) {
            showNotification('File already added.', 'info');
            return;
        }
        state.selectedEntries.push({
            path: absolutePath,
            label: relativePath || absolutePath
        });
        showNotification('File added to selection.', 'success');
        renderSelectedFiles();
    }

    async function addFolderFiles(relativePath) {
        if (!config.stagingScanEndpoint) {
            return;
        }
        try {
            const params = new URLSearchParams({ path: relativePath || '', recursive: 'true' });
            const response = await fetch(`${config.stagingScanEndpoint}?${params.toString()}`);
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Unable to scan folder');
            }
            const files = payload.files || [];
            let added = 0;
            files.forEach((file) => {
                if (!file.can_import) {
                    return;
                }
                if (state.selectedEntries.some((entry) => entry.path === file.absolute_path)) {
                    return;
                }
                state.selectedEntries.push({
                    path: file.absolute_path,
                    label: file.path || file.absolute_path
                });
                added += 1;
            });
            if (added) {
                showNotification(`Added ${added} file(s) from folder.`, 'success');
                renderSelectedFiles();
            } else {
                showNotification('No new files were added from that folder.', 'info');
            }
        } catch (error) {
            showNotification(error.message || 'Unable to scan folder', 'error');
        }
    }

    async function handleFileUpload(event) {
        const input = event.target;
        const files = Array.from(input?.files || []);
        if (!files.length) {
            return;
        }
        if (!config.uploadEndpoint) {
            showNotification('Upload endpoint is not configured.', 'error');
            return;
        }

        const formData = new FormData();
        files.forEach((file) => formData.append('files', file, file.name));

        elements.uploadStatus?.classList.remove('hidden');
        if (elements.uploadStatusText) {
            elements.uploadStatusText.textContent = files.length === 1
                ? `Uploading ${files[0].name}…`
                : `Uploading ${files.length} files…`;
        }
        elements.uploadFileBtn?.setAttribute('disabled', 'disabled');

        try {
            const response = await fetch(config.uploadEndpoint, {
                method: 'POST',
                body: formData
            });
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Upload failed');
            }

            const uploaded = payload.files || [];
            let added = 0;
            uploaded.forEach((file) => {
                if (state.selectedEntries.some((entry) => entry.path === file.absolute_path)) {
                    return;
                }
                state.selectedEntries.push({
                    path: file.absolute_path,
                    label: file.path || file.name || file.absolute_path
                });
                added += 1;
            });

            const skipped = payload.skipped || [];
            if (added) {
                renderSelectedFiles();
                showNotification(`Uploaded ${added} file(s) to the staging directory.`, 'success');
            }
            if (skipped.length) {
                showNotification(
                    `Skipped ${skipped.length} file(s): ${skipped.map((item) => item.reason).join('; ')}`,
                    'warning'
                );
            }
            loadStagingDirectory(state.staging.currentPath);
        } catch (error) {
            showNotification(error.message || 'Upload failed', 'error');
        } finally {
            elements.uploadStatus?.classList.add('hidden');
            elements.uploadFileBtn?.removeAttribute('disabled');
            input.value = '';
        }
    }

    function renderSelectedFiles() {
        if (!elements.selectedFilesContainer) {
            return;
        }

        if (!state.selectedEntries.length) {
            elements.selectedFilesContainer.innerHTML = '<span class="text-base-content/50">No sources selected yet.</span>';
            elements.previewBtn?.setAttribute('disabled', 'disabled');
            elements.importBtn?.setAttribute('disabled', 'disabled');
            resetBatchState();
            return;
        }

        elements.selectedFilesContainer.innerHTML = '';
        state.selectedEntries.forEach((entry, index) => {
            const row = document.createElement('div');
            row.className = 'flex items-center justify-between gap-3 rounded-md bg-base-200/60 px-3 py-2';
            row.innerHTML = `
                <div class="flex-1 min-w-0">
                    <p class="text-sm font-medium text-base-content truncate">${escapeHtml(entry.label)}</p>
                    <p class="text-xs text-base-content/60 truncate"><code>${escapeHtml(entry.path)}</code></p>
                </div>
                <button class="btn btn-ghost btn-xs text-error" type="button">
                    <i class="fas fa-times"></i>
                </button>
            `;
            row.querySelector('button')?.addEventListener('click', () => removeSelectedEntry(index));
            elements.selectedFilesContainer.appendChild(row);
        });

        elements.previewBtn?.removeAttribute('disabled');
    }

    function removeSelectedEntry(index) {
        state.selectedEntries.splice(index, 1);
        renderSelectedFiles();
    }

    function clearSelection() {
        state.selectedEntries = [];
        renderSelectedFiles();
    }

    function getSelectedPaths() {
        return state.selectedEntries.map((entry) => entry.path).filter(Boolean);
    }

    // --- Batch previewing + card rendering ---

    // A chosen library replaces the path and template, so the library's own template applies
    function withLibrary(payload) {
        const select = document.getElementById('importLibrary');
        if (!select) return payload;
        const { template_name, library_path, ...rest } = payload;
        return { ...rest, library_id: Number(select.value) };
    }

    document.getElementById('importLibrary')?.addEventListener('change', () => {
        if (state.batch && state.batch.id && getSelectedPaths().length) {
            handleStageBatch();
        }
    });

    async function handleStageBatch() {
        const paths = getSelectedPaths();
        if (!paths.length) {
            showNotification('Add at least one file path to stage.', 'warning');
            return;
        }
        if (!config.batchPreviewEndpoint) {
            showNotification('Batch preview endpoint is not configured.', 'error');
            return;
        }

        setButtonLoading(elements.previewBtn, true);
        elements.importBtn?.setAttribute('disabled', 'disabled');

        try {
            await discardExistingBatch();

            const response = await fetch(config.batchPreviewEndpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(withLibrary({
                    paths,
                    template_name: state.batch.template,
                    library_path: state.batch.libraryPath
                }))
            });

            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Unable to stage files');
            }

            state.batch = createBatchState(payload);
            renderBatchSummary(state.batch.summary);
            renderCardGrid();
            updateBatchMetaInfo();
            updateSelectAllCardsCheckbox();
            updateImportButtonState();
            showNotification('Files staged. Review the cards below.', 'success');

            await refreshAllCardsOnce();
        } catch (error) {
            showNotification(error.message || 'Staging failed', 'error');
            resetBatchState();
        } finally {
            setButtonLoading(elements.previewBtn, false);
        }
    }

    async function discardExistingBatch() {
        if (!state.batch.id || !config.batchDeleteEndpointTemplate) {
            return;
        }
        try {
            const url = buildUrl(config.batchDeleteEndpointTemplate, { '__batch__': state.batch.id });
            await fetch(url, { method: 'DELETE' });
        } catch (error) {
        } finally {
            resetBatchState();
        }
    }

    function createBatchState(data = {}) {
        const cards = Array.isArray(data.cards) ? data.cards.map((card) => ({ ...card })) : [];
        const cardMap = new Map();
        const selectedCardIds = new Set();

        cards.forEach((card) => {
            if (!card || !card.card_id) {
                return;
            }
            cardMap.set(card.card_id, card);
            if (card.status === 'ready') {
                selectedCardIds.add(card.card_id);
            }
        });

        return {
            id: data.batch_id || null,
            template: data.template || config.defaultTemplate || 'standard',
            libraryPath: data.library_path || config.defaultLibraryPath || '',
            cards,
            cardMap,
            summary: data.summary || createEmptySummary(),
            selectedCardIds
        };
    }

    function createEmptySummary() {
        return {
            total: 0,
            ready: 0,
            pending: 0,
            missing: 0,
            invalid: 0,
            error: 0,
            imported: 0
        };
    }

    function resetBatchState() {
        state.batch = createBatchState();
        renderBatchSummary(state.batch.summary);
        renderCardGrid();
        updateBatchMetaInfo();
        updateSelectAllCardsCheckbox();
        updateImportButtonState();
        if (elements.importResultCard) {
            elements.importResultCard.classList.add('hidden');
        }
        if (elements.importResultsBody) {
            elements.importResultsBody.innerHTML = '';
        }
    }

    function renderBatchSummary(summary) {
        if (!elements.previewSummary) {
            return;
        }
        if (!summary || !state.batch.id) {
            SUMMARY_FIELDS.forEach((field) => {
                if (elements.summaryFields[field]) {
                    elements.summaryFields[field].textContent = '0';
                }
            });
            return;
        }

        SUMMARY_FIELDS.forEach((field) => {
            const value = summary[field] ?? 0;
            if (elements.summaryFields[field]) {
                elements.summaryFields[field].textContent = value;
            }
        });
    }

    function updateBatchMetaInfo() {
        if (!elements.batchMetaInfo) {
            return;
        }
        if (!state.batch.id) {
            elements.batchMetaInfo.textContent = '';
            elements.batchMetaInfo.classList.add('opacity-0');
            return;
        }
        const shortId = state.batch.id.slice(0, 8);
        elements.batchMetaInfo.textContent = `Template: ${state.batch.template} • Library: ${state.batch.libraryPath} • Batch ${shortId}`;
        elements.batchMetaInfo.classList.remove('opacity-0');
    }

    function renderCardGrid() {
        if (!elements.cardGrid) {
            return;
        }
        const cards = state.batch.cards || [];
        elements.cardGrid.innerHTML = '';

        if (!cards.length) {
            elements.cardEmptyState?.classList.remove('hidden');
            elements.cardSelectAll?.setAttribute('disabled', 'disabled');
            elements.cardSelectAll && (elements.cardSelectAll.checked = false);
            return;
        }

        elements.cardEmptyState?.classList.add('hidden');

        cards.forEach((card) => {
            const cardEl = document.createElement('div');
            cardEl.className = `import-card border ${card.status === 'ready' ? 'border-success/30 bg-success/5' : 'border-base-content/15 bg-base-100/40'}`;
            cardEl.dataset.cardId = card.card_id;
            cardEl.innerHTML = buildCardMarkup(card);

            const checkbox = cardEl.querySelector('input[type="checkbox"]');
            if (checkbox) {
                checkbox.checked = state.batch.selectedCardIds.has(card.card_id);
                checkbox.disabled = card.status !== 'ready';
                checkbox.addEventListener('change', (event) => {
                    if (event.target.checked) {
                        state.batch.selectedCardIds.add(card.card_id);
                    } else {
                        state.batch.selectedCardIds.delete(card.card_id);
                    }
                    updateSelectAllCardsCheckbox();
                    updateImportButtonState();
                });
            }

            const refreshButton = cardEl.querySelector('[data-action="refresh"]');
            refreshButton?.addEventListener('click', () => refreshCard(card.card_id));

            const remapButton = cardEl.querySelector('[data-action="remap"]');
            remapButton?.addEventListener('click', () => openMetadataSearchModal(card.card_id));

            elements.cardGrid.appendChild(cardEl);
        });
    }

    function buildCardMarkup(card) {
        const metadata = card.metadata || {};
        const extracted = card.extracted || {};
        const destination = card.destination || {};
        const messages = [].concat(card.messages || [], extracted.warnings || []);
        const title = metadata.Title || extracted.title || extracted.clean_title || 'Untitled Import';
        const author = formatPeople(metadata.Author, metadata.Authors, extracted.author, extracted.album_artist);
        const narrator = formatPeople(metadata.Narrator, metadata.Narrators, extracted.narrator);
        const series = metadata.Series || extracted.series || 'N/A';
        const sequence = metadata.Sequence || extracted.sequence || '—';
        const runtime = metadata.Runtime || formatRuntime(extracted.duration_seconds);
        const publisher = metadata.Publisher || extracted.publisher || '—';
        const release = metadata['Release Date'] || extracted.year || '—';
        const asin = metadata.ASIN || extracted.asin || 'N/A';
        const sizeText = formatBytes(card.size_bytes);
        const statusBadge = formatStatusBadge(card.status);
        const seriesLabel = series && series !== 'N/A'
            ? `${series}${sequence && sequence !== '—' ? ` #${sequence}` : ''}`
            : 'Standalone';
        const summaryText = formatSummary(metadata.Summary || extracted.summary || extracted.description || null);
        const coverImage = selectCoverImage(metadata, extracted);
        const coverBlock = buildCoverElement(coverImage, title, 'sm');
        const destinationText = destination.full_path
            ? `<code class="text-xs break-all">${escapeHtml(destination.full_path)}</code>`
            : `<span class="text-warning">${escapeHtml(destination.error || 'Unable to determine destination')}</span>`;
        const sourcePath = card.source_path ? `<code class="text-xs break-all">${escapeHtml(card.source_path)}</code>` : '—';
        const summarySection = summaryText
            ? `<div class="import-card-summary"><span class="import-card-summary-label">Summary</span><p>${escapeHtml(summaryText)}</p></div>`
            : '';
        const chips = [
            ['Series', seriesLabel],
            ['Narrator', narrator],
            ['Runtime', runtime || '—'],
            ['File Size', sizeText],
            ['Publisher', publisher],
            ['Release', release]
        ].filter(([, value]) => value && value !== '—' && value !== 'N/A');
        const chipMarkup = chips.length
            ? chips.map(([label, value]) => `<span class="import-card-chip"><span class="import-card-chip-label">${escapeHtml(label)}:</span> ${escapeHtml(value)}</span>`).join('')
            : '<span class="import-card-chip import-card-chip--muted">Metadata pending</span>';

        return `
            <div class="import-card-main">
                <div class="import-card-checkbox">
                    <input type="checkbox" class="checkbox checkbox-sm" />
                </div>
                ${coverBlock}
                <div class="import-card-body">
                    <div class="import-card-header">
                        <div class="import-card-status">
                            ${statusBadge}
                            <span class="import-card-format">${escapeHtml(card.format || 'Unknown Format')}</span>
                        </div>
                        <div class="import-card-actions">
                            <button class="btn btn-ghost btn-xs" data-action="refresh" ${card.status === 'imported' ? 'disabled' : ''}>
                                <i class="fas fa-rotate"></i>
                                Refresh
                            </button>
                            <button class="btn btn-ghost btn-xs" data-action="remap">
                                <i class="fas fa-wand-magic"></i>
                                Remap
                            </button>
                        </div>
                    </div>
                    <div class="import-card-title">${escapeHtml(title)}</div>
                    <div class="import-card-people">
                        <div class="import-card-people-row">
                            <span class="import-card-people-label">Author:</span>
                            <span class="import-card-people-value">${escapeHtml(author)}</span>
                        </div>
                        <div class="import-card-people-row">
                            <span class="import-card-people-label">Narrator:</span>
                            <span class="import-card-people-value">${escapeHtml(narrator)}</span>
                        </div>
                    </div>
                    <div class="import-card-chips">
                        <span class="import-card-chip"><span class="import-card-chip-label">ASIN:</span> ${escapeHtml(asin)}</span>
                        ${chipMarkup}
                    </div>
                </div>
            </div>
            <div class="import-card-footer">
                <div>
                    <span class="import-card-footer-label">Destination</span>
                    ${destinationText}
                </div>
            </div>
            ${summarySection}
            ${messages.length ? `<div class="messages-list">${formatMessages(messages)}</div>` : ''}
        `;
    }

    function formatStatusBadge(status) {
        const map = {
            ready: 'badge-success',
            pending: 'badge-soft',
            missing: 'badge-warning',
            invalid: 'badge-error',
            error: 'badge-error',
            imported: 'badge-info'
        };
        const style = map[status] || 'badge-ghost';
        return `<span class="badge ${style} badge-sm capitalize">${escapeHtml(status || 'unknown')}</span>`;
    }

    function formatRuntime(durationSeconds) {
        if (!durationSeconds) {
            return null;
        }
        const minutes = Math.round(Number(durationSeconds) / 60);
        const hours = Math.floor(minutes / 60);
        const mins = minutes % 60;
        return `${hours}h ${mins}m`;
    }

    function formatBytes(bytes) {
        if (!bytes && bytes !== 0) return '—';
        if (bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const index = Math.floor(Math.log(bytes) / Math.log(1024));
        const value = bytes / Math.pow(1024, index);
        return `${value.toFixed(1)} ${units[index]}`;
    }

    function formatDate(value) {
        if (!value) {
            return '—';
        }
        try {
            const date = new Date(value);
            return date.toLocaleString();
        } catch (error) {
            return value;
        }
    }

    function formatMessages(messages) {
        if (!messages || (Array.isArray(messages) && messages.length === 0)) {
            return '<div class="text-base-content/40">—</div>';
        }
        if (Array.isArray(messages)) {
            return messages.map((msg) => `<div>${escapeHtml(String(msg))}</div>`).join('');
        }
        return `<div>${escapeHtml(String(messages))}</div>`;
    }

    function formatPeople(...values) {
        const people = normalizePeopleList(values);
        if (!people.length) {
            return 'Unknown';
        }
        return [...new Set(people)].join(', ');
    }

    function normalizePeopleList(values) {
        const tokens = [];
        (values || []).flat().forEach((value) => {
            if (value === null || value === undefined) {
                return;
            }
            if (Array.isArray(value)) {
                value.forEach((entry) => {
                    if (entry) {
                        tokens.push(String(entry));
                    }
                });
                return;
            }
            const text = String(value);
            text.split(/[;,/&]|\band\b/gi).forEach((part) => {
                const trimmed = part.trim();
                if (trimmed) {
                    tokens.push(trimmed);
                }
            });
        });
        return tokens.filter(Boolean);
    }

    function stripHtml(value) {
        if (!value) {
            return '';
        }
        const div = document.createElement('div');
        div.innerHTML = value;
        return div.textContent || div.innerText || '';
    }

    function truncateText(text, limit = 320) {
        if (!text || text.length <= limit) {
            return text;
        }
        return `${text.slice(0, limit).trim()}…`;
    }

    function formatSummary(value) {
        const stripped = stripHtml(value || '')
            .replace(/\s+/g, ' ')
            .trim();
        if (!stripped) {
            return null;
        }
        return truncateText(stripped, 360);
    }

    function selectCoverImage(metadata, extracted) {
        const candidates = [
            metadata['Cover Image'],
            metadata.coverImage,
            metadata.cover_image,
            metadata.cover_image_url,
            metadata.cover,
            extracted.cover_image,
            extracted.cover,
            extracted.picture
        ];
        const match = candidates.find((value) => typeof value === 'string' && value.trim());
        return match ? match.trim() : null;
    }

    function buildCoverElement(url, title, size = 'md') {
        const sizeClass = size === 'sm' ? 'import-card-cover--sm' : 'import-card-cover--md';
        if (url) {
            return `<div class="import-card-cover ${sizeClass}"><img src="${escapeHtml(url)}" alt="Cover art for ${escapeHtml(title)}" loading="lazy" /></div>`;
        }
        return `
            <div class="import-card-cover ${sizeClass} import-card-cover--empty">
                <i class="fas fa-book text-base-content/40 text-xl" aria-hidden="true"></i>
            </div>
        `;
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

    function updateSelectAllCardsCheckbox() {
        const checkbox = elements.cardSelectAll;
        if (!checkbox) {
            return;
        }
        const readyCards = (state.batch.cards || []).filter((card) => card.status === 'ready');
        if (!readyCards.length) {
            checkbox.checked = false;
            checkbox.indeterminate = false;
            checkbox.setAttribute('disabled', 'disabled');
            return;
        }

        checkbox.removeAttribute('disabled');
        const selectedReady = readyCards.filter((card) => state.batch.selectedCardIds.has(card.card_id)).length;
        checkbox.checked = selectedReady === readyCards.length && readyCards.length > 0;
        checkbox.indeterminate = selectedReady > 0 && selectedReady < readyCards.length;
    }

    function toggleSelectAllCards(event) {
        if (!state.batch.cards?.length) {
            return;
        }
        const shouldSelect = Boolean(event?.target?.checked);
        state.batch.cards.forEach((card) => {
            if (card.status !== 'ready') {
                return;
            }
            if (shouldSelect) {
                state.batch.selectedCardIds.add(card.card_id);
            } else {
                state.batch.selectedCardIds.delete(card.card_id);
            }
        });
        renderCardGrid();
        updateSelectAllCardsCheckbox();
        updateImportButtonState();
    }

    function updateImportButtonState() {
        const readySelection = state.batch.selectedCardIds.size;
        if (!readySelection) {
            elements.importBtn?.setAttribute('disabled', 'disabled');
        } else {
            elements.importBtn?.removeAttribute('disabled');
        }
    }

    function setButtonLoading(button, isLoading) {
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
    }

    function buildUrl(template, replacements) {
        if (!template) {
            return '';
        }
        return Object.entries(replacements || {}).reduce((acc, [token, value]) => (
            acc.replace(token, encodeURIComponent(value))
        ), template);
    }

    function getCardById(cardId) {
        return state.batch.cardMap?.get(cardId);
    }

    function upsertCard(updatedCard) {
        if (!updatedCard || !updatedCard.card_id) {
            return;
        }
        const index = state.batch.cards.findIndex((card) => card.card_id === updatedCard.card_id);
        if (index >= 0) {
            state.batch.cards[index] = updatedCard;
        } else {
            state.batch.cards.push(updatedCard);
        }
        state.batch.cardMap.set(updatedCard.card_id, updatedCard);
        if (updatedCard.status === 'ready') {
            state.batch.selectedCardIds.add(updatedCard.card_id);
        } else {
            state.batch.selectedCardIds.delete(updatedCard.card_id);
        }
    }

    // --- Metadata refresh & remapping ---

    async function refreshCard(cardId, options = {}) {
        if (!state.batch.id || !config.batchCardRefreshEndpointTemplate) {
            showNotification('Batch not ready yet.', 'warning');
            return;
        }
        const card = getCardById(cardId);
        if (!card) {
            showNotification('Unable to locate card.', 'error');
            return;
        }

        const payload = {};
        if (options.metadata) {
            payload.metadata = options.metadata;
        }
        if (options.asin) {
            payload.asin = options.asin;
        }
        if (options.template_name) {
            payload.template_name = options.template_name;
        }
        if (options.library_path) {
            payload.library_path = options.library_path;
        }

        try {
            const url = buildUrl(config.batchCardRefreshEndpointTemplate, {
                '__batch__': state.batch.id,
                '__card__': cardId
            });
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: Object.keys(payload).length ? JSON.stringify(payload) : '{}'
            });
            const result = await response.json();
            if (!response.ok || !result.success) {
                throw new Error(result.error || 'Unable to refresh metadata');
            }

            upsertCard(result.card);
            if (result.summary) {
                state.batch.summary = result.summary;
            }
            renderBatchSummary(state.batch.summary);
            renderCardGrid();
            updateSelectAllCardsCheckbox();
            updateImportButtonState();
            if (!options.silent) {
                showNotification('Card metadata updated.', 'success');
            }
        } catch (error) {
            if (!options.silent) {
                showNotification(error.message || 'Metadata refresh failed', 'error');
            }
        }
    }

    function openMetadataSearchModal(cardId) {
        const modal = elements.metadataSearchModal;
        if (!modal) {
            showNotification('Metadata search modal is unavailable.', 'error');
            return;
        }
        const card = getCardById(cardId);
        if (!card) {
            showNotification('Unable to locate card.', 'error');
            return;
        }

        state.metadataSearch.cardId = cardId;
        state.metadataSearch.results = [];

        const metadata = card.metadata || {};
        const extracted = card.extracted || {};

        if (elements.metadataSearchQuery) {
            elements.metadataSearchQuery.value = metadata.Title || extracted.title || '';
        }
        if (elements.metadataSearchAuthor) {
            elements.metadataSearchAuthor.value = metadata.Author || extracted.author || extracted.album_artist || '';
        }
        if (elements.metadataSearchAsin) {
            elements.metadataSearchAsin.value = metadata.ASIN || extracted.asin || '';
        }
        if (elements.metadataSearchCardTitle) {
            elements.metadataSearchCardTitle.textContent = `Currently editing “${metadata.Title || extracted.title || card.source_path}”`;
        }
        renderMetadataSearchResults([]);
        setMetadataSearchStatus('Enter a query to search for matches.');

        modal.showModal();
    }

    function closeMetadataSearchModal() {
        elements.metadataSearchModal?.close();
    }

    function resetMetadataSearchState() {
        state.metadataSearch.cardId = null;
        state.metadataSearch.results = [];
        elements.metadataSearchResults && (elements.metadataSearchResults.innerHTML = '');
        setMetadataSearchStatus('Enter a query to search for matches.');
    }

    async function handleMetadataSearchSubmit(event) {
        event.preventDefault();
        if (!state.metadataSearch.cardId) {
            setMetadataSearchStatus('Open a card before searching.', true);
            return;
        }
        if (!config.metadataSearchEndpoint) {
            setMetadataSearchStatus('Metadata search endpoint unavailable.', true);
            return;
        }

        const query = elements.metadataSearchQuery?.value.trim();
        const author = elements.metadataSearchAuthor?.value.trim();
        const asin = elements.metadataSearchAsin?.value.trim();
        if (!query && !asin) {
            setMetadataSearchStatus('Provide a title or ASIN to search.', true);
            return;
        }

        setButtonLoading(elements.metadataSearchSubmit, true);
        setMetadataSearchStatus('Searching for matches…');

        try {
            const params = new URLSearchParams();
            if (query) params.set('q', query);
            if (author) params.set('author', author);
            if (asin) params.set('asin', asin);
            params.set('limit', '12');
            const url = `${config.metadataSearchEndpoint}?${params.toString()}`;
            const response = await fetch(url);
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Search failed');
            }

            state.metadataSearch.results = payload.results || [];
            renderMetadataSearchResults(state.metadataSearch.results);
            if (!state.metadataSearch.results.length) {
                setMetadataSearchStatus('No matches found. Try different keywords.', true);
            } else {
                setMetadataSearchStatus(`Found ${state.metadataSearch.results.length} candidate${state.metadataSearch.results.length === 1 ? '' : 's'}.`);
            }
        } catch (error) {
            setMetadataSearchStatus(error.message || 'Search failed', true);
        } finally {
            setButtonLoading(elements.metadataSearchSubmit, false);
        }
    }

    async function refreshAllCardsOnce() {
        if (!state.batch || !Array.isArray(state.batch.cards) || !state.batch.cards.length) {
            return;
        }
        for (const card of state.batch.cards) {
            if (!card || !card.card_id) {
                continue;
            }
            try {
                await refreshCard(card.card_id, { silent: true });
            } catch (err) {
            }
        }
    }

    function renderMetadataSearchResults(results) {
        if (!elements.metadataSearchResults) {
            return;
        }
        elements.metadataSearchResults.innerHTML = '';
        if (!results || !results.length) {
            return;
        }
        results.forEach((candidate, index) => {
            const metadata = candidate.metadata || {};
            const title = metadata.Title || 'Unknown Title';
            const authors = formatPeople(metadata.Author, metadata.Authors);
            const coverImage = selectCoverImage(metadata, {});
            const summaryText = formatSummary(metadata.Summary);
            const coverBlock = buildCoverElement(coverImage, title, 'sm');
            const card = document.createElement('div');
            card.className = 'metadata-result-card rounded-lg border border-base-content/20 bg-base-200/40 p-3 space-y-3';
            card.innerHTML = `
                <div class="flex items-start gap-3">
                    ${coverBlock}
                    <div class="flex-1 min-w-0 space-y-1">
                        <p class="font-semibold text-sm truncate">${escapeHtml(title)}</p>
                        <p class="text-xs text-base-content/70">${escapeHtml(authors)}</p>
                        <div class="text-[0.65rem] uppercase tracking-wide text-base-content/60 flex flex-wrap gap-2">
                            <span>ASIN: <code>${escapeHtml(metadata.ASIN || candidate.asin || 'N/A')}</code></span>
                            <span>Source: ${escapeHtml(candidate.source || metadata.source || 'unknown')}</span>
                            <span>Strategy: ${escapeHtml(candidate.match?.strategy || 'n/a')}</span>
                        </div>
                    </div>
                    <button class="btn btn-primary btn-xs whitespace-nowrap" data-candidate-index="${index}">
                        Use match
                    </button>
                </div>
                ${summaryText ? `<p class="text-xs text-base-content/70">${escapeHtml(summaryText)}</p>` : ''}
            `;
            card.querySelector('button')?.addEventListener('click', () => applyMetadataCandidate(index));
            elements.metadataSearchResults.appendChild(card);
        });
    }

    function setMetadataSearchStatus(message, isError = false) {
        if (!elements.metadataSearchStatus) {
            return;
        }
        elements.metadataSearchStatus.textContent = message;
        if (isError) {
            elements.metadataSearchStatus.classList.add('text-error');
        } else {
            elements.metadataSearchStatus.classList.remove('text-error');
        }
    }

    async function applyMetadataCandidate(index) {
        if (!state.metadataSearch.cardId) {
            return;
        }
        const candidate = state.metadataSearch.results[index];
        if (!candidate) {
            showNotification('Candidate no longer available.', 'error');
            return;
        }
        await refreshCard(state.metadataSearch.cardId, {
            metadata: candidate.metadata,
            asin: candidate.asin
        });
        closeMetadataSearchModal();
    }

    async function applyManualAsin() {
        if (!state.metadataSearch.cardId) {
            showNotification('Open a card before forcing an ASIN refresh.', 'warning');
            return;
        }
        const asin = elements.metadataSearchAsin?.value.trim();
        if (!asin) {
            showNotification('Enter an ASIN value first.', 'warning');
            return;
        }
        await refreshCard(state.metadataSearch.cardId, { asin });
        closeMetadataSearchModal();
    }

    // --- Import execution ---

    async function handleImportBatch() {
        if (!state.batch.id || !config.batchImportEndpointTemplate) {
            showNotification('Stage files before importing.', 'warning');
            return;
        }
        const selectedIds = Array.from(state.batch.selectedCardIds);
        if (!selectedIds.length) {
            showNotification('Select at least one ready card to import.', 'warning');
            return;
        }

        setButtonLoading(elements.importBtn, true);

        try {
            const url = buildUrl(config.batchImportEndpointTemplate, { '__batch__': state.batch.id });
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(withLibrary({
                    card_ids: selectedIds,
                    template_name: state.batch.template,
                    library_path: state.batch.libraryPath,
                    move: true
                }))
            });

            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Import failed');
            }

            const result = payload.result || {};
            applyImportOutcomes(result.results || []);
            if (payload.summary) {
                state.batch.summary = payload.summary;
                renderBatchSummary(state.batch.summary);
            }
            renderCardGrid();
            updateSelectAllCardsCheckbox();
            updateImportButtonState();
            renderImportResults(result);
            showNotification(`Import complete: ${(result.summary?.successful) || 0} succeeded.`, 'success');
        } catch (error) {
            showNotification(error.message || 'Import failed', 'error');
        } finally {
            setButtonLoading(elements.importBtn, false);
        }
    }

    function applyImportOutcomes(outcomes) {
        outcomes.forEach((outcome) => {
            if (!outcome || !outcome.card_id) {
                return;
            }
            const card = getCardById(outcome.card_id);
            if (!card) {
                return;
            }
            card.status = outcome.success ? 'imported' : 'error';
            card.messages = card.messages || [];
            if (outcome.message) {
                card.messages.push(outcome.message);
            }
            card.destination = card.destination || {};
            if (outcome.destination_path) {
                card.destination.final_path = outcome.destination_path;
                card.destination.full_path = outcome.destination_path;
            }
            if (outcome.success) {
                state.batch.selectedCardIds.delete(card.card_id);
            }
            upsertCard(card);
        });
    }

    function renderImportResults(result) {
        if (!result) {
            return;
        }
        const summary = result.summary || {};
        const results = result.results || [];

        elements.importSuccess.textContent = summary.successful ?? 0;
        elements.importFailed.textContent = summary.failed ?? 0;
        elements.importTotal.textContent = summary.total ?? results.length;

        elements.importResultsBody.innerHTML = '';
        results.forEach((entry) => {
            const row = document.createElement('tr');
            const card = getCardById(entry.card_id) || {};
            row.innerHTML = `
                <td><code class="text-xs">${escapeHtml(entry.path || card.source_path || 'N/A')}</code></td>
                <td>${formatStatusBadge(entry.success ? 'imported' : 'error')}</td>
                <td class="text-sm">${escapeHtml(entry.message || '—')}</td>
                <td><code class="text-xs">${escapeHtml(entry.destination_path || card.destination?.full_path || '—')}</code></td>
            `;
            elements.importResultsBody.appendChild(row);
        });

        elements.importResultCard?.classList.remove('hidden');
    }
})();
