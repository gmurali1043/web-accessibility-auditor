import xml.etree.ElementTree as ET
from collections import deque
from urllib.parse import urlparse, urldefrag, urljoin

import requests
from playwright.sync_api import sync_playwright

from . import config, netguard
from .browser import launch_browser

USER_AGENT = "Mozilla/5.0 (compatible; WebAccessibilityAuditor/1.0; +local-tool)"
NAV_TIMEOUT_MS = 15000
NETWORK_IDLE_TIMEOUT_MS = 4000
REQUEST_TIMEOUT = 8

MAX_SITEMAP_FILES = 25
MAX_SITEMAP_URLS = 500

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

NON_HTML_EXT = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".mp4", ".mp3", ".zip", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".woff", ".woff2", ".ttf", ".eot", ".json", ".xml", ".rss",
)

# Common two-part public suffixes where the registrable domain needs three
# labels (e.g. "corporate.example.co.uk" -> "example.co.uk", not "co.uk").
# Not exhaustive (no full Public Suffix List), but covers the common cases
# well enough for a same-site heuristic.
MULTIPART_SUFFIXES = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "co.in", "co.jp", "co.nz",
    "co.za", "com.au", "com.br", "com.mx", "com.sg", "com.hk", "com.cn",
}


def _registrable_domain(netloc):
    """Best-effort eTLD+1 extraction so subdomains of the same brand
    (e.g. corporate.example.com and www.example.com) are treated as the
    same site during a crawl."""
    host = netloc.split(":")[0].lower()
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last_two = ".".join(parts[-2:])
    if last_two in MULTIPART_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last_two


def _same_site(base_netloc, candidate_netloc):
    return _registrable_domain(base_netloc) == _registrable_domain(candidate_netloc)


def _normalize(url):
    url, _ = urldefrag(url)
    if url.endswith("/") and url.count("/") > 2:
        url = url[:-1]
    return url


def _is_html_candidate(url, base_netloc):
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    if not _same_site(base_netloc, p.netloc):
        return False
    if url.lower().endswith(NON_HTML_EXT):
        return False
    return True


# ---------------------------------------------------------------------------
# robots.txt: Sitemap: directives + a simplified Disallow parser
# ---------------------------------------------------------------------------

def _fetch_text(url):
    proxies = None
    if config.PUBLIC_MODE:
        proxy = netguard.proxy_url()
        proxies = {"http": proxy, "https": proxy}
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT},
                            proxies=proxies)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def _parse_robots(robots_text):
    """Returns (sitemap_urls, disallow_paths) for the '*' user-agent group.
    A small, spec-lax parser: good enough to be polite, not a full RFC 9309
    implementation (no wildcard/$ matching, no per-agent overrides beyond '*')."""
    sitemap_urls = []
    groups = []  # [(set(agents), [disallow paths])]
    current_agents, current_disallows = [], []

    def flush():
        if current_agents:
            groups.append((set(current_agents), list(current_disallows)))

    for raw_line in (robots_text or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "sitemap" and value:
            sitemap_urls.append(value)
        elif key == "user-agent":
            if current_disallows:
                flush()
                current_agents, current_disallows = [], []
            current_agents.append(value.lower())
        elif key == "disallow":
            current_disallows.append(value)
    flush()

    disallow_paths = []
    for agents, disallows in groups:
        if "*" in agents:
            disallow_paths.extend(d for d in disallows if d)
    return sitemap_urls, disallow_paths


def _is_disallowed(url, disallow_paths):
    path = urlparse(url).path or "/"
    for prefix in disallow_paths:
        if prefix == "/":
            return True
        if prefix and path.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# sitemap.xml (and nested sitemap indexes)
# ---------------------------------------------------------------------------

def _parse_sitemap_xml(xml_text):
    """Returns (page_urls, nested_sitemap_urls)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []
    tag = root.tag.lower()
    if tag.endswith("sitemapindex"):
        nested = [el.text.strip() for el in root.findall(".//sm:sitemap/sm:loc", SITEMAP_NS) if el.text]
        if not nested:
            nested = [el.text.strip() for el in root.findall(".//sitemap/loc") if el.text]
        return [], nested
    pages = [el.text.strip() for el in root.findall(".//sm:url/sm:loc", SITEMAP_NS) if el.text]
    if not pages:
        pages = [el.text.strip() for el in root.findall(".//url/loc") if el.text]
    return pages, []


def discover_sitemap_urls(start_url, base_netloc):
    """Full-site-index discovery: follows robots.txt Sitemap: directives
    (falling back to the conventional /sitemap.xml and /sitemap_index.xml
    locations), recursively expands sitemap indexes, and returns every
    same-site page URL declared - capped at MAX_SITEMAP_FILES sitemap
    documents / MAX_SITEMAP_URLS page URLs. Also returns the robots.txt
    Disallow list so callers can honor it. Never raises; returns
    ([], []) on any failure (no sitemap, unreachable robots.txt, etc.)."""
    robots_text = _fetch_text(urljoin(start_url, "/robots.txt"))
    sitemap_candidates, disallow_paths = _parse_robots(robots_text) if robots_text else ([], [])
    if not sitemap_candidates:
        sitemap_candidates = [
            urljoin(start_url, "/sitemap.xml"),
            urljoin(start_url, "/sitemap_index.xml"),
        ]

    queue = deque(sitemap_candidates)
    seen_sitemaps = set()
    found = []
    seen_found = set()
    files_fetched = 0

    while queue and files_fetched < MAX_SITEMAP_FILES and len(found) < MAX_SITEMAP_URLS:
        sm_url = queue.popleft()
        norm = _normalize(sm_url)
        if norm in seen_sitemaps:
            continue
        seen_sitemaps.add(norm)
        text = _fetch_text(sm_url)
        files_fetched += 1
        if not text:
            continue
        pages, nested = _parse_sitemap_xml(text)
        for p in pages:
            p_norm = _normalize(p)
            if not _is_html_candidate(p_norm, base_netloc):
                continue
            if _is_disallowed(p_norm, disallow_paths):
                continue
            if p_norm not in seen_found:
                seen_found.add(p_norm)
                found.append(p_norm)
            if len(found) >= MAX_SITEMAP_URLS:
                break
        for n in nested:
            n_norm = _normalize(n)
            if n_norm not in seen_sitemaps:
                queue.append(n)

    return found, disallow_paths


# ---------------------------------------------------------------------------
# Main crawl: sitemap-seeded + browser-rendered breadth-first link crawl
# ---------------------------------------------------------------------------

def discover_pages(start_url, max_pages=40, max_depth=3, use_sitemap=True):
    """Discovers pages on a site two ways, combined:

    1. Full site index (when use_sitemap=True): reads robots.txt and any
       sitemap.xml/sitemap index it declares (or the conventional default
       locations) to seed every page the site itself lists, honoring
       robots.txt Disallow rules. These seeds are exempt from max_depth -
       they're the site's own declared index, not link-derived.
    2. Breadth-first link crawl: each visited page is rendered in a real
       headless browser (so JS-rendered navigation is found, not just
       links in the initial HTML) and its same-site links are queued for
       the next depth.

    Both share the same max_pages cap and the same robots.txt Disallow
    filter. Read-only throughout: only ever navigates via GET, never
    clicks or submits anything. Returns
    {"pages": [{"url", "title"}], "errors": [...], "sitemap_seed_count": int}."""
    if not start_url.startswith(("http://", "https://")):
        start_url = "https://" + start_url

    start_url = _normalize(start_url)
    base_netloc = urlparse(start_url).netloc

    disallow_paths = []
    sitemap_seeds = []
    if use_sitemap:
        try:
            sitemap_seeds, disallow_paths = discover_sitemap_urls(start_url, base_netloc)
        except Exception:
            sitemap_seeds, disallow_paths = [], []
    if not disallow_paths:
        robots_text = _fetch_text(urljoin(start_url, "/robots.txt"))
        if robots_text:
            _, disallow_paths = _parse_robots(robots_text)

    visited = set()
    final_visited = set()  # dedupes by the *post-redirect* URL - several
    # sitemap/link entries can point to different paths that all resolve to
    # the same canonical page (locale prefixes, short-link aliases, etc.)
    queued = {start_url}
    queue = deque([(start_url, 0)])
    sitemap_seed_set = set()
    for u in sitemap_seeds:
        if u not in queued and not _is_disallowed(u, disallow_paths):
            queued.add(u)
            sitemap_seed_set.add(u)
            queue.append((u, 0))

    pages = []
    errors = []

    with sync_playwright() as p:
        browser = launch_browser(p)
        context = browser.new_context(
            user_agent=USER_AGENT, viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        try:
            while queue and len(pages) < max_pages:
                url, depth = queue.popleft()
                if url in visited:
                    continue
                visited.add(url)

                page = context.new_page()
                try:
                    resp = page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
                    try:
                        page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
                    except Exception:
                        pass
                except Exception as e:
                    errors.append({"url": url, "error": str(e)})
                    page.close()
                    continue

                status = resp.status if resp else None
                content_type = resp.headers.get("content-type", "") if resp else ""
                if resp and resp.headers.get(netguard.BLOCKED_HEADER.lower()):
                    errors.append({"url": url, "error": netguard.BLOCKED_MESSAGE})
                    page.close()
                    continue
                if status and status >= 400:
                    errors.append({"url": url, "error": f"HTTP {status}"})
                    page.close()
                    continue
                if content_type and "text/html" not in content_type:
                    page.close()
                    continue

                final_url = _normalize(page.url)
                if final_url in final_visited:
                    # This URL redirected to a page we've already recorded
                    # under a different alias - don't audit the same
                    # rendered page twice under two "different" entries.
                    page.close()
                    continue
                final_visited.add(final_url)
                queued.add(final_url)

                title = (page.title() or "").strip() or final_url
                pages.append({"url": final_url, "title": title, "source": "sitemap" if url in sitemap_seed_set else "link"})

                if depth < max_depth:
                    try:
                        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                    except Exception:
                        hrefs = []
                    for href in hrefs:
                        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                            continue
                        candidate = _normalize(href)
                        if not _is_html_candidate(candidate, base_netloc):
                            continue
                        if _is_disallowed(candidate, disallow_paths):
                            continue
                        if candidate not in queued:
                            queued.add(candidate)
                            queue.append((candidate, depth + 1))

                page.close()
        finally:
            browser.close()

    return {
        "pages": pages,
        "errors": errors,
        "sitemap_seed_count": len(sitemap_seed_set),
    }
