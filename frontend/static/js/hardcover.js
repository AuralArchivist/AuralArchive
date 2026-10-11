(function () {
    "use strict";

    const STATUS_LABELS = {
        1: "Want to Read", 2: "Currently Reading", 3: "Read",
        4: "Paused", 5: "Did Not Finish", 6: "Ignored",
    };

    function notifyError(message, err) {
        console.error(message, err);
        if (typeof showNotification === "function") {
            showNotification(message, "error");
        }
    }

    function formatDate(isoString) {
        if (!isoString) return "";
        try {
            return new Date(isoString).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
        } catch (e) {
            return isoString;
        }
    }

    function formatDateTime(isoString) {
        if (!isoString) return "";
        try {
            return "Updated " + new Date(isoString).toLocaleString();
        } catch (e) {
            return "";
        }
    }

    function bookCard(entry, opts) {
        opts = opts || {};
        const cover = entry.cover_url
            ? '<img src="' + AA.escapeHtml(entry.cover_url) + '" alt="" loading="lazy" class="discover-cover__img" />'
            : '<div class="discover-cover__img" style="display:flex;align-items:center;justify-content:center;background:var(--b1);color:var(--bc, #cbd5e1);font-size:0.75rem;">No Cover</div>';
        const selectCheckbox = opts.selectable
            ? '<input type="checkbox" class="checkbox checkbox-sm hc-card-select" data-action="select-book" aria-label="Select ' + AA.escapeHtml(entry.title || 'title') + '" />'
            : '';

        const metaItems = [];
        if (entry.stat_label) {
            metaItems.push('<div class="discover-meta-item"><i class="fas fa-chart-line"></i><span>' + AA.escapeHtml(entry.stat_label) + '</span></div>');
        }
        if (entry.release_date) {
            metaItems.push('<div class="discover-meta-item"><i class="fas fa-calendar"></i><span>' + AA.escapeHtml(formatDate(entry.release_date)) + '</span></div>');
        }
        if (entry.narrator) {
            metaItems.push('<div class="discover-meta-item"><i class="fas fa-microphone"></i><span>' + AA.escapeHtml(entry.narrator) + '</span></div>');
        }

        const ratingValue = parseFloat(entry.rating);
        const ratingMarkup = !isNaN(ratingValue) && ratingValue > 0
            ? '<div class="discover-rating"><span class="discover-rating__star">★</span><span class="discover-rating__value">' + ratingValue.toFixed(1) + '</span>' +
              (entry.ratings_count ? '<span class="discover-rating__count">(' + entry.ratings_count + ')</span>' : '') + '</div>'
            : '';

        const statusLabels = {
            in_library: "In Library",
            wanted: "Wanted",
            downloading: "Downloading",
            not_in_library: "Not in Library",
        };
        const addable = !!(entry.asin || entry.has_audio);
        const status = entry.library_status || (addable ? "not_in_library" : null);
        const statusClass = status === "in_library" ? "discover-status discover-status--success"
            : status === "wanted" ? "discover-status discover-status--warning"
            : status === "downloading" ? "discover-status discover-status--info"
            : "discover-status";

        let actionButton = "";
        if (status === "not_in_library" && addable) {
            actionButton = '<button class="discover-btn discover-btn--primary" type="button" data-action="add-to-library" data-asin="' + AA.escapeHtml(entry.asin || '') + '">+ Add to Library</button>';
        } else if (status) {
            actionButton = '<span class="' + statusClass + '">' + AA.escapeHtml(statusLabels[status] || status) + '</span>';
        } else {
            actionButton = '<span class="discover-status">No ASIN Match</span>';
        }

        return [
            '<div class="discover-card">',
            '<div class="discover-media"><div class="discover-cover">' + (opts.selectable && addable ? selectCheckbox : '') + cover + '</div></div>',
            '<div class="discover-body">',
            '<div class="discover-title" title="' + AA.escapeHtml(entry.title || '') + '">' + AA.escapeHtml(entry.title || 'Untitled') + '</div>',
            '<div class="discover-author">' + AA.escapeHtml(entry.author || '') + '</div>',
            '<div class="discover-meta-list">' + metaItems.join('') + '</div>',
            '<div class="discover-rating-row">' + ratingMarkup + '</div>',
            '</div>',
            '<div class="discover-actions">' + actionButton + '</div>',
            '</div>',
        ].join('');
    }

    async function addEntryToLibrary(entry) {
        const resp = await AA.apiFetch("/api/hardcover/add-to-library", {
            method: "POST",
            body: JSON.stringify({
                hardcover_book_id: entry.hardcover_book_id,
                preferred_asin: entry.asin,
                title: entry.title,
                author: entry.author,
                cover_url: entry.cover_url,
                release_date: entry.release_date,
                narrator: entry.narrator,
            }),
        });
        const payload = resp.data || {};
        if (!resp.ok || payload.success === false) {
            throw new Error(payload.error || "Failed to add book");
        }
        return payload;
    }

    function attachCardInteractions(cardEl, entry, opts) {
        opts = opts || {};
        const btn = cardEl.querySelector('[data-action="add-to-library"]');
        if (btn) {
            btn.addEventListener("click", async function (event) {
                event.preventDefault();
                event.stopPropagation();
                const original = btn.innerHTML;
                btn.disabled = true;
                try {
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Adding…';
                    const payload = await addEntryToLibrary(entry);
                    const status = document.createElement("span");
                    status.className = "discover-status discover-status--warning";
                    status.textContent = "Wanted";
                    btn.replaceWith(status);
                    if (typeof showNotification === "function") {
                        showNotification(payload.message || "Book added to library", "success");
                    }
                } catch (err) {
                    btn.innerHTML = original;
                    btn.disabled = false;
                    if (typeof showNotification === "function") {
                        showNotification(err.message || "Unable to add book to library", "error");
                    } else {
                        console.error("Failed to add book to library:", err);
                    }
                }
            });
        }

        const checkbox = cardEl.querySelector('[data-action="select-book"]');
        if (checkbox && typeof opts.onToggleSelect === "function") {
            checkbox.addEventListener("click", function (event) { event.stopPropagation(); });
            checkbox.addEventListener("change", function () {
                opts.onToggleSelect(entry, checkbox.checked, cardEl);
            });
        }
    }

    function renderGrid(containerId, emptyId, entries, opts) {
        opts = opts || {};
        const container = document.getElementById(containerId);
        const empty = document.getElementById(emptyId);
        if (!container) return;
        container.innerHTML = "";
        entries = (entries || []).filter(function (entry) { return !!(entry.asin || entry.has_audio); });
        if (!entries.length) {
            if (empty) empty.classList.remove("hidden");
            return;
        }
        if (empty) empty.classList.add("hidden");
        entries.forEach(function (entry) {
            const div = document.createElement("div");
            div.innerHTML = bookCard(entry, opts);
            const cardEl = div.firstElementChild || div;
            attachCardInteractions(cardEl, entry, opts);
            container.appendChild(cardEl);
        });
    }


    function extractSearchDocuments(rawResults) {
        let parsed = rawResults;
        if (typeof parsed === "string") {
            try { parsed = JSON.parse(parsed); } catch (e) { return []; }
        }
        if (!parsed) return [];
        if (Array.isArray(parsed)) return parsed;
        if (Array.isArray(parsed.hits)) {
            return parsed.hits.map(function (h) { return h.document || h; });
        }
        if (Array.isArray(parsed.documents)) return parsed.documents;
        if (Array.isArray(parsed.results)) return parsed.results;
        return [];
    }

    function searchDocToEntry(doc) {
        const author = Array.isArray(doc.author_names) ? doc.author_names.join(", ") : (doc.author_names || "");
        const image = doc.image && (doc.image.url || doc.image);
        return {
            hardcover_book_id: doc.id,
            title: doc.title,
            author: author,
            cover_url: image || null,
            rating: doc.rating,
            ratings_count: doc.ratings_count,
            audio_seconds: doc.audio_seconds,
            has_audio: !!doc.has_audiobook,
        };
    }

    function searchResultCard(doc) {
        const title = doc.title || doc.name || "Untitled";
        const author = Array.isArray(doc.author_names) ? doc.author_names.join(", ") : (doc.author_names || "");
        const image = doc.image && (doc.image.url || doc.image);
        const cover = image
            ? '<img src="' + AA.escapeHtml(image) + '" class="w-full aspect-[2/3] object-cover rounded" loading="lazy" alt="" />'
            : '<div class="w-full aspect-[2/3] bg-base-300 rounded flex items-center justify-center"><i class="fas fa-book text-base-content/20"></i></div>';
        return [
            '<div class="space-y-1">',
            cover,
            '<div class="text-xs font-medium truncate" title="' + AA.escapeHtml(title) + '">' + AA.escapeHtml(title) + '</div>',
            author ? '<div class="text-[11px] text-base-content/50 truncate">' + AA.escapeHtml(author) + '</div>' : '',
            '</div>',
        ].join("");
    }

    function renderSearchResults(containerId, documents) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = "";
        if (!documents.length) {
            container.innerHTML = '<div class="col-span-full text-center text-base-content/40 py-8">No results.</div>';
            return;
        }
        documents.forEach(function (doc) {
            const div = document.createElement("div");
            div.innerHTML = searchResultCard(doc);
            container.appendChild(div.firstElementChild || div);
        });
    }

    function listResultCard(list) {
        const covers = (list.cover_urls || []).slice(0, 6).map(function (url) {
            return '<img src="' + AA.escapeHtml(url) + '" class="w-10 aspect-[2/3] object-cover rounded" loading="lazy" alt="" />';
        }).join("");
        const coversRow = covers ? '<div class="flex gap-1 flex-wrap">' + covers + '</div>' : '';
        const metaBits = [];
        if (list.books_count != null) metaBits.push(list.books_count + " books");
        if (list.likes_count != null) metaBits.push(list.likes_count + " likes");
        if (list.user && list.user.username) metaBits.push("by " + list.user.username);
        const clickable = list.id != null;
        return [
            '<div class="card bg-base-100 shadow' + (clickable ? ' cursor-pointer hover:shadow-md' : '') + '"' + (clickable ? ' data-action="view-list" data-list-id="' + AA.escapeHtml(String(list.id)) + '" data-list-name="' + AA.escapeHtml(list.name || 'Untitled list') + '"' : '') + '>',
            '<div class="card-body p-4 space-y-2">',
            '<h3 class="font-semibold text-sm truncate">' + AA.escapeHtml(list.name || "Untitled list") + '</h3>',
            list.description ? '<p class="text-xs text-base-content/50 line-clamp-2">' + AA.escapeHtml(list.description) + '</p>' : '',
            coversRow,
            '<div class="text-[11px] text-base-content/40">' + AA.escapeHtml(metaBits.join(" · ")) + '</div>',
            clickable ? '<button class="btn btn-ghost btn-xs self-start" type="button" data-action="view-list" data-list-id="' + AA.escapeHtml(String(list.id)) + '" data-list-name="' + AA.escapeHtml(list.name || 'Untitled list') + '">Browse Titles →</button>' : '',
            '</div></div>',
        ].join("");
    }

    function renderLists(containerId, emptyId, lists) {
        const container = document.getElementById(containerId);
        const empty = document.getElementById(emptyId);
        if (!container) return;
        container.innerHTML = "";
        if (!lists || !lists.length) {
            if (empty) empty.classList.remove("hidden");
            return;
        }
        if (empty) empty.classList.add("hidden");
        lists.forEach(function (list) {
            const div = document.createElement("div");
            div.innerHTML = listResultCard(list);
            const cardEl = div.firstElementChild || div;
            cardEl.querySelectorAll('[data-action="view-list"]').forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.preventDefault();
                    event.stopPropagation();
                    openListDetail(parseInt(el.dataset.listId, 10), el.dataset.listName);
                });
            });
            container.appendChild(cardEl);
        });
    }


    const listDetailState = {
        listId: null,
        nextOffset: 0,
        hasMore: false,
        selected: new Map(), // hardcover_book_id -> { entry, cardEl }
    };

    function updateBulkToolbar() {
        const toolbar = document.getElementById("hcListBulkToolbar");
        const countEl = document.getElementById("hcListSelectedCount");
        const count = listDetailState.selected.size;
        if (countEl) countEl.textContent = count + " selected";
        if (toolbar) toolbar.classList.toggle("hidden", count === 0);
    }

    function handleListDetailToggleSelect(entry, checked, cardEl) {
        const key = entry.hardcover_book_id;
        if (checked) {
            listDetailState.selected.set(key, { entry: entry, cardEl: cardEl });
        } else {
            listDetailState.selected.delete(key);
        }
        updateBulkToolbar();
    }

    function clearListDetailSelection() {
        listDetailState.selected.forEach(function (item) {
            const checkbox = item.cardEl.querySelector('[data-action="select-book"]');
            if (checkbox) checkbox.checked = false;
        });
        listDetailState.selected.clear();
        updateBulkToolbar();
    }

    function renderListDetailPage(entries) {
        const container = document.getElementById("hcListDetailGrid");
        const empty = document.getElementById("hcListDetailEmpty");
        if (!container) return;
        entries = (entries || []).filter(function (entry) { return !!(entry.asin || entry.has_audio); });
        if (empty) empty.classList.toggle("hidden", container.children.length > 0 || entries.length > 0);
        entries.forEach(function (entry) {
            const div = document.createElement("div");
            div.innerHTML = bookCard(entry, { selectable: true });
            const cardEl = div.firstElementChild || div;
            attachCardInteractions(cardEl, entry, { onToggleSelect: handleListDetailToggleSelect });
            container.appendChild(cardEl);
        });
    }

    async function loadListDetailPage() {
        const loadMoreBtn = document.getElementById("hcListDetailLoadMoreBtn");
        const loadMoreWrap = document.getElementById("hcListDetailLoadMoreWrap");
        const isFirstPage = listDetailState.nextOffset === 0;
        if (loadMoreBtn) { loadMoreBtn.disabled = true; loadMoreBtn.textContent = "Loading…"; }
        try {
            const resp = await AA.apiFetch("/api/hardcover/lists/" + listDetailState.listId + "?offset=" + listDetailState.nextOffset);
            if (!resp.ok) return;
            const data = resp.data || {};
            if (isFirstPage && data.list) {
                const descEl = document.getElementById("hcListDetailDescription");
                if (descEl) descEl.textContent = data.list.description || "";
            }
            renderListDetailPage(data.entries || []);
            listDetailState.nextOffset = data.next_offset || listDetailState.nextOffset;
            listDetailState.hasMore = !!data.has_more;
            if (loadMoreWrap) loadMoreWrap.classList.toggle("hidden", !listDetailState.hasMore);
        } catch (err) {
            notifyError("Failed to load list books", err);
        } finally {
            if (loadMoreBtn) { loadMoreBtn.disabled = false; loadMoreBtn.textContent = "Load More"; }
        }
    }

    async function openListDetail(listId, listName) {
        listDetailState.listId = listId;
        listDetailState.nextOffset = 0;
        listDetailState.hasMore = false;
        listDetailState.selected.clear();
        updateBulkToolbar();

        const grid = document.getElementById("hcListDetailGrid");
        if (grid) grid.innerHTML = "";
        const empty = document.getElementById("hcListDetailEmpty");
        if (empty) empty.classList.add("hidden");
        const loadMoreWrap = document.getElementById("hcListDetailLoadMoreWrap");
        if (loadMoreWrap) loadMoreWrap.classList.add("hidden");

        const nameEl = document.getElementById("hcListDetailName");
        const descEl = document.getElementById("hcListDetailDescription");
        if (nameEl) nameEl.textContent = listName || "";
        if (descEl) descEl.textContent = "";

        document.getElementById("hcListsBrowseView").classList.add("hidden");
        document.getElementById("hcListDetailView").classList.remove("hidden");

        await loadListDetailPage();
    }

    const hcListDetailBack = document.getElementById("hcListDetailBack");
    if (hcListDetailBack) {
        hcListDetailBack.addEventListener("click", function () {
            document.getElementById("hcListDetailView").classList.add("hidden");
            document.getElementById("hcListsBrowseView").classList.remove("hidden");
        });
    }

    const hcListDetailLoadMoreBtn = document.getElementById("hcListDetailLoadMoreBtn");
    if (hcListDetailLoadMoreBtn) {
        hcListDetailLoadMoreBtn.addEventListener("click", loadListDetailPage);
    }

    const hcListClearSelectionBtn = document.getElementById("hcListClearSelectionBtn");
    if (hcListClearSelectionBtn) {
        hcListClearSelectionBtn.addEventListener("click", clearListDetailSelection);
    }

    const hcListAddSelectedBtn = document.getElementById("hcListAddSelectedBtn");
    if (hcListAddSelectedBtn) {
        hcListAddSelectedBtn.addEventListener("click", async function () {
            const items = Array.from(listDetailState.selected.values());
            if (!items.length) return;
            hcListAddSelectedBtn.disabled = true;
            let succeeded = 0;
            let failed = 0;
            for (const item of items) {
                const btn = item.cardEl.querySelector('[data-action="add-to-library"]');
                try {
                    if (btn) btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Adding…';
                    const payload = await addEntryToLibrary(item.entry);
                    succeeded++;
                    if (btn) {
                        const status = document.createElement("span");
                        status.className = "discover-status discover-status--warning";
                        status.textContent = "Wanted";
                        btn.replaceWith(status);
                    }
                } catch (err) {
                    failed++;
                    if (btn) btn.innerHTML = '+ Add to Library';
                    console.error("Failed to add \"" + (item.entry.title || "book") + "\" to library:", err);
                }
                const checkbox = item.cardEl.querySelector('[data-action="select-book"]');
                if (checkbox) checkbox.checked = false;
                listDetailState.selected.delete(item.entry.hardcover_book_id);
                updateBulkToolbar();
            }
            hcListAddSelectedBtn.disabled = false;
            if (typeof showNotification === "function") {
                const message = failed
                    ? "Added " + succeeded + " book(s), " + failed + " failed"
                    : "Added " + succeeded + " book(s) to library";
                showNotification(message, failed ? "warning" : "success");
            }
        });
    }


    const loadedPanels = {};

    document.querySelectorAll("#hcTabs .tab").forEach(function (btn) {
        btn.addEventListener("click", function () {
            document.querySelectorAll("#hcTabs .tab").forEach(function (t) { t.classList.remove("tab-active"); });
            btn.classList.add("tab-active");
            const target = btn.dataset.tab;
            document.querySelectorAll(".hc-panel").forEach(function (p) { p.classList.add("hidden"); });
            const panel = document.getElementById("hcPanel-" + target);
            if (panel) panel.classList.remove("hidden");
            ensurePanelLoaded(target);
        });
    });

    function ensurePanelLoaded(target) {
        if (target === "trending") { loadTrending("1m"); return; }
        if (target === "releases" && !loadedPanels.releases) { loadedPanels.releases = true; loadNewReleases(); return; }
        if (target === "shelves" && !loadedPanels.shelves) { loadedPanels.shelves = true; loadShelves(); return; }
        if (target === "lists" && !loadedPanels.lists) { loadedPanels.lists = true; loadListsBrowse("featured"); return; }
        if (target === "genres" && !loadedPanels.genres) { loadedPanels.genres = true; loadGenreMoodChips(); return; }
    }


    document.querySelectorAll("#hcTrendingWindows .tab").forEach(function (btn) {
        btn.addEventListener("click", function () {
            document.querySelectorAll("#hcTrendingWindows .tab").forEach(function (t) { t.classList.remove("tab-active"); });
            btn.classList.add("tab-active");
            loadTrending(btn.dataset.window);
        });
    });

    async function loadTrending(window) {
        try {
            const resp = await AA.apiFetch("/api/hardcover/trending?window=" + encodeURIComponent(window));
            if (!resp.ok) return;
            const data = resp.data || {};
            renderGrid("hcTrendingGrid", "hcTrendingEmpty", data.entries || []);
            document.getElementById("hcTrendingRefreshedAt").textContent = formatDateTime(data.refreshed_at) + (data.stale ? " (stale)" : "");
        } catch (err) {
            notifyError("Failed to load trending books", err);
        }
    }


    async function loadNewReleases() {
        try {
            const resp = await AA.apiFetch("/api/hardcover/new-releases");
            if (!resp.ok) return;
            const data = resp.data || {};
            renderGrid("hcReleasesGrid", "hcReleasesEmpty", data.entries || []);
            document.getElementById("hcReleasesRefreshedAt").textContent = formatDateTime(data.refreshed_at) + (data.stale ? " (stale)" : "");
        } catch (err) {
            notifyError("Failed to load new releases", err);
        }
    }


    async function loadShelves() {
        const body = document.getElementById("hcShelvesBody");
        const empty = document.getElementById("hcShelvesEmpty");
        if (!body) return;
        try {
            const resp = await AA.apiFetch("/api/hardcover/shelves");
            const data = resp.data || {};
            if (!resp.ok) {
                body.innerHTML = "";
                if (empty) { empty.textContent = data.error || "Failed to load shelves."; empty.classList.remove("hidden"); }
                return;
            }
            const shelves = data.shelves || [];
            body.innerHTML = "";
            if (!shelves.length) {
                if (empty) { empty.textContent = "Nothing shelved on Hardcover yet."; empty.classList.remove("hidden"); }
                return;
            }
            if (empty) empty.classList.add("hidden");
            shelves.forEach(function (entry) {
                const book = entry.book || {};
                const authors = (book.contributions || []).map(function (c) { return c.author && c.author.name; }).filter(Boolean).join(", ");
                const cover = book.image && book.image.url;
                const tr = document.createElement("tr");
                tr.innerHTML = [
                    '<td><div class="w-8 h-11 rounded overflow-hidden bg-base-300">' + (cover ? '<img src="' + AA.escapeHtml(cover) + '" class="w-full h-full object-cover" loading="lazy" alt="" />' : '') + '</div></td>',
                    '<td class="font-medium">' + AA.escapeHtml(book.title || "Untitled") + '</td>',
                    '<td class="text-base-content/60">' + AA.escapeHtml(authors) + '</td>',
                    '<td><span class="badge badge-sm badge-ghost">' + AA.escapeHtml(STATUS_LABELS[entry.status_id] || "—") + '</span></td>',
                    '<td>' + (entry.owned ? '<i class="fas fa-check text-success"></i>' : '') + '</td>',
                ].join("");
                body.appendChild(tr);
            });
        } catch (err) {
            notifyError("Failed to load your shelves", err);
        }
    }


    const hcSearchBtn = document.getElementById("hcSearchBtn");
    if (hcSearchBtn) {
        hcSearchBtn.addEventListener("click", async function () {
            const q = (document.getElementById("hcSearchInput").value || "").trim();
            const type = document.getElementById("hcSearchType").value;
            if (!q) return;
            try {
                const resp = await AA.apiFetch("/api/hardcover/search?q=" + encodeURIComponent(q) + "&type=" + encodeURIComponent(type));
                const data = resp.data || {};
                const docs = extractSearchDocuments(data.results);
                const searchEmpty = document.getElementById("hcSearchEmpty");
                if (searchEmpty) searchEmpty.classList.add("hidden");
                if (type === "book") {
                    renderGrid("hcSearchResults", "hcSearchEmpty", docs.map(searchDocToEntry));
                } else {
                    renderSearchResults("hcSearchResults", docs);
                }
            } catch (err) {
                notifyError("Hardcover search failed", err);
            }
        });
    }

    const hcSearchInput = document.getElementById("hcSearchInput");
    if (hcSearchInput) {
        hcSearchInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") hcSearchBtn.click();
        });
    }


    async function loadListsBrowse(sort) {
        try {
            const resp = await AA.apiFetch("/api/hardcover/lists/browse?sort=" + encodeURIComponent(sort));
            if (!resp.ok) return;
            const data = resp.data || {};
            renderLists("hcListsResults", "hcListsEmpty", data.lists || []);
        } catch (err) {
            notifyError("Failed to browse lists", err);
        }
    }

    document.querySelectorAll("#hcListsSort .tab").forEach(function (btn) {
        btn.addEventListener("click", function () {
            document.querySelectorAll("#hcListsSort .tab").forEach(function (t) { t.classList.remove("tab-active"); });
            btn.classList.add("tab-active");
            const input = document.getElementById("hcListsInput");
            if (input) input.value = "";
            loadListsBrowse(btn.dataset.sort);
        });
    });

    const hcListsBtn = document.getElementById("hcListsBtn");
    if (hcListsBtn) {
        hcListsBtn.addEventListener("click", async function () {
            const q = (document.getElementById("hcListsInput").value || "").trim();
            if (!q) {
                const activeSort = document.querySelector("#hcListsSort .tab-active");
                loadListsBrowse(activeSort ? activeSort.dataset.sort : "featured");
                return;
            }
            try {
                const resp = await AA.apiFetch("/api/hardcover/search?q=" + encodeURIComponent(q) + "&type=list");
                const data = resp.data || {};
                const docs = extractSearchDocuments(data.results);
                renderLists("hcListsResults", "hcListsEmpty", docs);
            } catch (err) {
                notifyError("Hardcover list search failed", err);
            }
        });
    }

    const hcListsInput = document.getElementById("hcListsInput");
    if (hcListsInput) {
        hcListsInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") hcListsBtn.click();
        });
    }


    async function loadGenreMoodChips() {
        try {
            const [genresResp, moodsResp] = await Promise.all([
                AA.apiFetch("/api/hardcover/genres"),
                AA.apiFetch("/api/hardcover/moods"),
            ]);
            renderChips("hcGenreChips", (genresResp.data || {}).genres || [], "genre");
            renderChips("hcMoodChips", (moodsResp.data || {}).moods || [], "mood");
        } catch (err) {
            console.error("Failed to load genre/mood chips:", err);
        }
    }

    function renderChips(containerId, terms, type) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = "";
        terms.forEach(function (term) {
            const btn = document.createElement("button");
            btn.className = "btn btn-xs btn-outline";
            btn.textContent = term;
            btn.addEventListener("click", async function () {
                container.querySelectorAll("button").forEach(function (b) { b.classList.remove("btn-primary"); });
                btn.classList.add("btn-primary");
                try {
                    const resp = await AA.apiFetch("/api/hardcover/browse?type=" + type + "&term=" + encodeURIComponent(term));
                    const data = resp.data || {};
                    const docs = extractSearchDocuments(data.results);
                    renderGrid("hcGenreResults", "hcGenreEmpty", docs.map(searchDocToEntry));
                } catch (err) {
                    notifyError("Hardcover browse failed", err);
                }
            });
            container.appendChild(btn);
        });
    }


    const hcPageRefreshBtn = document.getElementById("hcPageRefreshBtn");
    if (hcPageRefreshBtn) {
        hcPageRefreshBtn.addEventListener("click", async function () {
            hcPageRefreshBtn.disabled = true;
            try {
                await AA.apiFetch("/api/hardcover/refresh", { method: "POST" });
                await loadTrending(document.querySelector("#hcTrendingWindows .tab-active").dataset.window);
                await loadNewReleases();
            } finally {
                hcPageRefreshBtn.disabled = false;
            }
        });
    }


    async function init() {
        try {
            const resp = await AA.apiFetch("/api/hardcover/status");
            const data = resp.data || {};
            if (!data.connected) {
                document.getElementById("hcNotConnectedCard").classList.remove("hidden");
                return;
            }
            document.getElementById("hcConnectedContent").classList.remove("hidden");
            document.getElementById("hcPageRefreshBtn").classList.remove("hidden");
            loadTrending("1m");
        } catch (err) {
            console.error("Failed to check Hardcover status:", err);
            document.getElementById("hcNotConnectedCard").classList.remove("hidden");
        }
    }

    init();
})();
