"""
Seudonimización de datos personales antes de enviar texto a un LLM.

Reemplaza nombres de estudiantes conocidos, RUT, correos y teléfonos por tokens estables
(`[ESTUDIANTE_1]`, `[RUT_1]`, `[CORREO_1]`, `[TELEFONO_1]`) y permite restaurarlos y mapear
offsets de vuelta al texto original (validación de citas, IA-08).

Patrones de RUT, correo y teléfono adaptados de `src/lib/server/aiPrivacy.ts`; la idea de
alias estable por persona viene de `aiPseudonymize.ts` (rama upstream cursor/feat-agentai-9e9d).

El mapping contiene los datos originales: vive solo en memoria, nunca se loguea ni se persiste.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_TOKEN_RE = re.compile(r"\[(?:ESTUDIANTE|RUT|CORREO|TELEFONO)_\d+\]")
_EMAIL_RE = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
_RUT_RE = re.compile(
    r"(?<![\w.])(?:\d{1,2}(?:\.\d{3}){2}-?[\dkK]|\d{7,8}-[\dkK]|\d{7,8}[kK])(?!\w)"
)
_PHONE_RE = re.compile(
    r"(?<![\w+])(?:\(?\+?56\)?[\s.-]?)?9[\s.-]?\d{4}[\s.-]?\d{4}(?!\d)"
    r"|(?<![\w+])\(?\+?56\)?[\s.-]?(?:2|[3-7]\d)[\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)"
)
_KIND_PREFIX = {"name": "ESTUDIANTE", "rut": "RUT", "email": "CORREO", "phone": "TELEFONO"}
_PRIORITY = {"literal": 0, "email": 1, "rut": 2, "phone": 3, "name": 4}
_MIN_SINGLE_WORD = 3


@dataclass(frozen=True)
class Replacement:
    token: str
    original: str = field(repr=False)
    start: int  # offsets en el texto original
    end: int
    pseudo_start: int  # offsets en el texto seudonimizado
    pseudo_end: int


@dataclass
class PseudonymMapping:
    replacements: List[Replacement] = field(default_factory=list)
    canonical: Dict[str, str] = field(default_factory=dict)  # token -> primera forma original

    def __repr__(self) -> str:  # sin datos personales: seguro para logs
        return f"PseudonymMapping(tokens={len(self.canonical)}, reemplazos={len(self.replacements)})"


def _fold_char(ch: str) -> str:
    base = unicodedata.normalize("NFD", ch)[0]
    low = base.lower()
    return low if len(low) == 1 else base


def _fold(text: str) -> Tuple[str, List[int]]:
    """
    Minúsculas y sin tildes; descarta tildes combinantes (texto NFD, p.ej. extraído de PDF).
    Devuelve también la posición en el original de cada carácter normalizado.
    """
    chars, index = [], []
    for i, ch in enumerate(text):
        if not unicodedata.combining(ch):
            chars.append(_fold_char(ch))
            index.append(i)
    return "".join(chars), index


def _name_key(text: str) -> str:
    return " ".join(re.split(r"[\s,]+", _fold(text)[0].strip()))


def _rut_key(value: str) -> str:
    return re.sub(r"[^\dK]", "", value.upper())


def _phone_key(value: str) -> str:
    return re.sub(r"\D", "", value)[-9:]


def _get(person: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = person.get(key)
        if value:
            return str(value).strip()
    return ""


def _name_variants(given: List[str], surnames: List[str]) -> set:
    variants = {" ".join(given + surnames), " ".join(surnames + given)}
    if surnames:
        variants.add(" ".join(given + surnames[:1]))
        variants.add(" ".join(surnames))
        for k in range(1, len(surnames) + 1):
            variants.add(" ".join(surnames[:k] + given))
            for g in given:
                variants.add(" ".join([g] + surnames[:k]))
                variants.add(" ".join(surnames[:k] + [g]))
    singles = given + surnames[:1]
    variants.update(w for w in singles if len(w) >= _MIN_SINGLE_WORD)
    return {_name_key(v) for v in variants if v.strip()}


def _variant_pattern(key: str) -> str:
    return r"(?:\s*,\s*|\s+)".join(re.escape(word) for word in key.split(" "))


class _Roster:
    def __init__(self, known_people: List[Dict[str, Any]]):
        self.size = len(known_people)
        self.names: Dict[str, set] = {}
        self.ruts: Dict[str, int] = {}
        self.emails: Dict[str, int] = {}
        self.phones: Dict[str, int] = {}
        for idx, person in enumerate(known_people):
            given = _get(person, "nombres", "nombre").split()
            surnames = _get(person, "apellidos", "apellido").split()
            for key in _name_variants(given, surnames):
                self.names.setdefault(key, set()).add(idx)
            if rut := _rut_key(_get(person, "rut")):
                self.ruts.setdefault(rut, idx)
            if email := _get(person, "email", "correo").lower():
                self.emails.setdefault(email, idx)
            if phone := _phone_key(_get(person, "telefono", "phone")):
                self.phones.setdefault(phone, idx)
        keys = sorted(self.names, key=len, reverse=True)  # gana la forma más larga
        self.name_re = (
            re.compile(r"(?<!\w)(?:" + "|".join(_variant_pattern(k) for k in keys) + r")(?!\w)")
            if keys
            else None
        )
        bare = sorted({r for r in self.ruts if len(r) >= 8}, key=len, reverse=True)
        self.bare_rut_re = (
            re.compile(r"(?<![\w.])(?:" + "|".join(bare) + r")(?!\w)", re.IGNORECASE) if bare else None
        )


def _candidates(text: str, roster: _Roster) -> List[Tuple[int, int, str, Any]]:
    found = [(m.start(), m.end(), "literal", None) for m in _TOKEN_RE.finditer(text)]
    for m in _EMAIL_RE.finditer(text):
        idx = roster.emails.get(m.group(0).lower())
        found.append((m.start(), m.end(), "email", idx if idx is not None else ("email", m.group(0).lower())))
    rut_matches = list(_RUT_RE.finditer(text))
    if roster.bare_rut_re:
        rut_matches += list(roster.bare_rut_re.finditer(text))
    for m in rut_matches:
        key = _rut_key(m.group(0))
        idx = roster.ruts.get(key)
        found.append((m.start(), m.end(), "rut", idx if idx is not None else ("rut", key)))
    for m in _PHONE_RE.finditer(text):
        key = _phone_key(m.group(0))
        idx = roster.phones.get(key)
        found.append((m.start(), m.end(), "phone", idx if idx is not None else ("phone", key)))
    if roster.name_re:
        folded, index = _fold(text)
        for m in roster.name_re.finditer(folded):
            start, end = index[m.start()], index[m.end() - 1] + 1
            while end < len(text) and unicodedata.combining(text[end]):
                end += 1  # incluye la tilde combinante final
            key = _name_key(m.group(0))
            if " " not in key and not text[start].isupper():
                continue  # palabra suelta en minúscula: probablemente no es un nombre ("los campos")
            people = roster.names[key]
            entity = next(iter(people)) if len(people) == 1 else ("name", key)  # ambiguo: token propio
            found.append((start, end, "name", entity))
    return found


def pseudonymize(
    texto: str, known_people: Optional[List[Dict[str, Any]]] = None
) -> Tuple[str, PseudonymMapping]:
    """
    Reemplaza datos personales de `texto` por tokens y devuelve `(texto_seudo, mapping)`.

    `known_people`: filas de `students` (`nombres`, `apellidos`, `rut`, `email`; `telefono`
    opcional). DEBE venir ordenada de forma estable (por `students.id`): la persona en la
    posición i recibe `[ESTUDIANTE_{i+1}]`, `[RUT_{i+1}]` y `[CORREO_{i+1}]`, y así dos textos
    del mismo curso (p.ej. versión actual y anterior) comparten tokens.

    RUT, correos y teléfonos que no son de nadie en la lista, y nombres que comparten dos
    personas, reciben un número nuevo mayor que `len(known_people)`. Los nombres de una sola
    palabra (solo apellido o solo nombre) solo se reemplazan si empiezan con mayúscula.
    """
    roster = _Roster(known_people or [])
    selected, last_end = [], 0
    for cand in sorted(_candidates(texto, roster), key=lambda c: (c[0], c[0] - c[1], _PRIORITY[c[2]])):
        if cand[0] >= last_end:
            selected.append(cand)
            last_end = cand[1]

    mapping, numbers, out, pos, pseudo_pos = PseudonymMapping(), {}, [], 0, 0
    for start, end, kind, entity in selected:
        original = texto[start:end]
        if kind == "literal":
            token = original  # token ya presente en el texto: se conserva tal cual
        else:
            if isinstance(entity, int):
                number = entity + 1
            else:
                number = numbers.setdefault(entity, roster.size + len(numbers) + 1)
            token = f"[{_KIND_PREFIX[kind]}_{number}]"
            mapping.canonical.setdefault(token, original)
        out.append(texto[pos:start])
        pseudo_pos += start - pos
        mapping.replacements.append(Replacement(token, original, start, end, pseudo_pos, pseudo_pos + len(token)))
        out.append(token)
        pseudo_pos += len(token)
        pos = end
    out.append(texto[pos:])
    return "".join(out), mapping


def restore(texto: str, mapping: PseudonymMapping) -> str:
    """
    Reemplaza los tokens de `texto` por los datos originales.

    Si `texto` trae la misma secuencia de tokens que el texto seudonimizado, cada aparición
    vuelve a su forma exacta (`restore(*pseudonymize(x)) == x`). Si no (p.ej. respuesta del
    LLM), cada token se restaura a su forma canónica (primera aparición en el original).
    """
    found = list(_TOKEN_RE.finditer(texto))
    exact = [m.group(0) for m in found] == [r.token for r in mapping.replacements]
    out, pos = [], 0
    for i, m in enumerate(found):
        token = m.group(0)
        out.append(texto[pos:m.start()])
        out.append(mapping.replacements[i].original if exact else mapping.canonical.get(token, token))
        pos = m.end()
    out.append(texto[pos:])
    return "".join(out)


def _to_original(pos: int, mapping: PseudonymMapping, is_end: bool) -> int:
    shift = 0
    for r in mapping.replacements:
        if pos < r.pseudo_start or (is_end and pos == r.pseudo_start):
            break
        if pos < r.pseudo_end or (is_end and pos == r.pseudo_end):
            return r.end if is_end else r.start  # cae dentro de un token: cubre la entidad completa
        shift = r.end - r.pseudo_end
    return pos + shift


def map_offsets(start: int, end: int, mapping: PseudonymMapping) -> Tuple[int, int]:
    """Convierte un rango `[start, end)` del texto seudonimizado a offsets del texto original."""
    if not 0 <= start <= end:
        raise ValueError("Rango de offsets inválido.")
    return _to_original(start, mapping, is_end=False), _to_original(end, mapping, is_end=True)
