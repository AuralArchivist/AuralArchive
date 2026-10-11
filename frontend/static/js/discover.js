(function () {
    const configElement = document.getElementById('discover-config');
    let config = {
        recommendationsConfigured: false,
        recommendationsEndpoint: ''
    };

    if (configElement && configElement.textContent.trim()) {
        try {
            config = JSON.parse(configElement.textContent);
        } catch (error) {
        }
    }

    const resultsContainer = document.getElementById('discover-results');
    const emptyState = document.getElementById('discover-empty');
    const statusBadge = document.getElementById('recommendationStatus');
    const refreshButton = document.getElementById('refreshRecommendationsBtn');
    const gridToggle = document.getElementById('discover-view-grid');
    const listToggle = document.getElementById('discover-view-list');
    const tableToggle = document.getElementById('discover-view-table');
    const emptyRefreshButton = document.getElementById('discover-empty-refresh');

    const defaultCover = '/static/images/auralarchive_logo.png';
    const VIEW_PREF_KEY = 'aa-discover-view';
    const recommendationLimit = 40; // over-fetch to account for filtering/dedup
    const recommendationDisplayLimit = 16;

    let currentView = loadViewPreference();
    let isFetching = false;
    let latestRecommendations = [];

    if (!resultsContainer || !config.recommendationsConfigured) {
        if (refreshButton) {
            refreshButton.disabled = true;
        }
        if (gridToggle) {
            gridToggle.disabled = true;
        }
        if (listToggle) {
            listToggle.disabled = true;
        }
        if (emptyRefreshButton) {
            emptyRefreshButton.disabled = true;
        }
        if (tableToggle) {
            tableToggle.disabled = true;
        }
        updateStatus('Unavailable', 'unavailable');
        return;
    }

    function loadViewPreference() {
        try {
            const stored = localStorage.getItem(VIEW_PREF_KEY);
            if (!stored) {
                return 'grid';
            }
            const parsed = JSON.parse(stored);
            return ['grid', 'list', 'table'].includes(parsed) ? parsed : 'grid';
        } catch (_) {
            return 'grid';
        }
    }

    function persistViewPreference(view) {
        try {
            localStorage.setItem(VIEW_PREF_KEY, JSON.stringify(view));
        } catch (_) {
            /* ignore persistence errors */
        }
    }

    function applyViewToggle() {
        if (!resultsContainer) {
            return;
        }
        resultsContainer.dataset.view = currentView;
        if (currentView === 'grid') {
            resultsContainer.style.display = 'grid';
        } else {
            resultsContainer.style.removeProperty('display');
        }
        if (gridToggle) {
            gridToggle.classList.toggle('is-active', currentView === 'grid');
        }
        if (listToggle) {
            listToggle.classList.toggle('is-active', currentView === 'list');
        }
        if (tableToggle) {
            tableToggle.classList.toggle('is-active', currentView === 'table');
        }
    }

    function setView(view) {
    const validViews = ['grid', 'list', 'table'];
        if (!validViews.includes(view)) {
            view = 'grid';
        }
        if (currentView === view) {
            return;
        }
        currentView = view;
        persistViewPreference(view);
        applyViewToggle();
        renderCurrentView();
    }

    function updateStatus(text, variant) {
        if (!statusBadge) {
            return;
        }

        const variants = {
            ready: 'badge badge-outline badge-sm capitalize',
            loading: 'badge badge-warning badge-sm capitalize',
            error: 'badge badge-error badge-sm capitalize',
            unavailable: 'badge badge-outline badge-sm capitalize'
        };

        statusBadge.className = variants[variant] || variants.ready;
        statusBadge.textContent = text;
    }

    function toggleEmptyState(show) {
        if (!emptyState) {
            return;
        }
        emptyState.classList.toggle('hidden', !show);
        resultsContainer.classList.toggle('hidden', show);
    }

    function clearResults() {
        resultsContainer.innerHTML = '';
    }

    function renderSkeleton(count) {
        clearResults();
        const limit = count || 6;
        for (let index = 0; index < limit; index += 1) {
            const skeleton = document.createElement('div');
            skeleton.className = 'search-card search-card--skeleton animate-pulse';
            skeleton.innerHTML = `
                <div class="search-card-shell card card-side bg-base-100 shadow-sm">
                    <figure class="search-card-media bg-base-200"></figure>
                    <div class="card-body search-card-content space-y-3">
                        <div class="h-6 bg-base-content/10 rounded"></div>
                        <div class="h-6 bg-base-content/10 rounded w-5/6"></div>
                        <div class="h-4 bg-base-content/5 rounded w-3/4"></div>
                    </div>
                </div>
            `;
            resultsContainer.appendChild(skeleton);
        }
        toggleEmptyState(false);
    }

    function renderRecommendations(items) {
        const normalized = Array.isArray(items) ? items : [];
        latestRecommendations = normalized.slice(0, recommendationDisplayLimit);

        if (!latestRecommendations.length) {
            clearResults();
            toggleEmptyState(true);
            return;
        }

        if (!['grid', 'list', 'table'].includes(currentView)) {
            currentView = 'grid';
        }

        toggleEmptyState(false);
        renderCurrentView();
    }

    function renderCurrentView() {
        if (!resultsContainer) {
            return;
        }

        applyViewToggle();

        if (!latestRecommendations.length) {
            return;
        }

        clearResults();

        if (currentView === 'grid') {
            renderGridView(latestRecommendations);
        } else if (currentView === 'list') {
            renderListView(latestRecommendations);
        } else if (currentView === 'table') {
            renderTableView(latestRecommendations);
        }
    }

    function renderGridView(items) {
        items.forEach((item) => {
            const card = document.createElement('article');
            card.className = 'search-card';
            card.tabIndex = 0;
            const status = getLibraryStatus(item);
            if (status === 'in_library') {
                card.classList.add('is-in-library');
            } else if (status === 'wanted') {
                card.classList.add('is-wanted');
            }
            card.innerHTML = createCardMarkup(item);
            card.dataset.asin = resolveAsin(item);
            resultsContainer.appendChild(card);
            attachCardInteractions(card, item);
        });
    }

    function renderListView(items) {
        const wrapper = document.createElement('div');
        wrapper.className = 'discover-list-view overflow-x-auto';
        const rows = items.map((item) => createCompactRow(item)).join('');
        wrapper.innerHTML = `
            <table class="table table-xs">
                <thead>
                    <tr>
                        <th class="w-6"></th>
                        <th class="w-10">Cover</th>
                        <th class="w-48">Title</th>
                        <th class="w-40">Author</th>
                        <th class="w-40">Series</th>
                        <th class="w-40">Narrator</th>
                        <th class="w-20">Runtime</th>
                        <th class="w-20">Rating</th>
                        <th class="w-16">Status</th>
                        <th class="w-16"></th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;
        resultsContainer.appendChild(wrapper);
        attachStructuredInteractions(wrapper);
    }

    function renderTableView(items) {
        const wrapper = document.createElement('div');
        wrapper.className = 'discover-table-view overflow-x-auto';
        const rows = items.map((item) => createTableRow(item)).join('');
        wrapper.innerHTML = `
            <table class="table table-zebra">
                <thead>
                    <tr>
                        <th class="w-16"></th>
                        <th>Cover</th>
                        <th>Title</th>
                        <th>Author</th>
                        <th>Series</th>
                        <th>Narrator</th>
                        <th>Runtime</th>
                        <th>Rating</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;
        resultsContainer.appendChild(wrapper);
        attachStructuredInteractions(wrapper);
    }

    function attachCardInteractions(card, item) {
        if (!card) {
            return;
        }

        const modalTriggers = card.querySelectorAll('[data-action="open-modal"]');
        modalTriggers.forEach((trigger) => {
            trigger.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                openRecommendationModal(item);
            });
        });

        const addToLibraryBtn = card.querySelector('[data-action="card-add-to-library"]');
        if (addToLibraryBtn) {
            addToLibraryBtn.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                const asin = addToLibraryBtn.dataset.asin;
                const book = asin ? findRecommendationByAsin(asin) : item;
                addRecommendationToLibrary(book || item, addToLibraryBtn);
            });
        }

        card.addEventListener('click', (event) => {
            if (resultsContainer.dataset.view !== 'grid') {
                return;
            }
            if (event.target.closest('button, a')) {
                return;
            }
            openRecommendationModal(item);
        });

        card.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' || resultsContainer.dataset.view !== 'grid') {
                return;
            }
            if (event.target.closest('button, a')) {
                return;
            }
            openRecommendationModal(item);
        });
    }

    function attachStructuredInteractions(root) {
        if (!root) {
            return;
        }

        root.querySelectorAll('.discover-row').forEach((row) => {
            const asin = row.dataset.asin;
            if (!asin) {
                return;
            }
            row.tabIndex = 0;
            row.addEventListener('click', () => openRecommendationByAsin(asin));
            row.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    openRecommendationByAsin(asin);
                }
            });
        });

        root.querySelectorAll('[data-action="open-modal"]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                openRecommendationByAsin(button.dataset.asin);
            });
        });

        root.querySelectorAll('[data-action="row-add-to-library"]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                const asin = button.dataset.asin;
                const book = findRecommendationByAsin(asin);
                if (!book) {
                    return;
                }
                addRecommendationToLibrary(book, button);
            });
        });
    }

    function openRecommendationByAsin(asin) {
        const book = findRecommendationByAsin(asin);
        if (book) {
            openRecommendationModal(book);
        }
    }

    function findRecommendationByAsin(asin) {
        if (!asin) {
            return null;
        }
        return latestRecommendations.find((item) => resolveAsin(item) === asin) || null;
    }

    function createCompactRow(item) {
        const asin = escapeHtml(resolveAsin(item));
        const cover = buildCoverCell(item, 8);
        const title = escapeHtml(item.title || item.Title || 'Unknown Title');
        const releaseYear = resolveReleaseYear(item);
        const author = escapeHtml(resolveAuthor(item));
        const series = buildSeriesCell(item, true);
        const narrator = buildNarratorCell(item, true);
        const runtime = buildRuntimeCell(item, true);
        const rating = buildRatingCell(item, true);
    const status = buildStatusBadge(item, true);
    const actionButton = buildActionControl(item, 'list');

        return `
            <tr class="discover-row hover cursor-pointer" data-asin="${asin}">
                <td>
                    <div class="selection-checkbox hidden">
                        <input type="checkbox" class="checkbox checkbox-primary checkbox-xs" aria-label="Select ${title}">
                    </div>
                </td>
                <td>${cover}</td>
                <td>
                    <div class="font-semibold text-xs line-clamp-1">${title}</div>
                    ${releaseYear ? `<div class="text-[10px] opacity-50">${escapeHtml(releaseYear)}</div>` : ''}
                </td>
                <td><span class="text-xs line-clamp-1">${author}</span></td>
                <td>${series}</td>
                <td>${narrator}</td>
                <td>${runtime}</td>
                <td>${rating}</td>
                <td>${status}</td>
                <td>
                    <div class="flex gap-0.5">
                        <button class="btn btn-soft btn-secondary btn-xs" data-action="open-modal" data-asin="${asin}" type="button" style="padding: 0.125rem 0.375rem;">
                            <i class="fas fa-info" style="font-size: 0.625rem;"></i>
                        </button>
                        ${actionButton}
                    </div>
                </td>
            </tr>
        `;
    }

    function createTableRow(item) {
        const asin = escapeHtml(resolveAsin(item));
        const cover = buildCoverCell(item, 16);
        const title = escapeHtml(item.title || item.Title || 'Unknown Title');
        const author = escapeHtml(resolveAuthor(item));
        const series = buildSeriesCell(item, false);
        const narrator = buildNarratorCell(item, false);
        const runtime = buildRuntimeCell(item, false);
        const rating = buildRatingCell(item, false);
    const status = buildStatusBadge(item, false);
    const actionButton = buildActionControl(item, 'table');

        return `
            <tr class="discover-row hover cursor-pointer" data-asin="${asin}">
                <td>
                    <div class="selection-checkbox hidden">
                        <input type="checkbox" class="checkbox checkbox-primary checkbox-sm" aria-label="Select ${title}">
                    </div>
                </td>
                <td>${cover}</td>
                <td>
                    <div class="font-bold line-clamp-1">${title}</div>
                </td>
                <td><span class="line-clamp-1">${author}</span></td>
                <td>${series}</td>
                <td>${narrator}</td>
                <td>${runtime}</td>
                <td>${rating}</td>
                <td>${status}</td>
                <td>
                    <div class="flex gap-1">
                        <button class="btn btn-soft btn-secondary btn-xs" data-action="open-modal" data-asin="${asin}" type="button">
                            <i class="fas fa-info"></i>
                        </button>
                        ${actionButton}
                    </div>
                </td>
            </tr>
        `;
    }

    function buildCoverCell(item, size) {
        const title = escapeHtml(item.title || item.Title || 'Unknown Title');
        const cover = item.cover_image || item.cover_url;
        const dimension = size === 16 ? 'w-16 h-16' : 'w-8 h-8';
        const placeholderSize = size === 16 ? 'text-base-content/30' : 'text-base-content/40';
        if (cover) {
            const safeCover = escapeHtml(cover);
            return `
                <div class="avatar">
                    <div class="${dimension}" style="border-radius: 0.25rem;">
                        <img src="${safeCover}" alt="${title}" style="border-radius: 0.25rem;" />
                    </div>
                </div>
            `;
        }
        return `
            <div class="avatar">
                <div class="${dimension} bg-base-300 flex items-center justify-center" style="border-radius: 0.25rem;">
                    <i class="fas fa-book ${placeholderSize}"></i>
                </div>
            </div>
        `;
    }

    function buildSeriesCell(item, compact) {
        const series = item.series || item.Series;
        const sequence = item.sequence || item.Sequence;
        if (series && series !== 'N/A') {
            const sequenceLabel = sequence && sequence !== 'N/A' ? `<div class="${compact ? 'text-[10px]' : 'text-xs'} opacity-50">${compact ? `Book #${escapeHtml(sequence)}` : `#${escapeHtml(sequence)}`}</div>` : '';
            return `
                <div class="${compact ? 'text-xs' : ''} line-clamp-1">${escapeHtml(series)}</div>
                ${sequenceLabel}
            `;
        }
        return `<span class="opacity-40 ${compact ? 'text-xs' : ''}">-</span>`;
    }

    function buildNarratorCell(item, compact) {
        const narrator = getPrimaryNarrator(item.narrator || item.Narrator || '');
        if (narrator) {
            return `<span class="${compact ? 'text-xs' : ''} line-clamp-1">${escapeHtml(narrator)}</span>`;
        }
        return `<span class="opacity-40 ${compact ? 'text-xs' : ''}">-</span>`;
    }

    function buildRuntimeCell(item, compact) {
        const runtime = resolveRuntime(item);
        if (runtime) {
            return `<span class="${compact ? 'text-xs' : ''} whitespace-nowrap">${escapeHtml(runtime)}</span>`;
        }
        return `<span class="opacity-40 ${compact ? 'text-xs' : ''}">-</span>`;
    }

    function buildRatingCell(item, compact) {
        const ratingValue = parseFloat(item.rating || item['Overall Rating'] || 0);
        if (Number.isNaN(ratingValue) || ratingValue <= 0) {
            return `<span class="opacity-40 ${compact ? 'text-xs' : ''}">-</span>`;
        }
        const count = Number(item.num_ratings || item.numRatings || item.NumRatings || 0);
        const countLabel = count > 0 ? `<div class="${compact ? 'text-[10px]' : 'text-xs'} opacity-50">(${formatRatingsCount(count)})</div>` : '';
        return `
            <div class="flex items-center gap-0.5 ${compact ? '' : 'text-sm'}">
                <i class="fas fa-star text-warning" style="font-size: ${compact ? '0.625rem' : '0.75rem'};"></i>
                <span class="${compact ? 'text-xs' : ''}">${ratingValue.toFixed(1)}</span>
            </div>
            ${countLabel}
        `;
    }

    function buildStatusBadge(item, compact) {
        const status = getLibraryStatus(item);
        let pillClass = 'meta-pill meta-pill--status meta-pill--status-info';
        let icon = 'bookmark';
        let label = 'Not in Library';

        if (status === 'in_library') {
            pillClass = 'meta-pill meta-pill--status meta-pill--status-success';
            icon = 'check-circle';
            label = 'In Library';
        } else if (status === 'wanted') {
            pillClass = 'meta-pill meta-pill--status meta-pill--status-warning';
            icon = 'star';
            label = 'Wanted';
        } else if (status === 'downloading') {
            pillClass = 'meta-pill meta-pill--status meta-pill--status-info';
            icon = 'download';
            label = 'Downloading';
        }

        return `
            <span class="${pillClass} ${compact ? 'text-[10px]' : ''}"><i class="fas fa-${icon}"></i><span>${label}</span></span>
        `;
    }

    function buildActionControl(item, context = 'table') {
        const asin = escapeHtml(resolveAsin(item));
        const status = getLibraryStatus(item);
        if (status !== 'not_in_library') {
            return '';
        }

        const label = context === 'table' ? ' Add' : '';
        return `<button class="btn btn-soft btn-primary btn-xs" data-action="row-add-to-library" data-asin="${asin}"><i class="fas fa-plus"></i>${label}</button>`;
    }

    function resolveRuntime(item) {
        return item.runtime || item.Runtime || '';
    }

    function formatRatingsCount(count) {
        if (count >= 1000) {
            return `${(count / 1000).toFixed(1)}k`;
        }
        return formatNumber(count);
    }

    function resolveReleaseYear(item) {
        const raw = item.release_date || item['Release Date'];
        if (!raw) {
            return '';
        }
        const match = String(raw).match(/(\d{4})/);
        return match ? match[1] : '';
    }

    function getLibraryStatus(item) {
        if (!item) {
            return 'not_in_library';
        }

        const rawStatus = item.library_status
            || item.status
            || item.Status
            || item.ownership_status
            || (item.in_library ? 'in_library' : '');

        const normalized = String(rawStatus || '').toLowerCase();

        if (['in_library', 'owned', 'audible_library', 'completed'].includes(normalized)) {
            return 'in_library';
        }
        if (normalized === 'wanted') {
            return 'wanted';
        }
        if (normalized === 'downloading') {
            return 'downloading';
        }

        return 'not_in_library';
    }

    function isOwned(item) {
        return getLibraryStatus(item) === 'in_library';
    }

    function createCardMarkup(item) {
        const rawTitle = item.title || item.Title || 'Unknown Title';
        const safeTitle = escapeHtml(rawTitle);
        const author = escapeHtml(resolveAuthor(item));
        const cover = escapeHtml(item.cover_image || item.cover_url || defaultCover);
        const asin = escapeHtml(resolveAsin(item));
        const seriesTitle = item.series || item.Series || '';
        const sequence = item.sequence || item.Sequence;
        const seriesLabel = seriesTitle ? `${seriesTitle}${sequence ? ` · Book ${sequence}` : ''}` : '';
        const runtime = resolveRuntime(item);
        const ratingValue = parseFloat(item.rating || item['Overall Rating'] || 0);
        const ratingCount = Number(item.num_ratings || item.numRatings || item.NumRatings || 0);
        const narrator = getPrimaryNarrator(item.narrator || item.Narrator || '');
        const status = getLibraryStatus(item);
        const source = formatSource(item.search_source || item.searchSource || 'Audible');
        const chipRow = item.download_available
            ? '<span class="discover-chip discover-chip--accent"><i class="fas fa-download"></i> Ready</span>'
            : '';

        const statusLabels = {
            in_library: 'In Library',
            wanted: 'Wanted',
            downloading: 'Downloading',
            not_in_library: 'Not in Library'
        };

        const statusClass = status === 'in_library'
            ? 'discover-status discover-status--success'
            : status === 'wanted'
                ? 'discover-status discover-status--warning'
                : status === 'downloading'
                    ? 'discover-status discover-status--info'
                    : 'discover-status';

        const addAction = status === 'not_in_library' && asin
            ? `<button class="discover-btn discover-btn--primary" type="button" data-action="card-add-to-library" data-asin="${asin}">+ Add to Library</button>`
            : `<span class="${statusClass}">${statusLabels[status] || 'In Library'}</span>`;

        const ratingMarkup = (!Number.isNaN(ratingValue) && ratingValue > 0)
            ? `<div class="discover-rating">
                    <span class="discover-rating__star">★</span>
                    <span class="discover-rating__value">${ratingValue.toFixed(1)}</span>
                    ${ratingCount > 0 ? `<span class="discover-rating__count">(${formatRatingsCount(ratingCount)})</span>` : ''}
               </div>`
            : '<div class="discover-pill discover-pill--muted">New pick</div>';

        const inspectorHref = `/search?query=${encodeURIComponent(asin || rawTitle)}`;
        const audibleHref = '';

        const seriesMeta = seriesLabel
            ? `<div class="discover-meta-item"><i class="fas fa-layer-group"></i><span>${escapeHtml(seriesLabel)}</span></div>`
            : '';

        const narratorMeta = narrator
            ? `<div class="discover-meta-item"><i class="fas fa-microphone"></i><span>${escapeHtml(narrator)}</span></div>`
            : '';

        const runtimeMeta = runtime
            ? `<div class="discover-meta-item"><i class="fas fa-clock"></i><span>${escapeHtml(runtime)}</span></div>`
            : '';

        return `
            <div class="discover-card">
                <div class="discover-media">
                    <div class="discover-cover">
                        <img src="${cover}" alt="${safeTitle} cover" loading="lazy" class="discover-cover__img">
                        ${chipRow ? `<div class="discover-chip-row">${chipRow}</div>` : ''}
                    </div>
                </div>
                <div class="discover-body">
                    <div class="discover-title">${safeTitle}</div>
                    <div class="discover-author">${author}</div>
                    <div class="discover-meta-list">
                        ${seriesMeta}
                        ${narratorMeta}
                        ${runtimeMeta}
                    </div>
                    <div class="discover-rating-row">${ratingMarkup}</div>
                </div>
                <div class="discover-actions">
                    <button class="discover-btn" type="button" data-action="open-modal">Details</button>
                    <a href="${escapeHtml(inspectorHref)}" class="discover-btn discover-btn--ghost">Similar</a>
                    ${addAction}
                </div>
            </div>
        `;
    }

    function resolveAsin(item) {
        if (!item) {
            return '';
        }
        return item.asin || item.ASIN || item.id || '';
    }

    function resolveAuthor(item) {
        const raw = item.author || item.Author || item.authors;
        if (!raw) {
            return 'Unknown Author';
        }
        if (Array.isArray(raw)) {
            return raw.join(', ');
        }
        return String(raw);
    }

    function createMetaPills(item) {
        const pills = [];

        const status = getLibraryStatus(item);
        if (status === 'in_library') {
            pills.push(metaPill('check-circle', 'In Library', 'meta-pill meta-pill--status meta-pill--status-success'));
        } else if (status === 'wanted') {
            pills.push(metaPill('star', 'Wanted', 'meta-pill meta-pill--status meta-pill--status-warning'));
        } else if (status === 'downloading') {
            pills.push(metaPill('download', 'Downloading', 'meta-pill meta-pill--status meta-pill--status-info'));
        } else {
            pills.push(metaPill('bookmark', 'Not in Library', 'meta-pill meta-pill--status meta-pill--status-info'));
        }

        const rating = parseFloat(item.rating || item['Overall Rating'] || 0);
        const ratingsCount = Number(item.num_ratings || item.numRatings || item.NumRatings || 0);
        if (!Number.isNaN(rating) && rating > 0) {
            const countLabel = ratingsCount > 0 ? `<span class="meta-pill-sub">${formatNumber(ratingsCount)} ratings</span>` : '';
            pills.push(`<span class="meta-pill meta-pill--rating"><i class="fas fa-star"></i><span>${rating.toFixed(1)}</span>${countLabel}</span>`);
        }

        const series = item.series || item.Series;
        const sequence = item.sequence || item.Sequence;
        if (series && series !== 'N/A') {
            let label = String(series);
            if (sequence) {
                label += ` - Book ${sequence}`;
            }
            pills.push(metaPill('layer-group', label));
        }

        const runtime = item.runtime || item.Runtime;
        if (runtime) {
            pills.push(metaPill('clock', runtime));
        }

        const language = item.language || item.Language;
        if (language) {
            pills.push(metaPill('language', language));
        }

        const narrator = getPrimaryNarrator(item.narrator || item.Narrator);
        if (narrator) {
            pills.push(metaPill('microphone', narrator));
        }

        const releaseDate = item.release_date || item['Release Date'];
        if (releaseDate) {
            pills.push(metaPill('calendar-alt', releaseDate));
        }

        const source = item.search_source || item.searchSource;
        if (source) {
            pills.push(metaPill('signal', formatSource(source), 'meta-pill meta-pill--neutral'));
        }

        if (item.download_available) {
            pills.push(metaPill('download', 'Downloadable', 'meta-pill meta-pill--accent'));
        }

        return pills.join('');
    }

    function metaPill(icon, label, classes) {
        const safeLabel = escapeHtml(String(label));
        const pillClasses = classes || 'meta-pill';
        return `<span class="${pillClasses}"><i class="fas fa-${icon}"></i><span>${safeLabel}</span></span>`;
    }

    function createActionRow(asin, title) {
        const queueTarget = asin || title || '';
        const inspectHref = `/search?query=${encodeURIComponent(queueTarget)}`;
        const inspectButton = `<a href="${escapeHtml(inspectHref)}" class="btn btn-sm btn-primary">Inspect</a>`;
        const audibleHref = asin ? `https://www.audible.com/pd?asin=${encodeURIComponent(asin)}` : '';
        const audibleButton = audibleHref
            ? `<a href="${escapeHtml(audibleHref)}" target="_blank" rel="noopener" class="btn btn-sm btn-secondary">Audible</a>`
            : '';
        return `${inspectButton}${audibleButton ? ` ${audibleButton}` : ''}`;
    }

    function cleanSummary(value) {
        if (!value) {
            return '';
        }
        const text = String(value).replace(/<[^>]+>/g, ' ');
        const collapsed = text.replace(/\s+/g, ' ').trim();
        if (!collapsed) {
            return '';
        }
        return truncated(collapsed, 220);
    }

    function truncated(text, max) {
        if (text.length <= max) {
            return text;
        }
        return `${text.slice(0, max - 3).trim()}...`;
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function buildSummaryHtml(value) {
        const text = extractSummaryText(value);
        if (!text) {
            return '';
        }
        const paragraphs = text
            .split(/\n{2,}/)
            .map((segment) => segment.trim())
            .filter(Boolean);
        if (!paragraphs.length) {
            paragraphs.push(text);
        }
        return paragraphs.map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join('');
    }

    function extractSummaryText(value) {
        if (!value) {
            return '';
        }
        return String(value)
            .replace(/<br\s*\/?>(?=\s*<br\s*\/?>(\s|$))/gi, '\n\n')
            .replace(/<br\s*\/?/gi, '\n')
            .replace(/<\/(p|div)>/gi, '\n\n')
            .replace(/<[^>]+>/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function openRecommendationModal(book) {
        if (!book) {
            return;
        }

        const modal = document.createElement('div');
        modal.className = 'modal modal-open';

        const title = escapeHtml(book.title || book.Title || 'Unknown Title');
        const author = escapeHtml(resolveAuthor(book));
        const coverMarkup = AA.bookCoverImg(book.cover_image || book.cover_url || defaultCover, (book.title || book.Title || '') + ' cover', 'book-modal__cover');
        const metaSection = createMetaPills(book);
        const summaryHtml = buildSummaryHtml(book.summary || book.Summary);
        const inLibrary = isOwned(book) || Boolean(book.in_library);
        const seriesLabel = book.series || book.Series;
        const sequence = book.sequence || book.Sequence;

        const addButton = inLibrary
            ? '<button class="btn btn-sm btn-outline" disabled>In Library</button>'
            : '<button class="btn btn-sm btn-primary" data-action="modal-add-library"><i class="fas fa-plus"></i> Add to Library</button>';

        modal.innerHTML = `
            <div class="modal-box max-w-5xl p-0 book-modal-shell">
                <button class="btn btn-sm btn-ghost book-modal-close" aria-label="Close" data-action="close-modal">
                    <i class="fas fa-times"></i>
                </button>
                <div class="book-modal">
                    <div class="book-modal__media">
                        ${coverMarkup}
                        ${book.download_available ? '<span class="book-modal__badge">Download Ready</span>' : ''}
                    </div>
                    <div class="book-modal__details">
                        ${seriesLabel && seriesLabel !== 'N/A' ? `<span class="book-modal__series">${escapeHtml(seriesLabel)}${sequence ? ` · Book ${escapeHtml(sequence)}` : ''}</span>` : ''}
                        <h2 class="book-modal__title">${title}</h2>
                        <p class="book-modal__author">By ${author}</p>
                        ${metaSection ? `<div class="book-modal__meta">${metaSection}</div>` : ''}
                        ${summaryHtml ? `<div class="book-modal__summary">${summaryHtml}</div>` : ''}
                        <div class="book-modal__actions">
                            ${addButton}
                            <button class="btn btn-sm btn-outline" data-action="close-modal">Close</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        const closeModal = () => {
            if (modal && modal.parentNode) {
                modal.parentNode.removeChild(modal);
            }
        };

        modal.addEventListener('click', (event) => {
            if (event.target === modal) {
                closeModal();
            }
        });

        modal.querySelectorAll('[data-action="close-modal"]').forEach((button) => {
            button.addEventListener('click', closeModal);
        });

        const addBtn = modal.querySelector('[data-action="modal-add-library"]');
        if (addBtn) {
            addBtn.addEventListener('click', () => addRecommendationToLibrary(book, addBtn, closeModal));
        }
    }

    function addRecommendationToLibrary(book, button, closeModal) {
        if (!book || !button) {
            return;
        }

        const original = button.innerHTML;
        button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Adding...';
        button.disabled = true;

        fetch('/search/add-book', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(book)
        })
            .then((response) => response.json())
            .then((payload) => {
                if (!payload.success) {
                    throw new Error(payload.error || 'Failed to add book');
                }

                book.in_library = true;
                book.library_status = 'in_library';
                book.status = 'owned';
                book.Status = 'owned';
                if (typeof showNotification === 'function') {
                    showNotification(payload.message || 'Book added to library', 'success');
                }

                if (typeof closeModal === 'function') {
                    closeModal();
                }

                renderCurrentView();
            })
            .catch((error) => {
                if (typeof showNotification === 'function') {
                    showNotification(error.message || 'Unable to add book to library', 'error');
                }
                button.innerHTML = original;
                button.disabled = false;
            });
    }

    function getPrimaryNarrator(narrator) {
        if (!narrator) {
            return '';
        }

        const parts = String(narrator)
            .split(/,|&| and /i)
            .map((segment) => segment.trim())
            .filter(Boolean);

        return parts.length ? parts[0] : String(narrator).trim();
    }

    function formatSource(sourceValue) {
        if (!sourceValue) {
            return '';
        }
        const normalized = String(sourceValue).replace(/[_-]+/g, ' ');
        return normalized.replace(/\b\w/g, (char) => char.toUpperCase());
    }

    function formatNumber(num) {
        try {
            return Number(num).toLocaleString();
        } catch (_) {
            return String(num);
        }
    }

    async function fetchRecommendations() {
        if (!config.recommendationsEndpoint || isFetching) {
            return;
        }

        isFetching = true;
        updateStatus('Refreshing...', 'loading');
        if (refreshButton) {
            refreshButton.disabled = true;
        }
        if (emptyRefreshButton) {
            emptyRefreshButton.disabled = true;
        }

        renderSkeleton(6);

        try {
            const response = await fetch(`${config.recommendationsEndpoint}?num_results=${recommendationLimit}`, {
                cache: 'no-store'
            });

            if (!response.ok) {
                throw new Error(`Status ${response.status}`);
            }

            const payload = await response.json();
            if (!payload.success) {
                throw new Error(payload.error || 'Failed to load recommendations');
            }

            renderRecommendations(payload.recommendations);
            updateStatus('Ready', 'ready');
        } catch (error) {
            updateStatus('Error', 'error');
            toggleEmptyState(true);
            if (typeof showNotification === 'function') {
                showNotification(`Unable to refresh recommendations: ${error.message || error}`, 'error');
            }
        } finally {
            isFetching = false;
            if (refreshButton) {
                refreshButton.disabled = false;
            }
            if (emptyRefreshButton) {
                emptyRefreshButton.disabled = false;
            }
        }
    }

    if (gridToggle) {
        gridToggle.addEventListener('click', () => setView('grid'));
    }

    if (listToggle) {
        listToggle.addEventListener('click', () => setView('list'));
    }

    if (tableToggle) {
        tableToggle.addEventListener('click', () => setView('table'));
    }

    if (refreshButton) {
        refreshButton.addEventListener('click', fetchRecommendations);
    }

    if (emptyRefreshButton) {
        emptyRefreshButton.addEventListener('click', fetchRecommendations);
    }

    function init() {
        applyViewToggle();
        fetchRecommendations();
    }

    if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
