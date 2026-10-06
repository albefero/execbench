"""Print the reference-policy results table used in the README."""

from execbench.baselines import run_policy
from execbench.scenarios import SCENARIOS
from execbench.scoring import evaluate

print("| Scenario | TWAP score | TWAP IS (bps) | Dump score | Dump IS (bps) |")
print("|---|---|---|---|---|")
totals = {"twap": 0.0, "dump": 0.0}
for s in SCENARIOS:
    t = evaluate(s, run_policy(s, "twap"))
    d = evaluate(s, run_policy(s, "dump"))
    totals["twap"] += t["score"]
    totals["dump"] += d["score"]
    print(f"| {s.id} | {t['score']:.2f} | {t['shortfall_bps']:.1f} | {d['score']:.2f} | {d['shortfall_bps']:.1f} |")
n = len(SCENARIOS)
print(f"| **mean** | **{totals['twap'] / n:.2f}** | | **{totals['dump'] / n:.2f}** | |")
