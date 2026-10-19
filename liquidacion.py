"""Lectura de una liquidación de sueldo en PDF.

Los formatos cambian según el empleador. Esta lectura es una ayuda: la
interfaz siempre muestra los montos para corregirlos y no los usa hasta
que la persona los confirma.
"""

from __future__ import annotations

import io
import re
import unicodedata
from typing import Callable

# Miles con punto (2.500.000) o un entero de al menos 4 dígitos (15000).
_MONTO = re.compile(r"(?<![\d.])(?:\$\s*)?(\d{1,3}(?:\.\d{3})+|\d{4,})(?!\d)")


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar etiquetas."""
    base = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in base if unicodedata.category(c) != "Mn")


def _preparar_linea(linea: str) -> str:
    """Unifica miles separados por espacio y saca RUT, que parecen montos."""
    linea = re.sub(r"(?<=\d)\s+(?=\d{3}(?!\d))", ".", linea)
    linea = re.sub(r"\b\d{1,2}\.\d{3}\.\d{3}-[\dkK]\b", " ", linea)
    linea = re.sub(r"\b\d{7,9}-[\dkK]\b", " ", linea)
    return linea


def montos_en(linea: str) -> list[float]:
    """Montos en pesos de una línea. Ignora años y porcentajes chicos."""
    encontrados: list[float] = []
    for coincidencia in _MONTO.finditer(_preparar_linea(linea)):
        texto = coincidencia.group(1)
        valor = float(texto.replace(".", ""))
        if "." not in texto and 1900 <= valor <= 2100:
            continue
        if valor >= 1000:
            encontrados.append(valor)
    return encontrados


def _monto_concepto(lineas: list[str], indice: int) -> float | None:
    """Último monto de la línea, o el de la línea siguiente si viene solo."""
    propios = montos_en(lineas[indice])
    if propios:
        return propios[-1]
    if indice + 1 >= len(lineas):
        return None
    siguiente = lineas[indice + 1]
    if _normalizar(siguiente).strip() == "" or montos_en(siguiente) == []:
        return None
    # No robar el monto si la línea siguiente es otro concepto con texto.
    resto = _MONTO.sub(" ", _preparar_linea(siguiente))
    if re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{3,}", resto):
        return None
    siguientes = montos_en(siguiente)
    return siguientes[-1] if siguientes else None


def _recolectar(
    lineas: list[str],
    normas: list[str],
    coincide: Callable[[str], bool],
) -> list[float]:
    montos: list[float] = []
    for indice, norma in enumerate(normas):
        if not coincide(norma):
            continue
        monto = _monto_concepto(lineas, indice)
        if monto is not None:
            montos.append(monto)
    return montos


def _imponible(lineas: list[str], normas: list[str]) -> float | None:
    """Prefiere el total imponible. El sueldo base solo se usa si no hay total."""
    candidatos: list[tuple[int, float]] = []
    for indice, norma in enumerate(normas):
        if "no imponible" in norma:
            continue
        if "total haberes imponibles" in norma or "total imponible" in norma:
            puntaje = 0
        elif "haberes imponibles" in norma or "base imponible" in norma or "renta imponible" in norma:
            puntaje = 1
        elif "sueldo imponible" in norma:
            puntaje = 2
        elif "sueldo base" in norma:
            puntaje = 5
        else:
            continue
        monto = _monto_concepto(lineas, indice)
        if monto is not None:
            candidatos.append((puntaje, monto))
    if not candidatos:
        return None
    mejor = min(puntaje for puntaje, _ in candidatos)
    # Si hay varios totales, el último suele ser el consolidado.
    return [monto for puntaje, monto in candidatos if puntaje == mejor][-1]


def _suma(montos: list[float]) -> float | None:
    if not montos:
        return None
    return float(sum(montos))


def _es_afp(norma: str) -> bool:
    if "empleador" in norma or re.search(r"\bsis\b", norma):
        return False
    return bool(re.search(r"\bafp\b", norma) or "fondo de pensiones" in norma)


def _es_salud(norma: str) -> bool:
    if "empleador" in norma:
        return False
    return any(clave in norma for clave in ("fonasa", "isapre", "cotizacion de salud", "cotizacion salud", "salud"))


def _es_cesantia(norma: str) -> bool:
    if "empleador" in norma:
        return False
    return any(clave in norma for clave in ("cesantia", "afc"))


def _es_impuesto(norma: str) -> bool:
    if "impuesto unico" in norma or "imp unico" in norma or re.search(r"\biusc\b", norma):
        return True
    return "impuesto" in norma and ("segunda" in norma or "2da" in norma or "2a cat" in norma)


def parsear_texto(texto: str) -> dict[str, object]:
    """Extrae montos desde el texto de una liquidación. Los faltantes quedan en None."""
    avisos: list[str] = []
    crudo = texto or ""
    if not crudo.strip():
        avisos.append(
            "No hay texto seleccionable en el PDF. Si es un escaneo, escribe los montos a mano."
        )
        return _vacio(avisos, crudo)

    lineas = [ln.strip() for ln in crudo.splitlines() if ln.strip()]
    normas = [_normalizar(ln) for ln in lineas]

    imponible = _imponible(lineas, normas)
    colacion = _suma(_recolectar(lineas, normas, lambda n: "colacion" in n))
    movilizacion = _suma(_recolectar(lineas, normas, lambda n: "movilizacion" in n))
    afp = _suma(_recolectar(lineas, normas, _es_afp))

    salud_esp = _recolectar(
        lineas, normas, lambda n: _es_salud(n) and ("fonasa" in n or "isapre" in n)
    )
    salud = _suma(salud_esp) if salud_esp else _suma(_recolectar(lineas, normas, _es_salud))

    cesantia_trab = _recolectar(
        lineas, normas, lambda n: _es_cesantia(n) and "trabajador" in n
    )
    cesantia = (
        _suma(cesantia_trab) if cesantia_trab else _suma(_recolectar(lineas, normas, _es_cesantia))
    )
    impuesto = _suma(_recolectar(lineas, normas, _es_impuesto))

    if imponible is None:
        avisos.append("No encontré el total imponible. Complétalo antes de confirmar.")
    if afp is None:
        avisos.append("No encontré el descuento de AFP.")
    if salud is None:
        avisos.append("No encontré el descuento de salud.")
    if cesantia is None:
        avisos.append("No encontré el descuento de cesantía.")
    if impuesto is None:
        avisos.append("No encontré el impuesto único.")

    return {
        "imponible": imponible,
        "colacion": colacion,
        "movilizacion": movilizacion,
        "afp": afp,
        "salud": salud,
        "cesantia": cesantia,
        "impuesto_unico": impuesto,
        "avisos": avisos,
        "texto": crudo,
    }


def _vacio(avisos: list[str], texto: str) -> dict[str, object]:
    return {
        "imponible": None,
        "colacion": None,
        "movilizacion": None,
        "afp": None,
        "salud": None,
        "cesantia": None,
        "impuesto_unico": None,
        "avisos": avisos,
        "texto": texto,
    }


def extraer_texto_pdf(contenido: bytes) -> str:
    """Texto del PDF. Primero pdfplumber; si falla o viene vacío, pypdf."""
    errores: list[str] = []
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            texto = "\n".join((pagina.extract_text() or "") for pagina in pdf.pages)
        if texto.strip():
            return texto
        errores.append("pdfplumber no encontró texto")
    except Exception as exc:  # noqa: BLE001 — el respaldo es pypdf
        errores.append(f"pdfplumber: {exc}")

    try:
        from pypdf import PdfReader

        lector = PdfReader(io.BytesIO(contenido))
        return "\n".join((pagina.extract_text() or "") for pagina in lector.pages)
    except Exception as exc:  # noqa: BLE001 — se informa un solo error a la UI
        detalle = "; ".join(errores) if errores else "sin intento previo"
        raise ValueError(f"No se pudo leer el PDF ({detalle}).") from exc


def leer_liquidacion(contenido: bytes) -> dict[str, object]:
    """Lee el PDF y devuelve los montos encontrados, sin aplicarlos al cálculo."""
    return parsear_texto(extraer_texto_pdf(contenido))
