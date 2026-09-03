import checker


def test_resolve_env_substitutes_existing_var(monkeypatch):
    monkeypatch.setenv("MY_URL", "https://example.com")
    result = checker.resolve_env("${MY_URL}/health")
    assert result == "https://example.com/health"


def test_resolve_env_leaves_unset_var_untouched(monkeypatch):
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    result = checker.resolve_env("${DOES_NOT_EXIST}/health")
    # Deliberately NOT crashing or raising - stays literal so the
    # subsequent request just fails its check instead of killing the app.
    assert result == "${DOES_NOT_EXIST}/health"


def test_resolve_env_plain_url_passes_through_unchanged():
    result = checker.resolve_env("https://example.com/health")
    assert result == "https://example.com/health"


def test_resolve_env_handles_multiple_vars_in_one_string(monkeypatch):
    monkeypatch.setenv("HOST", "example.com")
    monkeypatch.setenv("PATH_SUFFIX", "status")
    result = checker.resolve_env("https://${HOST}/${PATH_SUFFIX}")
    assert result == "https://example.com/status"
