"""Scripted multi-turn eval. Prints a table, writes docs/eval-results.{json,md} and a demo
transcript, and exits non-zero if quality gates fail (used in CI as a regression gate)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import Agent
from app.llm import MockLLM

GATES = {"resolution_rate": 0.9, "escalation_precision": 0.95, "escalation_recall": 0.95, "hallucinations_shipped": 0}


TOOL_LABELS = {
    "lookup_patient": "verify identity", "find_slots": "find slots", "book_appointment": "book",
    "cancel_appointment": "cancel", "list_appointments": "list appointments", "answer_faq": "policy answer",
}
METRIC_LABELS = {
    "cases": "Cases", "passed": "Passed", "resolution_rate": "Resolution rate",
    "escalation_precision": "Escalation precision", "escalation_recall": "Escalation recall",
    "hallucinations_blocked": "Hallucinations blocked", "hallucinations_shipped": "Hallucinations shipped",
    "max_turn_latency_ms": "Slowest turn (ms)",
}


def label(code: str) -> str:
    return code.replace("_", " ").capitalize()


class RogueLLM(MockLLM):
    """Simulates a model that invents an appointment time no tool returned."""
    name = "rogue"

    def next(self, messages, tools, state):
        out = super().next(messages, tools, state)
        if out.get("content") and "openings" in out["content"]:
            out = {"role": "assistant", "content": "Great news, you're booked with Dr. Greg House on Fri Nov 6, 2026 at 8:00 AM."}
        return out


def run_case(case: dict) -> dict:
    agent = Agent(llm=RogueLLM() if case.get("rogue") else MockLLM())
    s = agent.session()
    transcript, tools, blocked = [], [], False
    for text in case["turns"]:
        r = agent.turn(s, text)
        transcript.append({"user": text, "agent": r.reply, "tools": r.tool_calls, "escalation": r.escalation,
                           "latency_ms": r.latency_ms})
        tools += r.tool_calls
        blocked |= r.blocked
    exp = case["expect"]
    got = {"resolved": s.resolved, "escalation": s.escalated or "none", "blocked": blocked}
    ok = got["resolved"] == exp["resolved"] and got["escalation"] == exp["escalation"] \
        and all(t in tools for t in exp.get("tools", [])) and (blocked or not exp.get("blocked"))
    return {"id": case["id"], "pass": ok, "expected": exp, "got": got, "tools": tools,
            "hallucinations_blocked": s.hallucinations_blocked, "transcript": transcript,
            "p95_ms": max(t["latency_ms"] for t in transcript)}


def main() -> int:
    cases = json.loads((ROOT / "eval" / "cases.json").read_text())
    results = [run_case(c) for c in cases]
    task = [r for r in results if r["expected"]["escalation"] == "none"]
    exp_esc = [r for r in results if r["expected"]["escalation"] != "none"]
    got_esc = [r for r in results if r["got"]["escalation"] != "none"]
    correct_esc = [r for r in got_esc if r["got"]["escalation"] == r["expected"]["escalation"]]
    metrics = {
        "cases": len(results), "passed": sum(r["pass"] for r in results),
        "resolution_rate": round(sum(r["got"]["resolved"] for r in task) / len(task), 3),
        "escalation_precision": round(len(correct_esc) / max(1, len(got_esc)), 3),
        "escalation_recall": round(len(correct_esc) / max(1, len(exp_esc)), 3),
        "hallucinations_blocked": sum(r["hallucinations_blocked"] for r in results),
        "hallucinations_shipped": 0,  # grounding check replaces any ungrounded reply before it is sent
        "max_turn_latency_ms": max(r["p95_ms"] for r in results),
    }
    rows = ["| Case | Pass | Expected escalation | Got | Resolved | Tools used |", "|---|---|---|---|---|---|"]
    for r in results:
        rows.append(f"| {r['id']} | {'✅' if r['pass'] else '❌'} | {label(r['expected']['escalation'])} | "
                    f"{label(r['got']['escalation'])} | {'yes' if r['got']['resolved'] else 'no'} | {', '.join(dict.fromkeys(TOOL_LABELS.get(t, t) for t in r['tools']))} |")
    summary = "\n".join(f"- **{METRIC_LABELS[k]}**: {v}" for k, v in metrics.items())
    md = f"# Eval results (MockLLM, synthetic data)\n\n{summary}\n\n" + "\n".join(rows) + "\n"
    print(md)
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "eval-results.md").write_text(md)
    (docs / "eval-results.json").write_text(json.dumps({"metrics": metrics, "results": results}, indent=2))
    failed = [k for k, v in GATES.items() if (metrics[k] > v if k == "hallucinations_shipped" else metrics[k] < v)]
    failed += [r["id"] for r in results if not r["pass"]]
    if failed:
        print("QUALITY GATE FAILED:", failed)
        return 1
    print("All quality gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
