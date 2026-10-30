"""Ask Claude to write the week's issue as structured JSON."""

import json

import anthropic

from .extract import Week
from .stats import Stats

MODEL = "claude-opus-5-5"

# Rough budget for the transcript: ~4 characters per token, so about 100k tokens.
MAX_TRANSCRIPT_CHARS = 400_000

SYSTEM_PROMPT = """\
You are the editor of THE DAILY GROUPCHAT, a satirical one-page weekly newspaper written \
about a group of friends, using their own group chat as the source. They will all read it. \
Write like a deadpan broadsheet covering tiny events as if they were world news.

Roast, don't wound. The goal is that every person in the chat laughs, including the person \
the joke is about. Never write about health, illness, bodies, weight, relationships or \
dating, money troubles, family problems, work trouble, or anything that reads as private or \
said in confidence. If the week is mostly about something like that, write about something \
else from the chat. Never invent events that would embarrass someone.

Quoting rules:
- Every quote must be copied exactly, character for character, from one message in the \
transcript, and attributed to the person who sent it. List each one in `quotes` with an id \
like "q1".
- In the prose, a quote appears ONLY as its placeholder, e.g. [[q1]]. Never put quotation \
marks (" or “ ”) anywhere else in the prose.
- quote_of_week_id must be the id of one entry in `quotes`.

Numbers: do not state message counts, percentages or other statistics; the paper prints the \
real numbers next to your writing. Do not write the issue number.

Keep it short enough to fit one phone screen: headline under 14 words, lead story 2-3 \
sentences, op-ed blurb 1-2 sentences, forecast 1-2 sentences, one correction."""

ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "lead_headline": {"type": "string"},
        "lead_story": {"type": "string"},
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "sender": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["id", "sender", "text"],
                "additionalProperties": False,
            },
        },
        "quote_of_week_id": {"type": "string"},
        "op_ed": {
            "type": "object",
            "properties": {
                "author": {"type": "string"},
                "title": {"type": "string"},
                "blurb": {"type": "string"},
            },
            "required": ["author", "title", "blurb"],
            "additionalProperties": False,
        },
        "vibe_forecast": {"type": "string"},
        "corrections": {"type": "string"},
    },
    "required": [
        "lead_headline", "lead_story", "quotes", "quote_of_week_id",
        "op_ed", "vibe_forecast", "corrections",
    ],
    "additionalProperties": False,
}


class WriterError(Exception):
    pass


def transcript(week: Week, max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """One line per message, newest kept when the week is too long."""
    lines = []
    for m in week.messages:
        reactions = ""
        if m.tapbacks:
            reactions = "  (reactions: " + ", ".join(f"{k} x{n}" for k, n in sorted(m.tapbacks.items())) + ")"
        lines.append(f"[{m.sent_at.astimezone():%a %H:%M}] {m.sender}: {m.text}{reactions}")
    kept, size = [], 0
    for line in reversed(lines):
        size += len(line) + 1
        if size > max_chars:
            break
        kept.append(line)
    return "\n".join(reversed(kept))


def build_prompt(week: Week, stats: Stats, previous_issue: dict = None, notes: list = None) -> str:
    people = ", ".join(name for name, _, _ in stats.per_person)
    parts = [
        f"Group chat: {week.chat_name}",
        f"People: {people}",
        f"Week: {week.start.astimezone():%b %d} to {week.end.astimezone():%b %d}",
        f"Most-loved message (by reactions): {stats.most_loved.sender}: {stats.most_loved.text}"
        if stats.most_loved else "",
        f"Funniest by laughs received: {stats.funniest}" if stats.funniest else "",
    ]
    if previous_issue:
        parts.append(
            "Last posted issue, for the corrections column to call back to:\n"
            + json.dumps({k: previous_issue.get(k) for k in ("lead_headline", "lead_story", "corrections")})
        )
    if notes:
        parts.append("Editor's notes on the previous draft, apply all of them:\n- " + "\n- ".join(notes))
    parts.append("<transcript>\n" + transcript(week) + "\n</transcript>")
    return "\n\n".join(p for p in parts if p)


def write_issue(week: Week, stats: Stats, previous_issue: dict = None, notes: list = None,
                client: anthropic.Anthropic = None) -> dict:
    client = client or anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(week, stats, previous_issue, notes)}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ISSUE_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise WriterError("Authentication failed. Set ANTHROPIC_API_KEY or run `ant auth login`.")
    except anthropic.RateLimitError as e:
        retry_after = e.response.headers.get("retry-after", "a few seconds")
        raise WriterError(f"Rate limited. Retry after {retry_after}.")
    except anthropic.APIStatusError as e:
        raise WriterError(f"API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise WriterError("Network error while contacting the Claude API. Check your connection.")

    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        raise WriterError(f"Claude declined to write this issue (category: {category}).")
    if response.stop_reason == "max_tokens":
        raise WriterError("The issue was cut off at the output limit.")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def sample_issue(week: Week, stats: Stats) -> dict:
    """A canned issue built from real messages, for --sample runs and tests (no API call)."""
    top = stats.per_person[0][0] if stats.per_person else "Nobody"
    loved = stats.most_loved or (week.messages[0] if week.messages else None)
    quotes = [{"id": "q1", "sender": loved.sender, "text": loved.text}] if loved else []
    return {
        "lead_headline": f"{top.upper()} SENDS MORE MESSAGES THAN THE REST OF THE CHAT CAN READ",
        "lead_story": f"Sources close to the chat confirm {top} has not stopped typing since Monday. "
                      f"When reached for comment, {loved.sender if loved else top} said only [[q1]].",
        "quotes": quotes,
        "quote_of_week_id": "q1" if quotes else "",
        "op_ed": {
            "author": stats.funniest or top,
            "title": "Brunch Is A Scam, And I Will Be There At 11",
            "blurb": "A brave stand against eggs, followed immediately by eggs.",
        },
        "vibe_forecast": "Scattered plans, mostly cancelled. A 70% chance of someone saying they're five minutes away.",
        "corrections": "Last week we reported the group trip was 'basically booked.' It was not. We regret the error.",
    }
