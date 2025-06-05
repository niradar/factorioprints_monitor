"""
blueprints_scraper.py
---------------------

Usage:
    python blueprints_scraper.py "https://factorioprints.com/user/I6YX1Ar1cWUwhbQgMcW4nyZkDs52"

Output:
    JSON printed to stdout (see bottom of file if you prefer saving to disk)
"""

import json
import sys
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PWTimeoutError
import time

def scroll_until_loaded(page, pause_ms: int = 800, max_idle_loops: int = 3):
    """
    Scrolls to the bottom, pauses, and repeats until no new
    blueprint cards appear for `max_idle_loops` consecutive rounds.

    Works even when the site keeps a websocket open, so 'networkidle'
    would hang forever.
    """
    css_card = "a[href^='/blueprint/']"  # adjust if your selector differs
    last_count = 0
    idle_rounds = 0

    while True:
        # Scroll down a viewport height each time (faster than scrollHeight jumps)
        page.evaluate("window.scrollBy(0, window.innerHeight);")
        page.wait_for_timeout(pause_ms)  # let lazy-load JS run

        current_count = page.locator(css_card).count()
        if current_count == last_count:
            idle_rounds += 1
            if idle_rounds >= max_idle_loops:
                break                       # nothing new is loading → we’re done
        else:
            idle_rounds = 0                 # reset idle counter
            last_count = current_count

def extract_blueprints(page):
    """
    Parse every .blueprint-thumbnail card and return url, name, favorites.
    """
    results = []
    cards = page.query_selector_all(".blueprint-thumbnail")

    for card in cards:
        # first <a> contains the relative /view/<id> url and the thumbnail
        anchor = card.query_selector("a")
        rel_href = anchor.get_attribute("href")
        full_url = urljoin(page.url, rel_href)

        # favorites = first text node inside the <p> before the heart icon
        favorites_raw = (
            card.query_selector("p").inner_text().strip().split(" ", 1)[0]
        )
        favorites = int(favorites_raw)

        # blueprint title lives in <p> → <a> → <span>
        name = card.query_selector("p a span").inner_text().strip()

        results.append(
            {
                "url": full_url,
                "name": name,
                "favorites": favorites,
            }
        )
    return results


def scrape_user_blueprints(user_url: str, headless: bool = True, timeout: int = 60_000):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(user_url, timeout=timeout)

        scroll_until_loaded(page)
        data = extract_blueprints(page)

        browser.close()
    return data


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python blueprints_scraper.py <user_page_url>")
        print("Defaulting to a sample user URL.")
        url = "https://factorioprints.com/user/I6YX1Ar1cWUwhbQgMcW4nyZkDs52"
    else:
        url = sys.argv[1]

    blueprints = scrape_user_blueprints(url)

    # --- output ---
    print(json.dumps(blueprints, ensure_ascii=False, indent=2))

    # alternatively, write to a file:
    # with open("blueprints.json", "w", encoding="utf-8") as f:
    #     json.dump(blueprints, f, ensure_ascii=False, indent=2)
