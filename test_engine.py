"""Comprobaciones puntuales contra la tabla SII de octubre 2026."""

from engine import (
    Parametros,
    apv_optimo_mensual,
    apv_para_iusc_cero,
    base_anual_operacion_renta,
    global_complementario,
    iusc_mensual,
    renta_liquida_imponible,
    renta_liquida_sin_apv,
    separar_arriendos,
    simular,
    tope_mensual_pesos,
)

P = Parametros()


def test_rebaja_primer_tramo():
    # 20 UTM: factor 4%, rebaja 0,54 UTM.
    rli = 20 * P.utm
    esperado = rli * 0.04 - 0.54 * P.utm
    assert abs(iusc_mensual(rli, P) - esperado) < 0.01


def test_exento():
    assert iusc_mensual(13.5 * P.utm, P) == 0.0


def test_tabla_octubre_tramo_8():
    # $2.164.530,01 cae al 8%; usamos un punto interior.
    rli = 2_500_000
    esperado = rli * 0.08 - 125_542.74
    assert abs(iusc_mensual(rli, P) - esperado) < 0.05


def test_optimo_no_supera_50_uf():
    res = simular(20_000_000, 0, P)
    assert abs(res.apv_optimo - 50 * P.uf) < 1.0
    assert res.ahorro_anual > 0


def test_apv_cero_es_la_resta_del_optimo():
    tope = tope_mensual_pesos(P)
    for sueldo in (800_000, 2_500_000, 8_000_000, 20_000_000):
        cero = apv_para_iusc_cero(sueldo, P)
        optimo, _ = apv_optimo_mensual(sueldo, P)
        rli = renta_liquida_sin_apv(sueldo, P)
        assert abs(optimo - min(tope, cero, rli)) < 0.01
        if cero <= tope + 1:
            assert iusc_mensual(renta_liquida_imponible(sueldo, cero, P), P) < 1.0


def test_sueldo_alto_no_llega_a_iusc_cero():
    assert apv_para_iusc_cero(20_000_000, P) > tope_mensual_pesos(P)


def test_sueldo_bajo_ya_esta_exento():
    assert apv_para_iusc_cero(500_000, P) == 0.0


def test_global_complementario_equivale_a_doce_meses():
    rli = renta_liquida_sin_apv(4_000_000, P)
    assert abs(global_complementario(rli * 12, P) - iusc_mensual(rli, P) * 12) < 1.0


def test_arriendos_dfl2_quedan_fuera_de_la_base():
    afectos, exentos = separar_arriendos([(6_000_000, False), (4_000_000, True), (float("nan"), False)])
    assert afectos == 6_000_000
    assert exentos == 4_000_000
    assert base_anual_operacion_renta(1_000_000, afectos) == 18_000_000
    solo_sueldo = global_complementario(base_anual_operacion_renta(1_000_000, 0), P)
    con_arriendo = global_complementario(base_anual_operacion_renta(1_000_000, afectos), P)
    assert con_arriendo > solo_sueldo
