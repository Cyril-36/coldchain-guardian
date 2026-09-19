"""The twenty frozen holdout cases.

Four each of door exposure, refrigeration problem, sensor disagreement, ambiguous or
missing evidence, and normal control. Within a family the patterns differ in shape,
not just in magnitude: excursion onset, duration, recovery profile, event ordering and
coverage all vary, so a model cannot pass by recognising one template.

Freeze discipline (docs/VERIFICATION.md section 3): these cases are the holdout. Do not
tune prompts against them. If they are ever used for tuning, replace them and say so.
"""

from __future__ import annotations

from eval.builder import EvalCase

SUPPORTED = "hypothesis_supported"
UNRESOLVED = "unresolved"
NO_EXCURSION = "no_excursion"

HOLDOUT: list[EvalCase] = [
    # ── Door exposure ──────────────────────────────────────────────────────
    EvalCase(
        case_id="door_01",
        family="door",
        expected_outcome=SUPPORTED,
        expected_hypothesis="door_exposure",
        note="Long single opening with full recovery well before cutoff.",
        reference=[(0, 5.0), (840, 5.1), (1140, 9.6), (1500, 9.8), (1860, 5.3), (2700, 5.0)],
        events=[
            (840, "door_state", "open"),
            (1560, "door_state", "closed"),
            (0, "refrigeration_state", "running"),
            (0, "vehicle_state", "stopped"),
        ],
    ),
    EvalCase(
        case_id="door_02",
        family="door",
        expected_outcome=SUPPORTED,
        expected_hypothesis="door_exposure",
        note="Short sharp opening early in the trip; steep rise, quick recovery.",
        reference=[(0, 4.2), (180, 4.3), (300, 8.9), (600, 9.1), (840, 4.8), (1800, 4.4)],
        events=[
            (240, "door_state", "open"),
            (660, "door_state", "closed"),
            (0, "refrigeration_state", "running"),
            (1200, "vehicle_state", "moving"),
        ],
    ),
    EvalCase(
        case_id="door_03",
        family="door",
        expected_outcome=SUPPORTED,
        expected_hypothesis="door_exposure",
        note="Shallow excursion barely above the limit, recovering to exactly 8.0 first.",
        reference=[(0, 6.9), (1140, 7.2), (1440, 8.6), (1800, 8.7), (2040, 8.0), (2700, 7.1)],
        events=[
            (1140, "door_state", "open"),
            (1860, "door_state", "closed"),
            (0, "refrigeration_state", "running"),
            (0, "vehicle_state", "stopped"),
        ],
    ),
    EvalCase(
        case_id="door_04",
        family="door",
        expected_outcome=SUPPORTED,
        expected_hypothesis="door_exposure",
        note="Two openings, only the second overlaps the excursion.",
        reference=[
            (0, 5.0), (300, 5.1), (1440, 5.2), (1740, 9.4),
            (2100, 9.5), (2460, 5.4), (3000, 5.1),
        ],
        events=[
            (240, "door_state", "open"),
            (300, "door_state", "closed"),
            (1440, "door_state", "open"),
            (2160, "door_state", "closed"),
            (0, "refrigeration_state", "running"),
        ],
    ),
    # ── Refrigeration problem ──────────────────────────────────────────────
    EvalCase(
        case_id="refrigeration_01",
        family="refrigeration",
        expected_outcome=SUPPORTED,
        expected_hypothesis="refrigeration_problem",
        note="Explicit fault, then a slow monotonic climb with no door activity.",
        reference=[(0, 4.8), (600, 5.4), (1200, 7.6), (1800, 9.2), (2700, 10.4)],
        events=[
            (0, "refrigeration_state", "running"),
            (540, "refrigeration_state", "fault"),
            (0, "door_state", "closed"),
            (0, "vehicle_state", "moving"),
        ],
    ),
    EvalCase(
        case_id="refrigeration_02",
        family="refrigeration",
        expected_outcome=SUPPORTED,
        expected_hypothesis="refrigeration_problem",
        note="Unit stops then restarts; temperature recovers after the restart.",
        reference=[(0, 4.5), (300, 4.6), (900, 8.9), (1500, 9.6), (1980, 5.0), (2700, 4.6)],
        events=[
            (0, "refrigeration_state", "running"),
            (360, "refrigeration_state", "stopped"),
            (1560, "refrigeration_state", "running"),
            (0, "door_state", "closed"),
        ],
    ),
    EvalCase(
        case_id="refrigeration_03",
        family="refrigeration",
        expected_outcome=SUPPORTED,
        expected_hypothesis="refrigeration_problem",
        note="Fault reported after the rise began; still the only explanation on offer.",
        reference=[(0, 5.2), (600, 6.8), (1200, 8.8), (2100, 10.1), (2700, 10.6)],
        events=[
            (0, "refrigeration_state", "running"),
            (1260, "refrigeration_state", "fault"),
            (0, "door_state", "closed"),
            (0, "vehicle_state", "moving"),
        ],
    ),
    EvalCase(
        case_id="refrigeration_04",
        family="refrigeration",
        expected_outcome=SUPPORTED,
        expected_hypothesis="refrigeration_problem",
        note="Below-range excursion: the unit over-cools after a fault.",
        reference=[(0, 4.6), (600, 3.4), (1200, 1.6), (1800, 0.9), (2700, 0.6)],
        events=[
            (0, "refrigeration_state", "running"),
            (480, "refrigeration_state", "fault"),
            (0, "door_state", "closed"),
        ],
    ),
    # ── Sensor disagreement ────────────────────────────────────────────────
    EvalCase(
        case_id="sensor_01",
        family="sensor",
        expected_outcome=SUPPORTED,
        expected_hypothesis="sensor_disagreement",
        note="Comparison sensor spikes far above a reference that stays in range.",
        reference=[(0, 5.0), (900, 5.1), (1800, 5.0), (2700, 5.1)],
        comparison=[(0, 5.2), (600, 9.4), (1500, 10.2), (2100, 9.8), (2700, 9.6)],
        events=[(0, "refrigeration_state", "running"), (0, "door_state", "closed")],
    ),
    EvalCase(
        case_id="sensor_02",
        family="sensor",
        expected_outcome=SUPPORTED,
        expected_hypothesis="sensor_disagreement",
        note="Both sensors excurse, but by very different magnitudes.",
        reference=[(0, 5.0), (600, 6.2), (1200, 8.4), (1800, 8.6), (2700, 8.3)],
        comparison=[(0, 5.3), (600, 9.8), (1200, 13.1), (1800, 13.4), (2700, 13.0)],
        events=[(0, "refrigeration_state", "running"), (0, "door_state", "closed")],
    ),
    EvalCase(
        case_id="sensor_03",
        family="sensor",
        expected_outcome=SUPPORTED,
        expected_hypothesis="sensor_disagreement",
        note="Comparison sensor reads far colder than the reference throughout.",
        reference=[(0, 6.0), (900, 6.4), (1800, 6.2), (2700, 6.1)],
        comparison=[(0, 2.2), (900, 0.8), (1800, 0.4), (2700, 0.6)],
        events=[(0, "refrigeration_state", "running"), (0, "door_state", "closed")],
    ),
    EvalCase(
        case_id="sensor_04",
        family="sensor",
        expected_outcome=SUPPORTED,
        expected_hypothesis="sensor_disagreement",
        note="Divergence appears only in the second half of the trip.",
        reference=[(0, 5.1), (900, 5.2), (1500, 5.3), (2100, 5.2), (2700, 5.1)],
        comparison=[(0, 5.3), (900, 5.4), (1500, 7.9), (2100, 11.2), (2700, 11.6)],
        events=[(0, "refrigeration_state", "running"), (0, "door_state", "closed")],
    ),
    # ── Ambiguous, missing or conflicting ──────────────────────────────────
    EvalCase(
        case_id="ambiguous_01",
        family="ambiguous",
        expected_outcome=UNRESOLVED,
        expected_hypothesis=None,
        note="Door opening and refrigeration fault overlap the same excursion.",
        reference=[(0, 5.0), (1140, 5.2), (1440, 9.3), (1800, 9.7), (2160, 5.4), (2700, 5.1)],
        events=[
            (1140, "door_state", "open"),
            (1860, "door_state", "closed"),
            (0, "refrigeration_state", "running"),
            (1140, "refrigeration_state", "fault"),
        ],
    ),
    EvalCase(
        case_id="ambiguous_02",
        family="ambiguous",
        expected_outcome=UNRESOLVED,
        expected_hypothesis=None,
        note="A clear excursion with no door, refrigeration or vehicle events at all.",
        reference=[(0, 5.0), (600, 6.4), (1200, 9.1), (1800, 9.5), (2700, 8.9)],
        events=[],
    ),
    EvalCase(
        case_id="ambiguous_03",
        family="ambiguous",
        expected_outcome=UNRESOLVED,
        expected_hypothesis=None,
        note="Excursion sits inside an unobserved gap, so no duration is establishable.",
        reference=[(0, 5.0), (600, 5.1), (900, 9.4), (1800, 9.6), (2400, 5.2), (2700, 5.1)],
        drop_reference=(960, 1020, 1080, 1140, 1200, 1260, 1320, 1380, 1440, 1500, 1560, 1620, 1680),
        events=[
            (900, "door_state", "open"),
            (1740, "door_state", "closed"),
            (0, "refrigeration_state", "running"),
        ],
    ),
    EvalCase(
        case_id="ambiguous_04",
        family="ambiguous",
        expected_outcome=UNRESOLVED,
        expected_hypothesis=None,
        note="Door state goes unknown mid-excursion; the comparison sensor is absent.",
        reference=[(0, 5.0), (600, 5.2), (1200, 9.2), (1800, 9.4), (2400, 5.3), (2700, 5.0)],
        drop_comparison_entirely=True,
        events=[
            (1140, "door_state", "open"),
            (1320, "door_state", "unknown"),
            (2040, "door_state", "closed"),
            (0, "refrigeration_state", "running"),
        ],
    ),
    # ── Normal control ─────────────────────────────────────────────────────
    EvalCase(
        case_id="normal_01",
        family="normal",
        expected_outcome=NO_EXCURSION,
        expected_hypothesis=None,
        note="Steady mid-range hold with complete coverage.",
        reference=[(0, 5.0), (900, 5.1), (1800, 4.9), (2700, 5.0)],
        events=[(0, "refrigeration_state", "running"), (0, "door_state", "closed")],
    ),
    EvalCase(
        case_id="normal_02",
        family="normal",
        expected_outcome=NO_EXCURSION,
        expected_hypothesis=None,
        note="Brief door opening that never pushes the temperature out of range.",
        reference=[(0, 4.6), (600, 4.8), (900, 6.9), (1200, 5.1), (2700, 4.7)],
        events=[
            (840, "door_state", "open"),
            (1020, "door_state", "closed"),
            (0, "refrigeration_state", "running"),
        ],
    ),
    EvalCase(
        case_id="normal_03",
        family="normal",
        expected_outcome=NO_EXCURSION,
        expected_hypothesis=None,
        note="Runs close to the upper limit and touches 8.0 without exceeding it.",
        reference=[(0, 7.4), (900, 7.9), (1500, 8.0), (2100, 7.8), (2700, 7.5)],
        # Explicit, because the default mirror runs 0.3C warmer and would cross 8.0.
        comparison=[(0, 7.1), (900, 7.6), (1500, 7.7), (2100, 7.5), (2700, 7.2)],
        events=[(0, "refrigeration_state", "running"), (0, "door_state", "closed")],
    ),
    EvalCase(
        case_id="normal_04",
        family="normal",
        expected_outcome=NO_EXCURSION,
        expected_hypothesis=None,
        note="Runs close to the lower limit and touches 2.0 without going below.",
        reference=[(0, 2.6), (900, 2.2), (1500, 2.0), (2100, 2.3), (2700, 2.7)],
        events=[(0, "refrigeration_state", "running"), (0, "vehicle_state", "moving")],
    ),
]

BY_ID = {case.case_id: case for case in HOLDOUT}
