"""OAuth 2.1 provider using MCP SDK's OAuthAuthorizationServerProvider.

The SDK auto-generates all required endpoints:
- /.well-known/oauth-authorization-server
- /oauth/register  (Dynamic Client Registration, RFC 7591)
- /oauth/authorize
- /oauth/token

Based on: research/2026-03-31-mcp-server-implementation-research.md lines 148-182
"""

import hashlib
import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from jarvis.config import settings

# In-memory stores (replace with Redis/DB in production)
_clients: dict[str, OAuthClientInformationFull] = {}
_auth_codes: dict[str, AuthorizationCode] = {}
_access_tokens: dict[str, AccessToken] = {}
_refresh_tokens: dict[str, RefreshToken] = {}


class JarvisOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Self-hosted OAuth 2.1 Authorization Server for JARVIS MCP."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return _clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id:
            _clients[client_info.client_id] = client_info

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Generate authorization code and return redirect URL.

        For MVP, auto-authorize with a default dev user.
        Production: redirect to HTML login form → POST back → generate code.
        """
        code_str = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code_str,
            scopes=params.scopes or ["mcp:tools"],
            expires_at=time.time() + 600,  # 10 min expiry
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        _auth_codes[code_str] = auth_code

        # Build redirect URL with code + state
        redirect = str(params.redirect_uri)
        sep = "&" if "?" in redirect else "?"
        url = f"{redirect}{sep}code={code_str}"
        if params.state:
            url += f"&state={params.state}"
        return url

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        """Load and validate authorization code."""
        code = _auth_codes.get(authorization_code)
        if not code or code.client_id != (client.client_id or ""):
            return None
        if code.expires_at < time.time():
            _auth_codes.pop(authorization_code, None)
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Exchange authorization code for tokens."""
        _auth_codes.pop(authorization_code.code, None)

        client_id = client.client_id or ""

        # Create access token
        token_str = secrets.token_urlsafe(32)
        access_token = AccessToken(
            token=token_str,
            client_id=client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + settings.oauth_token_ttl_seconds,
        )
        _access_tokens[token_str] = access_token

        # Create refresh token
        refresh_str = secrets.token_urlsafe(32)
        refresh_token = RefreshToken(
            token=refresh_str,
            client_id=client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + settings.oauth_refresh_token_ttl_seconds,
        )
        _refresh_tokens[refresh_str] = refresh_token

        return OAuthToken(
            access_token=token_str,
            token_type="Bearer",
            expires_in=settings.oauth_token_ttl_seconds,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh_str,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Validate access token."""
        at = _access_tokens.get(token)
        if not at:
            return None
        if at.expires_at and at.expires_at < int(time.time()):
            _access_tokens.pop(token, None)
            return None
        return at

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        """Load refresh token."""
        rt = _refresh_tokens.get(refresh_token)
        if not rt or rt.client_id != (client.client_id or ""):
            return None
        if rt.expires_at and rt.expires_at < int(time.time()):
            _refresh_tokens.pop(refresh_token, None)
            return None
        return rt

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Exchange refresh token for new access token."""
        _refresh_tokens.pop(refresh_token.token, None)

        client_id = client.client_id or ""
        use_scopes = scopes or refresh_token.scopes

        token_str = secrets.token_urlsafe(32)
        access_token = AccessToken(
            token=token_str,
            client_id=client_id,
            scopes=use_scopes,
            expires_at=int(time.time()) + settings.oauth_token_ttl_seconds,
        )
        _access_tokens[token_str] = access_token

        # Issue new refresh token
        new_refresh_str = secrets.token_urlsafe(32)
        new_refresh = RefreshToken(
            token=new_refresh_str,
            client_id=client_id,
            scopes=use_scopes,
            expires_at=int(time.time()) + settings.oauth_refresh_token_ttl_seconds,
        )
        _refresh_tokens[new_refresh_str] = new_refresh

        return OAuthToken(
            access_token=token_str,
            token_type="Bearer",
            expires_in=settings.oauth_token_ttl_seconds,
            scope=" ".join(use_scopes),
            refresh_token=new_refresh_str,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke a token."""
        if isinstance(token, AccessToken):
            _access_tokens.pop(token.token, None)
        elif isinstance(token, RefreshToken):
            _refresh_tokens.pop(token.token, None)


def hash_password(plain: str) -> str:
    """Hash password for storage. Use bcrypt in production."""
    return hashlib.sha256(plain.encode()).hexdigest()
