from __future__ import annotations

import importlib.resources
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml


DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass
class Endpoint:
    """
    Representation of a single REST endpoint derived from the overview tree.
    """

    name: str
    category: str
    method: str
    path: str
    description: str
    auth: str
    request_schema: Dict[str, Any]
    response_schema: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "method": self.method,
            "path": self.path,
            "description": self.description,
            "auth": self.auth,
            "request_schema": self.request_schema,
            "response_schema": self.response_schema,
        }


def parse_overview_html(html: str) -> List[Endpoint]:
    """
    Extract endpoint data from the offline overview-tree HTML.
    """
    script_pattern = re.compile(
        r'<script[^>]*id=["\\\']baritone-endpoints["\\\'][^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    match = script_pattern.search(html)
    if not match:
        raise ValueError("Unable to find embedded endpoint JSON in overview-tree HTML")
    raw_json = match.group(1).strip()
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Embedded endpoint JSON is malformed") from exc
    return [Endpoint(**_normalize_endpoint(entry)) for entry in parsed]


def _normalize_endpoint(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize field names from the embedded overview tree into the Endpoint dataclass.
    """
    return {
        "name": entry["name"],
        "category": entry["category"],
        "method": entry["method"],
        "path": entry["path"],
        "description": entry.get("description", ""),
        "auth": entry.get("auth", "token"),
        "request_schema": entry.get("request", entry.get("request_schema", {})),
        "response_schema": entry.get("response", entry.get("response_schema", {})),
    }


def load_endpoints(
    html_path: Optional[Path] = None, json_path: Optional[Path] = None
) -> List[Endpoint]:
    """
    Load endpoints from the offline HTML and optional JSON snapshot.
    """
    resolved_html = html_path or _resource_path("overview-tree.html")
    html_text = Path(resolved_html).read_text(encoding="utf-8")
    endpoints_from_html = parse_overview_html(html_text)

    resolved_json = json_path or _resource_path("endpoints.json")
    if Path(resolved_json).exists():
        parsed = json.loads(Path(resolved_json).read_text(encoding="utf-8"))
        endpoints_from_json = [
            Endpoint(**_normalize_endpoint(item)) for item in parsed
        ]
        return _merge_endpoints(endpoints_from_html, endpoints_from_json)

    return endpoints_from_html


def _merge_endpoints(
    primary: Iterable[Endpoint], secondary: Iterable[Endpoint]
) -> List[Endpoint]:
    merged: Dict[str, Endpoint] = {ep.name: ep for ep in primary}
    for ep in secondary:
        merged.setdefault(ep.name, ep)
    return list(merged.values())


def export_openapi_yaml(endpoints: Iterable[Endpoint]) -> str:
    """
    Generate a minimal OpenAPI 3.0 document as a YAML string for code generation.
    """
    paths: Dict[str, Dict[str, Any]] = {}
    for ep in endpoints:
        path_entry = paths.setdefault(ep.path, {})
        path_entry[ep.method.lower()] = {
            "summary": ep.description,
            "operationId": ep.name.replace("-", "_"),
            "security": [] if ep.auth == "none" else [{"bearerAuth": []}],
            "requestBody": {
                "required": bool(ep.request_schema),
                "content": {
                    "application/json": {"schema": _schema_from_value(ep.request_schema)}
                },
            }
            if ep.method in {"POST", "PUT", "PATCH"} and ep.request_schema
            else None,
            "responses": {
                "200": {
                    "description": "Successful response",
                    "content": {
                        "application/json": {
                            "schema": _schema_from_value(ep.response_schema)
                        }
                    },
                }
            },
        }

    doc = {
        "openapi": "3.0.3",
        "info": {
            "title": "Baritone Control API",
            "version": "0.1.0",
            "description": "Offline snapshot of the Baritone HTTP control surface.",
        },
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            }
        },
    }
    return yaml.safe_dump(doc, sort_keys=False)


def _schema_from_value(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {k: _schema_from_value(v) for k, v in value.items()},
        }
    if isinstance(value, list):
        if not value:
            return {"type": "array"}
        return {"type": "array", "items": _schema_from_value(value[0])}
    if value in ("string", "integer", "number", "boolean"):
        return {"type": value}
    if value == "any":
        return {}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    return {"type": "string"}


def _resource_path(name: str) -> Path:
    """
    Resolve a data file either from the package resources or the local filesystem.
    """
    try:
        data_package = f"{__package__}.data" if __package__ else "baritone_client.data"
        package_files = importlib.resources.files(data_package)
        candidate = package_files / name
        if candidate.exists():
            return Path(candidate)
    except (FileNotFoundError, ModuleNotFoundError):
        pass
    return DATA_DIR / name
