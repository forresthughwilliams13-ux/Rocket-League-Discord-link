import json
import os
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# =========================
# CONFIG
# =========================
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
NEWS_URL = "https://www.rocketleague.com/news"
STATUS_API_URL = "https://status.epicgames.com/api/v2/summary.json"
STATE_FILE = "rocket_league_state.json"
CHECK_INTERVAL_SECONDS = 600  # 10 minutes

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

# =========================
# HELPERS
# =========================
def ensure_env():
    if not WEBHOOK_URL:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL is not set. Add it in Railway > Service > Variables."
        )

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "seen_news_urls": [],
        "last_rl_status": None,
        "startup_message_sent": False,
    }

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def send_discord_message(content=None, embeds=None):
    payload = {
        "username": "Rocket League Updates",
    }

    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    response = requests.post(WEBHOOK_URL, json=payload, timeout=20)
    response.raise_for_status()

def fetch_html(url):
    session = requests.Session()

    # Warm up session on homepage first
    session.get("https://www.rocketleague.com/", headers=HEADERS, timeout=20)

    response = session.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    response.raise_for_status()
    return response.text

def fetch_json(url):
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.json()

# =========================
# NEWS PARSING
# =========================
def parse_news_page(html):
    soup = BeautifulSoup(html, "html.parser")
    items = []

    # Grab article links that live under /news/
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True)

        if not href:
            continue

        if href.startswith("/news/"):
            full_url = "https://www.rocketleague.com" + href
        elif href.startswith("https://www.rocketleague.com/news/"):
            full_url = href
        else:
            continue

        title = " ".join(text.split())
        if len(title) < 8:
            continue

        items.append({
            "url": full_url,
            "title": title
        })

    # De-duplicate while preserving order
    seen = set()
    deduped = []
    for item in items:
        if item["url"] not in seen:
            seen.add(item["url"])
            deduped.append(item)

    return deduped[:20]

def check_news(state):
    html = fetch_html(NEWS_URL)
    items = parse_news_page(html)

    new_items = [item for item in items if item["url"] not in state["seen_news_urls"]]

    # Post oldest first so Discord reads in order
    for item in reversed(new_items):
        embed = {
            "title": item["title"],
            "url": item["url"],
            "description": "New official Rocket League post detected.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "rocketleague.com"}
        }
        send_discord_message(
            content="🚗⚽ **New Rocket League update posted**",
            embeds=[embed]
        )
        state["seen_news_urls"].append(item["url"])

    # Keep state file from growing forever
    state["seen_news_urls"] = state["seen_news_urls"][-200:]

# =========================
# STATUS CHECKING
# =========================
def get_rocket_league_status(summary_json):
    components = summary_json.get("components", [])

    # Try exact Rocket League component first
    for component in components:
        if component.get("name") == "Rocket League":
            return {
                "name": component.get("name"),
                "status": component.get("status"),
            }

    # Fallback if naming changes
    for component in components:
        name = (component.get("name") or "").lower()
        if "rocket league" in name:
            return {
                "name": component.get("name"),
                "status": component.get("status"),
            }

    return {
        "name": "Rocket League",
        "status": "unknown",
    }

def humanize_status(status):
    mapping = {
        "operational": "Operational",
        "degraded_performance": "Degraded Performance",
        "partial_outage": "Partial Outage",
        "major_outage": "Major Outage",
        "under_maintenance": "Under Maintenance",
        "unknown": "Unknown",
    }
    return mapping.get(status, status.replace("_", " ").title())

def check_status(state):
    summary = fetch_json(STATUS_API_URL)
    rl_status = get_rocket_league_status(summary)
    current_status = rl_status["status"]

    if state["last_rl_status"] is None:
        state["last_rl_status"] = current_status
        return

    if current_status != state["last_rl_status"]:
        previous = humanize_status(state["last_rl_status"])
        current = humanize_status(current_status)

        embed = {
            "title": "Rocket League server status changed",
            "url": "https://status.epicgames.com/",
            "description": f"**Previous:** {previous}\n**Current:** {current}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "status.epicgames.com"}
        }
        send_discord_message(
            content="📡 **Rocket League status update**",
            embeds=[embed]
        )

        state["last_rl_status"] = current_status

# =========================
# MAIN LOOP
# =========================
def main():
    ensure_env()
    state = load_state()

    # Send one startup message the first time this deployment runs
    if not state.get("startup_message_sent"):
        send_discord_message("✅ Rocket League update bot is live.")
        state["startup_message_sent"] = True
        save_state(state)

    while True:
        try:
            print(f"[{datetime.now().isoformat()}] Checking Rocket League news and status...")
            check_news(state)
            check_status(state)
            save_state(state)
            print("Check complete.")
        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()


  
   
    
