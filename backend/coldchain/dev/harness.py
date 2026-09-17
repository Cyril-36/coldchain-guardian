"""Loopback-only development API harness.

Provides a local HTTP server using the same route logic with in-memory storage
and a synthetic operator context. Excluded from production packaging.

Usage: python -m coldchain.dev.harness
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from coldchain.contracts.enums import (
    GenerationMode,
    Outcome,
    PublicStage,
    RunStatus,
)
from coldchain.contracts.schemas import (
    ArtifactRef,
    CreateRunRequest,
    HealthResponse,
    QueueMessage,
    ReviewRequest,
    Run,
    Snapshot,
    Policy,
    Sensor,
    SensorRole,
    Reading,
    Event,
    EventType,
)
from coldchain.investigation.agent import investigate
from coldchain.storage.memory import MemoryStorage

# Synthetic operator for dev mode
DEV_OPERATOR_SUB = "dev-operator-001"
DEV_BUILD_SHA = "local-dev"

# Scenarios available in dev mode
DEV_SCENARIOS = {
    "door_exposure": "Door left open during loading",
    "refrigeration_fault": "Refrigeration unit stops working",
    "sensor_disagreement": "Sensors report different temperatures",
    "normal": "Normal shipment, no excursion",
}


class DevHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for the dev API harness."""

    storage: MemoryStorage  # Class-level storage shared across requests

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/")

        if path == "/v1/health":
            self._json_response(200, HealthResponse(build_sha=DEV_BUILD_SHA).model_dump())
        elif path == "/v1/scenarios":
            self._json_response(200, [
                {"scenario_id": k, "label": v} for k, v in DEV_SCENARIOS.items()
            ])
        elif path == "/v1/demo-runs":
            runs = self.storage.list_public_runs()
            self._json_response(200, [r.model_dump(mode="json") for r in runs])
        elif path.startswith("/v1/runs/") or path.startswith("/v1/demo-runs/"):
            self._handle_get_run(path)
        else:
            self._json_response(404, {"error": {"code": "not_found", "message": "Route not found", "request_id": str(uuid.uuid4()), "retryable": False}})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")

        if path == "/v1/runs":
            self._handle_create_run()
        elif path.endswith("/review"):
            self._handle_review(path)
        else:
            self._json_response(404, {"error": {"code": "not_found", "message": "Route not found", "request_id": str(uuid.uuid4()), "retryable": False}})

    def _handle_get_run(self, path: str) -> None:
        parts = path.split("/")
        run_id = parts[3] if len(parts) > 3 else None
        if not run_id:
            self._json_response(400, {"error": {"code": "bad_request", "message": "Missing run_id", "request_id": str(uuid.uuid4()), "retryable": False}})
            return

        run = self.storage.get_run(run_id)
        if run is None:
            self._json_response(404, {"error": {"code": "not_found", "message": "Run not found", "request_id": str(uuid.uuid4()), "retryable": False}})
            return

        # Sub-resource routing
        if path.endswith("/snapshot") and run.snapshot_ref:
            snapshot = self.storage.get_snapshot(run.snapshot_ref)
            self._json_response(200, json.loads(snapshot.model_dump_json()))
        elif path.endswith("/report") and run.report_ref:
            report = self.storage.get_report(run.report_ref)
            self._json_response(200, json.loads(report.model_dump_json()))
        elif path.endswith("/report") and not run.report_ref:
            self._json_response(409, {"error": {"code": "not_ready", "message": "Report not ready", "request_id": str(uuid.uuid4()), "retryable": False}})
        else:
            self._json_response(200, json.loads(run.model_dump_json()))

    def _handle_create_run(self) -> None:
        body = self._read_body()
        if body is None:
            return

        try:
            req = CreateRunRequest.model_validate_json(body)
        except Exception as e:
            self._json_response(422, {"error": {"code": "validation_error", "message": str(e), "request_id": str(uuid.uuid4()), "retryable": False}})
            return

        idempotency_key = self.headers.get("Idempotency-Key", str(uuid.uuid4()))
        request_hash = hashlib.sha256(body).hexdigest()

        try:
            run = self.storage.create_or_get_run(
                owner_sub=DEV_OPERATOR_SUB,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                metadata={"scenario_id": req.scenario_id, "seed": req.seed},
            )
        except ValueError as e:
            self._json_response(409, {"error": {"code": "conflict", "message": str(e), "request_id": str(uuid.uuid4()), "retryable": False}})
            return

        # Generate and store snapshot
        snapshot = _generate_dev_snapshot(req.scenario_id, req.seed)
        snapshot_ref = self.storage.put_snapshot(snapshot)

        # Update run with snapshot ref
        run = self.storage.get_run(run.run_id)
        if run:
            run = run.model_copy(update={"snapshot_ref": snapshot_ref})
            self.storage._runs[run.run_id] = run

        # Run investigation synchronously in dev mode
        report = investigate(
            snapshot=snapshot,
            run_id=run.run_id,
            model_id=None,
            use_bedrock=False,
        )
        report_ref = self.storage.put_report(report)

        terminal = RunStatus.completed if report.outcome.value != "unresolved" else RunStatus.needs_review
        if report.verification.status.value == "blocked":
            terminal = RunStatus.needs_review

        self.storage.complete_run(
            run_id=run.run_id,
            attempt_id="dev-attempt",
            status=terminal,
            report_ref=report_ref,
            summary=f"Dev investigation: {report.outcome.value}",
        )

        self._json_response(202, {
            "run_id": run.run_id,
            "status": terminal.value,
            "poll_url": f"/v1/runs/{run.run_id}",
        })

    def _handle_review(self, path: str) -> None:
        parts = path.split("/")
        run_id = parts[3] if len(parts) > 3 else None
        if not run_id:
            self._json_response(400, {"error": {"code": "bad_request", "message": "Missing run_id", "request_id": str(uuid.uuid4()), "retryable": False}})
            return

        body = self._read_body()
        if body is None:
            return

        try:
            req = ReviewRequest.model_validate_json(body)
        except Exception as e:
            self._json_response(422, {"error": {"code": "validation_error", "message": str(e), "request_id": str(uuid.uuid4()), "retryable": False}})
            return

        try:
            review = self.storage.save_review(
                run_id=run_id,
                actor_sub=DEV_OPERATOR_SUB,
                report_id=req.report_id,
                decision=req.decision,
                note=req.note,
            )
            self._json_response(200, json.loads(review.model_dump_json()))
        except Exception as e:
            self._json_response(400, {"error": {"code": "bad_request", "message": str(e), "request_id": str(uuid.uuid4()), "retryable": False}})

    def _read_body(self) -> bytes | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._json_response(400, {"error": {"code": "bad_request", "message": "Empty body", "request_id": str(uuid.uuid4()), "retryable": False}})
            return None
        return self.rfile.read(length)

    def _json_response(self, status: int, data: dict | list) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Dev-Mode", "true")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        print(f"[dev-api] {args[0]} {args[1]} {args[2]}")


def _generate_dev_snapshot(scenario_id: str, seed: int | None = None) -> Snapshot:
    """Generate a minimal synthetic snapshot for dev mode."""
    import random
    rng = random.Random(seed or 42)
    base_time = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)

    policy = Policy(
        policy_id="policy-dev-001",
        policy_version="1.0",
        min_c=2.0,
        max_c=8.0,
        expected_interval_seconds=60.0,
        max_gap_seconds=120.0,
    )

    sensors = [
        Sensor(sensor_id="sensor-ref-001", placement="cargo-center", role=SensorRole.reference),
        Sensor(sensor_id="sensor-cmp-001", placement="cargo-door", role=SensorRole.comparison),
    ]

    readings = []
    events = []
    snapshot_id = str(uuid.uuid4())

    if scenario_id == "normal":
        # All readings in range
        for i in range(46):
            t = base_time.replace(second=0) 
            from datetime import timedelta
            t = base_time + timedelta(seconds=60 * i)
            for s in sensors:
                readings.append(Reading(
                    event_id=str(uuid.uuid4()),
                    sensor_id=s.sensor_id,
                    observed_at=t,
                    temperature_c=round(4.0 + rng.uniform(-1, 1), 2),
                ))
    elif scenario_id == "door_exposure":
        # Door opens, temp rises, door closes, temp recovers
        for i in range(46):
            from datetime import timedelta
            t = base_time + timedelta(seconds=60 * i)
            if 10 <= i <= 25:
                ref_temp = round(5.0 + (i - 10) * 0.5, 2) if i <= 18 else round(9.0 - (i - 18) * 0.5, 2)
            else:
                ref_temp = round(4.0 + rng.uniform(-0.5, 0.5), 2)
            readings.append(Reading(
                event_id=str(uuid.uuid4()),
                sensor_id="sensor-ref-001",
                observed_at=t,
                temperature_c=min(ref_temp, 12.0),
            ))
            readings.append(Reading(
                event_id=str(uuid.uuid4()),
                sensor_id="sensor-cmp-001",
                observed_at=t,
                temperature_c=round(ref_temp + rng.uniform(-0.3, 0.3), 2),
            ))

        events.append(Event(
            event_id=str(uuid.uuid4()),
            observed_at=base_time + timedelta(minutes=10),
            event_type=EventType.door_state,
            value="open",
            source="simulated",
        ))
        events.append(Event(
            event_id=str(uuid.uuid4()),
            observed_at=base_time + timedelta(minutes=20),
            event_type=EventType.door_state,
            value="closed",
            source="simulated",
        ))
    else:
        # Default: simple excursion
        for i in range(46):
            from datetime import timedelta
            t = base_time + timedelta(seconds=60 * i)
            temp = round(5.0 + rng.uniform(-1, 3), 2)
            for s in sensors:
                readings.append(Reading(
                    event_id=str(uuid.uuid4()),
                    sensor_id=s.sensor_id,
                    observed_at=t,
                    temperature_c=temp,
                ))

    from datetime import timedelta
    cutoff = base_time + timedelta(minutes=45)

    return Snapshot(
        snapshot_id=snapshot_id,
        shipment_id=str(uuid.uuid4()),
        cutoff_at=cutoff,
        policy=policy,
        sensors=sensors,
        readings=readings,
        events=events,
    )


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the loopback dev API server."""
    DevHandler.storage = MemoryStorage()
    server = HTTPServer((host, port), DevHandler)
    print(f"[dev-api] ColdChain Guardian dev harness on http://{host}:{port}")
    print(f"[dev-api] X-Dev-Mode: true — excluded from production packaging")
    print(f"[dev-api] Available scenarios: {list(DEV_SCENARIOS.keys())}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dev-api] Shutting down")
        server.server_close()


if __name__ == "__main__":
    run_server()
