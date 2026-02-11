"""Tests for environment variable filtering (M-6).

Sensitive env vars (API keys, tokens, secrets, passwords) must be
stripped before tool execution to prevent leaking credentials into
tool output that gets sent to the LLM.
"""

from amplifier_module_loop_agent.env_filter import sanitize_env


class TestSanitizeEnv:
    """Tests for the sanitize_env() helper."""

    def test_strips_api_key(self):
        """Variables ending in _API_KEY are removed."""
        env = {"OPENAI_API_KEY": "sk-123", "HOME": "/home/user"}
        result = sanitize_env(env)
        assert "OPENAI_API_KEY" not in result
        assert result["HOME"] == "/home/user"

    def test_strips_token(self):
        """Variables ending in _TOKEN are removed."""
        env = {"GITHUB_TOKEN": "ghp_abc", "PATH": "/usr/bin"}
        result = sanitize_env(env)
        assert "GITHUB_TOKEN" not in result
        assert result["PATH"] == "/usr/bin"

    def test_strips_secret(self):
        """Variables ending in _SECRET are removed."""
        env = {"AWS_SECRET": "wJalrXU", "TERM": "xterm"}
        result = sanitize_env(env)
        assert "AWS_SECRET" not in result
        assert result["TERM"] == "xterm"

    def test_strips_password(self):
        """Variables ending in _PASSWORD are removed."""
        env = {"DB_PASSWORD": "hunter2", "LANG": "en_US"}
        result = sanitize_env(env)
        assert "DB_PASSWORD" not in result
        assert result["LANG"] == "en_US"

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        env = {"my_api_key": "val", "My_Token": "val2", "SAFE": "ok"}
        result = sanitize_env(env)
        assert "my_api_key" not in result
        assert "My_Token" not in result
        assert result["SAFE"] == "ok"

    def test_preserves_safe_vars(self):
        """Non-sensitive variables pass through unchanged."""
        env = {"HOME": "/home/u", "PATH": "/usr/bin", "LANG": "en_US"}
        result = sanitize_env(env)
        assert result == env

    def test_empty_env(self):
        """Empty dict returns empty dict."""
        assert sanitize_env({}) == {}

    def test_returns_new_dict(self):
        """Sanitized env is a new dict, not a mutation of the input."""
        env = {"OPENAI_API_KEY": "sk-123", "HOME": "/home/u"}
        result = sanitize_env(env)
        assert result is not env
        # Original is unchanged
        assert "OPENAI_API_KEY" in env

    def test_default_uses_os_environ(self):
        """When called with no argument, uses os.environ."""
        result = sanitize_env()
        # Should be a dict, not os._Environ
        assert isinstance(result, dict)
        # Should not contain any keys matching the deny patterns
        for key in result:
            upper = key.upper()
            assert not upper.endswith("_API_KEY")
            assert not upper.endswith("_TOKEN")
            assert not upper.endswith("_SECRET")
            assert not upper.endswith("_PASSWORD")

    def test_strips_secret_key_compound(self):
        """Compound patterns like _SECRET_KEY are also caught."""
        env = {"AWS_SECRET_KEY": "abc", "SAFE": "ok"}
        result = sanitize_env(env)
        assert "AWS_SECRET_KEY" not in result
        assert result["SAFE"] == "ok"

    def test_returns_stripped_names(self):
        """sanitize_env returns stripped key names when requested."""
        env = {"OPENAI_API_KEY": "sk-123", "DB_PASSWORD": "pw", "HOME": "/h"}
        result, stripped = sanitize_env(env, return_stripped=True)
        assert "OPENAI_API_KEY" not in result
        assert "DB_PASSWORD" not in result
        assert set(stripped) == {"OPENAI_API_KEY", "DB_PASSWORD"}
