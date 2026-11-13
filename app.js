
const API = {
    overview: "/api/overview",
    books: "/api/books",
    status: "/api/control/status",
    logs: "/api/logs",
    start: "/api/control/start",
    pause: "/api/control/pause",
    resume: "/api/control/resume",
    stop: "/api/control/stop"
};

const $ = id => document.getElementById(id);

let lastLogId = 0;
let totalLogCount = 0;
let displayedLogs = [];
let terminalClearedAt = 0;
let controlToken = sessionStorage.getItem("docreaper_token") || "";

const elements = {
    books: $("books-count"),
    target: $("target-count"),
    pending: $("pending-count"),
    duplicates: $("duplicate-count"),
    progress: $("progress-number"),
    progressFill: $("progress-fill"),
    progressBooks: $("progress-books"),
    progressTarget: $("progress-target"),
    kept: $("kept-count"),
    failed: $("failed-count"),
    table: $("books-table"),
    updated: $("last-updated"),
    refresh: $("refresh-button"),

    token: $("control-token"),
    saveToken: $("save-token"),
    status: $("engine-status"),
    start: $("start-engine"),
    pause: $("pause-engine"),
    resume: $("resume-engine"),
    stop: $("stop-engine"),

    engineBooks: $("engine-books"),
    enginePending: $("engine-pending"),
    engineTarget: $("engine-target"),

    terminal: $("terminal-output"),
    connection: $("terminal-connection"),
    filter: $("log-filter"),
    autoScroll: $("auto-scroll"),
    clearTerminal: $("clear-terminal"),
    logCount: $("terminal-log-count")
};

function formatNumber(value) {
    return Number(value || 0).toLocaleString();
}

function safeText(value) {
    return String(value ?? "—").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
    })[char]);
}

async function apiRequest(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            ...(options.headers || {}),
            ...(controlToken
                ? {"X-Control-Token": controlToken}
                : {})
        }
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(data.detail || "Request failed.");
    }

    return data;
}

async function loadOverview() {
    const response = await fetch(API.overview);

    if (!response.ok) {
        throw new Error("Could not load statistics.");
    }

    const data = await response.json();

    elements.books.textContent = formatNumber(data.books);
    elements.target.textContent = formatNumber(data.target);
    elements.pending.textContent = formatNumber(data.pending);
    elements.duplicates.textContent = formatNumber(data.duplicates);

    elements.progress.textContent =
        `${data.progress_percent}%`;

    elements.progressFill.style.width =
        `${data.progress_percent}%`;

    elements.progressBooks.textContent =
        `${formatNumber(data.books)} books collected`;

    elements.progressTarget.textContent =
        `Target: ${formatNumber(data.target)}`;

    elements.kept.textContent = formatNumber(data.kept);
    elements.failed.textContent = formatNumber(data.failed);
}

async function loadBooks() {
    const response = await fetch(API.books);

    if (!response.ok) {
        throw new Error("Could not load catalogue.");
    }

    const data = await response.json();
    const books = data.books || [];

    if (!books.length) {
        elements.table.innerHTML = `
            <tr>
                <td colspan="5" class="empty-state">
                    No books have been catalogued yet.
                </td>
            </tr>
        `;
        return;
    }

    elements.table.innerHTML = books.map(book => `
        <tr>
            <td>${safeText(book.title)}</td>
            <td>${safeText(book.authors)}</td>
            <td>${safeText(book.subject)}</td>
            <td>${safeText(book.fmt).toUpperCase()}</td>
            <td>${safeText(book.year)}</td>
        </tr>
    `).join("");
}

async function refreshDashboard() {
    try {
        await Promise.all([
            loadOverview(),
            loadBooks()
        ]);

        elements.updated.textContent =
            `Updated ${new Date().toLocaleTimeString()}`;

    } catch (error) {
        elements.updated.textContent = error.message;
    }
}

function setEngineStatus(status) {
    const normalized = String(status || "idle").toLowerCase();

    elements.status.textContent = normalized.toUpperCase();

    const running = normalized === "running";
    const paused = normalized === "paused";
    const stopping = normalized === "stopping";
    const active = running || paused || stopping;

    elements.start.disabled = active;
    elements.pause.disabled = !running;
    elements.resume.disabled = !paused;
    elements.stop.disabled = !running && !paused;
}

async function refreshEngineStatus() {
    try {
        const data = await apiRequest(API.status);

        setEngineStatus(data.status);

        elements.engineBooks.textContent =
            formatNumber(data.books);

        elements.enginePending.textContent =
            formatNumber(data.pending);

        elements.engineTarget.textContent =
            formatNumber(data.target);

        elements.connection.textContent = "CONNECTED";

    } catch (error) {
        elements.connection.textContent = "CONNECTION ERROR";
        console.error(error);
    }
}

function addLog(entry) {
    displayedLogs.push(entry);

    const filter = elements.filter.value;

    if (
        filter !== "ALL" &&
        entry.level !== filter
    ) {
        return;
    }

    const line = document.createElement("div");

    line.className =
        `terminal-line ${entry.level.toLowerCase()}`;

    line.textContent =
        `[${new Date(entry.time).toLocaleTimeString()}] ` +
        `[${entry.level}] ${entry.message}`;

    elements.terminal.appendChild(line);

    if (elements.autoScroll.checked) {
        elements.terminal.scrollTop =
            elements.terminal.scrollHeight;
    }

    elements.logCount.textContent =
        `${displayedLogs.length} EVENTS`;
}

async function fetchLogs() {
    try {
        const response = await fetch(
            `${API.logs}?after=${lastLogId}&limit=250`
        );

        if (!response.ok) {
            throw new Error("Log feed unavailable");
        }

        const data = await response.json();

        for (const entry of data.logs || []) {
            lastLogId = Math.max(lastLogId, entry.id);

            if (entry.id > terminalClearedAt) {
                addLog(entry);
            }
        }

        elements.connection.textContent = "CONNECTED";

    } catch (error) {
        elements.connection.textContent = "RECONNECTING";
    }
}

function redrawTerminal() {
    elements.terminal.innerHTML = "";

    const filter = elements.filter.value;

    const filtered = displayedLogs.filter(entry =>
        filter === "ALL" || entry.level === filter
    );

    for (const entry of filtered) {
        const line = document.createElement("div");

        line.className =
            `terminal-line ${entry.level.toLowerCase()}`;

        line.textContent =
            `[${new Date(entry.time).toLocaleTimeString()}] ` +
            `[${entry.level}] ${entry.message}`;

        elements.terminal.appendChild(line);
    }

    if (elements.autoScroll.checked) {
        elements.terminal.scrollTop =
            elements.terminal.scrollHeight;
    }
}

async function controlAction(action) {
    try {
        if (!controlToken) {
            throw new Error("Enter and unlock your control token first.");
        }

        const result = await apiRequest(
            API[action],
            {method: "POST"}
        );

        addLog({
            id: -Date.now(),
            time: new Date().toISOString(),
            level: "INFO",
            message: `Dashboard command accepted: ${action.toUpperCase()}`
        });

        setEngineStatus(result.status);

        await refreshEngineStatus();

    } catch (error) {
        alert(error.message);
    }
}

elements.saveToken.addEventListener("click", () => {
    const value = elements.token.value.trim();

    if (!value) {
        alert("Enter your control token.");
        return;
    }

    controlToken = value;
    sessionStorage.setItem("docreaper_token", value);

    elements.token.value = "";

    alert("Control token saved for this browser session.");
});

elements.start.addEventListener("click", () =>
    controlAction("start")
);

elements.pause.addEventListener("click", () =>
    controlAction("pause")
);

elements.resume.addEventListener("click", () =>
    controlAction("resume")
);

elements.stop.addEventListener("click", () => {
    if (confirm("Stop the current collection run?")) {
        controlAction("stop");
    }
});

elements.filter.addEventListener("change", redrawTerminal);

elements.clearTerminal.addEventListener("click", () => {
    terminalClearedAt = lastLogId;
    displayedLogs = [];
    elements.terminal.innerHTML = "";
    elements.logCount.textContent = "0 EVENTS";
});

elements.refresh.addEventListener("click", refreshDashboard);

async function initialize() {
    await refreshDashboard();
    await refreshEngineStatus();
    await fetchLogs();

    setInterval(refreshDashboard, 5000);
    setInterval(refreshEngineStatus, 2500);
    setInterval(fetchLogs, 1000);
}

initialize();
