import json
import os
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = "https://discord.com/api/webhooks/1481941481605431388/ywDVOnXcd8cZHD2Zi6RP3djnfd57xyRMIR7uCFA65_QHuss3qVuRRtyRFJuxbDpYpAw_"

NEWS_URL = "https://www.rocketleague.com/en/news"
PATCH_URL = "https://www.rocketleague.com/news/tag/patch-notes"
STATUS_URL = "https://status.epicgames.com/"

STATE_FILE = "rocket_league_seen.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RocketLeagueDiscordUpdater/1.0)"
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_news": [], "last_status": ""}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def send_discord_message(content=None, embeds=None):
    payload = {
        "username": "Rocket League Updates",
        "avatar_url": "https://www.rocketleague.com/images/meta/rl_og_image.jpg",
    }
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    r = requests.post(WEBHOOK_URL, json=payload, timeout=20)
    r.raise_for_status()


def fetch_page(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def parse_news_items(html, base_url="https://www.rocketleague.com"):
    soup = BeautifulSoup(html, "html.parser")
    items = []

    # Finds article links on RL news pages
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)

        if not text:
            continue

        if "/news/" in href or href.startswith("/news"):
            full_url = href if href.startswith("http") else base_url.rstrip("/") + href
            title = text.strip()

            if len(title) < 8:
                continue

            items.append({
                "id": full_url,
                "title": title,
                "url": full_url
            })

    # De-duplicate while keeping order
    seen = set()
    unique = []
    for item in items:
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)

    return unique[:10]


def parse_epic_status(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    # Keep this simple and robust
    if "Rocket League Operational" in text:
        return "Operational"
    if "Rocket League" in text:
        # fallback if wording changes
        idx = text.find("Rocket League")
        snippet = text[idx:idx + 200]
        return snippet
    return "Unknown"


def check_news(state):
    new_posts = []

    for url, label in [
        (NEWS_URL, "News"),
        (PATCH_URL, "Patch Notes"),
    ]:
        html = fetch_page(url)
        items = parse_news_items(html)

        for item in items:
            if item["id"] not in state["seen_news"]:
                new_posts.append((label, item))

    # Deduplicate across both pages
    deduped = []
    seen_ids = set()
    for label, item in new_posts:
        if item["id"] not in seen_ids:
            seen_ids.add(item["id"])
            deduped.append((label, item))

    # Post oldest first if multiple new ones appear
    deduped.reverse()

    for label, item in deduped:
        embed = {
            "title": item["title"],
            "url": item["url"],
            "description": f"New Rocket League {label.lower()} update posted.",
            "footer": {"text": "Official Rocket League source"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        send_discord_message(
            content="🚗⚽ New Rocket League update detected!",
            embeds=[embed]
        )
        state["seen_news"].append(item["id"])

    # Keep state from growing forever
    state["seen_news"] = state["seen_news"][-200:]


def check_status(state):
    html = fetch_page(STATUS_URL)
    current_status = parse_epic_status(html)

    if not state["last_status"]:
        state["last_status"] = current_status
        return

    if current_status != state["last_status"]:
        embed = {
            "title": "Rocket League Service Status Changed",
            "url": STATUS_URL,
            "description": f"Previous: **{state['last_status']}**\nCurrent: **{current_status}**",
            "footer": {"text": "Epic Games Public Status"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        send_discord_message(
            content="📡 Rocket League server status update detected!",
            embeds=[embed]
        )
        state["last_status"] = current_status


def main():
    state = load_state()

    while True:
        try:
            check_news(state)
            check_status(state)
            save_state(state)
        except Exception as e:
            print(f"[ERROR] {e}")

        # Check every 10 minutes
        time.sleep(600)


if __name__ == "__main__":
    main()
