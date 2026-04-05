# tg-backup

## Goal

Python CLI tool to export Telegram account structure (metadata only, no message content) to JSON.

## What to export

For each dialog (chat/channel/group/user/bot):
- `id` — Telegram ID
- `type` — one of: `user`, `bot`, `group`, `supergroup`, `channel`
- `title` or `name` — display name
- `username` — @username if exists
- `description` / `bio` — if available
- `invite_link` — if available
- `members_count` — if available
- `folder` — which folder(s) this dialog belongs to (if any)
- `is_archived` — whether it's in Archive
- `is_muted` — mute status
- `unread_count` — number of unread messages

For contacts specifically, also export:
- `phone` — phone number
- `first_name`, `last_name`

## Tech stack

- Python 3.11+
- **pytdbot** (`pip install pytdbot`) — async TDLib wrapper, actively maintained
- Output: JSON file with timestamp in name (e.g. `tg-backup-2026-04-03.json`)
- Config: `api_id` and `api_hash` from environment variables `TG_API_ID` and `TG_API_HASH`
  - Get credentials at https://my.telegram.org

## Structure

Keep it simple — single `main.py` file unless it grows beyond ~300 lines.

```
tg-backup/
  main.py          # main script
  requirements.txt # pytdbot
  .env.example     # template for credentials
  README.md        # usage instructions
```

## Usage flow

1. User sets `TG_API_ID` and `TG_API_HASH` in `.env` or environment
2. First run: TDLib asks for phone number + confirmation code (interactive)
3. Session is saved locally (subsequent runs are automatic)
4. Script fetches all dialogs, contacts, folders → writes JSON

## Important notes

- This is a READ-ONLY tool — never send messages, join/leave chats, or modify anything
- Use TDLib's official methods, not undocumented hacks
- Handle rate limits gracefully (TDLib handles this internally)
- The `td_data/` directory stores TDLib session — add to `.gitignore`
