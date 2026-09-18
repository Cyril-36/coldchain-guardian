from __future__ import annotations

import base64
import json

from coldchain.storage import MemoryStorage, TemporaryStorageError

from .conftest import NOW, decode, event
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
    serialized = allowed["body"].lower()
    for forbidden in ("expected_cause", "expected_hypothesis", "ground_truth", "answer"):
        assert forbidden not in serialized


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


def test_unauthenticated_post_is_401_and_owner_spoof_is_rejected(api_factory) -> None:
    app, _, _ = api_factory()
    anonymous = app.handle(
        event(
            "POST",
            "/v1/runs",
            subject=None,
            body={"scenario_id": "normal_control", "seed": 1},
            headers={"Idempotency-Key": "key"},
        )
    )
    spoofed = app.handle(
        event(
            "POST",
            "/v1/runs",
            body={"scenario_id": "normal_control", "seed": 1, "owner_sub": "operator-b"},
            headers={"Idempotency-Key": "key"},
        )
    )

    assert anonymous["statusCode"] == 401
    assert spoofed["statusCode"] == 422


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


def test_each_private_demo_subresource_returns_404(api_factory) -> None:
    app, _, _ = api_factory()
    run_id = decode(_create(app))["run_id"]

    for suffix in ("", "/snapshot", "/report", "/download"):
        response = app.handle(event("GET", f"/v1/demo-runs/{run_id}{suffix}", subject=None))
        assert response["statusCode"] == 404


def test_public_listing_uses_curated_index_and_is_limited_to_five(api_factory) -> None:
    storage = MemoryStorage(clock=lambda: NOW)
    app, _, _ = api_factory(storage=storage)
    public_ids = []
    for index in range(6):
        run = storage.create_or_get_run(
            f"demo-owner-{index}",
            f"demo-key-{index}",
            f"request-hash-{index}",
            {"scenario_id": "normal_control", "seed": index, "base_timestamp": NOW},
        )
        storage.set_public_demo(run.run_id, {"label": f"Demo {index}"})
        public_ids.append(run.run_id)
    private = storage.create_or_get_run(
        "private-owner",
        "private-key",
        "private-hash",
        {"scenario_id": "normal_control", "seed": 99, "base_timestamp": NOW},
    )

    response = app.handle(event("GET", "/v1/demo-runs", subject=None))
    body = decode(response)

    assert response["statusCode"] == 200
    assert len(body) == 5
    assert {item["run_id"] for item in body} == set(public_ids[:5])
    assert private.run_id not in {item["run_id"] for item in body}


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


def test_invalid_body_is_validated_before_missing_idempotency_key(api_factory) -> None:
    app, _, _ = api_factory()
    invalid = event("POST", "/v1/runs")
    invalid["body"] = "{"

    response = app.handle(invalid)

    assert response["statusCode"] == 422
    assert decode(response)["error"]["code"] == "invalid_json"


def test_base64_body_and_case_insensitive_idempotency_header_are_supported(api_factory) -> None:
    app, _, _ = api_factory()
    request = event("POST", "/v1/runs", headers={"IDEMPOTENCY-KEY": "key"})
    request["body"] = base64.b64encode(
        json.dumps({"scenario_id": "normal_control", "seed": 1}).encode()
    ).decode()
    request["isBase64Encoded"] = True

    response = app.handle(request)

    assert response["statusCode"] == 202


def test_invalid_scenario_and_idempotency_keys_are_rejected(api_factory) -> None:
    app, _, _ = api_factory()
    invalid_scenario = app.handle(
        event(
            "POST",
            "/v1/runs",
            body={"scenario_id": "hidden_answer", "seed": 1},
            headers={"Idempotency-Key": "key"},
        )
    )
    missing_key = app.handle(
        event("POST", "/v1/runs", body={"scenario_id": "normal_control", "seed": 1})
    )
    blank_key = app.handle(
        event(
            "POST",
            "/v1/runs",
            body={"scenario_id": "normal_control", "seed": 1},
            headers={"Idempotency-Key": "   "},
        )
    )
    long_key = app.handle(
        event(
            "POST",
            "/v1/runs",
            body={"scenario_id": "normal_control", "seed": 1},
            headers={"Idempotency-Key": "x" * 201},
        )
    )

    assert invalid_scenario["statusCode"] == 422
    assert missing_key["statusCode"] == 422
    assert blank_key["statusCode"] == 422
    assert long_key["statusCode"] == 422


def test_known_routes_with_unsupported_methods_return_405(api_factory) -> None:
    app, _, _ = api_factory()
    run_id = "00000000-0000-4000-8000-000000000000"

    for method, path in (
        ("POST", "/v1/health"),
        ("POST", "/v1/scenarios"),
        ("GET", "/v1/runs"),
        ("DELETE", f"/v1/runs/{run_id}"),
        ("POST", f"/v1/demo-runs/{run_id}"),
    ):
        response = app.handle(event(method, path))
        assert response["statusCode"] == 405
        assert decode(response)["error"]["code"] == "method_not_allowed"


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


class UnsafeMessageStorage(MemoryStorage):
    def create_or_get_run(self, *args, **kwargs):
        raise TemporaryStorageError("AWS_ACCESS_KEY=do-not-leak raw provider response")


class UnexpectedFailureStorage(MemoryStorage):
    def create_or_get_run(self, *args, **kwargs):
        raise RuntimeError("secret provider traceback content")


def test_storage_and_unexpected_errors_are_sanitized(api_factory, caplog) -> None:
    storage_app, _, _ = api_factory(storage=UnsafeMessageStorage(clock=lambda: NOW))
    unexpected_app, _, _ = api_factory(storage=UnexpectedFailureStorage(clock=lambda: NOW))

    storage_response = _create(storage_app)
    unexpected_response = _create(unexpected_app)
    combined = storage_response["body"] + unexpected_response["body"] + caplog.text

    assert storage_response["statusCode"] == 503
    assert unexpected_response["statusCode"] == 500
    assert "AWS_ACCESS_KEY" not in combined
    assert "secret provider traceback" not in combined
    assert "traceback" not in storage_response["body"].lower()
    assert decode(storage_response)["error"]["request_id"] == "request-123"
