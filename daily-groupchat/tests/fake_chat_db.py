"""Build a small fake chat.db with the same tables and quirks as the real Messages database."""

import sqlite3
from datetime import datetime, timedelta, timezone

from groupchat.extract import datetime_to_apple_date

GROUP_GUID = "iMessage;+;chat000000000000000001"
OTHER_GUID = "iMessage;-;+15550000009"
NOW = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)

HANDLES = {1: "+15550000001", 2: "+15550000002", 3: "jake@example.com", 4: "+15550000004"}
NAMES = {"me": "Kira", "+15550000001": "Priya", "+15550000002": "Maya", "jake@example.com": "Jake"}
# +15550000004 is deliberately missing from NAMES -> "A Mysterious Stranger"


def attributed_body(text: str) -> bytes:
    """Encode text the way NSAttributedString typedstream blobs store it."""
    data = text.encode("utf-8")
    if len(data) < 0x80:
        length = bytes([len(data)])
    else:
        length = b"\x81" + len(data).to_bytes(2, "little")
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + length + data + b"\x86\x84\x02iI\x01"


def build(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB, handle_id INTEGER,
            date INTEGER, is_from_me INTEGER, associated_message_guid TEXT,
            associated_message_type INTEGER DEFAULT 0, item_type INTEGER DEFAULT 0,
            cache_has_attachments INTEGER DEFAULT 0, date_edited INTEGER DEFAULT 0,
            date_retracted INTEGER DEFAULT 0);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER, message_date INTEGER);
        """
    )
    conn.executemany("INSERT INTO handle VALUES (?, ?)", HANDLES.items())
    conn.execute("INSERT INTO chat VALUES (1, ?, 'chat000000000000000001', 'Brunch Bandits')", (GROUP_GUID,))
    conn.execute("INSERT INTO chat VALUES (2, ?, '+15550000009', NULL)", (OTHER_GUID,))
    conn.executemany("INSERT INTO chat_handle_join VALUES (1, ?)", [(1,), (2,), (3,), (4,)])

    rowid = 0

    def add(chat, handle, text, hours_ago, *, from_me=0, body=False, kind=0, target=None,
            item_type=0, attach=0, edited=0, retracted=0, original=None):
        nonlocal rowid
        rowid += 1
        guid = f"GUID-{rowid}"
        when = datetime_to_apple_date(NOW - timedelta(hours=hours_ago))
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, date, is_from_me,"
            " associated_message_guid, associated_message_type, item_type, cache_has_attachments,"
            " date_edited, date_retracted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rowid, guid, original if body else text, attributed_body(text) if body and text else None,
             0 if from_me else handle, when, from_me, target, kind, item_type, attach, edited, retracted),
        )
        conn.execute("INSERT INTO chat_message_join VALUES (?, ?, ?)", (chat, rowid, when))
        return guid

    # Outside the 7-day window: must be ignored.
    add(1, 1, "this is from two weeks ago", 24 * 14)

    tupperware = add(1, 3, "the tupperware is basically returned, it's in the car", 100)
    add(1, 2, "jake it has been in the car since september", 99)
    sleepy = add(1, 1, "i am simply not a morning person or an afternoon person", 80, body=True)
    add(1, 2, "brunch at 11 saturday??", 60)
    add(1, 0, "i will be there at 11:40", 59, from_me=1)
    add(1, 2, "💀💀💀", 58)
    add(1, 4, "who is this chat", 50)
    add(1, 2, "ok but what if we all moved to lisbon", 40)
    # Edited message: attributedBody holds the latest text, text column the original.
    add(1, 3, "final answer: no", 30, body=True, edited=1, original="final answer: yes")
    # Unsent message: must never appear.
    add(1, 1, "delete this, wrong chat", 20, retracted=datetime_to_apple_date(NOW))
    # Group event (rename) and attachment-only rows: never messages.
    add(1, 2, None, 15, item_type=2)
    add(1, 1, "￼", 14, attach=1)

    # Tapbacks on Priya's line: 4 loves, 3 laughs with one later removed -> 2.
    for handle in (2, 3, 4):
        add(1, handle, None, 79, kind=2000, target=f"p:0/{sleepy}")
    add(1, 0, None, 79, from_me=1, kind=2000, target=f"p:0/{sleepy}")
    for handle in (2, 3):
        add(1, handle, None, 78, kind=2003, target=f"p:0/{sleepy}")
    add(1, 4, None, 78, kind=2003, target=f"bp:{sleepy}")
    add(1, 4, None, 77, kind=3003, target=f"bp:{sleepy}")
    add(1, 2, None, 98, kind=2003, target=f"p:0/{tupperware}")

    # A different chat in the same window: must be ignored.
    add(2, 1, "secret plans in another chat", 10)

    conn.commit()
    conn.close()
