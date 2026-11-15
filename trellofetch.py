import time
import re
import requests
import os
from dotenv import load_dotenv

load_dotenv()  # Or load_dotenv("your_file.env") if it has a custom name

API_KEY = os.getenv("API_KEY")
TOKEN = os.getenv("TOKEN")
BOARD_ID = os.getenv("BOARD_ID")
 # From https://trello.com/b/BOARD_ID/...
OUTPUT_FILE = "Trello_aotr.txt"

BASE_URL = "https://api.trello.com/1"
session = requests.Session()


def trello_get(endpoint, params=None, max_retries=5):
    """Fetches from Trello API while respecting rate-limit headers and HTTP 429s."""
    req_params = {"key": API_KEY, "token": TOKEN}
    if params:
        req_params.update(params)

    url = f"{BASE_URL}/{endpoint.lstrip('/')}"

    for attempt in range(1, max_retries + 1):
        response = session.get(url, params=req_params, timeout=30)

        # Handle HTTP 429 Rate Limit Exceeded
        if response.status_code == 429:
            wait_time = 10 * attempt
            print(f"[Rate Limit] 429 received. Cooling down for {wait_time}s...")
            time.sleep(wait_time)
            continue

        response.raise_for_status()

        # Check Trello's real-time rate limit headers
        token_rem = int(response.headers.get("x-rate-limit-api-token-remaining", 100))
        key_rem = int(response.headers.get("x-rate-limit-api-key-remaining", 300))

        if token_rem < 10 or key_rem < 15:
            print(f"[Rate Limit] Low quota (Token: {token_rem}). Pausing 10s...")
            time.sleep(10)
        else:
            # Polite pacing (~5 req/sec, well below the 100 req/10s cap)
            time.sleep(0.2)

        return response.json()

    raise RuntimeError(f"Failed to fetch {endpoint} after {max_retries} retries.")


def clean_text(text):
    """Normalizes line breaks and trims excessive blank lines."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def main():
    print("1. Fetching board details and lists...")
    board = trello_get(f"boards/{BOARD_ID}", {"fields": "name,desc,url,dateLastActivity"})
    raw_lists = trello_get(f"boards/{BOARD_ID}/lists", {"filter": "open", "fields": "name,pos"})
    lists = sorted(raw_lists, key=lambda x: x.get("pos", 0))

    print("2. Fetching board comments...")
    actions = trello_get(
        f"boards/{BOARD_ID}/actions",
        {"filter": "commentCard", "limit": 1000, "fields": "data,date,memberCreator"}
    )
    comments_by_card = {}
    for act in actions:
        card_id = act.get("data", {}).get("card", {}).get("id")
        if card_id:
            author = act.get("memberCreator", {}).get("fullName", "User")
            comment_text = clean_text(act.get("data", {}).get("text", ""))
            comments_by_card.setdefault(card_id, []).append(f"[{author}]: {comment_text}")

    print(f"3. Fetching cards across {len(lists)} lists (rate-limit safe)...")
    cards_by_list = {}
    total_cards = 0

    for idx, lst in enumerate(lists, 1):
        lst_id = lst["id"]
        lst_name = lst["name"].strip()
        print(f"   [{idx}/{len(lists)}] Pulling list: {lst_name}...")

        raw_cards = trello_get(
            f"lists/{lst_id}/cards",
            {
                "filter": "open",
                "fields": "name,desc,labels,due,dueComplete,shortUrl,pos",
                "checklists": "all",
                "attachments": "true",
                "attachment_fields": "name,url"
            }
        )
        sorted_cards = sorted(raw_cards, key=lambda x: x.get("pos", 0))
        cards_by_list[lst_id] = sorted_cards
        total_cards += len(sorted_cards)

    print(f"4. Formatting and writing {total_cards} cards to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        # --- BOARD HEADER ---
        f.write("=" * 70 + "\n")
        f.write(f"BOARD TITLE : {board.get('name', 'Unknown Board')}\n")
        f.write(f"BOARD URL   : {board.get('url', '')}\n")
        f.write(f"LAST ACTIVE : {board.get('dateLastActivity', '')}\n")
        f.write(f"TOTAL LISTS : {len(lists)} | TOTAL CARDS: {total_cards}\n")
        if board.get("desc"):
            f.write(f"DESCRIPTION : {clean_text(board['desc'])}\n")
        f.write("=" * 70 + "\n\n")

        # --- TABLE OF CONTENTS ---
        f.write("TABLE OF CONTENTS (LISTS):\n")
        for idx, lst in enumerate(lists, 1):
            count = len(cards_by_list.get(lst["id"], []))
            f.write(f"  {idx:02d}. {lst['name'].strip()} ({count} cards)\n")
        f.write("\n" + "=" * 70 + "\n\n")

        # --- LISTS & CARDS ---
        for lst in lists:
            lst_name = lst["name"].strip()
            cards = cards_by_list.get(lst["id"], [])

            f.write(f"# LIST: {lst_name.upper()}\n")
            f.write("=" * 70 + "\n\n")

            if not cards:
                f.write("(No cards in this list)\n\n")
                continue

            for card in cards:
                card_name = card.get("name", "Untitled Card").strip()
                f.write(f"## [{lst_name}] > {card_name}\n")
                f.write("-" * 50 + "\n")

                # Labels / Categories / Rarities
                labels = [
                    lbl["name"].strip() if lbl.get("name") else lbl.get("color", "unlabeled")
                    for lbl in card.get("labels", [])
                ]
                if labels:
                    f.write(f"Labels      : {', '.join(labels)}\n")

                # Due Date
                if card.get("due"):
                    status = "Complete" if card.get("dueComplete") else "Pending"
                    f.write(f"Due Date    : {card['due']} ({status})\n")

                # Card URL
                if card.get("shortUrl"):
                    f.write(f"Card Link   : {card['shortUrl']}\n")

                # Description
                desc = clean_text(card.get("desc", ""))
                if desc:
                    f.write("\nDescription:\n")
                    for line in desc.split("\n"):
                        f.write(f"  {line}\n")

                # Checklists (sorted by position)
                checklists = sorted(card.get("checklists", []), key=lambda x: x.get("pos", 0))
                for cl in checklists:
                    cl_name = cl.get("name", "Checklist").strip()
                    f.write(f"\nChecklist - {cl_name}:\n")
                    items = sorted(cl.get("checkItems", []), key=lambda x: x.get("pos", 0))
                    for item in items:
                        mark = "x" if item.get("state") == "complete" else " "
                        f.write(f"  [{mark}] {clean_text(item.get('name', ''))}\n")

                # Attachments / Reference Links
                attachments = card.get("attachments", [])
                if attachments:
                    f.write("\nAttachments / Links:\n")
                    for att in attachments:
                        att_name = att.get("name", "Link").strip()
                        att_url = att.get("url", "")
                        f.write(f"  - {att_name}: {att_url}\n")

                # Comments
                card_comments = comments_by_card.get(card["id"], [])
                if card_comments:
                    f.write("\nComments:\n")
                    for cmt in card_comments:
                        f.write(f"  * {cmt}\n")

                f.write("\n\n")

    print(f"Done! Successfully exported to '{OUTPUT_FILE}'.")


if __name__ == "__main__":
    main()
