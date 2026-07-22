from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import requests


class BaritoneError(RuntimeError):
    """
    Raised when an API request fails or returns an unexpected payload.
    """

    def __init__(self, message: str, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass
class RetryConfig:
    attempts: int = 3
    backoff_seconds: float = 0.5
    retry_statuses: Iterable[int] = (429, 502, 503, 504)


class BaritoneClient:
    """
    Thin wrapper around :class:`requests.Session` with retry and auth support.
    """

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        *,
        timeout: float = 10.0,
        session: Optional[requests.Session] = None,
        retry: Optional[RetryConfig] = None,
        dry_run: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.session = session or requests.Session()
        self.retry = retry or RetryConfig()
        self.dry_run = dry_run

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {"User-Agent": "baritone-client/0.1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        expected: Iterable[int] | int = (200, 201, 202, 204),
    ) -> Any:
        url = f"{self.base_url}{path}"
        expected_codes = {expected} if isinstance(expected, int) else set(expected)
        for attempt in range(1, self.retry.attempts + 1):
            if self.dry_run:
                return {
                    "dry_run": True,
                    "method": method,
                    "url": url,
                    "json": json_body,
                    "params": params,
                }
            try:
                request_kwargs = {
                    "method": method,
                    "url": url,
                    "timeout": self.timeout,
                    "headers": self._headers(),
                    "params": params,
                }
                if json_body is not None:
                    request_kwargs["json"] = json_body
                response = self.session.request(
                    **request_kwargs,
                )
            except requests.RequestException as exc:
                if attempt >= self.retry.attempts:
                    raise BaritoneError(f"Request failed after {attempt} attempts: {exc}") from exc
                time.sleep(self.retry.backoff_seconds * attempt)
                continue

            if response.status_code in self.retry.retry_statuses and attempt < self.retry.attempts:
                time.sleep(self.retry.backoff_seconds * attempt)
                continue

            if response.status_code not in expected_codes:
                raise BaritoneError(
                    f"Unexpected status {response.status_code} for {method} {path}",
                    status_code=response.status_code,
                    payload=_safe_json(response),
                )

            return _safe_json(response)

        raise BaritoneError("Request retries exhausted")

    # Auth helpers
    def login(self, username: str, password: str) -> Dict[str, Any]:
        payload = {"username": username, "password": password}
        data = self._request("POST", "/api/login", json_body=payload)
        if isinstance(data, dict) and "token" in data:
            self.token = data["token"]
        return data

    def logout(self) -> Dict[str, Any]:
        return self._request("POST", "/api/logout")

    def session_info(self) -> Dict[str, Any]:
        return self._request("GET", "/api/session")

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/api/health", expected=(200, 204))

    # Status
    def status(self) -> Dict[str, Any]:
        return self._request("GET", "/api/status")

    def queue(self) -> Dict[str, Any]:
        return self._request("GET", "/api/queue")

    def operations_history(self) -> Dict[str, Any]:
        return self._request("GET", "/api/operations/history")

    def events(self, since_id: Optional[str] = None) -> Dict[str, Any]:
        params = {"since_id": since_id} if since_id else None
        return self._request("GET", "/api/events", params=params)

    # Pathing controls
    def set_goal(self, goal: Dict[str, Any], follow_best: bool = False) -> Dict[str, Any]:
        payload = {"goal": goal, "follow_best": follow_best}
        return self._request("POST", "/api/path/goal", json_body=payload)

    def goto(
        self,
        x: float,
        y: float,
        z: float,
        *,
        dimension: str = "overworld",
        allow_break: bool = True,
        allow_place: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "x": float(x),
            "y": float(y),
            "z": float(z),
            "dimension": dimension,
            "allow_break": allow_break,
            "allow_place": allow_place,
        }
        return self._request("POST", "/api/path/goto", json_body=payload)

    def pause(self, reason: Optional[str] = None) -> Dict[str, Any]:
        payload = {"reason": reason} if reason else None
        return self._request("POST", "/api/path/pause", json_body=payload)

    def resume(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        payload = {"task_id": task_id} if task_id else None
        return self._request("POST", "/api/path/resume", json_body=payload)

    def stop(self, reason: Optional[str] = None) -> Dict[str, Any]:
        payload = {"reason": reason} if reason else None
        return self._request("POST", "/api/path/stop", json_body=payload)

    # Commands
    def mine(self, blocks: Iterable[str], target_count: int | None = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"blocks": list(blocks)}
        if target_count is not None:
            payload["target_count"] = target_count
        return self._request("POST", "/api/command/mine", json_body=payload)

    def farm(self, crops: Iterable[str], radius: float = 8.0) -> Dict[str, Any]:
        payload = {"crops": list(crops), "radius": radius}
        return self._request("POST", "/api/command/farm", json_body=payload)

    def build(self, schematic: str, origin: Dict[str, float]) -> Dict[str, Any]:
        payload = {"schematic": schematic, "origin": origin}
        return self._request("POST", "/api/command/build", json_body=payload)

    def send_command(self, command: str) -> Dict[str, Any]:
        payload = {"command": command}
        return self._request("POST", "/api/command/custom", json_body=payload)

    # Settings
    def list_settings(self) -> Dict[str, Any]:
        return self._request("GET", "/api/settings")

    def get_setting(self, name: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/settings/{name}")

    def set_setting(self, name: str, value: Any) -> Dict[str, Any]:
        payload = {"value": value}
        return self._request("PUT", f"/api/settings/{name}", json_body=payload)


def _safe_json(response: requests.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except json.JSONDecodeError:
        return response.text
