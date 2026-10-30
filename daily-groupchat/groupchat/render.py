"""Turn a checked issue into an HTML page and a phone-sized PNG."""

import json
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from .extract import Week
from .stats import Stats
from .verify import PLACEHOLDER, PROSE_FIELDS

TEMPLATES = Path(__file__).parent / "templates"
TAPBACK_EMOJI = {
    "love": "❤️", "like": "👍", "dislike": "👎", "laugh": "😂",
    "emphasize": "‼️", "question": "❓", "emoji": "✨",
}


def fill_quotes(text: str, quotes: dict) -> Markup:
    """Escape Claude's prose, then put the checked quotes in. The prose is never run as a template."""
    out, last = [], 0
    for match in PLACEHOLDER.finditer(text):
        out.append(escape(text[last:match.start()]))
        quote = quotes.get(match.group(1))
        if quote:
            out.append(Markup("<q>{}</q>").format(quote["text"]))
        last = match.end()
    out.append(escape(text[last:]))
    return Markup("").join(out)


def render_html(issue: dict, week: Week, stats: Stats) -> str:
    quotes = {q["id"]: q for q in issue["quotes"]}
    prose = {key: fill_quotes(issue[key], quotes) for key in PROSE_FIELDS}
    op_ed = {key: fill_quotes(issue["op_ed"][key], quotes) for key in ("title", "blurb")}
    op_ed["author"] = issue["op_ed"]["author"]

    quote_of_week = quotes.get(issue["quote_of_week_id"])
    reactions = ""
    if quote_of_week:
        match = next((m for m in week.messages
                      if m.sender == quote_of_week["sender"] and quote_of_week["text"] in m.text), None)
        if match and match.tapbacks:
            reactions = " ".join(f"{TAPBACK_EMOJI.get(k, k)}×{n}" for k, n in sorted(match.tapbacks.items()))

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    return env.get_template("newspaper.html").render(
        prose=prose, op_ed=op_ed, quote_of_week=quote_of_week, reactions=reactions,
        stats=stats, week=week,
    )


def screenshot(html_path: Path, png_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # GROUPCHAT_CHROMIUM points at a specific Chrome/Chromium binary if Playwright's own isn't installed.
        browser = p.chromium.launch(executable_path=os.environ.get("GROUPCHAT_CHROMIUM") or None)
        page = browser.new_page(viewport={"width": 540, "height": 960}, device_scale_factor=2)
        page.goto(html_path.resolve().as_uri())
        page.locator(".paper").screenshot(path=str(png_path))
        browser.close()


def save(issue: dict, week: Week, stats: Stats, out_dir: Path, version: int = 1, png: bool = True) -> dict:
    """Write issues/<date>-v<N>.json, .html and (optionally) .png. Returns the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{week.end.astimezone():%Y-%m-%d}-v{version}"
    paths = {"json": out_dir / f"{stem}.json", "html": out_dir / f"{stem}.html"}
    record = {**issue, "issue_number": stats.issue_number, "chat_guid": week.chat_guid,
              "week_end": week.end.isoformat(), "version": version}
    paths["json"].write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    paths["html"].write_text(render_html(issue, week, stats), encoding="utf-8")
    if png:
        paths["png"] = out_dir / f"{stem}.png"
        screenshot(paths["html"], paths["png"])
    return paths
