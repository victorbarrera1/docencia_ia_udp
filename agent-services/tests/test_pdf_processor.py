import pytest

from rag.pdf_processor import (
    PdfDemasiadoLargoError,
    PdfInvalidoError,
    PdfSinTextoError,
    chunk_with_offsets,
    extract_pdf_text,
    normalize_text,
)
from tests.pdf_fixtures import SAMPLE_LINES, make_blank_pdf, make_encrypted_pdf, make_text_pdf


def test_extracts_text_with_accents_and_pages():
    result = extract_pdf_text(make_text_pdf([SAMPLE_LINES, ["Segunda página ficticia con suficiente texto."]]))
    assert result.pages == 2
    assert "Diseño de una arquitectura" in result.text
    assert "Segunda página ficticia" in result.text
    assert result.text == normalize_text(result.text)  # ya normalizado: es lo que se persiste


def test_blank_pdf_is_reported_as_scanned():
    with pytest.raises(PdfSinTextoError) as err:
        extract_pdf_text(make_blank_pdf(2))
    assert err.value.code == "PDF_SIN_TEXTO"
    assert "escaneado" in err.value.message


def test_corrupt_pdf_is_invalid_without_leaking_internals():
    with pytest.raises(PdfInvalidoError) as err:
        extract_pdf_text(b"%PDF-1.4\nesto no es un pdf valido")
    assert "Traceback" not in err.value.message and "pypdf" not in err.value.message


def test_encrypted_pdf_is_rejected():
    with pytest.raises(PdfInvalidoError):
        extract_pdf_text(make_encrypted_pdf())


def test_page_and_text_limits():
    pdf = make_text_pdf([SAMPLE_LINES] * 3)
    with pytest.raises(PdfDemasiadoLargoError):
        extract_pdf_text(pdf, max_pages=2)
    with pytest.raises(PdfDemasiadoLargoError):
        extract_pdf_text(pdf, max_chars=50)


def test_normalize_text():
    raw = "Hola\x00  mundo \r\n\r\n\r\n\r\n  Diseño seguro​\tfin  "
    assert normalize_text(raw) == "Hola mundo\n\nDiseño seguro fin"


def _long_text(words=2000):
    vocab = ["alfa", "beta", "gamma", "diseño", "ñandú", "arquitectura", "😀emoji"]
    return " ".join(vocab[(i * 7) % len(vocab)] for i in range(words))


def test_chunk_offsets_slice_the_original_text_exactly():
    text = normalize_text(_long_text())
    chunks = chunk_with_offsets(text, chunk_chars=300, overlap_chars=60)
    assert len(chunks) > 10
    for i, c in enumerate(chunks):
        assert c.idx == i
        assert text[c.char_start:c.char_end] == c.texto
        assert c.texto == c.texto.strip() and c.texto
        assert c.char_end - c.char_start <= 300
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)


def test_chunks_overlap_and_cover_everything():
    text = normalize_text(_long_text())
    chunks = chunk_with_offsets(text, chunk_chars=300, overlap_chars=60)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.char_start < prev.char_end  # solape
        assert nxt.char_start > prev.char_start  # siempre avanza
        assert prev.char_end - nxt.char_start <= 60
    covered = set()
    for c in chunks:
        covered.update(range(c.char_start, c.char_end))
    assert all(i in covered or text[i].isspace() for i in range(len(text)))


def test_short_text_is_one_chunk_and_default_sizes_apply():
    text = normalize_text("Texto corto de una sola entrega ficticia.")
    only = chunk_with_offsets(text)
    assert [(c.char_start, c.char_end) for c in only] == [(0, len(text))]
    big = normalize_text(_long_text(3000))
    chunks = chunk_with_offsets(big)  # ~800 tokens ≈ 3200 caracteres
    assert 1 < len(chunks) < len(big) // 2000 + 3
    assert max(c.char_end - c.char_start for c in chunks) <= 3200


def test_unbroken_text_still_progresses():
    text = "x" * 1000
    chunks = chunk_with_offsets(text, chunk_chars=300, overlap_chars=60)
    assert chunks[-1].char_end == 1000 and all(text[c.char_start:c.char_end] == c.texto for c in chunks)


def test_invalid_overlap_rejected():
    with pytest.raises(ValueError):
        chunk_with_offsets("texto", chunk_chars=10, overlap_chars=10)
