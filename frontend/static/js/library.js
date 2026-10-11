
const LibraryState = {
    selectedBooks: new Set(),
    isSelectMode: false,
    viewMode: 'grid',
    currentFilters: {
        search: '',
        status: '',
        source: '',
        genre: '',
        sort: 'title'
    },
    filteredIds: new Set(),
    books: [],
    filteredBooks: []
};

const LazyState = {
    batchSize: 48,
    renderedCount: 0,
    observer: null,
    loading: false,
    cooldownMs: 100,
    pendingTimer: null
};

// --- DOM ready --------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    initializeFilters();
    initializeSelectionMode();
    initializeBookCards();
    initializeBulkActions();
    initializeSizeSlider();
    initializeViewModes();
    initializeLazyGrid();

    const searchResultsSort = document.getElementById('searchResultsSort');
    if (searchResultsSort) {
        searchResultsSort.addEventListener('change', handleSearchResultsSortChange);
    }

    const params = new URLSearchParams(window.location.search);
    const bookParam = params.get('book');
    if (bookParam) {
        const bookId = parseInt(bookParam, 10);
        if (!isNaN(bookId)) {
            setTimeout(() => openBookModal(bookId), 300);
        }
        history.replaceState(null, '', window.location.pathname);
    }
});

// --- Size slider ------------------------------------------------------------

function initializeSizeSlider() {
    const sizeSlider = document.getElementById('sizeSlider');
    const booksGrid = document.getElementById('booksGrid');
    
    if (!sizeSlider || !booksGrid) return;
    
    const savedSize = localStorage.getItem('libraryCardSize') || '50';
    sizeSlider.value = savedSize;
    applySizeClass(parseInt(savedSize));
    
    sizeSlider.addEventListener('input', (e) => {
        const value = parseInt(e.target.value);
        applySizeClass(value);
        localStorage.setItem('libraryCardSize', value);
    });
}

function applySizeClass(value) {
    const booksGrid = document.getElementById('booksGrid');
    if (!booksGrid) return;
    
    booksGrid.classList.remove('size-xs', 'size-sm', 'size-md', 'size-lg', 'size-xl');
    
    if (value <= 20) {
        booksGrid.classList.add('size-xs');
    } else if (value <= 40) {
        booksGrid.classList.add('size-sm');
    } else if (value <= 60) {
        booksGrid.classList.add('size-md');
    } else if (value <= 80) {
        booksGrid.classList.add('size-lg');
    } else {
        booksGrid.classList.add('size-xl');
    }
}

// --- Filter system ----------------------------------------------------------

function initializeFilters() {
    const searchInput = document.getElementById('searchInput');
    const statusFilter = document.getElementById('statusFilter');
    const sourceFilter = document.getElementById('sourceFilter');
    const genreFilter = document.getElementById('genreFilter');
    const sortFilter = document.getElementById('sortFilter');
    const libraryFilter = document.getElementById('libraryFilter');
    const listeningFilter = document.getElementById('listeningFilter');
    const clearBtn = document.getElementById('clearFiltersBtn');

    libraryFilter?.addEventListener('change', (e) => {
        LibraryState.currentFilters.library = e.target.value;
        applyFilters();
    });

    listeningFilter?.addEventListener('change', (e) => {
        LibraryState.currentFilters.listening = e.target.value;
        applyFilters();
    });

    let searchTimeout;
    searchInput?.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            LibraryState.currentFilters.search = e.target.value.toLowerCase();
            applyFilters();
        }, 300);
    });

    statusFilter?.addEventListener('change', (e) => {
        LibraryState.currentFilters.status = e.target.value;
        applyFilters();
    });

    sourceFilter?.addEventListener('change', (e) => {
        LibraryState.currentFilters.source = e.target.value;
        applyFilters();
    });

    genreFilter?.addEventListener('change', (e) => {
        LibraryState.currentFilters.genre = e.target.value;
        applyFilters();
    });

    sortFilter?.addEventListener('change', (e) => {
        LibraryState.currentFilters.sort = e.target.value;
        applyFilters();
    });

    clearBtn?.addEventListener('click', () => {
        searchInput.value = '';
        statusFilter.value = '';
        if (sourceFilter) sourceFilter.value = '';
        if (libraryFilter) libraryFilter.value = '';
        if (listeningFilter) listeningFilter.value = '';
        genreFilter.value = '';
        sortFilter.value = 'title';
        
        LibraryState.currentFilters = {
            search: '',
            status: '',
            source: '',
            genre: '',
            library: '',
            listening: '',
            sort: 'title'
        };
        
        applyFilters();
        showNotification('Filters cleared', 'info');
    });
}

function applyFilters() {
    const searchTerm = (LibraryState.currentFilters.search || '').toLowerCase();
    const statusFilter = LibraryState.currentFilters.status || '';
    const sourceFilter = LibraryState.currentFilters.source || '';
    const genreFilter = LibraryState.currentFilters.genre || '';
    const libraryFilter = LibraryState.currentFilters.library || '';
    const listeningFilter = LibraryState.currentFilters.listening || '';
    const sortBy = LibraryState.currentFilters.sort;

    LibraryState.filteredIds = new Set();

    const filtered = LibraryState.books.filter(book => {
        const title = book.title_lc || '';
        const author = book.author_lc || '';
        const status = book.library_status || 'unknown';
        const genre = book.genre || 'unknown';

        const matchesSearch = !searchTerm || title.includes(searchTerm) || author.includes(searchTerm);
        const matchesStatus = !statusFilter || status === statusFilter;
        const matchesSource = !sourceFilter
            || (sourceFilter === 'audible' && book.in_audible === true)
            || (sourceFilter === 'not_audible' && book.in_audible !== true);
        const matchesGenre = !genreFilter || genre === genreFilter;
        const matchesLibrary = !libraryFilter || String(book.library_id) === libraryFilter;
        const matchesListening = !listeningFilter || (book.abs_linked && book.abs_state === listeningFilter);

        if (matchesSearch && matchesStatus && matchesSource && matchesGenre && matchesLibrary && matchesListening) {
            if (book.id !== undefined && book.id !== null) {
                LibraryState.filteredIds.add(String(book.id));
            }
            return true;
        }
        return false;
    });

    filtered.sort((a, b) => {
        switch (sortBy) {
            case 'author':
                return (a.author || '').localeCompare(b.author || '');
            case 'rating':
                return (parseFloat(b.rating) || 0) - (parseFloat(a.rating) || 0);
            case 'date':
                return (b.date_added || '').localeCompare(a.date_added || '');
            case 'title':
            default:
                return (a.title || '').localeCompare(b.title || '');
        }
    });

    LibraryState.filteredBooks = filtered;

    if (LibraryState.viewMode === 'table') {
        if (LazyState.observer) LazyState.observer.disconnect();
        renderTableView();
        return;
    }

    if (LibraryState.viewMode === 'list') {
        if (LazyState.observer) LazyState.observer.disconnect();
        renderListView();
        return;
    }

    LazyState.renderedCount = 0;

    const grid = document.getElementById('booksGrid');
    if (grid) {
        grid.innerHTML = '';
    }

    const sentinel = document.getElementById('lazySentinel');
    if (LazyState.observer && sentinel) {
        LazyState.observer.disconnect();
        LazyState.observer.observe(sentinel);
    }

    requestNextBatch(true);
}

function initializeLazyGrid() {
    if (!Array.isArray(window.libraryBooks)) {
        window.libraryBooks = [];
    }

    LibraryState.books = window.libraryBooks.map(book => ({
        ...book,
        title_lc: (book.title || '').toLowerCase(),
        author_lc: (book.author || '').toLowerCase()
    }));
    LibraryState.filteredBooks = LibraryState.books;


    const sentinel = document.getElementById('lazySentinel');
    if (sentinel) {
        LazyState.observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    requestNextBatch();
                }
            });
        }, { root: null, rootMargin: '200px', threshold: 0 });
        LazyState.observer.observe(sentinel);
    }

    applyFilters();
}

function requestNextBatch(immediate = false) {
    if (LazyState.loading) return;
    if (LazyState.pendingTimer) return;

    if (immediate) {
        renderNextBatch();
        return;
    }

    LazyState.pendingTimer = setTimeout(() => {
        LazyState.pendingTimer = null;
        renderNextBatch();
    }, LazyState.cooldownMs);
}

function showLoader() {
    const loader = document.getElementById('lazyLoader');
    if (loader) loader.classList.remove('hidden');
}

function hideLoader() {
    const loader = document.getElementById('lazyLoader');
    if (loader) loader.classList.add('hidden');
}

function setEmptyState(visible) {
    const empty = document.getElementById('emptyState');
    const grid = document.getElementById('booksGrid');
    if (empty) {
        if (visible) {
            empty.classList.remove('hidden');
        } else {
            empty.classList.add('hidden');
        }
    }
    if (grid && visible) {
        grid.innerHTML = '';
    }
}

function renderNextBatch() {
    if (LibraryState.viewMode !== 'grid') return;
    if (LazyState.loading) return;

    const grid = document.getElementById('booksGrid');
    if (!grid) return;

    if (!LibraryState.filteredBooks || LibraryState.filteredBooks.length === 0) {
        setEmptyState(true);
        hideLoader();
        if (LazyState.observer) {
            LazyState.observer.disconnect();
        }
        return;
    }

    setEmptyState(false);

    if (LazyState.renderedCount >= LibraryState.filteredBooks.length) {
        hideLoader();
        if (LazyState.observer) {
            LazyState.observer.disconnect();
        }
        return;
    }

    LazyState.loading = true;
    showLoader();

    try {
        const start = LazyState.renderedCount;
        const end = Math.min(start + LazyState.batchSize, LibraryState.filteredBooks.length);
        const frag = document.createDocumentFragment();

        for (let i = start; i < end; i++) {
            const card = createBookCard(LibraryState.filteredBooks[i]);
            if (card) {
                frag.appendChild(card);
            }
        }

        grid.appendChild(frag);
        LazyState.renderedCount = end;
    } catch (err) {
        setEmptyState(true);
        if (LazyState.observer) {
            LazyState.observer.disconnect();
        }
    } finally {
        LazyState.loading = false;
        hideLoader();

        if (LazyState.pendingTimer) {
            clearTimeout(LazyState.pendingTimer);
            LazyState.pendingTimer = null;
        }
    }

    if (LazyState.renderedCount >= LibraryState.filteredBooks.length) {
        hideLoader();
        if (LazyState.observer) {
            LazyState.observer.disconnect();
        }
    }
}

function showLibraryLabels() {
    return (window.libraryLibraries || []).length > 1;
}

// Audiobookshelf listening state on a cover: a tick when finished, a ring while in progress
function listeningBadge(book) {
    if (!book.abs_linked || book.abs_state === 'not_started') return '';
    if (book.abs_state === 'finished') {
        return '<div class="abs-badge abs-badge--done" title="Finished in Audiobookshelf"><i class="fas fa-check"></i></div>';
    }
    const pct = Math.max(2, Math.min(99, Math.round((book.abs_progress || 0) * 100)));
    return `<div class="abs-badge abs-badge--ring" style="--p:${pct}" title="${pct}% listened in Audiobookshelf"></div>`;
}

function createBookCard(book) {
    if (!book || book.id === undefined || book.id === null) return null;

    const titleLc = book.title_lc || (book.title || '').toLowerCase();
    const authorLc = book.author_lc || (book.author || '').toLowerCase();

    const card = document.createElement('div');
    card.className = 'cursor-pointer book-card group relative';
    card.dataset.bookId = book.id;
    card.dataset.status = book.library_status || 'unknown';
    card.dataset.genre = book.genre || 'unknown';
    card.dataset.title = titleLc;
    card.dataset.author = authorLc;

    const rating = book.rating ? Number(book.rating).toFixed(1) : null;
    const numRatings = book.num_ratings ? `(${book.num_ratings})` : '';
    const hasRating = rating && rating !== 'NaN';

    card.innerHTML = `
        <div class="book-cover-container relative bg-base-300 rounded-sm overflow-hidden shadow-md hover:shadow-lg transition-all duration-200 aspect-square">
            ${book.cover_image ? `
                <img src="${escapeHtml(book.cover_image)}" 
                     alt="${escapeHtml(book.title || '')}" 
                     class="w-full h-full object-cover"
                     loading="lazy"
                     decoding="async"
                     width="225"
                     height="225"
                     onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                <div class="w-full h-full hidden items-center justify-center text-base-content/30 text-xs font-bold flex-col gap-2" style="display:none;">
                    <i class="fas fa-book text-3xl"></i>
                    <span>NO COVER</span>
                </div>` : `
                <div class="w-full h-full flex items-center justify-center text-base-content/30 text-xs font-bold flex-col gap-2">
                    <i class="fas fa-book text-3xl"></i>
                    <span>NO COVER</span>
                </div>`}

            ${listeningBadge(book)}

            <div class="absolute top-2 left-2 selection-checkbox hidden">
                <input type="checkbox" class="checkbox checkbox-primary checkbox-sm shadow-lg bg-base-100" aria-label="Select ${escapeHtml(book.title || 'book')}" />
            </div>

            <div class="absolute inset-0 bg-gradient-to-t from-black/80 via-black/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex items-end justify-center pb-3">
                <div class="flex gap-1">
                    <button class="btn btn-soft btn-primary btn-xs shadow-lg" onclick="event.stopPropagation(); openBookModal(${book.id})" title="Book Details">
                        <i class="fas fa-info"></i>
                    </button>
                    <button class="btn btn-soft btn-accent btn-xs shadow-lg" onclick="event.stopPropagation(); searchForBook(${book.id}, this.dataset.title, this.dataset.author)" data-title="${escapeHtml(book.title || '')}" data-author="${escapeHtml(book.author || '')}" title="Interactive Download">
                        <i class="fas fa-download"></i>
                    </button>
                    <button class="btn btn-soft btn-warning btn-xs shadow-lg" onclick="event.stopPropagation(); autoDownloadBook(${book.id}, this)" title="Auto Download">
                        <i class="fas fa-bolt"></i>
                    </button>
                </div>
            </div>
        </div>

        <div class="mt-1.5">
            <h3 class="text-sm line-clamp-2 leading-tight font-semibold text-base-content">${escapeHtml(book.title || '')}</h3>
            <p class="text-xs text-base-content/60 line-clamp-1">${escapeHtml(book.author || '')}</p>
            ${showLibraryLabels() && book.library_name ? `<p class="lib-chip">${escapeHtml(book.library_name)}</p>` : ''}
            ${hasRating ? `<div class="flex items-center gap-1 mt-0.5 text-xs text-base-content/60">
                <i class="fas fa-star text-warning text-[10px]"></i>
                <span>${escapeHtml(rating)}</span>
                ${numRatings ? `<span class="text-base-content/50">${escapeHtml(numRatings)}</span>` : ''}
            </div>` : ''}
        </div>
    `;

    return card;
}

// --- Selection mode ---------------------------------------------------------

function initializeSelectionMode() {
    const selectModeBtn = document.getElementById('selectModeBtn');
    
    selectModeBtn?.addEventListener('click', () => {
        toggleSelectionMode();
    });
}

function toggleSelectionMode() {
    LibraryState.isSelectMode = !LibraryState.isSelectMode;
    const grid = document.getElementById('booksGrid');
    const listWrap = document.getElementById('booksListWrap');
    const tableWrap = document.getElementById('booksTableWrap');
    const toolbar = document.getElementById('selectionToolbar');
    const selectModeBtn = document.getElementById('selectModeBtn');

    if (LibraryState.isSelectMode) {
        grid?.classList.add('selection-mode');
        listWrap?.classList.add('selection-mode');
        tableWrap?.classList.add('selection-mode');
        toolbar.classList.remove('hidden');
        toolbar.setAttribute('aria-hidden', 'false');
        selectModeBtn.innerHTML = '<i class="fas fa-times"></i> Cancel';
        selectModeBtn.classList.add('btn-error');
        selectModeBtn.classList.remove('btn-primary');
    } else {
        grid?.classList.remove('selection-mode');
        listWrap?.classList.remove('selection-mode');
        tableWrap?.classList.remove('selection-mode');
        toolbar.classList.add('hidden');
        toolbar.setAttribute('aria-hidden', 'true');
        selectModeBtn.innerHTML = '<i class="fas fa-check-square"></i> Select';
        selectModeBtn.classList.remove('btn-error');
        selectModeBtn.classList.add('btn-primary');
        clearSelection();
    }
}

function clearSelection() {
    LibraryState.selectedBooks.clear();
    document.querySelectorAll('.book-card, tr.lib-table-row, tr.lib-list-row').forEach(card => {
        card.classList.remove('selected');
        const checkbox = card.querySelector('.selection-checkbox input, input[type="checkbox"]');
        if (checkbox) checkbox.checked = false;
    });
    updateSelectionCount();
}

function updateSelectionCount() {
    const count = LibraryState.selectedBooks.size;
    const countElement = document.getElementById('selectedCount');
    if (countElement) {
        countElement.textContent = `${count} book${count !== 1 ? 's' : ''} selected`;
    }
}

// --- Book cards interaction -------------------------------------------------

function initializeBookCards() {
    const grid = document.getElementById('booksGrid');
    if (!grid) return;

    grid.addEventListener('click', (e) => {
        const card = e.target.closest('.book-card');
        if (!card) return;
        const bookId = card.dataset.bookId;

        if (LibraryState.isSelectMode) {
            e.preventDefault();
            toggleBookSelection(card, bookId);
        } else {
            openBookModal(bookId);
        }
    });
}

function toggleBookSelection(card, bookId) {
    const checkbox = card.querySelector('.selection-checkbox input');
    
    if (LibraryState.selectedBooks.has(bookId)) {
        LibraryState.selectedBooks.delete(bookId);
        card.classList.remove('selected');
        if (checkbox) checkbox.checked = false;
    } else {
        LibraryState.selectedBooks.add(bookId);
        card.classList.add('selected');
        if (checkbox) checkbox.checked = true;
    }
    
    updateSelectionCount();
}

async function openBookModal(bookId) {
    try {
        const response = await fetch(`/library/book/${bookId}`);
        const data = await response.json();
        
        if (data.success && data.book) {
            displayBookModal(data.book);
        } else {
            showNotification('Failed to load book details', 'error');
        }
    } catch (error) {
        showNotification('Error loading book details', 'error');
    }
}

async function toggleBookMonitored(checkbox) {
    const bookId = Number.parseInt(checkbox.dataset.bookId, 10);
    const monitored = checkbox.checked;
    checkbox.disabled = true;

    try {
        const response = await fetch(`/library/book/${bookId}/monitored`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ monitored })
        });
        const data = await response.json();

        if (!response.ok || data.success === false) {
            throw new Error(data.error || 'Failed to update monitored status');
        }

        const book = LibraryState.books.find(b => String(b.id) === String(bookId));
        if (book) {
            book.monitored = monitored;
        }
        showNotification(monitored ? 'Book is now monitored' : 'Book excluded from automatic search', 'success');
    } catch (error) {
        checkbox.checked = !monitored;
        showNotification(error.message || 'Failed to update monitored status', 'error');
    } finally {
        checkbox.disabled = false;
    }
}

function displayBookModal(book) {
    const modal = document.getElementById('bookModal');
    const content = document.getElementById('modalContent');
    const e = AA.escapeHtml;

    const rawStatus = (book.library_status || '').toLowerCase();
    const statusMap = {
        owned:   ['badge-success', 'Owned',   'downloaded'],
        wanted:  ['badge-ghost',   'Wanted',  'wanted'],
        missing: ['badge-error',   'Missing', 'wanted'],
    };
    const [statusCls, statusText, tintClass] = statusMap[rawStatus] || ['badge-ghost', book.status || '', 'wanted'];
    const statusBadge = statusText
        ? `<span class="badge ${statusCls} badge-sm">${e(statusText)}</span>`
        : '';

    const isMonitored = book.monitored !== false;
    const monitoredToggle = `
        <label class="flex items-center gap-2 cursor-pointer select-none" title="When off, this book is excluded from automatic search">
            <input type="checkbox" class="toggle toggle-success toggle-sm" id="bookMonitoredToggle"
                   data-book-id="${e(String(book.id))}" ${isMonitored ? 'checked' : ''}
                   onchange="toggleBookMonitored(this)" />
            <span class="text-xs text-base-content/60">Monitored</span>
        </label>`;

    const coverUrl = book.cover_image || '';
    const coverHtml = coverUrl
        ? `<img src="${e(coverUrl)}" alt="${e(book.title || '')}" class="w-full h-full object-cover" onerror="this.style.display='none'" />`
        : `<div class="w-full h-full flex items-center justify-center"><i class="fas fa-book text-5xl opacity-20"></i></div>`;

    const ratingValue = parseFloat(book.rating) || 0;
    const ratingHtml = ratingValue > 0 ? `
        <div class="bd-rating">
            <i class="fas fa-star text-warning text-xs"></i>
            <span style="color:var(--bc);font-weight:600;">${ratingValue.toFixed(1)}</span>
            ${book.num_ratings && book.num_ratings > 0 ? `<span>(${Number(book.num_ratings).toLocaleString()} ratings)</span>` : ''}
        </div>` : '';

    const hasSeries = book.series && book.series !== 'N/A';
    const seriesText = hasSeries
        ? `${e(book.series)}${book.sequence && book.sequence !== 'N/A' && book.sequence !== '' ? ' #' + e(book.sequence) : ''}`
        : '';

    const seriesTab = hasSeries && book.series_asin
        ? `<button role="tab" class="bd-tab" onclick="bookModalSwitchTab('series', this)">
               <i class="fas fa-layer-group"></i>Series
           </button>`
        : '';
    const seriesPanel = hasSeries && book.series_asin
        ? `<div id="bookTab-series" class="book-tab-panel tab-scroll pt-1"
               data-series-asin="${e(book.series_asin)}" data-loaded="false">
               <div class="flex justify-center py-8"><span class="loading loading-spinner loading-md"></span></div>
           </div>`
        : '';

    const hardcoverTab = book.hardcover_linked
        ? `<button role="tab" class="bd-tab" onclick="bookModalSwitchTab('hardcover', this)">
               <i class="fas fa-book-open"></i>Hardcover
           </button>`
        : '';
    const hardcoverPanel = book.hardcover_linked
        ? `<div id="bookTab-hardcover" class="book-tab-panel tab-scroll pt-1"
               data-book-id="${book.id}" data-loaded="false">
               <div class="flex justify-center py-8"><span class="loading loading-spinner loading-md"></span></div>
           </div>`
        : '';

    const synopsisHtml = book.summary
        ? `<div>
            <h3 class="text-[10px] font-bold uppercase tracking-widest text-base-content/35 mb-2">Synopsis</h3>
            <div class="text-sm text-base-content/70 leading-relaxed space-y-3">${
                book.summary
                    .split(/<\/?\s*p\s*>/i)
                    .map(s => s.replace(/<[^>]*>/g, '').trim())
                    .filter(s => s.length > 0)
                    .map(s => `<p>${e(s)}</p>`)
                    .join('')
            }</div>
           </div>`
        : `<p class="text-sm text-base-content/40">No synopsis available.</p>`;

    const filesHtml = book.file_location
        ? `<div class="bg-base-200 border border-base-300 overflow-hidden" style="border-radius:0.25rem;">
               <div class="flex items-start gap-3 p-4">
                   <div class="w-8 h-8 bg-success/10 flex items-center justify-center flex-shrink-0 mt-0.5" style="border-radius:0.25rem;">
                       <i class="fas fa-file-audio text-success text-sm"></i>
                   </div>
                   <div class="min-w-0">
                       <p class="text-[10px] text-base-content/40 uppercase tracking-wider mb-0.5">File Location</p>
                       <p class="text-xs font-mono text-base-content/65 break-all leading-relaxed">${e(book.file_location)}</p>
                   </div>
               </div>
           </div>`
        : `<p class="text-sm text-base-content/40">No file location recorded.</p>`;

    const factRows = [
        book.narrator && book.narrator !== 'Unknown'
            ? `<div class="fact-card"><dt><i class="fas fa-microphone mr-1.5 opacity-50"></i>Narrator</dt><dd>${e(book.narrator)}</dd></div>` : '',
        book.runtime
            ? `<div class="fact-card"><dt><i class="fas fa-clock mr-1.5 opacity-50"></i>Runtime</dt><dd>${e(book.runtime)}</dd></div>` : '',
        hasSeries
            ? `<div class="fact-card"><dt><i class="fas fa-layer-group mr-1.5 opacity-50"></i>Series</dt><dd>${e(book.series)}</dd></div>` : '',
        hasSeries && book.sequence && book.sequence !== 'N/A' && book.sequence !== ''
            ? `<div class="fact-card"><dt><i class="fas fa-hashtag mr-1.5 opacity-50"></i>Book</dt><dd>#${e(book.sequence)}</dd></div>` : '',
        book.release_date && book.release_date !== 'Unknown'
            ? `<div class="fact-card"><dt><i class="fas fa-calendar mr-1.5 opacity-50"></i>Released</dt><dd>${e(AA.formatDate(book.release_date))}</dd></div>` : '',
        book.publisher && book.publisher !== 'Unknown'
            ? `<div class="fact-card"><dt><i class="fas fa-building mr-1.5 opacity-50"></i>Publisher</dt><dd>${e(book.publisher)}</dd></div>` : '',
        book.language
            ? `<div class="fact-card"><dt><i class="fas fa-globe mr-1.5 opacity-50"></i>Language</dt><dd>${e(book.language)}</dd></div>` : '',
        book.asin
            ? `<div class="fact-card"><dt><i class="fas fa-fingerprint mr-1.5 opacity-50"></i>ASIN</dt><dd class="font-mono text-[0.65rem]">${e(book.asin)}</dd></div>` : '',
        (book.libraries || []).length > 1 && book.library_name
            ? `<div class="fact-card"><dt><i class="fas fa-book mr-1.5 opacity-50"></i>Library</dt><dd>${e(book.library_name)}</dd></div>` : '',
    ].filter(Boolean).join('');

    const abs = book.abs || null;
    const playButton = abs && abs.play_url
        ? `<a class="bd-iconbtn" title="Play in Audiobookshelf" href="${e(abs.play_url)}" target="_blank" rel="noopener"><i class="fas fa-headphones"></i></a>`
        : '';
    const listeningHtml = abs && abs.progress && abs.progress.length
        ? `<p class="bd-right-h" style="margin-top:14px;">Listening</p>
           <div class="abs-listen">${abs.progress.map((p) => {
               const pct = Math.round((p.progress || 0) * 100);
               const label = p.finished ? 'Finished' : `${pct}%`;
               return `<div class="abs-listen-row"><span>${e(p.user || '')}</span><span>${e(label)}</span>
                       <div class="abs-listen-bar"><i style="width:${p.finished ? 100 : pct}%"></i></div></div>`;
           }).join('')}</div>`
        : '';

    const otherLibraries = (book.libraries || []).filter((l) => l.enabled && String(l.id) !== String(book.library_id));
    const moveHtml = book.file_location && rawStatus === 'owned' && otherLibraries.length
        ? `<div class="bg-base-200 border border-base-300 p-4" style="border-radius:0.25rem;">
               <p class="text-[10px] text-base-content/40 uppercase tracking-wider mb-2">Library${book.library_name ? `: ${e(book.library_name)}` : ''}</p>
               <div class="flex flex-wrap items-center gap-2">
                   <select id="bookMoveTarget" class="select select-bordered select-sm">${otherLibraries.map((l) => `<option value="${l.id}">${e(l.name)}</option>`).join('')}</select>
                   <button class="btn btn-outline btn-sm" onclick="previewBookMove('${e(book.asin || '')}')">Move to library…</button>
               </div>
               <p id="bookMoveStatus" class="text-xs text-base-content/60 mt-2"></p>
           </div>`
        : '';

    const enabledLibraries = (book.libraries || []).filter((l) => l.enabled);
    const chooseHtml = rawStatus === 'wanted' && enabledLibraries.length > 1
        ? `<div class="bg-base-200 border border-base-300 p-4" style="border-radius:0.25rem;">
               <p class="text-[10px] text-base-content/40 uppercase tracking-wider mb-2">Library</p>
               <select id="bookLibraryChoice" class="select select-bordered select-sm" onchange="chooseBookLibrary('${e(book.asin || '')}', this)">
                   <option value="">Automatic (routing rules)</option>
                   ${enabledLibraries.map((l) => `<option value="${l.id}" ${String(l.id) === String(book.library_id) ? 'selected' : ''}>${e(l.name)}</option>`).join('')}
               </select>
               <p id="bookLibraryStatus" class="text-xs text-base-content/60 mt-2">Where this book goes when it's downloaded</p>
           </div>`
        : '';

    content.innerHTML = `
        <!-- HEADER (flat) -->
        <div class="bd-top">
            <button class="bd-x" onclick="document.getElementById('bookModal').close()"><i class="fas fa-xmark"></i></button>
            <div class="bd-cover">${coverHtml}</div>
            <div class="bd-id">
                <div class="bd-meta">${statusBadge}${monitoredToggle}</div>
                <h1 class="bd-title">${e(book.title || '')}</h1>
                <p class="bd-by">by <a href="/authors/${encodeURIComponent(book.author || '')}">${e(book.author || '')}</a>${hasSeries ? ` <span class="bd-series">&middot; ${seriesText}</span>` : ''}</p>
                ${ratingHtml}
                <div class="bd-actions">
                    <button class="btn btn-primary btn-sm gap-1.5" onclick="autoDownloadBook(${book.id}, this)">
                        <i class="fas fa-bolt text-xs"></i> Download
                    </button>
                    <button class="bd-iconbtn" title="Interactive Search"
                            data-title="${e(book.title || '')}" data-author="${e(book.author || '')}"
                            onclick="searchForBook(${book.id}, this.dataset.title, this.dataset.author)">
                        <i class="fas fa-search"></i>
                    </button>
                    <button class="bd-iconbtn" title="Refresh Metadata" onclick="updateBookMetadata(${book.id})">
                        <i class="fas fa-rotate"></i>
                    </button>
                    ${playButton}
                    <div class="dropdown dropdown-end">
                        <button tabindex="0" class="bd-iconbtn"><i class="fas fa-ellipsis-v"></i></button>
                        <ul tabindex="0" class="dropdown-content menu menu-sm bg-base-200 shadow-xl w-44 mt-1 z-[100] border border-base-300 p-1" style="border-radius:0.25rem;">
                            ${book.hardcover_connected ? (book.hardcover_linked
                                ? `<li><a class="text-xs" onclick="unlinkBookFromHardcover(${book.id})">Unlink from Hardcover</a></li>`
                                : `<li><a class="text-xs" onclick="matchBookToHardcover(${book.id})">Match to Hardcover</a></li>`) : ''}
                            <li><a class="text-error text-xs" onclick="deleteBook(${book.id})"><i class="fas fa-trash mr-2"></i>Delete Book</a></li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>

        <!-- BODY -->
        <div class="bd-body">
            <!-- Left: Tabs -->
            <div class="bd-left">
                <div role="tablist" class="bd-tabs">
                    <button role="tab" class="bd-tab tab-active" onclick="bookModalSwitchTab('overview', this)"><i class="fas fa-align-left"></i>Overview</button>
                    <button role="tab" class="bd-tab" onclick="bookModalSwitchTab('files', this)"><i class="fas fa-folder"></i>Files</button>
                    ${seriesTab}
                    ${hardcoverTab}
                    <button role="tab" class="bd-tab" onclick="bookModalSwitchTab('history', this)"><i class="fas fa-clock-rotate-left"></i>History</button>
                </div>

                <div id="bookTab-overview" class="book-tab-panel book-tab-active tab-scroll pt-1 space-y-4">
                    ${synopsisHtml}
                </div>
                <div id="bookTab-files" class="book-tab-panel tab-scroll pt-1 space-y-3">
                    ${filesHtml}
                    ${moveHtml}
                    ${chooseHtml}
                </div>
                ${seriesPanel}
                ${hardcoverPanel}
                <div id="bookTab-history" class="book-tab-panel tab-scroll pt-1"
                     data-book-id="${book.id}" data-loaded="false">
                    <div class="flex justify-center py-8"><span class="loading loading-spinner loading-md"></span></div>
                </div>
            </div>

            <!-- Right: Details Sidebar -->
            <div class="bd-right">
                <p class="bd-right-h">Details</p>
                <div>${factRows}</div>
                ${listeningHtml}
            </div>
        </div>
    `;

    modal.showModal();
}

async function previewBookMove(asin) {
    const target = document.getElementById('bookMoveTarget');
    const status = document.getElementById('bookMoveStatus');
    if (!target || !asin) return;
    status.textContent = 'Checking…';
    try {
        const preview = await AA.apiFetch(`/api/libraries/move-preview?asin=${encodeURIComponent(asin)}&library_id=${encodeURIComponent(target.value)}`, { method: 'GET' });
        if (!preview.ok || !preview.data || preview.data.success === false) {
            throw new Error((preview.data && preview.data.error) || 'This book can\'t be moved');
        }
        status.textContent = '';
        const ok = await AA.showConfirmDialog(
            `Move this book's folder to ${preview.data.library}?\n\nNew location: ${preview.data.destination}`,
            'Move to another library', 'Move', 'btn-primary');
        if (!ok) return;
        status.textContent = 'Moving files…';
        const moved = await AA.apiFetch('/api/libraries/move', { method: 'POST', body: JSON.stringify({ asin, library_id: Number(target.value) }) });
        if (!moved.ok || !moved.data || moved.data.success === false) {
            throw new Error((moved.data && moved.data.error) || 'Move failed');
        }
        status.textContent = `Moved to ${moved.data.library}`;
        showNotification(`Moved to ${moved.data.library}`, 'success');
        const book = LibraryState.books.find((b) => b.asin === asin);
        if (book) {
            const lib = (window.libraryLibraries || []).find((l) => l.name === moved.data.library);
            book.library_id = lib ? lib.id : book.library_id;
            book.library_name = moved.data.library;
            book.file_location = moved.data.file_path;
        }
    } catch (error) {
        status.textContent = error.message;
        status.className = 'text-xs text-error mt-2';
    }
}

async function chooseBookLibrary(asin, select) {
    const status = document.getElementById('bookLibraryStatus');
    if (!asin || !select) return;
    select.disabled = true;
    status.className = 'text-xs text-base-content/60 mt-2';
    status.textContent = 'Saving…';
    try {
        const libraryId = select.value ? Number(select.value) : null;
        const saved = await AA.apiFetch('/api/libraries/book-target', { method: 'POST', body: JSON.stringify({ asin, library_id: libraryId }) });
        if (!saved.ok || !saved.data || saved.data.success === false) {
            throw new Error((saved.data && saved.data.error) || 'Could not save the library');
        }
        const library = saved.data.library;
        status.textContent = library ? `Goes to ${library.name}` : 'Goes where the routing rules decide';
        const book = LibraryState.books.find((b) => b.asin === asin);
        if (book) {
            book.library_id = library ? library.id : null;
            book.library_name = library ? library.name : null;
        }
    } catch (error) {
        status.textContent = error.message;
        status.className = 'text-xs text-error mt-2';
    } finally {
        select.disabled = false;
    }
}

function bookModalSwitchTab(name, btn) {
    document.querySelectorAll('#modalContent .book-tab-panel')
        .forEach(p => p.classList.remove('book-tab-active'));
    document.querySelectorAll('#modalContent [role="tab"]')
        .forEach(t => t.classList.remove('tab-active'));
    document.getElementById('bookTab-' + name).classList.add('book-tab-active');
    btn.classList.add('tab-active');

    if (name === 'series') {
        const panel = document.getElementById('bookTab-series');
        if (panel && panel.dataset.loaded === 'false') {
            bookModalLoadSeriesTab(panel, panel.dataset.seriesAsin);
        }
    }
    if (name === 'history') {
        const panel = document.getElementById('bookTab-history');
        if (panel && panel.dataset.loaded === 'false') {
            bookModalLoadHistoryTab(panel, panel.dataset.bookId);
        }
    }
    if (name === 'hardcover') {
        const panel = document.getElementById('bookTab-hardcover');
        if (panel && panel.dataset.loaded === 'false') {
            bookModalLoadHardcoverTab(panel, panel.dataset.bookId);
        }
    }
}

async function bookModalLoadSeriesTab(panel, seriesAsin) {
    const e = AA.escapeHtml;
    try {
        const res = await fetch(`/series/api/${encodeURIComponent(seriesAsin)}/books`);
        const data = await res.json();
        panel.dataset.loaded = 'true';

        if (!data.success) {
            panel.innerHTML = `<p class="text-sm text-error">Failed to load series data.</p>`;
            return;
        }

        const books = Array.isArray(data.books) ? data.books : [];
        const stats = data.statistics || {};
        const importAuthor = data.primary_author || '';

        const rows = books.length ? books.map(book => {
            const isOwned = book.in_library || ['in_library','owned'].includes((book.library_status||'').toLowerCase());
            const isPending = ['wanted','queued','pending'].includes((book.library_status||'').toLowerCase());
            let badge;
            if (isOwned)        badge = '<span class="badge badge-success badge-xs">In Library</span>';
            else if (isPending) badge = '<span class="badge badge-warning badge-xs">Wanted</span>';
            else                badge = '<span class="badge badge-ghost badge-xs">Not Owned</span>';

            const titleCell = isOwned && book.asin
                ? `<button class="text-sm text-left hover:underline text-primary" onclick="openBookModal('${e(book.asin)}')">${e(book.title || 'Unknown')}</button>`
                : `<span class="text-sm">${e(book.title || 'Unknown')}</span>`;

            const bookAuthor = book.author || importAuthor;
            const actionCell = !isOwned && !isPending && book.asin
                ? `<button class="btn btn-xs btn-soft btn-primary js-series-tab-import"
                       data-asin="${e(book.asin)}" data-author="${e(bookAuthor)}" data-title="${e(book.title || '')}"
                       data-series-asin="${e(seriesAsin)}">
                       <i class="fas fa-download"></i> Add
                   </button>`
                : '';

            return `<tr class="hover">
                <td class="text-xs font-semibold w-10">${e(String(book.sequence || '—'))}</td>
                <td>${titleCell}</td>
                <td>${badge}</td>
                <td class="text-right">
                    ${(book.rating && book.rating !== 'N/A')
                        ? `<span class="text-xs">${e(String(book.rating))} <i class="fas fa-star text-warning text-[10px]"></i></span>`
                        : '<span class="text-xs opacity-40">—</span>'
                    }
                </td>
                <td class="text-right">${actionCell}</td>
            </tr>`;
        }).join('') : `<tr><td colspan="5" class="text-center text-sm text-base-content/40 py-4">No books found.</td></tr>`;

        panel.innerHTML = `
            <div class="space-y-3">
                <div class="flex flex-wrap gap-2 items-center">
                    ${stats.total_books  ? `<span class="badge badge-ghost badge-sm">${stats.total_books} Books</span>` : ''}
                    ${stats.owned_books  ? `<span class="badge badge-success badge-sm">${stats.owned_books} Owned</span>` : ''}
                    ${stats.missing_books > 0 ? `<span class="badge badge-warning badge-sm">${stats.missing_books} Missing</span>` : ''}
                </div>
                <div class="border border-base-300 rounded overflow-hidden">
                    <table class="table table-sm w-full">
                        <thead class="bg-base-200 text-xs uppercase tracking-wide text-base-content/50">
                            <tr><th>#</th><th>Title</th><th>Status</th><th class="text-right">Rating</th><th></th></tr>
                        </thead>
                        <tbody class="text-sm">${rows}</tbody>
                    </table>
                </div>
            </div>`;

        panel.querySelectorAll('.js-series-tab-import').forEach(btn => {
            btn.addEventListener('click', async () => {
                const asin = btn.dataset.asin;
                const author = btn.dataset.author;
                const thisSeries = btn.dataset.seriesAsin;
                btn.disabled = true;
                btn.innerHTML = '<span class="loading loading-spinner loading-xs"></span>';
                try {
                    const r = await fetch('/authors/api/import-book', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ author_name: author, asin })
                    });
                    const result = await r.json();
                    if (result.success) {
                        showNotification(result.message || 'Import queued.', 'success');
                        panel.dataset.loaded = 'false';
                        bookModalLoadSeriesTab(panel, thisSeries);
                    } else {
                        showNotification(result.error || 'Import failed.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-download"></i> Add';
                    }
                } catch (err) {
                    showNotification('Import request failed.', 'error');
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-download"></i> Add';
                }
            });
        });

    } catch (err) {
        panel.dataset.loaded = 'true';
        panel.innerHTML = `<p class="text-sm text-error">Error loading series: ${e(err.message)}</p>`;
    }
}

async function bookModalLoadHistoryTab(panel, bookId) {
    const e = escapeHtml;
    try {
        const res = await fetch(`/library/book/${encodeURIComponent(bookId)}/history`);
        const data = await res.json();
        panel.dataset.loaded = 'true';

        if (!data.success || !data.events || data.events.length === 0) {
            panel.innerHTML = `<p class="text-sm text-base-content/40">No history recorded for this book.</p>`;
            return;
        }

        const typeMap = {
            success: 'status-success',
            info:    'status-info',
            warning: 'status-warning',
            error:   'status-error',
        };

        const rows = data.events.map((ev, i) => {
            const dot = typeMap[ev.type] || 'status-info';
            const isLast = i === data.events.length - 1;
            const tsDisplay = ev.ts ? new Date(ev.ts).toLocaleString(undefined, {
                month: 'short', day: 'numeric', year: 'numeric',
                hour: 'numeric', minute: '2-digit'
            }) : '';
            return `
            <div class="flex items-start gap-3 py-2.5 ${isLast ? '' : 'border-b border-base-300/50'}">
                <span class="status ${dot} status-xs mt-1.5 flex-shrink-0"></span>
                <div class="min-w-0 flex-1">
                    <p class="text-sm">${e(ev.label)}</p>
                    ${tsDisplay ? `<p class="text-xs text-base-content/40">${e(tsDisplay)}</p>` : ''}
                </div>
            </div>`;
        }).join('');

        panel.innerHTML = `<div class="space-y-0">${rows}</div>`;
    } catch (err) {
        panel.dataset.loaded = 'true';
        panel.innerHTML = `<p class="text-sm text-error">Error loading history: ${e(err.message)}</p>`;
    }
}

function toggleBookSynopsis(btn) {
    const p = document.getElementById('bookModalSynopsis');
    const nowClamped = p.classList.toggle('line-clamp-5');
    btn.textContent = nowClamped ? 'Show more' : 'Show less';
}


const HC_STATUS_OPTIONS = [
    { id: 1, label: 'Want to Read' },
    { id: 2, label: 'Currently Reading' },
    { id: 3, label: 'Read' },
    { id: 4, label: 'Paused' },
    { id: 5, label: 'Did Not Finish' },
    { id: 6, label: 'Ignored' },
];
const HC_PRIVACY_OPTIONS = [
    { id: 1, label: 'Public' },
    { id: 2, label: 'Followers' },
    { id: 3, label: 'Private' },
];

async function bookModalLoadHardcoverTab(panel, bookId) {
    const e = AA.escapeHtml;
    try {
        const resp = await AA.apiFetch(`/api/hardcover/book/${encodeURIComponent(bookId)}/actions`);
        panel.dataset.loaded = 'true';
        const data = resp.data || {};
        if (!resp.ok) {
            panel.innerHTML = `<p class="text-sm text-error">${e(data.error || 'Failed to load Hardcover data.')}</p>`;
            return;
        }
        renderHardcoverTab(panel, bookId, data);
    } catch (err) {
        panel.dataset.loaded = 'true';
        panel.innerHTML = `<p class="text-sm text-error">Error loading Hardcover data: ${e(err.message)}</p>`;
    }
}

function renderHardcoverTab(panel, bookId, data) {
    const e = AA.escapeHtml;
    const statusOptions = HC_STATUS_OPTIONS.map(o =>
        `<option value="${o.id}" ${Number(data.status_id) === o.id ? 'selected' : ''}>${e(o.label)}</option>`
    ).join('');
    const privacyOptions = HC_PRIVACY_OPTIONS.map(o =>
        `<option value="${o.id}" ${Number(data.privacy_setting_id) === o.id ? 'selected' : ''}>${e(o.label)}</option>`
    ).join('');
    const rating = Number(data.rating) || 0;
    const stars = [1, 2, 3, 4, 5].map(n =>
        `<i class="fas fa-star hc-star" data-value="${n}" style="cursor:pointer;color:${n <= rating ? 'var(--color-warning,#f59e0b)' : 'rgba(128,128,128,.3)'}"></i>`
    ).join('');
    const lists = Array.isArray(data.lists) ? data.lists : [];
    const listOptions = lists.map(l => `<option value="${e(String(l.id))}">${e(l.name || '')}</option>`).join('');

    panel.innerHTML = `
        <div class="space-y-5">
            <div>
                <label class="text-[10px] font-bold uppercase tracking-widest text-base-content/35">Shelf Status</label>
                <select id="hcStatusSelect" class="select select-sm select-bordered w-full mt-1">
                    <option value=""> - Not shelved - </option>
                    ${statusOptions}
                </select>
            </div>
            <div>
                <label class="text-[10px] font-bold uppercase tracking-widest text-base-content/35">Your Rating</label>
                <div class="flex gap-1 mt-1" id="hcRatingStars">${stars}</div>
            </div>
            <label class="flex items-center gap-2 cursor-pointer select-none">
                <input type="checkbox" class="toggle toggle-success toggle-sm" id="hcOwnedToggle" ${data.owned ? 'checked' : ''} />
                <span class="text-sm">Owned</span>
            </label>
            <div>
                <label class="text-[10px] font-bold uppercase tracking-widest text-base-content/35">Privacy</label>
                <select id="hcPrivacySelect" class="select select-sm select-bordered w-full mt-1">${privacyOptions}</select>
            </div>
            <button class="btn btn-sm btn-soft ${data.liked ? 'btn-error' : ''}" id="hcLikeBtn">
                <i class="fas fa-heart"></i> ${data.liked ? 'Liked' : 'Like'}
            </button>
            <div>
                <label class="text-[10px] font-bold uppercase tracking-widest text-base-content/35">Add to List</label>
                <div class="flex gap-2 mt-1">
                    <select id="hcListSelect" class="select select-sm select-bordered flex-1">
                        <option value="">Select a list…</option>
                        ${listOptions}
                    </select>
                    <button class="btn btn-sm btn-soft btn-primary" id="hcAddToListBtn">Add</button>
                </div>
                <div class="flex gap-2 mt-2">
                    <input type="text" id="hcNewListName" placeholder="New list name" class="input input-sm input-bordered flex-1" />
                    <button class="btn btn-sm btn-soft" id="hcCreateListBtn">Create</button>
                </div>
            </div>
        </div>`;

    panel.querySelector('#hcStatusSelect').addEventListener('change', (ev) => {
        const val = ev.target.value;
        hcPushAction(bookId, { status_id: val ? Number(val) : null });
    });
    panel.querySelector('#hcPrivacySelect').addEventListener('change', (ev) => {
        hcPushAction(bookId, { privacy_setting_id: Number(ev.target.value) });
    });
    panel.querySelectorAll('.hc-star').forEach(star => {
        star.addEventListener('click', () => {
            hcPushAction(bookId, { rating: Number(star.dataset.value) });
        });
    });
    panel.querySelector('#hcOwnedToggle').addEventListener('change', async (ev) => {
        const owned = ev.target.checked;
        const resp = await AA.apiFetch(`/api/hardcover/book/${encodeURIComponent(bookId)}/owned`, {
            method: 'POST',
            body: JSON.stringify({ owned }),
        });
        if (!resp.ok) {
            ev.target.checked = !owned;
            showNotification((resp.data || {}).error || 'Failed to update owned flag.', 'error');
        }
    });
    panel.querySelector('#hcLikeBtn').addEventListener('click', async (ev) => {
        const btn = ev.currentTarget;
        const nowLiked = !btn.classList.contains('btn-error');
        const resp = await AA.apiFetch(`/api/hardcover/book/${encodeURIComponent(bookId)}/like`, {
            method: 'POST',
            body: JSON.stringify({ liked: nowLiked }),
        });
        if (resp.ok) {
            btn.classList.toggle('btn-error', nowLiked);
            btn.innerHTML = `<i class="fas fa-heart"></i> ${nowLiked ? 'Liked' : 'Like'}`;
        } else {
            showNotification((resp.data || {}).error || 'Failed to update like.', 'error');
        }
    });
    panel.querySelector('#hcAddToListBtn').addEventListener('click', async () => {
        const listId = panel.querySelector('#hcListSelect').value;
        if (!listId) return;
        const resp = await AA.apiFetch(`/api/hardcover/book/${encodeURIComponent(bookId)}/lists/${encodeURIComponent(listId)}`, {
            method: 'POST',
        });
        showNotification(resp.ok ? 'Added to list.' : ((resp.data || {}).error || 'Failed to add to list.'), resp.ok ? 'success' : 'error');
    });
    panel.querySelector('#hcCreateListBtn').addEventListener('click', async () => {
        const input = panel.querySelector('#hcNewListName');
        const name = input.value.trim();
        if (!name) return;
        const resp = await AA.apiFetch('/api/hardcover/lists', {
            method: 'POST',
            body: JSON.stringify({ name }),
        });
        if (resp.ok) {
            input.value = '';
            showNotification('List created.', 'success');
            panel.dataset.loaded = 'false';
            bookModalLoadHardcoverTab(panel, bookId);
        } else {
            showNotification((resp.data || {}).error || 'Failed to create list.', 'error');
        }
    });
}

async function hcPushAction(bookId, fields) {
    const resp = await AA.apiFetch(`/api/hardcover/book/${encodeURIComponent(bookId)}/actions`, {
        method: 'POST',
        body: JSON.stringify(fields),
    });
    if (!resp.ok) {
        showNotification((resp.data || {}).error || 'Failed to update Hardcover.', 'error');
    }
    return resp.ok;
}

function formatSourceLabel(book) {
    const source = (book.source_label || book.source || '').toLowerCase();

    if (source === 'audiobookshelf' || source === 'imported_abs' || source === 'abs_import') {
        return 'AudioBookShelf Import';
    }
    if (source === 'audible_catalog') {
        return 'Audible Catalog';
    }
    if (source === 'manual_import' || source === 'manual' || source === 'manual_override') {
        return 'Manual Import';
    }
    if (source === 'download_manager') {
        return 'Download Manager';
    }
    if (source === 'indexer') {
        return 'Indexer';
    }
    return source ? source.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()) : 'Unknown';
}

// --- Bulk actions -----------------------------------------------------------

function initializeBulkActions() {
    const selectAllBtn = document.getElementById('selectAllBtn');
    const clearSelectionBtn = document.getElementById('clearSelectionBtn');
    const updateMetadataBtn = document.getElementById('updateMetadataBtn');
    const changeStatusBtn = document.getElementById('changeStatusBtn');
    const deleteSelectedBtn = document.getElementById('deleteSelectedBtn');

    selectAllBtn?.addEventListener('click', selectAllBooks);
    clearSelectionBtn?.addEventListener('click', clearSelection);
    updateMetadataBtn?.addEventListener('click', updateSelectedMetadata);
    changeStatusBtn?.addEventListener('click', changeSelectedStatus);
    deleteSelectedBtn?.addEventListener('click', deleteSelectedBooks);
}

function selectAllBooks() {
    const visibleCards = document.querySelectorAll('.book-card:not([style*="display: none"])');
    
    visibleCards.forEach(card => {
        const bookId = card.dataset.bookId;
        LibraryState.selectedBooks.add(bookId);
        card.classList.add('selected');
        const checkbox = card.querySelector('.selection-checkbox input');
        if (checkbox) checkbox.checked = true;
    });
    
    updateSelectionCount();
    showNotification(`Selected ${LibraryState.selectedBooks.size} books`, 'success');
}

async function updateSelectedMetadata() {
    if (LibraryState.selectedBooks.size === 0) {
        showNotification('No books selected', 'warning');
        return;
    }

    const bookIds = Array.from(LibraryState.selectedBooks);
    showProgressModal('Updating Metadata', bookIds.length);

    let processed = 0;
    let successful = 0;
    let failed = 0;
    const errors = [];

    try {
        for (const bookId of bookIds) {
            updateProgressModal(processed, bookIds.length, successful, failed, errors);

            try {
                const response = await fetch(`/library/book/${bookId}/update-metadata`, {
                    method: 'POST'
                });

                const data = await response.json();

                if (data.success) {
                    successful += 1;
                } else {
                    failed += 1;
                    errors.push({ book_id: bookId, error: data.error || 'Unknown error' });
                    if (errors.length > 10) {
                        errors.splice(0, errors.length - 10);
                    }
                }
            } catch (error) {
                failed += 1;
                errors.push({ book_id: bookId, error: error.message || 'Request failed' });
                if (errors.length > 10) {
                    errors.splice(0, errors.length - 10);
                }
            }

            processed += 1;
            updateProgressModal(processed, bookIds.length, successful, failed, errors);

            await sleep(250);
        }

        if (successful > 0 && failed === 0) {
            showNotification(`Updated ${successful} books successfully`, 'success');
            setTimeout(() => location.reload(), 1500);
        } else if (successful > 0) {
            showNotification(`Updated ${successful} books; ${failed} failed`, 'warning');
        } else {
            showNotification('Failed to update metadata', 'error');
        }
    } catch (error) {
        showNotification('Failed to update metadata', 'error');
    } finally {
        updateProgressModal(processed, bookIds.length, successful, failed, errors);
        document.getElementById('progressCloseBtn')?.classList.remove('hidden');
    }
}

async function changeSelectedStatus() {
    if (LibraryState.selectedBooks.size === 0) {
        showNotification('No books selected', 'warning');
        return;
    }

    const newStatus = await promptStatusChange(LibraryState.selectedBooks.size);
    if (!newStatus) return;

    const bookIds = Array.from(LibraryState.selectedBooks);
    showProgressModal('Changing Status', bookIds.length);

    let processed = 0;
    let successful = 0;
    let failed = 0;
    const errors = [];

    try {
        for (const bookId of bookIds) {
            updateProgressModal(processed, bookIds.length, successful, failed, errors);

            try {
                const response = await fetch(`/library/book/${bookId}/status`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: newStatus })
                });
                const data = await response.json();

                if (data.success) {
                    successful += 1;
                } else {
                    failed += 1;
                    errors.push({ book_id: bookId, error: data.error || 'Unknown error' });
                    if (errors.length > 10) errors.splice(0, errors.length - 10);
                }
            } catch (error) {
                failed += 1;
                errors.push({ book_id: bookId, error: error.message || 'Request failed' });
                if (errors.length > 10) errors.splice(0, errors.length - 10);
            }

            processed += 1;
            updateProgressModal(processed, bookIds.length, successful, failed, errors);
        }

        if (successful > 0 && failed === 0) {
            showNotification(`Changed status of ${successful} books to "${newStatus}"`, 'success');
            setTimeout(() => location.reload(), 1200);
        } else if (successful > 0) {
            showNotification(`Changed ${successful} books; ${failed} failed`, 'warning');
        } else {
            showNotification('Failed to change status', 'error');
        }
    } finally {
        updateProgressModal(processed, bookIds.length, successful, failed, errors);
        document.getElementById('progressCloseBtn')?.classList.remove('hidden');
    }
}

function promptStatusChange(count) {
    return new Promise((resolve) => {
        const modal = document.getElementById('statusChangeModal');
        if (!modal) {
            resolve(null);
            return;
        }

        document.getElementById('statusChangeCount').textContent =
            `Apply a new status to ${count} selected book${count === 1 ? '' : 's'}.`;

        const applyBtn = document.getElementById('statusChangeApplyBtn');
        const cancelBtn = document.getElementById('statusChangeCancelBtn');

        function cleanup(result) {
            modal.close();
            applyBtn.removeEventListener('click', onApply);
            cancelBtn.removeEventListener('click', onCancel);
            modal.removeEventListener('cancel', onCancel);
            modal.removeEventListener('close', onClose);
            resolve(result);
        }
        function onApply() {
            const selected = modal.querySelector('input[name="bulkStatus"]:checked');
            cleanup(selected ? selected.value : null);
        }
        function onCancel() { cleanup(null); }
        function onClose() { cleanup(null); }

        applyBtn.addEventListener('click', onApply);
        cancelBtn.addEventListener('click', onCancel);
        modal.addEventListener('cancel', onCancel, { once: true });
        modal.addEventListener('close', onClose, { once: true });

        modal.showModal();
    });
}

async function deleteSelectedBooks() {
    if (LibraryState.selectedBooks.size === 0) {
        showNotification('No books selected', 'warning');
        return;
    }

    if (!await AA.showConfirmDialog(`Delete ${LibraryState.selectedBooks.size} selected books?`, 'Delete Books')) {
        return;
    }

    const bookIds = Array.from(LibraryState.selectedBooks).map(id => Number(id));
    showProgressModal('Deleting Books', bookIds.length);

    try {
        const response = await fetch('/library/books/bulk-delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ book_ids: bookIds })
        });

        const data = await response.json();

        const deletedCount = data.deleted_ids ? data.deleted_ids.length : 0;
        const failedCount = data.failed ? data.failed.length : 0;

        updateProgressModal(bookIds.length, bookIds.length, deletedCount, failedCount, data.failed || []);
        document.getElementById('progressCloseBtn')?.classList.remove('hidden');

        (data.deleted_ids || []).forEach(id => {
            const card = document.querySelector(`.book-card[data-book-id="${id}"]`);
            if (card) card.remove();
            LibraryState.selectedBooks.delete(String(id));
        });

        updateSelectionCount();

        if (deletedCount > 0 && failedCount === 0) {
            showNotification(`Deleted ${deletedCount} book(s)`, 'success');
        } else if (deletedCount > 0) {
            showNotification(`Deleted ${deletedCount} book(s); ${failedCount} failed`, 'warning');
        } else {
            showNotification('Failed to delete selected books', 'error');
        }

        if (deletedCount > 0) {
            setTimeout(() => location.reload(), 800);
        }

    } catch (error) {
        showNotification('Failed to delete selected books', 'error');
        document.getElementById('progressCloseBtn')?.classList.remove('hidden');
    }
}

// --- Single book actions ----------------------------------------------------

async function updateBookMetadata(bookId) {
    try {
        const response = await fetch(`/library/book/${bookId}/update-metadata`, {
            method: 'POST'
        });

        const data = await response.json();
        
        if (data.success) {
            showNotification('Metadata updated successfully', 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            showNotification(data.error || 'Update failed', 'error');
        }
    } catch (error) {
        showNotification('Failed to update metadata', 'error');
    }
}

async function searchForBook(bookId, bookTitle = '', bookAuthor = '') {
    const modal = document.getElementById('searchModal');
    const loading = document.getElementById('searchLoading');
    const results = document.getElementById('searchResults');
    const noResults = document.getElementById('searchNoResults');
    const error = document.getElementById('searchError');
    
    loading.classList.remove('hidden');
    results.classList.add('hidden');
    noResults.classList.add('hidden');
    error.classList.add('hidden');

    document.getElementById('searchBookTitle').textContent = bookTitle;
    document.getElementById('searchBookAuthor').textContent = bookAuthor;
    
    modal.showModal();
    
    try {
        const response = await fetch(`/api/search/manual/book/${bookId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || 'Search failed');
        }
        
        if (data.book_info) {
            document.getElementById('searchBookTitle').textContent = data.book_info.title;
            document.getElementById('searchBookAuthor').textContent = data.book_info.author || '';
        }
        
        loading.classList.add('hidden');
        
        if (data.results && data.results.length > 0) {
            displaySearchResults(data.results, bookId);
            results.classList.remove('hidden');
        } else {
            noResults.classList.remove('hidden');
        }
        
    } catch (err) {
        loading.classList.add('hidden');
        error.classList.remove('hidden');
        document.getElementById('searchErrorMessage').textContent = err.message;
    }
}

async function autoDownloadBook(bookId, triggerButton = null) {
    if (!bookId) {
        showNotification('Invalid book selected for auto download.', 'error');
        return;
    }

    const numericId = Number.parseInt(bookId, 10);
    if (Number.isNaN(numericId) || numericId <= 0) {
        showNotification('Invalid book identifier provided.', 'error');
        return;
    }

    if (triggerButton?.dataset.loading === 'true') {
        return;
    }

    const button = triggerButton || null;
    const originalHtml = button ? button.innerHTML : '';

    if (button) {
        button.dataset.loading = 'true';
        button.disabled = true;
        button.innerHTML = '<span class="loading loading-spinner loading-xs"></span>';
    }

    try {
        const response = await fetch(`/api/search/automatic/force/${numericId}`, {
            method: 'POST'
        });

        const data = await response.json();

        if (!response.ok || data.success === false || data.error) {
            throw new Error(data.error || data.message || 'Automatic search failed');
        }

        showNotification(data.message || 'Automatic search queued', 'success');

        if (button) {
            button.innerHTML = '<i class="fas fa-check"></i>';
            button.classList.remove('btn-warning');
            button.classList.add('btn-success');
            button.title = 'Queued';
        }
    } catch (error) {
        showNotification(error.message || 'Automatic search failed', 'error');

        if (button) {
            button.innerHTML = originalHtml || '<i class="fas fa-bolt"></i>';
            button.disabled = false;
        }
    } finally {
        if (button) {
            delete button.dataset.loading;
            if (!button.classList.contains('btn-success')) {
                button.disabled = false;
            }
        }
    }
}

let _lastSearchResults = [];
let _lastSearchBookId = null;

function sortSearchResults(results, sortBy) {
    const sorted = results.slice();
    if (sortBy === 'size') {
        sorted.sort((a, b) => (b.size_bytes || 0) - (a.size_bytes || 0));
    } else if (sortBy === 'seeders') {
        sorted.sort((a, b) => (b.seeders || 0) - (a.seeders || 0));
    } else {
        sorted.sort((a, b) => {
            const confA = (a.quality_assessment && a.quality_assessment.confidence) || a.confidence || 0;
            const confB = (b.quality_assessment && b.quality_assessment.confidence) || b.confidence || 0;
            return confB - confA;
        });
    }
    return sorted;
}

function handleSearchResultsSortChange() {
    if (!_lastSearchResults.length) {
        return;
    }
    const sortSelect = document.getElementById('searchResultsSort');
    const sortBy = sortSelect ? sortSelect.value : 'confidence';
    displaySearchResults(_lastSearchResults, _lastSearchBookId, sortBy);
}

function displaySearchResults(results, bookId, sortBy) {
    const tbody = document.getElementById('searchResultsBody');
    const count = document.getElementById('resultsCount');

    _lastSearchResults = results;
    _lastSearchBookId = bookId;

    const sortSelect = document.getElementById('searchResultsSort');
    const effectiveSort = sortBy || (sortSelect ? sortSelect.value : 'confidence');
    const sortedResults = sortSearchResults(results, effectiveSort);

    count.textContent = sortedResults.length;
    tbody.innerHTML = '';

    sortedResults.forEach((result, index) => {
        
    const row = document.createElement('tr');

    let downloadType = (result.download_type || result.source || '').toLowerCase();
    let isAudibleDirect = downloadType.includes('audible');

        let matchConfidence = 0;
        let releaseQuality = 0;

        if (result.quality_assessment) {
            matchConfidence = result.quality_assessment.match_confidence ?? result.quality_assessment.confidence ?? 0;
            releaseQuality = result.quality_assessment.release_quality ?? 0;
        } else {
            matchConfidence = result.confidence_score ?? result.confidence ?? 0;
            releaseQuality = result.release_quality_score ?? 0;
        }
        
        
        const fullTitle = result.title || 'Unknown';
        let displayTitle = fullTitle;
        let displayAuthor = getAuthorLabel(result);
        let bitrate = '';
        
        if (!displayAuthor) {
            const dashIndex = fullTitle.indexOf(' - ');
            if (dashIndex > 0) {
                displayTitle = fullTitle.substring(0, dashIndex).trim();
                const remainder = fullTitle.substring(dashIndex + 3);
                displayAuthor = remainder.replace(/\[.*?\]/g, '').trim();
            }
        }
        
        const bitrateMatch = fullTitle.match(/(\d+)\s*kbps/i);
        if (bitrateMatch) {
            bitrate = `${bitrateMatch[1]} kbps`;
        }
        
        displayTitle = displayTitle.trim();
        
        const format = result.format || detectFormat(fullTitle);

        const indexerLabelRaw = (result.indexer || result.source || '').toString();
        const indexerLower = indexerLabelRaw.toLowerCase();
        const isAudiobookBay = indexerLower.includes('audiobookbay');
        
        const seeders = result.seeders || 0;
        const peers = result.peers || 0;
        
        const matchClass = matchConfidence >= 80 ? 'badge-success' : matchConfidence >= 60 ? 'badge-warning' : 'badge-error';
        const releaseQualityClass = releaseQuality >= 80 ? 'badge-success' : releaseQuality >= 60 ? 'badge-warning' : 'badge-error';
        
        const sizeBytes = result.size_bytes || 0;
        const sizeFormatted = sizeBytes > 0 ? AA.formatSize(sizeBytes) : (result.size || 'Unknown');
        
        const titleCell = displayAuthor 
            ? `<div class="max-w-xs">
                 <div class="truncate font-semibold" title="${escapeHtml(fullTitle)}">${escapeHtml(displayTitle)}</div>
                 <div class="text-xs text-base-content/60 truncate">${escapeHtml(displayAuthor)}</div>
               </div>`
            : `<div class="max-w-xs truncate" title="${escapeHtml(fullTitle)}">${escapeHtml(displayTitle)}</div>`;
        
        const isPrivate = result.private === true;
        const freeleech = result.freeleech;
        // Every result carries empty mam_* keys; only the direct MAM adapter fills them
        const isMamResult = typeof result.mam_free === 'boolean';

        let flCell;
        if (isAudibleDirect || (!isPrivate && !freeleech)) {
            flCell = '<span class="text-xs text-base-content/40">N/A</span>';
        } else if (result.mam_personal_freeleech) {
            flCell = '<span class="badge badge-success badge-sm" title="Personal freeleech">PFL</span>';
        } else if (freeleech === 'fl') {
            flCell = '<span class="badge badge-success badge-sm">FL</span>';
        } else if (freeleech === 'vip') {
            flCell = '<span class="badge badge-accent badge-sm">VIP</span>';
        } else {
            flCell = '<span class="badge badge-ghost badge-sm">—</span>';
        }

        const statusBadges = [];
        if (!isAudibleDirect && isPrivate) {
            if (result.mam_my_snatched) {
                statusBadges.push('<span class="badge badge-outline badge-sm" title="Already downloaded">Snatched</span>');
            }
            if (result.mam_bookmarked) {
                statusBadges.push('<span class="badge badge-outline badge-sm" title="Bookmarked">Bookmarked</span>');
            }
        }
        const statusCell = statusBadges.length
            ? `<div class="flex flex-col gap-1">${statusBadges.join('')}</div>`
            : '<span class="text-xs text-base-content/40">—</span>';

        const canUseWedge = isMamResult && isPrivate && !freeleech;
        const wedgeToggle = canUseWedge
            ? `<label class="flex items-center gap-1.5 text-xs cursor-pointer" title="Spend a freeleech wedge on this download">
                 <input type="checkbox" class="checkbox checkbox-xs use-fl-wedge-cb" />
                 <span class="text-base-content/60">Use wedge</span>
               </label>`
            : '';

        let uploaderTitleAttr = '';
        if (isMamResult && result.mam_owner) {
            let titleText = `Uploaded by ${result.mam_owner}`;
            if (result.mam_times_completed) {
                titleText += ` · completed ${result.mam_times_completed}x`;
            }
            uploaderTitleAttr = `title="${escapeHtml(titleText)}"`;
        }

        row.innerHTML = `
            <td>${titleCell}</td>
            <td>
                <span class="badge badge-sm ${isAudibleDirect ? 'badge-primary' : 'badge-outline'}">
                    ${isAudibleDirect ? 'Audible' : escapeHtml(format)}
                </span>
                ${bitrate ? `<div class="text-xs text-base-content/60 mt-1">${escapeHtml(bitrate)}</div>` : ''}
            </td>
            <td>${isAudibleDirect ? '—' : escapeHtml(sizeFormatted)}</td>
            <td>
                <span class="badge ${matchClass} badge-sm">
                    ${matchConfidence > 0 ? matchConfidence.toFixed(0) + '%' : isAudibleDirect ? '100%' : 'N/A'}
                </span>
            </td>
            <td>
                <span class="badge ${releaseQualityClass} badge-sm">
                    ${releaseQuality > 0 ? releaseQuality.toFixed(0) + '%' : isAudibleDirect ? '100%' : 'N/A'}
                </span>
            </td>
            <td>
                <div class="flex items-center gap-2 text-xs">
                    ${isAudibleDirect ? '<span class="badge badge-success badge-sm">owned</span>' : `
                    <span class="badge ${isAudiobookBay ? 'badge-success' : seeders > 10 ? 'badge-success' : seeders > 0 ? 'badge-warning' : 'badge-error'} badge-sm">
                        ${escapeHtml(seeders)} <i class="fas fa-arrow-up text-[10px] ml-1"></i>
                    </span>
                    <span class="badge ${isAudiobookBay ? 'badge-success' : 'badge-ghost'} badge-sm">
                        ${escapeHtml(peers)} <i class="fas fa-arrow-down text-[10px] ml-1"></i>
                    </span>`}
                </div>
            </td>
            <td>${flCell}</td>
            <td>${statusCell}</td>
            <td class="text-xs">
                <span ${uploaderTitleAttr}>${escapeHtml(result.indexer || result.source || (isAudibleDirect ? 'Audible Library' : 'Unknown'))}</span>
            </td>
            <td>
                <div class="flex items-center gap-2">
                    <button type="button" class="btn ${isAudibleDirect ? 'btn-primary' : 'btn-soft btn-primary'} btn-xs manual-download-btn">
                        <i class="fas fa-download"></i> ${isAudibleDirect ? 'Download from Audible' : 'Download'}
                    </button>
                    ${wedgeToggle}
                </div>
            </td>
        `;

        const wedgeCheckbox = row.querySelector('.use-fl-wedge-cb');
        if (wedgeCheckbox) {
            wedgeCheckbox.addEventListener('change', async (event) => {
                if (!event.target.checked) {
                    result.use_fl_wedge = false;
                    return;
                }
                const confirmed = await AA.showConfirmDialog(
                    'Spend one of your freeleech wedges on this download? This cannot be undone.',
                    'Use Freeleech Wedge',
                    'Use Wedge',
                    'btn-primary'
                );
                if (!confirmed) {
                    event.target.checked = false;
                    result.use_fl_wedge = false;
                    return;
                }
                result.use_fl_wedge = true;
            });
        }

        const downloadBtn = row.querySelector('.manual-download-btn');
        if (downloadBtn) {
            downloadBtn.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                downloadResult(bookId, result, downloadBtn, row);
            });
        }

        tbody.appendChild(row);
    });
}

function detectFormat(title) {
    if (!title) return 'M4B';
    const formatMatch = title.match(/\[(M4B|MP3|FLAC|AAC|OGG|WMA|M4A)\]/i);
    return formatMatch ? formatMatch[1].toUpperCase() : 'M4B';
}

function getAuthorLabel(result) {
    if (!result || typeof result !== 'object') {
        return '';
    }

    const candidateKeys = [
        'author',
        'authors',
        'author_name',
        'primary_author',
        'book_author',
    ];

    for (const key of candidateKeys) {
        const value = result[key];
        if (!value) {
            continue;
        }

        if (Array.isArray(value)) {
            const joined = value.filter(Boolean).join(', ').trim();
            if (joined) {
                return joined;
            }
        } else if (typeof value === 'object') {
            const joined = Object.values(value)
                .map(part => (typeof part === 'string' ? part.trim() : ''))
                .filter(Boolean)
                .join(', ');
            if (joined) {
                return joined;
            }
        } else if (typeof value === 'string') {
            const cleaned = value.trim();
            if (cleaned && cleaned.toLowerCase() !== 'unknown author') {
                return cleaned;
            }
        }
    }

    return '';
}

function formatSize(bytes) {
    return AA.formatSize(bytes);
}

function escapeHtml(text) {
    return AA.escapeHtml(text);
}

async function downloadResult(bookId, result, triggerButton = null, tableRow = null) {
    if (triggerButton?.dataset.loading === 'true') {
        return;
    }

    let originalButtonHtml = null;
    if (triggerButton) {
        originalButtonHtml = triggerButton.innerHTML;
        triggerButton.dataset.loading = 'true';
        triggerButton.disabled = true;
        triggerButton.innerHTML = '<span class="loading loading-spinner loading-xs"></span>';
    }

    try {
        const response = await fetch('/api/search/manual/download', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                book_id: bookId,
                result: result
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            if (triggerButton) {
                triggerButton.innerHTML = '<i class="fas fa-check"></i> Enqueued';
                triggerButton.classList.remove('btn-primary', 'btn-error', 'btn-warning');
                triggerButton.classList.add('btn-soft', 'btn-success');
            }
            if (tableRow) {
                tableRow.classList.add('opacity-70');
            }
            const jobId = data.job_id ? ` Job: ${data.job_id}` : '';
            showNotification(`Download enqueued successfully.${jobId ? jobId : ''} Check the Downloads page.`, 'success');
            const modal = document.getElementById('searchModal');
            modal?.close();
        } else {
            if (data.duplicate || response.status === 409) {
                if (triggerButton) {
                    triggerButton.innerHTML = '<i class="fas fa-inbox"></i> In Queue';
                    triggerButton.classList.remove('btn-primary', 'btn-error', 'btn-warning');
                    triggerButton.classList.add('btn-soft', 'btn-warning');
                }
                if (tableRow) {
                    tableRow.classList.add('opacity-70');
                }
                showNotification('This book is already in the download queue. Check the Downloads page.', 'warning');
                const modal = document.getElementById('searchModal');
                modal?.close();
            } else {
                showNotification(data.error || 'Download failed', 'error');
                if (triggerButton) {
                    triggerButton.innerHTML = originalButtonHtml;
                    triggerButton.disabled = false;
                }
            }
        }
    } catch (err) {
        showNotification('Failed to start download', 'error');
        if (triggerButton) {
            triggerButton.innerHTML = originalButtonHtml;
            triggerButton.disabled = false;
        }
    }
    if (triggerButton) {
        delete triggerButton.dataset.loading;
    }
}

async function deleteBook(bookId) {
    if (!await AA.showConfirmDialog('Delete this book?', 'Delete Book')) {
        return;
    }

    try {
        const response = await fetch(`/library/book/${bookId}`, {
            method: 'DELETE'
        });

        const data = await response.json();
        
        if (data.success) {
            showNotification('Book deleted successfully', 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            showNotification(data.error || 'Delete failed', 'error');
        }
    } catch (error) {
        showNotification('Failed to delete book', 'error');
    }
}

// --- Progress modal ---------------------------------------------------------

function showProgressModal(title, total) {
    const modal = document.getElementById('progressModal');
    document.getElementById('progressTitle').textContent = title;
    document.getElementById('progressDetails').textContent = 'Starting operation...';
    document.getElementById('progressBar').value = 0;
    document.getElementById('progressCount').textContent = `0 / ${total}`;
    document.getElementById('progressPercent').textContent = '0%';
    document.getElementById('progressSuccess').textContent = '0';
    document.getElementById('progressFailed').textContent = '0';
    document.getElementById('errorsList').classList.add('hidden');
    document.getElementById('progressCloseBtn').classList.add('hidden');
    
    modal.showModal();
}

function updateProgressModal(current, total, success, failed, errors) {
    const progressBar = document.getElementById('progressBar');
    const progressCount = document.getElementById('progressCount');
    const progressPercent = document.getElementById('progressPercent');
    const progressSuccess = document.getElementById('progressSuccess');
    const progressFailed = document.getElementById('progressFailed');
    const progressDetails = document.getElementById('progressDetails');

    if (!progressBar || !progressCount || !progressPercent || !progressSuccess || !progressFailed || !progressDetails) {
        return;
    }

    const safeTotal = total || 1;
    const percent = Math.round((current / safeTotal) * 100);
    const nextIndex = Math.min(current + 1, safeTotal);
    
    progressBar.value = percent;
    progressCount.textContent = `${current} / ${total}`;
    progressPercent.textContent = `${percent}%`;
    progressSuccess.textContent = success;
    progressFailed.textContent = failed;
    progressDetails.textContent = 
        current === total ? 'Operation complete' : `Processing book ${nextIndex} of ${total}...`;
    
    if (errors && errors.length > 0) {
        const errorsList = document.getElementById('errorsList');
        const errorsContent = document.getElementById('errorsContent');
        if (errorsList && errorsContent) {
            errorsContent.innerHTML = errors.map(err => 
                `<div class="text-error"><strong>${err.title || err.book_id}</strong>: ${err.error}</div>`
            ).join('');

            errorsList.classList.remove('hidden');
        }
    } else {
        const errorsList = document.getElementById('errorsList');
        if (errorsList) {
            errorsList.classList.add('hidden');
            const errorsContent = document.getElementById('errorsContent');
            if (errorsContent) {
                errorsContent.innerHTML = '';
            }
        }
    }
    
    if (current === total) {
        document.getElementById('progressCloseBtn').classList.remove('hidden');
    }
}

function sleep(durationMs) {
    return new Promise(resolve => setTimeout(resolve, durationMs));
}

function closeProgressModal() {
    const modal = document.getElementById('progressModal');
    modal.close();
}


window.updateBookMetadata = updateBookMetadata;
window.searchForBook = searchForBook;
window.deleteBook = deleteBook;
window.downloadResult = downloadResult;
window.autoDownloadBook = autoDownloadBook;

// --- View modes (grid / list / table) ---------------------------------------

function initializeViewModes() {
    const saved = localStorage.getItem('libraryViewMode') || 'grid';
    LibraryState.viewMode = saved;

    document.getElementById('viewGridBtn')?.addEventListener('click', () => setViewMode('grid'));
    document.getElementById('viewListBtn')?.addEventListener('click', () => setViewMode('list'));
    document.getElementById('viewTableBtn')?.addEventListener('click', () => setViewMode('table'));

    _applyViewButtonState(saved);
    _applyViewContainerState(saved);
}

function setViewMode(mode) {
    if (mode === LibraryState.viewMode) return;
    LibraryState.viewMode = mode;
    localStorage.setItem('libraryViewMode', mode);
    _applyViewButtonState(mode);
    _applyViewContainerState(mode);
    applyFilters();
}

function _applyViewButtonState(mode) {
    ['grid', 'list', 'table'].forEach(m => {
        const btn = document.getElementById(`view${m.charAt(0).toUpperCase() + m.slice(1)}Btn`);
        if (!btn) return;
        const active = m === mode;
        btn.classList.toggle('btn-ghost', !active);
        btn.classList.toggle('btn-primary', active);
        btn.classList.toggle('btn-soft', active);
    });
}

function _applyViewContainerState(mode) {
    const grid = document.getElementById('booksGrid');
    const listWrap = document.getElementById('booksListWrap');
    const tableWrap = document.getElementById('booksTableWrap');

    if (mode === 'table') {
        grid?.classList.add('hidden');
        listWrap?.classList.add('hidden');
        tableWrap?.classList.remove('hidden');
    } else if (mode === 'list') {
        grid?.classList.add('hidden');
        tableWrap?.classList.add('hidden');
        listWrap?.classList.remove('hidden');
    } else {
        listWrap?.classList.add('hidden');
        tableWrap?.classList.add('hidden');
        grid?.classList.remove('hidden');
        if (grid) {
            grid.classList.remove('library-list');
            grid.classList.add('grid', 'gap-4', 'library-grid');
            applySizeClass(parseInt(document.getElementById('sizeSlider')?.value || '50'));
        }
    }
}

function renderListView() {
    const listWrap = document.getElementById('booksListWrap');
    if (!listWrap) return;

    const books = LibraryState.filteredBooks;

    if (!books || books.length === 0) {
        listWrap.innerHTML = '';
        setEmptyState(true);
        return;
    }
    setEmptyState(false);

    const statusMap = {
        owned:   ['badge-success', 'Owned'],
        wanted:  ['badge-warning', 'Wanted'],
        missing: ['badge-error',   'Missing'],
    };

    const rows = books.map(book => {
        const rating = book.rating ? Number(book.rating).toFixed(1) : null;
        const hasRating = rating && rating !== 'NaN';
        const [badgeCls, badgeLabel] = statusMap[book.library_status] || ['badge-ghost', book.library_status || ''];

        return `
            <tr class="lib-list-row row-hover cursor-pointer" data-book-id="${book.id}">
                <td class="lib-sel-col" style="display:none;width:2rem;padding-right:0;">
                    <input type="checkbox" class="checkbox checkbox-primary checkbox-xs" />
                </td>
                <td class="font-medium">${escapeHtml(book.title || '')}</td>
                <td style="opacity:.55;">${escapeHtml(book.author || '')}</td>
                <td><span class="badge badge-xs ${badgeCls} badge-soft">${escapeHtml(badgeLabel)}</span></td>
                <td>${hasRating
                    ? `<span class="text-warning" style="font-size:10px;">★</span> <span style="font-size:11px;">${escapeHtml(rating)}</span>`
                    : `<span style="opacity:.3;">—</span>`}
                </td>
            </tr>`;
    }).join('');

    listWrap.innerHTML = `
        <div class="overflow-x-auto rounded-sm border border-base-content/10">
            <table class="table table-xs">
                <thead>
                    <tr>
                        <th class="lib-sel-col" style="display:none;width:2rem;"></th>
                        <th>Title</th>
                        <th>Author</th>
                        <th>Status</th>
                        <th>Rating</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;

    listWrap.querySelectorAll('tr.lib-list-row').forEach(tr => {
        tr.addEventListener('click', e => {
            if (e.target.closest('input[type="checkbox"]')) return;
            const bookId = tr.dataset.bookId;
            if (LibraryState.isSelectMode) {
                toggleBookSelection(tr, bookId);
            } else {
                openBookModal(bookId);
            }
        });
        const cb = tr.querySelector('input[type="checkbox"]');
        cb?.addEventListener('change', () => toggleBookSelection(tr, tr.dataset.bookId));
    });
}

function renderTableView() {
    const tableWrap = document.getElementById('booksTableWrap');
    if (!tableWrap) return;

    const books = LibraryState.filteredBooks;

    if (!books || books.length === 0) {
        tableWrap.innerHTML = '';
        setEmptyState(true);
        return;
    }
    setEmptyState(false);

    const statusMap = {
        owned:   ['badge-success', 'Owned'],
        wanted:  ['badge-warning', 'Wanted'],
        missing: ['badge-error',   'Missing'],
    };

    const rows = books.map(book => {
        const rating = book.rating ? Number(book.rating).toFixed(1) : null;
        const hasRating = rating && rating !== 'NaN';
        const ratingNum = hasRating ? parseFloat(rating) : 0;
        const [badgeCls, badgeLabel] = statusMap[book.library_status] || ['badge-ghost', book.library_status || ''];
        const source = book.asin ? 'Audible' : '—';

        const cover = book.cover_image
            ? `<img src="${escapeHtml(book.cover_image)}" alt="" style="width:48px;height:48px;border-radius:4px;object-fit:cover;display:block;flex-shrink:0;" loading="lazy" onerror="this.style.display='none'">`
            : `<div style="width:48px;height:48px;border-radius:4px;flex-shrink:0;background:color-mix(in oklch,var(--b3) 80%,transparent);display:flex;align-items:center;justify-content:center;"><i class="fas fa-book" style="opacity:.2;font-size:.8rem;"></i></div>`;

        const seriesLine = book.series
            ? `<div style="font-size:11px;opacity:.4;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:260px;">${escapeHtml(book.series)}</div>`
            : '';

        let starsHtml = '<span style="opacity:.3;">—</span>';
        if (hasRating) {
            const full = Math.floor(ratingNum);
            const half = (ratingNum - full) >= 0.5;
            let stars = '';
            for (let i = 1; i <= 5; i++) {
                if (i <= full) stars += '<i class="fas fa-star" style="font-size:10px;color:var(--color-warning,oklch(var(--wa)))"></i>';
                else if (i === full + 1 && half) stars += '<i class="fas fa-star-half-alt" style="font-size:10px;color:var(--color-warning,oklch(var(--wa)))"></i>';
                else stars += '<i class="far fa-star" style="font-size:10px;opacity:.25;"></i>';
            }
            starsHtml = `<div style="display:flex;align-items:center;gap:1px;">${stars}<span style="margin-left:5px;font-size:12px;">${escapeHtml(rating)}</span></div>`;
        }

        return `
            <tr class="lib-table-row row-hover cursor-pointer" data-book-id="${book.id}">
                <td class="lib-sel-col" style="display:none;width:2rem;padding-right:0;">
                    <input type="checkbox" class="checkbox checkbox-primary checkbox-xs" />
                </td>
                <td>
                    <div style="display:flex;align-items:center;gap:12px;">
                        ${cover}
                        <div style="min-width:0;">
                            <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:280px;">${escapeHtml(book.title || '')}</div>
                            ${seriesLine}
                        </div>
                    </div>
                </td>
                <td style="opacity:.6;">${escapeHtml(book.author || '')}</td>
                <td><span class="badge ${badgeCls} badge-soft">${escapeHtml(badgeLabel)}</span></td>
                <td>${starsHtml}</td>
                <td style="opacity:.5;font-size:13px;">${escapeHtml(source)}</td>
            </tr>`;
    }).join('');

    tableWrap.innerHTML = `
        <div class="overflow-x-auto rounded-sm border border-base-content/10">
            <table class="table">
                <thead>
                    <tr>
                        <th class="lib-sel-col" style="display:none;width:2rem;"></th>
                        <th>Title</th>
                        <th>Author</th>
                        <th>Status</th>
                        <th>Rating</th>
                        <th>Source</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;

    tableWrap.querySelectorAll('tr.lib-table-row').forEach(tr => {
        tr.addEventListener('click', e => {
            if (e.target.closest('input[type="checkbox"]')) return;
            const bookId = tr.dataset.bookId;
            if (LibraryState.isSelectMode) {
                toggleBookSelection(tr, bookId);
            } else {
                openBookModal(bookId);
            }
        });
        const cb = tr.querySelector('input[type="checkbox"]');
        cb?.addEventListener('change', () => toggleBookSelection(tr, tr.dataset.bookId));
    });
}


async function matchBookToHardcover(bookId, hardcoverBookId = null) {
    const body = hardcoverBookId ? { hardcover_book_id: hardcoverBookId } : {};
    const resp = await AA.apiFetch(`/api/hardcover/book/${encodeURIComponent(bookId)}/match`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = resp.data || {};
    if (!resp.ok) {
        showNotification(data.error || 'Could not reach Hardcover', 'error');
        return;
    }
    if (data.linked) {
        document.getElementById('hardcoverMatchModal')?.close();
        showNotification('Linked to Hardcover', 'success');
        openBookModal(bookId);
        return;
    }
    if (!data.candidates || !data.candidates.length) {
        showNotification('No Hardcover match found for this book', 'warning');
        return;
    }
    showHardcoverCandidates(bookId, data.candidates);
}

function showHardcoverCandidates(bookId, candidates) {
    const e = AA.escapeHtml;
    let dialog = document.getElementById('hardcoverMatchModal');
    if (!dialog) {
        dialog = document.createElement('dialog');
        dialog.id = 'hardcoverMatchModal';
        dialog.className = 'modal';
        document.body.appendChild(dialog);
    }
    const rows = candidates.map(c => `
        <li class="flex items-center gap-3 py-2 border-b border-base-300 last:border-0">
            ${c.image ? `<img src="${e(c.image)}" alt="" class="w-10 h-14 object-cover rounded-sm shrink-0" loading="lazy">` : '<div class="w-10 h-14 bg-base-300 rounded-sm shrink-0"></div>'}
            <div class="min-w-0 flex-1">
                <div class="text-sm font-semibold truncate">${e(c.title)}</div>
                <div class="text-xs text-base-content/60 truncate">${e((c.authors || []).join(', '))}</div>
                <div class="text-[11px] text-base-content/40">${Math.round((c.score || 0) * 100)}% title match${c.has_audiobook ? ' · audiobook edition' : ''}</div>
            </div>
            <button class="btn btn-primary btn-xs" onclick="matchBookToHardcover(${bookId}, ${Number(c.hardcover_book_id)})">Link</button>
        </li>`).join('');
    dialog.innerHTML = `
        <div class="modal-box max-w-lg">
            <h3 class="font-semibold text-base mb-1">Pick the Hardcover book</h3>
            <p class="text-xs text-base-content/60 mb-3">No confident match was found automatically.</p>
            <ul>${rows}</ul>
            <div class="modal-action"><button class="btn btn-ghost btn-sm" onclick="this.closest('dialog').close()">Cancel</button></div>
        </div>
        <form method="dialog" class="modal-backdrop"><button>close</button></form>`;
    dialog.showModal();
}

async function unlinkBookFromHardcover(bookId) {
    const ok = await AA.showConfirmDialog('Remove the link between this book and Hardcover?', 'Unlink from Hardcover', 'Unlink');
    if (!ok) return;
    const resp = await AA.apiFetch(`/api/hardcover/book/${encodeURIComponent(bookId)}/link`, { method: 'DELETE' });
    if (!resp.ok) {
        showNotification('Could not unlink the book', 'error');
        return;
    }
    showNotification('Unlinked from Hardcover', 'success');
    openBookModal(bookId);
}

