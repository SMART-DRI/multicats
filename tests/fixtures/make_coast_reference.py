# pyright: basic, reportMissingImports=false
"""Regenerate ``coast_reference.json`` from the COAST reference implementation.

Not run by the test suite. Usage, with a checkout of SMART-DRI/algorithms:

    python tests/fixtures/make_coast_reference.py /path/to/algorithms/coast

The script replays three CEA-Curie workload days through the reference
simulator and stores the start slots the reference assigns under four weight
settings for nine micro-batches: per day, the three batches in which COAST
moves the most jobs away from the Carbon+waiting assignment, so that the
congestion term is exercised.
"""

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# the first three (workload day, carbon-trace offset) pairs of the paper's
# held-out test set
DAYS = [(22, 7785), (194, 111), (236, 9464)]
# name -> ((alpha, beta, gamma, reference solver), CoastConfig kwargs)
SETTINGS: dict[str, tuple[tuple[float, float, float, str], dict[str, float]]] = {
    "coast_paper": ((4.0, 1.0, 0.0005, "coast"), {}),
    "coast_heavy_congestion": (
        (1.0, 1.0, 0.01, "coast"),
        {"carbon_weight": 1.0, "congestion_weight": 0.01},
    ),
    "carbon_wait": ((4.0, 1.0, 0.0, "independent"), {"congestion_weight": 0.0}),
    "carbon_greedy": (
        (1.0, 0.0, 0.0, "independent"),
        {"carbon_weight": 1.0, "delay_weight": 0.0, "congestion_weight": 0.0},
    ),
}
NORMS = {"carbon_ref": 82.6623227202825, "wait_ref": 60.0, "congestion_ref": 9216.0}


def _solve(ref: Any, batch: list[Any], k: int, name: str, bg: Any, carbon: Any) -> Any:
    (a, b, g, solver), _ = SETTINGS[name]
    w = ref.C.Weights(a, b, g)
    norms = ref.C.Norms(**NORMS)
    decision_min = (k + 1) * ref.C.BATCH_MIN
    if solver == "coast":
        assign, _ = ref.game.solve_batch_coast(
            batch, bg.copy(), carbon, w, norms, decision_min
        )
        return assign
    return ref.game.solve_batch_independent(
        batch, bg.copy(), carbon, w, norms, decision_min
    )


def _moved(expected: dict[str, dict[str, int]]) -> tuple[int, int]:
    base = expected["carbon_wait"]
    return (
        sum(expected["coast_paper"][j] != base[j] for j in base),
        sum(expected["coast_heavy_congestion"][j] != base[j] for j in base),
    )


def _day_cases(
    ref: Any, raw: Any, t0: int, submits: Any, full_carbon: Any, day: int, offset: int
) -> list[dict[str, Any]]:
    cfg = ref.C.SimConfig(sim_days=1.0)
    n = ref.simulator._timeline_slots(cfg)
    jobs = ref.dataio.jobs_in_window(raw, t0, submits, float(day), cfg)
    carbon = ref.np.asarray(full_carbon[offset : offset + n])

    # replay the day under the paper's COAST weights; record each batch's
    # committed background load and the assignments of all settings
    load = ref.np.zeros(n)
    candidates = []
    for k, batch in sorted(ref.simulator._group_batches(jobs).items()):
        bg = load.copy()
        expected = {name: _solve(ref, batch, k, name, bg, carbon) for name in SETTINGS}
        candidates.append((_moved(expected), float(bg.sum()), k, batch, bg, expected))
        for job in batch:
            if job.job_id in expected["coast_paper"]:
                ref.game.add_load(load, job, expected["coast_paper"][job.job_id])

    selected = sorted(candidates, key=lambda c: (c[0], c[1]), reverse=True)[:3]
    return [
        {
            "day": day,
            "carbon_offset": offset,
            "batch": k,
            "decision_seconds": (k + 1) * ref.C.BATCH_MIN * 60,
            "carbon": carbon.tolist(),
            "background": bg.tolist(),
            "jobs": [
                {
                    "job_id": j.job_id,
                    "arrival_seconds": round(j.arrival_min * 60),
                    "duration_minutes": j.runtime_min,
                    "resource": j.r,
                    "energy_kwh": j.energy_kwh,
                    "max_wait_minutes": j.max_wait_min,
                    "deadline_seconds": round(j.deadline_min * 60),
                }
                for j in batch
            ],
            "expected": expected,
        }
        for _, _, k, batch, bg, expected in sorted(selected, key=lambda c: c[2])
    ]


def main(coast_dir: Path) -> None:
    sys.path.insert(0, str(coast_dir / "src"))
    import types

    import config
    import dataio
    import game
    import numpy
    import simulator

    ref = types.SimpleNamespace(
        C=config, dataio=dataio, game=game, np=numpy, simulator=simulator
    )
    data = coast_dir / "data"
    raw, t0, submits = dataio.load_swf_raw(str(data / "CEA-Curie-2011-2.1-cln.swf.gz"))
    full_carbon = dataio.load_carbon_csv(str(data / "carbon_30min.csv"))
    cases = [
        case
        for day, offset in DAYS
        for case in _day_cases(ref, raw, t0, submits, full_carbon, day, offset)
    ]
    out = {
        "source": "SMART-DRI/algorithms coast reference implementation",
        "settings": {name: kwargs for name, (_, kwargs) in SETTINGS.items()},
        "cases": cases,
    }
    path = HERE / "coast_reference.json"
    _ = path.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {len(cases)} batches to {path}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
