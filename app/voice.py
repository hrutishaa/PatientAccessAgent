"""Voice channel. A telephony provider (Twilio, Cisco Webex Contact Center, ElevenLabs agents,
Amazon Connect...) does speech-to-text, POSTs the transcript here, and speaks our SSML back.
Only this adapter changes per provider; the agent is channel-agnostic."""
from __future__ import annotations

import hashlib
import hmac
import re
from xml.sax.saxutils import escape


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    if not secret:
        return True  # local dev only; set VOICE_WEBHOOK_SECRET in any real environment
    return bool(signature) and hmac.compare_digest(sign(body, secret), signature)


def to_ssml(text: str) -> str:
    """Make agent text TTS-friendly: pauses between sentences, ids spelled out."""
    safe = escape(text)
    safe = re.sub(r"\b(appt-\d+)\b", r'<say-as interpret-as="characters">\1</say-as>', safe)
    safe = re.sub(r"(?<=[.!?])\s+", '<break time="300ms"/> ', safe)
    return f"<speak>{safe}</speak>"


def action_for(escalation: str) -> str:
    if escalation == "emergency":
        return "transfer_emergency"
    return "transfer" if escalation != "none" else "gather"
