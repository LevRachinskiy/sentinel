import asyncio
import contextlib
import hmac
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel, Field

from .config import Settings
from .connectors import READ_TOOLS
from .scenarios import SCENARIOS
from .service import Conflict, ControlPlane, Missing
from .store import Store, digest


class NewIncident(BaseModel):
    scenario: str = Field(max_length=80)
    live: bool = False


class Alert(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    source_key: str = Field(min_length=1, max_length=160)


class Decision(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64)
    decision: Literal["approve", "reject"]


def create_app(settings=None):
    settings = settings or Settings()
    settings.validate()
    store = Store(settings.database_url)
    control = ControlPlane(store, settings)
    for identity in settings.tokens.values():
        store.ensure_tenant(identity["tenant"])
    if settings.demo_mode:
        store.ensure_tenant("demo")

    async def worker():
        while True:
            try:
                await control.tick()
            except Exception:
                logging.getLogger("sentinel.worker").exception("Worker tick failed; retrying")
            await asyncio.sleep(0.15)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(worker()) if settings.worker_enabled else None
        app.state.worker = task
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        store.engine.dispose()

    app = FastAPI(title="Sentinel Control Plane", version="0.1.0", lifespan=lifespan)
    app.state.control, app.state.store = control, store
    FastAPIInstrumentor.instrument_app(app)

    def identity(authorization: str = Header(default="")):
        if settings.demo_mode and not authorization:
            return {"tenant": "demo", "actor": "demo-operator", "role": "operator"}
        token = authorization.removeprefix("Bearer ")
        for secret, record in settings.tokens.items():
            if hmac.compare_digest(token, secret):
                return record
        raise HTTPException(401, "A configured bearer token is required")

    def operator(who=Depends(identity)):
        if who["role"] != "operator":
            raise HTTPException(403, "Operator role required")
        return who

    @app.exception_handler(Missing)
    async def missing(_, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "demo" if settings.demo_mode else "authenticated"}

    @app.get("/api/session")
    def session(who=Depends(identity)):
        return {**who, "demo_mode": settings.demo_mode, "provider": settings.provider}

    @app.get("/api/scenarios")
    def scenarios(who=Depends(identity)):
        return [{"id": k, "title": v["title"], "hint": v["hint"]} for k, v in SCENARIOS.items()]

    @app.get("/api/incidents")
    def incidents(who=Depends(identity)):
        return control.list(who["tenant"])

    @app.post("/api/incidents", status_code=201)
    def create(body: NewIncident, who=Depends(operator), idempotency_key: str = Header(default="")):
        if len(idempotency_key) > 160:
            raise HTTPException(422, "Idempotency key too long")
        try:
            return control.create(
                who["tenant"], who["actor"], body.scenario, idempotency_key or uuid.uuid4().hex, body.live
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/alerts", status_code=201)
    def alert(body: Alert, who=Depends(operator)):
        try:
            return control.ingest_alert(who["tenant"], who["actor"], body.title, body.source_key)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/incidents/{incident_id}")
    def incident(incident_id, who=Depends(identity)):
        data = control.get(who["tenant"], incident_id)
        data["plan_hash"] = digest(data["plan"])
        data["events"] = store.events(who["tenant"], incident_id)
        return data

    @app.post("/api/incidents/{incident_id}/decision")
    def decide(incident_id, body: Decision, who=Depends(operator)):
        return control.decide(who["tenant"], who["actor"], incident_id, body.plan_hash, body.decision)

    @app.get("/api/incidents/{incident_id}/postmortem")
    def postmortem(incident_id, who=Depends(identity)):
        from fastapi.responses import PlainTextResponse

        i = control.get(who["tenant"], incident_id)
        if not i["postmortem"]:
            raise HTTPException(409, "A postmortem is available after verified recovery")
        return PlainTextResponse(
            i["postmortem"],
            headers={"Content-Disposition": f'attachment; filename="sentinel-{incident_id}.md"'},
        )

    @app.post("/api/evaluations")
    async def evaluations(who=Depends(operator)):
        from .evaluate import evaluate

        return await evaluate()

    @app.get("/api/audit/verify")
    def verify_audit(who=Depends(identity)):
        return store.verify_audit(who["tenant"])

    @app.get("/api/events")
    async def events(
        request: Request, after: int = 0, who=Depends(identity), last_event_id: str = Header(default="")
    ):
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError as exc:
            raise HTTPException(422, "Invalid event cursor") from exc

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                for row in store.events(who["tenant"], after=cursor):
                    cursor = row["sequence"]
                    yield f"id: {cursor}\nevent: incident\ndata: {json.dumps(row)}\n\n"
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/mcp")
    async def mcp(request: Request, who=Depends(identity)):
        # Minimal stateless JSON transport: no sessions/subscriptions/resource API.
        try:
            message = await request.json()
        except Exception:
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
        rpc_id, method = message.get("id"), message.get("method")
        if rpc_id is None:
            from fastapi.responses import Response

            return Response(status_code=202)
        result, error = None, None
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sentinel", "version": "0.1.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": name,
                        "description": f"Read {key} evidence for one incident",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"incident_id": {"type": "string"}},
                            "required": ["incident_id"],
                            "additionalProperties": False,
                        },
                        "annotations": {"readOnlyHint": True, "destructiveHint": False},
                    }
                    for name, key in READ_TOOLS.items()
                ]
            }
        elif method == "tools/call":
            params = message.get("params", {})
            if not isinstance(params, dict):
                params = {}
            name = params.get("name")
            args = params.get("arguments", {})
            if (
                name not in READ_TOOLS
                or not isinstance(args, dict)
                or set(args) != {"incident_id"}
                or not isinstance(args.get("incident_id"), str)
            ):
                error = {"code": -32602, "message": "Read-only allowlisted tools and incident_id required"}
            else:
                try:
                    incident_data = control.get(who["tenant"], args["incident_id"])
                    value = incident_data["evidence"].get(READ_TOOLS[name], {})
                    result = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
                except Missing:
                    result = {"content": [{"type": "text", "text": "Incident not found"}], "isError": True}
        else:
            error = {"code": -32601, "message": "Method not found"}
        return {"jsonrpc": "2.0", "id": rpc_id, **({"error": error} if error else {"result": result})}

    return app


def application():
    return create_app()
