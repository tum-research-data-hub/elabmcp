"""AI tools with a real LLM round-trip — against a local OpenAI-compatible stub.

Proves the code path that is inactive without a key: prompt building, JSON parsing,
Bearer auth, the provenance header on AI comments, and tag writes.
No external API is called, so no quota is spent.

    python tests/test_ai_tools.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    RESULTS.append((bool(ok), label))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + extra) if extra else ''}", flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def spawn(module: str, port: int) -> subprocess.Popen:
    log = pathlib.Path(tempfile.mkdtemp(prefix=f"elabmcp-{module.split('.')[-1]}-")) / "log"
    return subprocess.Popen([sys.executable, "-u", "-m", module, "--port", str(port)],
                            cwd=str(ROOT), stdout=log.open("w", encoding="utf-8"),
                            stderr=subprocess.STDOUT)


def wait_for(url: str, attempts: int = 60) -> bool:
    for _ in range(attempts):
        try:
            if httpx.get(url, timeout=2.0).status_code < 500:
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


async def run(elab_url: str, llm_url: str, api_key: str = "stub-key", prefix: str = "") -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.path.insert(0, str(ROOT / "src"))
    from elabftw_mcp.config import Config, set_config
    from elabftw_mcp.credentials import set_credentials

    config = Config.load()
    config.elabftw.base_url = elab_url
    config.elabftw.api_key = api_key
    config.ai.base_url = f"{llm_url}/v1"
    config.ai.api_key = "llm-stub-key"
    config.ai.model = "stub-model"
    set_config(config)
    set_credentials(elab_url, api_key, "f", None)

    from elabftw_mcp.tools import ai_tools, write

    experiment = json.loads(await write.create_experiment(
        title=f"{prefix}TGA measurement AI test",
        body="Sample 4.2 mg, pan: alumina, method TRIOS-A"))
    eid = experiment["id"]

    print("\n-- review_experiment (real LLM round-trip) --")
    review = json.loads(await ai_tools.review_experiment(id=eid))
    check(review.get("status") == "ok", "status ok", review.get("status"))
    check(bool(re.match(r"^trace-[0-9a-f]{16}$", str(review.get("trace_id")))),
          "trace id has the upstream shape", str(review.get("trace_id")))
    body = review.get("review") if isinstance(review.get("review"), dict) else {}
    check(body.get("completeness_score") == 62 and body.get("risks"),
          "model JSON parsed into the review payload", f"score {body.get('completeness_score')}")

    print("\n-- suggest_tags / suggest_metadata --")
    tags = json.loads(await ai_tools.suggest_tags(entity_type="experiments", id=eid))
    check(tags.get("status") == "ok" and "tga" in (tags.get("suggestions") or []),
          f"suggestions parsed ({len(tags.get('suggestions') or [])})", str(tags.get("suggestions")))
    fields = json.loads(await ai_tools.suggest_metadata(entity_type="experiments", id=eid))
    keys = [f.get("key") for f in (fields.get("suggestions") or [])]
    check(fields.get("status") == "ok" and "Instrument" in keys, f"field keys {keys}")

    print("\n-- what the tools sent to the model --")
    sent = httpx.get(f"{llm_url}/requests", timeout=10).json()
    check(len(sent) == 3, f"3 completions recorded (got {len(sent)})")
    check(all(s["authorization"] == "Bearer llm-stub-key" for s in sent), "Bearer auth on every call")
    check(all(s["model"] == "stub-model" for s in sent), "configured model is used")
    check(any("TGA measurement AI test" in s["prompt"] and "Sample 4.2 mg" in s["prompt"] for s in sent),
          "entity title and body are in the prompt")
    check(sum(1 for s in sent if s["json_mode"]) == 2, "json response_format where expected")

    print("\n-- AI writes keep their provenance --")
    applied = json.loads(await ai_tools.apply_tag_suggestions(
        entity_type="experiments", id=eid, tags=tags.get("suggestions") or []))
    stored_tags = [str(t).casefold() for t in (applied.get("tags_applied") or [])]
    check(applied.get("status") == "ok" and {"tga", "calibration"} <= set(stored_tags),
          f"tags written through the API ({applied.get('tags_now')})")
    comment = json.loads(await ai_tools.add_ai_review_comment(
        entity_type="experiments", id=eid, comment="Review: add the TRIOS method id.",
        trace_id=review.get("trace_id")))
    check(comment.get("status") in ("created", "ok"), f"comment status {comment.get('status')}")
    response = httpx.get(f"{elab_url}/api/v2/experiments/{eid}/comments",
                         headers={"Authorization": api_key}, timeout=10)
    stored = response.json()
    if not isinstance(stored, list):
        print(f"      raw comments response: HTTP {response.status_code} {response.text[:200]}")
        stored = []
    text = stored[0]["comment"] if stored else ""
    check(text.startswith("[AI-GENERATED] | ") and f"trace: {review.get('trace_id')}" in text,
          "comment carries the provenance header", text.split("\n")[0][:70])
    check("Review: add the TRIOS method id." in text, "comment text preserved after the header")

    print("\n-- without a key the documented fallback stays in place --")
    config.ai.api_key = ""
    set_config(config)
    offline = json.loads(await ai_tools.review_experiment(id=eid))
    check(offline.get("status") == "placeholder" and "BOOTSTRAP PLACEHOLDER" in str(offline.get("review")),
          "placeholder wording without an endpoint")

    if prefix:  # live run: remove the test entity again
        from elabftw_mcp.tools._common import client as _api
        try:
            await _api().delete(f"/experiments/{eid}")
            # eLabFTW deletes softly: the record stays with state=3 and disappears from listings.
            probe = httpx.get(f"{elab_url}/api/v2/experiments/{eid}",
                              headers={"Authorization": api_key}, timeout=10)
            state = probe.json().get("state") if probe.status_code == 200 else None
            listed = httpx.get(f"{elab_url}/api/v2/experiments",
                               params={"q": f"{prefix}", "limit": 5},
                               headers={"Authorization": api_key}, timeout=10).json()
            gone_from_listings = not any(r.get("id") == eid for r in listed if isinstance(r, dict))
            check(probe.status_code == 404 or state == 3, f"cleanup: experiment {eid} removed (state={state})")
            check(gone_from_listings, "cleanup: no longer listed")
        except Exception as exc:  # noqa: BLE001
            check(False, "cleanup", f"{type(exc).__name__}: {exc}")


def main() -> int:
    live = "--live" in sys.argv
    import os

    key_file = pathlib.Path(__file__).resolve().parents[2] / ".elab_key"
    if live and not key_file.exists():
        print(f"missing {key_file}")
        return 2
    elab_port, llm_port = free_port(), free_port()
    llm_url = f"http://127.0.0.1:{llm_port}"
    elab_url = os.environ.get("ELABFTW_TEST_BASE_URL", "https://elntest.ub.tum.de") if live         else f"http://127.0.0.1:{elab_port}"
    api_key = key_file.read_text(encoding="utf-8").strip() if live else "stub-key"
    prefix = "elabmcp-ai-test-" if live else ""
    print(f"backend: {elab_url}{' (live)' if live else ' (stub)'} | model: local stub")
    elab = None if live else spawn("tests.fake_elabftw", elab_port)
    llm = spawn("tests.fake_llm", llm_port)
    try:
        if not wait_for(f"{llm_url}/requests") or (elab is not None and not wait_for(f"{elab_url}/api/v2/info")):
            print("stubs did not start")
            return 2
        asyncio.run(run(elab_url, llm_url, api_key, prefix))
    finally:
        for proc in [p for p in (elab, llm) if p is not None]:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} AI checks passed ===")
    for label in failed:
        print("  FAILED:", label)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
