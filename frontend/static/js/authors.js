const AUTHORS_API_URL = '/authors/api/list?include_stats=true';

let allAuthors = [];
let filteredAuthors = [];
let currentView = localStorage.getItem('authorsView') || 'compact';

const METADATA_POLL_MS = 20000;
const METADATA_POLL_MAX = 15;
let metadataPolls = 0;
let metadataPollTimer = null;

document.addEventListener('DOMContentLoaded', () => {
    initializeView();
    initializeFilters();
    loadAuthors();
});

function initializeView() {
    setView(currentView);
}

function initializeFilters() {
    const searchInput = document.getElementById('searchInput');
    const sortFilter = document.getElementById('sortFilter');

    let debounceId;
    searchInput?.addEventListener('input', () => {
        clearTimeout(debounceId);
        debounceId = window.setTimeout(applyFilters, 250);
    });

    sortFilter?.addEventListener('change', applyFilters);
}

async function loadAuthors({ background = false } = {}) {
    if (!background) {
        toggleState('loading');
    }

    try {
        const response = await fetch(AUTHORS_API_URL);
        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Failed to load authors');
        }

        allAuthors = data.authors || [];
        scheduleMetadataPoll(Boolean(data.metadata_pending));

        if (!allAuthors.length) {
            filteredAuthors = [];
            toggleState('empty');
            return;
        }

        filteredAuthors = [...allAuthors];
        toggleState('content');
        applyFilters();
    } catch (error) {
        if (!background) {
            toggleState('error', error.message);
        }
    }
}

function scheduleMetadataPoll(pending) {
    clearTimeout(metadataPollTimer);
    if (!pending || metadataPolls >= METADATA_POLL_MAX) {
        return;
    }
    metadataPolls += 1;
    metadataPollTimer = window.setTimeout(() => {
        if (document.hidden) {
            scheduleMetadataPoll(true);  // try again later; don't count hidden time
            metadataPolls -= 1;
            return;
        }
        loadAuthors({ background: true });
    }, METADATA_POLL_MS);
}

function applyFilters() {
    if (!allAuthors.length) {
        return;
    }

    const searchValue = (document.getElementById('searchInput')?.value || '').trim().toLowerCase();
    const sortValue = document.getElementById('sortFilter')?.value || 'name';

    filteredAuthors = allAuthors
        .filter((author) => (author.name || '').toLowerCase().includes(searchValue))
        .sort((a, b) => compareAuthors(a, b, sortValue));

    if (!filteredAuthors.length) {
        document.getElementById('authorsContent')?.classList.add('hidden');
        document.getElementById('emptyState')?.classList.remove('hidden');
        return;
    }

    document.getElementById('emptyState')?.classList.add('hidden');
    document.getElementById('authorsContent')?.classList.remove('hidden');
    displayAuthors(filteredAuthors);
}

function compareAuthors(a, b, sortKey) {
    switch (sortKey) {
        case 'books':
            return (b.book_count || 0) - (a.book_count || 0);
        case 'hours':
            return (b.total_hours || 0) - (a.total_hours || 0);
        case 'completion':
            return (b.completion_rate || 0) - (a.completion_rate || 0);
        case 'name':
        default:
            return (a.name || '').localeCompare(b.name || '');
    }
}

function displayAuthors(list) {
    if (currentView === 'table') {
        displayTableView(list);
    } else {
        displayCompactView(list);
    }
}

function displayTableView(list) {
    const tbody = document.getElementById('tableBody');
    if (!tbody) {
        return;
    }

    tbody.innerHTML = '';

    list.forEach((author) => {
        const completion = Math.round(author.completion_rate || 0);
        const row = document.createElement('tr');
        row.className = 'hover cursor-pointer';
        row.addEventListener('click', () => openAuthorDetail(author.name));

        row.innerHTML = `
            <td>
                <div class="flex items-center gap-3">
                    <div class="avatar">
                        <div class="w-12 h-12 rounded-full overflow-hidden bg-base-300 flex items-center justify-center text-base-content/40">
                            ${author.author_image
                                ? `<img src="${escapeHtml(author.author_image)}" alt="${escapeHtml(author.name || 'Author')}" class="object-cover w-full h-full">`
                                : '<i class="fas fa-user"></i>'}
                        </div>
                    </div>
                    <div>
                        <div class="font-semibold">${escapeHtml(author.name || 'Unknown Author')}</div>
                        ${author.primary_publisher && author.primary_publisher !== 'Unknown'
                            ? `<div class="text-xs text-base-content/60">${escapeHtml(author.primary_publisher)}</div>`
                            : ''}
                    </div>
                </div>
            </td>
            <td class="text-center font-semibold">${author.book_count || 0}</td>
            <td class="text-center">${author.series_count || 0}</td>
            <td class="text-center">${author.total_hours || 0}</td>
            <td class="text-center">
                <div class="flex items-center gap-2 justify-center">
                    <progress class="progress progress-primary w-24" value="${completion}" max="100"></progress>
                    <span class="text-sm font-semibold">${completion}%</span>
                </div>
            </td>
        `;

        tbody.appendChild(row);
    });
}

function displayCompactView(list) {
    const container = document.getElementById('compactList');
    if (!container) {
        return;
    }

    container.innerHTML = '';

    list.forEach((author) => {
        const completion = Math.round(author.completion_rate || 0);
        const totalHours = author.total_hours || 0;
        const owned = author.owned_count || 0;
        const missing = author.missing_count || 0;

        const item = document.createElement('div');
        item.className = 'card bg-base-200 hover:bg-base-300 transition-colors cursor-pointer';
        item.addEventListener('click', () => openAuthorDetail(author.name));

        item.innerHTML = `
            <div class="card-body p-4">
                <div class="flex items-center justify-between gap-4">
                    <div class="flex items-center gap-4">
                        <div class="avatar">
                            <div class="w-16 h-16 rounded-full overflow-hidden bg-base-300 flex items-center justify-center text-base-content/40">
                                ${author.author_image
                                    ? `<img src="${escapeHtml(author.author_image)}" alt="${escapeHtml(author.name || 'Author')}" class="object-cover w-full h-full">`
                                    : '<i class="fas fa-user"></i>'}
                            </div>
                        </div>
                        <div>
                            <h3 class="card-title text-lg mb-1">${escapeHtml(author.name || 'Unknown Author')}</h3>
                            <div class="flex flex-wrap gap-3 text-sm text-base-content/60">
                                <span><i class="fas fa-book mr-1"></i>${author.book_count || 0} books</span>
                                <span><i class="fas fa-layer-group mr-1"></i>${author.series_count || 0} series</span>
                                <span><i class="fas fa-headphones mr-1"></i>${totalHours} hrs</span>
                                <span class="text-success"><i class="fas fa-check mr-1"></i>${owned} owned</span>
                                ${missing > 0 ? `<span class="text-warning">${missing} missing</span>` : ''}
                            </div>
                        </div>
                    </div>
                    <div class="text-right">
                        <div class="text-2xl font-bold">${completion}%</div>
                        <div class="text-xs text-base-content/60">Complete</div>
                    </div>
                </div>
            </div>
        `;

        container.appendChild(item);
    });
}

function setView(view) {
    currentView = view;
    localStorage.setItem('authorsView', view);

    const tableBtn = document.getElementById('tableViewBtn');
    const compactBtn = document.getElementById('compactViewBtn');
    const tableView = document.getElementById('tableView');
    const compactView = document.getElementById('compactView');

    tableBtn?.classList.toggle('btn-primary', view === 'table');
    compactBtn?.classList.toggle('btn-primary', view === 'compact');

    tableView?.classList.toggle('hidden', view !== 'table');
    compactView?.classList.toggle('hidden', view !== 'compact');

    if (filteredAuthors.length) {
        displayAuthors(filteredAuthors);
    }
}

function toggleState(state, errorMessage) {
    const loading = document.getElementById('loadingState');
    const error = document.getElementById('errorState');
    const empty = document.getElementById('emptyState');
    const content = document.getElementById('authorsContent');

    if (state === 'loading') {
        loading?.classList.remove('hidden');
    } else {
        loading?.classList.add('hidden');
    }

    if (state === 'error') {
        error?.classList.remove('hidden');
        if (errorMessage) {
            const errorText = document.getElementById('errorMessage');
            if (errorText) {
                errorText.textContent = errorMessage;
            }
        }
    } else {
        error?.classList.add('hidden');
    }

    if (state === 'empty') {
        empty?.classList.remove('hidden');
    } else {
        empty?.classList.add('hidden');
    }

    if (state === 'content') {
        content?.classList.remove('hidden');
    } else if (state !== 'content') {
        content?.classList.add('hidden');
    }
}

function openAuthorDetail(authorName) {
    if (!authorName) {
        return;
    }
    window.location.href = `/authors/${encodeURIComponent(authorName)}`;
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

window.setView = setView;
window.loadAuthors = loadAuthors;

