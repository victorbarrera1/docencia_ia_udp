import unicodedata

import pytest

from core.privacy import map_offsets, pseudonymize, restore

# Personas ficticias (el repo es público: nunca datos reales).
PEOPLE = [
    {"id": "a", "nombres": "Juan Andrés", "apellidos": "Pérez Soto", "rut": "12.345.678-5", "email": "juan.perez@mail.udp.cl"},
    {"id": "b", "nombres": "María José", "apellidos": "Núñez Rojas", "rut": "7.654.321-K", "email": "maria.nunez@mail.udp.cl"},
    {"id": "c", "nombres": "Juan", "apellidos": "Campos Vera", "rut": "15.111.222-3", "email": "jcampos@gmail.com"},
]


def seudo(texto, people=PEOPLE):
    return pseudonymize(texto, people)


@pytest.mark.parametrize(
    "texto",
    [
        "Integrantes: Juan Andrés Pérez Soto y María José Núñez Rojas.",
        "Juan Perez hizo el backend",  # sin tildes
        "JUAN PÉREZ SOTO",  # mayúsculas
        "juan pérez escribió esto",  # minúsculas, nombre + apellido
        "Según Pérez, el sistema escala.",  # solo apellido
        "Pérez, Juan Andrés",  # apellido, nombre
        "Pe\u0301rez Soto",  # tilde combinante (texto NFD)
    ],
)
def test_nombres_conocidos(texto):
    out, _ = seudo(texto)
    assert "[ESTUDIANTE_1]" in out
    for palabra in ("juan", "perez", "pérez", "soto"):
        assert palabra not in out.lower()
    assert "pe\u0301rez" not in unicodedata.normalize("NFD", out.lower())


def test_varias_formas_de_la_misma_persona_mismo_token():
    out, _ = seudo("Juan Andrés Pérez Soto lideró. Pérez además documentó; juan pérez revisó.")
    assert out == "[ESTUDIANTE_1] lideró. [ESTUDIANTE_1] además documentó; [ESTUDIANTE_1] revisó."


def test_palabra_suelta_en_minuscula_no_se_toca():
    out, _ = seudo("Los campos de la tabla se validan.")
    assert out == "Los campos de la tabla se validan."
    out, _ = seudo("Campos agregó validaciones.")
    assert out == "[ESTUDIANTE_3] agregó validaciones."


def test_nombre_compartido_recibe_token_propio():
    out, _ = seudo("Juan hizo el diagrama y Juan Campos el informe.")
    assert out == "[ESTUDIANTE_4] hizo el diagrama y [ESTUDIANTE_3] el informe."


def test_nombre_dentro_de_otra_palabra_no_se_toca():
    out, _ = seudo("Usamos Sotomayor y Juanito como ejemplo.")
    assert out == "Usamos Sotomayor y Juanito como ejemplo."


@pytest.mark.parametrize(
    "rut,token",
    [
        ("12.345.678-5", "[RUT_1]"),
        ("12345678-5", "[RUT_1]"),
        ("123456785", "[RUT_1]"),  # sin guion: solo si es de la lista
        ("7.654.321-K", "[RUT_2]"),
        ("7654321-k", "[RUT_2]"),
        ("7654321K", "[RUT_2]"),
        ("9.876.543-2", "[RUT_4]"),  # desconocido: número nuevo
        ("11222333-k", "[RUT_4]"),
    ],
)
def test_rut(rut, token):
    out, _ = seudo(f"RUT: {rut}.")
    assert out == f"RUT: {token}."


def test_numero_sin_guion_desconocido_no_es_rut():
    out, _ = seudo("Pedido 200000017 confirmado.")
    assert out == "Pedido 200000017 confirmado."


def test_correos():
    out, _ = seudo("Escribir a JUAN.PEREZ@mail.udp.cl o a otro.alumno@mail.udp.cl, y a jcampos@gmail.com.")
    assert out == "Escribir a [CORREO_1] o a [CORREO_4], y a [CORREO_3]."


@pytest.mark.parametrize("telefono", ["+56 9 1234 5678", "(+56) 9 1234 5678", "+56912345678", "56-9-1234-5678", "9 8765 4321", "+56 2 2676 8000"])
def test_telefonos(telefono):
    out, _ = seudo(f"Llamar al {telefono} hoy.")
    assert out == "Llamar al [TELEFONO_4] hoy."


def test_telefono_conocido_usa_numero_de_la_persona():
    people = [{"nombres": "Ana", "apellidos": "Lagos", "telefono": "+56 9 1111 2222"}]
    out, _ = pseudonymize("Fono 911112222", people)
    assert out == "Fono [TELEFONO_1]"


def test_tokens_estables_por_persona():
    out, _ = seudo("María José Núñez (7.654.321-K, maria.nunez@mail.udp.cl) y Juan Pérez (12345678-5).")
    assert out == "[ESTUDIANTE_2] ([RUT_2], [CORREO_2]) y [ESTUDIANTE_1] ([RUT_1])."


def test_desconocidos_no_se_confunden_entre_si():
    out, _ = seudo("Contacto: 9.876.543-2, x@y.cl, +56 9 1234 5678.")
    assert out == "Contacto: [RUT_4], [CORREO_5], [TELEFONO_6]."


def test_mismo_numero_entre_textos_del_mismo_curso():
    actual, _ = seudo("Versión 2 de Núñez.")
    anterior, _ = seudo("Versión 1 de María José Núñez Rojas.")
    assert "[ESTUDIANTE_2]" in actual and "[ESTUDIANTE_2]" in anterior


def test_sin_known_people_igual_cubre_rut_correo_y_telefono():
    out, _ = pseudonymize("Juan, 12.345.678-5, a@b.cl, +56 9 1234 5678", [])
    assert out == "Juan, [RUT_1], [CORREO_2], [TELEFONO_3]"


TEXTOS_IDA_Y_VUELTA = [
    "",
    "Sin datos personales.",
    "Integrantes: Juan Andrés Pérez Soto (12.345.678-5) y María José Núñez Rojas <maria.nunez@mail.udp.cl>.",
    "PÉREZ, juan pérez, Pérez Soto, Juan: todos son el mismo. Fono +56 9 1234 5678.",
    "Ya venía un token [ESTUDIANTE_1] escrito y luego Juan Pérez. También [RUT_9].",
    "Nu\u0301n\u0303ez Rojas y 7654321k y otro 9.876.543-2.",
]


@pytest.mark.parametrize("texto", TEXTOS_IDA_Y_VUELTA)
def test_ida_y_vuelta(texto):
    out, mapping = seudo(texto)
    assert restore(out, mapping) == texto


def test_restore_salida_del_llm_usa_forma_canonica():
    _, mapping = seudo("Juan Pérez y luego Pérez. RUT 12.345.678-5.")
    llm = "[ESTUDIANTE_1] explica bien; [ESTUDIANTE_1] debe citar. Token ajeno [ESTUDIANTE_9]."
    assert restore(llm, mapping) == "Juan Pérez explica bien; Juan Pérez debe citar. Token ajeno [ESTUDIANTE_9]."


def test_map_offsets_fuera_y_dentro_de_tokens():
    texto = "Hola Juan Pérez, tu RUT 12.345.678-5 está ok."
    out, mapping = seudo(texto)
    assert out == "Hola [ESTUDIANTE_1], tu RUT [RUT_1] está ok."

    def original(fragmento):
        start = out.index(fragmento)
        s, e = map_offsets(start, start + len(fragmento), mapping)
        return texto[s:e]

    assert original("Hola") == "Hola"  # antes de cualquier token
    assert original("está ok.") == "está ok."  # después de tokens
    assert original(", tu RUT") == ", tu RUT"  # entre tokens
    assert original("[ESTUDIANTE_1], tu RUT [RUT_1]") == "Juan Pérez, tu RUT 12.345.678-5"  # cruza tokens
    assert original("DIANTE") == "Juan Pérez"  # dentro de un token: la entidad completa
    assert map_offsets(0, len(out), mapping) == (0, len(texto))


def test_map_offsets_sin_reemplazos_es_identidad():
    _, mapping = seudo("Nada que ocultar.")
    assert map_offsets(3, 8, mapping) == (3, 8)


def test_map_offsets_rango_invalido():
    _, mapping = seudo("Juan Pérez")
    with pytest.raises(ValueError):
        map_offsets(5, 2, mapping)


def test_repr_del_mapping_no_expone_datos():
    _, mapping = seudo("Juan Pérez, 12.345.678-5, juan.perez@mail.udp.cl")
    texto = repr(mapping) + repr(mapping.replacements)
    for dato in ("Juan", "Pérez", "12.345.678-5", "juan.perez"):
        assert dato not in texto
