"""In-memory FHIR R4 store shaped like Epic's FHIR scheduling APIs (Patient.Search,
Slot search, Appointment $book / cancel). Every access is written to the audit log."""
from __future__ import annotations

import copy
import itertools

from app.audit import audit
from app.fhir import data


class FhirError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def outcome(self) -> dict:
        return {"resourceType": "OperationOutcome",
                "issue": [{"severity": "error", "code": self.code, "diagnostics": self.message}]}


def bundle(resources: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "searchset", "total": len(resources),
            "entry": [{"resource": r} for r in resources]}


class FhirStore:
    def __init__(self) -> None:
        self.patients = {p["id"]: data.patient_resource(p) for p in data.PATIENTS}
        self.practitioners = {p["id"]: data.practitioner_resource(p) for p in data.PRACTITIONERS}
        self.slots = {s["id"]: s for s in data.build_slots()}
        self.appointments: dict[str, dict] = {}
        self._ids = itertools.count(1001)

    # ---- Patient -------------------------------------------------------
    def search_patient(self, family: str, birthdate: str, given: str | None = None,
                       actor: str = "system") -> list[dict]:
        hits = []
        for p in self.patients.values():
            n = p["name"][0]
            if (n["family"].lower() == family.strip().lower() and p["birthDate"] == birthdate
                    and (not given or n["given"][0].lower() == given.strip().lower())):
                hits.append(copy.deepcopy(p))
        audit.record(actor, "search", "Patient", None, [h["id"] for h in hits])
        return hits

    def read_patient(self, pid: str, actor: str = "system") -> dict:
        audit.record(actor, "read", "Patient", pid, [pid])
        if pid not in self.patients:
            raise FhirError(404, "not-found", f"Patient/{pid} not found")
        return copy.deepcopy(self.patients[pid])

    # ---- Practitioner / Slot ---------------------------------------------
    def search_practitioners(self, specialty: str | None = None) -> list[dict]:
        out = list(self.practitioners.values())
        if specialty:
            out = [p for p in out if p["qualification"][0]["code"]["text"].lower() == specialty.lower()]
        return copy.deepcopy(out)

    def search_slots(self, specialty: str | None = None, practitioner: str | None = None,
                     start_ge: str | None = None, limit: int = 3, actor: str = "system") -> list[dict]:
        out = []
        for s in sorted(self.slots.values(), key=lambda s: s["start"]):
            if s["status"] != "free":
                continue
            if specialty and s["serviceType"][0]["text"].lower() != specialty.lower():
                continue
            if practitioner and s["_practitioner"] != practitioner:
                continue
            if start_ge and s["start"][:10] < start_ge[:10]:
                continue
            out.append(copy.deepcopy(s))
        audit.record(actor, "search", "Slot", None, [])
        return out[:limit]

    # ---- Appointment -----------------------------------------------------
    def book(self, patient_id: str, slot_id: str, actor: str = "system") -> dict:
        if patient_id not in self.patients:
            raise FhirError(404, "not-found", "Unknown patient")
        slot = self.slots.get(slot_id)
        if slot is None:
            raise FhirError(404, "not-found", f"Slot/{slot_id} not found")
        if slot["status"] != "free":
            raise FhirError(409, "conflict", "Slot is no longer available")
        slot["status"] = "busy"
        aid = f"appt-{next(self._ids)}"
        prac = self.practitioners[slot["_practitioner"]]
        appt = {
            "resourceType": "Appointment", "id": aid, "status": "booked",
            "start": slot["start"], "end": slot["end"],
            "slot": [{"reference": f"Slot/{slot_id}"}],
            "serviceType": slot["serviceType"],
            "participant": [
                {"actor": {"reference": f"Patient/{patient_id}"}, "status": "accepted"},
                {"actor": {"reference": f"Practitioner/{prac['id']}",
                           "display": prac["name"][0]["text"]}, "status": "accepted"},
            ],
        }
        self.appointments[aid] = appt
        audit.record(actor, "create", "Appointment", aid, [patient_id])
        return copy.deepcopy(appt)

    def appointments_for(self, patient_id: str, actor: str = "system") -> list[dict]:
        out = [copy.deepcopy(a) for a in self.appointments.values()
               if a["participant"][0]["actor"]["reference"] == f"Patient/{patient_id}"
               and a["status"] == "booked"]
        audit.record(actor, "search", "Appointment", None, [patient_id])
        return out

    def cancel(self, patient_id: str, appointment_id: str, actor: str = "system") -> dict:
        appt = self.appointments.get(appointment_id)
        if appt is None or appt["participant"][0]["actor"]["reference"] != f"Patient/{patient_id}":
            raise FhirError(404, "not-found", "Appointment not found for this patient")
        if appt["status"] == "cancelled":
            raise FhirError(409, "conflict", "Appointment already cancelled")
        appt["status"] = "cancelled"
        self.slots[appt["slot"][0]["reference"].split("/")[1]]["status"] = "free"
        audit.record(actor, "update", "Appointment", appointment_id, [patient_id])
        return copy.deepcopy(appt)
