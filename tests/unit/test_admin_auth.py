from rockygpt_brain.security.admin_auth import extract_bearer_token, token_is_valid


def test_extracts_bearer_token() -> None:
    assert extract_bearer_token("Bearer abc123") == "abc123"


def test_scheme_is_case_insensitive() -> None:
    assert extract_bearer_token("bearer abc123") == "abc123"
    assert extract_bearer_token("BEARER abc123") == "abc123"


def test_multiple_separating_spaces_allowed() -> None:
    assert extract_bearer_token("Bearer   abc123") == "abc123"


def test_missing_header_returns_none() -> None:
    assert extract_bearer_token(None) is None


def test_wrong_scheme_returns_none() -> None:
    assert extract_bearer_token("Basic abc123") is None


def test_missing_credential_returns_none() -> None:
    assert extract_bearer_token("Bearer ") is None
    assert extract_bearer_token("Bearer") is None


def test_extra_token_or_params_rejected() -> None:
    assert extract_bearer_token("Bearer a b") is None


def test_oversized_token_rejected() -> None:
    assert extract_bearer_token("Bearer " + "a" * 3000) is None


def test_oversized_header_rejected_outright() -> None:
    assert extract_bearer_token("x" * 5000) is None


def test_token_is_valid_matches_and_mismatches() -> None:
    assert token_is_valid(presented="secret", expected="secret")
    assert not token_is_valid(presented="wrong", expected="secret")
    assert not token_is_valid(presented=None, expected="secret")
    assert not token_is_valid(presented="", expected="secret")
