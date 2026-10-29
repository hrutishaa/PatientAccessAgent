"""Safe-escalation policy. Rules run BEFORE the LLM so emergencies never depend on a model."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Escalation(str, Enum):
    NONE = "none"
    EMERGENCY = "emergency"
    CLINICAL = "clinical"
    USER_REQUEST = "user_request"
    REPEATED_FAILURE = "repeated_failure"
    LOW_CONFIDENCE = "low_confidence"
    SAFETY = "safety"


EMERGENCY = re.compile(
    r"chest pain|can'?t breathe|cannot breathe|trouble breathing|difficulty breathing|stroke|"
    r"heart attack|suicid|kill myself|end my life|overdos|severe bleeding|bleeding heavily|"
    r"unconscious|passed out|seizure|face is drooping", re.IGNORECASE)
CLINICAL = re.compile(
    r"\bdos(e|age)\b|should i (take|stop|start)|side effects?|\bdiagnos|is (it|this) (serious|normal)|"
    r"what medication|how much .* (take|ibuprofen|tylenol)|interact with|test results? mean|"
    r"\bprescri(be|ption) .* (change|increase|refill)", re.IGNORECASE)
HUMAN = re.compile(
    r"\b(human|real person|representative|operator|live agent|speak to (someone|a person|staff)|"
    r"talk to (someone|a person|a nurse|staff))\b", re.IGNORECASE)

MESSAGES = {
    Escalation.EMERGENCY: ("If this is a medical emergency, please hang up and call 911 now, or go to the "
                           "nearest emergency room. I'm also alerting our nurse line."),
    Escalation.CLINICAL: ("I can't give medical advice, but I'm connecting you with a nurse who can help "
                          "with that question."),
    Escalation.USER_REQUEST: "Of course. I'm transferring you to a member of our patient access team now.",
    Escalation.REPEATED_FAILURE: ("I'm sorry I haven't been able to help. I'm transferring you to a member "
                                  "of our patient access team, who can see this conversation."),
    Escalation.LOW_CONFIDENCE: ("I want to make sure you get the right help, so I'm connecting you with a "
                                "member of our team."),
    Escalation.SAFETY: ("I couldn't verify that information, so I'm connecting you with a member of our "
                        "team to make sure it's correct."),
}


@dataclass
class EscalationDecision:
    kind: Escalation
    reason: str = ""

    @property
    def escalate(self) -> bool:
        return self.kind is not Escalation.NONE


def classify(text: str) -> EscalationDecision:
    if m := EMERGENCY.search(text):
        return EscalationDecision(Escalation.EMERGENCY, m.group(0))
    if m := CLINICAL.search(text):
        return EscalationDecision(Escalation.CLINICAL, m.group(0))
    if m := HUMAN.search(text):
        return EscalationDecision(Escalation.USER_REQUEST, m.group(0))
    return EscalationDecision(Escalation.NONE)
