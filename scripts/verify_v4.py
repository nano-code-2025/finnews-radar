"""Verify Feature Extraction v4: fear_score + fomo_score + topic expansion"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "features.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. Schema check
    cursor = conn.execute("PRAGMA table_info(post_features)")
    cols = [row["name"] for row in cursor.fetchall()]
    new_cols = ["fear_score", "fomo_score", "llm_fear_score", "llm_fomo_score"]
    print("=== Schema Check ===")
    for c in new_cols:
        status = "OK" if c in cols else "MISSING"
        print(f"  {c}: {status}")

    # 2. Sample data (latest 5)
    print("\n=== Sample Features (latest 5) ===")
    rows = conn.execute(
        """
        SELECT author_handle, topic, sentiment, fear_score, fomo_score,
               llm_fear_score, llm_fomo_score, llm_summary, llm_enhanced
        FROM post_features ORDER BY extracted_at DESC LIMIT 5
        """
    ).fetchall()
    for r in rows:
        print(f"  @{r['author_handle']:20s} topic={r['topic']:12s} "
              f"fear={r['fear_score']!s:5s} fomo={r['fomo_score']!s:5s} "
              f"llm_fear={r['llm_fear_score']!s:5s} llm_fomo={r['llm_fomo_score']!s:5s} "
              f"| {r['llm_summary'] or '(no summary)'}")

    # 3. Aggregate stats for today
    stats = conn.execute(
        """
        SELECT COUNT(*) as total,
               AVG(fear_score) as avg_fear, AVG(fomo_score) as avg_fomo,
               AVG(llm_fear_score) as avg_llm_fear, AVG(llm_fomo_score) as avg_llm_fomo,
               SUM(CASE WHEN fear_score > 0.7 THEN 1 ELSE 0 END) as high_fear,
               SUM(CASE WHEN fomo_score > 0.7 THEN 1 ELSE 0 END) as high_fomo,
               SUM(CASE WHEN llm_enhanced = 1 THEN 1 ELSE 0 END) as llm_count
        FROM post_features WHERE extracted_at LIKE '2026-02-09%'
        """
    ).fetchone()
    print(f"\n=== Today's Stats ===")
    print(f"  Total: {stats['total']} | LLM enhanced: {stats['llm_count']}")
    avg_fear = stats['avg_fear'] or 0
    avg_fomo = stats['avg_fomo'] or 0
    avg_llm_fear = stats['avg_llm_fear'] or 0
    avg_llm_fomo = stats['avg_llm_fomo'] or 0
    print(f"  Rule:  avg_fear={avg_fear:.3f}  avg_fomo={avg_fomo:.3f}")
    print(f"  LLM:   avg_fear={avg_llm_fear:.3f}  avg_fomo={avg_llm_fomo:.3f}")
    print(f"  High fear (>0.7): {stats['high_fear']}  High fomo (>0.7): {stats['high_fomo']}")

    # 4. Topic distribution (check new topics)
    print("\n=== Topic Distribution ===")
    topics = conn.execute(
        """
        SELECT topic, COUNT(*) as cnt FROM post_features
        WHERE extracted_at LIKE '2026-02-09%'
        GROUP BY topic ORDER BY cnt DESC
        """
    ).fetchall()
    for t in topics:
        print(f"  {t['topic']:15s} {t['cnt']} 条")

    conn.close()
    print("\n[OK] Verification complete.")


if __name__ == "__main__":
    main()
