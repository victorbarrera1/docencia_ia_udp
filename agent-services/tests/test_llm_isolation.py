import httpx
import pytest

from core.openrouter_client import openrouter_client


def test_llm_client_runs_without_key():
    assert openrouter_client.is_configured() is False


def test_network_guard_blocks_outbound_calls():
    with pytest.raises(AssertionError):
        httpx.Client().post("https://example.invalid")


async def test_network_guard_blocks_async_outbound_calls():
    async with httpx.AsyncClient() as http:
        with pytest.raises(AssertionError):
            await http.post("https://example.invalid")
        with pytest.raises(AssertionError):
            await http.send(http.build_request("GET", "https://example.invalid"))


def test_guard_allows_only_mock_transport():
    mock = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    with httpx.Client(transport=mock) as http:
        assert http.post("https://example.invalid").json() == {"ok": True}
    with httpx.Client(transport=httpx.HTTPTransport()) as http:
        with pytest.raises(AssertionError):
            http.post("https://example.invalid")


def test_guard_blocks_mock_transport_with_real_mount():
    mock = httpx.MockTransport(lambda request: httpx.Response(200))
    with httpx.Client(transport=mock, mounts={"all://": httpx.HTTPTransport()}) as http:
        with pytest.raises(AssertionError):
            http.post("https://example.invalid")
