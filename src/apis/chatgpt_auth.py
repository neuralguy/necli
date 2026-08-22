"""ChatGPT OAuth login and token persistence."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import stat
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from config import paths
from config._atomic import atomic_write_json

OAUTH_ISSUER = "https://auth.openai.com"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_CALLBACK_PORT = 1455
OAUTH_SCOPES = (
    "openid profile email offline_access api.connectors.read api.connectors.invoke"
)
CHATGPT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"

_AUTH_LOCK = threading.RLock()
_REFRESH_LOCK = asyncio.Lock()
_CALLBACK_LOCK = threading.RLock()
_active_callback: (
    tuple[ThreadingHTTPServer, threading.Thread, dict[str, str]] | None
) = None


class ChatGPTAuthError(RuntimeError):
    pass


class _OAuthCallbackServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _stop_callback(
    server: ThreadingHTTPServer,
    server_thread: threading.Thread,
    result: dict[str, str],
    *,
    replaced: bool = False,
) -> None:
    with _CALLBACK_LOCK:
        if getattr(server, "_necli_stopped", False):
            return
        server._necli_stopped = True
        if replaced and "code" not in result and "error" not in result:
            result["error"] = "ChatGPT sign-in restarted"
    server.shutdown()
    server.server_close()
    server_thread.join(timeout=1.0)


def _stop_active_callback() -> None:
    global _active_callback
    with _CALLBACK_LOCK:
        active = _active_callback
        _active_callback = None
    if active is not None:
        _stop_callback(*active, replaced=True)


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _account_id(*tokens: str) -> str:
    for token in tokens:
        claims = _decode_jwt_claims(token)
        direct = claims.get("chatgpt_account_id")
        if isinstance(direct, str) and direct:
            return direct
        auth = claims.get("https://api.openai.com/auth")
        if isinstance(auth, dict):
            value = auth.get("chatgpt_account_id")
            if isinstance(value, str) and value:
                return value
        organizations = claims.get("organizations")
        if isinstance(organizations, list) and organizations:
            first = organizations[0]
            if isinstance(first, dict) and isinstance(first.get("id"), str):
                return first["id"]
    return ""


def load_chatgpt_auth() -> dict[str, Any] | None:
    with _AUTH_LOCK:
        path = paths.CHATGPT_AUTH_FILE
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ChatGPTAuthError(f"Cannot read ChatGPT credentials: {exc}") from exc
        if not isinstance(data, dict):
            raise ChatGPTAuthError("ChatGPT credentials file is invalid")
        return data


def save_chatgpt_auth(data: dict[str, Any]) -> None:
    with _AUTH_LOCK:
        atomic_write_json(paths.CHATGPT_AUTH_FILE, data)
        try:
            os.chmod(paths.CHATGPT_AUTH_FILE, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def clear_chatgpt_auth() -> bool:
    with _AUTH_LOCK:
        path = paths.CHATGPT_AUTH_FILE
        if not path.exists():
            return False
        path.unlink()
        return True


def chatgpt_auth_status() -> dict[str, Any]:
    try:
        auth = load_chatgpt_auth()
    except ChatGPTAuthError as exc:
        return {"authenticated": False, "error": str(exc)}
    if not auth:
        return {"authenticated": False}
    return {
        "authenticated": bool(auth.get("access_token") and auth.get("refresh_token")),
        "account_id": str(auth.get("account_id") or ""),
        "email": str(auth.get("email") or ""),
        "expires_at": float(auth.get("expires_at") or 0),
    }


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _authorize_url(redirect_uri: str, challenge: str, state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": OAUTH_SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state": state,
            "originator": "necli",
        }
    )
    return f"{OAUTH_ISSUER}/oauth/authorize?{query}"


def _callback_server(expected_state: str) -> tuple[ThreadingHTTPServer, dict[str, str]]:
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_error(404)
                return
            params = parse_qs(parsed.query)
            state = (params.get("state") or [""])[0]
            if not secrets.compare_digest(state, expected_state):
                result["error"] = "OAuth state mismatch"
                self._respond(
                    400, "ChatGPT sign-in failed. Return to necli and try again."
                )
                return
            error = (params.get("error_description") or params.get("error") or [""])[0]
            code = (params.get("code") or [""])[0]
            if error:
                result["error"] = error
                self._respond(
                    400, "ChatGPT sign-in was not completed. You may close this tab."
                )
                return
            if not code:
                result["error"] = "Authorization callback did not contain a code"
                self._respond(
                    400, "ChatGPT sign-in failed. Return to necli and try again."
                )
                return
            result["code"] = code
            self._respond(
                200,
                "ChatGPT sign-in complete. You can close this tab and return to necli.",
            )

        def _respond(self, status: int, message: str) -> None:
            body = (
                "<!doctype html><meta charset='utf-8'><title>necli</title>"
                f"<body style='font-family:system-ui;padding:3rem'><h1>necli</h1><p>{message}</p></body>"
            ).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    try:
        server = _OAuthCallbackServer(("127.0.0.1", OAUTH_CALLBACK_PORT), Handler)
    except OSError as exc:
        raise ChatGPTAuthError(
            f"Cannot start OAuth callback on localhost:{OAUTH_CALLBACK_PORT}: {exc}"
        ) from exc
    return server, result


async def _token_request(data: dict[str, str]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{OAUTH_ISSUER}/oauth/token", data=data)
    except httpx.HTTPError as exc:
        raise ChatGPTAuthError(f"ChatGPT authentication request failed: {exc}") from exc
    if response.status_code != 200:
        try:
            payload = response.json()
            detail = (
                payload.get("error_description")
                or payload.get("error")
                or response.text
            )
        except (ValueError, AttributeError):
            detail = response.text
        raise ChatGPTAuthError(
            f"ChatGPT authentication failed ({response.status_code}): {detail}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ChatGPTAuthError("ChatGPT authentication returned invalid JSON") from exc
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ChatGPTAuthError("ChatGPT authentication did not return an access token")
    return payload


def _stored_tokens(
    payload: dict[str, Any], previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    previous = previous or {}
    access_token = str(
        payload.get("access_token") or previous.get("access_token") or ""
    )
    id_token = str(payload.get("id_token") or previous.get("id_token") or "")
    refresh_token = str(
        payload.get("refresh_token") or previous.get("refresh_token") or ""
    )
    expires_in = float(payload.get("expires_in") or 3600)
    id_claims = _decode_jwt_claims(id_token)
    access_claims = _decode_jwt_claims(access_token)
    exp = access_claims.get("exp")
    expires_at = (
        float(exp) if isinstance(exp, int | float) else time.time() + expires_in
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "account_id": _account_id(id_token, access_token)
        or str(previous.get("account_id") or ""),
        "email": str(id_claims.get("email") or previous.get("email") or ""),
        "expires_at": expires_at,
    }


async def login_chatgpt(*, open_browser: bool = True, timeout: float = 300.0) -> str:
    global _active_callback

    _stop_active_callback()
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    server, result = _callback_server(state)
    redirect_uri = f"http://localhost:{OAUTH_CALLBACK_PORT}/auth/callback"
    auth_url = _authorize_url(redirect_uri, challenge, state)
    server_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.1},
        name="necli-chatgpt-oauth",
        daemon=True,
    )
    server_thread.start()
    with _CALLBACK_LOCK:
        _active_callback = (server, server_thread, result)
    try:
        if open_browser and not webbrowser.open(auth_url):
            raise ChatGPTAuthError(
                f"Could not open a browser. Open this URL manually: {auth_url}"
            )

        deadline = time.monotonic() + timeout
        while "code" not in result and "error" not in result:
            if time.monotonic() >= deadline:
                raise ChatGPTAuthError("ChatGPT sign-in timed out")
            await asyncio.sleep(0.05)
    finally:
        _stop_callback(server, server_thread, result)
        with _CALLBACK_LOCK:
            if _active_callback is not None and _active_callback[0] is server:
                _active_callback = None
    if result.get("error"):
        raise ChatGPTAuthError(result["error"])

    payload = await _token_request(
        {
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": redirect_uri,
            "client_id": OAUTH_CLIENT_ID,
            "code_verifier": verifier,
        }
    )
    auth = _stored_tokens(payload)
    if not auth["refresh_token"]:
        raise ChatGPTAuthError("ChatGPT authentication did not return a refresh token")
    save_chatgpt_auth(auth)
    return auth_url


async def get_chatgpt_access(*, force_refresh: bool = False) -> tuple[str, str]:
    auth = load_chatgpt_auth()
    if not auth or not auth.get("access_token"):
        raise ChatGPTAuthError(
            "ChatGPT is not connected. Open /api and sign in with ChatGPT."
        )
    if force_refresh or float(auth.get("expires_at") or 0) <= time.time() + 300:
        previous_access_token = str(auth.get("access_token") or "")
        async with _REFRESH_LOCK:
            current = load_chatgpt_auth()
            if not current:
                raise ChatGPTAuthError(
                    "ChatGPT is not connected. Open /api and sign in again."
                )
            token_is_stale = (
                str(current.get("access_token") or "") == previous_access_token
            )
            needs_refresh = (force_refresh and token_is_stale) or float(
                current.get("expires_at") or 0
            ) <= time.time() + 300
            if needs_refresh:
                refresh_token = str(current.get("refresh_token") or "")
                if not refresh_token:
                    raise ChatGPTAuthError(
                        "ChatGPT session expired. Sign in again from /api."
                    )
                payload = await _token_request(
                    {
                        "client_id": OAUTH_CLIENT_ID,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    }
                )
                auth = _stored_tokens(payload, current)
                save_chatgpt_auth(auth)
            else:
                auth = current
    account_id = str(auth.get("account_id") or _account_id(str(auth["access_token"])))
    return str(auth["access_token"]), account_id


__all__ = [
    "CHATGPT_RESPONSES_URL",
    "ChatGPTAuthError",
    "chatgpt_auth_status",
    "clear_chatgpt_auth",
    "get_chatgpt_access",
    "load_chatgpt_auth",
    "login_chatgpt",
    "save_chatgpt_auth",
]
