"""Who is making this request?

Cloudflare Access logs people in (one-time code to their email) and then adds a signed token to
every request it forwards. We check that signature, so a request that did not come through
Cloudflare Access - or that carries a forged email - is never trusted.

AUTH_MODE=cloudflare   production: needs CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD
AUTH_MODE=dev          trying it out on your own machine: everyone is DEV_USER_EMAIL. Never expose this.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

import jwt

logger = logging.getLogger("thermal_report.auth")


class CloudflareAccess:
    def __init__(self, team_domain: str, aud: str, key_fetcher: Optional[Callable[[str], object]] = None):
        team_domain = team_domain.strip().replace("https://", "").strip("/")
        if not team_domain or not aud.strip():
            raise ValueError("CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD are both required")
        self.issuer, self.aud = f"https://{team_domain}", aud.strip()
        if key_fetcher is None:
            client = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", cache_keys=True, timeout=10)
            key_fetcher = lambda token: client.get_signing_key_from_jwt(token).key
        self._key_for = key_fetcher

    def __call__(self, request) -> Optional[str]:
        token = request.headers.get("Cf-Access-Jwt-Assertion") or request.cookies.get("CF_Authorization")
        if not token:
            return None
        try:
            claims = jwt.decode(token, self._key_for(token), algorithms=["RS256"], audience=self.aud,
                                issuer=self.issuer, options={"require": ["exp", "iat", "aud", "iss"]})
        except Exception as exc:                       # bad signature, expired, wrong audience, key fetch failed...
            logger.warning("Rejected access token: %s", exc)
            return None
        email = claims.get("email")
        return str(email).strip().lower() if email else None


class DevIdentity:
    def __init__(self, email: str):
        if not email:
            raise ValueError("DEV_USER_EMAIL is required when AUTH_MODE=dev")
        self.email = email.strip().lower()
        logger.warning("AUTH_MODE=dev: everyone is treated as %s. Do not expose this to the internet.", self.email)

    def __call__(self, request) -> Optional[str]:
        return self.email


def build_identity(mode: str, team_domain: str = "", aud: str = "", dev_email: str = "") -> Callable:
    mode = (mode or "").strip().lower()
    if mode == "cloudflare":
        return CloudflareAccess(team_domain, aud)
    if mode == "dev":
        return DevIdentity(dev_email)
    raise ValueError("AUTH_MODE must be 'cloudflare' (or 'dev' for local trials) when ORGS_FILE is set")
