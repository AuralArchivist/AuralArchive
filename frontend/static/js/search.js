(function () {
  const results = document.getElementById('search-results');
  if (!results) {
    return;
  }

  const empty = document.getElementById('search-empty');
  const loading = document.getElementById('search-loading');
  const stats = document.getElementById('search-stats');
  const qInput = document.getElementById('search-query');
  const goBtn = document.getElementById('search-go');
  const vGrid = document.getElementById('search-view-grid');
  const vList = document.getElementById('search-view-list');
  const vTable = document.getElementById('search-view-table');
  const filterSelect = document.getElementById('search-filter');
  const sortSelect = document.getElementById('search-sort');
  const bootstrapEl = document.getElementById('search-bootstrap');

  const PREF = 'aa-search';

  function loadPrefs() {
    try {
      return JSON.parse(localStorage.getItem(PREF)) || {};
    } catch (_) {
      return {};
    }
  }

  function persistPrefs() {
    try {
      localStorage.setItem(PREF, JSON.stringify(prefs));
    } catch (_) {
      /* ignore persistence errors */
    }
  }

  const prefs = loadPrefs();
  if (!prefs.view) {
    prefs.view = 'grid';
    persistPrefs();
  }

  let current = [];
  let renderedResults = [];
  let isLoading = false;
  let searchStartTime = 0;

  function setView(view) {
    const allowedViews = ['grid', 'list', 'table'];
    if (!allowedViews.includes(view)) {
      view = 'grid';
    }
    if (prefs.view === view) {
      return;
    }
    prefs.view = view;
    persistPrefs();
    updateViewToggle();
    renderCurrentView();
  }

  function updateViewToggle() {
    const view = prefs.view || 'grid';
    results.dataset.view = view;
    if (vGrid) {
      vGrid.classList.toggle('is-active', view === 'grid');
    }
    if (vList) {
      vList.classList.toggle('is-active', view === 'list');
    }
    if (vTable) {
      vTable.classList.toggle('is-active', view === 'table');
    }
  }

  function search() {
    const q = (qInput?.value || '').trim();
    if (!q) {
      current = [];
      render();
      showEmpty(true);
      hideStats();
      return;
    }

    if (isLoading) {
      return;
    }

    isLoading = true;
    searchStartTime = Date.now();

    showLoading(true);
    hideEmpty();
    hideStats();

    const url = new URL(window.location.href);
    url.searchParams.set('q', q);
    window.history.replaceState({}, '', url);

    fetch('/search/api/search', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        query: q,
        include_downloads: true
      })
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.success) {
          current = Array.isArray(data.results) ? data.results : [];
          const searchTime = Date.now() - searchStartTime;
          updateStats(current.length, q, searchTime);
        } else {
          current = [];
          showNotification(data.error || 'Search failed', 'error');
        }
        render();
      })
      .catch((error) => {
        current = [];
        showNotification('Search failed. Please try again.', 'error');
        render();
      })
      .finally(() => {
        isLoading = false;
        showLoading(false);
      });
  }

  function render() {
    updateViewToggle();

    if (!current.length) {
      clearResults();
      showEmpty(true, qInput?.value?.trim());
      hideStats();
      renderedResults = [];
      return;
    }

    hideEmpty();

    let filteredResults = applyFilters(current);
    filteredResults = applySorting(filteredResults);

    renderedResults = filteredResults;

    if (!filteredResults.length) {
      clearResults();
      showEmpty(true, qInput?.value?.trim());
      updateStats(0, qInput?.value?.trim(), null, current.length);
      return;
    }

    renderCurrentView();
    updateStats(filteredResults.length, qInput?.value?.trim(), null, current.length);
  }

  function clearResults() {
    results.innerHTML = '';
  }

  function renderCurrentView() {
    clearResults();

    if (!renderedResults.length) {
      return;
    }

    const view = prefs.view || 'grid';
    results.dataset.view = view;

    if (view === 'grid') {
      renderGridView(renderedResults);
    } else if (view === 'list') {
      renderListView(renderedResults);
    } else {
      renderTableView(renderedResults);
    }
  }

  function renderGridView(items) {
    items.forEach((item) => {
      const el = document.createElement('article');
      el.className = 'search-card';
      el.tabIndex = 0;
      const libraryStatus = getLibraryStatus(item);
      item.library_status = libraryStatus;
      item.in_library = libraryStatus === 'in_library';
      if (libraryStatus === 'in_library') {
        el.classList.add('is-in-library');
      } else if (libraryStatus === 'wanted') {
        el.classList.add('is-wanted');
      }
      el.innerHTML = createGridMarkup(item);
      el.dataset.asin = resolveAsin(item);
      el.dataset.inLibrary = item.in_library ? 'true' : 'false';
      el.dataset.libraryStatus = libraryStatus;
      el.dataset.downloadable = item.download_available ? 'true' : 'false';
      results.appendChild(el);
      attachGridInteractions(el, item);
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
    results.appendChild(wrapper);
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
    results.appendChild(wrapper);
    attachStructuredInteractions(wrapper);
  }

  function attachGridInteractions(card, item) {
    const asin = resolveAsin(item);
    card.addEventListener('click', (event) => {
      if (event.target.closest('button, a')) {
        return;
      }
      viewDetails(asin);
    });

    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.target.closest('button, a')) {
        event.preventDefault();
        viewDetails(asin);
      }
    });

    card.querySelectorAll('[data-action="add-to-library"]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        addToLibrary(asin, button);
      });
    });

    card.querySelectorAll('[data-action="view-details"]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        viewDetails(asin);
      });
    });

    card.querySelectorAll('[data-action="find-similar"]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        const author = button.dataset.author;
        filterByAuthor(author || item.author || '');
      });
    });

  }

  function attachStructuredInteractions(root) {
    if (!root) {
      return;
    }

    root.querySelectorAll('.search-row').forEach((row) => {
      const asin = row.dataset.asin;
      if (!asin) {
        return;
      }
      row.tabIndex = 0;
      row.addEventListener('click', () => viewDetails(asin));
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          viewDetails(asin);
        }
      });
    });

    root.querySelectorAll('[data-action="open-modal"]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        const asin = button.dataset.asin;
        viewDetails(asin);
      });
    });

    root.querySelectorAll('[data-action="row-add-to-library"]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        addToLibrary(button.dataset.asin, button);
      });
    });

  }

  function createCompactRow(item) {
    const asin = escapeHtml(resolveAsin(item));
    const cover = buildCoverCell(item, 8);
    const title = escapeHtml(item.title || 'Unknown Title');
    const releaseYear = resolveReleaseYear(item);
    const author = escapeHtml(item.author || 'Unknown Author');
    const series = buildSeriesCell(item, true);
    const narrator = buildNarratorCell(item, true);
    const runtime = buildRuntimeCell(item, true);
    const rating = buildRatingCell(item, true);
    const status = buildStatusBadge(item, true);
    const actionButton = buildActionControl(item, 'list');

    return `
      <tr class="search-row hover cursor-pointer" data-asin="${asin}">
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
    const title = escapeHtml(item.title || 'Unknown Title');
    const author = escapeHtml(item.author || 'Unknown Author');
    const series = buildSeriesCell(item, false);
    const narrator = buildNarratorCell(item, false);
    const runtime = buildRuntimeCell(item, false);
    const rating = buildRatingCell(item, false);
    const status = buildStatusBadge(item, false);
    const actionButton = buildActionControl(item, 'table');

    return `
      <tr class="search-row hover cursor-pointer" data-asin="${asin}">
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
    const title = escapeHtml(item.title || 'Unknown Title');
    const cover = item.cover_image;
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
    const series = item.series;
    const sequence = item.sequence;
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
    const narrator = getPrimaryNarrator(item.narrator);
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
    const ratingValue = parseFloat(item.rating);
    if (Number.isNaN(ratingValue) || ratingValue <= 0) {
      return `<span class="opacity-40 ${compact ? 'text-xs' : ''}">-</span>`;
    }
    const count = Number(item.num_ratings || item.rating_count || 0);
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
    }

    return `
      <span class="${pillClass} ${compact ? 'text-[10px]' : ''}"><i class="fas fa-${icon}"></i><span>${label}</span></span>
    `;
  }

  function buildActionControl(item, context = 'table') {
    const asin = escapeHtml(resolveAsin(item));
    const status = getLibraryStatus(item);

    if (status === 'not_in_library') {
      const label = context === 'table' ? ' Add' : '';
      return `<button class="btn btn-soft btn-primary btn-xs" data-action="row-add-to-library" data-asin="${asin}"><i class="fas fa-plus"></i>${label}</button>`;
    }

    return '';
  }

  function resolveRuntime(item) {
    return item.runtime || '';
  }

  function resolveReleaseYear(item) {
    const raw = item.release_date;
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

    const rawStatus = item.library_status || item.status || (item.in_library ? 'in_library' : 'not_in_library');
    const normalized = String(rawStatus || '').toLowerCase();

    if (normalized === 'in_library' || normalized === 'owned' || normalized === 'audible_library') {
      return 'in_library';
    }
    if (normalized === 'wanted') {
      return 'wanted';
    }

    return 'not_in_library';
  }

  function formatRatingsCount(count) {
    if (count >= 1000) {
      return `${(count / 1000).toFixed(1)}k`;
    }
    return formatNumber(count);
  }

  function resolveAsin(item) {
    if (!item) {
      return '';
    }
    return item.asin || item.ASIN || item.id || '';
  }

  function createGridMarkup(item) {
    const asin = escapeHtml(resolveAsin(item));
    const status = getLibraryStatus(item);
    const author = escapeHtml(item.author || 'Unknown Author');
    const rawTitle = item.title || 'Unknown Title';
    const title = escapeHtml(rawTitle);
    const cover = buildDiscoverCover(item, title);

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

    const actionButton = status === 'not_in_library' && asin
      ? `<button class="discover-btn discover-btn--primary" type="button" data-action="add-to-library" data-asin="${asin}">+ Add to Library</button>`
      : `<span class="${statusClass}">${statusLabels[status] || 'In Library'}</span>`;

    const ratingValue = parseFloat(item.rating);
    const ratingCount = Number(item.num_ratings || item.rating_count || 0);
     const ratingMarkup = !Number.isNaN(ratingValue) && ratingValue > 0
      ? `<div class="discover-rating">
          <span class="discover-rating__star">★</span>
          <span class="discover-rating__value">${ratingValue.toFixed(1)}</span>
          ${ratingCount > 0 ? `<span class="discover-rating__count">(${formatRatingsCount(ratingCount)})</span>` : ''}
        </div>`
      : '';

    const seriesLabel = buildSeriesLabel(item);
    const seriesMeta = seriesLabel
      ? `<div class="discover-meta-item"><i class="fas fa-layer-group"></i><span>${escapeHtml(seriesLabel)}</span></div>`
      : '';

    const narrator = getPrimaryNarrator(item.narrator);
    const narratorMeta = narrator
      ? `<div class="discover-meta-item"><i class="fas fa-microphone"></i><span>${escapeHtml(narrator)}</span></div>`
      : '';

    const runtime = resolveRuntime(item);
    const runtimeMeta = runtime
      ? `<div class="discover-meta-item"><i class="fas fa-clock"></i><span>${escapeHtml(runtime)}</span></div>`
      : '';

    return `
      <div class="discover-card">
        <div class="discover-media">
          <div class="discover-cover">
            ${cover}
          </div>
        </div>
        <div class="discover-body">
          <div class="discover-title">${title}</div>
          <div class="discover-author">${author}</div>
          <div class="discover-meta-list">
            ${seriesMeta}
            ${narratorMeta}
            ${runtimeMeta}
          </div>
          <div class="discover-rating-row">${ratingMarkup}</div>
        </div>
        <div class="discover-actions">
          <button class="discover-btn" type="button" data-action="view-details" data-asin="${asin}">Details</button>
          <button class="discover-btn discover-btn--ghost" type="button" data-action="find-similar" data-author="${author}">Similar</button>
          ${actionButton}
        </div>
      </div>
    `;
  }

  function buildDiscoverCover(item, title) {
    if (item.cover_image) {
      return `<img src="${escapeHtml(item.cover_image)}" alt="${title}" loading="lazy" class="discover-cover__img">`;
    }
    return '<div class="discover-cover__img" style="display:flex;align-items:center;justify-content:center;background:var(--b1);color:var(--bc, #cbd5e1);font-size:0.75rem;">No Cover</div>';
  }

  function buildSeriesLabel(item) {
    const series = item.series;
    if (!series || series === 'N/A') {
      return '';
    }
    const sequence = item.sequence;
    if (sequence && sequence !== 'N/A') {
      return `${series} · Book ${sequence}`;
    }
    return series;
  }

  function buildClassicLibraryButton(status, asin) {
    if (status === 'in_library') {
      return '<button class="search-action-btn in-library-btn" disabled><i class="fas fa-check"></i> In Library</button>';
    }
    if (status === 'wanted') {
      return '<button class="search-action-btn in-library-btn" disabled><i class="fas fa-star"></i> Wanted</button>';
    }
    return `<button class="search-action-btn add-btn" data-action="add-to-library" data-asin="${asin}"><i class="fas fa-plus"></i> Add to Library</button>`;
  }

  function buildClassicCover(item, title) {
    if (item.cover_image) {
      return `<img src="${escapeHtml(item.cover_image)}" alt="${title}" loading="lazy">`;
    }
    return '<div class="search-book-placeholder"><i class="fas fa-book"></i><span>AUDIO<br>BOOK</span></div>';
  }

  function buildDownloadBadge(item) {
    if (!item.download_available) {
      return '';
    }
    return '<span class="search-download-badge"><i class="fas fa-download"></i> Download Ready</span>';
  }

  function createOverlayBadges(item) {
    if (!item.download_available) {
      return '';
    }
    return '<div class="search-card-overlay"><span class="media-chip media-chip--accent"><i class="fas fa-download"></i> Download Ready</span></div>';
  }

  function createMetaPills(item) {
    const pills = [];
    const status = getLibraryStatus(item);
    if (status === 'in_library') {
      pills.push(metaPill('check-circle', 'In Library', 'meta-pill--status meta-pill--status-success'));
    } else if (status === 'wanted') {
      pills.push(metaPill('star', 'Wanted', 'meta-pill--status meta-pill--status-warning'));
    } else {
      pills.push(metaPill('bookmark', 'Not in Library', 'meta-pill--status meta-pill--status-info'));
    }

    const ratingPill = createRatingPill(item);
    if (ratingPill) {
      pills.push(ratingPill);
    }

    if (item.series && item.series !== 'N/A') {
      let label = item.series;
      if (item.sequence) {
        label += ` · Book ${item.sequence}`;
      }
      pills.push(metaPill('layer-group', label));
    }
    if (item.runtime) {
      pills.push(metaPill('clock', item.runtime));
    }
    const narrator = getPrimaryNarrator(item.narrator);
    if (narrator) {
      pills.push(metaPill('microphone', narrator));
    }
    if (item.release_date) {
      pills.push(metaPill('calendar-alt', item.release_date));
    }
    if (item.language) {
      pills.push(metaPill('language', item.language));
    }
    if (item.search_source) {
      pills.push(metaPill('signal', formatSource(item.search_source), 'meta-pill--neutral'));
    }
    if (item.download_available) {
      pills.push(metaPill('download', 'Downloadable', 'meta-pill--accent'));
    }
    return pills.join('');
  }

  function createRatingPill(item) {
    const rating = parseFloat(item.rating);
    if (!rating || Number.isNaN(rating)) {
      return '';
    }

    const ratingsCount = Number(item.num_ratings || item.rating_count || 0);
    const countLabel = ratingsCount > 0 ? `<span class="meta-pill-sub">${formatNumber(ratingsCount)} ratings</span>` : '';
    return `<span class="meta-pill meta-pill--rating"><i class="fas fa-star"></i><span>${rating.toFixed(1)}</span>${countLabel}</span>`;
  }

  function metaPill(icon, text, extraClass = '') {
    const safeText = escapeHtml(text);
    const classes = extraClass ? `meta-pill ${extraClass}` : 'meta-pill';
    return `<span class="${classes}"><i class="fas fa-${icon}"></i><span>${safeText}</span></span>`;
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

  function createSummary(summary, modern = false) {
    const compact = collapseSummary(summary);
    if (!compact) {
      return '';
    }
    const trimmed = truncateText(compact, 220);
    const cls = modern ? 'search-card-summary-modern' : 'search-card-summary';
    return `<div class="${cls}">${escapeHtml(trimmed)}</div>`;
  }

  function collapseSummary(summary) {
    const raw = extractSummaryText(summary);
    if (!raw) {
      return '';
    }
    return raw.replace(/\s+/g, ' ').trim();
  }

  function formatSource(source) {
    if (!source) {
      return '';
    }
    const normalized = String(source).replace(/[_-]+/g, ' ');
    return normalized.replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  function applyFilters(items) {
    const filter = filterSelect?.value;
    if (!filter) {
      return items;
    }

    return items.filter((item) => {
      const status = getLibraryStatus(item);
      switch (filter) {
        case 'in_library':
          return status === 'in_library';
        case 'wanted':
          return status === 'wanted';
        case 'not_in_library':
          return status === 'not_in_library';
        case 'downloadable':
          return !!item.download_available;
        default:
          return true;
      }
    });
  }

  function applySorting(items) {
    const sort = sortSelect?.value || 'relevance';
    if (sort === 'relevance') {
      return items;
    }

    return [...items].sort((a, b) => {
      switch (sort) {
        case 'title':
          return (a.title || '').localeCompare(b.title || '');
        case 'author':
          return (a.author || '').localeCompare(b.author || '');
        case 'rating':
          return (parseFloat(b.rating) || 0) - (parseFloat(a.rating) || 0);
        case 'release_date':
          return new Date(b.release_date || 0) - new Date(a.release_date || 0);
        default:
          return 0;
      }
    });
  }

  function updateStats(count, query, searchTime, totalCount) {
    if (!stats) {
      return;
    }

    const countEl = stats.querySelector('.results-count');
    const timeEl = stats.querySelector('.search-time');

    if (countEl) {
      let text = `${count} result${count !== 1 ? 's' : ''}`;
      if (typeof totalCount === 'number' && totalCount !== count) {
        text += ` (${totalCount} total)`;
      }
      if (query) {
        text += ` for "${query}"`;
      }
      countEl.textContent = text;
    }

    if (timeEl && searchTime !== null && searchTime !== undefined) {
      timeEl.textContent = `Search completed in ${(searchTime / 1000).toFixed(2)}s`;
    } else if (timeEl) {
      timeEl.textContent = '';
    }

    stats.style.display = count > 0 ? 'flex' : 'none';
  }

  function hideStats() {
    if (stats) {
      stats.style.display = 'none';
    }
  }

  function showEmpty(show, query = '') {
    if (!empty) {
      return;
    }

    if (show) {
      empty.style.display = 'flex';
      const emptyState = empty.querySelector('.empty-state');
      const welcomeState = empty.querySelector('.search-welcome');

      if (query && emptyState) {
        emptyState.style.display = 'block';
        if (welcomeState) {
          welcomeState.style.display = 'none';
        }
      } else if (welcomeState) {
        welcomeState.style.display = 'block';
        if (emptyState) {
          emptyState.style.display = 'none';
        }
      }
    } else {
      empty.style.display = 'none';
    }
  }

  function hideEmpty() {
    if (empty) {
      empty.style.display = 'none';
    }
  }

  function showLoading(show) {
    if (!loading) {
      return;
    }
    loading.style.display = show ? 'flex' : 'none';
  }

  function escapeHtml(str) {
    if (!str) {
      return '';
    }
    return String(str).replace(/[&<>"']/g, (s) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    })[s]);
  }

  function formatNumber(value) {
    try {
      return new Intl.NumberFormat().format(value);
    } catch (_) {
      return value;
    }
  }

  function extractSummaryText(value) {
    if (!value) {
      return '';
    }

    const temp = document.createElement('div');
    temp.innerHTML = value;
    const text = (temp.textContent || temp.innerText || '').replace(/\u00a0/g, ' ');
    return text
      .replace(/\r\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function truncateText(value, maxLength = 220) {
    if (!value) {
      return '';
    }
    const stringValue = String(value).trim();
    if (stringValue.length <= maxLength) {
      return stringValue;
    }
    const truncated = stringValue.slice(0, maxLength);
    const lastSpace = truncated.lastIndexOf(' ');
    return (lastSpace > maxLength * 0.6 ? truncated.slice(0, lastSpace) : truncated).concat('…');
  }

  function showNotification(message, type = 'info') {
    if (typeof window.showNotification === 'function') {
      window.showNotification(message, type);
      return;
    }
  }

  function filterByAuthor(author) {
    const target = (author || '').trim();
    if (!target) {
      showNotification('No author info available for this title', 'info');
      return;
    }
    if (qInput) {
      qInput.value = target;
    }
    showNotification(`Searching for more by ${target}`, 'info');
    search();
  }

  function findResultByAsin(asin) {
    if (!asin) {
      return null;
    }
    return renderedResults.find((item) => resolveAsin(item) === asin)
      || current.find((item) => resolveAsin(item) === asin)
      || null;
  }

  const libraryChoices = (() => {
    try {
      return JSON.parse(document.getElementById('search-libraries')?.textContent || '[]');
    } catch (error) {
      return [];
    }
  })();

  // Library id, '' for the routing rules, or null when cancelled
  function pickLibrary() {
    if (libraryChoices.length < 2) {
      return Promise.resolve('');
    }
    return new Promise((resolve) => {
      const dialog = document.createElement('dialog');
      dialog.className = 'modal';
      dialog.innerHTML = `<div class="modal-box max-w-sm">
          <h3 class="font-semibold text-base mb-1">Add to which library?</h3>
          <p class="text-xs text-base-content/60 mb-3">You can change this later in the book's details.</p>
          <select class="select select-bordered select-sm w-full" data-role="library">
            <option value="">Automatic (routing rules)</option>
            ${libraryChoices.map((lib) => `<option value="${lib.id}">${escapeHtml(lib.name)}</option>`).join('')}
          </select>
          <div class="modal-action">
            <button type="button" class="btn btn-ghost btn-sm" data-role="cancel">Cancel</button>
            <button type="button" class="btn btn-primary btn-sm" data-role="add">Add</button>
          </div>
        </div>
        <form method="dialog" class="modal-backdrop"><button>close</button></form>`;
      let choice = null;
      dialog.querySelector('[data-role="add"]').addEventListener('click', () => {
        choice = dialog.querySelector('[data-role="library"]').value;
        dialog.close();
      });
      dialog.querySelector('[data-role="cancel"]').addEventListener('click', () => dialog.close());
      dialog.addEventListener('close', () => {
        dialog.remove();
        resolve(choice);
      });
      document.body.appendChild(dialog);
      dialog.showModal();
    });
  }

  window.addToLibrary = async function (asin, button) {
    if (!asin || !button) {
      return;
    }

    const libraryId = await pickLibrary();
    if (libraryId === null) {
      return;
    }

    const originalHtml = button.innerHTML;
    button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Adding...';
    button.disabled = true;

    const book = findResultByAsin(asin);
    if (!book) {
      showNotification('Book data not found', 'error');
      button.innerHTML = originalHtml;
      button.disabled = false;
      return;
    }

    fetch('/search/add-book', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ ...book, library_id: libraryId ? Number(libraryId) : null })
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.success) {
          showNotification(data.message || 'Book added to library', 'success');
          button.innerHTML = 'Added to Library';
          button.className = 'btn btn-sm btn-outline';
          book.in_library = true;
          book.library_status = 'in_library';
          render();
        } else {
          showNotification(data.error || 'Failed to add book', 'error');
          button.innerHTML = originalHtml;
          button.disabled = false;
        }
      })
      .catch((error) => {
        showNotification('Failed to add book', 'error');
        button.innerHTML = originalHtml;
        button.disabled = false;
      });
  };

  window.viewDetails = function (asin) {
    
    if (!asin) {
      return;
    }

    const book = findResultByAsin(asin);
    
    if (!book) {
      return;
    }

    if (typeof window.openBookDetails === 'function') {
      window.openBookDetails(book.id || asin);
      return;
    }

    showBookDetails(book);
  };

  function showBookDetails(book) {
    
    try {
      const modal = document.createElement('div');
      modal.className = 'modal modal-open';
      

      const cover = AA.bookCoverImg(book.cover_image, book.title, 'book-modal__cover');

      const metaSection = createMetaPills(book);
      const summaryText = extractSummaryText(book.summary || '');
      const summaryHtml = summaryText
        ? summaryText.split(/\n{2,}/).map((paragraph) => `<p>${escapeHtml(paragraph.trim())}</p>`).join('')
        : '';

      const status = getLibraryStatus(book);
      const addButton = status === 'in_library'
        ? '<button class="btn btn-sm btn-outline" disabled>In Library</button>'
        : status === 'wanted'
          ? '<button class="btn btn-sm btn-outline" disabled>Wanted</button>'
          : `<button class="btn btn-sm btn-primary" onclick="addToLibrary('${escapeHtml(book.asin)}', this)"><i class="fas fa-plus"></i> Add to Library</button>`;

      modal.innerHTML = `
  <div class="modal-box max-w-5xl p-0 book-modal-shell">
        <button class="btn btn-sm btn-ghost book-modal-close" aria-label="Close" onclick="this.closest('.modal').remove()">
          <i class="fas fa-times"></i>
        </button>
        <div class="book-modal">
          <div class="book-modal__media">
            ${cover}
            ${book.download_available ? '<span class="book-modal__badge">Download Ready</span>' : ''}
          </div>
          <div class="book-modal__details">
            ${book.series && book.series !== 'N/A' ? `<span class="book-modal__series">${escapeHtml(book.series)}${book.sequence ? ` · Book ${escapeHtml(book.sequence)}` : ''}</span>` : ''}
            <h2 class="book-modal__title">${escapeHtml(book.title)}</h2>
            <p class="book-modal__author">By ${escapeHtml(book.author || 'Unknown Author')}</p>
            ${metaSection ? `<div class="book-modal__meta">${metaSection}</div>` : ''}
            ${summaryHtml ? `<div class="book-modal__summary">${summaryHtml}</div>` : ''}
            <div class="book-modal__actions">
              ${addButton}
              <button class="btn btn-sm btn-outline" onclick="this.closest('.modal').remove()">Close</button>
            </div>
          </div>
        </div>
      </div>
    `;
      
      document.body.appendChild(modal);

      modal.addEventListener('click', (event) => {
        if (event.target === modal) {
          modal.remove();
        }
      });
    } catch (error) {
      showNotification('Error displaying book details: ' + error.message, 'error');
    }
  }

  goBtn?.addEventListener('click', search);
  qInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      search();
    }
  });

  filterSelect?.addEventListener('change', render);
  sortSelect?.addEventListener('change', render);

  vGrid?.addEventListener('click', () => setView('grid'));
  vList?.addEventListener('click', () => setView('list'));
  vTable?.addEventListener('click', () => setView('table'));

  let bootstrapResults = [];
  if (bootstrapEl && bootstrapEl.textContent.trim()) {
    try {
      const parsed = JSON.parse(bootstrapEl.textContent);
      if (Array.isArray(parsed)) {
        bootstrapResults = parsed;
      }
    } catch (error) {
    }
  }

  const urlParams = new URLSearchParams(window.location.search);
  const query = urlParams.get('q');
  if (qInput && query) {
    qInput.value = query;
  }

  if (bootstrapResults.length) {
    current = bootstrapResults;
    renderedResults = bootstrapResults;
    render();
  } else if (query) {
    search();
  } else {
    showEmpty(true);
    updateViewToggle();
  }

  window.__aaSearch = search;
})();
