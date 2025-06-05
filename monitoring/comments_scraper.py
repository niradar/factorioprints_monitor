"""
Scrape **all** Disqus comments (including replies) from a FactorioPrints page.
"""

from datetime import datetime, timezone
from typing import List, Dict, Any
import json, time
import logging
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page

# ---------- helpers ---------- #

def _scroll_until_iframe(page: Page, max_seconds: int = 30) -> None:
    """Scrolls slowly until the Disqus iframe shows up (or times out)."""
    start = time.time()
    step = page.viewport_size["height"] // 2
    while True:
        if page.query_selector("iframe[src*='disqus.com']"):
            return
        page.mouse.wheel(0, step)
        page.wait_for_timeout(300)          # let scripts fire
        if time.time() - start > max_seconds:
            raise PWTimeout("Scrolled for too long without finding Disqus iframe")

def _html_to_text(html: str) -> str:
    """Convert HTML to plain text, removing tags and extra spaces."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(" ", strip=True)


def _normalize(post: dict) -> Dict[str, Any]:
    """Normalize a Disqus post object to a simpler format."""

    author = post.get("author", {})

    # Disqus may give only `name` or even an empty dict for deleted users
    username = (
            author.get("username")          # normal case
            or author.get("name")           # fallback
            or f"user_{author.get('id')}"   # last-chance deterministic label
            or "unknown"
    )

    # Safe datetime parsing
    created_utc = datetime.now(timezone.utc)  # default value
    if created_str := post.get("createdAt"):
        try:
            created_utc = datetime.fromisoformat(created_str).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass  # Keep default if parsing fails

    return {
        "id"          : str(post.get("id", "")),
        "author"      : username,
        "parent_id" : post.get("parent"),          # None for main post, otherwise ID of parent comment
        "created_utc": created_utc,
        "message_html": post.get("message", ""),
        "message_text": _html_to_text(post.get("message", "")),
        "likes"     : post.get("likes", 0),
        "dislikes"  : post.get("dislikes", 0),
        "depth"     : post.get("depth", 0),           # 0 for main post, 1 for first reply, etc.
    }



# --------------------------------------------------------------------------- #
#  Main API                                                                   #
# --------------------------------------------------------------------------- #
def get_comments(url: str, timeout: int = 60_000) -> Dict[str, Any]:
    """Return {'total_comments': int, 'comments': [...] } for a blueprint page."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")

            # ── 1. scroll until the Disqus iframe appears ───────────────────
            try:
                _scroll_until_iframe(page)
            except PWTimeout:
                return {"total_comments": 0, "comments": []}

            iframe_handle = page.query_selector("iframe[src*='disqus.com']")
            if not iframe_handle:
                return {"total_comments": 0, "comments": []}
            iframe = iframe_handle.content_frame()

            # ── 2. grab the raw data node inside the iframe ─────────────────
            try:
                raw_node = iframe.wait_for_selector(
                    "#disqus-threadData",
                    state="attached",
                    timeout=timeout,
                )
                raw_text = raw_node.inner_text()

                # ── 3. peel off the JS wrapper safely  ────────────────────  ### CHANGED
                #
                # The script can be either:
                #   {"cursor":{...},"response":{...}}
                # or wrapped as:
                #   var threadData = {"cursor":{...},"response":{...}};
                #
                match = re.search(
                    r"=\s*(\{.*?\})\s*;?$",       # non-greedy to first closing brace
                    raw_text,
                    flags=re.S,
                )
                json_blob = match.group(1) if match else raw_text.strip()     ### CHANGED

                # (optional) cosmetic: collapse literal “\n” sequences
                json_blob = json_blob.replace("\\n", " ")                     ### CHANGED

                data = json.loads(json_blob)                                  ### CHANGED
            except (PWTimeout, json.JSONDecodeError, AttributeError) as e:
                logging.warning(f"Data extraction failed: {e} - {url}")
                return {"total_comments": 0, "comments": []}

            # ── 4. normalise & return ──────────────────────────────────────
            total = data.get("cursor", {}).get("total", 0)
            comments = []
            for post in data.get("response", {}).get("posts", []):
                try:
                    comments.append(_normalize(post))
                except Exception:
                    continue  # skip malformed record

            return {"total_comments": total, "comments": comments}

        finally:
            browser.close()



# ---------- quick demo ---------- #

BLUEPRINT_SAMPLE_URL = "https://factorioprints.com/view/-OO5V-wUkdrkhAji7y5m"


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    try:
        info = get_comments(BLUEPRINT_SAMPLE_URL)
        print("Total on thread:", info["total_comments"])
        for c in info["comments"]:
            indent = "  " * c["depth"]
            print(f"{indent}- {c['author']}: {c['message_text'][:60]}…")
    except PWTimeout as e:
        logging.error(f"Timeout: {e}")
