"""The SAM template must never route a CORS preflight through the JWT authorizer.

A browser sends the preflight OPTIONS without an Authorization header. API
Gateway answers preflight itself only when no route matches OPTIONS; a greedy
`ANY` route matches it, inherits DefaultAuthorizer and returns 401, which fails
the preflight and breaks every authenticated call from the browser.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[3] / "infra" / "template.yaml"
PROXY_PATH = "/v1/{proxy+}"


def _http_api_routes() -> list[tuple[str, str]]:
    """Return (path, method) for every HttpApi event, ignoring comments."""

    lines = [
        line for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    routes: list[tuple[str, str]] = []
    path: str | None = None
    for line in lines:
        if match := re.match(r"\s*Path:\s*(\S+)\s*$", line):
            path = match.group(1)
        elif match := re.match(r"\s*Method:\s*(\S+)\s*$", line):
            assert path is not None, "Method: appeared before any Path:"
            routes.append((path, match.group(1).upper()))
            path = None
    return routes


def test_template_declares_http_api_routes() -> None:
    # Guards the parser itself: a silently-empty parse would make the
    # assertions below vacuous.
    routes = _http_api_routes()
    assert len(routes) >= 7, routes
    assert ("/v1/health", "GET") in routes


def test_no_any_method_route() -> None:
    any_routes = [path for path, method in _http_api_routes() if method == "ANY"]
    assert not any_routes, (
        f"ANY routes also match the CORS preflight and would be rejected by the "
        f"default JWT authorizer: {any_routes}. Declare explicit methods instead."
    )


def test_proxy_path_declares_get_and_post() -> None:
    methods = {method for path, method in _http_api_routes() if path == PROXY_PATH}
    assert methods == {"GET", "POST"}, methods
