import os


def _env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# PUBLIC_MODE=1 is set by the hosted deployment (see Dockerfile). Left unset,
# the app behaves exactly as the local tool always has: no outbound network
# filtering (so intranet/localhost sites can be audited), no per-visitor
# limits, and results kept for the life of the process.
PUBLIC_MODE = _env_flag("PUBLIC_MODE")

if PUBLIC_MODE:
    MAX_URLS_PER_AUDIT = _env_int("MAX_URLS_PER_AUDIT", 10)
    MAX_CRAWL_PAGES = _env_int("MAX_CRAWL_PAGES", 30)
else:
    MAX_URLS_PER_AUDIT = _env_int("MAX_URLS_PER_AUDIT", 25)
    MAX_CRAWL_PAGES = _env_int("MAX_CRAWL_PAGES", 60)
MAX_CRAWL_DEPTH = 5

# Public-mode only: how many audits/crawls may run at once (each launches a
# headless Chromium), how many more may wait in line, how many jobs one
# visitor may start per hour, and how long finished results are kept.
MAX_CONCURRENT_JOBS = _env_int("MAX_CONCURRENT_JOBS", 2)
MAX_QUEUED_JOBS = _env_int("MAX_QUEUED_JOBS", 6)
JOBS_PER_IP_PER_HOUR = _env_int("JOBS_PER_IP_PER_HOUR", 20)
RESULT_TTL_SECONDS = _env_int("RESULT_TTL_SECONDS", 3600)

# RUN_JOBS_INLINE=1 runs each audit/crawl inside the HTTP request that
# started it instead of on a background thread. Needed on hosts that only
# give the container CPU while a request is open (Google Cloud Run's
# default request-based billing); the page then gets the result directly
# instead of polling, at the cost of the "N of M pages" progress updates.
RUN_JOBS_INLINE = _env_flag("RUN_JOBS_INLINE")
