"""Numbers for the paper. Computed in code so the printed facts are always true."""

import re
from collections import Counter
from dataclasses import dataclass

from .extract import Week

EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]"
)
LATE_NIGHT_HOURS = range(2, 5)  # 2:00-4:59 local time


@dataclass
class Stats:
    total: int
    per_person: list  # [(name, count, share)] most talkative first
    late_night: int
    late_night_champion: str
    top_emoji: str
    funniest: str  # most "laugh" tapbacks received
    most_loved: object  # Message with the most tapbacks, or None
    issue_number: int


def compute(week: Week, issue_number: int) -> Stats:
    messages = week.messages
    counts = Counter(m.sender for m in messages)
    total = len(messages)
    per_person = [(name, n, n / total) for name, n in counts.most_common()] if total else []

    late = [m for m in messages if m.sent_at.astimezone().hour in LATE_NIGHT_HOURS]
    late_counts = Counter(m.sender for m in late)

    emoji = Counter(ch for m in messages for ch in EMOJI.findall(m.text))

    laughs = Counter()
    for m in messages:
        laughs[m.sender] += m.tapbacks.get("laugh", 0)

    loved = max(messages, key=lambda m: (m.tapback_total, m.sent_at), default=None)
    if loved is not None and loved.tapback_total == 0:
        loved = None

    return Stats(
        total=total,
        per_person=per_person,
        late_night=len(late),
        late_night_champion=late_counts.most_common(1)[0][0] if late_counts else "",
        top_emoji=emoji.most_common(1)[0][0] if emoji else "",
        funniest=laughs.most_common(1)[0][0] if laughs and laughs.most_common(1)[0][1] else "",
        most_loved=loved,
        issue_number=issue_number,
    )
