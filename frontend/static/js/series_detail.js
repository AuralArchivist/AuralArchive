// --- Series detail page -----------------------------------------------------

let _sdData = null;
let _sdDescExpanded = false;
let _sdShowHidden = false;

document.addEventListener('DOMContentLoaded', () => {
    const page = document.getElementById('seriesDetailPage');
    if (!page) return;
    loadSeriesDetail(page.dataset.seriesAsin);
});

// --- Load & display ---------------------------------------------------------

async function loadSeriesDetail(seriesAsin) {
    sdShowState('loading');
    try {
        const res = await fetch(`/series/api/${seriesAsin}/books`);
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to load series');
        _sdData = data;
        renderSeriesDetail(data);
        sdShowState('content');
    } catch (err) {
        document.getElementById('sdErrorMessage').textContent = err.message;
        sdShowState('error');
    }
}

function renderSeriesDetail(data) {
    const stats   = data.statistics || {};
    const title   = data.series_title || 'Unknown Series';
    const books   = Array.isArray(data.books) ? data.books : [];
    const author  = sdGetImportAuthor(data);
    const pct     = stats.completion_percentage || 0;
    const total   = stats.total_books || books.length;
    const owned   = stats.owned_books || 0;
    const missing = stats.missing_books || 0;

    const coverUrl = (data.series_cover_url || '').trim();
    if (coverUrl) {
        document.getElementById('sdCover').src = coverUrl;
        document.getElementById('sdCoverWrap').classList.remove('hidden');
    }

    document.getElementById('sdBreadcrumb').textContent = title;
    document.getElementById('sdTitle').textContent = title;
    if (author) {
        document.getElementById('sdAuthor').textContent = `By ${author}`;
    }

    document.getElementById('sdStats').innerHTML = `
        <span class="badge badge-ghost">
            <i class="fas fa-books mr-1.5"></i>${total} Book${total !== 1 ? 's' : ''}
        </span>
        <span class="badge badge-success">
            <i class="fas fa-check mr-1.5"></i>${owned} Owned
        </span>
        ${missing > 0
            ? `<span class="badge badge-warning">
                   <i class="fas fa-clock mr-1.5"></i>${missing} Missing
               </span>`
            : ''}
        ${pct >= 100
            ? `<span class="badge badge-success badge-outline">
                   <i class="fas fa-trophy mr-1.5"></i>Complete
               </span>`
            : ''}
    `;

    const importBtn = document.getElementById('sdBtnImport');
    if (author) {
        importBtn.dataset.author      = author;
        importBtn.dataset.seriesTitle = title;
        importBtn.classList.remove('hidden');
    }

    document.getElementById('sdProgressBar').value            = pct;
    document.getElementById('sdProgressLabel').textContent    = `${pct}%`;
    document.getElementById('sdProgressDetail').textContent   = `${owned} of ${total} books`;

    const rawDesc = (data.series_description || '').trim();
    const descWrap = document.getElementById('sdDescWrap');
    const descEl   = document.getElementById('sdDescText');
    const descToggle = document.getElementById('sdDescToggle');
    if (rawDesc) {
        const paragraphs = rawDesc
            .split(/<\/?\s*p\s*>/i)
            .map(chunk => chunk.replace(/<[^>]*>/g, '').trim())
            .filter(Boolean);
        descEl.textContent = paragraphs.join('\n\n');
        descWrap.classList.remove('hidden');

        _sdDescExpanded = false;
        descEl.classList.add('sd-synopsis-collapsed');
        descToggle.textContent = 'Show more';
        descToggle.classList.add('hidden');
        requestAnimationFrame(() => {
            if (descEl.scrollHeight - descEl.clientHeight > 4) {
                descToggle.classList.remove('hidden');
            } else {
                descEl.classList.remove('sd-synopsis-collapsed');
            }
        });
    } else {
        descToggle.classList.add('hidden');
        descEl.classList.remove('sd-synopsis-collapsed');
        descEl.textContent = '';
        descWrap.classList.remove('hidden');
        descEl.innerHTML = `<span class="italic text-base-content/40">No description available - try
            <button onclick="refreshSeriesDetail()" class="text-primary hover:underline">Refresh Metadata</button>
            to pull it from Audible.</span>`;
    }


    const seriesAsin = document.getElementById('seriesDetailPage')?.dataset.seriesAsin || '';
    renderSdBookList(books, title, author, seriesAsin);
}

function renderSdBookList(books, seriesTitle, author, seriesAsin) {
    const listEl = document.getElementById('sdBookList');
    if (books.length === 0) {
        listEl.innerHTML = `
            <div class="text-center py-16 text-base-content/40">
                <i class="fas fa-book-open text-5xl mb-4"></i>
                <p class="text-lg">No books found for this series.</p>
            </div>`;
        document.getElementById('sdShowHiddenWrap').classList.add('hidden');
        return;
    }

    const hiddenCount = books.filter(b => b.monitored === false).length;
    const hiddenWrap = document.getElementById('sdShowHiddenWrap');
    if (hiddenCount > 0) {
        hiddenWrap.classList.remove('hidden');
        document.getElementById('sdShowHiddenLabel').textContent = `Show hidden titles (${hiddenCount})`;
        document.getElementById('sdShowHiddenToggle').checked = _sdShowHidden;
    } else {
        hiddenWrap.classList.add('hidden');
    }

    const visibleBooks = _sdShowHidden ? books : books.filter(b => b.monitored !== false);
    if (visibleBooks.length === 0) {
        listEl.innerHTML = `
            <div class="text-center py-16 text-base-content/40">
                <i class="fas fa-eye-slash text-5xl mb-4"></i>
                <p class="text-lg">All titles in this series are hidden.</p>
            </div>`;
        return;
    }
    listEl.innerHTML = visibleBooks.map((book, i) => renderBookCard(book, i, seriesTitle, author, seriesAsin)).join('');
}

function sdToggleShowHidden(checked) {
    _sdShowHidden = checked;
    const books = Array.isArray(_sdData?.books) ? _sdData.books : [];
    const title = _sdData?.series_title || 'Unknown Series';
    const author = sdGetImportAuthor(_sdData);
    const seriesAsin = document.getElementById('seriesDetailPage')?.dataset.seriesAsin || '';
    renderSdBookList(books, title, author, seriesAsin);
}

async function sdToggleTitleMonitored(btn, asin, monitored, title, author, seriesAsin, seriesTitle) {
    btn.disabled = true;
    try {
        const res = await fetch(`/api/titles/${encodeURIComponent(asin)}/monitored`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                monitored: !monitored,
                title, author,
                series_asin: seriesAsin,
                series_title: seriesTitle,
                source_context: 'series',
            }),
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to update');

        const book = (_sdData?.books || []).find(b => b.asin === asin);
        if (book) book.monitored = !monitored;
        sdNotify(monitored ? 'Title hidden.' : 'Title unhidden.', 'success');
        renderSdBookList(_sdData.books, _sdData.series_title, sdGetImportAuthor(_sdData), seriesAsin);
    } catch (err) {
        sdNotify('Failed to update: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// --- Book Card - styled like the book modal header --------------------------

function renderBookCard(book, index, seriesTitle, importAuthor, seriesAsin) {
    const seq = book.sequence || (index + 1);

    const rawStatus = (book.library_status || '').toLowerCase();
    const isOwned   = rawStatus === 'in_library' || Boolean(book.in_library);
    const isPending = ['wanted', 'queued', 'pending', 'processing'].includes(rawStatus);
    const isMonitored = book.monitored !== false;

    const hideBtn = `
        <button class="btn btn-xs btn-ghost rounded gap-1 text-base-content/40 hover:text-base-content"
                title="${isMonitored ? 'Hide this title' : 'Unhide this title'}"
                onclick="sdToggleTitleMonitored(this, '${sdEsc(book.asin)}', ${isMonitored}, '${sdEsc(book.title)}', '${sdEsc(importAuthor || book.author || '')}', '${sdEsc(seriesAsin || '')}', '${sdEsc(seriesTitle || '')}')">
            <i class="fas ${isMonitored ? 'fa-eye-slash' : 'fa-eye'}"></i>
        </button>`;

    let statusBadge;
    if (isOwned) {
        statusBadge = `<span class="badge badge-success badge-sm rounded">
            <i class="fas fa-check mr-1"></i>In Library
        </span>`;
    } else if (isPending) {
        statusBadge = `<span class="badge badge-warning badge-sm rounded">
            <i class="fas fa-hourglass-half mr-1"></i>Queued
        </span>`;
    } else {
        statusBadge = `<span class="badge badge-ghost badge-sm rounded">Not Owned</span>`;
    }

    let actionBtn = '';
    if (isOwned) {
        actionBtn = `
            <button class="btn btn-xs btn-outline btn-primary rounded gap-1"
                    onclick="openBookModal('${sdEsc(book.asin)}')">
                <i class="fas fa-eye"></i> View
            </button>`;
    } else if (!isPending && book.asin && importAuthor) {
        actionBtn = `
            <button class="btn btn-xs btn-primary rounded gap-1 js-sd-import-btn"
                    data-asin="${sdEsc(book.asin)}"
                    data-author="${sdEsc(importAuthor)}"
                    data-title="${sdEsc(book.title)}"
                    onclick="sdImportBook(this)">
                <i class="fas fa-plus"></i> Add
            </button>`;
    }

    const coverHtml = book.cover_image
        ? `<img src="${sdEsc(book.cover_image)}" alt="Cover"
                class="w-[72px] h-[72px] rounded object-cover bg-base-300 shadow flex-shrink-0">`
        : `<div class="w-[72px] h-[72px] rounded bg-base-300 flex items-center justify-center text-base-content/20 flex-shrink-0">
               <i class="fas fa-book text-2xl"></i>
           </div>`;

    const titleEl = isOwned
        ? `<button onclick="openBookModal('${sdEsc(book.asin)}')"
                   class="font-bold text-base leading-snug hover:text-primary text-left">
               ${sdEsc(book.title)}
           </button>`
        : `<span class="font-bold text-base leading-snug">${sdEsc(book.title)}</span>`;

    const authorParts = [];
    if (book.author) authorParts.push(`by <a href="/authors/${encodeURIComponent(book.author)}" class="text-primary hover:underline">${sdEsc(book.author)}</a>`);
    if (book.narrator && book.narrator !== 'Unknown Narrator')
        authorParts.push(`Narrated by ${sdEsc(book.narrator)}`);
    const authorLine = authorParts.length
        ? `<p class="text-sm text-base-content/60 mt-0.5">${authorParts.join(' &middot; ')}</p>`
        : '';

    const metaBits = [
        book.runtime      ? `<span class="flex items-center gap-1"><i class="fas fa-clock text-xs text-base-content/30"></i>${sdEsc(String(book.runtime))}</span>` : '',
        book.release_date ? `<span class="flex items-center gap-1"><i class="fas fa-calendar text-xs text-base-content/30"></i>${sdEsc(AA.formatDate(book.release_date))}</span>` : '',
        book.publisher && book.publisher !== 'Unknown Publisher'
            ? `<span class="flex items-center gap-1"><i class="fas fa-building text-xs text-base-content/30"></i>${sdEsc(book.publisher)}</span>` : '',
    ].filter(Boolean);
    const metaLine = metaBits.length
        ? `<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-base-content/50 mt-1.5">${metaBits.join('')}</div>`
        : '';

    const ratingVal = parseFloat(book.rating) || 0;
    const ratingHtml = ratingVal > 0
        ? `<div class="flex items-center gap-1 mt-1.5 text-xs">
               <i class="fas fa-star text-warning"></i>
               <span class="font-semibold">${ratingVal.toFixed(1)}</span>
               ${book.num_ratings ? `<span class="text-base-content/40">(${Number(book.num_ratings).toLocaleString()} ratings)</span>` : ''}
           </div>`
        : '';

    return `
    <div${isMonitored ? '' : ' class="opacity-50"'}>
        <p class="text-[11px] font-semibold text-base-content/40 uppercase tracking-wider mb-2">
            Book ${sdEsc(String(seq))}
        </p>
        <div class="card bg-base-200 border border-base-content/5 hover:border-base-content/10 transition-colors">
            <div class="card-body p-4">
                <!-- Header row: matches book modal header layout -->
                <div class="flex items-start gap-4 relative">
                    <!-- Cover -->
                    ${coverHtml}
                    <!-- Info -->
                    <div class="flex-1 min-w-0 pr-2">
                        ${titleEl}
                        <p class="text-[11px] text-base-content/35 mb-0.5">${sdEsc(seriesTitle)}, Book ${sdEsc(String(seq))}</p>
                        ${authorLine}
                        ${metaLine}
                        ${ratingHtml}
                    </div>
                    <!-- Status + action (top-right, matches modal toolbar) -->
                    <div class="flex flex-col items-end gap-2 shrink-0 pt-0.5">
                        ${statusBadge}
                        <div class="flex items-center gap-1">
                            ${actionBtn}
                            ${hideBtn}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>`;
}

// --- Description toggle -----------------------------------------------------

function toggleSeriesDesc() {
    _sdDescExpanded = !_sdDescExpanded;
    document.getElementById('sdDescText')
        .classList.toggle('sd-synopsis-collapsed', !_sdDescExpanded);
    document.getElementById('sdDescToggle').textContent =
        _sdDescExpanded ? 'Show less' : 'Show more';
}

// --- Refresh ----------------------------------------------------------------

async function refreshSeriesDetail() {
    const page = document.getElementById('seriesDetailPage');
    const btn  = document.getElementById('sdBtnRefresh');
    if (!page || !btn) return;

    const seriesAsin = page.dataset.seriesAsin;
    btn.disabled = true;
    btn.classList.add('loading');

    try {
        const res  = await fetch(`/series/api/refresh-series/${seriesAsin}`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            sdNotify('Series metadata refreshed.', 'success');
            _sdDescExpanded = false;
            await loadSeriesDetail(seriesAsin);
        } else {
            sdNotify(data.error || 'Refresh failed.', 'error');
        }
    } catch (err) {
        sdNotify('Refresh failed: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.classList.remove('loading');
    }
}

// --- Import - full series ---------------------------------------------------

async function importFullSeries() {
    const btn         = document.getElementById('sdBtnImport');
    const author      = btn?.dataset.author || '';
    const seriesTitle = btn?.dataset.seriesTitle || '';

    if (!author) { sdNotify('No author context available to import.', 'error'); return; }
    if (!await AA.showConfirmDialog(`Import every missing title from "${seriesTitle}"?`, 'Import Series')) return;

    btn.disabled = true;
    btn.classList.add('loading');

    try {
        const res  = await fetch('/authors/api/import-series', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ author_name: author, series_name: seriesTitle })
        });
        const data = await res.json();
        if (data.success) {
            sdNotify(data.message || 'Import queued.', 'success');
            const asin = document.getElementById('seriesDetailPage')?.dataset.seriesAsin;
            if (asin) await loadSeriesDetail(asin);
        } else {
            sdNotify(data.error || 'Import failed.', 'warning');
        }
    } catch (err) {
        sdNotify('Import failed: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.classList.remove('loading');
    }
}

// --- Import - single book ---------------------------------------------------

async function sdImportBook(btn) {
    const asin   = btn.dataset.asin;
    const author = btn.dataset.author;
    const title  = btn.dataset.title;

    if (!asin || !author) { sdNotify('Missing ASIN or author context.', 'error'); return; }

    btn.disabled = true;
    btn.classList.add('loading');

    try {
        const res  = await fetch('/authors/api/import-book', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ author_name: author, asin })
        });
        const data = await res.json();
        if (data.success) {
            sdNotify(`"${title}" queued for download.`, 'success');
            const seriesAsin = document.getElementById('seriesDetailPage')?.dataset.seriesAsin;
            if (seriesAsin) await loadSeriesDetail(seriesAsin);
        } else {
            sdNotify(data.error || 'Import failed.', 'warning');
        }
    } catch (err) {
        sdNotify('Import failed: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.classList.remove('loading');
    }
}

// --- Book quick-view modal --------------------------------------------------

async function sdOpenBookModal(asin) {
    const modal   = document.getElementById('sdBookModal');
    const content = document.getElementById('sdBookModalContent');
    if (!modal || !content) return;

    content.innerHTML = `
        <div class="flex justify-center py-10">
            <span class="loading loading-spinner loading-lg"></span>
        </div>`;
    modal.showModal();

    try {
        const res  = await fetch(`/library/book/${asin}`);
        const data = await res.json();
        if (!data.success || !data.book) throw new Error(data.error || 'Book not found');
        const book = data.book;
        const e = sdEsc;

        const coverHtml = book.cover_image
            ? `<img src="${e(book.cover_image)}" alt="Cover"
                    class="w-[72px] h-[72px] rounded object-cover bg-base-300 shadow flex-shrink-0">`
            : `<div class="w-[72px] h-[72px] rounded bg-base-300 flex items-center justify-center flex-shrink-0">
                   <i class="fas fa-book text-2xl text-base-content/20"></i>
               </div>`;

        const rawSt = (book.status || '').toLowerCase();
        const stMap = {
            downloaded: ['badge-success','Downloaded'],
            downloading:['badge-info',   'Downloading'],
            queued:     ['badge-warning','Queued'],
            owned:      ['badge-success','Owned'],
            wanted:     ['badge-ghost',  'Wanted'],
        };
        const [stCls, stText] = stMap[rawSt] || ['badge-ghost', book.status || ''];
        const statusBadge = stText
            ? `<span class="badge ${stCls} badge-sm rounded">${e(stText)}</span>` : '';

        const rVal = parseFloat(book.rating) || 0;

        const rawSynopsis = book.synopsis || book.summary || '';
        const synopsis = rawSynopsis
            .split(/<\/?\s*p\s*>/i)
            .map(chunk => chunk.replace(/<[^>]*>/g, '').trim())
            .filter(Boolean)
            .join('\n\n');

        const dlRows = [
            book.narrator && book.narrator !== 'Unknown'
                ? `<div class="flex items-start gap-2">
                       <i class="fas fa-microphone w-4 mt-0.5 text-base-content/30 text-xs flex-shrink-0"></i>
                       <div><dt class="text-xs text-base-content/40">Narrator</dt><dd class="font-medium">${e(book.narrator)}</dd></div>
                   </div>` : '',
            book.runtime
                ? `<div class="flex items-start gap-2">
                       <i class="fas fa-clock w-4 mt-0.5 text-base-content/30 text-xs flex-shrink-0"></i>
                       <div><dt class="text-xs text-base-content/40">Runtime</dt><dd class="font-medium">${e(book.runtime)}</dd></div>
                   </div>` : '',
            book.release_date && book.release_date !== 'Unknown'
                ? `<div class="flex items-start gap-2">
                       <i class="fas fa-calendar w-4 mt-0.5 text-base-content/30 text-xs flex-shrink-0"></i>
                       <div><dt class="text-xs text-base-content/40">Released</dt><dd class="font-medium">${e(AA.formatDate(book.release_date))}</dd></div>
                   </div>` : '',
            book.publisher && book.publisher !== 'Unknown'
                ? `<div class="flex items-start gap-2">
                       <i class="fas fa-building w-4 mt-0.5 text-base-content/30 text-xs flex-shrink-0"></i>
                       <div><dt class="text-xs text-base-content/40">Publisher</dt><dd class="font-medium">${e(book.publisher)}</dd></div>
                   </div>` : '',
        ].filter(Boolean).join('');

        content.innerHTML = `
            <!-- Header: cover + title block matching book modal -->
            <div class="flex items-start gap-4 border-b border-base-content/10 pb-4 relative pr-8">
                ${coverHtml}
                <div class="flex-1 min-w-0">
                    <div class="flex flex-wrap gap-1.5 mb-1.5">${statusBadge}</div>
                    <h2 class="text-xl font-bold leading-tight">${e(book.title || 'Unknown')}</h2>
                    <p class="text-sm text-base-content/60 mt-0.5">
                        by <a href="/authors/${encodeURIComponent(book.author || '')}" class="text-primary hover:underline">${e(book.author || '')}</a>
                    </p>
                    ${rVal > 0 ? `
                    <div class="flex items-center gap-1 mt-2 text-xs">
                        <i class="fas fa-star text-warning"></i>
                        <span class="font-semibold">${rVal.toFixed(1)}</span>
                        ${book.num_ratings ? `<span class="text-base-content/40">(${Number(book.num_ratings).toLocaleString()} ratings)</span>` : ''}
                    </div>` : ''}
                </div>
            </div>

            <!-- Body: synopsis + sidebar -->
            <div class="grid grid-cols-1 sm:grid-cols-[1fr_160px] gap-5 pt-4">
                ${synopsis
                    ? `<div>
                           <h3 class="text-xs font-semibold uppercase tracking-wider text-base-content/40 mb-2">Synopsis</h3>
                           <div class="text-sm text-base-content/80 whitespace-pre-line leading-relaxed max-h-44 overflow-y-auto">${e(synopsis)}</div>
                       </div>`
                    : '<div></div>'}
                <dl class="space-y-3 text-sm">
                    <h3 class="text-xs font-semibold uppercase tracking-wider text-base-content/40">Details</h3>
                    ${dlRows}
                </dl>
            </div>

            <!-- Footer actions -->
            <div class="flex justify-end mt-5 pt-4 border-t border-base-content/10 gap-2">
                <a href="/library" class="btn btn-soft btn-sm btn-primary rounded">
                    <i class="fas fa-books mr-1"></i> Open in Library
                </a>
            </div>`;
    } catch (err) {
        content.innerHTML = `
            <div class="alert alert-error">
                <i class="fas fa-exclamation-circle"></i>
                <span>${sdEsc(err.message)}</span>
            </div>`;
    }
}

// --- Utilities --------------------------------------------------------------

function sdGetImportAuthor(data) {
    if (data?.primary_author) return data.primary_author;
    if (Array.isArray(data?.author_candidates) && data.author_candidates.length > 0)
        return data.author_candidates[0];
    const fallback = (data?.books || []).find(
        b => b.author && b.author.toLowerCase() !== 'unknown author'
    );
    return fallback?.author || '';
}

function sdShowState(state) {
    document.getElementById('sdLoadingState').classList.toggle('hidden', state !== 'loading');
    document.getElementById('sdErrorState').classList.toggle('hidden',   state !== 'error');
    document.getElementById('sdContent').classList.toggle('hidden',      state !== 'content');
}

function sdEsc(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g,  '&amp;')
        .replace(/</g,  '&lt;')
        .replace(/>/g,  '&gt;')
        .replace(/"/g,  '&quot;')
        .replace(/'/g,  '&#39;');
}

let _sdNotifyTimer = null;
function sdNotify(message, type = 'info') {
    let toast = document.getElementById('sdToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'sdToast';
        toast.className = 'toast toast-top toast-end z-50';
        document.body.appendChild(toast);
    }
    const cls = { success: 'alert-success', error: 'alert-error', warning: 'alert-warning', info: 'alert-info' }[type] || 'alert-info';
    toast.innerHTML = `<div class="alert ${cls} shadow-lg"><span>${sdEsc(message)}</span></div>`;
    toast.classList.remove('hidden');
    clearTimeout(_sdNotifyTimer);
    _sdNotifyTimer = setTimeout(() => toast.classList.add('hidden'), 3500);
}
