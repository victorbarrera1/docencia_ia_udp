import json

import httpx
import pytest

from config import settings
from core.llm_support import (
    LLMCallContext, LLMHTTPError, LLMNotConfigured, LLMValidationError, fake_embedding,
)
from core.openrouter_client import OpenRouterClient, register_audit_listener

DIM = 8


class Harness:
    """Cliente con MockTransport: responde con la cola dada y registra peticiones, esperas y auditoría."""

    def __init__(self, responses):
        self.responses, self.requests, self.sleeps, self.audit = list(responses), [], [], []
        self.client = OpenRouterClient(transport=httpx.MockTransport(self.handle))
        self.client.api_key = "sk-or-test-key"
        self.client._sleep = self.sleeps.append
        register_audit_listener(lambda meta, ctx: self.audit.append((meta, ctx)))

    def handle(self, request):
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def vectors(n, dim=DIM):
    return [{"object": "embedding", "index": i, "embedding": [float(i + 1)] + [0.0] * (dim - 1)} for i in range(n)]


def ok(n, dim=DIM, usage=None, **extra):
    body = {"model": "openai/text-embedding-3-small", "data": vectors(n, dim),
            "usage": usage or {"prompt_tokens": 42, "total_tokens": 42, "cost": 0.0042}, **extra}
    return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def small_dim(monkeypatch):
    monkeypatch.setattr(settings, "embedding_dim", DIM)
    monkeypatch.setattr(settings, "llm_max_retries", 2)


def test_request_goes_to_embeddings_endpoint_with_configured_model():
    h = Harness([ok(2)])
    batch = h.client.embeddings(["uno", "dos"])
    req = h.requests[0]
    assert req.url.path.endswith("/embeddings") and not req.url.path.endswith("/chat/completions")
    assert req.headers["authorization"] == "Bearer sk-or-test-key"
    body = json.loads(req.content)
    assert body["model"] == settings.embedding_model == "openai/text-embedding-3-small"
    assert body["input"] == ["uno", "dos"] and "messages" not in body
    assert len(batch.vectors) == 2 and all(len(v) == DIM for v in batch.vectors)
    assert batch.model == "openai/text-embedding-3-small"


def test_vectors_are_returned_in_input_order_even_if_provider_shuffles():
    shuffled = httpx.Response(200, json={"data": list(reversed(vectors(3))), "usage": {"prompt_tokens": 3}})
    batch = Harness([shuffled]).client.embeddings(["a", "b", "c"])
    assert [v[0] for v in batch.vectors] == [1.0, 2.0, 3.0]


def test_each_call_is_audited_with_usage_and_context_but_without_text():
    h = Harness([ok(1)])
    ctx = LLMCallContext(agente="rag_embeddings", section_id="s", version_id="v")
    h.client.embeddings(["TEXTO-CONFIDENCIAL"], context=ctx)
    (meta, got_ctx), = h.audit
    assert got_ctx == ctx
    assert (meta.ok, meta.tokens_in, meta.tokens_out, meta.cost_usd, meta.attempts) == (True, 42, 0, 0.0042, 1)
    assert "TEXTO-CONFIDENCIAL" not in repr(meta) + repr(got_ctx)


def test_unknown_cost_stays_unknown():
    h = Harness([ok(1, usage={"prompt_tokens": 5})])
    h.client.embeddings(["x"])
    assert h.audit[0][0].cost_usd is None


def test_retries_rate_limit_then_succeeds():
    h = Harness([httpx.Response(429, headers={"Retry-After": "1"}, text="slow"), ok(1)])
    h.client.embeddings(["x"])
    assert len(h.requests) == 2 and h.sleeps == [1.0]
    assert [m.attempts for m, _ in h.audit] == [2] and h.audit[0][0].ok


def test_persistent_server_error_raises_and_is_audited_once():
    h = Harness([httpx.Response(500, text="boom")] * 3)
    with pytest.raises(LLMHTTPError) as err:
        h.client.embeddings(["x"])
    assert err.value.status == 500 and err.value.meta is not None
    assert len(h.audit) == 1 and h.audit[0][0].ok is False and h.audit[0][0].error == "HTTP 500"


@pytest.mark.parametrize("dim", [DIM - 1, DIM + 1])
def test_wrong_dimension_is_rejected_and_audited(dim):
    h = Harness([ok(1, dim=dim)])
    with pytest.raises(LLMValidationError):
        h.client.embeddings(["x"])
    assert h.audit[0][0].ok is False and "schema" in h.audit[0][0].error
    assert len(h.requests) == 1  # una respuesta mal formada no se reintenta


@pytest.mark.parametrize("data", [[], vectors(3), "nope"])
def test_wrong_vector_count_is_rejected(data):
    h = Harness([httpx.Response(200, json={"data": data})])
    with pytest.raises(LLMValidationError):
        h.client.embeddings(["a", "b"])


def test_non_numeric_or_non_finite_values_are_rejected():
    bad = vectors(1)
    bad[0]["embedding"][0] = "x"
    with pytest.raises(LLMValidationError):
        Harness([httpx.Response(200, json={"data": bad})]).client.embeddings(["a"])


def test_not_configured_and_empty_input():
    client = OpenRouterClient()
    client.api_key = ""
    with pytest.raises(LLMNotConfigured):
        client.embeddings(["x"])
    client.api_key = "sk-or-test-key"
    with pytest.raises(ValueError):
        client.embeddings([])


def test_fake_mode_is_deterministic_audited_and_offline(monkeypatch):
    monkeypatch.setattr(settings, "llm_fake", True)
    h = Harness([])
    a = h.client.embeddings(["alta disponibilidad"])
    b = h.client.embeddings(["alta disponibilidad"])
    assert a.vectors == b.vectors and len(a.vectors[0]) == DIM
    assert a.model == f"fake/{settings.embedding_model}" and h.requests == []
    assert [m.model for m, _ in h.audit] == ["fake/deterministic"] * 2


def test_fake_vectors_rank_similar_texts_closer():
    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))

    q = fake_embedding("tolerancia a fallas y failover", 256)
    near = fake_embedding("el failover maneja fallas del servicio", 256)
    far = fake_embedding("diagrama de clases del módulo de pagos", 256)
    assert cos(q, near) > cos(q, far)
    assert abs(cos(q, q) - 1.0) < 1e-9 and fake_embedding("", 256)[0] == 1.0
