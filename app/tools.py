"""Agent tools. Each validates args with Pydantic, calls the FHIR store, and returns
small JSON the LLM can read. Errors come back as data, never as exceptions to the model."""
from __future__ import annotations

from pydantic import ValidationError

from app.fhir.store import FhirError, FhirStore
from app.guardrails import TOOL_ARGS, fmt_dt
from app.rag import Retriever

TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": n, "description": d, "parameters": TOOL_ARGS[n].model_json_schema()}}
    for n, d in [
        ("lookup_patient", "Verify caller identity by first name, last name and date of birth. Required before any patient data."),
        ("find_slots", "Find open appointment slots for a specialty."),
        ("book_appointment", "Book a slot for the verified patient. Only after the patient picked a slot."),
        ("cancel_appointment", "Cancel one of the verified patient's appointments."),
        ("list_appointments", "List the verified patient's upcoming appointments."),
        ("answer_faq", "Answer a clinic policy question from approved documents."),
    ]
]


def _slot_view(s: dict, store: FhirStore) -> dict:
    prac = store.practitioners[s["_practitioner"]]["name"][0]["text"]
    return {"slot_id": s["id"], "start": s["start"], "when": fmt_dt(s["start"]), "practitioner": prac,
            "specialty": s["serviceType"][0]["text"]}


def _appt_view(a: dict) -> dict:
    return {"appointment_id": a["id"], "start": a["start"], "when": fmt_dt(a["start"]),
            "practitioner": a["participant"][1]["actor"]["display"], "status": a["status"]}


class ToolBox:
    def __init__(self, store: FhirStore, retriever: Retriever, actor: str = "agent") -> None:
        self.store, self.retriever, self.actor = store, retriever, actor

    def call(self, name: str, raw_args: dict, session) -> dict:
        model = TOOL_ARGS.get(name)
        if model is None:
            return {"ok": False, "error": f"unknown tool {name}"}
        try:
            args = model.model_validate(raw_args)
        except ValidationError as e:
            return {"ok": False, "error": "invalid_arguments", "detail": e.errors(include_url=False)[:3]}
        # authorization: patient-scoped tools only for the verified patient of THIS session
        pid = getattr(args, "patient_id", None)
        if pid is not None and pid != session.patient_id:
            return {"ok": False, "error": "not_authorized", "detail": "identity not verified for this patient"}
        try:
            return getattr(self, name)(args, session)
        except FhirError as e:
            return {"ok": False, "error": e.code, "detail": e.message}

    def lookup_patient(self, a, session) -> dict:
        hits = self.store.search_patient(a.family_name, a.birth_date, a.given_name, actor=self.actor)
        if len(hits) != 1:
            return {"ok": False, "error": "not_verified"}
        if session.bound_patient_id and hits[0]["id"] != session.bound_patient_id:
            return {"ok": False, "error": "not_authorized"}
        session.patient_id = hits[0]["id"]
        # minimum necessary: return only what the agent needs (id + first name)
        return {"ok": True, "patient_id": hits[0]["id"], "given_name": hits[0]["name"][0]["given"][0]}

    def find_slots(self, a, session) -> dict:
        slots = self.store.search_slots(a.specialty, start_ge=a.start_date, limit=a.limit, actor=self.actor)
        return {"ok": True, "slots": [_slot_view(s, self.store) for s in slots]}

    def book_appointment(self, a, session) -> dict:
        return {"ok": True, "appointment": _appt_view(self.store.book(a.patient_id, a.slot_id, actor=self.actor))}

    def cancel_appointment(self, a, session) -> dict:
        return {"ok": True, "appointment": _appt_view(self.store.cancel(a.patient_id, a.appointment_id, actor=self.actor))}

    def list_appointments(self, a, session) -> dict:
        return {"ok": True, "appointments": [_appt_view(x) for x in self.store.appointments_for(a.patient_id, actor=self.actor)]}

    def answer_faq(self, a, session) -> dict:
        hits = self.retriever.search(a.question, k=2)
        return {"ok": True, "passages": [{"title": p.title, "text": p.text, "score": round(s, 3)} for p, s in hits]}
