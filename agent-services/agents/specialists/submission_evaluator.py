"""
Worker Nivel 5: evaluador de entregas con citas verificadas (IA-08). Human-in-the-loop: solo propone.

Flujo: versión + rúbrica (BD) → contexto (texto completo o RAG de IA-06) → documento seudonimizado UNA vez (IA-02)
→ `complete_json` contra el endpoint del evaluador (IA-03; OpenRouter o vLLM de la UDP) → restaurar nombres →
validar cada cita contra `texto_extraido` original → `ai_reviews` (propuesta) + `ai_observations` (pendiente).
Nunca escribe `nota_final`: eso lo hace solo `confirm_ai_review` con un docente identificado.
"""
import logging
from typing import Callable, Dict, List, Optional, Tuple

from agents.specialists.citations import Citation, validate_citation
from agents.specialists.evaluator_contract import (
    EvaluadorLLMOutput, clamp_decimas, criterion_key, grade_from_ratio, load_prompt, render_prompt, round_half_up,
    score_criteria,
)
from agents.specialists.evaluator_context import build_evidence
from agents.specialists.evidence import ensure_private, pseudonymize_document
from config import settings
from core.hierarchy import hierarchy_manager
from core.llm_support import LLMCallContext, LLMNotConfigured
from core.models import (
    CriteriaScore, ObservationCitation, PedagogicalSettings, ReviewObservation, RubricItem,
    SubmissionEvaluationRequest, SubmissionEvaluationResponse,
)
from core.openrouter_client import OpenRouterClient, endpoint_client
from core.privacy import restore
from core.review_store import ReviewStore, SupabaseReviewStore
from rag.retriever import RagRetriever
from rag.rubric_text import parse_rubric

logger = logging.getLogger("submission_evaluator")

AGENT = "submission_evaluator"
MAX_OBSERVATIONS = 30
MAX_TEXT = 2000


class EvaluatorInputError(Exception):
    """Entrada no evaluable; `code` es estable para el cliente."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def evaluator_client() -> OpenRouterClient:
    return endpoint_client(settings.evaluator_base_url, settings.evaluator_api_key, settings.evaluator_timeout_s)


class SubmissionEvaluatorWorker:
    def __init__(
        self,
        client_factory: Callable[[], OpenRouterClient] = evaluator_client,
        store_factory: Callable[[], ReviewStore] = SupabaseReviewStore,
        retriever_factory: Callable[[], RagRetriever] = RagRetriever.from_settings,
    ):
        self.role_name = AGENT
        self._client_factory, self._store_factory, self._retriever_factory = client_factory, store_factory, retriever_factory

    def evaluate_submission(self, req: SubmissionEvaluationRequest) -> SubmissionEvaluationResponse:
        client = self._client_factory()
        if not client.is_configured():  # antes de leer datos: sin modelo no hay evaluación (ni notas inventadas)
            raise LLMNotConfigured("Evaluador sin endpoint ni llave configurados")
        store = self._store_factory()
        ctx = store.version_context(str(req.version_id))
        if ctx is None:
            raise EvaluatorInputError("VERSION_NO_ENCONTRADA", "No se encontró la versión de la entrega.", 404)
        if not ctx.texto.strip():
            raise EvaluatorInputError("ENTREGA_SIN_TEXTO", "La versión no tiene texto para evaluar.", 422)
        rubric = [i for i in (parse_rubric(ctx.rubric_json) or list(req.rubric or [])) if i.puntaje_max > 0]
        if not rubric:
            raise EvaluatorInputError("RUBRICA_VACIA", "La actividad no tiene rúbrica para evaluar.", 422)
        previous = store.previous_version(ctx.submission_id, ctx.version_n)
        people = _with_owner(store.roster_for_section(ctx.section_id), ctx.owner)

        pedagogy = self._pedagogy(req)
        tope = min(x for x in (req.max_decimas, ctx.max_decimas, pedagogy.politica_decimas) if x is not None)
        prompt_version, system = self._system_prompt(req.course_id, pedagogy, tope)
        title = req.assignment_title or ctx.activity_title
        doc = build_evidence(ctx, previous, rubric, title, self._retriever_factory, settings.evaluator_max_text_chars,
                             settings.evaluator_rag_top_k)
        pseudonymize_document(doc, people)
        ensure_private(system, people)

        output, meta = client.complete_json(
            system, doc.pseudo, EvaluadorLLMOutput, model=settings.evaluator_model, temperature=0.1,
            max_tokens=settings.evaluator_max_tokens,
            context=LLMCallContext(agente=AGENT, section_id=ctx.section_id, version_id=ctx.version_id,
                                   actor=str(req.actor) if req.actor else None))

        originals = {ctx.version_id: ctx.texto, **({previous.version_id: previous.texto} if previous else {})}
        observations, discarded = self._verified_observations(output, doc, originals, rubric)
        if discarded:
            logger.info("Evaluador: %s cita(s) descartada(s) en la versión %s", discarded, ctx.version_id)

        scored = score_criteria(rubric, output.criterios)
        total_max = sum(item.puntaje_max for item, _, _ in scored)
        ratio = sum(points for _, points, _ in scored) / total_max if total_max > 0 else 0.0
        nota = grade_from_ratio(ratio, settings.evaluator_exigencia)
        decimas = clamp_decimas(output.decimas_preliminares, tope)
        resumen = _restored(output.resumen, doc, 4000)

        stored = store.create_review(
            ctx.version_id, f"{AGENT}/{prompt_version}", meta.model, nota, decimas, resumen,
            [_row(obs, cita) for obs, cita in observations])
        ids = [str(i) for i in stored.get("observation_ids", [])]
        return SubmissionEvaluationResponse(
            nota_sugerida=min(round_half_up(nota + decimas), 7.0),  # misma regla que confirm_ai_review sin nota
            decimas_sugeridas=decimas,
            resumen_feedback=resumen,
            criterios_evaluados=self._criteria_scores(scored, observations, doc),
            sugerencias_mejora=[s for s in (_restored(x, doc, 300) for x in output.sugerencias_mejora[:5]) if s],
            profesor_revisor_pendiente=True,
            review_id=str(stored["review_id"]),
            nota_preliminar=nota,
            observaciones=[
                ReviewObservation(id=ids[i] if i < len(ids) else None, **_row_view(obs, cita))
                for i, (obs, cita) in enumerate(observations)],
            citas_descartadas=discarded,
            modelo=meta.model,
        )

    # ---------- Internos ----------

    @staticmethod
    def _pedagogy(req: SubmissionEvaluationRequest) -> PedagogicalSettings:
        node = hierarchy_manager.get_node(req.course_id)
        return req.settings or (node.default_settings if node else None) or PedagogicalSettings()

    @staticmethod
    def _system_prompt(node_id: str, pedagogy: PedagogicalSettings, tope: float) -> Tuple[str, str]:
        version, template = load_prompt()
        role = render_prompt(template, {
            "EXIGENCIA": f"{settings.evaluator_exigencia * 100:g}", "TOPE_DECIMAS": f"{tope:g}",
            "NIVEL": pedagogy.nivel_exigencia.value, "ESTILO": pedagogy.estilo_pedagogico.value})
        hierarchy = hierarchy_manager.compile_system_prompt(node_id, pedagogy, worker_role=AGENT)
        return version, f"{hierarchy}\n\n{role}"

    @staticmethod
    def _verified_observations(output: EvaluadorLLMOutput, doc, originals: Dict[str, str], rubric: List[RubricItem]):
        names = {criterion_key(item.criterio): item.criterio for item in rubric}
        verified, discarded = [], 0
        for obs in output.observaciones[:MAX_OBSERVATIONS]:
            cita = validate_citation(doc, obs.cita.version, obs.cita.texto, originals, settings.evaluator_min_cita_chars)
            texto = _restored(obs.texto, doc, MAX_TEXT)
            if cita is None or not texto:
                discarded += 1
                continue
            criterio = _restored(obs.criterio or "", doc, 255) or None
            criterio = names.get(criterion_key(criterio), criterio) if criterio else None
            verified.append((obs.model_copy(update={"texto": texto, "criterio": criterio}), cita))
        discarded += max(len(output.observaciones) - MAX_OBSERVATIONS, 0)
        return verified, discarded

    @staticmethod
    def _criteria_scores(scored, observations, doc) -> List[CriteriaScore]:
        first_quote: Dict[str, str] = {}
        for obs, cita in observations:
            first_quote.setdefault(criterion_key(obs.criterio), cita.texto)
        return [CriteriaScore(
            criterio=item.criterio, puntaje_obtenido=points, puntaje_max=item.puntaje_max,
            cita_textual=first_quote.get(criterion_key(item.criterio), ""),  # nunca una cita inventada
            comentario=_restored(comment, doc, MAX_TEXT)) for item, points, comment in scored]


def _with_owner(people: List[dict], owner: Optional[dict]) -> List[dict]:
    """El dueño de la entrega se seudonimiza aunque ya no esté matriculado (va al final: no altera los tokens)."""
    if not owner or any(str(p.get("id")) == str(owner.get("id")) for p in people):
        return people
    return [*people, owner]


def _restored(text: str, doc, limit: int) -> str:
    return restore(text or "", doc.mapping).strip()[:limit]


def _row(obs, cita: Citation) -> Dict[str, object]:
    return {"criterio": obs.criterio, "tipo": obs.tipo, "severidad": obs.severidad, "texto": obs.texto,
            "cita_texto": cita.texto, "cita_version_id": cita.version_id,
            "char_start": cita.char_start, "char_end": cita.char_end}


def _row_view(obs, cita: Citation) -> Dict[str, object]:
    return {"criterio": obs.criterio, "tipo": obs.tipo, "severidad": obs.severidad, "texto": obs.texto,
            "cita": ObservationCitation(version=cita.version, version_id=cita.version_id, texto=cita.texto,
                                        char_start=cita.char_start, char_end=cita.char_end)}


submission_evaluator_worker = SubmissionEvaluatorWorker()
