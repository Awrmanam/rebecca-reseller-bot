import pytest

from app.bot.handlers.settings import _masked_card, _parse_value


def test_parse_trial_service_ids():
    assert _parse_value("trial_service_ids", "2") == [2]
    assert _parse_value("trial_service_ids", "1, 2") == [1, 2]


def test_parse_trial_service_ids_rejects_empty():
    with pytest.raises(ValueError):
        _parse_value("trial_service_ids", "")


def test_parse_text_setting_preserves_string():
    assert _parse_value("card_number", " 6037991234567890 ") == "6037991234567890"


def test_masked_card_does_not_expose_full_number():
    masked = _masked_card("6037991234567890")
    assert masked.startswith("6037")
    assert masked.endswith("7890")
    assert "99123456" not in masked
