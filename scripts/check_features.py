"""检查 features.db 内容"""
import sys
sys.path.insert(0, ".")

from src.utils.features_db import FeaturesDatabase

db = FeaturesDatabase()
rows = db.get_features_by_date("2026-02-08")
print(f"features.db: {len(rows)} records\n")

for r in rows:
    handle = r["author_handle"]
    topic = r["topic"] or "?"
    sent = r["sentiment"] or 0
    rat = r["rationality"] or 0
    length = r["length"] or 0
    tags = r["event_tags"] or []
    eng = r["engagement_score"] or 0
    print(f"  @{handle:15s}  topic={topic:8s}  sent={sent:+.2f}  rat={rat:.2f}  len={length:3d}  eng={eng:,.0f}  tags={tags}")

print()
summary = db.get_daily_summary("2026-02-08")
print("Daily summary:")
for s in summary:
    topic = s["topic"] or "?"
    count = s["post_count"]
    avg_sent = s["avg_sentiment"] or 0
    total_eng = s["total_engagement"] or 0
    avg_rat = s["avg_rationality"] or 0
    avg_len = s["avg_length"] or 0
    print(f"  {topic:8s}: count={count}, avg_sent={avg_sent:+.3f}, engagement={total_eng:,.0f}, rationality={avg_rat:.2f}, avg_len={avg_len:.0f}")
