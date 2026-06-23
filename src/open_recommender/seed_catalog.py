from __future__ import annotations

from .models import validate_topic_name

SEED_TOPICS = tuple(
    validate_topic_name(topic)
    for topic in (
        "orf:technology/python",
        "orf:technology/open-source",
        "orf:technology/privacy",
        "orf:technology/cybersecurity",
        "orf:technology/web-development",
        "orf:technology/ai",
        "orf:media/podcasts",
        "orf:media/documentaries",
        "orf:media/gaming",
        "orf:media/streaming",
        "orf:media/music",
        "orf:science/space",
        "orf:science/biology",
        "orf:science/climate",
        "orf:business/startups",
        "orf:business/economics",
        "orf:design/ux",
        "orf:design/architecture",
        "orf:lifestyle/travel",
        "orf:lifestyle/fitness",
        "orf:lifestyle/food",
        "orf:sports/basketball",
        "orf:sports/soccer",
        "orf:education/history",
        "orf:education/languages",
        "orf:education/mathematics",
    )
)

SEED_SITES = (
    ("open-news", "Open News"),
    ("stream-grid", "Stream Grid"),
    ("daily-pulse", "Daily Pulse"),
    ("spark-feed", "Spark Feed"),
    ("pod-wave", "Pod Wave"),
    ("signal-weekly", "Signal Weekly"),
)
