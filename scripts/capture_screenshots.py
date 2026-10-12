# =============================================================================
# File:     capture_screenshots.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Captures the running dashboard with headless Chromium (Playwright)
#           so the README screenshots can be regenerated the same way.
# =============================================================================
"""Usage: start the app (python app.py), then

    python scripts/capture_screenshots.py [--url http://127.0.0.1:8050] [--chromium PATH]

Writes docs/screenshots/enhanced_*.png. If the portal is on (PORTAL_ENABLED=1)
pass --portal to capture the sign-in page and sign in first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"


def wait_for_table(page, text: str) -> None:
    page.wait_for_selector(".dash-spreadsheet td", timeout=15000)
    page.wait_for_function("t => document.querySelector('#criteria').innerText.includes(t)", arg=text, timeout=15000)
    page.wait_for_selector("#breed-chart .main-svg", timeout=15000)
    page.wait_for_timeout(1200)  # let the chart and map finish drawing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8050")
    parser.add_argument("--chromium", default=None, help="path to a Chromium binary")
    parser.add_argument("--portal", action="store_true", help="capture and pass the portal sign-in first")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    tile_results = {"ok": 0, "failed": 0}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium) if args.chromium else pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.on("requestfinished", lambda r: tile_results.__setitem__("ok", tile_results["ok"] + 1)
                if "tile" in r.url else None)
        page.on("requestfailed", lambda r: tile_results.__setitem__("failed", tile_results["failed"] + 1)
                if "tile" in r.url else None)

        page.goto(args.url, wait_until="networkidle")
        if args.portal:
            page.wait_for_selector("form.portal-form")
            page.screenshot(path=str(OUT / "enhanced_portal_signin.png"), full_page=True)
            page.fill("input[name=name]", "Demo Reviewer")
            page.fill("input[name=company]", "Example Co")
            page.click("button[type=submit]")
            page.wait_for_load_state("networkidle")

        wait_for_table(page, "every animal")
        page.screenshot(path=str(OUT / "enhanced_initial_all.png"), full_page=True)

        for label, key, marker in (
            ("Water Rescue", "water", "Water Rescue:"),
            ("Mountain or Wilderness Rescue", "mountain", "Mountain or Wilderness Rescue:"),
            ("Disaster Rescue or Individual Tracking", "disaster", "Disaster Rescue or Individual Tracking:"),
        ):
            page.get_by_label(label, exact=True).check()
            wait_for_table(page, marker)
            page.screenshot(path=str(OUT / f"enhanced_{key}.png"), full_page=True)

        # Water rescue, page 2, third row selected -> map follows the row.
        page.get_by_label("Water Rescue", exact=True).check()
        wait_for_table(page, "Water Rescue:")
        page.click("button.next-page")
        page.wait_for_function("() => document.querySelector('.current-page') && "
                               "(document.querySelector('.current-page').value || "
                               "document.querySelector('.current-page').placeholder) == '2'", timeout=15000)
        page.wait_for_timeout(800)
        page.locator(".dash-spreadsheet td.dash-select-cell input").nth(2).check()
        page.wait_for_function("() => document.querySelector('#map-id').innerText.includes('Selected:')", timeout=15000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "enhanced_map_selected_row.png"), full_page=True)
        browser.close()

    print(f"saved to {OUT}")
    print(f"map tile requests: {tile_results['ok']} finished, {tile_results['failed']} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
