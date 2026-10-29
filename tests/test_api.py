import json
import logging

from app.audit import audit, pseudonym
from app.observability import JsonFormatter, redact
from app.rag import Retriever
from app.voice import sign, to_ssml


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_bad_login(client):
    assert client.post("/token", data={"username": "admin", "password": "nope"}).status_code == 401


def test_chat_requires_token(client):
    assert client.post("/chat", json={"message": "hi"}).status_code == 401


def test_tampered_token_rejected(client, auth):
    h = auth("jordan")
    h["Authorization"] += "x"
    assert client.post("/chat", json={"message": "hi"}, headers=h).status_code == 401


def test_patient_chat_is_preverified(client, auth):
    h = auth("jordan")
    r = client.post("/chat", json={"message": "book a cardiology appointment"}, headers=h).json()
    assert "find_slots" in r["tool_calls"]
    r2 = client.post("/chat", json={"message": "option 1", "session_id": r["session_id"]}, headers=h).json()
    assert r2["resolved"] and "x-trace-id"


def test_patient_cannot_read_patients(client, auth):
    assert client.get("/patients/pat-002", headers=auth("jordan")).status_code == 403


def test_staff_reads_patient_and_is_audited(client, auth):
    audit.clear()
    r = client.get("/patients/pat-002", headers=auth("frontdesk"))
    assert r.status_code == 200 and r.json()["resourceType"] == "Patient"
    ev = audit.events[-1]
    assert ev["actor"] == "frontdesk" and ev["patients"] == [pseudonym("pat-002")] and "pat-002" not in json.dumps(ev)


def test_audit_admin_only(client, auth):
    assert client.get("/audit", headers=auth("frontdesk")).status_code == 403
    assert client.get("/audit", headers=auth("admin")).status_code == 200


def test_patient_appointments_scoped_to_self(client, auth):
    r = client.get("/appointments?patient=pat-002", headers=auth("jordan"))
    assert r.status_code == 200 and r.json()["total"] == 0


def test_fhir_capability_and_search(client, auth):
    h = auth("frontdesk")
    assert client.get("/fhir/metadata").json()["fhirVersion"] == "4.0.1"
    b = client.get("/fhir/Patient?family=Lee&birthdate=1978-07-21", headers=h).json()
    assert b["resourceType"] == "Bundle" and b["total"] == 1
    slots = client.get("/fhir/Slot?specialty=Dermatology&_count=2", headers=h).json()
    assert slots["total"] == 2 and slots["entry"][0]["resource"]["status"] == "free"


def test_fhir_book_conflict_and_cancel(client, auth):
    h = auth("frontdesk")
    a = client.post("/fhir/Appointment/$book", json={"patient": "pat-003", "slot": "slot-001-009"}, headers=h)
    assert a.status_code == 201 and a.json()["status"] == "booked"
    again = client.post("/fhir/Appointment/$book", json={"patient": "pat-002", "slot": "slot-001-009"}, headers=h)
    assert again.status_code == 409 and again.json()["resourceType"] == "OperationOutcome"
    c = client.post(f"/fhir/Appointment/{a.json()['id']}/$cancel?patient=pat-003", headers=h)
    assert c.json()["status"] == "cancelled"


def test_fhir_requires_staff(client, auth):
    assert client.get("/fhir/Slot", headers=auth("jordan")).status_code == 403


def test_fhir_not_found(client, auth):
    r = client.get("/fhir/Patient/pat-999", headers=auth("frontdesk"))
    assert r.status_code == 404 and r.json()["issue"][0]["code"] == "not-found"


def _voice(client, payload, secret="voice-test-secret"):
    body = json.dumps(payload).encode()
    return client.post("/voice", content=body, headers={"x-signature": sign(body, secret), "content-type": "application/json"})


def test_voice_signature_required(client):
    assert _voice(client, {"call_id": "c1", "speech_result": "hi"}, secret="wrong").status_code == 403


def test_voice_returns_ssml_and_gather(client):
    r = _voice(client, {"call_id": "c1", "speech_result": "what are your hours"}).json()
    assert r["ssml"].startswith("<speak>") and r["action"] == "gather"


def test_voice_emergency_transfer(client):
    r = _voice(client, {"call_id": "c2", "speech_result": "my wife is having a stroke"}).json()
    assert r["action"] == "transfer_emergency" and "911" in r["ssml"]


def test_ssml_escapes_and_spells_ids():
    s = to_ssml("Booked <b>. Ref appt-1001.")
    assert "&lt;b&gt;" in s and 'interpret-as="characters">appt-1001' in s


def test_redaction():
    t = redact("I'm Jordan Rivera, DOB 1985-04-12, call 504-555-0101 or j@x.com, SSN 123-45-6789")
    for leaked in ("Jordan", "1985-04-12", "504-555-0101", "j@x.com", "123-45-6789"):
        assert leaked not in t


def test_json_log_line_redacts():
    rec = logging.LogRecord("x", logging.INFO, "", 0, "my name is Ava Thompson", None, None)
    out = json.loads(JsonFormatter().format(rec))
    assert "Ava" not in out["msg"] and "trace_id" in out


def test_trace_id_header(client):
    assert client.get("/healthz", headers={"x-trace-id": "abc123"}).headers["x-trace-id"] == "abc123"


def test_retriever_ranks_relevant():
    top = Retriever().search("can I do a video visit", k=1)[0][0]
    assert top.title == "Telehealth"
