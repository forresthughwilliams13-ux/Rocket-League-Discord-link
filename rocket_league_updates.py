
import json
import os
import time
import feedparser
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = os.getenv("https://discord.com/api/webhooks/1482101116983447753/yqAsBHkyK3Reh4B78jYjPcx3HAWtEw4s0OzrsDwP81tXHgPJQFWnSChgWMRC1pIX9e_0")
NEWS_URL = "https://www.rocketleague.com/news"
STATUS_API_URL = "https://status.epicgames.com/api/v2/summary.json"
STATE_FILE = "rocket_league_state.json"
CHECK_INTERVAL_SECONDS = 600

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.rocketleague.com/",
}

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] Could not read state file: {e}")

    return {
        "seen_news_urls": [],
        "last_rl_status": None,
        "startup_message_sent": False,
    }


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[WARN] Could not save state file: {e}")


def send_discord_message(content: str | None = None, embeds: list | None = None) -> None:
    if not WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is missing.")

    payload = {"username": "Rocket League Updates"}

    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    response = requests.post(WEBHOOK_URL, json=payload, timeout=20)
    print(f"[DEBUG] Discord response: {response.status_code}")
    print(f"[DEBUG] Discord body: {response.text[:300]}")
    response.raise_for_status()


def fetch_html(url: str) -> str:
    session = requests.Session()

    # Warm up session first
    try:
        home = session.get("https://www.rocketleague.com/", headers=HEADERS, timeout=20)
        print(f"[DEBUG] Homepage fetch -> {home.status_code}")
    except Exception as e:
        print(f"[WARN] Homepage warmup failed: {e}")

    response = session.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    print(f"[DEBUG] Fetch HTML {url} -> {response.status_code}")
    response.raise_for_status()
    return response.text


def fetch_json(url: str) -> dict:
    response = requests.get(url, timeout=20)
    print(f"[DEBUG] Fetch JSON {url} -> {response.status_code}")
    response.raise_for_status()
    return response.json()


def parse_news_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True)

        if href.startswith("/news/"):
            full_url = "https://www.rocketleague.com" + href
        elif href.startswith("https://www.rocketleague.com/news/"):
            full_url = href
        else:
            continue

        title = " ".join(text.split())
        if len(title) < 8:
            continue

        items.append({"url": full_url, "title": title})

    seen = set()
    deduped = []
    for item in items:
        if item["url"] not in seen:
            seen.add(item["url"])
            deduped.append(item)

    print(f"[DEBUG] Parsed news items: {len(deduped)}")
    return deduped[:20]

def check_news(state):
    try:
        feed = feedparser.parse("https://www.rocketleague.com/rss")

        new_posts = []

        for entry in feed.entries:
            url = entry.link

            if url not in state["seen_news_urls"]:
                new_posts.append(entry)

        print(f"[DEBUG] New news items: {len(new_posts)}")

        for entry in reversed(new_posts):

            send_discord_message(
                f"📰 **New Rocket League Update**\n"
                f"**{entry.title}**\n"
                f"{entry.link}"
            )

            state["seen_news_urls"].append(entry.link)

    except Exception as e:
        print(f"[ERROR] check_news failed: {e}")

def get_rocket_league_status(summary_json: dict) -> str:
    components = summary_json.get("components", [])

    for component in components:
        if component.get("name") == "Rocket League":
            return component.get("status", "unknown")

    for component in components:
        name = (component.get("name") or "").lower()
        if "rocket league" in name:
            return component.get("status", "unknown")

    return "unknown"


def humanize_status(status: str) -> str:
    mapping = {
        "operational": "Operational",
        "degraded_performance": "Degraded Performance",
        "partial_outage": "Partial Outage",
        "major_outage": "Major Outage",
        "under_maintenance": "Under Maintenance",
        "unknown": "Unknown",
    }
    return mapping.get(status, status.replace("_", " ").title())


def check_status(state: dict) -> None:
    try:
        summary = fetch_json(STATUS_API_URL)
        current_status = get_rocket_league_status(summary)

        print(f"[DEBUG] Current RL status: {current_status}")

        if state["last_rl_status"] is None:
            state["last_rl_status"] = current_status
            return

        if current_status != state["last_rl_status"]:
            previous = humanize_status(state["last_rl_status"])
            current = humanize_status(current_status)

            embed = {
                "title": "Rocket League server status changed",
                "url": "https://status.epicgames.com/",
                "description": f"Previous: {previous}\nCurrent: {current}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": "status.epicgames.com"},
            }

            send_discord_message(
                content="📡 Rocket League status update",
                embeds=[embed],
            )

            state["last_rl_status"] = current_status
    except Exception as e:
        print(f"[ERROR] check_status failed: {e}")


def main() -> None:
    state = load_state()

    print("Webhook loaded:", bool(WEBHOOK_URL))

    if not state.get("startup_message_sent"):
        try:
            send_discord_message("🚀 Rocket League update bot is live")
            state["startup_message_sent"] = True
            save_state(state)
        except Exception as e:
            print(f"[ERROR] Startup Discord message failed: {e}")

    while True:
        try:
            print(f"[{datetime.now().isoformat()}] Checking Rocket League news and status...")
            check_news(state)
            check_status(state)
            save_state(state)
            print("[DEBUG] Check complete.")
        except Exception as e:
            print(f"[ERROR] Main loop failed: {e}")

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

  
   
    
