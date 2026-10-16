"""Comprobaciones puntuales contra la tabla SII de octubre 2026."""

from engine import Parametros, iusc_mensual, simular

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
