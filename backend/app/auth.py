"""Shared bearer tokens. This is not nurse login."""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .settings import Settings

_device = HTTPBearer(scheme_name="DeviceToken", auto_error=False)
_dashboard = HTTPBearer(scheme_name="DashboardToken", auto_error=False)
_either = HTTPBearer(scheme_name="DeviceOrDashboardToken", auto_error=False)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Missing or invalid access token")


def _forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail="This token cannot perform that action")


def _presented(creds: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        return None
    return creds.credentials


def _guard(scheme: HTTPBearer, allowed: frozenset):
    """One check for every route. `allowed` is which of the two tokens may pass."""

    def require_token(
        request: Request,
        creds: Optional[HTTPAuthorizationCredentials] = Depends(scheme),
    ) -> None:
        settings = _settings(request)
        token = _presented(creds)
        if token is None:
            raise _unauthorized()
        matched = set()
        if secrets.compare_digest(token, settings.device_api_token):
            matched.add("device")
        if secrets.compare_digest(token, settings.dashboard_api_token):
            matched.add("dashboard")
        if matched & allowed:
            return
        if matched:
            raise _forbidden()
        raise _unauthorized()

    return require_token


require_device = _guard(_device, frozenset({"device"}))
require_dashboard = _guard(_dashboard, frozenset({"dashboard"}))
require_either = _guard(_either, frozenset({"device", "dashboard"}))
