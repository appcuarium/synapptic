"""Sensitive data scrubbing — three-layer approach.

Applied before sending transcript text to LLM providers and before
persisting observations/evidence to disk.

Layer 1: Provider prefix patterns — catches secrets by their known format
Layer 2: Keyword-value pairs — catches key=secret style patterns
Layer 3: High-entropy string detection — catches unknown secret formats
"""

import math
import re


# Layer 1: Known provider secret formats (match anywhere, no keyword needed)
PROVIDER_PATTERNS = re.compile(
    r"("
    # Anthropic / OpenAI
    r"sk-[a-zA-Z0-9]{20,}"
    r"|sk-proj-[a-zA-Z0-9_-]{20,}"
    r"|sk-ant-[a-zA-Z0-9_-]{20,}"
    # Groq
    r"|gsk_[a-zA-Z0-9]{20,}"
    # GitHub
    r"|ghp_[a-zA-Z0-9]{36}"
    r"|gho_[a-zA-Z0-9]{36}"
    r"|github_pat_[a-zA-Z0-9_]{20,}"
    # Slack
    r"|xox[bpsa]-[a-zA-Z0-9\-]{20,}"
    # AWS
    r"|AKIA[A-Z0-9]{16}"
    # Stripe
    r"|sk_live_[a-zA-Z0-9]{20,}"
    r"|sk_test_[a-zA-Z0-9]{20,}"
    r"|pk_live_[a-zA-Z0-9]{20,}"
    r"|pk_test_[a-zA-Z0-9]{20,}"
    # Twilio
    r"|AC[a-f0-9]{32}"
    # SendGrid
    r"|SG\.[a-zA-Z0-9_-]{20,}"
    # Google
    r"|AIza[a-zA-Z0-9_-]{35}"
    # JWT (header.payload — don't need the signature)
    r"|eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}"
    r")"
)

# Layer 2: Keyword-value pairs (keyword followed by delimiter then value)
KEYWORD_PATTERN = re.compile(
    r"(?i)"
    r"("
    r"api[_-]?key|api[_-]?secret"
    r"|token|secret|password|passwd|pwd"
    r"|authorization|credentials?|auth"
    r"|database[_-]?url|redis[_-]?url|broker[_-]?url|mongodb[_-]?uri"
    r"|connection[_-]?string"
    r"|private[_-]?key|access[_-]?key|secret[_-]?key"
    r"|client[_-]?secret|client[_-]?id"
    r"|_KEY|_SECRET|_TOKEN|_PASS|_PWD"
    r")"
    r"""(["'\s:=]+)"""
    r"([A-Za-z]+\s+)?"  # optional auth scheme (Bearer, Basic)
    r"([A-Za-z0-9_\-/.+=]{8,})"
)

# Layer 2b: Credentials in URLs
URL_CREDS_PATTERN = re.compile(
    r"((?:postgres|postgresql|mysql|mongodb|redis|amqp|mqtt)(?:ql)?://)"
    r"([^:]+):([^@]{3,})@"
)

# PEM private keys (multiline)
PEM_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    r"[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)

# Layer 3 constants
ENTROPY_MIN_LENGTH = 20
ENTROPY_THRESHOLD = 4.2  # bits per char — English text ~4.0, random strings ~5.5+


def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy in bits per character."""
    if not s:
        return 0.0
    freq = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    length = len(s)
    entropy = 0.0
    for count in freq.values():
        p = count / length
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


# Words that look high-entropy but aren't secrets
ENTROPY_ALLOWLIST = {
    "undefined", "null", "true", "false", "localhost",
    "application", "requirements", "configuration",
    "implementation", "authentication", "authorization",
}


def scrub_high_entropy(text: str) -> str:
    """Layer 3: Replace high-entropy strings that look like secrets."""
    # Match runs of alphanumeric + common secret chars
    pattern = re.compile(r'[A-Za-z0-9_\-/.+=]{' + str(ENTROPY_MIN_LENGTH) + r',}')

    def check_and_redact(match):
        token = match.group(0)
        # Skip known safe patterns
        if token.lower() in ENTROPY_ALLOWLIST:
            return token
        # Skip file paths (contain / and start with common path prefixes)
        if '/' in token and (token.startswith('/') or token.startswith('./')):
            return token
        # Skip URLs that were already processed
        if token.startswith('http'):
            return token
        # Skip hex color codes and common hashes
        if len(token) <= 7 and all(c in '0123456789abcdefABCDEF' for c in token):
            return token
        entropy = shannon_entropy(token)
        if entropy >= ENTROPY_THRESHOLD:
            return f"[REDACTED-{len(token)}ch]"
        return token

    return pattern.sub(check_and_redact, text)


def scrub_text(text: str) -> str:
    """Apply all three scrubbing layers to text.

    Order matters:
    1. PEM keys (multiline, must go first)
    2. Provider prefixes (most specific)
    3. Keyword-value pairs
    4. URL credentials
    5. High-entropy strings (broadest, catches remainder)
    """
    if not text:
        return text

    # Layer 1a: PEM keys
    text = PEM_PATTERN.sub("[REDACTED-PEM-KEY]", text)

    # Layer 1b: Provider prefixes
    text = PROVIDER_PATTERNS.sub("[REDACTED]", text)

    # Layer 2b: URL credentials — BEFORE keyword-value so DATABASE_URL=postgres://...
    # gets the password scrubbed before the keyword match consumes the whole URL
    text = URL_CREDS_PATTERN.sub(r"\1\2:[REDACTED]@", text)

    # Layer 2a: Keyword-value pairs — preserve the keyword, redact the value
    text = KEYWORD_PATTERN.sub(
        lambda m: m.group(1) + m.group(2) + (m.group(3) or "") + "[REDACTED]",
        text,
    )

    # Layer 3: High-entropy strings
    text = scrub_high_entropy(text)

    return text
