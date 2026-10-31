"""Pruebas de la lectura automática de percepciones (python test_percepciones_pdf.py)."""
from percepciones_pdf import detectar

ALIC = [0.0, 2.5, 5.0, 10.5, 21.0, 27.0]

MEDIFARM = """SUBTOTAL
353.465,98 ARS
IVA 21.00 %
74.227,85 ARS
Percep.IIBB CABA II 0.75 %
2.650,99 ARS
Percep.IIBB R.N.II 1.00 %
3.534,66 ARS
Percep.IIBB Mdza.II 1.50 %
5.301,99 ARS
TOTAL
439.181,47 ARS"""

EN_UNA_LINEA = """Neto Gravado 100.000,00
IVA 21% 21.000,00
Perc. IVA 3,00% 3.000,00
IIBB BA 0.10% 100,00 - IIBB CABA 0.50% 500,00
Total 124.600,00"""


def test_medifarm():
    r = detectar(MEDIFARM, 439181.47, 21.0, ALIC)
    assert (r.neto, r.iva) == (353465.98, 74227.85)
    assert r.iibb == [("CABA", 2650.99), ("Río Negro", 3534.66), ("Mendoza", 5301.99)]


def test_varias_percepciones_en_una_linea():
    r = detectar(EN_UNA_LINEA, 124600.0, 21.0, ALIC)
    assert r.percepcion_iva == 3000.0
    assert sorted(r.iibb) == [("Buenos Aires", 100.0), ("CABA", 500.0)]


def test_alicuota_distinta_a_la_elegida():
    texto = "Neto 1.000,00\nIVA 10,5% 105,00\nTotal 1.105,00"
    assert detectar(texto, 1105.0, 21.0, ALIC).alicuota == 10.5


def test_sin_combinacion_no_propone_nada():
    assert detectar("Neto 1.000,00\nIVA 210,00\nTotal 9.999,00", 9999.0, 21.0, ALIC) is None
    assert detectar("", 100.0, 21.0, ALIC) is None


if __name__ == "__main__":
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_"):
            f()
            print("ok", nombre)
