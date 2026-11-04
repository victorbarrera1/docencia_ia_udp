import json
from urllib.parse import parse_qs

import httpx
import pytest

from core.postgrest import Postgrest
from core.submission_store import StoreError
from memory.vector_store import ChunkRow, InMemoryVectorStore, SupabaseVectorStore
from rag.pdf_processor import TextChunk
from rag.sources import SupabaseRagSources

OWNER = "f1060000-0000-4000-a000-000000000001"
OTHER = "f1060000-0000-4000-a000-000000000002"
MODEL = "openai/text-embedding-3-small"
ROW = {"id": "c1060000-0000-4000-b000-000000000001", "owner_type": "version", "owner_id": OWNER, "idx": 0,
       "texto": "Hola", "char_start": 0, "char_end": 4}


class Api:
    def __init__(self, *responses):
        self.responses, self.seen = list(responses), []
        self.db = Postgrest("https://proj.supabase.test/", "service-key", httpx.Client(transport=httpx.MockTransport(self.handle)))

    def handle(self, request):
        self.seen.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def query(self, i=0):
        return {k: v[0] for k, v in parse_qs(self.seen[i].url.query.decode()).items()}


def test_chunks_to_embed_asks_only_for_null_embeddings():
    api = Api(httpx.Response(200, json=[ROW]))
    rows = SupabaseVectorStore(api.db).chunks_to_embed(MODEL, owner_type="version", owner_id=OWNER, limit=50)
    assert rows == [ChunkRow(ROW["id"], "version", OWNER, 0, "Hola", 0, 4)]
    req, q = api.seen[0], api.query()
    assert req.method == "GET" and req.url.path == "/rest/v1/rag_chunks"
    assert req.headers["apikey"] == "service-key" and req.headers["authorization"] == "Bearer service-key"
    assert q["embedding"] == "is.null" and q["owner_type"] == "eq.version" and q["owner_id"] == f"eq.{OWNER}" and q["limit"] == "50"
    assert "embedding" not in q["select"].split(",")  # nunca descarga los vectores


def test_force_also_selects_other_or_unknown_models():
    api = Api(httpx.Response(200, json=[]))
    SupabaseVectorStore(api.db).chunks_to_embed(MODEL, force=True)
    q = api.query()
    assert "embedding" not in q and q["or"] == f'(embedding.is.null,embedding_model.is.null,embedding_model.neq."{MODEL}")'


def test_count_foreign_looks_for_embedded_chunks_with_a_different_model():
    api = Api(httpx.Response(200, json=[{"id": "a"}, {"id": "b"}]))
    assert SupabaseVectorStore(api.db).count_foreign(MODEL, "rubric", OWNER) == 2
    q = api.query()
    assert q["embedding"] == "not.is.null" and q["or"].startswith("(embedding_model.is.null,embedding_model.neq.")


def test_set_embeddings_calls_the_rpc_with_text_guard_and_vector_literals():
    api = Api(httpx.Response(200, json=1))
    row = ChunkRow(ROW["id"], "version", OWNER, 0, "Hola", 0, 4)
    assert SupabaseVectorStore(api.db).set_embeddings(MODEL, [(row, [0.5, -1.0])]) == 1
    req = api.seen[0]
    assert req.url.path == "/rest/v1/rpc/set_rag_chunk_embeddings"
    assert json.loads(req.content) == {"p_model": MODEL, "p_items": [{"id": ROW["id"], "texto": "Hola", "embedding": "[0.5,-1.0]"}]}
    assert SupabaseVectorStore(Api().db).set_embeddings(MODEL, []) == 0  # sin items no hay petición


def test_replace_chunks_calls_the_rpc():
    api = Api(httpx.Response(200, json={"insertados": 1, "actualizados": 0, "sin_cambios": 0, "borrados": 2}))
    result = SupabaseVectorStore(api.db).replace_chunks("rubric", OWNER, [TextChunk(0, "Criterio", 0, 8)])
    assert result["borrados"] == 2
    assert json.loads(api.seen[0].content) == {
        "p_owner_type": "rubric", "p_owner_id": OWNER,
        "p_chunks": [{"idx": 0, "texto": "Criterio", "char_start": 0, "char_end": 8}]}


def test_match_sends_a_vector_literal_and_parses_hits():
    api = Api(httpx.Response(200, json=[{**ROW, "similarity": 0.91}]))
    hits = SupabaseVectorStore(api.db).match([0.1, 0.2], [OWNER], 3)
    assert api.seen[0].url.path == "/rest/v1/rpc/match_rag_chunks"
    assert json.loads(api.seen[0].content) == {"query_embedding": "[0.1,0.2]", "owner_ids": [OWNER], "k": 3}
    assert hits[0].texto == "Hola" and hits[0].similarity == 0.91 and hits[0].owner_id == OWNER
    assert SupabaseVectorStore(Api().db).match([0.1], [], 3) == []


@pytest.mark.parametrize("response", [httpx.Response(400, json={"code": "XX000", "message": "detalle interno secreto"}),
                                       httpx.Response(200, text="no es json"),
                                       httpx.ConnectError("no route")])
def test_failures_become_store_errors_without_details(response):
    api = Api(response)
    with pytest.raises(StoreError) as err:
        SupabaseVectorStore(api.db).chunks_to_embed(MODEL)
    assert "secreto" not in str(err.value)


# ---------- Fuentes ----------

def test_version_info_and_previous_version_queries():
    api = Api(httpx.Response(200, json=[{"id": OWNER, "submission_id": OTHER, "version_n": 3,
                                          "activity_submissions": {"activities": {"section_id": "s-1"}}}]),
              httpx.Response(200, json=[{"id": OWNER, "submission_id": OTHER, "version_n": 3,
                                          "activity_submissions": {"activities": {"section_id": "s-1"}}}]),
              httpx.Response(200, json=[{"id": "prev"}]))
    sources = SupabaseRagSources(api.db)
    info = sources.version_info(OWNER)
    assert (info.version_id, info.submission_id, info.version_n, info.section_id) == (OWNER, OTHER, 3, "s-1")
    assert sources.previous_version_id(OWNER) == "prev"
    q = api.query(2)
    assert q["submission_id"] == f"eq.{OTHER}" and q["version_n"] == "lt.3" and q["order"] == "version_n.desc" and q["limit"] == "1"


def test_unknown_version_and_malformed_rows():
    assert SupabaseRagSources(Api(httpx.Response(200, json=[])).db).version_info(OWNER) is None
    with pytest.raises(StoreError):
        SupabaseRagSources(Api(httpx.Response(200, json=[{"id": OWNER}])).db).version_info(OWNER)


def test_roster_is_unique_and_sorted_by_student_id():
    rows = [{"students": {"id": "b", "nombres": "B"}}, {"students": {"id": "a", "nombres": "A"}},
            {"students": {"id": "b", "nombres": "B"}}, {"students": None}]
    api = Api(httpx.Response(200, json=rows), httpx.Response(200, json=rows))
    sources = SupabaseRagSources(api.db)
    assert [p["id"] for p in sources.roster_for_section("sec")] == ["a", "b"]
    assert api.query(0)["section_id"] == "eq.sec" and api.query(0)["student_id"] == "not.is.null"
    sources.roster_for_course("cur")
    assert api.query(1)["sections.course_id"] == "eq.cur" and "sections!inner" in api.query(1)["select"]


# ---------- Equivalencia del modo en memoria ----------

def test_in_memory_store_follows_the_same_contract():
    store = InMemoryVectorStore()
    store.add_chunks("version", OWNER, [TextChunk(0, "uno", 0, 3), TextChunk(1, "dos", 4, 7)])
    rows = store.chunks_to_embed(MODEL, owner_id=OWNER)
    assert len(rows) == 2
    assert store.set_embeddings(MODEL, [(rows[0], [1.0, 0.0])]) == 1
    assert store.set_embeddings(MODEL, [(ChunkRow(rows[1].id, "version", OWNER, 1, "otro texto", 4, 7), [1.0, 0.0])]) == 0
    assert [r.idx for r in store.chunks_to_embed(MODEL)] == [1]
    assert [h.idx for h in store.match([1.0, 0.0], [OWNER], 5)] == [0]  # los pendientes no se devuelven
    assert store.match([1.0, 0.0], [OTHER], 5) == [] and store.count_foreign("otro") == 1
    with pytest.raises(StoreError):
        store.replace_chunks("version", OWNER, [])
