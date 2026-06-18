"""Quick verification script for agent memory DB."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.agent_memory import (
    format_fundamental_history,
    format_risk_history,
    format_run_summaries,
    read_recent_runs,
    read_ticker_history,
)


async def main():
    h_nvda_ra = await read_ticker_history("NVDA", "risk_assessor")
    h_nvda_fa = await read_ticker_history("NVDA", "fundamental_analyst")
    h_msft_fa = await read_ticker_history("MSFT", "fundamental_analyst")

    print(f"NVDA risk_assessor : {len(h_nvda_ra)} record(s), quality={h_nvda_ra[0]['data']['quality']}")
    print(f"NVDA fundamental   : {len(h_nvda_fa)} record(s)")
    print(f"MSFT fundamental   : {len(h_msft_fa)} record(s)")

    runs = await read_recent_runs()
    print(f"run_summaries      : {len(runs)} run(s), mode={runs[0]['mode']}, candidates={runs[0]['candidates']}")

    print("\n--- format_risk_history NVDA ---")
    print(format_risk_history("NVDA", h_nvda_ra))

    print("\n--- format_fundamental_history MSFT ---")
    print(format_fundamental_history("MSFT", h_msft_fa))

    print("\n--- format_run_summaries ---")
    print(format_run_summaries(runs))


asyncio.run(main())
