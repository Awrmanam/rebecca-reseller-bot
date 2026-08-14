from app.audit.redaction import redact
from app.bot.credentials import credential_keyboard, credential_message
from app.reseller.service import generate_password, username_candidate


def test_password_is_fresh_and_independent_of_all_api_secrets():
    tokens = ("rebecca-token", "bot-token", "plisio-secret", "api-token")
    generated = {generate_password(forbidden=tokens) for _ in range(50)}
    assert len(generated) == 50
    assert all(password not in tokens for password in generated)
    assert all(token not in password for password in generated for token in tokens)


def test_username_uses_normalized_telegram_username_and_numeric_suffix():
    assert username_candidate("@Arman_Madani", 4827) == "arman_madani_4827"
    assert username_candidate(" @Bad.Name!! ", 1000) == "bad_name_1000"


def test_username_falls_back_when_telegram_username_is_missing():
    assert username_candidate(None, 4827) == "reseller_4827"
    assert username_candidate("@---", 1234) == "reseller_1234"


def test_copy_buttons_never_put_credentials_in_callback_data():
    keyboard = credential_keyboard("seller_4827", "top-secret-password", "https://panel.test")
    serialized = keyboard.model_dump()
    callback_values = [
        button.get("callback_data")
        for row in serialized["inline_keyboard"]
        for button in row
        if button.get("callback_data") is not None
    ]
    assert callback_values == []
    assert serialized["inline_keyboard"][0][0]["copy_text"]["text"] == "seller_4827"
    assert serialized["inline_keyboard"][1][0]["copy_text"]["text"] == "top-secret-password"


def test_panel_url_and_entitlement_are_in_credential_delivery():
    text = credential_message(
        product_name="Pro", traffic_gb=100, duration_days=30,
        expiry="2027-01-01", users_limit=5, username="seller_4827",
        password="one-time", panel_url="https://panel.test/login",
    )
    assert "https://panel.test/login" in text
    assert "Pro" in text and "100 GB" in text and "30 روز" in text


def test_audit_redaction_removes_credentials_bearer_and_card_number():
    value = redact({
        "password": "do-not-store",
        "detail": "Authorization: Bearer abc.def and card 6037991234567890",
        "api_token": "token-value",
    })
    rendered = str(value)
    assert "do-not-store" not in rendered and "abc.def" not in rendered
    assert "6037991234567890" not in rendered and "token-value" not in rendered
