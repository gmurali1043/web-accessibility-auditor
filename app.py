import os
import uuid

from flask import Flask, request, jsonify, render_template, send_file, abort

from checker.registry import TEST_CASES, all_ids_in_order, AUTOMATION_COUNTS
from checker.report import audit_urls, compute_overall_summary
from checker.crawler import discover_pages
from checker.exporters import to_markdown, to_html, to_xlsx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

app = Flask(__name__)

# In-memory batch store: {batch_id: {"reports": [...], "title": str}}
BATCHES = {}

MAX_URLS_PER_AUDIT = 25
MAX_CRAWL_PAGES = 60


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/test-cases")
def api_test_cases():
    return jsonify({
        "counts": AUTOMATION_COUNTS,
        "cases": [TEST_CASES[tid] for tid in all_ids_in_order()],
    })


def _clean_urls(raw):
    urls = []
    for u in raw or []:
        u = (u or "").strip()
        if not u:
            continue
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        urls.append(u)
    # de-dupe, preserve order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


@app.route("/api/audit", methods=["POST"])
def api_audit():
    data = request.get_json(force=True, silent=True) or {}
    urls = _clean_urls(data.get("urls", []))
    if not urls:
        return jsonify({"error": "No valid URL(s) provided."}), 400
    if len(urls) > MAX_URLS_PER_AUDIT:
        return jsonify({"error": f"Too many URLs at once (max {MAX_URLS_PER_AUDIT}). "
                                  f"Split into smaller batches."}), 400

    try:
        reports = audit_urls(urls)
    except Exception as e:
        return jsonify({"error": f"Audit failed: {e}"}), 500

    batch_id = uuid.uuid4().hex[:12]
    BATCHES[batch_id] = {"reports": reports, "title": "Web Accessibility Report"}

    return jsonify({
        "batch_id": batch_id,
        "reports": reports,
        "overall": compute_overall_summary(reports),
    })


@app.route("/api/crawl-discover", methods=["POST"])
def api_crawl_discover():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No domain/URL provided."}), 400
    max_pages = min(int(data.get("max_pages", 40) or 40), MAX_CRAWL_PAGES)
    max_depth = min(int(data.get("max_depth", 3) or 3), 5)
    use_sitemap = bool(data.get("use_sitemap", True))

    try:
        result = discover_pages(url, max_pages=max_pages, max_depth=max_depth, use_sitemap=use_sitemap)
    except Exception as e:
        return jsonify({"error": f"Crawl failed: {e}"}), 500

    return jsonify(result)


@app.route("/api/download/<batch_id>/<fmt>")
def api_download(batch_id, fmt):
    batch = BATCHES.get(batch_id)
    if not batch:
        abort(404)
    reports = batch["reports"]
    title = batch["title"]
    out_dir = os.path.join(RESULTS_DIR, batch_id)
    os.makedirs(out_dir, exist_ok=True)

    if fmt == "html":
        path = os.path.join(out_dir, "accessibility-report.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_html(reports, title=title))
        return send_file(path, as_attachment=True, download_name="accessibility-report.html")
    elif fmt == "md":
        path = os.path.join(out_dir, "accessibility-report.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_markdown(reports, title=title))
        return send_file(path, as_attachment=True, download_name="accessibility-report.md")
    elif fmt == "xlsx":
        path = os.path.join(out_dir, "accessibility-report.xlsx")
        to_xlsx(reports, path, title=title)
        return send_file(path, as_attachment=True, download_name="accessibility-report.xlsx")
    else:
        abort(404)


if __name__ == "__main__":
    app.run(debug=True, port=8060, use_reloader=False)
