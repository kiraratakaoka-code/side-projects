"""Make sure every quote in the issue was really said, by the person it's attributed to."""

import re

from .extract import Week
from .stats import Stats

PLACEHOLDER = re.compile(r"\[\[(q\d+)\]\]")
DOUBLE_QUOTES = re.compile(r"[\"“”]")
PROSE_FIELDS = ("lead_headline", "lead_story", "vibe_forecast", "corrections")


class UnverifiedQuote(Exception):
    """The prose quotes someone without going through a checked placeholder."""


def normalize(text: str) -> str:
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return " ".join(text.lower().split())


def _prose_fields(issue: dict):
    for key in PROSE_FIELDS:
        yield issue, key
    yield issue["op_ed"], "title"
    yield issue["op_ed"], "blurb"


def verify(issue: dict, week: Week, stats: Stats) -> dict:
    """Return a copy of the issue with only real quotes left in it.

    Quotes that don't match a message from their sender are dropped along with their
    placeholders. Raises UnverifiedQuote if the prose contains quotation marks outside
    a placeholder, since that text can't be checked.
    """
    said = {}
    for m in week.messages:
        said.setdefault(m.sender, []).append(normalize(m.text))

    issue = {**issue, "op_ed": dict(issue["op_ed"])}
    kept, dropped = [], []
    for quote in issue["quotes"]:
        needle = normalize(quote["text"])
        if needle and any(needle in line for line in said.get(quote["sender"], [])):
            kept.append(quote)
        else:
            dropped.append(quote["id"])
    issue["quotes"] = kept
    issue["dropped_quotes"] = dropped

    known = {q["id"] for q in kept}
    for holder, key in _prose_fields(issue):
        text = holder[key]
        if DOUBLE_QUOTES.search(PLACEHOLDER.sub("", text)):
            raise UnverifiedQuote(f"{key} quotes someone outside a checked placeholder: {text!r}")
        cleaned = PLACEHOLDER.sub(lambda m: m.group(0) if m.group(1) in known else "", text)
        holder[key] = re.sub(r"\s+([,.!?;:])", r"\1", " ".join(cleaned.split()))

    if issue["quote_of_week_id"] not in known:
        loved = stats.most_loved
        if loved:
            fallback = {"id": "qw", "sender": loved.sender, "text": loved.text}
            issue["quotes"].append(fallback)
            issue["quote_of_week_id"] = "qw"
        else:
            issue["quote_of_week_id"] = ""
    return issue
