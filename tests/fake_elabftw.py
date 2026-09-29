"""Minimal in-memory eLabFTW v2 stand-in for the offline test suites.

Only what the tools touch, with the API's observable behaviour: 201 + Location on
create, metadata stored as a JSON string, link rows exposing ``entityid``/``type``,
``?related=`` lookups, scoped link subresources.

Run standalone:  python tests/fake_elabftw.py --port 8099
"""
from __future__ import annotations

import argparse
import itertools
import json
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

ENTITY_TYPES = ("experiments", "items", "experiments_templates", "items_types")
LINK_SEGMENTS = {"experiments_links": "experiments", "items_links": "items"}
STATE: dict[str, dict[int, dict[str, Any]]] = {t: {} for t in ENTITY_TYPES}
STEPS: dict[tuple[str, int], dict[int, dict[str, Any]]] = {}
TAGS: dict[tuple[str, int], list[str]] = {}
COMMENTS: dict[tuple[str, int], dict[int, str]] = {}
UPLOADS: dict[tuple[str, int], dict[int, dict[str, Any]]] = {}
LINKS: dict[tuple[str, int], set[tuple[str, int]]] = {}
IDS = itertools.count(1000)
CATEGORIES = {"experiments_categories": [{"id": 1, "title": "General"}, {"id": 2, "title": "Chemistry"}],
              "experiments_status": [{"id": 1, "title": "Running"}, {"id": 2, "title": "Done"}],
              "resources_categories": [{"id": 1, "title": "Instrument"}],
              "items_status": [{"id": 1, "title": "Available"}],
              "tags": [{"tag": "seed"}]}


def next_id() -> int:
    return next(IDS)


def entity_payload(etype: str, eid: int) -> dict[str, Any]:
    entity = STATE[etype][eid]
    payload = dict(entity)
    payload.update({
        "id": eid,
        "metadata": json.dumps(entity.get("metadata") or {}),
        "tags": TAGS.get((etype, eid), []),
        "steps": list(STEPS.get((etype, eid), {}).values()),
        "comments": [{"id": cid, "comment": text} for cid, text in COMMENTS.get((etype, eid), {}).items()],
        "uploads": list(UPLOADS.get((etype, eid), {}).values()),
        "canread": json.dumps({"teams": [], "users": [], "teamgroups": []}),
        "elabid": f"20260101-{eid:040x}",
    })
    return payload


def key(etype: str, eid: int) -> tuple[str, int]:
    return (etype, eid)


def app() -> FastAPI:
    app = FastAPI(title="fake-elabftw")

    def create(etype: str, body: dict[str, Any]) -> Response:
        eid = next_id()
        STATE[etype][eid] = {
            "title": body.get("title") or "Untitled",
            "body": body.get("body"),
            "category": body.get("category"),
            "status": body.get("status"),
            "metadata": body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
            "state": 1,
            "userid": 194,
            "team": 29,
        }
        if body.get("tags"):
            TAGS[(etype, eid)] = [str(t) for t in body["tags"]]
        if body.get("template"):
            template = STATE["experiments_templates"].get(int(body["template"]))
            if template:
                STATE[etype][eid]["body"] = template.get("body")
                STEPS[(etype, eid)] = {sid: dict(step) for sid, step in
                                       STEPS.get(("experiments_templates", int(body["template"])), {}).items()}
        return Response(status_code=201, headers={"Location": f"/api/v2/{etype}/{eid}"})

    # ── instance / user / teams ────────────────────────────────────────────
    @app.get("/api/v2/info")
    async def info() -> dict[str, Any]:
        return {"elabftw_version": "6.0.1-fake", "lang": "en_GB"}

    @app.get("/api/v2/users/me")
    async def users_me() -> dict[str, Any]:
        return {"userid": 194, "fullname": "Fake User", "team": 29, "is_sysadmin": 0,
                "can_manage_compounds": 0, "can_manage_inventory_locations": 0}

    @app.get("/api/v2/apikeys")
    async def apikeys() -> list[dict[str, Any]]:
        return [{"id": 7, "name": "fake", "can_write": 1, "userid": 194}]

    @app.get("/api/v2/teams/current")
    @app.get("/api/v2/teams/{team_id}")
    async def team(team_id: str = "29") -> dict[str, Any]:
        return {"id": 29, "name": "Fake Lab", "users_canwrite_experiments": 1,
                "users_canwrite_items": 1, "force_exp_tpl": 0}

    @app.get("/api/v2/teams/{team_id}/{catalogue}")
    async def team_catalogue(team_id: str, catalogue: str) -> Any:
        if catalogue not in CATEGORIES:
            return JSONResponse({"code": 404, "message": "not found"}, status_code=404)
        return CATEGORIES[catalogue]

    @app.get("/api/v2/extra_fields_keys")
    async def extra_fields_keys() -> list[dict[str, Any]]:
        return [{"key": "Instrument", "type": "text"}]

    # ── entities ──────────────────────────────────────────────────────────
    for etype in ENTITY_TYPES:
        async def list_entities(request: Request, etype: str = etype) -> Any:
            params = request.query_params
            rows = [entity_payload(etype, eid) for eid in sorted(STATE[etype])]
            if params.get("q"):
                needle = params["q"].lower()
                rows = [r for r in rows if needle in str(r.get("title", "")).lower()]
            related = params.get("related")
            if related:
                origin = params.get("related_origin", "experiments")
                wanted = [key[1] for key, targets in LINKS.items()
                          if key[0] == etype and (origin, int(related)) in targets]
                rows = [entity_payload(etype, eid) for eid in sorted(wanted)]
            limit = int(params.get("limit", 25))
            offset = int(params.get("offset", 0))
            return rows[offset:offset + limit]

        async def get_entity(eid: int, etype: str = etype) -> Any:
            if eid not in STATE[etype]:
                return JSONResponse({"code": 400, "message": "id not found"}, status_code=404)
            return entity_payload(etype, eid)

        async def create_entity(request: Request, etype: str = etype) -> Response:
            return create(etype, await request.json())

        async def patch_entity(request: Request, eid: int, etype: str = etype) -> Any:
            body = await request.json()
            entity = STATE[etype].get(eid)
            if entity is None:
                return JSONResponse({"code": 400, "message": "id not found"}, status_code=404)
            if "metadata" in body:
                if not isinstance(body["metadata"], str):
                    return JSONResponse({"code": 3140, "message": "Invalid JSON text"}, status_code=500)
                entity["metadata"] = json.loads(body["metadata"] or "{}")
            for field in ("title", "body", "category", "status", "content_type"):
                if field in body and body[field] is not None:
                    entity[field] = body[field]
            if body.get("bodyappend"):
                entity["body"] = (entity.get("body") or "") + body["bodyappend"]
            return Response(status_code=200)

        async def delete_entity(eid: int, etype: str = etype) -> Response:
            STATE[etype].pop(eid, None)
            return Response(status_code=204)

        app.add_api_route(f"/api/v2/{etype}", list_entities, methods=["GET"])
        app.add_api_route(f"/api/v2/{etype}", create_entity, methods=["POST"], status_code=201)
        app.add_api_route(f"/api/v2/{etype}/{{eid}}", get_entity, methods=["GET"])
        app.add_api_route(f"/api/v2/{etype}/{{eid}}", patch_entity, methods=["PATCH"])
        app.add_api_route(f"/api/v2/{etype}/{{eid}}", delete_entity, methods=["DELETE"])

        # steps
        @app.get(f"/api/v2/{etype}/{{eid}}/steps")
        async def list_steps(eid: int, etype: str = etype) -> Any:
            return list(STEPS.get((etype, eid), {}).values())

        @app.post(f"/api/v2/{etype}/{{eid}}/steps", status_code=201)
        async def add_step(request: Request, eid: int, etype: str = etype) -> Response:
            body = await request.json()
            sid = next_id()
            STEPS.setdefault((etype, eid), {})[sid] = {
                "id": sid, "body": body.get("body"), "finished": 0, "is_immutable": 0,
                "deadline": body.get("deadline"), "deadline_notif": body.get("deadline_notif")}
            return Response(status_code=201, headers={"Location": f"/api/v2/{etype}/{eid}/steps/{sid}"})

        @app.patch(f"/api/v2/{etype}/{{eid}}/steps/{{sid}}")
        async def patch_step(request: Request, eid: int, sid: int, etype: str = etype) -> Response:
            body = await request.json()
            step = STEPS.get((etype, eid), {}).get(sid)
            if step is None:
                return JSONResponse({"code": 400, "message": "step not found"}, status_code=404)
            action = body.get("action")
            if action == "finish":
                step["finished"] = 1 if body.get("finished") is None and not step["finished"] \
                    else int(body.get("finished", 0))
            if action == "update" or action is None:
                for field in ("body", "deadline", "deadline_notif", "is_immutable"):
                    if body.get(field) is not None:
                        step[field] = body[field]
            return Response(status_code=200)

        @app.delete(f"/api/v2/{etype}/{{eid}}/steps/{{sid}}")
        async def delete_step(eid: int, sid: int, etype: str = etype) -> Response:
            STEPS.get((etype, eid), {}).pop(sid, None)
            return Response(status_code=204)

        # tags
        @app.get(f"/api/v2/{etype}/{{eid}}/tags")
        async def list_tags(eid: int, etype: str = etype) -> Any:
            return [{"tag": t, "id": i} for i, t in enumerate(TAGS.get((etype, eid), []))]

        @app.post(f"/api/v2/{etype}/{{eid}}/tags", status_code=201)
        async def add_tags(request: Request, eid: int, etype: str = etype) -> Response:
            body = await request.json()
            current = TAGS.setdefault((etype, eid), [])
            for tag in ([body["tag"]] if isinstance(body.get("tag"), str) else body.get("tags", [])):
                if tag not in current:
                    current.append(str(tag))
            return Response(status_code=201)

        @app.patch(f"/api/v2/{etype}/{{eid}}/tags/{{tid}}")
        async def patch_tag(request: Request, eid: int, tid: int, etype: str = etype) -> Response:
            body = await request.json()
            current = TAGS.setdefault((etype, eid), [])
            if 0 <= tid < len(current):
                current[tid] = str(body.get("tag", current[tid]))
            return Response(status_code=200)

        @app.delete(f"/api/v2/{etype}/{{eid}}/tags")
        async def clear_tags(eid: int, etype: str = etype) -> Response:
            TAGS[(etype, eid)] = []
            return Response(status_code=200)

        # comments
        @app.get(f"/api/v2/{etype}/{{eid}}/comments")
        async def list_comments(eid: int, etype: str = etype) -> Any:
            return [{"id": cid, "comment": text} for cid, text in COMMENTS.get((etype, eid), {}).items()]

        @app.post(f"/api/v2/{etype}/{{eid}}/comments", status_code=201)
        async def add_comment(request: Request, eid: int, etype: str = etype) -> Response:
            body = await request.json()
            cid = next_id()
            COMMENTS.setdefault((etype, eid), {})[cid] = str(body.get("comment", ""))
            return Response(status_code=201, headers={"Location": f"/api/v2/{etype}/{eid}/comments/{cid}"})

        # links
        for segment, target_type in LINK_SEGMENTS.items():
            @app.get(f"/api/v2/{etype}/{{eid}}/{segment}")
            async def list_links(eid: int, etype: str = etype, segment: str = segment,
                                 target_type: str = target_type) -> Any:
                targets = sorted(t for t in LINKS.get((etype, eid), set()) if t[0] == target_type)
                return [dict(entity_payload(t, tid), entityid=tid, type=t, link_state=1)
                        for t, tid in targets]

            @app.post(f"/api/v2/{etype}/{{eid}}/{segment}/{{target_id}}", status_code=201)
            async def create_link(request: Request, eid: int, target_id: int, etype: str = etype,
                                  target_type: str = target_type) -> Response:
                LINKS.setdefault((etype, eid), set()).add((target_type, target_id))
                return Response(status_code=201)

            @app.delete(f"/api/v2/{etype}/{{eid}}/{segment}/{{target_id}}")
            async def remove_link(eid: int, target_id: int, etype: str = etype,
                                  target_type: str = target_type) -> Response:
                LINKS.get((etype, eid), set()).discard((target_type, target_id))
                return Response(status_code=204)

        # uploads
        @app.get(f"/api/v2/{etype}/{{eid}}/uploads")
        async def list_uploads(eid: int, etype: str = etype) -> Any:
            return list(UPLOADS.get((etype, eid), {}).values())

        @app.post(f"/api/v2/{etype}/{{eid}}/uploads", status_code=201)
        async def add_upload(request: Request, eid: int, etype: str = etype) -> Response:
            form = await request.form()
            item = form.get("file")
            uid = next_id()
            size = len(await item.read()) if item is not None else 0
            UPLOADS.setdefault((etype, eid), {})[uid] = {
                "id": uid, "real_name": getattr(item, "filename", "upload.bin"),
                "long_name": f"{uid}.bin", "filesize": size, "comment": form.get("comment")}
            return Response(status_code=201, headers={"Location": f"/api/v2/{etype}/{eid}/uploads/{uid}"})

    @app.get("/api/v2/{etype}/{eid}/records")
    async def records(etype: str, eid: int) -> Any:
        return []

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(app(), host="127.0.0.1", port=args.port, log_level="warning")
