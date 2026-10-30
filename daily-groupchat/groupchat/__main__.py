"""The Daily Groupchat: turn a week of an Apple Messages group chat into a one-page newspaper.

    python -m groupchat chats                 list group chats and their GUIDs
    python -m groupchat peek                  week summary + most-loved messages (no API call)
    python -m groupchat rows --chat GUID      raw recent rows of any chat, for debugging
    python -m groupchat issue                 write this week's issue (calls Claude)
    python -m groupchat issue --sample        render a canned issue from real stats (no API call)
    python -m groupchat mark-posted FILE      record an issue as posted to the group
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import extract, render, stats, verify, write

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"


def load_config(path: Path) -> dict:
    config = {"db": str(extract.DEFAULT_DB), "names": "names.json", "issues_dir": "issues", "days": 7}
    if path.exists():
        with open(path, "rb") as f:
            config.update(tomllib.load(f))
    for key in ("names", "issues_dir"):
        if not Path(config[key]).is_absolute():
            config[key] = str(path.parent / config[key])
    return config


def posted_log(issues_dir: Path) -> list:
    log = issues_dir / "posted.json"
    return json.loads(log.read_text(encoding="utf-8")) if log.exists() else []


def previous_issue(issues_dir: Path):
    log = posted_log(issues_dir)
    if not log:
        return None
    path = Path(log[-1]["json"])
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_week(config: dict, chat_guid: str = None) -> extract.Week:
    chat_guid = chat_guid or config.get("chat_guid")
    if not chat_guid:
        raise SystemExit("No chat_guid in config.toml. Run `python -m groupchat chats` and copy one in.")
    conn = extract.open_copy(Path(config["db"]).expanduser())
    names = extract.load_names(config["names"])
    return extract.read_week(conn, chat_guid, names, days=int(config["days"]))


def cmd_chats(config, args):
    conn = extract.open_copy(Path(config["db"]).expanduser())
    for chat in extract.list_chats(conn, groups_only=not args.all):
        last = chat["last_message"].astimezone().strftime("%Y-%m-%d") if chat["last_message"] else "never"
        print(f"{chat['guid']}\n    {chat['name']}  ·  {chat['people']} people  ·  last message {last}")


def cmd_peek(config, args):
    week = load_week(config, args.chat)
    s = stats.compute(week, issue_number=len(posted_log(Path(config["issues_dir"]))) + 1)
    print(f"{week.chat_name}: {s.total} messages from {week.start.astimezone():%b %d} to {week.end.astimezone():%b %d}")
    print(f"Rows skipped (attachment-only or undecodable text): {week.skipped_empty}")
    for name, count, share in s.per_person:
        print(f"  {name:<24} {count:>5}  {share:5.0%}")
    print(f"Sent 2-5am: {s.late_night}   Top emoji: {s.top_emoji or '-'}   Funniest: {s.funniest or '-'}")
    loved = sorted(week.messages, key=lambda m: m.tapback_total, reverse=True)[:5]
    print("\nMost-loved messages:")
    for m in loved:
        if m.tapback_total:
            reactions = ", ".join(f"{k} x{n}" for k, n in sorted(m.tapbacks.items()))
            print(f"  [{reactions}] {m.sender}: {m.text}")
    unknown = sorted({m.sender for m in week.messages if m.sender == extract.UNKNOWN_NAME})
    if unknown:
        print("\nSome senders have no name yet. Add their phone numbers or emails to names.json.")


def cmd_rows(config, args):
    conn = extract.open_copy(Path(config["db"]).expanduser())
    rows = conn.execute(
        """
        SELECT m.ROWID, m.date, m.is_from_me, m.text, m.attributedBody, m.associated_message_type,
               m.cache_has_attachments, h.id AS handle
        FROM chat_message_join cmj
        JOIN chat c ON c.ROWID = cmj.chat_id
        JOIN message m ON m.ROWID = cmj.message_id
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE c.guid = ?
        ORDER BY m.ROWID DESC LIMIT ?
        """,
        (args.chat, args.limit),
    ).fetchall()
    for r in reversed(rows):
        when = extract.apple_date_to_datetime(r["date"]).astimezone()
        text = extract.message_text(r["text"], r["attributedBody"])
        print(f"{r['ROWID']:>8}  {when:%m-%d %H:%M:%S}  from_me={r['is_from_me']}  "
              f"type={r['associated_message_type']}  attach={r['cache_has_attachments']}  "
              f"handle={r['handle']}  text={text!r}")


def cmd_issue(config, args):
    issues_dir = Path(config["issues_dir"])
    week = load_week(config, args.chat)
    if not week.messages:
        raise SystemExit("No messages in this chat in the last week, so there's nothing to report.")
    s = stats.compute(week, issue_number=len(posted_log(issues_dir)) + 1)

    if args.sample:
        issue = verify.verify(write.sample_issue(week, s), week, s)
    else:
        previous = previous_issue(issues_dir)
        for attempt in (1, 2):
            draft = write.write_issue(week, s, previous, args.notes)
            try:
                issue = verify.verify(draft, week, s)
                break
            except verify.UnverifiedQuote as e:
                if attempt == 2:
                    raise SystemExit(f"Gave up: the issue kept quoting people without checkable quotes. {e}")
                print(f"Rewriting once: {e}", file=sys.stderr)

    if issue.get("dropped_quotes"):
        print(f"Dropped quotes that didn't match the chat: {', '.join(issue['dropped_quotes'])}", file=sys.stderr)
    paths = render.save(issue, week, s, issues_dir, version=args.version, png=not args.no_png)
    for kind, path in paths.items():
        print(f"{kind}: {path}")


def cmd_mark_posted(config, args):
    issues_dir = Path(config["issues_dir"])
    path = Path(args.file).resolve()
    if path.suffix != ".json":
        path = path.with_suffix(".json")
    if not path.exists():
        raise SystemExit(f"{path} doesn't exist.")
    log = posted_log(issues_dir)
    issue = json.loads(path.read_text(encoding="utf-8"))
    log.append({"issue_number": issue.get("issue_number"), "json": str(path),
                "posted_at": datetime.now(timezone.utc).isoformat()})
    issues_dir.mkdir(parents=True, exist_ok=True)
    (issues_dir / "posted.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"Recorded issue #{issue.get('issue_number')} as posted. The next issue will be #{len(log) + 1}.")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m groupchat", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("chats", help="List chats and their GUIDs")
    p.add_argument("--all", action="store_true", help="Include one-on-one chats (e.g. your chat with yourself)")
    p.set_defaults(func=cmd_chats)

    p = sub.add_parser("peek", help="Summarize the week without calling Claude")
    p.add_argument("--chat", help="Chat GUID (defaults to chat_guid in config.toml)")
    p.set_defaults(func=cmd_peek)

    p = sub.add_parser("rows", help="Print raw recent rows of a chat")
    p.add_argument("--chat", required=True, help="Chat GUID")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_rows)

    p = sub.add_parser("issue", help="Write and render this week's issue")
    p.add_argument("--chat", help="Chat GUID (defaults to chat_guid in config.toml)")
    p.add_argument("--sample", action="store_true", help="Use a canned issue instead of calling Claude")
    p.add_argument("--notes", action="append", help="Editor's note for a rewrite (repeatable)")
    p.add_argument("--version", type=int, default=1, help="Draft version number for the file name")
    p.add_argument("--no-png", action="store_true", help="Skip the PNG screenshot")
    p.set_defaults(func=cmd_issue)

    p = sub.add_parser("mark-posted", help="Record an issue as posted to the group")
    p.add_argument("file", help="The issue's .json (or .png) file")
    p.set_defaults(func=cmd_mark_posted)

    args = parser.parse_args(argv)
    config = load_config(args.config)
    try:
        args.func(config, args)
    except write.WriterError as e:
        raise SystemExit(str(e))
    except PermissionError:
        raise SystemExit(
            "Couldn't read the Messages database. Give your terminal app Full Disk Access in "
            "System Settings > Privacy & Security > Full Disk Access, then open a new terminal window."
        )


if __name__ == "__main__":
    main()
