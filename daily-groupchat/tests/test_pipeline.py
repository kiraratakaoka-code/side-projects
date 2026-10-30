import json
import os
from pathlib import Path

import pytest

from groupchat import extract, render, stats, verify, write
from groupchat.__main__ import main
from tests import fake_chat_db
from tests.fake_chat_db import GROUP_GUID, NAMES, NOW


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "chat.db"
    fake_chat_db.build(path)
    return path


@pytest.fixture
def week(db):
    return extract.read_week(extract.open_copy(db), GROUP_GUID, NAMES, now=NOW)


def by_text(week, fragment):
    return next(m for m in week.messages if fragment in m.text)


def test_decode_attributed_body_short_and_long():
    assert extract.decode_attributed_body(fake_chat_db.attributed_body("hi")) == "hi"
    long = "é" * 200
    assert extract.decode_attributed_body(fake_chat_db.attributed_body(long)) == long
    assert extract.decode_attributed_body(None) == ""
    assert extract.decode_attributed_body(b"no marker here") == ""


def test_apple_dates_round_trip():
    assert extract.apple_date_to_datetime(extract.datetime_to_apple_date(NOW)) == NOW
    # Pre-2017 databases stored seconds, not nanoseconds.
    assert extract.apple_date_to_datetime(0).year == 2001


def test_week_keeps_only_real_messages(week):
    texts = [m.text for m in week.messages]
    assert "this is from two weeks ago" not in texts           # outside the window
    assert "secret plans in another chat" not in texts         # other chat
    assert "delete this, wrong chat" not in texts              # unsent
    assert "i am simply not a morning person or an afternoon person" in texts  # attributedBody only
    assert "final answer: no" in texts and "final answer: yes" not in texts    # edited: latest text
    assert len(texts) == 9
    assert week.skipped_empty == 1                             # attachment-only row
    assert week.chat_name == "Brunch Bandits"


def test_names(week):
    assert by_text(week, "11:40").sender == "Kira"
    assert by_text(week, "who is this chat").sender == extract.UNKNOWN_NAME


def test_tapbacks_count_adds_and_removals(week):
    sleepy = by_text(week, "morning person")
    assert sleepy.tapbacks == {"love": 4, "laugh": 2}
    assert by_text(week, "tupperware").tapbacks == {"laugh": 1}


def test_stats(week):
    s = stats.compute(week, issue_number=3)
    assert s.total == 9
    assert s.per_person[0][:2] == ("Maya", 4)
    assert s.top_emoji == "💀"
    assert s.funniest == "Priya"
    assert s.most_loved.text.startswith("i am simply")
    assert s.issue_number == 3


def make_issue(**overrides):
    issue = {
        "lead_headline": "LOCAL MAN DEFENDS TUPPERWARE POLICY",
        "lead_story": "Jake insisted [[q1]], while Priya told reporters [[q2]].",
        "quotes": [
            {"id": "q1", "sender": "Jake", "text": "The tupperware is BASICALLY returned"},
            {"id": "q2", "sender": "Priya", "text": "i never said this"},
        ],
        "quote_of_week_id": "q2",
        "op_ed": {"author": "Maya", "title": "Lisbon Now", "blurb": "A modest proposal."},
        "vibe_forecast": "Cloudy with a chance of brunch.",
        "corrections": "None this week.",
    }
    issue.update(overrides)
    return issue


def test_verify_keeps_real_quotes_and_drops_fake_ones(week):
    s = stats.compute(week, issue_number=1)
    issue = verify.verify(make_issue(), week, s)
    assert [q["id"] for q in issue["quotes"]] == ["q1", "qw"]
    assert issue["dropped_quotes"] == ["q2"]
    assert issue["lead_story"] == "Jake insisted [[q1]], while Priya told reporters."
    # Quote of the week fell back to the most-loved real message.
    assert issue["quote_of_week_id"] == "qw"
    assert issue["quotes"][-1]["text"].startswith("i am simply")


def test_verify_checks_the_sender(week):
    s = stats.compute(week, issue_number=1)
    wrong_person = make_issue(quotes=[{"id": "q1", "sender": "Maya", "text": "basically returned"}])
    assert verify.verify(wrong_person, week, s)["dropped_quotes"] == ["q1"]


def test_verify_rejects_unplaceheld_quotes(week):
    s = stats.compute(week, issue_number=1)
    with pytest.raises(verify.UnverifiedQuote):
        verify.verify(make_issue(lead_story="Jake said “it’s in the car” again."), week, s)
    # Apostrophes and single quotes are fine.
    verify.verify(make_issue(lead_story="Jake's car is 'full'. [[q1]]"), week, s)


def test_rendered_prose_is_escaped(week):
    s = stats.compute(week, issue_number=1)
    issue = verify.verify(make_issue(lead_headline="<script>alert(1)</script> {{ 7*7 }}"), week, s)
    html = render.render_html(issue, week, s)
    assert "<script>alert" not in html
    assert "{{ 7*7 }}" in html  # printed literally, never run as a template
    assert "<q>The tupperware is BASICALLY returned</q>" in html
    assert "Vol. 1, No. 1" in html


def test_transcript_trims_oldest_first(week):
    text = write.transcript(week, max_chars=120)
    assert "final answer: no" in text
    assert "tupperware" not in text


def test_build_prompt_has_names_not_numbers(week):
    s = stats.compute(week, issue_number=1)
    prompt = write.build_prompt(week, s, notes=["less mean to Jake"])
    assert "Priya" in prompt and "+15550000001" not in prompt and "jake@example.com" not in prompt
    assert "less mean to Jake" in prompt


def test_cli_sample_issue_and_posting(tmp_path, db, capsys, monkeypatch):
    monkeypatch.setattr(extract, "datetime", type("FrozenDatetime", (extract.datetime,), {
        "now": classmethod(lambda cls, tz=None: NOW)}))
    (tmp_path / "names.json").write_text(json.dumps(NAMES))
    config = tmp_path / "config.toml"
    config.write_text(f'db = "{db}"\nchat_guid = "{GROUP_GUID}"\n')

    main(["--config", str(config), "peek"])
    out = capsys.readouterr().out
    assert "Brunch Bandits: 9 messages" in out
    assert "[laugh x2, love x4] Priya: i am simply" in out

    main(["--config", str(config), "issue", "--sample", "--no-png"])
    out = capsys.readouterr().out
    json_path = Path(next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("json:")))
    assert json.loads(json_path.read_text())["issue_number"] == 1

    main(["--config", str(config), "mark-posted", str(json_path)])
    assert "next issue will be #2" in capsys.readouterr().out


@pytest.mark.skipif(not os.environ.get("GROUPCHAT_RENDER_TEST"), reason="set GROUPCHAT_RENDER_TEST=1 to render a real PNG")
def test_png_render(tmp_path, week):
    s = stats.compute(week, issue_number=1)
    issue = verify.verify(write.sample_issue(week, s), week, s)
    paths = render.save(issue, week, s, tmp_path)
    assert paths["png"].stat().st_size > 10_000
