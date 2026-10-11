// Release calendar: the Discover schedule (timeline with date rail) and the dashboard strip
(function () {
    "use strict";

    const esc = (value) => AA.escapeHtml(value == null ? "" : String(value));
    const WATCH_COLORS = ["#818cf8", "#f472b6", "#34d399", "#22d3ee", "#a78bfa", "#f87171", "#60a5fa", "#fb923c"];
    // Wide enough for every unreleased Wanted book, within the API's 400-day limit
    const SCHEDULE_BACK_DAYS = 60;
    const SCHEDULE_AHEAD_DAYS = 330;

    // Local dates as YYYY-MM-DD; toISOString would shift the day across timezones
    function iso(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    function addDays(d, n) {
        const copy = new Date(d);
        copy.setDate(copy.getDate() + n);
        return copy;
    }
    function parseIso(value) {
        const [y, m, d] = value.split("-").map(Number);
        return new Date(y, m - 1, d);
    }
    function longDate(value) {
        return parseIso(value).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
    }
    function relativeDay(value) {
        const diff = Math.round((parseIso(value) - parseIso(iso(new Date()))) / 86400000);
        return { 0: "Today", 1: "Tomorrow", "-1": "Yesterday" }[diff] || null;
    }
    function hours(minutes) {
        return minutes ? `${Math.round(minutes / 6) / 10} hrs` : "";
    }
    function keyOf(entry) {
        return entry.asin || `book:${entry.book_id}`;
    }
    function seriesLabel(entry) {
        return entry.series ? `${entry.series}${entry.sequence ? " #" + entry.sequence : ""}` : "";
    }
    function isWanted(entry) {
        return entry.library_status === "wanted";
    }
    function coverHtml(entry, cls) {
        return entry.cover_url
            ? `<img class="${cls}" src="${esc(entry.cover_url)}" alt="" loading="lazy">`
            : `<div class="${cls} rcal-nocover"><i class="fas fa-book"></i></div>`;
    }

    // --- Shared details panel ---------------------------------------------------

    const panel = {
        el: null,
        owner: null,
        ensure() {
            if (this.el) return this.el;
            this.el = document.createElement("aside");
            this.el.className = "rcal rcal-panel";
            this.el.setAttribute("aria-label", "Book details");
            this.el.innerHTML = `
                <div class="rcal-panel-head"><span>Book details</span>
                    <button type="button" class="rcal-btn rcal-btn--icon" data-rcal-close aria-label="Close"><i class="fas fa-xmark"></i></button></div>
                <div class="rcal-panel-body"></div>`;
            this.el.addEventListener("click", (event) => {
                if (event.target.closest("[data-rcal-close]")) {
                    this.close();
                    return;
                }
                const add = event.target.closest("[data-rcal-add]");
                if (add && this.owner) this.owner.addToLibrary(add);
            });
            document.addEventListener("keydown", (event) => {
                if (event.key === "Escape") this.close();
            });
            document.body.appendChild(this.el);
            return this.el;
        },
        open(owner, entry) {
            this.owner = owner;
            const el = this.ensure();
            const watches = owner.watchesById();
            const facts = [
                ["Releases", longDate(entry.release_date)],
                ["Series", seriesLabel(entry)],
                ["Author", entry.authors],
                ["Narrator", entry.narrators],
                ["Length", hours(entry.runtime_min)],
                ["Publisher", entry.publisher],
                ["Language", entry.language ? entry.language.charAt(0).toUpperCase() + entry.language.slice(1) : ""],
                ["Found by", (entry.watch_ids || []).map((id) => watches[id] && watches[id].label).filter(Boolean).join(", ")
                    || (isWanted(entry) ? "Your wanted list" : "")],
            ].filter(([, value]) => value);
            const summary = (entry.summary || "").split(/\n{2,}/).filter(Boolean)
                .map((para) => `<p>${esc(para)}</p>`).join("") || '<p class="rcal-muted">No summary available yet.</p>';
            const action = isWanted(entry)
                ? '<span class="rcal-badge rcal-badge--wanted rcal-badge--lg"><i class="fas fa-star"></i> On your wanted list</span>'
                : entry.library_status
                    ? '<span class="rcal-badge rcal-badge--owned rcal-badge--lg">In your library</span>'
                    : `<button type="button" class="rcal-btn rcal-btn--primary" data-rcal-add="${esc(entry.asin)}"><i class="fas fa-plus"></i> Add to wanted</button>`;
            const audible = entry.asin
                ? `<a class="rcal-btn" href="https://www.audible.com/pd/${encodeURIComponent(entry.asin)}" target="_blank" rel="noopener"><i class="fas fa-arrow-up-right-from-square"></i> Audible</a>`
                : "";
            el.querySelector(".rcal-panel-body").innerHTML = `
                ${coverHtml(entry, "rcal-panel-cover")}
                <h3 class="rcal-panel-title">${esc(entry.title)}</h3>
                ${entry.subtitle ? `<div class="rcal-panel-sub">${esc(entry.subtitle)}</div>` : ""}
                <dl class="rcal-facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
                <div class="rcal-panel-actions">${action}${audible}</div>
                <div class="rcal-panel-summary">${summary}</div>`;
            el.querySelector(".rcal-panel-body").scrollTop = 0;
            el.classList.add("is-open");
            document.querySelectorAll(".rcal-card.is-selected").forEach((c) => c.classList.remove("is-selected"));
            document.querySelectorAll(`.rcal-card[data-rcal-book="${CSS.escape(keyOf(entry))}"]`).forEach((c) => c.classList.add("is-selected"));
        },
        close() {
            if (!this.el) return;
            this.el.classList.remove("is-open");
            document.querySelectorAll(".rcal-card.is-selected").forEach((c) => c.classList.remove("is-selected"));
        },
    };

    // --- Calendar ---------------------------------------------------------------

    class ReleaseCalendar {
        constructor(root) {
            this.root = root;
            this.strip = root.dataset.mode === "strip";
            this.entries = [];
            this.watches = [];
            this.hidden = new Set();
            this.showPast = false;
            this.query = "";
            this.month = new Date();
            this.month.setDate(1);
            this.selectedDay = null;
            root.classList.add("rcal", this.strip ? "rcal--strip" : "rcal--schedule");
            root.addEventListener("click", (event) => this.onClick(event));
            root.addEventListener("input", (event) => {
                if (event.target.matches("[data-rcal-search]")) {
                    this.query = event.target.value.trim().toLowerCase();
                    this.renderTimeline();
                    this.renderMini();
                }
            });
            root.addEventListener("change", (event) => {
                const filter = event.target.closest("[data-rcal-filter]");
                if (!filter) return;
                const key = filter.dataset.rcalFilter;
                if (key === "past") this.showPast = filter.checked;
                else {
                    const id = key === "wanted" ? "wanted" : Number(key);
                    filter.checked ? this.hidden.delete(id) : this.hidden.add(id);
                }
                this.renderTimeline();
                this.renderMini();
            });
            this.load();
        }

        range() {
            const today = new Date();
            if (this.strip) return [today, addDays(today, 6)];
            return [addDays(today, -SCHEDULE_BACK_DAYS), addDays(today, SCHEDULE_AHEAD_DAYS)];
        }

        async load() {
            const [start, end] = this.range();
            const resp = await AA.apiFetch(`/api/calendar?start=${iso(start)}&end=${iso(end)}`);
            if (!resp.ok || !resp.data || !resp.data.success) {
                this.root.innerHTML = '<div class="rcal-empty">Couldn\'t load the release calendar.</div>';
                return;
            }
            this.entries = resp.data.entries || [];
            this.watches = (resp.data.watches || []).filter((w) => w.enabled);
            this.render();
        }

        watchesById() {
            return Object.fromEntries(this.watches.map((w) => [w.id, w]));
        }

        watchColor(id) {
            const index = this.watches.findIndex((w) => w.id === id);
            return WATCH_COLORS[(index < 0 ? 0 : index) % WATCH_COLORS.length];
        }

        // Wanted books show while "My wanted" is on, even when no watch found them
        passesFilters(entry) {
            const viaWanted = isWanted(entry) && !this.hidden.has("wanted");
            const viaWatch = (entry.watch_ids || []).some((id) => !this.hidden.has(id));
            if (!viaWanted && !viaWatch) return false;
            if (this.query) {
                const haystack = `${entry.title} ${entry.subtitle || ""} ${entry.authors || ""} ${entry.series || ""}`.toLowerCase();
                if (!haystack.includes(this.query)) return false;
            }
            return true;
        }

        visibleEntries() {
            const today = iso(new Date());
            return this.entries.filter((e) => this.passesFilters(e) && (this.showPast || e.release_date >= today));
        }

        render() {
            if (this.strip) {
                this.renderStrip(this.entries.filter((e) => this.passesFilters(e)));
                return;
            }
            this.root.innerHTML = `
                <div class="rcal-hero">
                    <div><h2>Release Calendar</h2><p>Upcoming and recent titles from your watches, plus everything on your wanted list.</p></div>
                    <div class="rcal-hero-actions">
                        <a class="rcal-btn" href="/settings#discovery"><i class="fas fa-sliders"></i> Manage watches</a>
                        <button type="button" class="rcal-btn rcal-btn--primary" data-rcal-refresh><i class="fas fa-rotate"></i> Refresh</button>
                    </div>
                </div>
                <div class="rcal-stats">${this.statsHtml()}</div>
                <div class="rcal-layout">
                    <aside class="rcal-rail">
                        <div class="rcal-box">
                            <div class="rcal-box-head">Jump to date</div>
                            <div class="rcal-mini" data-rcal-mini></div>
                        </div>
                        <div class="rcal-box">
                            <div class="rcal-box-head">Filter</div>
                            <div class="rcal-filters">${this.filtersHtml()}</div>
                        </div>
                    </aside>
                    <section class="rcal-timeline" data-rcal-timeline aria-live="polite"></section>
                </div>`;
            this.renderMini();
            this.renderTimeline();
        }

        statsHtml() {
            const today = iso(new Date());
            const upcoming = this.entries.filter((e) => e.release_date >= today);
            const within = (days) => upcoming.filter((e) => e.release_date <= iso(addDays(new Date(), days))).length;
            const tiles = [
                ["fa-bolt", "Releasing today", upcoming.filter((e) => e.release_date === today).length, "var(--p)"],
                ["fa-calendar-week", "Next 7 days", within(6), "var(--in)"],
                ["fa-calendar", "Next 30 days", within(30), "var(--su)"],
                ["fa-star", "Wanted, unreleased", upcoming.filter(isWanted).length, "var(--wa)"],
            ];
            return tiles.map(([icon, label, value, color]) => `
                <div class="rcal-stat">
                    <div class="rcal-stat-ico" style="background:color-mix(in oklch,${color} 20%,transparent);color:${color}"><i class="fas ${icon}"></i></div>
                    <div><div class="rcal-stat-v">${value}</div><div class="rcal-stat-k">${label}</div></div>
                </div>`).join("");
        }

        filtersHtml() {
            const today = iso(new Date());
            const upcoming = this.entries.filter((e) => e.release_date >= today);
            const count = (fn) => upcoming.filter(fn).length;
            const rows = this.watches.map((w) => `
                <label class="rcal-check"><input type="checkbox" data-rcal-filter="${w.id}" ${this.hidden.has(w.id) ? "" : "checked"}>
                    <span class="rcal-dot" style="background:${this.watchColor(w.id)}"></span>${esc(w.label)}
                    <span class="rcal-check-n">${count((e) => (e.watch_ids || []).includes(w.id))}</span></label>`);
            rows.push(`
                <label class="rcal-check"><input type="checkbox" data-rcal-filter="wanted" ${this.hidden.has("wanted") ? "" : "checked"}>
                    <span class="rcal-dot" style="background:var(--wa)"></span>My wanted<span class="rcal-check-n">${count(isWanted)}</span></label>
                <label class="rcal-check"><input type="checkbox" data-rcal-filter="past" ${this.showPast ? "checked" : ""}>
                    <span class="rcal-dot" style="background:var(--aa-muted2)"></span>Show recent releases</label>`);
            return `
                <label class="rcal-search"><i class="fas fa-magnifying-glass"></i>
                    <input type="search" data-rcal-search placeholder="Title, author or series" value="${esc(this.query)}" aria-label="Search the calendar"></label>
                ${rows.join("")}
                ${this.watches.length ? "" : '<p class="rcal-hint">No watches yet. <a href="/settings#discovery">Add a tag or keyword</a> to fill the calendar.</p>'}`;
        }

        renderMini() {
            const host = this.root.querySelector("[data-rcal-mini]");
            if (!host) return;
            const first = new Date(this.month);
            const start = addDays(first, -((first.getDay() + 6) % 7));
            const today = iso(new Date());
            const counts = {};
            const wanted = {};
            this.entries.filter((e) => this.passesFilters(e)).forEach((e) => {
                counts[e.release_date] = (counts[e.release_date] || 0) + 1;
                if (isWanted(e)) wanted[e.release_date] = true;
            });
            const max = Math.max(1, ...Object.values(counts));
            let cells = ["M", "T", "W", "T", "F", "S", "S"].map((d) => `<div class="rcal-mini-dow">${d}</div>`).join("");
            for (let i = 0; i < 42; i++) {
                const day = addDays(start, i);
                const key = iso(day);
                const n = counts[key] || 0;
                const cls = ["rcal-mini-day", day.getMonth() !== first.getMonth() && "is-out", n && "has",
                    key === today && "is-today", this.selectedDay === key && "is-selected", wanted[key] && "has-wanted"].filter(Boolean).join(" ");
                const heat = n ? `<span class="rcal-heat" style="width:${6 + 14 * n / max}px;opacity:${0.35 + 0.65 * n / max}"></span>` : "";
                cells += `<button type="button" class="${cls}" ${n ? `data-rcal-jump="${key}"` : "disabled"} title="${n ? `${n} title${n === 1 ? "" : "s"}` : ""}">${day.getDate()}${heat}</button>`;
            }
            host.innerHTML = `
                <div class="rcal-mini-head">
                    <button type="button" class="rcal-btn rcal-btn--icon" data-rcal-month="-1" aria-label="Previous month"><i class="fas fa-chevron-left"></i></button>
                    <span>${esc(this.month.toLocaleDateString(undefined, { month: "long", year: "numeric" }))}</span>
                    <button type="button" class="rcal-btn rcal-btn--icon" data-rcal-month="1" aria-label="Next month"><i class="fas fa-chevron-right"></i></button>
                </div>
                <div class="rcal-mini-grid">${cells}</div>`;
        }

        card(entry) {
            // One status badge, as in the design: Wanted / In library, otherwise the watch that found it
            const tags = [];
            if (isWanted(entry)) tags.push('<span class="rcal-badge rcal-badge--wanted"><i class="fas fa-star"></i> Wanted</span>');
            else if (entry.library_status) tags.push('<span class="rcal-badge rcal-badge--owned">In library</span>');
            else {
                const id = (entry.watch_ids || [])[0];
                const watch = this.watchesById()[id];
                if (watch) tags.push(`<span class="rcal-badge" style="border-color:color-mix(in oklch,${this.watchColor(id)} 60%,transparent);color:${this.watchColor(id)}">${esc(watch.label)}</span>`);
            }
            if (entry.runtime_min) tags.push(`<span class="rcal-badge"><i class="fas fa-clock"></i> ${hours(entry.runtime_min)}</span>`);
            const add = entry.library_status || !entry.asin
                ? ""
                : `<button type="button" class="rcal-btn rcal-btn--primary rcal-btn--icon rcal-card-add" data-rcal-add="${esc(entry.asin)}" title="Add to wanted" aria-label="Add ${esc(entry.title)} to wanted"><i class="fas fa-plus"></i></button>`;
            return `
                <div class="rcal-card" data-rcal-book="${esc(keyOf(entry))}" tabindex="0">
                    ${coverHtml(entry, "rcal-card-cover")}
                    <div class="rcal-card-body">
                        <div class="rcal-card-title">${esc(entry.title)}</div>
                        <div class="rcal-card-author">${esc(entry.authors || "")}</div>
                        ${seriesLabel(entry) ? `<div class="rcal-card-series">${esc(seriesLabel(entry))}</div>` : ""}
                        <div class="rcal-card-tags">${tags.join("")}</div>
                    </div>
                    ${add}
                </div>`;
        }

        renderTimeline() {
            const host = this.root.querySelector("[data-rcal-timeline]");
            if (!host) return;
            const groups = new Map();
            this.visibleEntries().forEach((e) => {
                if (!groups.has(e.release_date)) groups.set(e.release_date, []);
                groups.get(e.release_date).push(e);
            });
            if (!groups.size) {
                host.innerHTML = `<div class="rcal-empty">${this.query ? "No titles match your search." : "Nothing coming up for these filters."}</div>`;
                return;
            }
            const today = iso(new Date());
            host.innerHTML = Array.from(groups).map(([day, items]) => `
                <div class="rcal-daygroup ${day < today ? "is-past" : ""}" id="rcal-day-${day}">
                    <div class="rcal-daygroup-head">
                        ${relativeDay(day) ? `<span class="rcal-rel">${relativeDay(day)}</span>` : ""}
                        <span class="rcal-daygroup-date">${esc(longDate(day))}</span>
                        <span class="rcal-daygroup-count">${items.length} title${items.length === 1 ? "" : "s"}</span>
                    </div>
                    <div class="rcal-cards">${items.map((e) => this.card(e)).join("")}</div>
                </div>`).join("");
        }

        jumpTo(day) {
            this.selectedDay = day;
            if (day < iso(new Date()) && !this.showPast) {
                this.showPast = true;
                const box = this.root.querySelector('[data-rcal-filter="past"]');
                if (box) box.checked = true;
                this.renderTimeline();
            }
            this.renderMini();
            const target = this.root.querySelector(`#rcal-day-${CSS.escape(day)}`);
            if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        }

        stripCard(entry) {
            const key = esc(keyOf(entry));
            const cover = entry.cover_url
                ? `<img src="${esc(entry.cover_url)}" alt="" loading="lazy">`
                : '<div class="rcal-tile-nocover"><i class="fas fa-book"></i></div>';
            const status = isWanted(entry)
                ? '<span class="rcal-badge rcal-badge--wanted">Wanted</span>'
                : entry.library_status ? '<span class="rcal-badge rcal-badge--owned">In library</span>' : "";
            const add = entry.library_status || !entry.asin
                ? ""
                : `<button type="button" class="rcal-tile-btn rcal-tile-btn--add" data-rcal-add="${esc(entry.asin)}" title="Add to wanted"><i class="fas fa-plus"></i></button>`;
            const label = relativeDay(entry.release_date)
                || parseIso(entry.release_date).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
            return `
                <div class="rcal-tile" data-rcal-book="${key}" title="${esc(entry.title)}">
                    <div class="rcal-tile-cover">
                        ${cover}
                        <span class="rcal-tile-date">${esc(label)}</span>
                        <div class="rcal-tile-overlay">
                            <button type="button" class="rcal-tile-btn" data-rcal-book="${key}" title="Book details"><i class="fas fa-info"></i></button>
                            ${add}
                        </div>
                    </div>
                    <div class="rcal-tile-title">${esc(entry.title)}</div>
                    <div class="rcal-tile-author">${esc(entry.authors || "")}</div>
                    ${status ? `<div class="rcal-tile-status">${status}</div>` : ""}
                </div>`;
        }

        renderStrip(entries) {
            if (!entries.length) {
                this.root.innerHTML = '<div class="rcal-empty">Nothing releasing in the next 7 days. <a href="/settings#discovery">Release calendar settings</a></div>';
                return;
            }
            this.root.innerHTML = `
                <div class="rcal-strip-wrap">
                    <button type="button" class="rcal-strip-nav rcal-strip-nav--prev" data-rcal-scroll="-1" aria-label="Scroll left"><i class="fas fa-chevron-left"></i></button>
                    <div class="rcal-strip" tabindex="0">${entries.map((e) => this.stripCard(e)).join("")}</div>
                    <button type="button" class="rcal-strip-nav rcal-strip-nav--next" data-rcal-scroll="1" aria-label="Scroll right"><i class="fas fa-chevron-right"></i></button>
                </div>`;
            const track = this.root.querySelector(".rcal-strip");
            const update = () => {
                this.root.querySelector(".rcal-strip-nav--prev").disabled = track.scrollLeft <= 2;
                this.root.querySelector(".rcal-strip-nav--next").disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
            };
            track.addEventListener("scroll", update, { passive: true });
            // Mouse wheels scroll the row sideways instead of the page
            track.addEventListener("wheel", (event) => {
                if (Math.abs(event.deltaY) > Math.abs(event.deltaX) && track.scrollWidth > track.clientWidth) {
                    event.preventDefault();
                    track.scrollLeft += event.deltaY;
                }
            }, { passive: false });
            update();
        }

        async refresh(button) {
            button.disabled = true;
            const resp = await AA.apiFetch("/api/calendar/refresh", { method: "POST" });
            const ok = resp.ok && resp.data && resp.data.success;
            const message = ok ? "Refreshing the release calendar…" : (resp.data && resp.data.error) || "Couldn't start a refresh";
            if (typeof window.showNotification === "function") window.showNotification(message, ok ? "info" : "error");
            if (ok) setTimeout(() => this.load(), 10000);
            else button.disabled = false;
        }

        onClick(event) {
            const scroll = event.target.closest("[data-rcal-scroll]");
            if (scroll) {
                const track = this.root.querySelector(".rcal-strip");
                track.scrollBy({ left: Number(scroll.dataset.rcalScroll) * track.clientWidth * 0.8, behavior: "smooth" });
                return;
            }
            const add = event.target.closest("[data-rcal-add]");
            if (add) {
                this.addToLibrary(add);
                return;
            }
            const month = event.target.closest("[data-rcal-month]");
            if (month) {
                this.month.setMonth(this.month.getMonth() + Number(month.dataset.rcalMonth));
                this.renderMini();
                return;
            }
            const jump = event.target.closest("[data-rcal-jump]");
            if (jump) {
                this.jumpTo(jump.dataset.rcalJump);
                return;
            }
            const refresh = event.target.closest("[data-rcal-refresh]");
            if (refresh) {
                this.refresh(refresh);
                return;
            }
            const book = event.target.closest("[data-rcal-book]");
            if (book) {
                const entry = this.entries.find((e) => keyOf(e) === book.dataset.rcalBook);
                if (entry) panel.open(this, entry);
            }
        }

        async addToLibrary(button) {
            const entry = this.entries.find((e) => e.asin === button.dataset.rcalAdd);
            if (!entry) return;
            button.disabled = true;
            const resp = await AA.apiFetch("/search/add-book", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    asin: entry.asin, title: entry.title, author: entry.authors, narrator: entry.narrators,
                    series: entry.series || "N/A", sequence: entry.sequence || "", release_date: entry.release_date,
                    publisher: entry.publisher, language: entry.language, summary: entry.summary,
                    runtime: entry.runtime_min ? `${Math.floor(entry.runtime_min / 60)} hrs ${entry.runtime_min % 60} mins` : "",
                    cover_image: entry.cover_url,
                }),
            });
            const ok = resp.ok && resp.data && resp.data.success;
            const message = ok ? `Added ${entry.title} to your wanted list` : (resp.data && resp.data.error) || "Couldn't add the book";
            if (typeof window.showNotification === "function") window.showNotification(message, ok ? "success" : "error");
            if (!ok) {
                button.disabled = false;
                return;
            }
            entry.library_status = "wanted";
            document.querySelectorAll(`[data-rcal-add="${CSS.escape(entry.asin)}"]`).forEach((el) => {
                el.replaceWith(Object.assign(document.createElement("span"), {
                    className: "rcal-badge rcal-badge--wanted", textContent: "Wanted",
                }));
            });
            if (this.strip) this.render();
            else {
                const stats = this.root.querySelector(".rcal-stats");
                if (stats) stats.innerHTML = this.statsHtml();
                this.renderTimeline();
                this.renderMini();
            }
            if (panel.el && panel.el.classList.contains("is-open") && panel.owner === this) panel.open(this, entry);
        }
    }

    window.ReleaseCalendar = {
        mountAll() {
            document.querySelectorAll("[data-release-calendar]").forEach((el) => {
                if (!el._releaseCalendar) el._releaseCalendar = new ReleaseCalendar(el);
            });
        },
        reload(el) {
            if (el && el._releaseCalendar) el._releaseCalendar.load();
        },
    };
})();
