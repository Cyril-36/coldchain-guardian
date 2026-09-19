# Synthetic telemetry simulator

The simulator produces recorded, synthetic telemetry for the ColdChain Guardian demonstration. It is not connected to a truck, physical logger or medicine, and its output does not establish real-world sensor accuracy or product suitability.

## Snapshot design

`generate_snapshot(scenario_id, seed, base_timestamp, snapshot_id=None, shipment_id=None)` creates a contract-shaped snapshot with:

- `source: "simulated"` and `schema_version: "1.0"`;
- exactly two air-temperature sensors, one reference and one comparison;
- an illustrative 2–8 °C policy with a 60-second expected interval;
- a 45-minute observation window;
- readings at both endpoints, giving 46 readings per sensor from minute 0 through minute 45;
- UTC ISO-8601 timestamps ending in `Z`; and
- observable door, refrigeration or vehicle events when the selected scenario provides them.

The policy is illustrative for this synthetic demonstration. It is not a universal pharmaceutical handling rule.

## Determinism and identifiers

Telemetry depends only on the scenario configuration, integer seed and fixed UTC base timestamp. The generator uses a local `random.Random(seed)` instance and never reads the wall clock or changes global random state. The same inputs reproduce the same readings and events. Different seeds vary bounded noise and selected event timing while preserving scenario invariants.

Callers may supply snapshot and shipment UUIDs. When omitted, deterministic opaque UUIDs are generated for local use. The API control layer allocates stable opaque IDs during run reservation and supplies them to the generator.

## Demonstration scenarios

The public control layer may offer five scenario choices:

1. **Normal control:** both sensors remain inside the illustrative policy range.
2. **Door exposure:** an opening is recorded, both sensors rise after a lag, the door closes and temperatures recover.
3. **Refrigeration problem:** the door remains closed, a stopped or fault equipment status is recorded and both sensors rise.
4. **Sensor disagreement:** the comparison sensor spikes while the reference remains stable, without confirming equipment-fault evidence.
5. **Ambiguous incident:** both sensors rise while decisive door and refrigeration observations are absent.

These descriptions are generator controls and public catalogue metadata. They are not included in the exported snapshot.

## Bounded noise

Each reading receives small seeded noise bounded to ±0.16 °C. Scenario profiles have sufficient margin so tested seeds cannot push the normal control outside its policy range, erase required incident excursions, make the stable sensor anomalous, or add causal evidence to the ambiguous case. Behavioural tests cover 50 seeds per scenario; this is engineering fixture coverage, not a claim about physical sensor behaviour.

## Validation and normalization

Simulator-side normalization enforces the documented v1 boundary and then validates the normalized result through the canonical Pydantic `Snapshot` model:

- exact fields and schema version;
- UUID identifiers and exactly one reference sensor;
- finite numeric temperatures;
- valid UTC timestamps and the snapshot cutoff;
- known sensor references;
- valid event type/value combinations;
- at most 2,000 readings, 200 events and 1 MiB of serialized JSON;
- deterministic ordering by timestamp and event ID; and
- deduplication of identical IDs with rejection of conflicting duplicates.

Simulator-specific code remains responsible for deterministic sorting and deduplication before canonical validation. It does not define a competing contract model.

## Hidden-label protection

The worker/investigator-facing snapshot intentionally excludes:

- `scenario_id`;
- expected cause or hypothesis;
- ground-truth or answer labels;
- diagnoses; and
- free-form explanatory notes.

Snapshot, shipment, sensor and record IDs are opaque UUID strings. Event sources identify only the synthetic emitting component, not the selected scenario. Tests recursively inspect exported field names and string values for forbidden truth markers and scenario identifiers.

## Limitations

The five patterns are controlled demonstration fixtures, not a model of every refrigeration system, route, packaging configuration or sensor placement. Noise is deliberately bounded, timestamps are perfectly scheduled, and each incident contains one investigation window. The simulator does not calculate excursion duration, infer causes, evaluate medicine, emulate packet loss, or make release/disposal decisions. Those responsibilities either belong to other workstreams or are outside the MVP.
