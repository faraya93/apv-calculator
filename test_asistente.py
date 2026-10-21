from types import SimpleNamespace

import asistente
import llm_extract
from engine import Parametros

_P = Parametros()
CTX = {
    "sueldo_bruto": 4_712_425.0,
    "utm": _P.utm,
    "uf": _P.uf,
    "comision_afp": _P.comision_afp,
    "contrato_indefinido": True,
}


def test_simular_aporte_ahorra():
    r = asistente.ejecutar("simular_aporte", {"aporte_mensual": 500_000}, CTX)
    assert r["ahorro_anual_vs_no_aportar"] > 0
    assert r["iusc_anual"] > 0


def test_puntos_dulces_ordenados_y_dentro_del_tope():
    r = asistente.ejecutar("puntos_dulces", {}, CTX)
    pts = r["puntos_dulces"]
    assert pts, "para este sueldo debería haber puntos dulces"
    aportes = [p["aporte_mensual"] for p in pts]
    assert aportes == sorted(aportes)
    assert all(a <= r["tope_mensual"] for a in aportes)
    # El punto más caro baja al tramo de 4% (corte de 30 UTM); el de 13,5 UTM
    # exige más que el tope de 50 UF, así que no aparece.
    assert pts[-1]["tasa_marginal_pct"] == 4.0
    # Eficiencia decreciente: el primer peso aportado rinde más que el último.
    assert pts[0]["ahorro_por_peso_aportado"] > pts[-1]["ahorro_por_peso_aportado"]


def test_resumen_coherente():
    r = asistente.ejecutar("resumen_actual", {}, CTX)
    assert 0 < r["apv_optimo_tributario"] <= r["tope_mensual_legal"]
    assert r["ahorro_anual_maximo"] > 0


def test_herramienta_desconocida_no_lanza():
    r = asistente.ejecutar("no_existe", {}, CTX)
    assert "error" in r


class _FakeCompletions:
    def __init__(self, padre):
        self._padre = padre

    def create(self, **kwargs):
        self._padre.llamadas += 1
        if self._padre.llamadas == 1:
            llamada = SimpleNamespace(
                id="1",
                function=SimpleNamespace(
                    name="simular_aporte", arguments='{"aporte_mensual": 500000}'
                ),
            )
            mensaje = SimpleNamespace(content=None, tool_calls=[llamada])
        else:
            mensaje = SimpleNamespace(content="Con $500.000 ahorras.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=mensaje)])


class _FakeClient:
    def __init__(self):
        self.llamadas = 0
        self.chat = SimpleNamespace(completions=_FakeCompletions(self))


def test_responder_ejecuta_herramienta_y_responde():
    fake = _FakeClient()
    original = llm_extract._cliente_y_modelo
    llm_extract._cliente_y_modelo = lambda: (fake, "fake")
    try:
        out = asistente.responder(
            [{"role": "user", "content": "¿y si aporto $500.000?"}], CTX
        )
    finally:
        llm_extract._cliente_y_modelo = original
    assert out == "Con $500.000 ahorras."
    assert fake.llamadas == 2
