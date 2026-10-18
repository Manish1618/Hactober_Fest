"""Code-side checks on the user's text. These run before and independently of the model."""

from __future__ import annotations

import re

# A request to change something, phrased as an instruction. Questions that merely mention these
# words ("which orders were updated?") are not matched because the verb must lead a clause.
_WRITE_VERBS = (
    r"delete|drop|truncate|update|insert|erase|wipe|alter|rename|grant|revoke|create|merge|"
    r"overwrite|export|unload|upload|clone|undrop|cancel|kill|shut\s*down|disable|enable"
)
_IMPERATIVE_WRITE = re.compile(
    rf"(?:^|[.;!?:]\s*|\b(?:please|now|then|also|just)\s+)(?:{_WRITE_VERBS})\b",
    re.IGNORECASE,
)
_OTHER_WRITES = re.compile(
    r"^\s*(?:please\s+)?set\s+.+\bto\b|"
    r"\binto\s+(?:a\s+)?new\s+table\b|\bbackup\b.*\btable\b|"
    r"\b(?:give|assign)\b.*\b(?:role|privileges?|permissions?|ownership)\b",
    re.IGNORECASE,
)
_ROLE_SWITCH = re.compile(
    r"\b(?:use|switch\s+to|become|assume)\s+(?:the\s+)?(?:role\s+)?"
    r"(?:accountadmin|sysadmin|securityadmin|orgadmin|admin)\b",
    re.IGNORECASE,
)
_RAW_SQL_WRITE = re.compile(
    r"\b(?:delete\s+from|drop\s+(?:table|schema|database|view|role|user|warehouse)|"
    r"truncate\s+table|insert\s+into|update\s+\w+\s+set|alter\s+(?:table|session|user|role|"
    r"account|warehouse)|grant\s+\w+|create\s+(?:or\s+replace\s+)?(?:table|view|role|user|stage)|"
    r"copy\s+into|merge\s+into)\b",
    re.IGNORECASE,
)


def unsafe_reason(question: str) -> str | None:
    """Reason the request asks to change something, or None if it reads as a question."""
    if _RAW_SQL_WRITE.search(question):
        return "The request contains a command that would change data or settings."
    if _ROLE_SWITCH.search(question):
        return "Requests to switch roles or gain extra privileges are not allowed."
    if _IMPERATIVE_WRITE.search(question) or _OTHER_WRITES.search(question):
        return "DataMind only reads data. It cannot change, delete, copy or export anything."
    return None
