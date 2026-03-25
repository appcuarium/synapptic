"""Tests for scrub.py — three-layer sensitive data scrubbing."""

from synapptic.scrub import scrub_text, shannon_entropy, scrub_high_entropy


class TestLayer1ProviderPrefixes:
    """Secrets detected by their known format, no keyword needed."""

    def test_anthropic_key(self):
        result = scrub_text("My key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz123456")
        assert "sk-ant-api03" not in result
        assert "[REDACTED]" in result

    def test_openai_key(self):
        result = scrub_text("sk-proj-abcdefghijklmnopqrstuvwxyz12345678901234")
        assert "abcdefgh" not in result
        assert "[REDACTED]" in result

    def test_groq_key(self):
        # Constructed at runtime to avoid secret-scanning false positives on test fixtures
        key = "gsk_" + "a1b2c3d4" * 6 + "abcd"
        result = scrub_text(key)
        assert "a1b2c3d4" not in result

    def test_github_pat(self):
        result = scrub_text("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij")
        assert "ABCDEFGH" not in result

    def test_slack_token(self):
        key = "xoxb-" + "1" * 12 + "-" + "2" * 13 + "-" + "AbCdEfGh" * 3
        result = scrub_text(key)
        assert "AbCdEfGh" not in result

    def test_aws_access_key(self):
        result = scrub_text("AKIAIOSFODNN7EXAMPLE")
        assert "IOSFODNN" not in result

    def test_stripe_key(self):
        key = "sk_live_" + "abcd1234" * 4
        result = scrub_text(key)
        assert "abcd1234" not in result

    def test_jwt(self):
        header = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        payload = "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
        result = scrub_text(f"token: {header}.{payload}.signature")
        assert header not in result

    def test_google_api_key(self):
        result = scrub_text("AIzaSyA-abcdefghijklmnopqrstuvwxyz12345")
        assert "abcdefgh" not in result

    def test_normal_text_untouched(self):
        text = "This is a normal sentence about coding patterns."
        assert scrub_text(text) == text


class TestLayer2KeywordValue:
    """Secrets detected by keyword=value pattern."""

    def test_api_key_equals(self):
        result = scrub_text('API_KEY=abcdefghijklmnop')
        assert "API_KEY" in result  # keyword preserved
        assert "abcdefgh" not in result
        assert "[REDACTED]" in result

    def test_password_colon(self):
        result = scrub_text('password: "mysecretpassword123"')
        assert "password" in result
        assert "mysecretpassword123" not in result

    def test_authorization_bearer(self):
        result = scrub_text('Authorization: Bearer abcdefghijklmnopqrstuvwxyz')
        assert "Authorization" in result
        assert "Bearer" in result
        assert "abcdefghijklmnop" not in result

    def test_database_url(self):
        result = scrub_text('DATABASE_URL="postgres://user:secret123@host/db"')
        assert "DATABASE_URL" in result
        assert "secret123" not in result

    def test_token_in_env(self):
        result = scrub_text('export SLACK_TOKEN=xoxb-some-long-token-value-here')
        assert "SLACK_TOKEN" in result
        assert "some-long-token" not in result

    def test_short_values_not_matched(self):
        """Values under 8 chars shouldn't trigger keyword match."""
        result = scrub_text('password: "short"')
        assert 'short' in result  # too short to match


class TestLayer2bUrlCredentials:
    """Credentials embedded in connection URLs."""

    def test_postgres_url(self):
        result = scrub_text("postgres://admin:supersecret@db.host.com:5432/mydb")
        assert "admin" in result
        assert "supersecret" not in result
        assert "[REDACTED]@" in result
        assert "db.host.com" in result

    def test_redis_url(self):
        result = scrub_text("redis://default:mypassword@redis.host:6379")
        assert "mypassword" not in result

    def test_mongodb_url(self):
        result = scrub_text("mongodb://user:pass123@mongo.host/admin")
        assert "pass123" not in result

    def test_url_without_password(self):
        result = scrub_text("postgres://localhost:5432/mydb")
        assert result == "postgres://localhost:5432/mydb"


class TestLayer1PemKeys:
    """PEM private keys (multiline)."""

    def test_rsa_key(self):
        key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\nmore lines\n-----END RSA PRIVATE KEY-----"
        result = scrub_text(f"Here is the key:\n{key}\nEnd.")
        assert "BEGIN RSA PRIVATE KEY" not in result
        assert "[REDACTED-PEM-KEY]" in result
        assert "Here is the key:" in result

    def test_generic_private_key(self):
        key = "-----BEGIN PRIVATE KEY-----\ndata\n-----END PRIVATE KEY-----"
        result = scrub_text(key)
        assert "data" not in result


class TestLayer3Entropy:
    """High-entropy strings that don't match any known pattern."""

    def test_high_entropy_redacted(self):
        # Random-looking string with high entropy
        secret = "Kj9mX4pQ2rT8wL5nB7vC3hF6"
        result = scrub_text(f"value: {secret}")
        assert secret not in result
        assert "[REDACTED-" in result

    def test_normal_words_not_redacted(self):
        text = "implementation configuration authentication"
        assert scrub_text(text) == text

    def test_file_paths_not_redacted(self):
        text = "/Users/sorin/projects/machine-be/machine/consumers/workers"
        result = scrub_text(text)
        assert "machine-be" in result

    def test_repeated_chars_low_entropy(self):
        text = "aaaaaaaaaaaaaaaaaaaaaa"  # 22 chars, entropy = 0
        assert scrub_text(text) == text

    def test_hex_colors_not_redacted(self):
        assert scrub_text("#FFA726") == "#FFA726"


class TestShannonEntropy:

    def test_empty_string(self):
        assert shannon_entropy("") == 0.0

    def test_single_char_repeated(self):
        assert shannon_entropy("aaaaaaa") == 0.0

    def test_two_chars_equal(self):
        # "abababab" — 2 chars equally distributed = 1.0 bit
        assert abs(shannon_entropy("abababab") - 1.0) < 0.01

    def test_random_string_high(self):
        # Mix of many different chars = high entropy
        e = shannon_entropy("aB3$xK9!mZ7@qR5&")
        assert e > 3.5

    def test_english_text_moderate(self):
        e = shannon_entropy("this is a normal english sentence with some words")
        assert 3.0 < e < 4.5


class TestScrubIntegration:
    """Combined scenarios testing all layers together."""

    def test_env_file_content(self):
        text = """
DATABASE_URL=postgres://admin:hunter2secret@db.prod.com:5432/app
OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz12345678901234
SLACK_TOKEN=xoxb-123-456-abcdefghijklmnopqrstuvwxyz
REDIS_URL=redis://default:redispass@redis.prod.com:6379
"""
        result = scrub_text(text)
        # Keywords preserved
        assert "DATABASE_URL" in result
        assert "OPENAI_API_KEY" in result
        assert "SLACK_TOKEN" in result
        # Secrets scrubbed
        assert "hunter2secret" not in result
        assert "sk-proj-abcdef" not in result
        assert "redispass" not in result

    def test_mixed_content(self):
        text = "The API key is sk-ant-abcdefghijklmnopqrstuvwxyz. Use it to call the /v1/messages endpoint."
        result = scrub_text(text)
        assert "[REDACTED]" in result
        assert "/v1/messages" in result
        assert "The API key is" in result

    def test_empty_string(self):
        assert scrub_text("") == ""

    def test_none_returns_none(self):
        assert scrub_text(None) is None
