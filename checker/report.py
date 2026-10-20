import datetime
from collections import Counter, defaultdict
from fnmatch import fnmatch

from playwright.sync_api import sync_playwright

from . import axe_runner, custom_checks
from .browser import launch_browser
from .registry import TEST_CASES, all_ids_in_order

VIEWPORT = {"width": 1280, "height": 900}
NAV_TIMEOUT_MS = 30000


def _build_reverse_maps():
    axe_map = defaultdict(list)   # rule_id -> [test_case_id]  (exact or wildcard pattern)
    custom_map = defaultdict(list)  # check_name -> [test_case_id]
    meta_ids = []  # test case ids whose only checks are axe:* (the GEN-4-1 meta rule)
    for tc_id in all_ids_in_order():
        tc = TEST_CASES[tc_id]
        for chk in tc.get("checks", []):
            if chk.startswith("axe:"):
                pattern = chk[len("axe:"):]
                if pattern == "*":
                    meta_ids.append(tc_id)
                else:
                    axe_map[pattern].append(tc_id)
            elif chk.startswith("custom:"):
                name = chk[len("custom:"):]
                custom_map[name].append(tc_id)
    return axe_map, custom_map, meta_ids


AXE_PATTERN_MAP, CUSTOM_MAP, META_TC_IDS = _build_reverse_maps()


def _tc_ids_for_axe_rule(rule_id):
    ids = set()
    for pattern, tc_ids in AXE_PATTERN_MAP.items():
        if pattern == rule_id or ("*" in pattern and fnmatch(rule_id, pattern)):
            ids.update(tc_ids)
    return ids


def _new_page(context, url):
    page = context.new_page()
    nav_error = None
    try:
        page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
    except Exception as e:
        nav_error = str(e)
    return page, nav_error


def build_report_for_page(context, url, display_name=None):
    display_name = display_name or url
    page, nav_error = _new_page(context, url)
    if nav_error:
        page.close()
        return _error_report(url, display_name, nav_error)

    try:
        violations, axe_pass_ids, axe_incomplete_ids = axe_runner.run_axe(page)
    except Exception as e:
        violations, axe_pass_ids, axe_incomplete_ids = [], set(), set()
        violations_error = str(e)
    else:
        violations_error = None

    axe_issues = axe_runner.violations_to_issues(violations)
    custom_issues = custom_checks.run_all_checks(page)
    meta = custom_checks.collect_page_meta(page)
    page_title = meta.get("title") or ""

    issues_by_tc = defaultdict(list)
    for issue in axe_issues:
        for tc_id in _tc_ids_for_axe_rule(issue["rule_id"]):
            issues_by_tc[tc_id].append(issue)
    for issue in custom_issues:
        tc_ids = issue.get("test_case_ids") or CUSTOM_MAP.get(issue["check_name"], [])
        for tc_id in tc_ids:
            issues_by_tc[tc_id].append(issue)

    total_axe_violations = len(axe_issues)

    rows = []
    for tc_id in all_ids_in_order():
        tc = TEST_CASES[tc_id]
        tier = tc["automation"]
        tc_issues = issues_by_tc.get(tc_id, [])

        if tc_id in META_TC_IDS:
            status = "FAIL" if total_axe_violations > 0 else "PASS"
            tc_issues = axe_issues if total_axe_violations > 0 else []
        elif tier == "automated":
            status = "FAIL" if tc_issues else "PASS"
        elif tier == "partial":
            status = "FAIL" if tc_issues else "REVIEW"
        else:
            status = "REVIEW"

        rows.append({
            **_row_base(tc),
            "status": status,
            "issue_count": len(tc_issues),
            "issues": _dedupe_issues(tc_issues)[:25],
        })

    page.close()

    summary = _summarize(rows)
    report = {
        "url": url,
        "display_name": display_name,
        "title": page_title,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
        "summary": summary,
        "errors": [violations_error] if violations_error else [],
        "_meta": meta,
    }
    return report


def _row_base(tc):
    """Static test-case fields every result row carries: the VGAR id, its
    WCAG mapping, and the manual test script (tool / steps / expected
    result / pass-fail outcomes) shown for rows needing manual review."""
    return {
        "id": tc["id"],
        "title": tc["title"],
        "category": tc["category"],
        "requirement": tc["requirement"],
        "automation": tc["automation"],
        "method": tc["method"],
        "wcag": tc.get("wcag", []),
        "tool": tc.get("tool", ""),
        "expectation": tc.get("expectation", ""),
        "procedure": tc.get("procedure", ""),
        "outcomes": tc.get("outcomes", ""),
    }


def _dedupe_issues(issues):
    seen = set()
    out = []
    for i in issues:
        key = (i.get("selector", ""), i.get("message", "")[:120])
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out


def _summarize(rows):
    counts = Counter(r["status"] for r in rows)
    tier_counts = Counter(r["automation"] for r in rows)
    pass_n = counts.get("PASS", 0)
    fail_n = counts.get("FAIL", 0)
    review_n = counts.get("REVIEW", 0)
    determinable = pass_n + fail_n
    score = round((pass_n / determinable) * 100, 1) if determinable else None
    return {
        "total": len(rows),
        "pass": pass_n,
        "fail": fail_n,
        "review": review_n,
        "automated_tier": tier_counts.get("automated", 0),
        "partial_tier": tier_counts.get("partial", 0),
        "manual_tier": tier_counts.get("manual", 0),
        "determinable": determinable,
        "score_pct": score,
    }


def _error_report(url, display_name, error):
    rows = []
    for tc_id in all_ids_in_order():
        tc = TEST_CASES[tc_id]
        rows.append({**_row_base(tc), "status": "REVIEW", "issue_count": 0, "issues": []})
    return {
        "url": url, "display_name": display_name, "title": None,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "rows": rows, "summary": _summarize(rows),
        "errors": [f"Failed to load this page: {error}"],
        "_meta": {},
    }


def audit_urls(urls, progress_cb=None):
    """Runs a full audit for each URL in `urls` (order preserved). Opens one
    browser and one fresh context/page per URL. Applies batch-level
    cross-page checks when auditing more than one page."""
    reports = []
    with sync_playwright() as p:
        browser = launch_browser(p)
        try:
            for i, url in enumerate(urls):
                context = browser.new_context(viewport=VIEWPORT, ignore_https_errors=True)
                try:
                    report = build_report_for_page(context, url)
                except Exception as e:
                    report = _error_report(url, url, str(e))
                reports.append(report)
                context.close()
                if progress_cb:
                    progress_cb(i + 1, len(urls), url)
        finally:
            browser.close()

    if len(reports) > 1:
        apply_batch_checks(reports)

    return reports


def compute_overall_summary(reports):
    pass_n = sum(r["summary"]["pass"] for r in reports)
    fail_n = sum(r["summary"]["fail"] for r in reports)
    review_n = sum(r["summary"]["review"] for r in reports)
    determinable = pass_n + fail_n
    score = round((pass_n / determinable) * 100, 1) if determinable else None
    return {
        "pages": len(reports),
        "pass": pass_n,
        "fail": fail_n,
        "review": review_n,
        "determinable": determinable,
        "score_pct": score,
    }


# ---------------------------------------------------------------------------
# Batch-level (cross-page) checks: GEN-1-2, NAV-4-1, NAV-4-2, NAV-4-4
# ---------------------------------------------------------------------------

def _row_by_id(report, tc_id):
    for row in report["rows"]:
        if row["id"] == tc_id:
            return row
    return None


def _flag(row, message, fix):
    row["issues"].append({
        "source": "batch", "selector": "(cross-page)", "html": "",
        "message": message, "fix": fix,
    })
    row["issue_count"] = len(row["issues"])
    if row["automation"] == "partial":
        row["status"] = "FAIL"


def apply_batch_checks(reports):
    _apply_gen12(reports)
    _apply_nav44(reports)
    _apply_nav41(reports)
    _apply_nav42(reports)
    for r in reports:
        r["summary"] = _summarize(r["rows"])


def _apply_gen12(reports):
    titles = [r.get("_meta", {}).get("title", "") for r in reports]
    counts = Counter(t for t in titles if t)
    for r in reports:
        row = _row_by_id(r, "GEN-1-2")
        if not row:
            continue
        t = r.get("_meta", {}).get("title", "")
        if t and counts[t] > 1:
            _flag(row, f'Page title "{t}" is duplicated across {counts[t]} of the {len(reports)} audited pages.',
                  "Give every page a unique title while keeping a consistent structure (e.g. \"Page Name | Site Name\").")


def _apply_nav44(reports):
    for r in reports:
        row = _row_by_id(r, "NAV-4-4")
        if not row:
            continue
        meta = r.get("_meta", {})
        missing = []
        if not meta.get("title"):
            missing.append("a page title")
        if meta.get("h1_count") != 1:
            missing.append("exactly one H1")
        if meta.get("has_nav") and not meta.get("has_current_nav_indicator"):
            missing.append('a "current page" indicator in the navigation (e.g. aria-current="page")')
        if missing:
            _flag(row, f"This page is missing: {', '.join(missing)} — one or more of the context cues expected consistently across every audited page.",
                  "Ensure every page carries a title, a single H1, and (where a nav menu is present) marks the current item with aria-current=\"page\".")


def _apply_nav41(reports):
    if len(reports) < 2:
        return
    sequences = {r["url"]: r.get("_meta", {}).get("header_nav_hrefs", []) for r in reports}
    common = None
    for seq in sequences.values():
        s = set(seq)
        common = s if common is None else common & s
    if not common:
        return
    ref_order = None
    ref_url = None
    for r in reports:
        seq = sequences[r["url"]]
        filtered = [h for h in seq if h in common]
        if ref_order is None:
            ref_order = filtered
            ref_url = r["url"]
            continue
        if filtered != ref_order:
            row = _row_by_id(r, "NAV-4-1")
            if row:
                _flag(row, f"The shared navigation links that appear on multiple pages are not in the same relative order here as on {ref_url}.",
                      "Keep repeated components (nav, header, footer links) in the same relative order on every page.")


def _apply_nav42(reports):
    if len(reports) < 2:
        return
    href_to_names = defaultdict(set)
    href_to_pages = defaultdict(list)
    for r in reports:
        for href, name in r.get("_meta", {}).get("link_names", []):
            if not href or not name:
                continue
            href_to_names[href].add(name)
            href_to_pages[href].append((r["url"], name))
    flagged_hrefs = {h: names for h, names in href_to_names.items() if len(names) > 1}
    if not flagged_hrefs:
        return
    for r in reports:
        row = _row_by_id(r, "NAV-4-2")
        if not row:
            continue
        for href, name in r.get("_meta", {}).get("link_names", []):
            if href in flagged_hrefs:
                others = sorted({n for n in flagged_hrefs[href] if n != name})
                if others:
                    _flag(row, f'A navigation link to "{href}" is labeled "{name}" here but "{", ".join(others)}" on another audited page.',
                          "Use the same accessible name for links/controls that share the same destination or functionality across pages.")
                break
