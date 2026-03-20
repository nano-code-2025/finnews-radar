"""Minimal RSSFilter test (mock FinBERT)."""
import importlib.util
import sys
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
pkg = types.ModuleType("src.pipelines")
pkg.__path__ = [str(ROOT / "src" / "pipelines")]
sys.modules["src.pipelines"] = pkg
spec = importlib.util.spec_from_file_location("src.pipelines.base", ROOT / "src" / "pipelines" / "base.py")
base = importlib.util.module_from_spec(spec)
sys.modules["src.pipelines.base"] = base
spec.loader.exec_module(base)  # type: ignore[union-attr]

from src.analyzers.base import AnalyzedItem
from src.analyzers.rss_filter import RSSFilter, SCORE_THRESHOLD_IMPORTANT

def make_item(id_: str, title: str, content: str, source: str) -> AnalyzedItem:
    """Create a minimal AnalyzedItem."""
    return AnalyzedItem(id=id_, title=title, content=content, source=source, category="rss",
                        url="http://example.com", published_at=datetime.utcnow())

if __name__ == "__main__":
    items = [make_item("1", "Fed signals rate cut", "CPI easing, bitcoin reacts", "Federal Reserve"),
             make_item("2", "Random sports news", "Football match report", "ESPN"),
             make_item("3", "SEC lawsuit targets crypto exchange", "Enforcement action", "SEC Press")]
    rf = RSSFilter()
    rf.finbert = lambda text, truncation=True, max_length=512: [{"label": "positive", "score": 0.9}]
    result = rf.filter(items)
    assert len(result.passed) >= 1
    assert all(i.score >= SCORE_THRESHOLD_IMPORTANT for i in result.passed)
    assert any(i.raw_data.get("sourcing_category") in {"macro", "regulation", "crypto"} for i in result.passed)
    print("test_rss_filter.py OK")

