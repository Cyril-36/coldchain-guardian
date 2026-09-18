from __future__ import annotations

from .conftest import decode, event
from .test_runs import _create, complete_run


def test_health_is_public_and_contains_no_resource_names(api_factory) -> None:
    app, _, _ = api_factory()

    response = app.handle(event("GET", "/v1/health", subject=None))

    assert response["statusCode"] == 200
    assert decode(response) == {"status": "ok", "schema_version": "1.0", "build_sha": "abc123"}


def test_scenarios_require_verified_operator(api_factory) -> None:
    app, _, _ = api_factory()

    denied = app.handle(event("GET", "/v1/scenarios", subject=None))
    allowed = app.handle(event("GET", "/v1/scenarios"))

    assert denied["statusCode"] == 401
    assert allowed["statusCode"] == 200
    assert {item["scenario_id"] for item in decode(allowed)} == {
        "normal_control",
        "door_exposure",
        "refrigeration_problem",
        "sensor_disagreement",
        "ambiguous_incident",
    }


def test_writes_require_custom_scope_and_allowlisted_subject(api_factory) -> None:
    app, _, _ = api_factory()
    no_scope = app.handle(
        event(
            "POST",
            "/v1/runs",
            body={"scenario_id": "normal_control", "seed": 1},
            scope="openid",
            headers={"Idempotency-Key": "key"},
        )
    )
    outsider = app.handle(event("GET", "/v1/scenarios", subject="outsider"))

    assert no_scope["statusCode"] == 403
    assert outsider["statusCode"] == 403


def test_public_routes_never_expose_non_curated_run(api_factory) -> None:
    app, storage, _ = api_factory()
    run_id = decode(_create(app))["run_id"]

    private = app.handle(event("GET", f"/v1/demo-runs/{run_id}", subject=None))
    assert private["statusCode"] == 404

    complete_run(storage, run_id)
    storage.set_public_demo(run_id, {"label": "Curated synthetic control"})
    public = app.handle(event("GET", f"/v1/demo-runs/{run_id}", subject=None))
    snapshot = app.handle(event("GET", f"/v1/demo-runs/{run_id}/snapshot", subject=None))
    report = app.handle(event("GET", f"/v1/demo-runs/{run_id}/report", subject=None))
    download = app.handle(event("GET", f"/v1/demo-runs/{run_id}/download", subject=None))

    statuses = [
        public["statusCode"],
        snapshot["statusCode"],
        report["statusCode"],
        download["statusCode"],
    ]
    assert statuses == [200, 200, 200, 200]
    body = decode(public)
    assert "owner_sub" not in body
    assert "scenario_id" not in body


def test_invalid_json_unknown_fields_and_oversized_body_are_rejected(api_factory) -> None:
    app, _, _ = api_factory()
    invalid = event("POST", "/v1/runs", headers={"Idempotency-Key": "key"})
    invalid["body"] = "{"
    unknown = event(
        "POST",
        "/v1/runs",
        body={"scenario_id": "normal_control", "seed": 1, "extra": True},
        headers={"Idempotency-Key": "key"},
    )
    large = event("POST", "/v1/runs", headers={"Idempotency-Key": "key"})
    large["body"] = '"' + ("x" * 17_000) + '"'

    assert app.handle(invalid)["statusCode"] == 422
    assert app.handle(unknown)["statusCode"] == 422
    assert app.handle(large)["statusCode"] == 413


def test_error_envelope_contains_request_id_without_traceback(api_factory) -> None:
    app, _, _ = api_factory()
    response = app.handle(event("GET", "/v1/not-a-route", subject=None))
    body = decode(response)

    assert response["statusCode"] == 404
    assert body == {
        "error": {
            "code": "not_found",
            "message": "Route not found",
            "request_id": "request-123",
            "retryable": False,
        }
    }
