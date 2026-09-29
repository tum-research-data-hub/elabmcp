"""Validate every HTTP request the tools make against the official eLabFTW OpenAPI spec.

Reads the requests recorded by ``test_tools_offline.py`` (→ ../offline_requests.json)
and checks: the path/method exists, required query parameters are present, and no body
field is used that the spec does not declare (reported as a deviation — eLabFTW itself
accepts some undeclared fields, see DEVIATIONS below).

Spec lookup order: $ELABFTW_OPENAPI, ../reference/openapi-v2-6.0.2.yaml, GitHub raw.

    python tests/test_api_contract.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC_CANDIDATES = [
    os.environ.get("ELABFTW_OPENAPI", ""),
    str(ROOT / "reference" / "openapi-v2-6.0.2.yaml"),
]
SPEC_URL = "https://raw.githubusercontent.com/elabftw/elabftw/6.0.2/apidoc/v2/openapi.yaml"

# Parameters the running API accepts although the spec does not declare them
# (verified against a live instance; see README "documented differences").
KNOWN_DEVIATIONS = {
    ("PATCH", "/{entity_type}/{id}/steps/{subid}"): {"deadline"},
    ("POST", "/{entity_type}/{id}/steps"): {"deadline", "deadline_notif"},
}


def load_spec() -> dict | None:
    import yaml

    for candidate in SPEC_CANDIDATES:
        if candidate and pathlib.Path(candidate).exists():
            return yaml.safe_load(pathlib.Path(candidate).read_text(encoding="utf-8"))
    try:
        with urllib.request.urlopen(SPEC_URL, timeout=30) as response:
            return yaml.safe_load(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"no OpenAPI spec available ({exc}) — set ELABFTW_OPENAPI to the spec file")
        return None


def resolve(node, spec):
    while isinstance(node, dict) and "$ref" in node:
        cur = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        node = cur
    return node


def path_patterns(spec: dict) -> dict[str, dict]:
    return {path: item for path, item in spec["paths"].items() if isinstance(item, dict)}


def match_path(recorded: str, patterns: dict[str, dict]) -> str | None:
    """Match a concrete request path against the templated spec paths."""
    if recorded in patterns:
        return recorded
    for pattern in patterns:
        if "{" not in pattern:
            continue
        regex = "^" + re.sub(r"\{[^}]+\}", r"[^/?]+", pattern) + "$"
        if re.match(regex, recorded):
            return pattern
    return None


def main() -> int:
    requests_file = ROOT / "offline_requests.json"
    if not requests_file.exists():
        print("run tests/test_tools_offline.py first (it records the requests)")
        return 2
    recorded = json.loads(requests_file.read_text(encoding="utf-8"))
    spec = load_spec()
    if spec is None:
        return 2
    patterns = path_patterns(spec)
    print(f"spec: {spec.get('info', {}).get('version')} | {len(patterns)} paths | "
          f"{len(recorded)} recorded requests\n")

    unknown_path, unknown_method, missing_param, deviations = [], [], [], []
    checked = 0
    for entry in recorded:
        method, path = entry["method"], entry["path"]
        pattern = match_path(path, patterns)
        if pattern is None:
            unknown_path.append(f"{method} {path}")
            continue
        operations = patterns[pattern]
        operation = operations.get(method.lower())
        if operation is None:
            unknown_method.append(f"{method} {path} (only {sorted(k for k in operations if k.islower())})")
            continue
        checked += 1

        params = resolve(operation.get("parameters", []), spec) if operation.get("parameters") else []
        declared = {p.get("name"): p.get("required") for p in params
                    if isinstance(p, dict) and p.get("in") == "query"}
        # parameters can also come from the path-level definition
        for p in resolve(operations.get("parameters", []) or [], spec):
            if isinstance(p, dict) and p.get("in") == "query":
                declared.setdefault(p.get("name"), p.get("required"))
        for name, required in declared.items():
            if required and not name.endswith("[]") and name not in entry["params"]:
                missing_param.append(f"{method} {path}: required '{name}' missing")

        body_keys = entry.get("body_keys")
        if body_keys:
            schema = {}
            request_body = resolve(operation.get("requestBody"), spec) if operation.get("requestBody") else None
            if request_body:
                for media, media_spec in (request_body.get("content") or {}).items():
                    candidate = resolve(media_spec.get("schema"), spec)
                    properties = candidate.get("properties")
                    if not properties and candidate.get("allOf"):
                        properties = {}
                        for part in candidate["allOf"]:
                            properties.update((resolve(part, spec).get("properties") or {}))
                    if properties:
                        schema = properties
                        break
            if schema:
                allowed = set(schema) | KNOWN_DEVIATIONS.get((method, pattern), set())
                extra = sorted(set(body_keys) - allowed)
                if extra:
                    deviations.append(f"{method} {pattern}: body fields not in spec: {extra}")

    print(f"{checked} requests matched a spec path+method")
    for label, items in (("path not in spec", unknown_path),
                         ("method not allowed by spec", unknown_method),
                         ("required parameter missing", missing_param),
                         ("body field not declared in spec", deviations)):
        if items:
            print(f"\n{label} ({len(items)}):")
            for item in sorted(set(items)):
                print("  -", item)

    ok = not (unknown_path or unknown_method or missing_param or deviations)
    print("\nPASS — every request uses a documented path, method, parameters and fields" if ok
          else "\nFAIL — see the lists above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
