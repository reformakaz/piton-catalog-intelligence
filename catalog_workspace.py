"""Expose the loopback catalog workspace through its authenticated HTTPS mount."""

import re
from urllib.parse import parse_qs, urlsplit

MOUNT = "/catalog/"
ORIGIN = "https://ai.reforma.kz"
ADMIN_HEADER = "X-Piton-Catalog-User"
VIEWS = {"matrix", "card", "catalog", "series", "quote-comparison"}
WORKSPACE_TABS = (
    ("compare", "Сравнение"),
    ("library", "Библиотека каталогов"),
    ("importer", "Импорт характеристик"),
    ("extractor", "Извлечение из PDF"),
)
READ = {
    "ui.js", "api/library", "api/catalog-types", "api/spec-template",
    "api/scan-v6", "api/scan-v5", "api/extract-v4",
}
WRITE = {"api/upload", "api/catalog-type", "api/spec-import", "api/report"}
FILES = re.compile(r"api/(?:pdf/[a-zA-Z0-9_-]+|reports/comparison_(?:Cu|Al)_[0-9]{3,5}\.(?:html|csv|json))")


def public_route(handler):
    """Map allowed public requests to their original loopback routes."""
    url = urlsplit(handler.path)
    if not url.path.startswith(MOUNT):
        return True
    handler.catalog_public = True
    handler.catalog_admin = handler.headers.get_all(ADMIN_HEADER) == ["admin"]
    suffix = url.path[len(MOUNT):]
    query = parse_qs(url.query)
    if handler.command == "POST":
        if not handler.catalog_admin or handler.headers.get_all("Origin") != [ORIGIN]:
            handler.fail(403, "Доступ только администратору каталога с этой страницы")
            return False
        if suffix not in WRITE:
            handler.fail(405, "Запись по этому адресу недоступна")
            return False
        path = "/" + suffix
    elif not suffix:
        view = query.get("view", [""])[0]
        show_matrix = not handler.catalog_admin or view in VIEWS or "material" in query
        path = "/matrix-v7" if show_matrix else "/"
    else:
        if suffix not in READ and not FILES.fullmatch(suffix):
            handler.fail(404, "Не найдено")
            return False
        if not handler.catalog_admin:
            handler.fail(403, "Доступ только администратору каталога")
            return False
        path = "/" + suffix
    handler.path = path + ("?" + url.query if url.query else "")
    return True


def workspace_links():
    links = []
    for key, label in WORKSPACE_TABS:
        links.append(f'<a href="?workspace={key}">{label}</a>')
    return "".join(links)


def public_html(handler, body):
    text = body.decode("utf-8")
    path = urlsplit(handler.path).path
    if path == "/":
        text = text.replace('<span class="badge">Локальный доступ через SSH</span>', "")
        text = text.replace('href="/api/', 'href="/catalog/api/').replace('src="/ui.js"', 'src="/catalog/ui.js"')
        matrix = '<a href="?view=matrix" class="workspace-matrix">Матрица сравнений</a>'
        return text.replace("</nav>", matrix + "</nav>", 1).encode()
    if path == "/matrix-v7":
        text = text.replace('action="/matrix-v7"', 'action="/catalog/"').replace('href="/api/matrix-v7.', 'href="/catalog/api/matrix-v7.')
        text = text.replace('action="/catalog/">', 'action="/catalog/"><input type="hidden" name="view" value="matrix">', 1)
        if handler.catalog_admin:
            text = re.sub(r'(<nav class="catalog-tabs[^>]*>)', lambda m: m[1] + workspace_links(), text, count=1)
        return text.encode()
    return body
