"""Render the HTML sources in docs/img to PNG (2x). Usage: python render.py [docs_dir]
Needs: pip install playwright && playwright install chromium (or set CHROME_EXE to a Chromium binary)."""
import os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

img = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent) / "img"
# (html source, element to capture, png name)
SHOTS = [
    ("memhub-overview.html", "#c", "memhub-overview.png"),
    ("review-ui.html", "#memories", "review-ui-memories.png"),
    ("review-ui.html", "#history", "review-ui-history.png"),
    ("logo.html", "#logo", "logo.png"),
]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROME_EXE") or None)
    pg = b.new_page(viewport={"width": 1700, "height": 1000}, device_scale_factor=2)
    for html, selector, png in SHOTS:
        pg.goto((img / html).resolve().as_uri())
        pg.wait_for_timeout(300)
        if html == "memhub-overview.html":
            print("layout problems:", pg.evaluate("window.__bad"))  # overflow / overlap checks run in the page
        pg.locator(selector).screenshot(path=str(img / png), omit_background=True)  # transparent where the page is
        print("wrote", png)
    b.close()
