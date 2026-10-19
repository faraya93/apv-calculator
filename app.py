"""Calculadora Streamlit de APV régimen B — interfaz en español.

Ejecutar: streamlit run app.py
"""

from __future__ import annotations

import hashlib

import altair as alt
import pandas as pd
import streamlit as st

from engine import (
    COMISION_AFP_HABITAT,
    Parametros,
    apv_para_iusc_cero,
    base_anual_operacion_renta,
    curva_iusc_anual,
    global_complementario,
    separar_arriendos,
    simular,
)
from liquidacion import leer_liquidacion
from llm_extract import extract_with_llm, generar_recomendacion

# Campos que el respaldo por IA puede completar (claves locales → clave LLM).
_CAMPOS_LLM = {
    "imponible": "total_imponible",
    "afp": "descuento_afp",
    "cesantia": "descuento_cesantia",
    "impuesto_unico": "impuesto_unico",
}

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
      .st-key-devolucion_sii div[data-testid="stMetric"] {
        background: #e7f5ee;
        border-color: #1f7a4d;
      }
      .st-key-devolucion_sii div[data-testid="stMetricValue"] {
        color: #1f7a4d;
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


def _entero_leido(valor: object) -> int:
    if valor is None:
        return 0
    try:
        return max(0, int(round(float(valor))))
    except (TypeError, ValueError):
        return 0


def _volcar_liquidacion(datos: dict[str, object]) -> None:
    """Copia la lectura a los campos editables. Hay que llamarlo antes de los widgets."""
    st.session_state["liq_imponible"] = _entero_leido(datos.get("imponible"))
    st.session_state["liq_colacion"] = _entero_leido(datos.get("colacion"))
    st.session_state["liq_movilizacion"] = _entero_leido(datos.get("movilizacion"))
    st.session_state["liq_afp"] = _entero_leido(datos.get("afp"))
    st.session_state["liq_salud"] = _entero_leido(datos.get("salud"))
    st.session_state["liq_cesantia"] = _entero_leido(datos.get("cesantia"))
    st.session_state["liq_impuesto"] = _entero_leido(datos.get("impuesto_unico"))


def _asegurar_campos_liquidacion(datos: dict[str, object]) -> None:
    """Si el formulario no estaba en pantalla, recupera la última lectura."""
    if "liq_imponible" not in st.session_state:
        _volcar_liquidacion(datos)


def _avisos_faltantes(datos: dict[str, object]) -> list[str]:
    """Reconstruye los avisos de campos que siguen vacíos tras el respaldo LLM."""
    avisos: list[str] = []
    if not (datos.get("texto") or "").strip():
        return list(datos.get("avisos") or [])
    if datos.get("imponible") is None:
        avisos.append("No encontré el total imponible. Complétalo antes de confirmar.")
    if datos.get("afp") is None:
        avisos.append("No encontré el descuento de AFP.")
    if datos.get("salud") is None:
        avisos.append("No encontré el descuento de salud.")
    if datos.get("cesantia") is None:
        avisos.append("No encontré el descuento de cesantía.")
    if datos.get("impuesto_unico") is None:
        avisos.append("No encontré el impuesto único.")
    return avisos


def _fuentes_desde_parser(datos: dict[str, object]) -> dict[str, str]:
    """Marca como 'pdf' cada monto que encontró el parser determinista."""
    fuentes: dict[str, str] = {}
    for campo in (
        "imponible",
        "colacion",
        "movilizacion",
        "afp",
        "salud",
        "cesantia",
        "impuesto_unico",
    ):
        if datos.get(campo) is not None:
            fuentes[campo] = "pdf"
    return fuentes


def _completar_con_llm(
    datos: dict[str, object], fuentes: dict[str, str]
) -> tuple[dict[str, object], dict[str, str]]:
    """Si faltan imponible/AFP/cesantía, una sola llamada a Grok llena lo que pueda."""
    faltan = any(datos.get(campo) is None for campo in _CAMPOS_LLM)
    texto = (datos.get("texto") or "").strip()
    if not faltan or not texto:
        return datos, fuentes

    llm = extract_with_llm(texto)
    if not llm:
        return datos, fuentes

    for campo, clave_llm in _CAMPOS_LLM.items():
        if datos.get(campo) is not None:
            continue
        valor = llm.get(clave_llm)
        if valor is None:
            continue
        datos[campo] = valor
        fuentes[campo] = "ia"

    datos["avisos"] = _avisos_faltantes(datos)
    return datos, fuentes


def _caption_fuente(fuentes: dict[str, str], campo: str) -> None:
    origen = fuentes.get(campo)
    if origen == "pdf":
        st.caption("leído del PDF")
    elif origen == "ia":
        st.caption("leído por IA")


st.session_state.setdefault("liq_confirmada", False)

st.title("Calculadora de APV régimen B")
st.caption(
    "Estima el aporte mensual de APV-B que minimiza el Impuesto Único de "
    "Segunda Categoría de un trabajador dependiente. Reglas tributarias 2026 "
    "(tramos IUSC en UTM y tope de 600 UF anuales). Es una simulación, no "
    "asesoría tributaria."
)

with st.container(border=True):
    st.subheader("Mis datos desde liquidación")
    st.caption(
        "La base del IUSC es el total imponible, no el sueldo bruto ni el "
        "total de haberes. Sube el PDF, revisa cada monto y confírmalo: "
        "ningún valor leído se usa sin esa confirmación."
    )
    archivo = st.file_uploader(
        "Liquidación de sueldo (PDF)",
        type=["pdf"],
        key="liq_pdf",
        help="Solo se lee en esta sesión para proponerte los montos.",
    )
    st.caption(
        "Si el lector automático falla, el PDF se envía a la API de xAI para su lectura."
    )

    if archivo is None:
        st.session_state["liq_confirmada"] = False
        st.caption("Sin PDF confirmado, el cálculo usa el sueldo manual de la barra lateral.")
    else:
        contenido = archivo.getvalue()
        firma = hashlib.sha256(contenido).hexdigest()
        if st.session_state.get("liq_firma") != firma:
            try:
                leida = leer_liquidacion(contenido)
            except ValueError as exc:
                leida = {
                    "imponible": None,
                    "colacion": None,
                    "movilizacion": None,
                    "afp": None,
                    "salud": None,
                    "cesantia": None,
                    "impuesto_unico": None,
                    "avisos": [str(exc)],
                    "texto": "",
                }
            fuentes = _fuentes_desde_parser(leida)
            leida, fuentes = _completar_con_llm(leida, fuentes)
            st.session_state["liq_firma"] = firma
            st.session_state["liq_extraida"] = leida
            st.session_state["liq_fuentes"] = fuentes
            st.session_state["liq_confirmada"] = False
            _volcar_liquidacion(leida)
        else:
            _asegurar_campos_liquidacion(st.session_state.get("liq_extraida") or {})

        fuentes_ui = st.session_state.get("liq_fuentes") or {}
        for aviso in (st.session_state.get("liq_extraida") or {}).get("avisos") or []:
            st.warning(str(aviso))

        with st.form("confirmar_liquidacion"):
            st.markdown("**Revisa y corrige los montos leídos**")
            col_haberes, col_descuentos = st.columns(2)
            with col_haberes:
                imponible = st.number_input(
                    "Total imponible (CLP)",
                    min_value=0,
                    step=10_000,
                    key="liq_imponible",
                    help="Al confirmar, este monto reemplaza el sueldo manual.",
                )
                _caption_fuente(fuentes_ui, "imponible")
                st.number_input(
                    "Colación (no imponible)",
                    min_value=0,
                    step=1_000,
                    key="liq_colacion",
                )
                _caption_fuente(fuentes_ui, "colacion")
                st.number_input(
                    "Movilización (no imponible)",
                    min_value=0,
                    step=1_000,
                    key="liq_movilizacion",
                )
                _caption_fuente(fuentes_ui, "movilizacion")
            with col_descuentos:
                st.number_input("Descuento AFP", min_value=0, step=1_000, key="liq_afp")
                _caption_fuente(fuentes_ui, "afp")
                st.number_input("Descuento salud", min_value=0, step=1_000, key="liq_salud")
                _caption_fuente(fuentes_ui, "salud")
                st.number_input("Descuento cesantía", min_value=0, step=1_000, key="liq_cesantia")
                _caption_fuente(fuentes_ui, "cesantia")
                st.number_input("Impuesto único", min_value=0, step=1_000, key="liq_impuesto")
                _caption_fuente(fuentes_ui, "impuesto_unico")
            st.caption(
                "Solo el total imponible confirmado entra al cálculo. AFP, salud, "
                "cesantía e impuesto único sirven para comprobar que el PDF se leyó "
                "bien; las cotizaciones del modelo siguen los supuestos de la barra lateral."
            )
            confirmar = st.form_submit_button(
                "Confirmar y usar el imponible",
                type="primary",
            )

        if confirmar:
            if imponible <= 0:
                st.error("Indica un total imponible mayor que cero para usarlo como base.")
                st.session_state["liq_confirmada"] = False
            else:
                st.session_state["sueldo_confirmado"] = float(imponible)
                st.session_state["liq_confirmada"] = True

        texto_leido = (st.session_state.get("liq_extraida") or {}).get("texto") or ""
        if texto_leido.strip():
            with st.expander("Texto leído del PDF"):
                st.text(texto_leido)

        if st.session_state.get("liq_confirmada"):
            st.success(
                f"Imponible confirmado: {clp_md(st.session_state['sueldo_confirmado'])}. "
                "El cálculo usa este monto y no el sueldo manual."
            )
            if st.button("Volver al sueldo manual"):
                st.session_state["liq_confirmada"] = False
                st.rerun()

with st.sidebar:
    st.header("Datos del trabajador")
    sueldo_manual = st.number_input(
        "Sueldo bruto mensual (CLP)",
        min_value=0,
        value=2_500_000,
        step=50_000,
        help="Remuneración imponible mensual. Si confirmas una liquidación, "
        "ese total imponible reemplaza este monto.",
    )
    if st.session_state.get("liq_confirmada"):
        st.caption(
            "En uso: el total imponible confirmado de la liquidación. "
            "Este sueldo manual queda como respaldo."
        )
    arriendo_mensual = st.number_input(
        "Arriendo mensual total (CLP)",
        min_value=0,
        value=0,
        step=50_000,
        help="Suma de tus arriendos mensuales (antes de gastos). No paga IUSC: "
        "se usa en la proyección anual del Global Complementario.",
    )
    if float(arriendo_mensual) > 0:
        st.caption(
            "Ingresos mensuales totales: "
            f"{clp_md(float(sueldo_manual) + float(arriendo_mensual))} "
            "(sueldo + arriendos)."
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

if st.session_state.get("liq_confirmada"):
    sueldo = float(st.session_state["sueldo_confirmado"])
else:
    sueldo = float(sueldo_manual)

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

st.subheader("Simulador: juega con tu aporte")
st.caption("Arrastra el slider y mira en vivo cómo cambian el impuesto y el ahorro.")
tope_sim = int(res.tope_mensual_pesos)
apv_sim = st.slider(
    "APV-B mensual a simular (CLP)",
    min_value=0,
    max_value=tope_sim,
    value=min(int(apv_actual), tope_sim),
    step=10_000,
)
r_sim = simular(float(sueldo), float(apv_sim), params)
ahorro_sim = simular(float(sueldo), 0, params).iusc_anual_actual - r_sim.iusc_anual_actual
sim1, sim2, sim3 = st.columns(3)
sim1.metric("IUSC anual con ese aporte", clp(r_sim.iusc_anual_actual))
sim2.metric("IUSC mensual con ese aporte", clp(r_sim.iusc_mensual_actual))
sim3.metric("Ahorro anual vs $0 APV", clp(ahorro_sim))
if abs(apv_sim - res.apv_optimo) < 1:
    st.success("Ese aporte coincide con el óptimo tributario.")
elif apv_sim < res.apv_optimo:
    st.caption(f"Te faltan {clp_md(res.apv_optimo - apv_sim)}/mes para el óptimo.")
else:
    st.caption("Sobre el óptimo: el exceso ya no reduce más impuesto.")

st.subheader("Cuánto me devuelve el SII")
impuesto_sin_apv = simular(float(sueldo), 0, params).iusc_anual_actual
impuesto_con_apv = res.iusc_anual_actual
devolucion = impuesto_sin_apv - impuesto_con_apv

dev1, dev2, dev3 = st.columns(3)
dev1.metric("Impuesto anual sin APV", clp(impuesto_sin_apv))
dev2.metric("Impuesto anual con tu APV", clp(impuesto_con_apv))
with dev3.container(key="devolucion_sii"):
    st.metric("Devolución anual estimada", clp(devolucion))

st.caption(
    "La 'devolución' no es un pago del SII: es el menor IUSC anual por deducir "
    "el APV de la base imponible (régimen B). Lo ves como menor descuento en tu "
    "liquidación mes a mes."
)

ejemplos = []
for sueldo_ref, apv_ref in (
    (4_000_000, 1_000_000),
    (7_000_000, 1_500_000),
    (10_000_000, 2_000_000),
):
    sin_apv = simular(float(sueldo_ref), 0, params)
    con_apv = simular(float(sueldo_ref), float(apv_ref), params)
    ejemplos.append(
        {
            "Sueldo bruto": clp(sueldo_ref),
            "APV mensual": clp(apv_ref),
            "Impuesto anual sin APV": clp(sin_apv.iusc_anual_actual),
            "Devolución anual": clp(sin_apv.iusc_anual_actual - con_apv.iusc_anual_actual),
        }
    )
st.markdown("**Ejemplos de referencia**")
st.caption("Cada fila se calcula con la misma simulación y con la UF, UTM y AFP de la barra lateral.")
st.table(
    pd.DataFrame(ejemplos),
    alt="Impuesto anual sin APV y devolución para sueldos de 4, 7 y 10 millones",
)

st.subheader("Punto de equilibrio: \\$0 de impuesto")
st.caption(
    "El tope de 50 UF al mes es el máximo legal. Aquí se busca el APV mensual "
    "mínimo que deja el IUSC del mes en cero."
)
apv_cero = apv_para_iusc_cero(float(sueldo), params)
tope = res.tope_mensual_pesos
res_tope = simular(float(sueldo), tope, params)

if apv_cero < 1:
    st.info(r"Ya pagas \$0 de IUSC sin APV.")
elif apv_cero > tope + 1:
    st.warning(
        f"Impuesto mínimo posible (con 50 UF/mes): {clp_md(res_tope.iusc_mensual_actual)}/mes.",
        title=r"No alcanzable: ni con el tope legal de 50 UF/mes llegas a \$0 impuesto.",
    )
else:
    res_cero = simular(float(sueldo), apv_cero, params)
    st.table(
        pd.DataFrame(
            [
                {
                    "Escenario": "Tope máximo (50 UF/mes)",
                    "APV mensual": clp(tope),
                    "IUSC mensual": clp(res_tope.iusc_mensual_actual),
                    "IUSC anual": clp(res_tope.iusc_anual_actual),
                },
                {
                    "Escenario": "Punto de equilibrio ($0 impuesto)",
                    "APV mensual": clp(apv_cero),
                    "IUSC mensual": clp(res_cero.iusc_mensual_actual),
                    "IUSC anual": clp(res_cero.iusc_anual_actual),
                },
            ]
        ),
        alt="Tope legal de 50 UF comparado con el APV que deja el IUSC en cero",
    )
    diferencia_bolsillo = tope - apv_cero
    st.markdown(
        f"Diferencia de bolsillo: {clp_md(diferencia_bolsillo)}/mes extra "
        "inmovilizados en el APV sin beneficio tributario adicional."
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
st.altair_chart(
    linea + marcas + etiquetas,
    width="stretch",
    alt="IUSC anual según el APV mensual, con el aporte actual y el óptimo",
)

st.subheader("Operación Renta anual")
st.caption(
    "Proyección informativa del Impuesto Global Complementario. El optimizador "
    "mensual de más arriba sigue siendo el cálculo preciso del IUSC de cada liquidación."
)
st.info(
    "Los dividendos hipotecarios no se rebajan de la base del Impuesto Global "
    "Complementario. En Chile las personas naturales no tienen una deducción "
    "por intereses del crédito hipotecario, así que esta proyección no los pide "
    "ni los resta.",
    title="Los dividendos no son deducibles",
)

propiedades_iniciales = pd.DataFrame(
    {
        "Propiedad": pd.Series([""], dtype="string"),
        "Arriendo anual (CLP)": pd.Series([0], dtype="int64"),
        "DFL2 (exento)": pd.Series([False], dtype="bool"),
    }
)
propiedades = st.data_editor(
    propiedades_iniciales,
    key="editor_propiedades",
    num_rows="dynamic",
    hide_index=True,
    column_config={
        "Propiedad": st.column_config.TextColumn("Propiedad", width="medium"),
        "Arriendo anual (CLP)": st.column_config.NumberColumn(
            "Arriendo anual (CLP)",
            min_value=0,
            step=100_000,
            format="dollar",
        ),
        "DFL2 (exento)": st.column_config.CheckboxColumn(
            "DFL2 (exento)",
            help="El arriendo de una vivienda DFL2 exenta no se suma a la base.",
            default=False,
        ),
    },
    alt="Propiedades, arriendo anual y marca de exención DFL2",
)

filas_arriendo: list[tuple[float, bool]] = []
for _, fila in propiedades.iterrows():
    marca = fila["DFL2 (exento)"]
    es_dfl2 = False if pd.isna(marca) else bool(marca)
    filas_arriendo.append((fila["Arriendo anual (CLP)"], es_dfl2))
arriendos_afectos, arriendos_exentos = separar_arriendos(filas_arriendo)
if propiedades["Arriendo anual (CLP)"].sum() == 0 and float(arriendo_mensual) > 0:
    # Atajo: tabla vacía → se usa el arriendo mensual de la barra lateral × 12.
    arriendos_afectos = float(arriendo_mensual) * 12
    st.caption(
        f"Usando el arriendo mensual de la barra lateral × 12 ({clp_md(arriendos_afectos)}). "
        "Detállalo por propiedad en la tabla para aplicar la exención DFL2."
    )

base_sin = base_anual_operacion_renta(res.rli_sin_apv, arriendos_afectos)
base_con = base_anual_operacion_renta(res.rli_actual, arriendos_afectos)
gc_sin = global_complementario(base_sin, params)
gc_con = global_complementario(base_con, params)
ahorro_gc = gc_sin - gc_con

st.caption(
    f"Base anual sin APV: {clp_md(base_sin)}. Base anual con tu APV: {clp_md(base_con)}. "
    "Es 12 × la RLI mensual más los arriendos que no marcaste como DFL2. "
    f"La UTA usada es 12 × la UTM de la barra ({clp_md(params.utm)})."
)
if arriendos_exentos > 0:
    st.caption(f"Arriendos DFL2 excluidos de la base: {clp_md(arriendos_exentos)}.")

gc1, gc2, gc3 = st.columns(3)
gc1.metric("Global Complementario sin APV", clp(gc_sin))
gc2.metric("Global Complementario con tu APV", clp(gc_con))
gc3.metric("Ahorro anual de Global Complementario", clp(ahorro_gc))

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

st.subheader("Recomendación final")
st.caption(
    "Un resumen en palabras con tus cifras, generado por IA. No es asesoría tributaria: "
    "verifica con tu empleador o un contador antes de cambiar tu APV."
)
st.session_state.setdefault("recomendacion_ia", None)
if st.button("Generar recomendación con IA", type="primary"):
    with st.spinner("Analizando tus cifras..."):
        contexto = (
            f"Sueldo bruto mensual: {clp_md(float(sueldo))}. "
            f"Arriendo mensual total: {clp_md(float(arriendo_mensual))}. "
            f"APV-B mensual actual: {clp_md(float(apv_actual))}. "
            f"APV-B mensual óptimo: {clp_md(res.apv_optimo)}. "
            f"Ahorro tributario anual llevando el APV al óptimo: {clp_md(res.ahorro_anual)}. "
            f"IUSC anual con APV actual: {clp_md(res.iusc_anual_actual)}. "
            f"IUSC anual con APV óptimo: {clp_md(res.iusc_anual_optimo)}. "
            f"Tope APV mensual (50 UF): {clp_md(res.tope_mensual_pesos)}. "
            f"Global Complementario anual con APV actual: {clp_md(gc_con)}. "
            f"Ahorro anual de Global Complementario: {clp_md(ahorro_gc)}. "
            f"Motivo del óptimo: {res.motivo_optimo}"
        )
        st.session_state["recomendacion_ia"] = generar_recomendacion(contexto) or "ERROR"
rec = st.session_state["recomendacion_ia"]
if rec and rec != "ERROR":
    with st.container(border=True):
        st.markdown(rec)
elif rec == "ERROR":
    st.warning("No se pudo generar la recomendación (revisa tu saldo de créditos o la terminal).")
