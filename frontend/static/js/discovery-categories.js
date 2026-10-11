(function () {
    const tabs = document.querySelectorAll('.discovery-tab');
    const panels = {
        recommended: document.getElementById('discovery-tabpanel-recommended'),
        genres: document.getElementById('discovery-tabpanel-genres'),
        'new-releases': document.getElementById('discovery-tabpanel-new-releases'),
        popular: document.getElementById('discovery-tabpanel-popular')
    };

    const treeContainer = document.getElementById('discovery-genre-tree');
    const genreSearchInput = document.getElementById('discovery-genre-search');
    const genreLabel = document.getElementById('discovery-selected-genre-label');
    const sortSelect = document.getElementById('discovery-sort-select');
    const genreBooksContainer = document.getElementById('discovery-genre-books');
    const newReleasesContainer = document.getElementById('discovery-new-releases-books');
    const popularContainer = document.getElementById('discovery-popular-books');

    if (!tabs.length) {
        return;
    }

    const defaultCover = '/static/images/auralarchive_logo.png';

    let categoryTree = null;
    let favorites = new Set();
    let selectedCategoryId = null;
    let loadedTabs = new Set();

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    // --- Tabs ---

    function activateTab(tabName) {
        tabs.forEach((tab) => {
            const isActive = tab.dataset.tab === tabName;
            tab.classList.toggle('is-active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
        Object.entries(panels).forEach(([name, panel]) => {
            if (!panel) {
                return;
            }
            panel.classList.toggle('hidden', name !== tabName);
            panel.classList.toggle('is-active', name === tabName);
        });

        if (tabName === 'genres' && !loadedTabs.has('genres')) {
            loadedTabs.add('genres');
            loadCategoryTree();
            loadFavorites();
        } else if (tabName === 'new-releases' && !loadedTabs.has('new-releases')) {
            loadedTabs.add('new-releases');
            loadFavoritesBookFeed(newReleasesContainer, '-ReleaseDate');
        } else if (tabName === 'popular' && !loadedTabs.has('popular')) {
            loadedTabs.add('popular');
            loadFavoritesBookFeed(popularContainer, 'BestSellers');
        }
    }

    tabs.forEach((tab) => {
        tab.addEventListener('click', () => {
            activateTab(tab.dataset.tab);
            history.replaceState(null, '', tab.dataset.tab === 'recommended' ? window.location.pathname : `#${tab.dataset.tab}`);
        });
    });

    // Links like /discover#genres open that tab
    const initialTab = window.location.hash.replace('#', '');
    // The calendar moved to its own page; older notifications still link here
    const forwardCalendar = () => {
        if (window.location.hash === '#calendar') window.location.replace('/calendar');
    };
    window.addEventListener('hashchange', forwardCalendar);
    if (initialTab === 'calendar') {
        forwardCalendar();
        return;
    }
    if (initialTab && panels[initialTab]) {
        activateTab(initialTab);
    }

    // --- Category tree ---

    async function loadCategoryTree() {
        treeContainer.innerHTML = '<div class="discovery-tree-loading"><i class="fas fa-spinner fa-spin"></i> Loading categories...</div>';
        try {
            const response = await fetch('/api/discovery/categories');
            const payload = await response.json();
            if (!payload.success) {
                throw new Error(payload.error || 'Failed to load categories');
            }
            categoryTree = payload.categories || [];
            if (!categoryTree.length) {
                treeContainer.innerHTML = '<div class="discovery-tree-empty">No categories synced yet. Trigger a sync from Settings &gt; Discovery.</div>';
                return;
            }
            renderTree(categoryTree);
        } catch (error) {
            treeContainer.innerHTML = `<div class="discovery-tree-empty">Unable to load categories: ${escapeHtml(error.message || error)}</div>`;
        }
    }

    async function loadFavorites() {
        try {
            const response = await fetch('/api/discovery/preferences');
            const payload = await response.json();
            if (payload.success) {
                favorites = new Set((payload.favorites || []).filter((f) => f.enabled).map((f) => f.id));
                syncFavoriteStars();
            }
        } catch (_) {
            /* favorites are a nice-to-have; ignore failures */
        }
    }

    function syncFavoriteStars() {
        treeContainer.querySelectorAll('.discovery-tree-favorite').forEach((star) => {
            const id = star.dataset.categoryId;
            star.classList.toggle('is-favorite', favorites.has(id));
        });
    }

    function renderTree(nodes) {
        treeContainer.innerHTML = buildTreeMarkup(nodes, 0);
        attachTreeInteractions();
        syncFavoriteStars();
    }

    function buildTreeMarkup(nodes, depth) {
        return `<ul class="discovery-tree-list" data-depth="${depth}">${nodes.map((node) => buildNodeMarkup(node, depth)).join('')}</ul>`;
    }

    function buildNodeMarkup(node, depth) {
        const hasChildren = Array.isArray(node.children) && node.children.length > 0;
        const safeId = escapeHtml(node.id);
        const safeName = escapeHtml(node.name);
        return `
            <li class="discovery-tree-node" data-category-id="${safeId}">
                <div class="discovery-tree-row">
                    <button type="button" class="discovery-tree-toggle ${hasChildren ? '' : 'is-leaf'}" data-action="toggle" aria-label="Expand ${safeName}">
                        ${hasChildren ? '<i class="fas fa-chevron-right"></i>' : ''}
                    </button>
                    <button type="button" class="discovery-tree-label" data-action="select" data-category-id="${safeId}" data-category-name="${safeName}">
                        ${safeName}
                    </button>
                    <button type="button" class="discovery-tree-favorite" data-action="favorite" data-category-id="${safeId}" aria-label="Favorite ${safeName}">
                        <i class="fas fa-star"></i>
                    </button>
                </div>
                ${hasChildren ? `<div class="discovery-tree-children hidden">${buildTreeMarkup(node.children, depth + 1)}</div>` : ''}
            </li>
        `;
    }

    function attachTreeInteractions() {
        treeContainer.querySelectorAll('[data-action="toggle"]').forEach((button) => {
            if (button.classList.contains('is-leaf')) {
                return;
            }
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                const li = button.closest('.discovery-tree-node');
                const children = li.querySelector(':scope > .discovery-tree-children');
                if (!children) {
                    return;
                }
                const collapsed = children.classList.toggle('hidden');
                button.classList.toggle('is-expanded', !collapsed);
            });
        });

        treeContainer.querySelectorAll('[data-action="select"]').forEach((button) => {
            button.addEventListener('click', () => {
                selectCategory(button.dataset.categoryId, button.dataset.categoryName);
            });
        });

        treeContainer.querySelectorAll('[data-action="favorite"]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                toggleFavorite(button.dataset.categoryId, button);
            });
        });
    }

    async function toggleFavorite(categoryId, button) {
        const enable = !favorites.has(categoryId);
        try {
            const response = await fetch('/api/discovery/preferences', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ audible_category_id: categoryId, enabled: enable })
            });
            const payload = await response.json();
            if (!payload.success) {
                throw new Error('Failed to update favorite');
            }
            if (enable) {
                favorites.add(categoryId);
            } else {
                favorites.delete(categoryId);
            }
            button.classList.toggle('is-favorite', enable);
            loadedTabs.delete('new-releases');
            loadedTabs.delete('popular');
        } catch (error) {
            if (typeof showNotification === 'function') {
                showNotification(`Unable to update favorite: ${error.message || error}`, 'error');
            }
        }
    }

    async function selectCategory(categoryId, categoryName) {
        selectedCategoryId = categoryId;
        genreLabel.textContent = categoryName;
        await loadGenreBooks();
    }

    async function loadGenreBooks() {
        if (!selectedCategoryId) {
            return;
        }
        renderSkeleton(genreBooksContainer, 6);
        try {
            const sort = sortSelect ? sortSelect.value : '-ReleaseDate';
            const response = await fetch(`/api/discovery/categories/${encodeURIComponent(selectedCategoryId)}/books?sort=${encodeURIComponent(sort)}&num_results=50`);
            const payload = await response.json();
            if (!payload.success) {
                throw new Error(payload.error || 'Failed to load books');
            }
            renderBookGrid(genreBooksContainer, payload.books || []);
        } catch (error) {
            genreBooksContainer.innerHTML = `<div class="discovery-tree-empty">Unable to load books: ${escapeHtml(error.message || error)}</div>`;
        }
    }

    if (sortSelect) {
        sortSelect.addEventListener('change', loadGenreBooks);
    }

    if (genreSearchInput) {
        let searchTimer = null;
        genreSearchInput.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => runCategorySearch(genreSearchInput.value), 250);
        });
    }

    async function runCategorySearch(query) {
        const term = (query || '').trim();
        if (!term) {
            if (categoryTree) {
                renderTree(categoryTree);
            }
            return;
        }
        try {
            const response = await fetch(`/api/discovery/categories/search?q=${encodeURIComponent(term)}`);
            const payload = await response.json();
            if (!payload.success) {
                throw new Error('Search failed');
            }
            renderSearchResults(payload.results || []);
        } catch (_) {
            treeContainer.innerHTML = '<div class="discovery-tree-empty">Search failed. Try again.</div>';
        }
    }

    function renderSearchResults(results) {
        if (!results.length) {
            treeContainer.innerHTML = '<div class="discovery-tree-empty">No matching categories.</div>';
            return;
        }
        treeContainer.innerHTML = `<ul class="discovery-tree-list discovery-tree-list--flat">${results.map((r) => `
            <li class="discovery-tree-node">
                <div class="discovery-tree-row">
                    <button type="button" class="discovery-tree-toggle is-leaf"></button>
                    <button type="button" class="discovery-tree-label" data-action="select" data-category-id="${escapeHtml(r.id)}" data-category-name="${escapeHtml(r.name)}">
                        ${escapeHtml(r.name)}
                        <span class="discovery-tree-path">${escapeHtml(r.path)}</span>
                    </button>
                    <button type="button" class="discovery-tree-favorite" data-action="favorite" data-category-id="${escapeHtml(r.id)}" aria-label="Favorite ${escapeHtml(r.name)}">
                        <i class="fas fa-star"></i>
                    </button>
                </div>
            </li>
        `).join('')}</ul>`;
        attachTreeInteractions();
        syncFavoriteStars();
    }

    // --- New Releases / Popular Books feeds ---

    async function loadFavoritesBookFeed(container, sortBy) {
        renderSkeleton(container, 6);
        try {
            const response = await fetch('/api/discovery/preferences?enabled_only=true');
            const payload = await response.json();
            const favoriteCategories = (payload.favorites || []).slice(0, 5);

            if (!favoriteCategories.length) {
                container.innerHTML = '<div class="discovery-tree-empty">Favorite some genres in Browse Genres to populate this feed.</div>';
                return;
            }

            const results = await Promise.all(favoriteCategories.map((fav) =>
                fetch(`/api/discovery/categories/${encodeURIComponent(fav.id)}/books?sort=${encodeURIComponent(sortBy)}&num_results=10`)
                    .then((r) => r.json())
                    .then((p) => (p.success ? p.books : []))
                    .catch(() => [])
            ));

            const merged = sortBy === 'BestSellers' ? rankAcrossCategories(results) : dedupeBooks(results.flat());

            if (sortBy === '-ReleaseDate') {
                merged.sort((a, b) => String(b.release_date || '').localeCompare(String(a.release_date || '')));
            }

            renderBookGrid(container, merged.slice(0, 30));
        } catch (error) {
            container.innerHTML = `<div class="discovery-tree-empty">Unable to load books: ${escapeHtml(error.message || error)}</div>`;
        }
    }

    function dedupeBooks(books) {
        const seen = new Set();
        return books.filter((book) => {
            const asin = book.asin || book.ASIN;
            if (!asin) return true;
            if (seen.has(asin)) return false;
            seen.add(asin);
            return true;
        });
    }

    function rankAcrossCategories(lists) {
        const entries = new Map();
        lists.forEach((books) => {
            books.forEach((book, index) => {
                const key = book.asin || book.ASIN || `${book.title || book.Title}|${book.author || book.Author}`;
                const position = books.length > 1 ? 1 - index / (books.length - 1) : 1;
                const entry = entries.get(key);
                if (entry) {
                    entry.position = Math.max(entry.position, position);
                    entry.appearances += 1;
                } else {
                    entries.set(key, { book, position, appearances: 1 });
                }
            });
        });
        const all = [...entries.values()];
        const ratingCount = (b) => Number(b.num_ratings || b.Num_Ratings || 0) || 0;
        const maxLogRatings = Math.max(1, ...all.map((e) => Math.log10(1 + ratingCount(e.book))));
        all.forEach((e) => {
            const stars = Math.min(5, Number(e.book.rating || e.book['Overall Rating'] || 0) || 0);
            e.score = 0.5 * e.position
                + 0.3 * (Math.log10(1 + ratingCount(e.book)) / maxLogRatings)
                + 0.2 * (stars / 5)
                + 0.05 * (e.appearances - 1);
        });
        return all.sort((a, b) => b.score - a.score).map((e) => e.book);
    }

    // --- Shared book grid rendering ---

    function renderSkeleton(container, count) {
        container.innerHTML = Array.from({ length: count || 6 }).map(() => `
            <div class="discover-card discover-card--skeleton animate-pulse">
                <div class="discover-media bg-base-200"></div>
                <div class="discover-body">
                    <div class="h-4 bg-base-content/10 rounded mb-2"></div>
                    <div class="h-3 bg-base-content/10 rounded w-2/3"></div>
                </div>
            </div>
        `).join('');
    }

    function renderBookGrid(container, books) {
        if (!books.length) {
            container.innerHTML = '<div class="discovery-tree-empty">No books found.</div>';
            return;
        }
        container.innerHTML = books.map((book) => buildBookCardMarkup(book)).join('');
        container.querySelectorAll('[data-action="add-to-library"]').forEach((button) => {
            button.addEventListener('click', () => addBookToLibrary(button));
        });
    }

    function formatRatingsCount(count) {
        if (count >= 1000) {
            return `${(count / 1000).toFixed(1)}k`;
        }
        try {
            return Number(count).toLocaleString();
        } catch (_) {
            return String(count);
        }
    }

    function getLibraryStatus(book) {
        const raw = String(book.library_status || book.status || book.Status || '').toLowerCase();
        if (['in_library', 'owned', 'audible_library', 'completed'].includes(raw)) {
            return 'in_library';
        }
        if (raw === 'wanted') {
            return 'wanted';
        }
        if (raw === 'downloading') {
            return 'downloading';
        }
        return 'not_in_library';
    }

    function buildBookCardMarkup(book) {
        const title = escapeHtml(book.title || book.Title || 'Unknown Title');
        const author = escapeHtml(book.author || book.Author || 'Unknown Author');
        const narrator = escapeHtml(book.narrator || book.Narrator || '');
        const cover = escapeHtml(book.cover_image || book['Cover Image'] || defaultCover);
        const releaseDate = escapeHtml(book.release_date || book['Release Date'] || '');
        const rating = parseFloat(book.rating || book['Overall Rating'] || 0);
        const ratingCount = Number(book.num_ratings || book.Num_Ratings || 0);
        const asin = escapeHtml(book.asin || book.ASIN || '');
        const ratingMarkup = (!Number.isNaN(rating) && rating > 0)
            ? `<div class="discover-rating">
                    <span class="discover-rating__star">&#9733;</span>
                    <span class="discover-rating__value">${rating.toFixed(1)}</span>
                    ${ratingCount > 0 ? `<span class="discover-rating__count">(${formatRatingsCount(ratingCount)})</span>` : ''}
               </div>`
            : '';

        const status = getLibraryStatus(book);
        const statusLabels = {
            in_library: 'In Library',
            wanted: 'Wanted',
            downloading: 'Downloading'
        };
        const statusClass = status === 'in_library'
            ? 'discover-status discover-status--success'
            : status === 'wanted'
                ? 'discover-status discover-status--warning'
                : status === 'downloading'
                    ? 'discover-status discover-status--info'
                    : '';
        const action = status === 'not_in_library' && asin
            ? `<button type="button" class="discover-btn discover-btn--primary" data-action="add-to-library" data-book="${escapeHtml(JSON.stringify(book))}">+ Add to Library</button>`
            : `<span class="${statusClass}">${statusLabels[status] || 'In Library'}</span>`;

        return `
            <div class="discover-card">
                <div class="discover-media">
                    <div class="discover-cover">
                        <img src="${cover}" alt="${title} cover" loading="lazy" class="discover-cover__img">
                    </div>
                </div>
                <div class="discover-body">
                    <div class="discover-title">${title}</div>
                    <div class="discover-author">${author}</div>
                    <div class="discover-meta-list">
                        ${narrator ? `<div class="discover-meta-item"><i class="fas fa-microphone"></i><span>${narrator}</span></div>` : ''}
                        ${releaseDate ? `<div class="discover-meta-item"><i class="fas fa-calendar-alt"></i><span>${releaseDate}</span></div>` : ''}
                    </div>
                    <div class="discover-rating-row">${ratingMarkup}</div>
                </div>
                <div class="discover-actions">
                    ${action}
                </div>
            </div>
        `;
    }

    function addBookToLibrary(button) {
        let book;
        try {
            book = JSON.parse(button.dataset.book);
        } catch (_) {
            return;
        }

        const original = button.innerHTML;
        button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Adding...';
        button.disabled = true;

        fetch('/search/add-book', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(book)
        })
            .then((response) => response.json())
            .then((payload) => {
                if (!payload.success) {
                    throw new Error(payload.error || 'Failed to add book');
                }
                if (typeof showNotification === 'function') {
                    showNotification(payload.message || 'Book added to library', 'success');
                }
                const badge = document.createElement('span');
                badge.className = 'discover-status discover-status--success';
                badge.textContent = 'In Library';
                button.replaceWith(badge);
            })
            .catch((error) => {
                if (typeof showNotification === 'function') {
                    showNotification(error.message || 'Unable to add book to library', 'error');
                }
                button.innerHTML = original;
                button.disabled = false;
            });
    }
})();
