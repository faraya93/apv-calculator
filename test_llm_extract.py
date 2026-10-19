"""Validación de la respuesta del LLM sin llamadas de red."""

from llm_extract import parsear_respuesta_llm, validar_extraccion


def test_respuesta_valida():
    bruto = parsear_respuesta_llm(
        '{"total_imponible": 2500000, "descuento_afp": 281750, '
        '"descuento_cesantia": 15000, "sueldo_bruto": 2585000}'
    )
    assert bruto == {
        "total_imponible": 2_500_000.0,
        "descuento_afp": 281_750.0,
        "descuento_cesantia": 15_000.0,
        "sueldo_bruto": 2_585_000.0,
    }


def test_respuesta_con_nulls():
    datos = parsear_respuesta_llm(
        '{"total_imponible": 1800000, "descuento_afp": null, '
        '"descuento_cesantia": null, "sueldo_bruto": null}'
    )
    assert datos == {
        "total_imponible": 1_800_000.0,
        "descuento_afp": None,
        "descuento_cesantia": None,
        "sueldo_bruto": None,
    }


def test_rechaza_imponible_mayor_que_bruto():
    """Alucinación típica: total_imponible > sueldo_bruto → None."""
    assert (
        parsear_respuesta_llm(
            '{"total_imponible": 3000000, "descuento_afp": 100000, '
            '"descuento_cesantia": 10000, "sueldo_bruto": 2500000}'
        )
        is None
    )
    assert (
        validar_extraccion(
            {
                "total_imponible": 3_000_000,
                "descuento_afp": 100_000,
                "descuento_cesantia": 10_000,
                "sueldo_bruto": 2_500_000,
            }
        )
        is None
    )


def test_json_malformado_devuelve_none():
    assert parsear_respuesta_llm("esto no es json") is None
    assert parsear_respuesta_llm("{total_imponible: 1}") is None
    assert parsear_respuesta_llm("") is None


def test_acepta_json_en_fence_markdown():
    contenido = (
        "Aquí va el resultado:\n"
        "```json\n"
        '{"total_imponible": 2000000, "descuento_afp": 200000, '
        '"descuento_cesantia": 12000, "sueldo_bruto": 2100000}\n'
        "```"
    )
    datos = parsear_respuesta_llm(contenido)
    assert datos is not None
    assert datos["total_imponible"] == 2_000_000.0


def test_valores_no_positivos_pasan_a_null():
    datos = validar_extraccion(
        {
            "total_imponible": 0,
            "descuento_afp": -100,
            "descuento_cesantia": "abc",
            "sueldo_bruto": 1_000_000,
        }
    )
    assert datos == {
        "total_imponible": None,
        "descuento_afp": None,
        "descuento_cesantia": None,
        "sueldo_bruto": 1_000_000.0,
    }
