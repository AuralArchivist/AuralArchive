// The server pushes 'notifications:updated' over SocketIO, so the count stays
// live without polling; the slow poll is only a fallback.
(function () {
    "use strict";

    const dropdown = document.getElementById("notifDropdown");
    if (!dropdown) {
        return;
    }

    const API = "/api/notifications";
    const button = document.getElementById("notifButton");
    const countEl = document.getElementById("notifCount");
    const listEl = document.getElementById("notifList");
    const emptyEl = document.getElementById("notifEmpty");
    const esc = (value) => AA.escapeHtml(value == null ? "" : String(value));

    const ICONS = {
        book_missing: "fa-book-skull",
        book_released: "fa-gift",
        series_new_title: "fa-layer-group",
        library_review: "fa-folder-open",
        library_check_blocked: "fa-triangle-exclamation",
        download_found: "fa-magnifying-glass",
        download_imported: "fa-circle-check",
        download_failed: "fa-circle-xmark",
        update_available: "fa-arrow-up-right-from-square",
        audible_auth: "fa-key",
        storage: "fa-hard-drive",
        metadata_failures: "fa-rotate",
        author_new_titles: "fa-user-pen",
        release_calendar: "fa-calendar-days",
        next_in_series: "fa-forward",
        audiobookshelf: "fa-headphones",
    };
    const LEVEL_CLASS = { warning: "is-warning", error: "is-error", success: "is-success", info: "is-info" };
    const POLL_MS = 120000;

    function setCount(unread) {
        const n = Number(unread) || 0;
        countEl.textContent = n > 99 ? "99+" : String(n);
        countEl.classList.toggle("hidden", n === 0);
        button.setAttribute("aria-label", n ? `Notifications (${n} unread)` : "Notifications");
    }

    function timeAgo(value) {
        if (!value) {
            return "";
        }
        const stamp = new Date(String(value).replace(" ", "T") + (String(value).includes("Z") ? "" : "Z"));
        const mins = Math.max(0, Math.round((Date.now() - stamp.getTime()) / 60000));
        if (mins < 1) return "just now";
        if (mins < 60) return `${mins} min ago`;
        if (mins < 1440) return `${Math.round(mins / 60)} h ago`;
        const days = Math.round(mins / 1440);
        return `${days} day${days === 1 ? "" : "s"} ago`;
    }

    function render(items) {
        emptyEl.classList.toggle("hidden", items.length > 0);
        listEl.innerHTML = items.map((item) => `
            <li class="aa-notif-item ${item.read_at ? "" : "is-unread"} ${LEVEL_CLASS[item.level] || "is-info"}" data-id="${esc(item.id)}">
                <i class="fas ${ICONS[item.type] || "fa-bell"} aa-notif-icon" aria-hidden="true"></i>
                <a class="aa-notif-body" href="${esc(item.link || "#")}" data-action="open">
                    <span class="aa-notif-title">${esc(item.title)}</span>
                    ${item.message ? `<span class="aa-notif-msg">${esc(item.message)}</span>` : ""}
                    <span class="aa-notif-time">${esc(timeAgo(item.created_at))}</span>
                </a>
                <button type="button" class="btn btn-ghost btn-xs btn-square" data-action="dismiss" aria-label="Dismiss">
                    <i class="fas fa-xmark" aria-hidden="true"></i>
                </button>
            </li>`).join("");
    }

    async function call(url, method) {
        const { ok, data } = await AA.apiFetch(url, { method: method || "GET" });
        if (ok && data && data.success !== false) {
            setCount(data.unread);
            return data;
        }
        return null;
    }

    async function loadList() {
        const data = await call(API);
        if (data) {
            render(data.items || []);
        }
    }

    function isOpen() {
        return dropdown.contains(document.activeElement);
    }

    button.addEventListener("focus", loadList);
    button.addEventListener("click", loadList);

    listEl.addEventListener("click", async (event) => {
        const target = event.target.closest("[data-action]");
        const row = event.target.closest("[data-id]");
        if (!target || !row) {
            return;
        }
        const id = row.dataset.id;
        if (target.dataset.action === "dismiss") {
            event.preventDefault();
            row.remove();
            await call(`${API}/${id}/dismiss`, "POST");
            emptyEl.classList.toggle("hidden", listEl.children.length > 0);
            return;
        }
        const href = target.getAttribute("href");
        event.preventDefault();
        await call(`${API}/${id}/read`, "POST");
        if (href && /^https?:\/\//i.test(href)) {
            window.open(href, "_blank", "noopener");
            loadList();
        } else if (href && href !== "#") {
            window.location.href = href;
        } else {
            loadList();
        }
    });

    document.getElementById("notifReadAll").addEventListener("click", async () => {
        await call(`${API}/read-all`, "POST");
        loadList();
    });
    document.getElementById("notifClearAll").addEventListener("click", async () => {
        await call(`${API}/dismiss-all`, "POST");
        render([]);
    });

    const socket = window._appSocket || null;
    if (socket) {
        socket.on("notifications:updated", (payload) => {
            setCount(payload && payload.unread);
            if (isOpen()) {
                loadList();
            }
        });
    }
    window.setInterval(() => {
        if (!document.hidden) {
            call(`${API}?limit=1`);
        }
    }, POLL_MS);
})();
