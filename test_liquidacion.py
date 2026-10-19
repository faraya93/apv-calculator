"""Lectura de liquidaciones: el texto manda; el PDF solo lo extrae."""

from liquidacion import extraer_texto_pdf, leer_liquidacion, parsear_texto

TEXTO = """
LIQUIDACION DE SUELDO
Nombre: Ana Perez
RUT: 12.345.678-9
Periodo: Octubre 2026

HABERES
Sueldo Base                 2.200.000
Gratificacion Legal           300.000
Total Haberes Imponibles    2.500.000
Colacion                       45.000
Movilizacion                   40.000
Total Haberes No Imponibles    85.000

DESCUENTOS
Cotizacion AFP obligatoria   250.000
Comision AFP                   31.750
Fonasa 7%                     175.000
Seguro Cesantia Trabajador     15.000
Seguro Cesantia Empleador      50.000
Impuesto Unico                 32.410
"""


def test_prioriza_total_imponible_y_suma_afp():
    datos = parsear_texto(TEXTO)
    assert datos["imponible"] == 2_500_000
    assert datos["colacion"] == 45_000
    assert datos["movilizacion"] == 40_000
    assert datos["afp"] == 281_750
    assert datos["salud"] == 175_000
    assert datos["cesantia"] == 15_000
    assert datos["impuesto_unico"] == 32_410
    assert datos["avisos"] == []


def test_no_confunde_el_rut_con_un_monto():
    datos = parsear_texto("RUT: 12.345.678-9\nTotal imponible 1.800.000\n")
    assert datos["imponible"] == 1_800_000


def test_avisa_si_falta_el_imponible():
    datos = parsear_texto("Colacion 20.000\n")
    assert datos["imponible"] is None
    assert any("imponible" in aviso.lower() for aviso in datos["avisos"])


def _pdf_minimo(lineas: list[str]) -> bytes:
    """PDF de una página con Helvetica, suficiente para pdfplumber."""
    operaciones = ["BT", "/F1 12 Tf"]
    y = 740
    for linea in lineas:
        seguro = linea.encode("latin-1", "replace").decode("latin-1")
        seguro = seguro.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        operaciones.append(f"1 0 0 1 50 {y} Tm ({seguro}) Tj")
        y -= 18
    operaciones.append("ET")
    contenido = "\n".join(operaciones).encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(contenido) + contenido + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    salida = bytearray(b"%PDF-1.4\n")
    offsets = []
    for indice, objeto in enumerate(objetos, start=1):
        offsets.append(len(salida))
        salida += f"{indice} 0 obj\n".encode()
        salida += objeto + b"\nendobj\n"
    inicio_xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n".encode()
    salida += b"0000000000 65535 f \n"
    for offset in offsets:
        salida += f"{offset:010d} 00000 n \n".encode()
    salida += (
        f"trailer<< /Size {len(objetos) + 1} /Root 1 0 R >>\n"
        f"startxref\n{inicio_xref}\n%%EOF"
    ).encode()
    return bytes(salida)


def test_pdf_real_con_respaldo_de_texto():
    pdf = _pdf_minimo(
        [
            "Total imponible 2.500.000",
            "Colacion 45.000",
            "Movilizacion 40.000",
            "AFP Habitat 281.750",
            "Fonasa 175.000",
            "Cesantia trabajador 15.000",
            "Impuesto Unico 32.410",
        ]
    )
    texto = extraer_texto_pdf(pdf)
    assert "2.500.000" in texto
    datos = leer_liquidacion(pdf)
    assert datos["imponible"] == 2_500_000
    assert datos["impuesto_unico"] == 32_410
