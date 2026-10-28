from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import MutableHeaders


class McpHttpCompatibilityMiddleware:
    """Smooth over harmless client differences around MCP HTTP probing.

    Some remote clients probe with broad or missing Accept headers. The MCP
    SDK is intentionally strict, so this middleware normalizes those headers
    before requests reach the SDK handler. It also maps MCP-looking root POSTs
    to the configured streamable HTTP path, while leaving normal browser root
    GETs alone.
    """

    def __init__(self, app: Callable[..., Awaitable[Any]], *, streamable_http_path: str = "/mcp"):
        self.app = app
        self.streamable_http_path = streamable_http_path

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]) -> None:
        if scope.get("type") == "http":
            headers = MutableHeaders(scope=scope)
            method = str(scope.get("method", "")).upper()
            path = str(scope.get("path", ""))
            accept = headers.get("accept", "")
            content_type = headers.get("content-type", "")

            if path == "/" and self._looks_like_mcp_request(method, accept, content_type, headers):
                scope["path"] = self.streamable_http_path
                scope["raw_path"] = self.streamable_http_path.encode("ascii")
                path = self.streamable_http_path

            if path == self.streamable_http_path and method in {"GET", "POST", "DELETE"}:
                headers["accept"] = self._normalized_accept(accept)

        await self.app(scope, receive, send)

    @staticmethod
    def _looks_like_mcp_request(
        method: str,
        accept: str,
        content_type: str,
        headers: MutableHeaders,
    ) -> bool:
        if method in {"POST", "DELETE"}:
            return True
        lower_accept = accept.lower()
        return (
            "mcp-session-id" in headers
            or "application/json" in content_type.lower()
            or "text/event-stream" in lower_accept
        )

    @staticmethod
    def _normalized_accept(accept: str) -> str:
        lower_accept = accept.lower().strip()
        if not lower_accept or lower_accept == "*/*":
            return "application/json, text/event-stream"

        parts = [part.strip() for part in accept.split(",") if part.strip()]
        if "application/json" not in lower_accept:
            parts.append("application/json")
        if "text/event-stream" not in lower_accept:
            parts.append("text/event-stream")
        return ", ".join(parts)
