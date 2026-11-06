"""Validación de citas contra el texto original (IA-08): lógica pura, sin LLM."""
import pytest

from agents.specialists.citations import clean_quote, validate_citation
from agents.specialists.evidence import EvidenceBuilder, PrivacyGuardError, ensure_private, pseudonymize_document, to_pseudo
from core.privacy import pseudonymize
from tests.evaluator_fixtures import RUT_OTHER, TEXT_V1, TEXT_V2, V1, V2
from tests.rag_fixtures import PEOPLE, RUT_A, RUT_UNKNOWN

ORIGINALS = {V2: TEXT_V2, V1: TEXT_V1}


def _doc(current=TEXT_V2, previous=TEXT_V1, current_start=0):
    builder = EvidenceBuilder().text("Rúbrica: Escalabilidad\n<<<ENTREGA versión=actual>>>\n")
    builder.segment("actual", V2, current_start, current).text("\n<<<FIN>>>\n<<<ENTREGA versión=anterior>>>\n")
    builder.segment("anterior", V1, 0, previous).text("\n<<<FIN>>>\n")
    return pseudonymize_document(builder.build(), PEOPLE)


def test_valid_quote_gets_exact_offsets_in_original():
    doc = _doc()
    cita = validate_citation(doc, "actual", "usa microservicios con un balanceador", ORIGINALS, 8)
    assert cita is not None and cita.version_id == V2
    assert TEXT_V2[cita.char_start:cita.char_end] == cita.texto == "usa microservicios con un balanceador"


def test_invented_quote_is_rejected():
    assert validate_citation(_doc(), "actual", "usa una arquitectura serverless", ORIGINALS, 8) is None


def test_quote_with_tokens_maps_back_to_real_names():
    doc = _doc()
    assert "Pérez" not in doc.pseudo and RUT_A not in doc.pseudo
    name_token = doc.pseudo[doc.pseudo.index("arquitectura de ") + 16:].split(" ")[0]
    rut_token = doc.pseudo[doc.pseudo.index("(RUT ") + 5:].split(",")[0]
    quote = f"arquitectura de {name_token} (RUT {rut_token}"
    cita = validate_citation(doc, "actual", quote, ORIGINALS, 8)
    assert cita is not None
    assert cita.texto == f"arquitectura de Juan Andrés Pérez Soto (RUT {RUT_A}"
    assert TEXT_V2[cita.char_start:cita.char_end] == cita.texto


def test_whitespace_differences_are_tolerated_but_original_kept():
    cita = validate_citation(_doc(), "actual", "cuatro horas con\nreplicación entre zonas", ORIGINALS, 8)
    assert cita is not None and cita.texto == "cuatro horas con   replicación entre zonas"


def test_quotes_and_ellipsis_are_stripped():
    assert clean_quote('  "…usa microservicios..." ') == "usa microservicios"
    assert validate_citation(_doc(), "actual", "«usa microservicios con un balanceador»", ORIGINALS, 8) is not None


def test_version_label_must_match_where_the_text_is():
    doc = _doc()
    assert validate_citation(doc, "anterior", "usa microservicios con un balanceador", ORIGINALS, 8) is None
    cita = validate_citation(doc, "anterior", "Propone un monolito sin justificar", ORIGINALS, 8)
    assert cita is not None and cita.version_id == V1 and TEXT_V1[cita.char_start:cita.char_end] == cita.texto


def test_too_short_or_token_only_quotes_are_rejected():
    doc = _doc()
    assert validate_citation(doc, "actual", "usa", ORIGINALS, 8) is None
    token = doc.pseudo[doc.pseudo.index("arquitectura de ") + 16:].split(" ")[0]
    assert validate_citation(doc, "actual", f"{token} {token}", ORIGINALS, 8) is None
    assert validate_citation(doc, "actual", "", ORIGINALS, 8) is None


def test_fragment_offsets_are_relative_to_the_version_text():
    start = TEXT_V2.index("La solución")
    fragment = TEXT_V2[start:start + 60]
    doc = _doc(current=fragment, current_start=start)
    cita = validate_citation(doc, "actual", "balanceador de carga", ORIGINALS, 8)
    assert cita is not None and TEXT_V2[cita.char_start:cita.char_end] == "balanceador de carga"
    assert cita.char_start > start


def test_quote_must_match_stored_original_text():
    tampered = {V2: TEXT_V2.replace("microservicios", "microservicio"), V1: TEXT_V1}
    assert validate_citation(_doc(), "actual", "usa microservicios con un balanceador", tampered, 8) is None


def test_single_pseudonymization_keeps_unknown_entities_distinct():
    doc = _doc()
    tokens_v2 = doc.pseudo[doc.pseudo.index("y por ") + 6:].split(".")[0]
    tokens_v1 = doc.pseudo[doc.pseudo.index("Revisó ") + 7:].split(".")[0]
    assert tokens_v1.startswith("[RUT_") and tokens_v2.startswith("[RUT_") and tokens_v1 != tokens_v2
    assert RUT_UNKNOWN not in doc.pseudo and RUT_OTHER not in doc.pseudo


def test_to_pseudo_inverts_entity_boundaries():
    text = "Hola Juan Andrés Pérez Soto, bienvenido."
    pseudo, mapping = pseudonymize(text, PEOPLE)
    assert pseudo[to_pseudo(text.index(","), mapping):].startswith(", bienvenido")
    assert to_pseudo(text.index("Andrés"), mapping) == pseudo.index("[ESTUDIANTE_")


def test_privacy_guard_fails_closed():
    with pytest.raises(PrivacyGuardError):
        ensure_private("Texto con Juan Andrés Pérez Soto", PEOPLE)
    ensure_private("Texto con [ESTUDIANTE_1] y sin datos", PEOPLE)
