"""Guardrails: prompt-injection filter, typed tool arguments, and a grounding check that the
agent never states appointment facts (times, ids, clinicians) that no tool returned."""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules)|disregard (your|the) "
    r"(rules|instructions)|system prompt|developer mode|you are now|jailbreak|reveal (your|the) "
    r"(prompt|instructions)|act as (an? )?(admin|doctor|system)|list (all|every) patients|"
    r"other patients'? (records|data)", re.IGNORECASE)


def is_prompt_injection(text: str) -> bool:
    return bool(INJECTION.search(text))


# ---------- typed tool args ----------
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


class LookupPatientArgs(BaseModel):
    given_name: str = Field(min_length=1, max_length=50)
    family_name: str = Field(min_length=1, max_length=50)
    birth_date: str = Field(pattern=DATE_RE)

    @field_validator("birth_date")
    @classmethod
    def real_date(cls, v: str) -> str:
        datetime.strptime(v, "%Y-%m-%d")
        return v


class FindSlotsArgs(BaseModel):
    specialty: str = Field(pattern=r"^(Family Medicine|Cardiology|Dermatology)$")
    start_date: str | None = Field(default=None, pattern=DATE_RE)
    limit: int = Field(default=3, ge=1, le=5)


class BookArgs(BaseModel):
    patient_id: str = Field(pattern=r"^pat-\d{3}$")
    slot_id: str = Field(pattern=r"^slot-[\w-]+$")


class CancelArgs(BaseModel):
    patient_id: str = Field(pattern=r"^pat-\d{3}$")
    appointment_id: str = Field(pattern=r"^appt-\d+$")


class ListArgs(BaseModel):
    patient_id: str = Field(pattern=r"^pat-\d{3}$")


class FaqArgs(BaseModel):
    question: str = Field(min_length=2, max_length=500)


TOOL_ARGS: dict[str, type[BaseModel]] = {
    "lookup_patient": LookupPatientArgs, "find_slots": FindSlotsArgs,
    "book_appointment": BookArgs, "cancel_appointment": CancelArgs,
    "list_appointments": ListArgs, "answer_faq": FaqArgs,
}

# ---------- grounding ----------
DT_FACT = re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M\b")
ID_FACT = re.compile(r"\b(?:appt|slot)-[\w-]+\b")
DR_FACT = re.compile(r"\bDr\. [A-Z][a-z]+ [A-Z][a-z]+\b")


def fmt_dt(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%a %b %-d, %Y at %-I:%M %p")


def facts_from_tool_result(obj) -> set[str]:
    """Collect every appointment fact a tool returned, in the same surface form the agent speaks."""
    facts: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("start", "end") and isinstance(v, str):
                    facts.add(fmt_dt(v))
                elif k in ("id", "slot_id", "appointment_id") and isinstance(v, str) or k in ("display", "practitioner") and isinstance(v, str):
                    facts.add(v)
                walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)
    walk(obj)
    return facts


def ungrounded_facts(reply: str, grounded: set[str]) -> list[str]:
    claimed = DT_FACT.findall(reply) + ID_FACT.findall(reply) + DR_FACT.findall(reply)
    return [c for c in claimed if c not in grounded]
