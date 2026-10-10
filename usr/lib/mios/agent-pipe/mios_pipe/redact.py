# AI-hint: Redaction utilities for secrets and PII.
import logging
import re

API_KEY_PATTERNS = [
    re.compile(r"\b(sk-[a-zA-Z0-9_-]{32,})\b"),
    re.compile(r"\b(sk-ant-[a-zA-Z0-9_-]{32,})\b"),
    re.compile(r"\b(hf_[a-zA-Z0-9]{34,})\b"),
    re.compile(r"\b(ghp_[a-zA-Z0-9]{36,})\b"),
    re.compile(r"\b(Bearer\s+[a-zA-Z0-9-._~+/]+=*)\b", re.IGNORECASE),
]

EMAIL_PATTERN = re.compile(r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")

# MIOS_* environment variable secrets / passwords
MIOS_SECRET_PATTERN = re.compile(r"\b(MIOS_[A-Z0-9_]*(?:PASS|SECRET|KEY|TOKEN|AUTH))\s*=\s*['\"]?([^'\"\s]+)['\"]?", re.IGNORECASE)

# Database connection URIs with embedded passwords (e.g. postgresql://user:pass@host:port/db)
URI_PASSWORD_PATTERN = re.compile(r"([a-zA-Z0-9+.-]+://[^:\s@]+:)([^@\s]+)(@[^\s]+)")

# Generic password/secret key-value assignments
KV_SECRET_PATTERN = re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|pass|token|passphrase|access[_-]?key)\s*([:=])\s*['\"]?(?!\[REDACTED)([^\s'\"]+)['\"]?")

def redact(text: str) -> tuple[str, bool]:
    """Sanitizes text by replacing secrets, PII, and MIOS_* variables with [REDACTED].
    Returns (redacted_text, is_redacted)."""
    if not isinstance(text, str) or not text:
        return text, False

    original = text
    redacted = False

    for pattern in API_KEY_PATTERNS:
        new_text, count = pattern.subn("[REDACTED_API_KEY]", text)
        if count > 0:
            text = new_text
            redacted = True

    text, count = EMAIL_PATTERN.subn("[REDACTED_EMAIL]", text)
    if count > 0:
        redacted = True

    text, count = MIOS_SECRET_PATTERN.subn(r"\1=[REDACTED_SECRET]", text)
    if count > 0:
        redacted = True

    text, count = URI_PASSWORD_PATTERN.subn(r"\1[REDACTED_SECRET]\3", text)
    if count > 0:
        redacted = True

    text, count = KV_SECRET_PATTERN.subn(r"\1\2[REDACTED_SECRET]", text)
    if count > 0:
        redacted = True

    return text, redacted


class RedactingLogFilter(logging.Filter):
    """Logging filter that redacts API keys, tokens, emails, and passwords before emit."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg, _ = redact(record.msg)
        elif isinstance(record.msg, Exception):
            record.msg = redact(str(record.msg))[0]

        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (redact(v)[0] if isinstance(v, str)
                        else redact(str(v))[0] if isinstance(v, Exception)
                        else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact(a)[0] if isinstance(a, str)
                    else redact(str(a))[0] if isinstance(a, Exception)
                    else a
                    for a in record.args
                )
            elif isinstance(record.args, str):
                record.args = redact(record.args)[0]
            elif isinstance(record.args, Exception):
                record.args = redact(str(record.args))[0]

        if getattr(record, "exc_info", None):
            try:
                import traceback
                exc_str = "".join(traceback.format_exception(*record.exc_info))
                record.exc_text, _ = redact(exc_str)
            except Exception:
                pass
        elif getattr(record, "exc_text", None) and isinstance(record.exc_text, str):
            record.exc_text, _ = redact(record.exc_text)

        return True

