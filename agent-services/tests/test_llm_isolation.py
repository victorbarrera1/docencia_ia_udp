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
