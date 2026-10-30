"""Read one week of a group chat out of a copy of the macOS Messages database (chat.db)."""

import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"

# Apple stores dates as time since 2001-01-01 (nanoseconds on macOS 10.13+, seconds before that).
APPLE_EPOCH = 978307200

# associated_message_type values: 2000-2006 add a tapback, 3000-3006 remove it.
TAPBACK_TYPES = {
    2000: "love",
    2001: "like",
    2002: "dislike",
    2003: "laugh",
    2004: "emphasize",
    2005: "question",
    2006: "emoji",
}

UNKNOWN_NAME = "A Mysterious Stranger"
ME = "me"


@dataclass
class Message:
    rowid: int
    guid: str
    sender: str
    text: str
    sent_at: datetime
    tapbacks: dict = field(default_factory=dict)  # kind -> count

    @property
    def tapback_total(self) -> int:
        return sum(self.tapbacks.values())


@dataclass
class Week:
    chat_guid: str
    chat_name: str
    start: datetime
    end: datetime
    messages: list  # content messages only, oldest first
    skipped_empty: int  # rows that looked like messages but had no decodable text


def apple_date_to_datetime(value: int) -> datetime:
    seconds = value / 1e9 if value > 1e11 else value
    return datetime.fromtimestamp(seconds + APPLE_EPOCH, tz=timezone.utc)


def datetime_to_apple_date(when: datetime) -> int:
    return int((when.timestamp() - APPLE_EPOCH) * 1e9)


def decode_attributed_body(blob) -> str:
    """Pull the plain text out of an NSAttributedString typedstream blob.

    Recent macOS often leaves message.text NULL and keeps the text here. The string follows
    the NSString class name, a 5-byte header, then a length (1 byte, or 0x81 + 2 bytes,
    or 0x82 + 4 bytes, little-endian).
    """
    if not blob:
        return ""
    marker = blob.find(b"NSString")
    if marker == -1:
        return ""
    rest = blob[marker + len(b"NSString") + 5:]
    if not rest:
        return ""
    if rest[0] == 0x81:
        length, start = int.from_bytes(rest[1:3], "little"), 3
    elif rest[0] == 0x82:
        length, start = int.from_bytes(rest[1:5], "little"), 5
    else:
        length, start = rest[0], 1
    return rest[start:start + length].decode("utf-8", errors="replace")


def message_text(text, attributed_body) -> str:
    """Latest visible text of a message. The U+FFFC character stands in for an attachment.

    attributedBody is preferred because it holds the edited text when a message was edited.
    """
    value = decode_attributed_body(attributed_body) or text
    return (value or "").replace("￼", "").strip()


def copy_database(db_path: Path) -> Path:
    """Copy chat.db (and its WAL files) somewhere temporary so we never touch the live database."""
    tmp = Path(tempfile.mkdtemp(prefix="groupchat-"))
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(db_path) + suffix)
        if source.exists():
            shutil.copy2(source, tmp / (db_path.name + suffix))
    return tmp / db_path.name


def open_copy(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    copy = copy_database(Path(db_path))
    conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_names(path) -> dict:
    if not path or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _columns(conn, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def list_chats(conn, groups_only: bool = True) -> list:
    """Chats, newest activity first. By default only chats with more than one other person."""
    rows = conn.execute(
        """
        SELECT c.guid, c.display_name, c.chat_identifier,
               (SELECT COUNT(*) FROM chat_handle_join chj WHERE chj.chat_id = c.ROWID) AS people,
               (SELECT MAX(m.date) FROM chat_message_join cmj
                  JOIN message m ON m.ROWID = cmj.message_id
                 WHERE cmj.chat_id = c.ROWID) AS last_date
        FROM chat c
        """
    ).fetchall()
    chats = [
        {
            "guid": r["guid"],
            "name": r["display_name"] or r["chat_identifier"],
            "people": r["people"],
            "last_message": apple_date_to_datetime(r["last_date"]) if r["last_date"] else None,
        }
        for r in rows
        if not groups_only or (r["people"] or 0) > 1
    ]
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(chats, key=lambda c: c["last_message"] or epoch, reverse=True)


def read_week(conn, chat_guid: str, names: dict, days: int = 7, now: datetime = None) -> Week:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    chat = conn.execute(
        "SELECT ROWID, display_name, chat_identifier FROM chat WHERE guid = ?", (chat_guid,)
    ).fetchone()
    if chat is None:
        raise LookupError(f"No chat with guid {chat_guid!r}. Run `python -m groupchat chats` to list them.")

    cols = _columns(conn, "message")
    optional = [c if c in cols else f"0 AS {c}" for c in ("item_type", "date_retracted", "date_edited")]
    rows = conn.execute(
        f"""
        SELECT m.ROWID, m.guid, m.text, m.attributedBody, m.date, m.is_from_me,
               m.associated_message_guid, m.associated_message_type,
               {", ".join(optional)}, h.id AS handle
        FROM chat_message_join cmj
        JOIN message m ON m.ROWID = cmj.message_id
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE cmj.chat_id = ? AND m.date >= ? AND m.date < ?
        ORDER BY m.date, m.ROWID
        """,
        (chat["ROWID"], datetime_to_apple_date(start), datetime_to_apple_date(now)),
    ).fetchall()

    def name_for(row) -> str:
        key = ME if row["is_from_me"] else row["handle"]
        return names.get(key, "Me" if key == ME else UNKNOWN_NAME)

    messages, by_guid, reactions, skipped = [], {}, {}, 0
    for row in rows:
        kind = row["associated_message_type"] or 0
        if kind:
            if 2000 <= kind <= 2006 or 3000 <= kind <= 3006:
                # "p:0/GUID" or "bp:GUID" -> GUID
                target = (row["associated_message_guid"] or "").split("/")[-1].split(":")[-1]
                reactor = ME if row["is_from_me"] else row["handle"]
                tapback = TAPBACK_TYPES[kind % 1000 + 2000]
                key = (target, reactor, tapback)
                if kind < 3000:
                    reactions[key] = True
                else:
                    reactions.pop(key, None)
            continue
        if row["item_type"] or row["date_retracted"]:
            continue  # group events (renames, joins) and unsent messages never count
        text = message_text(row["text"], row["attributedBody"])
        if not text:
            skipped += 1  # attachment-only, sticker, or a body we couldn't decode
            continue
        message = Message(
            rowid=row["ROWID"],
            guid=row["guid"],
            sender=name_for(row),
            text=text,
            sent_at=apple_date_to_datetime(row["date"]),
        )
        messages.append(message)
        by_guid[message.guid] = message

    for target, _reactor, tapback in reactions:
        message = by_guid.get(target)
        if message:
            message.tapbacks[tapback] = message.tapbacks.get(tapback, 0) + 1

    return Week(
        chat_guid=chat_guid,
        chat_name=chat["display_name"] or chat["chat_identifier"],
        start=start,
        end=now,
        messages=messages,
        skipped_empty=skipped,
    )
