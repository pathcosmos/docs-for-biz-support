import httpx
import pytest

from src.scrapers.base import HttpClient, ScrapeError


def test_per_request_attempt_budget_and_structured_network_error():
    calls = 0

    def fail_connect(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("temporary DNS failure", request=request)

    with HttpClient(retries=3) as client:
        client._client.close()
        client._client = httpx.Client(transport=httpx.MockTransport(fail_connect))

        with pytest.raises(ScrapeError) as exc_info:
            client.get("https://example.test", attempts=1)

    assert calls == 1
    assert exc_info.value.kind == "ConnectError"
    assert exc_info.value.transient is True
    assert "failed after 1 attempts" in str(exc_info.value)


def test_attempt_budget_must_be_positive():
    with (
        HttpClient() as client,
        pytest.raises(ValueError, match="attempts must be at least 1"),
    ):
        client.get("https://example.test", attempts=0)
