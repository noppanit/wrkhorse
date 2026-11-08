"""Verify Cloudflare Access assertions at the origin (optional OAuth deployment).

Cloudflare Managed OAuth gives the external MCP client an opaque bearer.
Cloudflare Access validates it, then forwards an *independent signed JWT* in
Cf-Access-Jwt-Assertion. We validate its RS256 signature against the fixed
team-domain JWKS, its issuer, audience and expiry before any MCP tool call.

The JWKS URL is built only from a validated Cloudflare team domain (config).
This verifier never trusts the header merely because a request came through
a tunnel. Network/key errors fail closed; secrets and JWTs are never logged.
"""
from __future__ import annotations

import jwt


class AccessVerifier:
    def __init__(self, team_domain: str, audience: str) -> None:
        self.issuer = "https://" + team_domain
        self.audience = audience
        self.client = jwt.PyJWKClient(
            self.issuer + "/cdn-cgi/access/certs",
            cache_jwk_set=True,
            lifespan=300,
            timeout=5,
        )

    def verify(self, assertion: str) -> bool:
        if not assertion or len(assertion) > 8192:
            return False
        try:
            key = self.client.get_signing_key_from_jwt(assertion)
            jwt.decode(
                assertion,
                key.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["iss", "aud", "exp", "iat"]},
                leeway=30,
            )
            return True
        except (jwt.PyJWTError, OSError, ValueError, TimeoutError):
            return False
