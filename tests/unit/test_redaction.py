from rockygpt_brain.security.redaction import redact


def test_none_passes_through() -> None:
    assert redact(None) is None


def test_email_redacted() -> None:
    result = redact("contact me at jane.doe@example.edu please")
    assert "jane.doe@example.edu" not in result
    assert "[redacted-email]" in result


def test_phone_redacted() -> None:
    result = redact("call 555-123-4567 now")
    assert "555-123-4567" not in result
    assert "[redacted-phone]" in result


def test_ssn_redacted() -> None:
    result = redact("my ssn is 123-45-6789")
    assert "123-45-6789" not in result
    assert "[redacted-ssn]" in result


def test_payment_like_number_spaced() -> None:
    result = redact("card 4111 1111 1111 1111 expires soon")
    assert "4111 1111 1111 1111" not in result
    assert "[redacted-payment]" in result
    # The trailing word boundary must survive: no swallowed space merging
    # the marker into the next word.
    assert "expires soon" in result


def test_payment_like_number_hyphenated() -> None:
    result = redact("card 4111-1111-1111-1111 expires soon")
    assert "4111-1111-1111-1111" not in result
    assert "[redacted-payment]" in result
    assert "expires soon" in result


def test_payment_like_number_contiguous() -> None:
    result = redact("card 4111111111111111 expires soon")
    assert "4111111111111111" not in result
    assert "[redacted-payment]" in result
    assert "expires soon" in result


def test_openai_style_secret_redacted() -> None:
    result = redact("here is my key sk-abcdefghijklmnopqrstuvwx1234")
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in result
    assert "[redacted-secret]" in result


def test_student_id_like_number_redacted() -> None:
    result = redact("my id is A1234567")
    assert "[redacted-student-id]" in result


def test_ordinary_text_survives_unchanged() -> None:
    text = "What time does the library close on Fridays?"
    assert redact(text) == text


def test_short_number_not_redacted() -> None:
    text = "room 1234 building 5"
    assert redact(text) == text


def test_urls_survive_unchanged() -> None:
    # Unlike the logging module's scrubber, chat-log redaction must not
    # strip citation-bearing URLs from stored answer text.
    text = "See https://registrar.example.edu/hours for details."
    assert redact(text) == text
