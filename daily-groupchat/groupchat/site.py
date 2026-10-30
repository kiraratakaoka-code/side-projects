"""Build a small static website from the issues you've posted: the latest on the front page, plus an archive."""

import json
from pathlib import Path

from .render import environment, fill_quotes, render_html

ROBOTS = "User-agent: *\nDisallow: /\n"


def posted_issues(posted: list) -> list:
    """Load each posted issue's saved page data, oldest first. Issues without page data are skipped."""
    issues = []
    for entry in posted:
        path = Path(entry["json"])
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if "page" in record:
            issues.append(record["page"])
    return sorted(issues, key=lambda page: page["issue_number"])


def build(posted: list, out_dir: Path) -> list:
    """Write index.html, issue-<n>.html for every issue, archive.html and robots.txt. Returns the files written."""
    issues = posted_issues(posted)
    if not issues:
        raise ValueError("No posted issues yet. Run `python -m groupchat mark-posted <issue.json>` first.")

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in [*out_dir.glob("issue-*.html"), out_dir / "index.html", out_dir / "archive.html"]:
        old.unlink(missing_ok=True)  # only files this builder made, never anything else in the folder
    written = []

    def write(name: str, html: str) -> None:
        (out_dir / name).write_text(html, encoding="utf-8")
        written.append(out_dir / name)

    for i, page in enumerate(issues):
        nav = {
            "older": f"issue-{issues[i - 1]['issue_number']}.html" if i > 0 else None,
            "newer": f"issue-{issues[i + 1]['issue_number']}.html" if i + 1 < len(issues) else None,
            "archive": "archive.html",
        }
        write(f"issue-{page['issue_number']}.html", render_html(page, nav))

    latest = issues[-1]
    front_nav = {"older": f"issue-{issues[-2]['issue_number']}.html" if len(issues) > 1 else None,
                 "archive": "archive.html"}
    write("index.html", render_html(latest, front_nav))
    archive = [{**page, "headline": fill_quotes(page["issue"]["lead_headline"],
                                                {q["id"]: q for q in page["issue"]["quotes"]})}
               for page in reversed(issues)]
    write("archive.html", environment().get_template("archive.html").render(
        issues=archive, chat_name=latest["chat_name"]))
    write("robots.txt", ROBOTS)
    return written
