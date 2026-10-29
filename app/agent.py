"""The agent loop: guardrails -> escalation rules -> tool-calling LLM loop -> grounding check."""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from app.config import settings
from app.escalation import MESSAGES, Escalation, classify
from app.fhir.store import FhirStore
from app.guardrails import facts_from_tool_result, is_prompt_injection, ungrounded_facts
from app.observability import current_trace_id, traceable
from app.rag import Retriever
from app.tools import TOOL_SCHEMAS, ToolBox

log = logging.getLogger("agent")
MAX_STEPS = 6


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    channel: str = "chat"
    bound_patient_id: str | None = None  # set when a patient logs in with their own token
    patient_id: str | None = None        # set only after identity verification
    messages: list[dict] = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    pending_intent: str | None = None
    offered: list = field(default_factory=list)
    grounded: set = field(default_factory=set)
    failures: int = 0
    low_confidence: int = 0
    resolved: bool = False
    escalated: str | None = None
    queued: dict | None = None
    hallucinations_blocked: int = 0


@dataclass
class TurnResult:
    reply: str
    escalation: str
    tool_calls: list[str]
    latency_ms: float
    trace_id: str
    resolved: bool
    blocked: bool = False


class Agent:
    def __init__(self, store: FhirStore | None = None, llm=None, retriever: Retriever | None = None) -> None:
        from app.llm import default_llm
        self.store = store or FhirStore()
        self.llm = llm or default_llm()
        self.tools = ToolBox(self.store, retriever or Retriever())
        self.sessions: dict[str, Session] = {}

    def session(self, session_id: str | None = None, channel: str = "chat",
                bound_patient_id: str | None = None) -> Session:
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        s = Session(channel=channel, bound_patient_id=bound_patient_id)
        if session_id:
            s.id = session_id
        self.sessions[s.id] = s
        return s

    def _escalate(self, s: Session, kind: Escalation) -> str:
        s.escalated = kind.value
        return MESSAGES[kind]

    @traceable("agent_turn")
    def turn(self, s: Session, text: str) -> TurnResult:
        t0 = time.perf_counter()
        calls: list[str] = []
        blocked = False
        if s.escalated:
            reply = "You're already being connected to a member of our team. Please hold."
        elif is_prompt_injection(text):
            blocked = True
            s.failures += 1
            reply = "I can only help with appointments and clinic questions for you."
            if s.failures >= settings.max_failures_before_handoff:
                reply = self._escalate(s, Escalation.REPEATED_FAILURE)
        elif (decision := classify(text)).escalate:
            reply = self._escalate(s, decision.kind)
        else:
            reply = self._run_llm(s, text, calls)
            if bad := ungrounded_facts(reply, s.grounded):
                s.hallucinations_blocked += 1
                log.warning("grounding violation blocked", extra={"turn": {"ungrounded": bad}})
                reply = self._escalate(s, Escalation.SAFETY)
            elif s.failures >= settings.max_failures_before_handoff + 1:
                reply = self._escalate(s, Escalation.REPEATED_FAILURE)
            elif s.low_confidence >= 2:
                reply = self._escalate(s, Escalation.LOW_CONFIDENCE)
        s.messages.append({"role": "assistant", "content": reply})
        res = TurnResult(reply, s.escalated or "none", calls, round((time.perf_counter() - t0) * 1000, 2),
                         current_trace_id(), s.resolved, blocked)
        log.info("turn", extra={"turn": {"session": s.id, "channel": s.channel, "latency_ms": res.latency_ms,
                                          "tools": calls, "escalation": res.escalation, "resolved": s.resolved,
                                          "llm": getattr(self.llm, "name", "?")}})
        return res

    def _run_llm(self, s: Session, text: str, calls: list[str]) -> str:
        s.messages.append({"role": "user", "content": text})
        convo = [m for m in s.messages if m["role"] in ("user", "assistant", "tool")]
        parts: list[str] = []
        for _ in range(MAX_STEPS):
            if s.queued:
                out, s.queued = s.queued, None
            else:
                out = self.llm.next(convo, TOOL_SCHEMAS, s)
            if out.get("tool_calls"):
                convo.append({"role": "assistant", "content": None, "tool_calls": out["tool_calls"]})
                for tc in out["tool_calls"]:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = self.tools.call(name, args, s)
                    calls.append(name)
                    s.grounded |= facts_from_tool_result(result)
                    convo.append({"role": "tool", "tool_call_id": tc["id"], "name": name,
                                  "content": json.dumps(result)})
                continue
            if out.get("content"):
                parts.append(out["content"])
            if not s.queued:
                break
        return " ".join(p for p in parts if p).strip() or "How else can I help?"
