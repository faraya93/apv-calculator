"""Calculadora Streamlit de APV régimen B — interfaz en español.

Ejecutar: streamlit run app.py
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from engine import (
    COMISION_AFP_HABITAT,
    Parametros,
    curva_iusc_anual,
    simular,
)

st.set_page_config(
    page_title="Calculadora APV-B",
    page_icon="🇨🇱",
    layout="centered",
)

st.markdown(
    """
    <style>
      .stApp { background: #f6f3ee; }
      h1 { letter-spacing: -0.02em; }
      div[data-testid="stMetric"] {
        background: #fff;
        border: 1px solid #e4ddd3;
        border-radius: 12px;
        padding: 0.75rem 1rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def clp(valor: float) -> str:
    """Formato chileno: $1.234.567."""
    signo = "-" if valor < 0 else ""
    entero = f"{abs(valor):,.0f}".replace(",", ".")
    return f"{signo}${entero}"


def clp_md(valor: float) -> str:
    """Montos para Markdown de Streamlit: un `$` suelto abre LaTeX."""
    return clp(valor).replace("$", r"\$")


st.title("Calculadora de APV régimen B")
st.caption(
    "Estima el aporte mensual de APV-B que minimiza el Impuesto Único de "
    "Segunda Categoría de un trabajador dependiente. Reglas tributarias 2026 "
    "(tramos IUSC en UTM y tope de 600 UF anuales). Es una simulación, no "
    "asesoría tributaria."
)

with st.sidebar:
    st.header("Datos del trabajador")
    sueldo = st.number_input(
        "Sueldo bruto mensual (CLP)",
        min_value=0,
        value=2_500_000,
        step=50_000,
        help="Remuneración imponible mensual. Se asume 100% imponible.",
    )
    apv_actual = st.number_input(
        "APV-B mensual actual (CLP)",
        min_value=0,
        value=0,
        step=10_000,
        help="Aporte actual descontado por el empleador, acogido al régimen B.",
    )
    st.divider()
    st.header("Supuestos 2026")
    st.caption(
        "Valores de referencia de octubre 2026. Ajústalos si tu liquidación "
        "usa otra UF, UTM o AFP."
    )
    uf = st.number_input("Valor UF (CLP)", min_value=1.0, value=41_162.73, step=1.0, format="%.2f")
    utm = st.number_input("Valor UTM (CLP)", min_value=1.0, value=72_151.0, step=1.0, format="%.0f")
    comision = st.selectbox(
        "Comisión AFP (sobre renta imponible)",
        options=[
            ("Habitat 1,27%", 0.0127),
            ("Uno 0,46%", 0.0046),
            ("Modelo 0,58%", 0.0058),
            ("Planvital 1,16%", 0.0116),
            ("Capital / Cuprum 1,44%", 0.0144),
            ("Provida 1,45%", 0.0145),
        ],
        index=0,
        format_func=lambda x: x[0],
    )[1]
    indefinido = st.checkbox("Contrato indefinido (cesantía 0,6% a cargo del trabajador)", value=True)

params = Parametros(utm=utm, uf=uf, comision_afp=comision, contrato_indefinido=indefinido)
res = simular(float(sueldo), float(apv_actual), params)

col1, col2, col3 = st.columns(3)
col1.metric("APV-B mensual óptimo", clp(res.apv_optimo))
col2.metric(
    "Ahorro tributario anual estimado",
    clp(res.ahorro_anual),
    help="Diferencia de IUSC anual entre tu aporte actual y el óptimo. "
    "Si ya aportas el óptimo (o más de lo que rebaja impuesto), el ahorro extra es $0.",
)
col3.metric("IUSC anual con el óptimo", clp(res.iusc_anual_optimo))

st.info(res.motivo_optimo)

if res.ya_en_optimo:
    st.success("Tu aporte actual ya coincide con el óptimo para minimizar el IUSC.")
elif res.apv_actual > res.apv_optimo + 1:
    st.warning(
        "Aportas más que el óptimo tributario. El exceso sobre el tope de 50 UF "
        "o sobre el tramo exento no reduce más el IUSC de este mes "
        "(sí sigue siendo ahorro previsional)."
    )
elif res.apv_actual < res.apv_optimo - 1:
    extra = res.apv_optimo - res.apv_actual
    st.markdown(
        f"Si subes el APV-B en **{clp_md(extra)}** al mes, el IUSC anual pasaría de "
        f"**{clp_md(res.iusc_anual_actual)}** a **{clp_md(res.iusc_anual_optimo)}**."
    )

st.subheader("Impuesto único anual según el APV-B mensual")
datos = curva_iusc_anual(float(sueldo), params)
df = pd.DataFrame(datos, columns=["apv_mensual", "iusc_anual"])

puntos = pd.DataFrame(
    [
        {"apv_mensual": res.apv_actual, "iusc_anual": res.iusc_anual_actual, "etiqueta": "Actual"},
        {"apv_mensual": res.apv_optimo, "iusc_anual": res.iusc_anual_optimo, "etiqueta": "Óptimo"},
    ]
)

linea = (
    alt.Chart(df)
    .mark_line(color="#1f4e3d", strokeWidth=3)
    .encode(
        x=alt.X("apv_mensual:Q", title="APV-B mensual (CLP)", axis=alt.Axis(format="$,.0f")),
        y=alt.Y("iusc_anual:Q", title="IUSC anual estimado (CLP)", axis=alt.Axis(format="$,.0f")),
        tooltip=[
            alt.Tooltip("apv_mensual:Q", title="APV mensual", format="$,.0f"),
            alt.Tooltip("iusc_anual:Q", title="IUSC anual", format="$,.0f"),
        ],
    )
)
marcas = (
    alt.Chart(puntos)
    .mark_point(size=120, filled=True)
    .encode(
        x="apv_mensual:Q",
        y="iusc_anual:Q",
        color=alt.Color(
            "etiqueta:N",
            scale=alt.Scale(domain=["Actual", "Óptimo"], range=["#c45c26", "#1f4e3d"]),
            legend=alt.Legend(title=""),
        ),
        tooltip=["etiqueta:N", alt.Tooltip("apv_mensual:Q", format="$,.0f")],
    )
)
etiquetas = (
    alt.Chart(puntos)
    .mark_text(dy=-12, fontWeight="bold")
    .encode(x="apv_mensual:Q", y="iusc_anual:Q", text="etiqueta:N")
)
st.altair_chart(linea + marcas + etiquetas, use_container_width=True)

with st.expander("Detalle del cálculo"):
    st.markdown(
        f"""
| Concepto | Monto |
| --- | ---: |
| Sueldo bruto mensual | {clp_md(res.sueldo_bruto)} |
| Renta líquida *antes* de APV | {clp_md(res.rli_sin_apv)} |
| RLI con APV actual | {clp_md(res.rli_actual)} |
| RLI con APV óptimo | {clp_md(res.rli_optima)} |
| Tasa marginal actual | {res.tasa_marginal_actual:.1%} |
| IUSC mensual actual | {clp_md(res.iusc_mensual_actual)} |
| IUSC mensual óptimo | {clp_md(res.iusc_mensual_optimo)} |
| Tope APV mensual (50 UF) | {clp_md(res.tope_mensual_pesos)} |
| Tope APV anual (600 UF) | {clp_md(res.tope_anual_pesos)} |
"""
    )

with st.expander("Supuestos y puntos inciertos"):
    st.markdown(
        f"""
**Cómo se arma la base del IUSC**
- Del sueldo bruto se restan cotizaciones de cargo del trabajador: AFP 10% + comisión
  ({COMISION_AFP_HABITAT:.2%} Habitat por defecto), salud 7% y, si el contrato es
  indefinido, cesantía 0,6%.
- AFP y salud se calculan hasta **90 UF**; cesantía hasta **135,2 UF**
  (topes 2026 desde febrero, Superintendencia de Pensiones).
- El APV-B se resta *después*, solo a la renta líquida imponible, no a la
  base previsional (art. 42 bis N° 1 LIR).

**Topes APV-B**
- Mensual: 50 UF si el descuento lo hace el empleador.
- Anual: 600 UF (el remanente se puede usar en la operación renta si aportas
  directo). Con una sola UF, 12 × 50 UF = 600 UF.

**Qué no está modelado (incertidumbre)**
- UF del último día de cada mes vs. UF al 31 de diciembre: los topes en pesos
  no coinciden exactamente en un año real.
- Tablas IUSC distintas cada mes porque cambia la UTM. Aquí se usa la UTM que
  indiques (default octubre 2026, $72.151).
- Plan Isapre sobre 7%, asignaciones no imponibles, bonos, gratificación legal,
  reliquidación por más de un empleador, créditos y zona extrema.
- El APV-B **difiere** el impuesto: al retirar antes de pensionarte vuelves a
  tributar. El “óptimo” de esta app es el que minimiza el IUSC de 2026, no el
  VAN de largo plazo ni la comparación con régimen A (bonificación 15%).
"""
    )
