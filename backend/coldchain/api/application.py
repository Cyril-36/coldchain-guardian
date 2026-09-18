"""API Gateway HTTP API v2 parsing and route dispatch."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from coldchain.contracts import CreateRunRequest, ErrorDetail, ErrorResponse, ReviewRequest
from coldchain.storage import StorageError

from .service import ApiService

_MAX_REQUEST_BYTES = 16_384
_RUN_ROUTE = re.compile(r"^/v1/runs/([^/]+)(?:/(snapshot|report|download|review))?$")
_DEMO_ROUTE = re.compile(r"^/v1/demo-runs/([^/]+)(?:/(snapshot|report|download))?$")


class ApiRequestError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)

    def as_lambda_result(self) -> dict[str, Any]:
        headers = {"content-type": "application/json", **self.headers}
        return {
            "statusCode": self.status_code,
            "headers": headers,
            "body": json.dumps(self.body, allow_nan=False, separators=(",", ":")),
            "isBase64Encoded": False,
        }


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


class ApiApplication:
    """Small framework-free HTTP application with injected business dependencies."""

    def __init__(
        self,
        service: ApiService,
        *,
        build_sha: str = "unknown",
        allowed_operator_subs: frozenset[str] | None = None,
    ) -> None:
        self.service = service
        self.build_sha = build_sha
        self.allowed_operator_subs = allowed_operator_subs

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        request_id = str(event.get("requestContext", {}).get("requestId") or "unknown")
        try:
            response = self._dispatch(event)
        except ApiRequestError as error:
            response = self._error(
                error.status_code, error.code, str(error), request_id, error.retryable
            )
        except ValidationError as error:
            message = error.errors(include_url=False)[0].get("msg", "invalid request")
            response = self._error(422, "invalid_request", str(message), request_id, False)
        except StorageError as error:
            code = re.sub(r"(?<!^)(?=[A-Z])", "_", type(error).__name__).lower()
            if code.endswith("_error"):
                code = code[:-6]
            response = self._error(
                error.status_code, code, str(error).strip("'"), request_id, error.retryable
            )
            run_id = getattr(error, "run_id", None)
            if isinstance(run_id, str):
                response = HttpResponse(response.status_code, response.body, {"x-run-id": run_id})
        except ValueError as error:
            response = self._error(422, "invalid_request", str(error), request_id, False)
        except Exception:
            response = self._error(
                500, "internal_error", "An unexpected error occurred", request_id, True
            )
        return response.as_lambda_result()

    @staticmethod
    def _error(
        status_code: int,
        code: str,
        message: str,
        request_id: str,
        retryable: bool,
    ) -> HttpResponse:
        body = ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
                request_id=request_id,
                retryable=retryable,
            )
        )
        return HttpResponse(status_code, body.model_dump(mode="json"))

    def _dispatch(self, event: dict[str, Any]) -> HttpResponse:
        context = event.get("requestContext", {})
        method = str(context.get("http", {}).get("method") or event.get("httpMethod") or "GET")
        path = str(event.get("rawPath") or event.get("path") or "/")
        headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}

        if method == "GET" and path == "/v1/health":
            return HttpResponse(
                200,
                {"status": "ok", "schema_version": "1.0", "build_sha": self.build_sha},
            )
        if method == "GET" and path == "/v1/demo-runs":
            public_runs = [
                run for run in self.service.storage.list_public_runs() if run.is_public_demo
            ]
            return HttpResponse(200, [_dump(run) for run in public_runs[:5]])

        demo_match = _DEMO_ROUTE.fullmatch(path)
        if demo_match and method == "GET":
            run = self.service.get_public_run(demo_match.group(1))
            resource = demo_match.group(2)
            if resource == "snapshot":
                return HttpResponse(200, _dump(self.service.get_snapshot(run)))
            if resource == "report":
                return HttpResponse(200, _dump(self.service.get_report(run)))
            if resource == "download":
                return HttpResponse(200, self.service.get_download(run))
            return HttpResponse(200, _dump(run))

        if method == "GET" and path == "/v1/scenarios":
            self._operator(event, require_write=False)
            return HttpResponse(200, self.service.scenarios())
        if method == "POST" and path == "/v1/runs":
            owner_sub = self._operator(event, require_write=True)
            key = headers.get("idempotency-key", "")
            if not key.strip() or len(key) > 200:
                raise ApiRequestError(
                    422, "invalid_idempotency_key", "Idempotency-Key must be 1-200 characters"
                )
            request = CreateRunRequest.model_validate(self._body(event))
            response = self.service.create_run(owner_sub, key, request)
            return HttpResponse(202, _dump(response))

        run_match = _RUN_ROUTE.fullmatch(path)
        if run_match:
            resource = run_match.group(2)
            require_write = method == "POST" and resource == "review"
            if not ((method == "GET" and resource != "review") or require_write):
                raise ApiRequestError(404, "not_found", "Route not found")
            owner_sub = self._operator(event, require_write=require_write)
            run = self.service.get_owned_run(run_match.group(1), owner_sub)
            if resource == "snapshot":
                return HttpResponse(200, _dump(self.service.get_snapshot(run)))
            if resource == "report":
                return HttpResponse(200, _dump(self.service.get_report(run)))
            if resource == "download":
                return HttpResponse(200, self.service.get_download(run))
            if resource == "review":
                review = self.service.save_review(
                    run, owner_sub, ReviewRequest.model_validate(self._body(event))
                )
                return HttpResponse(200, _dump(review))
            return HttpResponse(200, _dump(run))

        raise ApiRequestError(404, "not_found", "Route not found")

    def _operator(self, event: dict[str, Any], *, require_write: bool) -> str:
        claims = (
            event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
        )
        subject = claims.get("sub") if isinstance(claims, dict) else None
        if not isinstance(subject, str) or not subject:
            raise ApiRequestError(401, "unauthenticated", "Authentication is required")
        if self.allowed_operator_subs is not None and subject not in self.allowed_operator_subs:
            raise ApiRequestError(403, "forbidden", "Operator is not allowed")
        if require_write:
            raw_scope = claims.get("scope", "")
            scopes = set(raw_scope.split()) if isinstance(raw_scope, str) else set(raw_scope or [])
            if "coldchain/write" not in scopes:
                raise ApiRequestError(403, "forbidden", "coldchain/write scope is required")
        return subject

    @staticmethod
    def _body(event: dict[str, Any]) -> Any:
        raw = event.get("body")
        if raw is None:
            raise ApiRequestError(422, "invalid_request", "JSON request body is required")
        try:
            data = (
                base64.b64decode(raw, validate=True)
                if event.get("isBase64Encoded")
                else raw.encode()
            )
        except (ValueError, TypeError) as error:
            raise ApiRequestError(422, "invalid_request", "Request body is invalid") from error
        if len(data) > _MAX_REQUEST_BYTES:
            raise ApiRequestError(413, "payload_too_large", "Request body is too large")
        try:
            return json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiRequestError(422, "invalid_json", "Request body must be valid JSON") from error
