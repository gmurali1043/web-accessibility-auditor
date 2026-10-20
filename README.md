# Web Accessibility Auditor

A local, offline-capable web accessibility testing tool built around
**axe-core v4.13.0** (vendored, no CDN) and **Playwright**, mapped to a
104-item VGAR-style Web accessibility test script (`data/test_cases.json`,
generated from `Accessibility Web test cases.xlsx`).

Every one of the 104 test cases is classified as one of:

- **Automated** (14 cases) — a deterministic axe-core rule or DOM check
  fully determines pass/fail.
- **Partially Automated** (66 cases) — axe-core and/or a custom Playwright
  heuristic surfaces concrete evidence when something is wrong, but a clean
  scan doesn't *prove* compliance (e.g. requires visual/AT confirmation), so
  it's reported as "Manual Review Required" rather than a false Pass.
- **Manual Review Required** (24 cases) — requires live assistive technology,
  a physical device, or business-process knowledge the tool cannot observe
  (e.g. actual screen reader announcements, session timeout behavior, drag
  gestures). These are always flagged for manual review.

See `checker/registry.py` / `data/test_cases.json` for the full mapping and
the `method` field on each test case for exactly how (or why not) it's
automated. The same table is browsable from the app's footer link.

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```bash
python app.py
```

Then open http://localhost:8060 in your browser. Everything runs locally —
no data leaves your machine except the actual HTTP requests to the page(s)
you choose to audit.

## Using the tool

- **URL Audit tab** — paste one or more URLs (up to 25) and run a full audit.
  Results are grouped per page with a page conformance score and an overall
  batch score. Auditing 2+ pages together also runs cross-page checks (title
  uniqueness, consistent navigation order/labels, consistent context cues).
- **Crawl a Site tab** — enter a domain, discover its pages, select which
  ones to audit from the results, then run the same audit on your
  selection. Discovery combines two sources (read-only throughout — it only
  navigates via GET, never clicks or submits anything):
  - **Full site index** (on by default): reads `robots.txt` for `Sitemap:`
    directives (falling back to the conventional `/sitemap.xml` /
    `/sitemap_index.xml` locations), recursively expands sitemap indexes,
    and seeds every same-site page the site itself declares — these aren't
    subject to Max depth, since they're the site's own index rather than
    something derived by following links.
  - **Link crawl**: each visited page is rendered in a real headless
    browser (so JavaScript-rendered navigation is found, not just links
    present in the initial HTML) and its same-site links are queued for
    the next depth, breadth-first, up to Max depth.
  Both sources honor `robots.txt` Disallow rules and share the same Max
  pages cap. The picker shows which pages came from the sitemap vs. link
  discovery.
- **Downloads** — every batch (single URL or crawled selection) can be
  exported as `.html`, `.md`, or `.xlsx`. The Excel workbook has a Summary
  tab (page list + scores, hyperlinked to each page's tab) and one detail
  tab per page listing every issue with its DOM location, a code snippet,
  and a recommended fix.

## How scoring works

For each page, every test case resolves to `PASS`, `FAIL`, or `REVIEW`
(manual review required):

- `Automated` tier cases: `PASS` if no issues found, `FAIL` if the axe-core
  rule(s)/DOM check found violations.
- `Partial` tier cases: `FAIL` if the available automated evidence found a
  problem, otherwise `REVIEW` (absence of automated evidence is not proof of
  compliance for these).
- `Manual` tier cases: always `REVIEW`.

**Conformance score** = `PASS / (PASS + FAIL)` among the cases that could be
automatically determined, expressed as a percentage. Cases flagged for
manual review are excluded from the score's denominator but are always
listed in the report so nothing silently disappears from the count.

## Architecture

```
app.py                  Flask app / API routes
checker/
  registry.py            Loads data/test_cases.json
  axe_runner.py           Injects vendored axe-core, runs a full scan
  custom_checks.py        ~45 Playwright/DOM heuristic checks, including
                           keyboard-driven widget checks run behind a guard
                           that blocks navigation and non-GET requests, so
                           it's safe to run against live third-party sites
  crawler.py               Same-site link crawler (Playwright, renders JS)
  report.py                 Maps axe + custom-check findings onto the 104
                             test cases, computes scores, batch/cross-page
                             checks
  exporters.py               HTML / Markdown / XLSX report generation
data/test_cases.json    The 104 classified test cases
vendor/axe.min.js       axe-core v4.13.0 (vendored, MPL-2.0)
templates/, static/     Frontend (vanilla HTML/CSS/JS, no build step)
```

## Notes & limitations

- Custom checks never click arbitrary buttons or submit forms. Most only
  read the DOM, resize the viewport, inject inert CSS, and press `Tab`.
- **Interactive widget checks** go one step further, on controls that
  declare themselves as toggles:
  - **Accordions / show-hide controls** (`aria-expanded` or
    `aria-controls`): pressed with Enter, then Space, to confirm they open
    from the keyboard and that `aria-expanded` flips to match whether the
    panel is actually shown. Each one is toggled back afterwards.
  - **Menus / submenus** (`aria-haspopup`, or `aria-expanded` inside
    nav/header): opened with Enter / Space / Arrow Down to confirm focus can
    reach the submenu's items; `role="menu"` submenus must also take focus
    on open, close on Escape, and return focus to their trigger.
  - **Hover-only submenus** (nav `li` with a nested list and no ARIA): the
    item is hovered with the mouse, then focused with the keyboard, to
    confirm the submenu also appears (and is reachable) on focus.
  - **Video**: native `<video>` elements with no declared caption track are
    played muted for ~1.5s so JavaScript players can expose caption tracks;
    embedded YouTube players are started muted and stopped again to read
    their caption list, and Vimeo players are read directly. Embeds whose
    caption state can't be read stay in manual review rather than failing.

  While the accordion and menu checks run, every top-level navigation, new
  window and non-GET request is blocked, so pressing Enter on a toggle
  can't leave the page, submit anything, or change server state. Modal
  dialogs are still not opened (focus trap / restore remain manual), and
  forms are never submitted, so error-handling checks stay partial/manual.
- The domain crawler renders each page in a real headless browser (like the
  audit itself), so it finds links that only appear after client-side
  rendering (menus, SPA routing, etc.). It stays on the same site — matched
  by registrable domain, so subdomains like `corporate.example.com` and
  `www.example.com` are treated as one site, using a small built-in list of
  common two-part suffixes (`co.uk`, `com.au`, ...) rather than a full
  public-suffix list — and skips non-HTML asset links. Because it's a real
  browser per page rather than a lightweight HTTP request, discovery is
  slower than a plain link crawl, especially at higher Max pages/Max depth.
- A single audit run is capped at 25 URLs; crawl discovery is capped at 60
  pages by default (configurable in the Crawl tab, hard cap enforced
  server-side).
