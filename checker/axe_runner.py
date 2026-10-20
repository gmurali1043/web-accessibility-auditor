import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AXE_JS_PATH = os.path.join(BASE_DIR, "vendor", "axe.min.js")

with open(AXE_JS_PATH, encoding="utf-8") as f:
    AXE_SOURCE = f.read()

AXE_TAGS = [
    "wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice",
]


def run_axe(page):
    """Injects the vendored axe-core v4.13.0 engine and runs a full scan.
    Returns (violations, passes_rule_ids) where violations is axe-core's
    raw violations array and passes_rule_ids is the set of rule ids that
    axe evaluated and found no failures for (used to mark automated
    test cases as PASS rather than "not evaluated")."""
    page.evaluate(AXE_SOURCE)
    result = page.evaluate(
        """(tags) => axe.run(document, { runOnly: { type: 'tag', values: tags } })""",
        AXE_TAGS,
    )
    violations = result.get("violations", [])
    passes = result.get("passes", [])
    incomplete = result.get("incomplete", [])
    passes_rule_ids = {p["id"] for p in passes}
    incomplete_rule_ids = {i["id"] for i in incomplete}
    return violations, passes_rule_ids, incomplete_rule_ids


def _wcag_from_tags(tags):
    """axe tags like "wcag143" / "wcag1410" -> ["1.4.3"] / ["1.4.10"]."""
    out = []
    for tag in tags or []:
        m = re.fullmatch(r"wcag(\d)(\d)(\d{1,2})", tag)
        if m:
            out.append(".".join(m.groups()))
    return out


def violations_to_issues(violations):
    """Flattens axe-core violations into one issue dict per offending node."""
    issues = []
    for v in violations:
        rule_id = v["id"]
        wcag = _wcag_from_tags(v.get("tags"))
        best_practice = "best-practice" in (v.get("tags") or [])
        impact = v.get("impact") or "moderate"
        help_text = v.get("help", "")
        help_url = v.get("helpUrl", "")
        description = v.get("description", "")
        for node in v.get("nodes", []):
            selector = " ".join(node.get("target", []) or [])
            html = node.get("html", "")
            failure_summary = node.get("failureSummary", "")
            issues.append({
                "source": "axe",
                "rule_id": rule_id,
                "impact": impact,
                "message": help_text or description,
                "selector": selector,
                "html": html,
                "fix": failure_summary or help_text,
                "help_url": help_url,
                "wcag": wcag,
                "best_practice": best_practice,
            })
    return issues
