import json

import httpx
import pytest

from core.submission_store import (
    InvalidUploader,
    StoreError,
    SubmissionNotFound,
    SubmissionStore,
)
from rag.pdf_processor import TextChunk

SUB = "e1050000-0000-4000-a000-000000000001"
USER = "a1050000-0000-4000-a000-000000000001"
PATH = f"section/s/activity/a/submission/{SUB}/v2.pdf"
RPC_OK = {"id": "f1050000-0000-4000-a000-000000000009", "version_n": 2, "storage_path": PATH, "atrasada": True, "chunks": 1}
CHUNKS = [TextChunk(idx=0, texto="Texto", char_start=0, char_end=5)]


def make_store(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SubmissionStore("https://proj.supabase.test/", "service-key", "entregas", client=client)


def test_create_version_calls_single_rpc_with_service_credentials():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json=RPC_OK)

    record = make_store(handler).create_version(SUB, USER, "Texto", CHUNKS)
    assert (record.version_n, record.atrasada, record.chunks, record.storage_path) == (2, True, 1, PATH)
    assert len(seen) == 1  # una sola llamada = una sola transacción
    req = seen[0]
    assert req.url == "https://proj.supabase.test/rest/v1/rpc/create_submission_version"
    assert req.headers["apikey"] == "service-key" and req.headers["authorization"] == "Bearer service-key"
    assert json.loads(req.content) == {
        "p_submission_id": SUB, "p_subido_por": USER, "p_texto": "Texto",
        "p_chunks": [{"idx": 0, "texto": "Texto", "char_start": 0, "char_end": 5}],
    }


@pytest.mark.parametrize("code,exc", [("P0002", SubmissionNotFound), ("23503", InvalidUploader), ("XX000", StoreError)])
def test_rpc_errors_map_to_domain_errors(code, exc):
    store = make_store(lambda r: httpx.Response(400, json={"code": code, "message": "detalle interno secreto"}))
    with pytest.raises(exc) as err:
        store.create_version(SUB, USER, "Texto", CHUNKS)
    assert "secreto" not in str(err.value)


def test_network_failure_becomes_store_error():
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(StoreError):
        make_store(boom).create_version(SUB, USER, "Texto", CHUNKS)


def test_malformed_rpc_response_is_store_error():
    with pytest.raises(StoreError):
        make_store(lambda r: httpx.Response(200, json={"id": "x"})).create_version(SUB, USER, "Texto", CHUNKS)


def test_upload_targets_private_bucket_without_upsert():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"Key": "entregas/x"})

    make_store(handler).upload_pdf(PATH, b"%PDF-1.4 datos")
    req = seen[0]
    assert req.method == "POST"
    assert req.url.path == f"/storage/v1/object/entregas/{PATH}"
    assert req.headers["content-type"] == "application/pdf" and req.headers["x-upsert"] == "false"
    assert req.content == b"%PDF-1.4 datos"


def test_upload_failure_raises_store_error():
    with pytest.raises(StoreError):
        make_store(lambda r: httpx.Response(404, json={"message": "Bucket not found"})).upload_pdf(PATH, b"x")


def test_delete_version_filters_by_id():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(204)

    make_store(handler).delete_version("abc")
    assert seen[0].method == "DELETE"
    assert seen[0].url.path == "/rest/v1/submission_versions" and seen[0].url.params["id"] == "eq.abc"
