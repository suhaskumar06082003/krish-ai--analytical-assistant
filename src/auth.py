from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass

from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StaticBearerTokenVerifier:
    expected_token: str
    required_scope: str | None = "sql.read"

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self.expected_token:
            return None
        if not hmac.compare_digest(token, self.expected_token):
            return None
        scopes = [self.required_scope] if self.required_scope else []
        return AccessToken(
            token=token,
            client_id="static-bearer-client",
            scopes=scopes,
        )


@dataclass(frozen=True)
class EntraJwtTokenVerifier:
    tenant_id: str
    client_id: str
    audience: str
    required_scope: str

    @property
    def issuer_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    def _accepted_audiences(self) -> list[str]:
        audiences = [self.audience, self.client_id, f"api://{self.client_id}"]
        return list(dict.fromkeys(value for value in audiences if value))

    def _accepted_issuers(self) -> list[str]:
        return [
            self.issuer_url,
            f"https://sts.windows.net/{self.tenant_id}/",
        ]

    def _scope_matches(self, scopes: list[str]) -> bool:
        if not self.required_scope:
            return True
        required_short = self.required_scope.rsplit("/", 1)[-1]
        return self.required_scope in scopes or required_short in scopes

    def _normalized_scopes(self, scopes: list[str]) -> list[str]:
        normalized = list(scopes)
        required_short = self.required_scope.rsplit("/", 1)[-1]
        if required_short in scopes and self.required_scope not in normalized:
            normalized.append(self.required_scope)
        return normalized

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            import jwt
            from jwt import PyJWKClient
        except ImportError as exc:
            raise RuntimeError("PyJWT is required for Microsoft Entra authentication.") from exc

        try:
            signing_key = PyJWKClient(self.jwks_url).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._accepted_audiences(),
                issuer=self._accepted_issuers(),
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except Exception as exc:
            logger.warning("entra_token_validation_failed error=%s", exc)
            return None

        token_tenant = str(claims.get("tid", ""))
        if token_tenant and token_tenant.lower() != self.tenant_id.lower():
            logger.warning("entra_token_wrong_tenant tid=%s", token_tenant)
            return None

        scopes = str(claims.get("scp", "")).split()
        roles = claims.get("roles", [])
        if isinstance(roles, list):
            scopes.extend(str(role) for role in roles)

        if not self._scope_matches(scopes):
            logger.warning("entra_token_missing_scope required_scope=%s scopes=%s", self.required_scope, scopes)
            return None

        subject = str(claims.get("oid") or claims.get("sub") or "")
        client_id = str(claims.get("azp") or claims.get("appid") or self.client_id)
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=self._normalized_scopes(scopes),
            expires_at=int(claims["exp"]) if "exp" in claims else None,
            resource=str(claims.get("aud", "")),
            subject=subject or None,
            claims=claims,
        )
