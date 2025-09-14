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
from playwright.async_api import async_playwright, TimeoutError as PWAsyncTimeout
import asyncio

# ---------- helpers ---------- #

def _scroll_until_iframe(page: Page, max_seconds: int = 30) -> None:
    """Scrolls slowly until the Disqus iframe shows up (or times out)."""
    start = time.time()
    try:
        step = page.viewport_size["height"] // 2
    except Exception:
        # Fallback to a reasonable height if viewport_size is None
        step = 400
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
    return " ".join(soup.stripped_strings)


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



def _extract_thread_data(raw_text, url, debug_dir=None):
    """Extract JSON from Disqus threadData script, robust to newlines/braces."""
    import os
    import logging
    # Save raw text for debug if requested
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        with open(os.path.join(debug_dir, "threadData_raw.txt"), "w", encoding="utf-8") as f:
            f.write(raw_text)
    raw = raw_text.strip()
    # Case 1: Just JSON
    if raw.startswith("{") and raw.endswith("}"):
        return json.loads(raw)
    # Case 2: Wrapped as var threadData = {...};
    if raw.startswith("var threadData ="):
        json_start = raw.find("{")
        json_end = raw.rfind("}")
        if json_start != -1 and json_end != -1:
            return json.loads(raw[json_start:json_end+1])
    # Fallback: try to extract with regex, but log for debug
    match = re.search(r"=\s*({[\s\S]*})\s*;?$", raw, flags=re.S)
    if match:
        return json.loads(match.group(1))
    # If all fails, log and raise
    logging.error(f"Could not extract Disqus threadData for {url}")
    if debug_dir:
        with open(os.path.join(debug_dir, "threadData_error.txt"), "w", encoding="utf-8") as f:
            f.write(f"Failed to extract threadData for {url}\n")
            f.write(raw)
    raise ValueError("Could not extract Disqus threadData")

# --------------------------------------------------------------------------- #
#  Main API                                                                   #
# --------------------------------------------------------------------------- #
def get_comments(url: str, timeout: int = 60_000, debug_dir: str = None) -> Dict[str, Any]:
    """Return {'total_comments': int, 'comments': [...] } for a blueprint page. Optionally save debug HTML."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            if debug_dir:
                import os
                os.makedirs(debug_dir, exist_ok=True)
                with open(os.path.join(debug_dir, "main.html"), "w", encoding="utf-8") as f:
                    f.write(page.content())
            # ── 1. scroll until the Disqus iframe appears ───────────────────
            try:
                _scroll_until_iframe(page)
            except PWTimeout:
                return {"total_comments": 0, "comments": []}
            iframe_handle = page.query_selector("iframe[src*='disqus.com']")
            if not iframe_handle:
                return {"total_comments": 0, "comments": []}
            iframe = iframe_handle.content_frame()
            if debug_dir and iframe:
                with open(os.path.join(debug_dir, "iframe.html"), "w", encoding="utf-8") as f:
                    f.write(iframe.content())
            # ── 2. grab the raw data node inside the iframe ─────────────────
            try:
                raw_node = iframe.wait_for_selector(
                    "#disqus-threadData",
                    state="attached",
                    timeout=timeout,
                )
                raw_text = raw_node.inner_text()
                data = _extract_thread_data(raw_text, url, debug_dir)
            except (PWTimeout, json.JSONDecodeError, AttributeError, ValueError) as e:
                logging.warning(f"Data extraction failed: {e} - {url}")
                if debug_dir:
                    with open(os.path.join(debug_dir, "error.txt"), "w", encoding="utf-8") as f:
                        f.write(str(e))
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

async def get_comments_async(url: str, timeout: int = 60_000, debug_dir: str = None) -> Dict[str, Any]:
    """Async version: Return {'total_comments': int, 'comments': [...] } for a blueprint page. Optionally save debug HTML."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            if debug_dir:
                import os
                os.makedirs(debug_dir, exist_ok=True)
                with open(os.path.join(debug_dir, "main.html"), "w", encoding="utf-8") as f:
                    f.write(await page.content())
            # ── 1. scroll until the Disqus iframe appears ───────────────────
            try:
                # Async version of scroll until iframe
                start = asyncio.get_event_loop().time()
                vs = page.viewport_size or {"height": 800}
                step = vs["height"] // 2
                while True:
                    if await page.query_selector("iframe[src*='disqus.com']"):
                        break
                    await page.mouse.wheel(0, step)
                    await page.wait_for_timeout(300)
                    if asyncio.get_event_loop().time() - start > 30:
                        raise PWAsyncTimeout("Scrolled for too long without finding Disqus iframe")
            except PWAsyncTimeout:
                return {"total_comments": 0, "comments": []}
            iframe_handle = await page.query_selector("iframe[src*='disqus.com']")
            if not iframe_handle:
                return {"total_comments": 0, "comments": []}
            iframe = await iframe_handle.content_frame()
            if debug_dir and iframe:
                with open(os.path.join(debug_dir, "iframe.html"), "w", encoding="utf-8") as f:
                    f.write(await iframe.content())
            # ── 2. grab the raw data node inside the iframe ─────────────────
            try:
                raw_node = await iframe.wait_for_selector(
                    "#disqus-threadData",
                    state="attached",
                    timeout=timeout,
                )
                raw_text = await raw_node.inner_text()
                data = _extract_thread_data(raw_text, url, debug_dir)
            except (PWAsyncTimeout, json.JSONDecodeError, AttributeError, ValueError) as e:
                logging.warning(f"Data extraction failed: {e} - {url}")
                if debug_dir:
                    with open(os.path.join(debug_dir, "error.txt"), "w", encoding="utf-8") as f:
                        f.write(str(e))
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
            await browser.close()



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
