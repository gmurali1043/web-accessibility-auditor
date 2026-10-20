from playwright.sync_api import Error as PlaywrightError

from . import config, netguard

# Tried in order: Playwright's bundled Chromium, then system-installed
# Chrome / Edge (used when `playwright install chromium` can't download).
CHANNELS = (None, "chrome", "msedge")


def launch_browser(p):
    """Launches a Chromium-based browser, falling back to installed
    Chrome/Edge if Playwright's own Chromium build isn't available. In
    public mode all browser traffic goes through the netguard filtering
    proxy, which blocks private/internal destinations."""
    options = {}
    if config.PUBLIC_MODE:
        options["proxy"] = {"server": netguard.proxy_url()}
    last_error = None
    for channel in CHANNELS:
        try:
            if channel:
                return p.chromium.launch(channel=channel, **options)
            return p.chromium.launch(**options)
        except PlaywrightError as e:
            last_error = e
    raise RuntimeError(
        "No Chromium browser available. Run `python -m playwright install chromium` "
        "or install Google Chrome / Microsoft Edge."
    ) from last_error
