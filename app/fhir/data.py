"""Synthetic FHIR R4 data. NO real patients. Dates fixed so tests/evals are reproducible."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

CT = timezone(timedelta(hours=-6), "CT")

PATIENTS = [
    {"id": "pat-001", "given": "Jordan", "family": "Rivera", "birthDate": "1985-04-12",
     "phone": "504-555-0101", "mrn": "SYN100001"},
    {"id": "pat-002", "given": "Ava", "family": "Thompson", "birthDate": "1992-11-03",
     "phone": "504-555-0102", "mrn": "SYN100002"},
    {"id": "pat-003", "given": "Marcus", "family": "Lee", "birthDate": "1978-07-21",
     "phone": "504-555-0103", "mrn": "SYN100003"},
]

PRACTITIONERS = [
    {"id": "prac-001", "name": "Dr. Priya Shah", "specialty": "Family Medicine"},
    {"id": "prac-002", "name": "Dr. Daniel Okafor", "specialty": "Cardiology"},
    {"id": "prac-003", "name": "Dr. Elena Garcia", "specialty": "Dermatology"},
]

BASE_DAY = datetime(2026, 11, 2, tzinfo=CT)  # a Monday


def patient_resource(p: dict) -> dict:
    return {
        "resourceType": "Patient",
        "id": p["id"],
        "identifier": [{"system": "urn:oid:synthetic-mrn", "value": p["mrn"]}],
        "name": [{"family": p["family"], "given": [p["given"]]}],
        "birthDate": p["birthDate"],
        "telecom": [{"system": "phone", "value": p["phone"]}],
    }


def practitioner_resource(p: dict) -> dict:
    return {
        "resourceType": "Practitioner",
        "id": p["id"],
        "name": [{"text": p["name"]}],
        "qualification": [{"code": {"text": p["specialty"]}}],
    }


def build_slots() -> list[dict]:
    slots = []
    for prac in PRACTITIONERS:
        for day in range(3):
            for hour in (9, 11, 14):
                start = BASE_DAY + timedelta(days=day, hours=hour)
                sid = f"slot-{prac['id'][-3:]}-{day}{hour:02d}"
                slots.append({
                    "resourceType": "Slot",
                    "id": sid,
                    "schedule": {"reference": f"Schedule/sched-{prac['id']}"},
                    "status": "free",
                    "start": start.isoformat(),
                    "end": (start + timedelta(minutes=30)).isoformat(),
                    "serviceType": [{"text": prac["specialty"]}],
                    "_practitioner": prac["id"],
                })
    return slots
