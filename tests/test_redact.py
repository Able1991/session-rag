import pytest

from session_rag.redact import redact

SECRETS = [
    (
        '"credentials": {"key": "EXAMPLEKEYID00000000", "secret": "exampleSecretValue0000000000abcd"}',
        ["EXAMPLEKEYID00000000", "exampleSecretValue0000000000abcd"],
    ),
    ("export OS_PASSWORD=hunter2hunter", ["hunter2hunter"]),
    ("mongodb://app:S3cr3tPass@10.0.0.1/db", ["S3cr3tPass"]),
    ("APP_ENROLL_TOKEN=ZXhhbXBsZS10b2tlbi12YWx1ZQ.abc_DEF123", ["ZXhhbXBsZS10b2tlbi12YWx1ZQ"]),
    ("Authorization: Bearer abcdefghijklmnop1234", ["abcdefghijklmnop1234"]),
    ("aws_access_key_id = AKIAIOSFODNN7EXAMPLE", ["AKIAIOSFODNN7EXAMPLE"]),
    ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", ["ghp_abcdefghijklmnopqrstuvwxyz0123456789"]),
    ("glpat-abcdefghij0123456789xyz", ["glpat-abcdefghij0123456789xyz"]),
    (
        "ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        ["sk-ant-api03-abcdefghijklmnopqrstuvwxyz"],
    ),
    ("stripe sk_live_51HxxxxxxxxxxxxxxxxxABC", ["sk_live_51HxxxxxxxxxxxxxxxxxABC"]),
    ("slack xoxb-1234567890-abcdefghij", ["xoxb-1234567890-abcdefghij"]),
    (
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        ["eyJhbGciOiJIUzI1NiJ9"],
    ),
    ("AGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ", ["AGE-SECRET-KEY-1QQQ"]),
    (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAA\n-----END OPENSSH PRIVATE KEY-----",
        ["b3BlbnNzaC1rZXktdjEAAAA"],
    ),
    ("google AIzaSyA1234567890abcdefghijklmnopqrstuv", ["AIzaSyA1234567890abcdefghijklmnopqrstuv"]),
    ("client_secret: 'f00b4rb4zqux'", ["f00b4rb4zqux"]),
    ("hf_abcdefghijklmnopqrstuvwxyzABCDEF12", ["hf_abcdefghijklmnopqrstuvwxyzABCDEF12"]),
]

KEEP = [
    "IP 203.0.113.37 streamId Xk2nAuvpQ7OIixKoPRDz1",
    "kubectl -n apps get pods worker-0-3et0594r9u6l2glv7kd83",
    "connect ETIMEDOUT 198.51.100.85:443",
    "https://cdn.example.com/hls/abc/index.m3u8",
    "the token bucket algorithm limits requests",
]


@pytest.mark.parametrize("text,secrets", SECRETS)
def test_secrets_are_masked(text, secrets):
    out = redact(text)
    for s in secrets:
        assert s not in out, out
    assert "***" in out


@pytest.mark.parametrize("text", KEEP)
def test_regular_text_is_kept(text):
    assert redact(text) == text


def test_idempotent():
    once = redact("OS_PASSWORD=hunter2hunter")
    assert redact(once) == once
