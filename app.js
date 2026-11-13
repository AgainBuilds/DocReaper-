
const API = {
    overview: "/api/overview",
    books: "/api/books"
};

const elements = {
    books: document.getElementById("books-count"),
    target: document.getElementById("target-count"),
    pending: document.getElementById("pending-count"),
    duplicates: document.getElementById("duplicate-count"),
    progress: document.getElementById("progress-number"),
    progressFill: document.getElementById("progress-fill"),
    progressBooks: document.getElementById("progress-books"),
    progressTarget: document.getElementById("progress-target"),
    kept: document.getElementById("kept-count"),
    failed: document.getElementById("failed-count"),
    table: document.getElementById("books-table"),
    updated: document.getElementById("last-updated"),
    refresh: document.getElementById("refresh-button")
};

function formatNumber(value) {
    return Number(value || 0).toLocaleString();
}

function safeText(value) {
    return String(value ?? "—").replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
    })[character]);
}

async function loadOverview() {
    const response = await fetch(API.overview);

    if (!response.ok) {
        throw new Error("Could not load collection statistics.");
    }

    const data = await response.json();

    elements.books.textContent = formatNumber(data.books);
    elements.target.textContent = formatNumber(data.target);
    elements.pending.textContent = formatNumber(data.pending);
    elements.duplicates.textContent = formatNumber(data.duplicates);

    elements.progress.textContent = `${data.progress_percent}%`;
    elements.progressFill.style.width = `${data.progress_percent}%`;

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
        throw new Error("Could not load the book catalogue.");
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
    elements.refresh.disabled = true;
    elements.refresh.textContent = "Refreshing...";

    try {
        await Promise.all([
            loadOverview(),
            loadBooks()
        ]);

        elements.updated.textContent =
            `Updated ${new Date().toLocaleTimeString()}`;

    } catch (error) {
        console.error(error);
        elements.updated.textContent = "Unable to retrieve database data.";
    } finally {
        elements.refresh.disabled = false;
        elements.refresh.textContent = "↻ Refresh data";
    }
}

elements.refresh.addEventListener("click", refreshDashboard);

refreshDashboard();
