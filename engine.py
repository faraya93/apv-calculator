"""Motor de cálculo del APV régimen B e Impuesto Único de Segunda Categoría.

Este módulo estima, para un trabajador dependiente chileno, el aporte mensual
de APV-B que minimiza el IUSC sujeto al tope legal. No es asesoría tributaria.

INCERTIDUMBRE (reglas que no pude confirmar al 100% o que el modelo simplifica):
- El IUSC se publica mes a mes en pesos según la UTM vigente. Los *factores*
  (4%–40%) y los cortes en UTM (13,5 / 30 / 50 / 70 / 90 / 120 / 310) son
  estables; los pesos cambian. Aquí se congela la UTM de octubre 2026
  (Circular SII N° 37/2026: UTM $72.151).
- El tope mensual de 50 UF usa la UF del último día del mes del descuento;
  el tope anual de 600 UF usa la UF al 31 de diciembre (art. 42 bis LIR).
  Al usar una sola UF, 12 × 50 UF = 600 UF, pero en la realidad esas UF
  no valen lo mismo en pesos. No modelamos esa diferencia.
- La comisión AFP varía por administradora (0,46% a 1,45% en oct-2026).
  El default es Habitat 1,27%. Un cambio de ~1 punto altera la RLI y el óptimo.
- No modelamos cotización de Isapre sobre el 7% legal, ni otras rebajas
  (créditos, reliquidación por dos empleadores, zona extrema, etc.).
- El SIS es de cargo del empleador (salvo subsidio trabajadores jóvenes);
  no se descuenta de la RLI. Si el caso es de subsidio, el IUSC estaría
  sobreestimado levemente.
- El APV-B solo difiere el impuesto: al retirar (si no es pensión) se tributa
  de nuevo. Este modelo minimiza el IUSC *corriente*, no el valor presente
  neto del beneficio.
- Enero 2026 tuvo tope imponible 89,9 UF; desde febrero 2026 es 90,0 UF
  (Superintendencia de Pensiones). Usamos 90 UF para un año “típico” 2026.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Parámetros 2026 (fuentes citadas en los comentarios).
# ---------------------------------------------------------------------------

# UTM octubre 2026, Circular SII N° 37 del 11-09-2026.
UTM_OCTUBRE_2026 = 72_151.0

# UF al 15-10-2026, Banco Central / Diario Oficial (período 10-oct a 9-nov).
UF_2026_10_15 = 41_162.73

# Cortes del IUSC en UTM. Coinciden con las tablas mensuales del SII 2026
# (p. ej. https://www.sii.cl/valores_y_fechas/impuesto_2da_categoria/impuesto2026.htm).
# Cada tramo se calcula como: impuesto = RLI × factor − rebaja,
# donde la rebaja acumula el “exceso” de las tasas de tramos anteriores.
IUSC_TRAMOS_UTM: tuple[tuple[float, float, float], ...] = (
    # (hasta_utm, factor, rebaja_utm)
    (13.5, 0.00, 0.00),
    (30.0, 0.04, 0.54),       # 13.5 × 0.04
    (50.0, 0.08, 1.74),       # 0.54 + (30 − 13.5) × 0.04
    (70.0, 0.135, 4.49),      # 1.74 + (50 − 30) × 0.08
    (90.0, 0.23, 11.14),      # 4.49 + (70 − 50) × 0.135
    (120.0, 0.304, 17.80),    # 11.14 + (90 − 70) × 0.23
    (310.0, 0.35, 23.32),     # 17.80 + (120 − 90) × 0.304
    (float("inf"), 0.40, 38.82),  # 23.32 + (310 − 120) × 0.35
)

# Topes APV régimen B / letra b) art. 20 L DL 3.500, vía art. 42 bis LIR.
TOPE_APV_MENSUAL_UF = 50.0
TOPE_APV_ANUAL_UF = 600.0

# Topes de renta imponible previsional 2026 (desde remuneraciones de febrero).
TOPE_AFP_SALUD_UF = 90.0
TOPE_CESANTIA_UF = 135.2

# Cotizaciones de cargo del trabajador. AFP: 10% legal + comisión.
TASA_AFP_CUENTA = 0.10
TASA_SALUD = 0.07
# INCERTIDUMBRE: 0,6% aplica a contrato indefinido. Plazo fijo: el trabajador
# no cotiza cesantía (solo el empleador 3%). Default = indefinido.
TASA_CESANTIA_TRABAJADOR = 0.006
COMISION_AFP_HABITAT = 0.0127

MESES_ANIO = 12


@dataclass(frozen=True)
class Parametros:
    """Parámetros económicos que el usuario puede ajustar sin cambiar la ley."""

    utm: float = UTM_OCTUBRE_2026
    uf: float = UF_2026_10_15
    comision_afp: float = COMISION_AFP_HABITAT
    contrato_indefinido: bool = True


@dataclass(frozen=True)
class Resultado:
    sueldo_bruto: float
    apv_actual: float
    apv_optimo: float
    rli_sin_apv: float
    rli_actual: float
    rli_optima: float
    iusc_mensual_actual: float
    iusc_mensual_optimo: float
    iusc_anual_actual: float
    iusc_anual_optimo: float
    ahorro_anual: float
    tope_mensual_pesos: float
    tope_anual_pesos: float
    tasa_marginal_actual: float
    ya_en_optimo: bool
    motivo_optimo: str
    parametros: Parametros


def tope_mensual_pesos(p: Parametros) -> float:
    """50 UF. Con UF constante, 12 × 50 UF = 600 UF, así que el tope anual
    no aprieta más que el mensual si el aporte es uniforme todo el año."""
    return TOPE_APV_MENSUAL_UF * p.uf


def tope_anual_pesos(p: Parametros) -> float:
    return TOPE_APV_ANUAL_UF * p.uf


def cotizaciones_trabajador(sueldo_bruto: float, p: Parametros) -> float:
    """Descuentos legales que bajan la base del IUSC.

    SUPUESTO: el sueldo bruto ingresado es 100% imponible (sin asignaciones
    no imponibles ni horas extra/bonos variables).
    AFP y salud se calculan sobre min(bruto, 90 UF); cesantía sobre
    min(bruto, 135,2 UF). El APV-B NO reduce la base previsional: se
    descuenta después, solo para el IUSC.
    """
    if sueldo_bruto <= 0:
        return 0.0
    base_afp_salud = min(sueldo_bruto, TOPE_AFP_SALUD_UF * p.uf)
    base_cesantia = min(sueldo_bruto, TOPE_CESANTIA_UF * p.uf)
    afp = base_afp_salud * (TASA_AFP_CUENTA + p.comision_afp)
    salud = base_afp_salud * TASA_SALUD
    cesantia = base_cesantia * TASA_CESANTIA_TRABAJADOR if p.contrato_indefinido else 0.0
    return afp + salud + cesantia


def renta_liquida_sin_apv(sueldo_bruto: float, p: Parametros) -> float:
    return max(0.0, sueldo_bruto - cotizaciones_trabajador(sueldo_bruto, p))


def apv_deducible(apv_mensual: float, p: Parametros) -> float:
    """Solo el APV-B descontado por el empleador, hasta 50 UF, rebaja el IUSC
    del mismo mes (art. 42 bis N° 1 LIR). Aportes sobre el tope no rebajan."""
    if apv_mensual <= 0:
        return 0.0
    return min(apv_mensual, tope_mensual_pesos(p))


def renta_liquida_imponible(sueldo_bruto: float, apv_mensual: float, p: Parametros) -> float:
    rli = renta_liquida_sin_apv(sueldo_bruto, p) - apv_deducible(apv_mensual, p)
    # INCERTIDUMBRE: la ley no permite base negativa; el exceso de APV sobre
    # la RLI no genera crédito, solo queda sin efecto tributario este mes.
    return max(0.0, rli)


def iusc_mensual(rli: float, p: Parametros) -> float:
    """Impuesto único mensual: RLI × factor − cantidad a rebajar.

    Verificado contra la tabla SII de octubre 2026:
    tramo 13,5–30 UTM, factor 0,04, rebaja $38.961,54 = 0,54 × $72.151.
    """
    if rli <= 0:
        return 0.0
    rli_utm = rli / p.utm
    factor = 0.0
    rebaja_utm = 0.0
    for hasta, fac, reb in IUSC_TRAMOS_UTM:
        factor, rebaja_utm = fac, reb
        if rli_utm <= hasta:
            break
    impuesto = rli * factor - rebaja_utm * p.utm
    return max(0.0, impuesto)


def tasa_marginal(rli: float, p: Parametros) -> float:
    rli_utm = rli / p.utm
    for hasta, fac, _ in IUSC_TRAMOS_UTM:
        if rli_utm <= hasta:
            return fac
    return 0.40


def apv_optimo_mensual(sueldo_bruto: float, p: Parametros) -> tuple[float, str]:
    """Menor aporte mensual de APV-B que alcanza el IUSC mínimo posible.

    Como el IUSC es no decreciente en la RLI, conviene rebajar la base hasta
    el tramo exento (13,5 UTM) o hasta el tope de 50 UF, lo que ocurra primero.
    Aportar más allá del tramo exento no reduce más impuesto (ya es $0).
    """
    rli0 = renta_liquida_sin_apv(sueldo_bruto, p)
    if rli0 <= 0:
        return 0.0, "Sin renta líquida imponible no hay IUSC ni beneficio APV-B."

    exento = 13.5 * p.utm
    tope = tope_mensual_pesos(p)
    # Lo que falta para caer al tramo exento. Si ya está exento, óptimo = 0.
    para_exento = max(0.0, rli0 - exento)
    optimo = min(tope, para_exento, rli0)

    if optimo <= 0:
        return 0.0, "La renta líquida ya está en el tramo exento (hasta 13,5 UTM)."
    if optimo >= tope - 0.5 and para_exento > tope:
        return tope, (
            "El tope legal de 50 UF mensuales (600 UF al año si el UF es constante) "
            "se agota antes de llegar al tramo exento. Ese es el máximo ahorro posible."
        )
    return optimo, (
        "Con este aporte la renta líquida queda en el tramo exento (13,5 UTM) "
        "y el IUSC mensual llega a $0. Aportar más no reduce más impuesto."
    )


def simular(sueldo_bruto: float, apv_actual: float, p: Parametros | None = None) -> Resultado:
    p = p or Parametros()
    apv_actual = max(0.0, apv_actual)
    optimo, motivo = apv_optimo_mensual(sueldo_bruto, p)

    rli0 = renta_liquida_sin_apv(sueldo_bruto, p)
    rli_act = renta_liquida_imponible(sueldo_bruto, apv_actual, p)
    rli_opt = renta_liquida_imponible(sueldo_bruto, optimo, p)

    iusc_act = iusc_mensual(rli_act, p)
    iusc_opt = iusc_mensual(rli_opt, p)

    return Resultado(
        sueldo_bruto=sueldo_bruto,
        apv_actual=apv_actual,
        apv_optimo=optimo,
        rli_sin_apv=rli0,
        rli_actual=rli_act,
        rli_optima=rli_opt,
        iusc_mensual_actual=iusc_act,
        iusc_mensual_optimo=iusc_opt,
        iusc_anual_actual=iusc_act * MESES_ANIO,
        iusc_anual_optimo=iusc_opt * MESES_ANIO,
        ahorro_anual=(iusc_act - iusc_opt) * MESES_ANIO,
        tope_mensual_pesos=tope_mensual_pesos(p),
        tope_anual_pesos=tope_anual_pesos(p),
        tasa_marginal_actual=tasa_marginal(rli_act, p),
        ya_en_optimo=abs(apv_actual - optimo) < 1.0,
        motivo_optimo=motivo,
        parametros=p,
    )


def curva_iusc_anual(sueldo_bruto: float, p: Parametros, puntos: int = 81) -> list[tuple[float, float]]:
    """Pares (APV mensual, IUSC anual) desde $0 hasta el tope de 50 UF."""
    tope = tope_mensual_pesos(p)
    if puntos < 2:
        puntos = 2
    paso = tope / (puntos - 1)
    serie = []
    for i in range(puntos):
        apv = i * paso
        rli = renta_liquida_imponible(sueldo_bruto, apv, p)
        serie.append((apv, iusc_mensual(rli, p) * MESES_ANIO))
    return serie
