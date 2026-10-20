import io
import os
import threading
import time
import uuid
from collections import defaultdict, deque

from flask import Flask, request, jsonify, render_template, send_file, abort

from checker import config, netguard
from checker.registry import TEST_CASES, all_ids_in_order, AUTOMATION_COUNTS
from checker.report import audit_urls, compute_overall_summary
from checker.crawler import discover_pages
from checker.exporters import to_markdown, to_html, to_xlsx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
if not config.PUBLIC_MODE:
    os.makedirs(RESULTS_DIR, exist_ok=True)

app = Flask(__name__)

if config.PUBLIC_MODE:
    # Behind the hosting platform's reverse proxy: trust its X-Forwarded-For
    # so per-visitor limits see the real client address.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# In-memory batch store: {batch_id: {"reports": [...], "title": str, "created": float}}
BATCHES = {}

# Background jobs (audits and crawls run off the request thread so a long
# run never hits an HTTP timeout; the page polls /api/jobs/<id>):
# {job_id: {"status", "progress", "result", "error", "ip", "created", "finished"}}
JOBS = {}
STATE_LOCK = threading.Lock()
JOB_SLOTS = threading.BoundedSemaphore(config.MAX_CONCURRENT_JOBS) if config.PUBLIC_MODE else None
JOB_STARTS_BY_IP = defaultdict(deque)
FINISHED_JOB_TTL = 3600


@app.route("/")
def index():
    return render_template(
        "index.html",
        public_mode=config.PUBLIC_MODE,
        max_urls=config.MAX_URLS_PER_AUDIT,
        max_crawl_pages=config.MAX_CRAWL_PAGES,
        max_crawl_depth=config.MAX_CRAWL_DEPTH,
    )


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


def _blocked_url_error(urls):
    """Public mode only: rejects URLs pointing at private/internal addresses
    up front with a readable message. (The netguard proxy enforces the same
    rule for every request the browser makes, including redirects.)"""
    if not config.PUBLIC_MODE:
        return None
    for u in urls:
        try:
            netguard.check_url(u)
        except netguard.BlockedAddress as e:
            return f"{u}: {e}"
    return None


def _cleanup_expired():
    now = time.time()
    with STATE_LOCK:
        for job_id in [j for j, job in JOBS.items()
                       if job["finished"] and now - job["finished"] > FINISHED_JOB_TTL]:
            del JOBS[job_id]
        if config.PUBLIC_MODE:
            for batch_id in [b for b, batch in BATCHES.items()
                             if now - batch["created"] > config.RESULT_TTL_SECONDS]:
                del BATCHES[batch_id]


def _admission_error(ip):
    """Public mode only: per-visitor and server-wide limits. Returns
    (message, http_status) or None. Caller holds STATE_LOCK."""
    if not config.PUBLIC_MODE:
        return None
    active = [j for j in JOBS.values() if j["status"] in ("queued", "running")]
    if any(j["ip"] == ip for j in active):
        return "You already have an audit or crawl in progress. Please wait for it to finish.", 429
    if len(active) >= config.MAX_CONCURRENT_JOBS + config.MAX_QUEUED_JOBS:
        return "The server is busy with other audits right now. Please try again in a few minutes.", 503
    starts = JOB_STARTS_BY_IP[ip]
    while starts and time.time() - starts[0] > 3600:
        starts.popleft()
    if len(starts) >= config.JOBS_PER_IP_PER_HOUR:
        return (f"Hourly limit reached ({config.JOBS_PER_IP_PER_HOUR} audits/crawls per hour). "
                f"Please try again later, or run the tool locally for unlimited use."), 429
    return None


def _start_job(work):
    """Runs work(job) on a background thread and returns a 202 response
    with the job id, or an error response if admission limits are hit.
    With RUN_JOBS_INLINE, runs it within this request and returns the
    result (200) or error (500) directly."""
    _cleanup_expired()
    ip = request.remote_addr or "unknown"
    with STATE_LOCK:
        error = _admission_error(ip)
        if error:
            return jsonify({"error": error[0]}), error[1]
        job_id = uuid.uuid4().hex[:12]
        job = {"status": "queued", "progress": None, "result": None, "error": None,
               "ip": ip, "created": time.time(), "finished": None}
        JOBS[job_id] = job
        JOB_STARTS_BY_IP[ip].append(time.time())

    def run():
        if JOB_SLOTS:
            JOB_SLOTS.acquire()
        try:
            job["status"] = "running"
            job["result"] = work(job)
            job["status"] = "done"
        except Exception as e:
            job["error"] = str(e)
            job["status"] = "error"
        finally:
            job["finished"] = time.time()
            if JOB_SLOTS:
                JOB_SLOTS.release()

    if config.RUN_JOBS_INLINE:
        run()
        if job["status"] == "error":
            return jsonify({"error": job["error"]}), 500
        return jsonify(job["result"])

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id}), 202


@app.route("/api/jobs/<job_id>")
def api_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "This job has expired or does not exist. Please run it again."}), 404
    return jsonify({
        "status": job["status"],
        "progress": job["progress"],
        "result": job["result"],
        "error": job["error"],
    })


@app.route("/api/audit", methods=["POST"])
def api_audit():
    data = request.get_json(force=True, silent=True) or {}
    urls = _clean_urls(data.get("urls", []))
    if not urls:
        return jsonify({"error": "No valid URL(s) provided."}), 400
    if len(urls) > config.MAX_URLS_PER_AUDIT:
        return jsonify({"error": f"Too many URLs at once (max {config.MAX_URLS_PER_AUDIT}). "
                                  f"Split into smaller batches."}), 400
    blocked = _blocked_url_error(urls)
    if blocked:
        return jsonify({"error": blocked}), 400

    def work(job):
        def progress(done, total, url):
            job["progress"] = {"done": done, "total": total, "url": url}
        job["progress"] = {"done": 0, "total": len(urls), "url": None}
        try:
            reports = audit_urls(urls, progress_cb=progress)
        except Exception as e:
            raise RuntimeError(f"Audit failed: {e}")

        batch_id = uuid.uuid4().hex[:12]
        with STATE_LOCK:
            BATCHES[batch_id] = {"reports": reports, "title": "Web Accessibility Report",
                                 "created": time.time()}
        return {
            "batch_id": batch_id,
            "reports": reports,
            "overall": compute_overall_summary(reports),
        }

    return _start_job(work)


@app.route("/api/crawl-discover", methods=["POST"])
def api_crawl_discover():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No domain/URL provided."}), 400
    max_pages = min(int(data.get("max_pages", 40) or 40), config.MAX_CRAWL_PAGES)
    max_depth = min(int(data.get("max_depth", 3) or 3), config.MAX_CRAWL_DEPTH)
    use_sitemap = bool(data.get("use_sitemap", True))
    blocked = _blocked_url_error(_clean_urls([url]))
    if blocked:
        return jsonify({"error": blocked}), 400

    def work(job):
        try:
            return discover_pages(url, max_pages=max_pages, max_depth=max_depth, use_sitemap=use_sitemap)
        except Exception as e:
            raise RuntimeError(f"Crawl failed: {e}")

    return _start_job(work)


EXPORTS = {
    "html": ("accessibility-report.html", lambda reports, title: to_html(reports, title=title)),
    "md": ("accessibility-report.md", lambda reports, title: to_markdown(reports, title=title)),
}


@app.route("/api/download/<batch_id>/<fmt>")
def api_download(batch_id, fmt):
    batch = BATCHES.get(batch_id)
    if not batch:
        abort(404)
    reports = batch["reports"]
    title = batch["title"]

    if fmt in EXPORTS:
        filename, render = EXPORTS[fmt]
        content = render(reports, title).encode("utf-8")
    elif fmt == "xlsx":
        filename = "accessibility-report.xlsx"
        buf = io.BytesIO()
        to_xlsx(reports, buf, title=title)
        content = buf.getvalue()
    else:
        abort(404)

    if not config.PUBLIC_MODE:
        # Local runs also keep a copy of every export under results/.
        out_dir = os.path.join(RESULTS_DIR, batch_id)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, filename), "wb") as f:
            f.write(content)

    return send_file(io.BytesIO(content), as_attachment=True, download_name=filename)


if __name__ == "__main__":
    if config.PUBLIC_MODE:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), threaded=True)
    else:
        app.run(debug=True, port=8060, use_reloader=False)
