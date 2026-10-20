from playwright.sync_api import Error as PlaywrightError

# Tried in order: Playwright's bundled Chromium, then system-installed
# Chrome / Edge (used when `playwright install chromium` can't download).
CHANNELS = (None, "chrome", "msedge")


def launch_browser(p):
    """Launches a Chromium-based browser, falling back to installed
    Chrome/Edge if Playwright's own Chromium build isn't available."""
    last_error = None
    for channel in CHANNELS:
        try:
            if channel:
                return p.chromium.launch(channel=channel)
            return p.chromium.launch()
        except PlaywrightError as e:
            last_error = e
    raise RuntimeError(
        "No Chromium browser available. Run `python -m playwright install chromium` "
        "or install Google Chrome / Microsoft Edge."
    ) from last_error
