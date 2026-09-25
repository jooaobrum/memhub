"""Render memhub-overview.html to PNG (2x). Usage: python render.py [docs_dir]
Needs: pip install playwright && playwright install chromium (or set CHROME_EXE to a Chromium binary)."""
import os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

img = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent) / "img"
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROME_EXE") or None)
    pg = b.new_page(viewport={"width": 1600, "height": 980}, device_scale_factor=2)
    pg.goto((img / "memhub-overview.html").resolve().as_uri())
    pg.wait_for_timeout(300)
    print("layout problems:", pg.evaluate("window.__bad"))  # overflow / overlap checks run in the page
    pg.locator("#c").screenshot(path=str(img / "memhub-overview.png"))
    b.close()
