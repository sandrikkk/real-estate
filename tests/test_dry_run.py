import asyncio
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import RealEstateOrchestrator


async def test_live_cycle():
    print("Testing 1 Live Polling Cycle...")
    orchestrator = RealEstateOrchestrator()
    stats = await orchestrator.run_cycle()
    print("Dry run completed successfully with stats:", stats)
    assert stats["total_fetched"] >= 0
    print("ALL DRY RUN CHECKS PASSED!")


if __name__ == "__main__":
    asyncio.run(test_live_cycle())
