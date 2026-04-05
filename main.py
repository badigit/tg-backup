import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from pytdbot import Client
import pytdbot.types as types

# --- Logging ---
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg-backup.log")

file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="w")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(message)s"))

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler])

log = logging.getLogger("tg-backup")
log.addHandler(console_handler)

logging.getLogger("pytdbot").setLevel(logging.WARNING)


def _excepthook(exc_type, exc_value, exc_tb):
    log.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _excepthook

load_dotenv()

API_ID = os.getenv("TG_API_ID")
API_HASH = os.getenv("TG_API_HASH")

if not API_ID or not API_HASH:
    log.error("TG_API_ID and TG_API_HASH must be set in .env or environment")
    sys.exit(1)

# --- CLI ---

parser = argparse.ArgumentParser(description="Export Telegram account structure to JSON")
parser.add_argument("-p", "--profile", default="default",
                    help="Session profile name (for multiple accounts)")
parser.add_argument("--single-file", action="store_true",
                    help="Export everything into a single JSON file instead of split by type")
parser.add_argument("--full", action="store_true",
                    help="Fetch full info for all contacts (birthdate etc). Slower.")
parser.add_argument("-o", "--output-dir", default=".",
                    help="Directory to save export (default: current)")
ARGS = parser.parse_args()

# --- Client ---

session_dir = os.path.join("td_data", ARGS.profile)

client = Client(
    api_id=int(API_ID),
    api_hash=API_HASH,
    database_encryption_key="tg-backup",
    files_directory=session_dir,
    td_verbosity=1,
    user_bot=True,
)

folders_data = {}  # folder_id -> {name, chat_ids}
folders_ready = asyncio.Event()


# --- Helpers ---


def get_username(obj):
    if hasattr(obj, "usernames") and obj.usernames:
        active = obj.usernames.active_usernames
        if active:
            return active[0]
    return None


def get_last_seen(user):
    """Extract last seen info from user status."""
    status = getattr(user, "status", None)
    if not status:
        return None
    if isinstance(status, types.UserStatusOnline):
        return "online"
    if isinstance(status, types.UserStatusOffline):
        if status.was_online:
            return datetime.fromtimestamp(status.was_online, tz=timezone.utc).isoformat()
    if isinstance(status, types.UserStatusRecently):
        return "recently"
    if isinstance(status, types.UserStatusLastWeek):
        return "last_week"
    if isinstance(status, types.UserStatusLastMonth):
        return "last_month"
    return None


def get_birthdate(user_full):
    bd = getattr(user_full, "birthdate", None)
    if not bd:
        return None
    parts = []
    if getattr(bd, "day", 0):
        parts.append(str(bd.day).zfill(2))
    if getattr(bd, "month", 0):
        parts.append(str(bd.month).zfill(2))
    if getattr(bd, "year", 0):
        parts.append(str(bd.year))
    return "-".join(parts) if parts else None


def get_last_message_date(chat):
    msg = getattr(chat, "last_message", None)
    if msg and getattr(msg, "date", None):
        return datetime.fromtimestamp(msg.date, tz=timezone.utc).isoformat()
    return None


def get_muted(entry_type, chat, scope_mute):
    ns = chat.notification_settings
    if not ns.use_default_mute_for:
        return ns.mute_for > 0
    if entry_type in ("user", "bot"):
        return scope_mute.get("private", False)
    if entry_type == "channel":
        return scope_mute.get("channel", False)
    return scope_mute.get("group", False)


def get_folder_name(folder):
    """ChatFolder.name -> ChatFolderName.text -> FormattedText.text -> str"""
    try:
        return folder.name.text.text
    except (AttributeError, TypeError):
        return str(folder.name) if folder.name else ""


async def fetch_private_info(ct, contact_ids):
    entry = {}
    user = await client.getUser(user_id=ct.user_id)
    if isinstance(user, types.Error):
        entry["type"] = "user"
        return entry

    is_bot = hasattr(user, "type") and isinstance(user.type, types.UserTypeBot)
    entry["type"] = "bot" if is_bot else "user"
    entry["name"] = f"{user.first_name or ''} {user.last_name or ''}".strip()
    entry["username"] = get_username(user)
    entry["phone"] = user.phone_number or None
    entry["is_premium"] = getattr(user, "is_premium", False)
    entry["last_seen"] = get_last_seen(user)

    user_full = await client.getUserFullInfo(user_id=ct.user_id)
    if not isinstance(user_full, types.Error):
        bio = getattr(user_full, "bio", None)
        if bio:
            entry["bio"] = bio.text if hasattr(bio, "text") else str(bio)
        entry["birthdate"] = get_birthdate(user_full)

    entry["is_contact"] = ct.user_id in contact_ids
    return entry


async def fetch_basic_group_info(ct):
    entry = {"type": "group"}
    bg_full = await client.getBasicGroupFullInfo(basic_group_id=ct.basic_group_id)
    if isinstance(bg_full, types.Error):
        return entry
    entry["description"] = bg_full.description or None
    entry["members_count"] = len(bg_full.members) if bg_full.members else None
    if bg_full.invite_link:
        entry["invite_link"] = bg_full.invite_link.invite_link
    return entry


async def fetch_supergroup_info(ct):
    entry = {}
    sg = await client.getSupergroup(supergroup_id=ct.supergroup_id)
    if isinstance(sg, types.Error):
        entry["type"] = "supergroup"
        return entry

    entry["type"] = "channel" if sg.is_channel else "supergroup"
    entry["username"] = get_username(sg)
    entry["members_count"] = sg.member_count or None

    sg_full = await client.getSupergroupFullInfo(supergroup_id=ct.supergroup_id)
    if not isinstance(sg_full, types.Error):
        entry["description"] = sg_full.description or None
        if not entry["members_count"]:
            entry["members_count"] = sg_full.member_count or None
        if sg_full.invite_link:
            entry["invite_link"] = sg_full.invite_link.invite_link
        linked = getattr(sg_full, "linked_chat_id", 0)
        if linked:
            entry["linked_chat_id"] = linked
    return entry


# --- Handlers ---


@client.on_updateAuthorizationState()
async def on_auth_state(client, update):
    state = update.authorization_state

    if isinstance(state, types.AuthorizationStateWaitPhoneNumber):
        phone = await asyncio.to_thread(input, "Enter phone number: ")
        result = await client.setAuthenticationPhoneNumber(phone_number=phone)
        if isinstance(result, types.Error):
            log.error("Auth error: %s", result.message)
            await client.stop()

    elif isinstance(state, types.AuthorizationStateWaitCode):
        code = await asyncio.to_thread(input, "Enter confirmation code: ")
        result = await client.checkAuthenticationCode(code=code)
        if isinstance(result, types.Error):
            log.error("Auth error: %s", result.message)
            await client.stop()

    elif isinstance(state, types.AuthorizationStateWaitPassword):
        password = await asyncio.to_thread(input, "Enter 2FA password: ")
        result = await client.checkAuthenticationPassword(password=password)
        if isinstance(result, types.Error):
            log.error("Auth error: %s", result.message)
            await client.stop()

    elif isinstance(state, types.AuthorizationStateReady):
        me = await client.getMe()
        if not isinstance(me, types.Error):
            log.info("Logged in as %s (id=%d)", me.first_name, me.id)
            _save_profile(me)
        else:
            log.info("Logged in")
        asyncio.ensure_future(export_data())


@client.on_updateChatFolders()
async def on_folders(client, update):
    for fi in update.chat_folders:
        folder = await client.getChatFolder(chat_folder_id=fi.id)
        if isinstance(folder, types.Error):
            continue
        chat_ids = set()
        for id_list in (folder.included_chat_ids, folder.pinned_chat_ids):
            if id_list:
                chat_ids.update(id_list)
        folders_data[fi.id] = {"name": get_folder_name(folder), "chat_ids": chat_ids}
    folders_ready.set()


# --- Export ---


async def export_data():
    t0 = time.monotonic()
    log.info("=== Starting export ===")
    try:
        await _do_export()
    except Exception:
        log.exception("Export failed")
    finally:
        elapsed = time.monotonic() - t0
        log.info("Finished in %.1fs", elapsed)
        await client.stop()


async def _do_export():
    # Folders
    log.info("Waiting for folders...")
    try:
        await asyncio.wait_for(folders_ready.wait(), timeout=5)
        log.info("Got %d folder(s)", len(folders_data))
    except asyncio.TimeoutError:
        log.info("No folders (timeout)")

    chat_folders_map = {}
    for fid, fdata in folders_data.items():
        for cid in fdata["chat_ids"]:
            chat_folders_map.setdefault(cid, []).append(fdata["name"])

    # Scope mute defaults
    log.info("Fetching notification defaults...")
    scope_mute = {}
    for scope_cls, key in [
        (types.NotificationSettingsScopePrivateChats, "private"),
        (types.NotificationSettingsScopeGroupChats, "group"),
        (types.NotificationSettingsScopeChannelChats, "channel"),
    ]:
        result = await client.getScopeNotificationSettings(scope=scope_cls())
        scope_mute[key] = not isinstance(result, types.Error) and result.mute_for > 0

    # Contacts
    log.info("Fetching contacts...")
    contacts_result = await client.getContacts()
    contact_ids = set()
    if not isinstance(contacts_result, types.Error):
        contact_ids = set(contacts_result.user_ids)

    # All chats (main + archive)
    log.info("Fetching chat list...")
    all_chat_ids = []
    for chat_list in [types.ChatListMain(), types.ChatListArchive()]:
        while True:
            result = await client.getChats(chat_list=chat_list, limit=100)
            if isinstance(result, types.Error) or not result.chat_ids:
                break
            all_chat_ids.extend(result.chat_ids)
            if len(result.chat_ids) < 100:
                break

    # Deduplicate
    seen = set()
    unique_chat_ids = [cid for cid in all_chat_ids if cid not in seen and not seen.add(cid)]

    total = len(unique_chat_ids)
    log.info("Found %d dialogs, %d contacts. Processing...", total, len(contact_ids))

    # Process each dialog
    dialogs = []
    for i, chat_id in enumerate(unique_chat_ids, 1):
        chat = await client.getChat(chat_id=chat_id)
        if isinstance(chat, types.Error):
            log.warning("Failed to get chat %d: %s", chat_id, chat.message)
            continue

        entry = {
            "id": chat.id,
            "title": chat.title,
            "unread_count": chat.unread_count,
            "last_message_date": get_last_message_date(chat),
            "is_archived": _is_archived(chat),
            "folders": chat_folders_map.get(chat_id, []),
        }

        ct = chat.type
        if isinstance(ct, types.ChatTypePrivate):
            entry.update(await fetch_private_info(ct, contact_ids))
        elif isinstance(ct, types.ChatTypeBasicGroup):
            entry.update(await fetch_basic_group_info(ct))
        elif isinstance(ct, types.ChatTypeSupergroup):
            entry.update(await fetch_supergroup_info(ct))
        elif isinstance(ct, types.ChatTypeSecret):
            entry["type"] = "user"

        entry["is_muted"] = get_muted(entry.get("type", "user"), chat, scope_mute)
        dialogs.append(entry)

        if i % 20 == 0 or i == total:
            log.info("  %d/%d dialogs", i, total)

    # Contacts not in dialogs
    extra_contact_ids = contact_ids - {d["id"] for d in dialogs if d.get("type") in ("user", "bot")}
    extra_total = len(extra_contact_ids)
    log.info("Fetching %d extra contacts...", extra_total)

    contacts = []
    for i, uid in enumerate(extra_contact_ids, 1):
        user = await client.getUser(user_id=uid)
        if isinstance(user, types.Error):
            continue
        c = {
            "id": user.id,
            "type": "user",
            "name": f"{user.first_name or ''} {user.last_name or ''}".strip(),
            "username": get_username(user),
            "phone": user.phone_number or None,
            "is_premium": getattr(user, "is_premium", False),
            "last_seen": get_last_seen(user),
            "is_contact": True,
        }
        if ARGS.full:
            user_full = await client.getUserFullInfo(user_id=uid)
            if not isinstance(user_full, types.Error):
                c["birthdate"] = get_birthdate(user_full)
        contacts.append(c)
        if i % 100 == 0 or i == extra_total:
            log.info("  %d/%d contacts", i, extra_total)

    # Own profile
    log.info("Fetching own profile...")
    my_profile = None
    if client.me:
        me = client.me
        my_profile = {
            "id": me.id,
            "first_name": me.first_name or "",
            "last_name": getattr(me, "last_name", "") or "",
            "username": get_username(me),
            "phone": me.phone_number or None,
            "is_premium": getattr(me, "is_premium", False),
        }
        me_full = await client.getUserFullInfo(user_id=me.id)
        if not isinstance(me_full, types.Error):
            bio = getattr(me_full, "bio", None)
            if bio:
                my_profile["bio"] = bio.text if hasattr(bio, "text") else str(bio)
            my_profile["birthdate"] = get_birthdate(me_full)

    # Write output
    now = datetime.now(timezone.utc)
    meta = {
        "exported_at": now.isoformat(),
        "account_id": client.me.id if client.me else None,
        "my_profile": my_profile,
        "dialogs_count": len(dialogs),
        "extra_contacts_count": len(contacts),
    }

    if ARGS.single_file:
        _write_single(now, meta, dialogs, contacts)
    else:
        _write_split(now, meta, dialogs, contacts)


def _dump(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _export_name(now):
    """Build export base name, e.g. tg-backup-2026-04-04 or tg-backup-work-2026-04-04."""
    date = now.strftime("%Y-%m-%d")
    if ARGS.profile == "default":
        return f"tg-backup-{date}"
    return f"tg-backup-{ARGS.profile}-{date}"


def _write_single(now, meta, dialogs, contacts):
    output = {
        **meta,
        "folders": {str(fid): fdata["name"] for fid, fdata in folders_data.items()},
        "dialogs": dialogs,
        "contacts_not_in_dialogs": contacts,
    }
    filename = os.path.join(ARGS.output_dir, f"{_export_name(now)}.json")
    _dump(filename, output)
    log.info("=== Done! %d dialogs + %d contacts -> %s ===",
             len(dialogs), len(contacts), filename)


def _write_split(now, meta, dialogs, contacts):
    outdir = os.path.join(ARGS.output_dir, _export_name(now))
    os.makedirs(outdir, exist_ok=True)

    _dump(os.path.join(outdir, "meta.json"), meta)
    _dump(os.path.join(outdir, "folders.json"),
          {str(fid): fdata["name"] for fid, fdata in folders_data.items()})

    # Split dialogs by type
    by_type = {}
    for d in dialogs:
        by_type.setdefault(d.get("type", "other"), []).append(d)

    type_to_file = {
        "user": "users.json",
        "bot": "bots.json",
        "group": "groups.json",
        "supergroup": "supergroups.json",
        "channel": "channels.json",
    }
    for t, items in by_type.items():
        fname = type_to_file.get(t, f"{t}.json")
        _dump(os.path.join(outdir, fname), items)
        log.info("  %s: %d", fname, len(items))

    _dump(os.path.join(outdir, "contacts.json"), contacts)
    log.info("  contacts.json: %d", len(contacts))
    log.info("=== Done! Exported to %s/ ===", outdir)


def _save_profile(me):
    """Save minimal profile info to session directory for identification."""
    profile = {
        "id": me.id,
        "name": f"{me.first_name or ''} {getattr(me, 'last_name', '') or ''}".strip(),
        "phone": me.phone_number or None,
        "username": get_username(me),
    }
    path = os.path.join(session_dir, "profile.json")
    _dump(path, profile)
    log.info("Profile saved to %s", path)


def _is_archived(chat):
    for attr in ("positions", "chat_lists"):
        items = getattr(chat, attr, None)
        if items and any(isinstance(x if attr == "chat_lists" else x.list,
                                    types.ChatListArchive) for x in items):
            return True
    return False


if __name__ == "__main__":
    client.run()
