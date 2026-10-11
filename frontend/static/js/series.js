// --- Series page ------------------------------------------------------------

let allSeries = [];
let currentView = 'compact';

// --- DOM ready --------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    initializeView();
    initializeFilters();
    loadSeries();
});

// --- View management --------------------------------------------------------

function initializeView() {
    const savedView = localStorage.getItem('seriesView') || 'compact';
    setView(savedView);
}

function setView(view) {
    currentView = view;
    localStorage.setItem('seriesView', view);
    
    document.getElementById('tableViewBtn').classList.toggle('btn-primary', view === 'table');
    document.getElementById('compactViewBtn').classList.toggle('btn-primary', view === 'compact');
    
    document.getElementById('tableView').classList.toggle('hidden', view !== 'table');
    document.getElementById('compactView').classList.toggle('hidden', view !== 'compact');
    
    if (allSeries.length > 0) {
        displaySeries(allSeries);
    }
}

// --- Data loading -----------------------------------------------------------

async function loadSeries() {
    const loadingState = document.getElementById('loadingState');
    const errorState = document.getElementById('errorState');
    const emptyState = document.getElementById('emptyState');
    const seriesContent = document.getElementById('seriesContent');
    
    loadingState.classList.remove('hidden');
    errorState.classList.add('hidden');
    emptyState.classList.add('hidden');
    seriesContent.classList.add('hidden');
    
    try {
        const response = await fetch('/series/api/list');
        const data = await response.json();
        
        if (data.success && data.series) {
            allSeries = data.series;
            
            if (allSeries.length === 0) {
                loadingState.classList.add('hidden');
                emptyState.classList.remove('hidden');
            } else {
                loadingState.classList.add('hidden');
                seriesContent.classList.remove('hidden');
                displaySeries(allSeries);
            }
        } else {
            throw new Error(data.error || 'Failed to load series');
        }
    } catch (error) {
        loadingState.classList.add('hidden');
        errorState.classList.remove('hidden');
        document.getElementById('errorMessage').textContent = error.message;
    }
}

// --- Silent refresh helpers -------------------------------------------------

async function refreshSeriesData(seriesAsin) {
    await Promise.all([refreshSeriesList(), refreshSeriesModal(seriesAsin)]);
}

async function refreshSeriesList() {
    try {
        const response = await fetch('/series/api/list');
        const data = await response.json();
        if (data.success && Array.isArray(data.series)) {
            allSeries = data.series;
            if (document.getElementById('searchInput')) {
                applyFilters();
            } else {
                displaySeries(allSeries);
            }
        }
    } catch (error) {
    }
}

async function refreshSeriesModal(seriesAsin) {
    if (!seriesAsin) {
        return;
    }
    const modal = document.getElementById('seriesModal');
    if (!modal || !modal.open) {
        return;
    }
    try {
        const response = await fetch(`/series/api/${seriesAsin}/books`);
        const data = await response.json();
        if (data.success) {
            displaySeriesModal(data);
        }
    } catch (error) {
    }
}

// --- Display functions ------------------------------------------------------

function displaySeries(series) {
    if (currentView === 'table') {
        displayTableView(series);
    } else {
        displayCompactView(series);
    }
}

function displayTableView(series) {
    const tbody = document.getElementById('tableBody');
    tbody.innerHTML = '';
    
    series.forEach(s => {
        const total = s.total_books || 0;
        const owned = s.owned_books || 0;
        const missing = total - owned;
        const percentage = total > 0 ? Math.round((owned / total) * 100) : 0;
        
        const row = document.createElement('tr');
        row.className = 'hover cursor-pointer';
        row.onclick = () => openSeriesModal(s.series_asin);
        
        row.innerHTML = `
            <td>
                <div class="font-semibold">${escapeHtml(s.series_title || 'Unknown Series')}</div>
            </td>
            <td class="text-center">
                <span class="badge badge-ghost">${total}</span>
            </td>
            <td class="text-center">
                <span class="badge badge-success">${owned}</span>
            </td>
            <td class="text-center">
                <span class="badge badge-warning">${missing}</span>
            </td>
            <td class="text-center">
                <div class="flex items-center gap-2">
                    <progress class="progress progress-primary w-20" value="${percentage}" max="100"></progress>
                    <span class="text-sm font-semibold">${percentage}%</span>
                </div>
            </td>
            <td class="text-right">
                <button class="btn btn-soft btn-primary btn-xs" onclick="event.stopPropagation(); window.location.href='/series/${s.series_asin}'">
                    <i class="fas fa-arrow-right"></i> Details
                </button>
            </td>
        `;
        
        tbody.appendChild(row);
    });
}

function displayCompactView(series) {
    const list = document.getElementById('compactList');
    list.innerHTML = '';
    
    series.forEach(s => {
        const total = s.total_books || 0;
        const owned = s.owned_books || 0;
        const missing = total - owned;
        const percentage = total > 0 ? Math.round((owned / total) * 100) : 0;
        
        const item = document.createElement('div');
        item.className = 'card bg-base-200 hover:bg-base-300 transition-colors cursor-pointer';
        item.onclick = () => openSeriesModal(s.series_asin);
        
        item.innerHTML = `
            <div class="card-body p-4">
                <div class="flex flex-wrap items-center justify-between gap-4">
                    <div class="flex-1 min-w-0">
                        <h3 class="card-title text-lg mb-1">${escapeHtml(s.series_title || 'Unknown Series')}</h3>
                        <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-base-content/60">
                            <span><i class="fas fa-books"></i> ${total} book${total !== 1 ? 's' : ''}</span>
                            <span class="text-success"><i class="fas fa-check"></i> ${owned} owned</span>
                            ${missing > 0 ? `<span class="text-warning">${missing} missing</span>` : ''}
                        </div>
                    </div>
                    <div class="flex items-center gap-4">
                        <div class="text-right">
                            <div class="text-2xl font-bold">${percentage}%</div>
                            <div class="text-xs text-base-content/60">Complete</div>
                        </div>
                        <button class="btn btn-soft btn-primary btn-sm" onclick="event.stopPropagation(); window.location.href='/series/${s.series_asin}'">
                            <i class="fas fa-arrow-right"></i> View Details
                        </button>
                    </div>
                </div>
            </div>
        `;
        
        list.appendChild(item);
    });
}

// --- Filter system ----------------------------------------------------------

function initializeFilters() {
    const searchInput = document.getElementById('searchInput');
    const sortFilter = document.getElementById('sortFilter');

    let searchTimeout;
    searchInput?.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            applyFilters();
        }, 300);
    });

    sortFilter?.addEventListener('change', () => {
        applyFilters();
    });
}

function applyFilters() {
    const searchValue = document.getElementById('searchInput').value.toLowerCase();
    const sortValue = document.getElementById('sortFilter').value;
    
    let filtered = allSeries.filter(s => {
        const title = (s.series_title || '').toLowerCase();
        return title.includes(searchValue);
    });
    
    filtered.sort((a, b) => {
        switch (sortValue) {
            case 'title':
                return (a.series_title || '').localeCompare(b.series_title || '');
            case 'total_books':
                return (b.total_books || 0) - (a.total_books || 0);
            case 'completion':
                const aPercent = a.total_books > 0 ? (a.owned_books / a.total_books) : 0;
                const bPercent = b.total_books > 0 ? (b.owned_books / b.total_books) : 0;
                return bPercent - aPercent;
            default:
                return 0;
        }
    });
    
    displaySeries(filtered);
}

// --- Modal functions --------------------------------------------------------

async function openSeriesModal(seriesAsin) {
    const modal = document.getElementById('seriesModal');
    const content = document.getElementById('seriesModalContent');
    
    content.innerHTML = `
        <div class="flex justify-center py-8">
            <span class="loading loading-spinner loading-lg"></span>
        </div>
    `;
    
    modal.showModal();
    
    try {
        const response = await fetch(`/series/api/${seriesAsin}/books`);
        const data = await response.json();
        
        if (data.success) {
            displaySeriesModal(data);
        } else {
            throw new Error(data.error || 'Failed to load series details');
        }
    } catch (error) {
        content.innerHTML = `
            <div class="alert alert-error">
                <i class="fas fa-exclamation-circle"></i>
                <span>Error: ${error.message}</span>
            </div>
        `;
    }
}

function displaySeriesModal(data) {
    const content = document.getElementById('seriesModalContent');
    const stats = data.statistics || {};
    const seriesTitle = data.series_title || 'Unknown Series';
    const books = Array.isArray(data.books) ? data.books : [];
    const importAuthor = getSeriesImportAuthor(data);
    const hasImportContext = Boolean(importAuthor);
    const missingCount = books.filter(book => !book.in_library).length;

    const bookRows = books.length ? books.map(book => {
        const ratingCell = (book.rating && book.rating !== 'N/A')
            ? `<span class="text-sm">${escapeHtml(book.rating)} <i class="fas fa-star text-warning text-xs"></i></span>`
            : '<span class="opacity-50 text-xs">—</span>';

        const rawStatus = book.library_status || (book.in_library ? 'in_library' : 'missing');
        const normalizedStatus = (rawStatus || '').toLowerCase();
        const ownedStatuses = new Set(['in_library', 'owned']);
        const pendingStatuses = new Set(['wanted', 'queued', 'pending', 'processing']);
        const isOwned = ownedStatuses.has(normalizedStatus) || Boolean(book.in_library);
        const isPending = pendingStatuses.has(normalizedStatus);

        let statusBadge;
        if (isOwned) {
            statusBadge = '<span class="badge badge-success badge-sm"><i class="fas fa-check"></i> In Library</span>';
        } else if (isPending) {
            const pendingLabel = normalizedStatus === 'wanted' ? 'Wanted' : 'Queued';
            statusBadge = `<span class="badge badge-warning badge-sm"><i class="fas fa-hourglass-half"></i> ${pendingLabel}</span>`;
        } else {
            statusBadge = '<span class="badge badge-ghost badge-sm">Not Owned</span>';
        }

        const canImportBook = hasImportContext && book.asin && !isOwned && !isPending;
        const importButton = canImportBook
            ? `<button type="button" class="btn btn-xs btn-soft btn-primary js-series-import-book" data-asin="${escapeHtml(book.asin)}" data-author="${escapeHtml(book.author || importAuthor)}" data-title="${escapeHtml(book.title || 'Unknown Title')}">
                    <i class="fas fa-download"></i>
               </button>`
            : '<span class="text-xs text-base-content/50">—</span>';

        return `
            <tr class="hover">
                <td class="font-semibold">${book.sequence || '—'}</td>
                <td>${escapeHtml(book.title || 'Unknown')}</td>
                <td><code>${escapeHtml(book.asin || '—')}</code></td>
                <td>${escapeHtml(book.author || 'Unknown')}</td>
                <td class="text-center">${statusBadge}</td>
                <td class="text-center">${ratingCell}</td>
                <td class="text-center">${importButton}</td>
            </tr>
        `;
    }).join('') : '<tr><td colspan="7" class="text-center text-base-content/60">No books found for this series.</td></tr>';

    content.innerHTML = `
        <div class="space-y-5">
            <div class="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div>
                    <h2 class="text-2xl font-bold mb-2">${escapeHtml(seriesTitle)}</h2>
                    <div class="flex flex-wrap gap-3 text-sm">
                        <span class="badge badge-lg badge-ghost">${stats.total_books || books.length} Books</span>
                        <span class="badge badge-lg badge-success">${stats.owned_books || 0} Owned</span>
                        ${stats.missing_books > 0 ? `<span class="badge badge-lg badge-warning">${stats.missing_books} Missing</span>` : ''}
                        ${missingCount === 0 ? '<span class="badge badge-lg badge-success badge-outline">Complete</span>' : ''}
                    </div>
                    ${hasImportContext
                        ? `<p class="text-xs text-base-content/60 mt-2">Imports will use <span class="font-semibold">${escapeHtml(importAuthor)}</span> as the author context.</p>`
                        : `<div class="alert alert-warning text-sm mt-3">
                            <i class="fas fa-info-circle"></i>
                            <span>Add or update author metadata before importing this series.</span>
                        </div>`}
                </div>
                <div class="flex flex-wrap gap-2">
                    <button class="btn btn-soft btn-primary btn-sm js-series-import-all" ${hasImportContext ? '' : 'disabled'}>
                        <i class="fas fa-cloud-download-alt"></i> Import Series
                    </button>
                </div>
            </div>
            
            <div class="overflow-x-auto">
                <table class="table table-zebra table-sm">
                    <thead>
                        <tr>
                            <th>Book #</th>
                            <th>Title</th>
                            <th>ASIN</th>
                            <th>Author</th>
                            <th class="text-center">Status</th>
                            <th class="text-center">Rating</th>
                            <th class="text-center">Import</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${bookRows}
                    </tbody>
                </table>
            </div>
        </div>
    `;

    content.dataset.importAuthor = importAuthor || '';
    content.dataset.seriesTitle = seriesTitle;
    content.dataset.seriesAsin = data.series_asin || '';
    initializeSeriesModalActions(content);
}

// --- Import helpers ---------------------------------------------------------

function initializeSeriesModalActions(container) {
    if (!container) {
        return;
    }

    const importAuthor = (container.dataset.importAuthor || '').trim();
    const seriesTitle = container.dataset.seriesTitle || 'this series';

    const fullSeriesButton = container.querySelector('.js-series-import-all');
    if (fullSeriesButton) {
        fullSeriesButton.addEventListener('click', async () => {
            if (!importAuthor) {
                showNotification('Series import requires author metadata.', 'error');
                return;
            }

            const confirmMessage = `Import every title from series "${seriesTitle}"?`;
            if (!await AA.showConfirmDialog(confirmMessage, 'Import Series')) {
                return;
            }

            handleImportRequest(
                '/authors/api/import-series',
                { author_name: importAuthor, series_name: seriesTitle },
                fullSeriesButton
            );
        });
    }

    const bookButtons = container.querySelectorAll('.js-series-import-book');
    bookButtons.forEach((button) => {
        button.addEventListener('click', () => {
            const asin = button.dataset.asin;
            const authorName = (button.dataset.author || importAuthor || '').trim();
            const bookTitle = button.dataset.title || 'this title';

            if (!asin) {
                showNotification('Missing ASIN for this title.', 'error');
                return;
            }

            if (!authorName) {
                showNotification('Author context missing for this title.', 'error');
                return;
            }

            handleImportRequest(
                '/authors/api/import-book',
                { author_name: authorName, asin },
                button
            );
        });
    });
}

function getSeriesImportAuthor(data) {
    if (!data) {
        return '';
    }

    if (data.primary_author) {
        return data.primary_author;
    }

    if (Array.isArray(data.author_candidates) && data.author_candidates.length > 0) {
        return data.author_candidates[0];
    }

    const fallbackBook = (data.books || []).find((book) => {
        const authorValue = (book.author || '').trim();
        return authorValue && authorValue.toLowerCase() !== 'unknown author';
    });

    return fallbackBook ? fallbackBook.author : '';
}

async function handleImportRequest(url, payload, button) {
    setLoadingState(button, true);

    try {
        const data = await sendImportRequest(url, payload);

        if (data.success) {
            showNotification(data.message || 'Import complete.', 'success');
            const seriesAsin = document.getElementById('seriesModalContent')?.dataset.seriesAsin;
            await refreshSeriesData(seriesAsin);
        } else {
            let warningMessage = data.error || 'No titles imported.';

            const skippedMessages = [];
            if (Number(data.language_skipped) > 0) {
                skippedMessages.push(`${data.language_skipped} filtered by language`);
            }
            if (Number(data.missing_raw) > 0) {
                skippedMessages.push(`${data.missing_raw} skipped due to missing metadata`);
            }

            if (skippedMessages.length) {
                warningMessage += ` (${skippedMessages.join(', ')})`;
            }

            showNotification(warningMessage, 'warning');
        }
    } catch (error) {
        showNotification(error.message || 'Import failed.', 'error');
    } finally {
        setLoadingState(button, false);
    }
}

async function sendImportRequest(url, payload) {
    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    });

    let data = {};
    try {
        data = await response.json();
    } catch (error) {
    }

    if (!response.ok) {
        const message = data.error || `Request failed (${response.status})`;
        throw new Error(message);
    }

    return data;
}

function setLoadingState(button, isLoading) {
    if (!button) {
        return;
    }

    if (isLoading) {
        button.disabled = true;
        button.classList.add('loading');
        button.setAttribute('aria-busy', 'true');
    } else {
        button.disabled = false;
        button.classList.remove('loading');
        button.removeAttribute('aria-busy');
    }
}

// --- Utility functions ------------------------------------------------------

function escapeHtml(text) {
    if (text == null) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function showNotification(message, type = 'info') {
}
