"""Asistente conversacional del APV-B (chat con herramientas).

El modelo conversa, pero NO calcula: cada cifra sale de engine.py a través de
las herramientas (function calling). Su misión no es empujar el óptimo
tributario (a menudo el tope legal), sino ayudar a encontrar un equilibrio
entre ahorro de impuestos y liquidez mensual.
"""

from __future__ import annotations

import json
import math
from typing import Any

from engine import IUSC_TRAMOS_UTM, Parametros, renta_liquida_sin_apv, simular

import llm_extract

_SISTEMA = """Eres el asistente de un simulador tributario chileno de APV-B.
Tu misión: ayudar al usuario a encontrar un EQUILIBRIO entre ahorro de impuestos
y liquidez mensual. El aporte que minimiza el impuesto suele ser el máximo
legal, pero eso deja poco efectivo en el mes; un aporte menor puede capturar
gran parte del ahorro (por ejemplo, el mínimo para bajar de tramo).

REGLAS:
- Para CUALQUIER cifra usa las herramientas; nunca calcules ni inventes números.
- Si preguntan por un monto concreto, llama a simular_aporte.
- Si preguntan por "equilibrio", "bajar de tramo" o montos "razonables", llama
  a puntos_dulces y presenta 2 o 3 opciones: aporte mensual, ahorro anual y
  ahorro por peso aportado, para que el usuario elija según su liquidez.
- Responde en español, segunda persona, breve (máx ~150 palabras), con los
  montos en pesos chilenos.
- No des asesoría tributaria ni de inversión: explica lo que muestran los cálculos.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "simular_aporte",
            "description": (
                "Simula un aporte mensual de APV-B: devuelve el IUSC mensual y "
                "anual resultante, el ahorro anual frente a no aportar nada y "
                "la tasa marginal (tramo) resultante."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "aporte_mensual": {
                        "type": "integer",
                        "description": "Aporte mensual de APV-B en pesos chilenos.",
                    }
                },
                "required": ["aporte_mensual"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "puntos_dulces",
            "description": (
                "Lista los aportes 'punto dulce': el aporte mínimo para que la "
                "renta líquida quede justo en cada corte de tramo menor al "
                "actual, con su impuesto, su ahorro anual y el ahorro por peso "
                "aportado. Úsala para hablar de equilibrio impuesto/liquidez."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resumen_actual",
            "description": (
                "Resumen base del usuario: sueldo, renta líquida sin APV, tasa "
                "marginal actual, IUSC sin APV, aporte óptimo tributario (el "
                "que minimiza el impuesto), tope legal mensual y ahorro máximo."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _parametros(ctx: dict[str, Any]) -> Parametros:
    base = Parametros()

    def _f(clave: str, defecto: float) -> float:
        valor = ctx.get(clave)
        return defecto if valor is None else float(valor)

    return Parametros(
        utm=_f("utm", base.utm),
        uf=_f("uf", base.uf),
        comision_afp=_f("comision_afp", base.comision_afp),
        contrato_indefinido=bool(ctx.get("contrato_indefinido", base.contrato_indefinido)),
    )


def _entero(x: float) -> int:
    return int(round(x))


def _simular_aporte(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    p = _parametros(ctx)
    sueldo = float(ctx["sueldo_bruto"])
    try:
        aporte = max(0, int(float(args.get("aporte_mensual") or 0)))
    except (TypeError, ValueError):
        aporte = 0
    res = simular(sueldo, float(aporte), p)
    sin = simular(sueldo, 0.0, p)
    return {
        "aporte_mensual": aporte,
        "iusc_mensual": _entero(res.iusc_mensual_actual),
        "iusc_anual": _entero(res.iusc_anual_actual),
        "ahorro_anual_vs_no_aportar": _entero(
            sin.iusc_anual_actual - res.iusc_anual_actual
        ),
        "tasa_marginal_pct": res.tasa_marginal_actual * 100,
        "renta_liquida": _entero(res.rli_actual),
    }


def _puntos_dulces(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    p = _parametros(ctx)
    sueldo = float(ctx["sueldo_bruto"])
    rli0 = renta_liquida_sin_apv(sueldo, p)
    sin = simular(sueldo, 0.0, p)
    tope = sin.tope_mensual_pesos
    puntos: list[dict[str, Any]] = []
    for hasta_utm, _factor, _rebaja in IUSC_TRAMOS_UTM:
        if not math.isfinite(hasta_utm):
            continue
        umbral = hasta_utm * p.utm
        if rli0 <= umbral:
            continue
        aporte = rli0 - umbral
        if aporte > tope:
            continue
        # Redondeo hacia arriba al mil más cercano: la RLI queda bajo el corte.
        aporte = float(math.ceil(aporte / 1000.0) * 1000)
        res = simular(sueldo, aporte, p)
        ahorro = sin.iusc_anual_actual - res.iusc_anual_actual
        puntos.append(
            {
                "aporte_mensual": int(aporte),
                "renta_liquida_queda_en_utm": hasta_utm,
                "tasa_marginal_pct": res.tasa_marginal_actual * 100,
                "iusc_anual": _entero(res.iusc_anual_actual),
                "ahorro_anual_vs_no_aportar": _entero(ahorro),
                "ahorro_por_peso_aportado": round(ahorro / (aporte * 12), 3)
                if aporte > 0
                else 0.0,
            }
        )
    puntos.sort(key=lambda d: d["aporte_mensual"])
    return {"puntos_dulces": puntos, "tope_mensual": _entero(tope)}


def _resumen(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    p = _parametros(ctx)
    sueldo = float(ctx["sueldo_bruto"])
    sin = simular(sueldo, 0.0, p)
    return {
        "sueldo_bruto": _entero(sueldo),
        "renta_liquida_sin_apv": _entero(sin.rli_sin_apv),
        "tasa_marginal_actual_pct": sin.tasa_marginal_actual * 100,
        "iusc_anual_sin_apv": _entero(sin.iusc_anual_actual),
        "apv_optimo_tributario": _entero(sin.apv_optimo),
        "tope_mensual_legal": _entero(sin.tope_mensual_pesos),
        "ahorro_anual_maximo": _entero(sin.ahorro_anual),
        "motivo_optimo": sin.motivo_optimo,
    }


_EJECUTORES = {
    "simular_aporte": _simular_aporte,
    "puntos_dulces": _puntos_dulces,
    "resumen_actual": _resumen,
}


def ejecutar(nombre: str, args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta una herramienta por nombre. Nunca lanza: devuelve error en dict."""
    funcion = _EJECUTORES.get(nombre)
    if funcion is None:
        return {"error": f"herramienta desconocida: {nombre}"}
    try:
        return funcion(args or {}, ctx)
    except Exception as exc:  # noqa: BLE001 — la herramienta no debe botar el chat
        return {"error": f"{type(exc).__name__}: {exc}"}


def responder(historial: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    """Un turno de chat: llama al modelo y ejecuta sus herramientas hasta
    obtener la respuesta final en texto."""
    cliente, _modelo = llm_extract._cliente_y_modelo()
    if cliente is None:
        return (
            "El asistente no está disponible: falta la API key de xAI o "
            "Ollama no está corriendo."
        )
    modelo = _modelo
    mensajes: list[Any] = [{"role": "system", "content": _SISTEMA}, *historial]
    for _ in range(6):
        try:
            respuesta = cliente.chat.completions.create(
                model=modelo,
                temperature=0.2,
                messages=mensajes,
                tools=TOOLS,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[asistente] llamada falló: {type(exc).__name__}: {exc}")
            return "No pude consultar el modelo. Revisa la terminal para el detalle."
        mensaje = respuesta.choices[0].message
        if not getattr(mensaje, "tool_calls", None):
            return (mensaje.content or "").strip() or "No tengo una respuesta para eso."
        mensajes.append(mensaje)
        for llamada in mensaje.tool_calls:
            try:
                args = json.loads(llamada.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            resultado = ejecutar(llamada.function.name, args, ctx)
            mensajes.append(
                {
                    "role": "tool",
                    "tool_call_id": llamada.id,
                    "content": json.dumps(resultado, ensure_ascii=False),
                }
            )
    return "La consulta tomó demasiados pasos. Reformula la pregunta, por favor."
