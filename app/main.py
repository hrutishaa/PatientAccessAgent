"""FastAPI service: chat + voice channels, a mock FHIR R4 API, and staff/admin endpoints."""
from __future__ import annotations

import logging
import time
from dataclasses import asdict

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from app.agent import Agent
from app.audit import audit
from app.auth import authenticate, issue_token, require
from app.config import settings
from app.fhir.store import FhirError, bundle
from app.observability import configure_logging, new_trace_id, set_trace_id
from app.voice import action_for, to_ssml, verify_signature

configure_logging()
log = logging.getLogger("http")
app = FastAPI(title="Patient Access Agent", version="1.0.0",
              description="Synthetic-data demo of a HIPAA-minded scheduling agent. Not for clinical use.")
agent = Agent()


@app.middleware("http")
async def trace_mw(request: Request, call_next):
    tid = request.headers.get("x-trace-id")
    set_trace_id(tid) if tid else new_trace_id()
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = round((time.perf_counter() - t0) * 1000, 2)
    from app.observability import current_trace_id
    response.headers["x-trace-id"] = current_trace_id()
    log.info("request", extra={"http": {"method": request.method, "path": request.url.path,
                                        "status": response.status_code, "latency_ms": ms}})
    return response


@app.exception_handler(FhirError)
async def fhir_error(_: Request, exc: FhirError):
    return JSONResponse(exc.outcome(), status_code=exc.status)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "llm": agent.llm.name}


@app.post("/token")
def token(form: OAuth2PasswordRequestForm = Depends()):
    user = authenticate(form.username, form.password)
    if not user:
        raise HTTPException(401, "Bad credentials")
    return {"access_token": issue_token(user), "token_type": "bearer"}


@app.post("/chat")
def chat(body: ChatIn, user: dict = require("chat")):
    s = agent.session(body.session_id, "chat", bound_patient_id=user.get("patient_id"))
    if user.get("patient_id"):
        s.patient_id = user["patient_id"]  # logged-in patient is already identity-verified
    r = agent.turn(s, body.message)
    return {"session_id": s.id, **asdict(r)}


@app.post("/voice")
async def voice(request: Request):
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("x-signature"), settings.voice_webhook_secret):
        raise HTTPException(403, "Bad signature")
    form = await request.json()
    s = agent.session(form.get("call_id"), "voice")
    r = agent.turn(s, form.get("speech_result", "") or "")
    return {"call_id": s.id, "ssml": to_ssml(r.reply), "action": action_for(r.escalation),
            "escalation": r.escalation, "trace_id": r.trace_id}


# ---------- staff / admin ----------
@app.get("/patients/{pid}")
def get_patient(pid: str, user: dict = require("patients:read")):
    return agent.store.read_patient(pid, actor=user["sub"])


@app.get("/appointments")
def my_appointments(patient: str | None = None, user: dict = require("chat")):
    if user["role"] == "patient":
        patient = user["patient_id"]
    elif "appointments:read" not in user["scope"].split() or not patient:
        raise HTTPException(400, "patient query parameter required")
    return bundle(agent.store.appointments_for(patient, actor=user["sub"]))


@app.get("/audit")
def get_audit(limit: int = 100, user: dict = require("audit:read")):
    return list(audit.events)[-limit:]


# ---------- mock FHIR R4 (Epic-on-FHIR shaped) ----------
@app.get("/fhir/metadata")
def capability():
    return {"resourceType": "CapabilityStatement", "fhirVersion": "4.0.1", "status": "active",
            "rest": [{"mode": "server", "security": {"service": [{"text": "SMART-on-FHIR (simulated)"}]},
                      "resource": [{"type": t} for t in ("Patient", "Practitioner", "Slot", "Appointment")]}]}


@app.get("/fhir/Patient")
def fhir_patient_search(family: str, birthdate: str, given: str | None = None, user: dict = require("fhir")):
    return bundle(agent.store.search_patient(family, birthdate, given, actor=user["sub"]))


@app.get("/fhir/Patient/{pid}")
def fhir_patient_read(pid: str, user: dict = require("fhir")):
    return agent.store.read_patient(pid, actor=user["sub"])


@app.get("/fhir/Practitioner")
def fhir_practitioners(specialty: str | None = None, user: dict = require("fhir")):
    return bundle(agent.store.search_practitioners(specialty))


@app.get("/fhir/Slot")
def fhir_slots(specialty: str | None = None, start: str | None = None, _count: int = 10,
               user: dict = require("fhir")):
    return bundle(agent.store.search_slots(specialty, start_ge=start, limit=_count, actor=user["sub"]))


class BookIn(BaseModel):
    patient: str = Field(pattern=r"^pat-\d{3}$")
    slot: str = Field(pattern=r"^slot-[\w-]+$")


@app.post("/fhir/Appointment/$book", status_code=201)
def fhir_book(body: BookIn, user: dict = require("fhir", "appointments:write")):
    return agent.store.book(body.patient, body.slot, actor=user["sub"])


@app.get("/fhir/Appointment")
def fhir_appts(patient: str, user: dict = require("fhir")):
    return bundle(agent.store.appointments_for(patient, actor=user["sub"]))


@app.post("/fhir/Appointment/{aid}/$cancel")
def fhir_cancel(aid: str, patient: str, user: dict = require("fhir", "appointments:write")):
    return agent.store.cancel(patient, aid, actor=user["sub"])
