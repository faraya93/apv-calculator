"""Respaldo por LLM (xAI Grok) cuando el parser determinista no encuentra montos.

Nunca inventa cifras: si el modelo no las ve en el texto, debe devolver null.
Si falta la API key, hay timeout o la respuesta no valida, se retorna None
y la UI sigue mostrando los avisos de "no encontrado".
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

# Un cambio de línea para actualizar el modelo.
# grok-4.7: más barato y rápido entre los modelos de texto de uso general
# (docs.x.ai/docs/models).
MODEL = "grok-4.7"

_XAI_BASE_URL = "https://api.x.ai/v1"
_TIMEOUT_S = 120.0

_CLAVES = (
    "total_imponible",
    "descuento_afp",
    "descuento_cesantia",
    "sueldo_bruto",
)

_SYSTEM = """Eres un extractor de montos de liquidaciones de sueldo chilenas.
Devuelve ÚNICAMENTE un JSON válido (sin markdown ni texto extra) con exactamente
estas claves: total_imponible, descuento_afp, descuento_cesantia, sueldo_bruto.
Usa null cuando no encuentres el valor en el texto. No inventes ni estimes números.
Los montos deben ser números en pesos chilenos (sin puntos de miles ni símbolos).
total_imponible es la base imponible / total haberes imponibles (no el sueldo bruto).
descuento_afp es la cotización AFP a cargo del trabajador (no el SIS del empleador).
descuento_cesantia es el seguro de cesantía a cargo del trabajador (no el del empleador).
sueldo_bruto es el total de haberes o sueldo bruto si aparece; si no, null."""


def _api_key() -> str | None:
    """XAI_API_KEY del entorno o de st.secrets. Nunca hardcodear."""
    env = os.environ.get("XAI_API_KEY", "").strip()
    if env:
        return env
    try:
        import streamlit as st

        secreto = st.secrets["XAI_API_KEY"]
    except Exception:  # noqa: BLE001 — sin key o sin Streamlit → None
        return None
    if secreto is None:
        return None
    texto = str(secreto).strip()
    return texto or None


def _a_positivo_o_null(valor: Any) -> float | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if numero != numero or numero in (float("inf"), float("-inf")):  # NaN / inf
        return None
    if numero <= 0:
        return None
    return numero


def validar_extraccion(datos: dict[str, Any]) -> dict[str, float | None] | None:
    """Valida el dict del modelo. Rechaza (None) si hay inconsistencias."""
    if not isinstance(datos, dict):
        return None

    limpio: dict[str, float | None] = {}
    for clave in _CLAVES:
        limpio[clave] = _a_positivo_o_null(datos.get(clave))

    imponible = limpio["total_imponible"]
    bruto = limpio["sueldo_bruto"]
    if imponible is not None and bruto is not None and imponible > bruto:
        return None

    return limpio


def _extraer_json(texto: str) -> Any | None:
    """Parsea JSON puro o envuelto en fences markdown."""
    crudo = (texto or "").strip()
    if not crudo:
        return None

    candidatos = [crudo]
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", crudo, re.IGNORECASE)
    if fence:
        candidatos.insert(0, fence.group(1).strip())

    # Objeto JSON embebido en prosa.
    inicio = crudo.find("{")
    fin = crudo.rfind("}")
    if inicio != -1 and fin > inicio:
        candidatos.append(crudo[inicio : fin + 1])

    vistos: set[str] = set()
    for candidato in candidatos:
        if not candidato or candidato in vistos:
            continue
        vistos.add(candidato)
        try:
            return json.loads(candidato)
        except json.JSONDecodeError:
            continue
    return None


def parsear_respuesta_llm(contenido: str) -> dict[str, float | None] | None:
    """JSON + validación. Usado por extract_with_llm y por los tests."""
    parseado = _extraer_json(contenido)
    if parseado is None:
        return None
    return validar_extraccion(parseado)


def extract_with_llm(pdf_text: str) -> dict[str, float | None] | None:
    """Llama a Grok y devuelve los montos, o None si no hay key / falla / no valida."""
    texto = (pdf_text or "").strip()
    texto = texto[:15000]  # acota el input: una liquidación cabe de sobra
    if not texto:
        return None

    key = _api_key()
    if not key:
        return None

    try:
        from openai import OpenAI

        cliente = OpenAI(api_key=key, base_url=_XAI_BASE_URL, timeout=_TIMEOUT_S)
        respuesta = cliente.chat.completions.create(
            model=MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "Extrae los montos de esta liquidación de sueldo:\n\n"
                        f"{texto}"
                    ),
                },
            ],
        )
        contenido = (respuesta.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[llm_extract] llamada a Grok falló: {type(exc).__name__}: {exc}")
        return None

    return parsear_respuesta_llm(contenido)

_SISTEMA_RECOMENDACION = """Eres un asesor previsional-tributario chileno explicándole
a un trabajador dependiente. Te entregaré sus cifras ya calculadas (sueldo, arriendos,
APV óptimo, ahorros). Redacta una recomendación final breve (máx 150 palabras) en
español, en segunda persona, concreta: cuánto aportar al APV-B al mes, por qué, y qué
revisar antes de decidir. Basa TODO lo numérico solo en las cifras entregadas; no
inventes montos ni tasas. Cierra con: "Esto es una orientación, no asesoría tributaria"."""


def generar_recomendacion(contexto: str) -> str | None:
    """Un disparo LLM que redacta la recomendación final en prosa (sin chat)."""
    texto = (contexto or "").strip()
    if not texto:
        return None
    key = _api_key()
    if not key:
        return None
    try:
        from openai import OpenAI

        cliente = OpenAI(api_key=key, base_url=_XAI_BASE_URL, timeout=_TIMEOUT_S)
        respuesta = cliente.chat.completions.create(
            model=MODEL,
            temperature=0.3,
            messages=[
                {"role": "system", "content": _SISTEMA_RECOMENDACION},
                {"role": "user", "content": texto},
            ],
        )
        return (respuesta.choices[0].message.content or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        print(f"[llm_extract] recomendación falló: {type(exc).__name__}: {exc}")
        return None
