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
    ("matrix", "Матрица сравнений"),
)
WORKSPACE_STYLE = """
html.catalog-workspace{scrollbar-gutter:stable}
.catalog-workspace main{box-sizing:border-box;width:100%;max-width:none}
.catalog-workspace main > section{min-width:0}
.catalog-workspace .workspace-header{display:block}
.workspace-tabs{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px;margin:0 0 18px}
.workspace-tabs a{display:flex;align-items:center;justify-content:center;text-align:center;
  padding:9px 8px;color:#577082;border-bottom:3px solid transparent;text-decoration:none;font-weight:600}
.workspace-tabs a[aria-current=page]{border-color:#06a0da;color:#007daf}
.workspace-tabs a:hover,.workspace-tabs a:focus-visible{color:#007daf;background:#edf5fa}
@media(max-width:700px){.workspace-tabs{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""
WORKSPACE_HEADER = (
    '<header class="workspace-header"><h1>PitON '
    '<span style="color:#079cd0">Catalog Intelligence</span></h1></header>'
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


def workspace_shell(text, active):
    links = []
    for key, label in WORKSPACE_TABS:
        href = "?view=matrix" if key == "matrix" else f"?workspace={key}"
        current = ' aria-current="page"' if key == active else ""
        local_tab = f' data-tab="{key}"' if active != "matrix" and key != "matrix" else ""
        links.append(f'<a href="{href}"{current}{local_tab}>{label}</a>')
    nav = '<nav class="workspace-tabs" aria-label="Разделы каталога">' + "".join(links) + "</nav>"
    text = text.replace('<html lang="ru">', '<html lang="ru" class="catalog-workspace">', 1)
    text = text.replace("</style>", WORKSPACE_STYLE + "</style>", 1)
    text = re.sub(r"<header\b[^>]*>.*?</header>", lambda _: WORKSPACE_HEADER, text, count=1, flags=re.S)
    return re.sub(r"<nav\b[^>]*>.*?</nav>", lambda _: nav, text, count=1, flags=re.S)


def public_html(handler, body):
    text = body.decode("utf-8")
    path = urlsplit(handler.path).path
    if path == "/":
        text = text.replace('href="/api/', 'href="/catalog/api/').replace('src="/ui.js"', 'src="/catalog/ui.js"')
        key = handler.params().get("workspace", ["compare"])[0]
        active = key if any(key == tab and tab != "matrix" for tab, _ in WORKSPACE_TABS) else "compare"
        return workspace_shell(text, active).encode()
    if path == "/matrix-v7":
        text = text.replace('action="/matrix-v7"', 'action="/catalog/"').replace('href="/api/matrix-v7.', 'href="/catalog/api/matrix-v7.')
        text = text.replace('action="/catalog/">', 'action="/catalog/"><input type="hidden" name="view" value="matrix">', 1)
        view = handler.params().get("view", ["matrix"])[0]
        if handler.catalog_admin and view not in VIEWS - {"matrix"}:
            text = workspace_shell(text, "matrix")
        return text.encode()
    return body
