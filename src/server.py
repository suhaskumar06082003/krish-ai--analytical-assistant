from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from .auth import EntraJwtTokenVerifier, StaticBearerTokenVerifier
from .business_context import render_server_instructions, write_business_context
from .config import Settings
from .mcp_compat import McpHttpCompatibilityMiddleware
from .schema_catalog import generate_metadata_artifacts, load_schema_catalog
from .tools.registry import register_tools


@dataclass(frozen=True)
class AppState:
    settings: Settings


class CompatibleFastMCP(FastMCP):
    def streamable_http_app(self):
        app = super().streamable_http_app()
        app.add_middleware(
            McpHttpCompatibilityMiddleware,
            streamable_http_path=self.settings.streamable_http_path,
        )
        return app


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_mcp_server(settings: Settings) -> FastMCP:
    configure_logging(settings)
    logger = logging.getLogger(__name__)

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[AppState]:
        settings.artifact_dir.mkdir(parents=True, exist_ok=True)
        if settings.metadata_on_startup:
            try:
                catalog = generate_metadata_artifacts(settings)
                logger.info("metadata_generated table_count=%s", catalog.get("table_count", 0))
            except Exception as exc:
                logger.exception("metadata_generation_failed error=%s", exc)
                if not settings.business_context_path.exists():
                    fallback_catalog = load_schema_catalog(settings.schema_catalog_path)
                    write_business_context(settings.business_context_path, fallback_catalog)
                if settings.fail_on_startup_metadata:
                    raise
        yield AppState(settings=settings)

    auth_kwargs = {}
    if settings.mcp_auth_tenant_id and settings.mcp_auth_client_id:
        if not settings.mcp_public_url:
            raise RuntimeError("MCP_PUBLIC_URL is required when Microsoft Entra MCP auth is enabled.")
        audience = settings.mcp_auth_audience or f"api://{settings.mcp_auth_client_id}"
        oauth_metadata_issuer_url = settings.mcp_auth_issuer_url or settings.public_base_url
        required_scopes = [settings.mcp_required_scope] if settings.mcp_required_scope else None
        auth_kwargs = {
            "token_verifier": EntraJwtTokenVerifier(
                tenant_id=settings.mcp_auth_tenant_id,
                client_id=settings.mcp_auth_client_id,
                audience=audience,
                required_scope=settings.mcp_required_scope,
            ),
            "auth": AuthSettings(
                issuer_url=AnyHttpUrl(oauth_metadata_issuer_url),
                required_scopes=required_scopes,
                resource_server_url=AnyHttpUrl(settings.mcp_public_url),
            ),
        }
        logger.info("mcp_entra_auth_enabled=true")
    elif settings.mcp_bearer_token:
        if not settings.mcp_public_url:
            raise RuntimeError("MCP_PUBLIC_URL is required when MCP_BEARER_TOKEN is set.")
        issuer_url = settings.mcp_auth_issuer_url or settings.mcp_public_url
        required_scopes = [settings.mcp_required_scope] if settings.mcp_required_scope else None
        auth_kwargs = {
            "token_verifier": StaticBearerTokenVerifier(
                expected_token=settings.mcp_bearer_token,
                required_scope=settings.mcp_required_scope or None,
            ),
            "auth": AuthSettings(
                issuer_url=AnyHttpUrl(issuer_url),
                required_scopes=required_scopes,
                resource_server_url=AnyHttpUrl(settings.mcp_public_url),
            ),
        }
        logger.info("mcp_bearer_auth_enabled=true")
    else:
        logger.warning("mcp_auth_enabled=false")

    mcp = CompatibleFastMCP(
        "Hangyo Silver Azure SQL MCP",
        instructions=render_server_instructions(),
        host=settings.fastmcp_host,
        port=settings.fastmcp_port,
        log_level=settings.log_level,
        json_response=settings.fastmcp_json_response,
        stateless_http=settings.fastmcp_stateless_http,
        lifespan=lifespan,
        **auth_kwargs,
    )

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "hangyo-silver-azure-sql-mcp"})

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def root(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "hangyo-silver-azure-sql-mcp",
                "mcp_endpoint": "/mcp",
                "health_endpoint": "/health",
            }
        )

    @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"], include_in_schema=False)
    async def oauth_authorization_server_metadata(_: Request) -> JSONResponse:
        if not settings.mcp_auth_tenant_id or not settings.mcp_auth_client_id:
            return JSONResponse({"error": "OAuth authorization server metadata is not configured."}, status_code=404)
        issuer_url = settings.public_base_url or settings.mcp_public_url
        oauth_base = issuer_url.rstrip("/")
        scopes = ["openid", "profile", "email", "offline_access"]
        if settings.mcp_required_scope:
            scopes.append(settings.mcp_required_scope)
        return JSONResponse(
            {
                "issuer": issuer_url,
                "authorization_endpoint": f"{oauth_base}/oauth/authorize",
                "token_endpoint": f"{oauth_base}/oauth/token",
                "jwks_uri": f"https://login.microsoftonline.com/{settings.mcp_auth_tenant_id}/discovery/v2.0/keys",
                "userinfo_endpoint": "https://graph.microsoft.com/oidc/userinfo",
                "response_types_supported": ["code"],
                "response_modes_supported": ["query"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
                "scopes_supported": scopes,
            }
        )

    @mcp.custom_route("/.well-known/openid-configuration", methods=["GET"], include_in_schema=False)
    async def openid_configuration(_: Request) -> JSONResponse:
        return JSONResponse({"error": "OpenID discovery is not served by this MCP relay."}, status_code=404)

    @mcp.custom_route("/oauth/authorize", methods=["GET"], include_in_schema=False)
    async def oauth_authorize(request: Request) -> RedirectResponse:
        if not settings.mcp_auth_tenant_id:
            return RedirectResponse("/", status_code=302)
        query = dict(request.query_params.multi_items())
        query.pop("resource", None)
        # Ensure Entra issues a refresh token so clients renew access tokens silently
        # instead of forcing the user to sign in again when the access token expires.
        scope_parts = query.get("scope", "").split()
        for required_scope in ("openid", "offline_access"):
            if required_scope not in scope_parts:
                scope_parts.append(required_scope)
        query["scope"] = " ".join(scope_parts)
        entra_authorize_url = f"https://login.microsoftonline.com/{settings.mcp_auth_tenant_id}/oauth2/v2.0/authorize"
        logger.info("oauth_authorize_redirect tenant_id=%s", settings.mcp_auth_tenant_id)
        return RedirectResponse(f"{entra_authorize_url}?{urlencode(query)}", status_code=302)

    @mcp.custom_route("/oauth/token", methods=["POST"], include_in_schema=False)
    async def oauth_token(request: Request) -> Response:
        if not settings.mcp_auth_tenant_id:
            return JSONResponse({"error": "OAuth token endpoint is not configured."}, status_code=404)

        body = (await request.body()).decode("utf-8")
        form = parse_qs(body, keep_blank_values=True)
        form.pop("resource", None)
        encoded_body = urlencode(form, doseq=True).encode("utf-8")

        headers = MutableHeaders()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        authorization = request.headers.get("authorization")
        if authorization:
            headers["Authorization"] = authorization

        entra_token_url = f"https://login.microsoftonline.com/{settings.mcp_auth_tenant_id}/oauth2/v2.0/token"
        token_request = UrlRequest(entra_token_url, data=encoded_body, headers=dict(headers), method="POST")
        try:
            with urlopen(token_request, timeout=30) as response:
                logger.info("oauth_token_relay status=%s", response.status)
                return Response(
                    response.read(),
                    status_code=response.status,
                    media_type=response.headers.get("content-type", "application/json"),
                )
        except HTTPError as exc:
            logger.warning("oauth_token_relay_failed status=%s", exc.code)
            return Response(
                exc.read(),
                status_code=exc.code,
                media_type=exc.headers.get("content-type", "application/json"),
            )

    @mcp.resource("business://context", mime_type="text/markdown")
    def business_context_resource() -> str:
        if not settings.business_context_path.exists():
            write_business_context(settings.business_context_path, load_schema_catalog(settings.schema_catalog_path))
        return settings.business_context_path.read_text(encoding="utf-8")

    @mcp.resource("schema://catalog", mime_type="application/json")
    def schema_catalog_resource() -> str:
        return settings.schema_catalog_path.read_text(encoding="utf-8") if settings.schema_catalog_path.exists() else "{}"

    register_tools(mcp, settings)
    return mcp


settings = Settings.from_env()
mcp = create_mcp_server(settings)


def main() -> None:
    transport = settings.mcp_transport
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError("MCP_TRANSPORT must be one of: stdio, sse, streamable-http")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
