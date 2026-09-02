import hashlib
import hmac
import json

import pytest

from parchi.razorpay import RazorpayClient, RazorpayError


def test_only_test_mode_keys_are_accepted():
    with pytest.raises(RazorpayError, match="test-mode"):
        RazorpayClient("rzp_live_123", "secret")


def test_checkout_signature_binds_order_and_payment():
    client = RazorpayClient("rzp_test_123", "secret")
    signature = hmac.new(b"secret", b"order_1|pay_1", hashlib.sha256).hexdigest()
    assert client.verify_checkout_signature("order_1", "pay_1", signature)
    assert not client.verify_checkout_signature("order_2", "pay_1", signature)


def test_create_order_uses_paise_and_rejects_response_mismatch(monkeypatch):
    client = RazorpayClient("rzp_test_123", "secret")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return json.dumps({
                "id": "order_123", "amount": 420_000,
                "currency": "INR", "status": "created",
            }).encode()

    def open_request(request, timeout):
        assert timeout == 8
        assert json.loads(request.data)["amount"] == 420_000
        assert request.get_header("Authorization").startswith("Basic ")
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    order = client.create_order("txn_1", "mnd_1", 420_000)
    assert order.id == "order_123" and order.currency == "INR"


def test_create_order_never_leaks_secret_in_errors(monkeypatch):
    client = RazorpayClient("rzp_test_123", "top-secret")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("top-secret")),
    )
    with pytest.raises(RazorpayError) as exc:
        client.create_order("txn_1", "mnd_1", 100)
    assert "top-secret" not in str(exc.value)


def test_webhook_signature_covers_the_exact_raw_body():
    client = RazorpayClient("rzp_test_123", "secret", webhook_secret="whsec")
    body = b'{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_1"}}}}'
    signature = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    assert client.verify_webhook_signature(body, signature)
    # A re-serialised body - same JSON, different whitespace - must not verify.
    assert not client.verify_webhook_signature(json.dumps(json.loads(body)), signature)
    assert not client.verify_webhook_signature(b'{"event":"payment.failed"}', signature)


def test_webhook_verification_requires_a_configured_secret():
    client = RazorpayClient("rzp_test_123", "secret")
    body = b"{}"
    signature = hmac.new(b"", body, hashlib.sha256).hexdigest()
    assert not client.webhooks_configured
    assert not client.verify_webhook_signature(body, signature)
