"""Pruebas del cálculo CM03 (python test_cm.py)."""
from cm_calculo import calcular_cm03, canonizar_jurisdiccion, coeficientes_vigentes, validar_coeficientes

COEFS = {"CABA": 0.3050, "Córdoba": 0.3787, "Santa Fe": 0.3163}
ALIC = {("Ventas", j): 2.0 for j in COEFS}


def test_distribucion_y_totales():
    r = calcular_cm03("2026-06", {"Ventas": 20000}, COEFS, ALIC, {}, {})
    por = {f.jurisdiccion: f for f in r.filas}
    assert por["CABA"].base == 6100.0 and por["CABA"].impuesto_determinado == 122.0
    assert por["Córdoba"].base == 7574.0 and por["Santa Fe"].impuesto == 126.52
    assert r.totales["impuesto_determinado"] == 400.0 and r.totales["a_pagar"] == 400.0


def test_deducciones_saldo_a_favor_no_se_compensa():
    cred = {"CABA": dict(retenciones=10, percepciones=60), "Córdoba": dict(saldo_anterior=500)}
    r = calcular_cm03("2026-06", {"Ventas": 20000}, COEFS, ALIC, {}, cred)
    por = {f.jurisdiccion: f for f in r.filas}
    assert por["CABA"].saldo == 52.0 and por["Córdoba"].saldo == -348.52
    assert r.totales["a_pagar"] == round(52.0 + 126.52, 2) and r.totales["a_favor"] == 348.52


def test_minimo_y_alicuota_faltante():
    r = calcular_cm03("2026-06", {"Ventas": 1000}, COEFS, {("Ventas", "CABA"): 2.0}, {"CABA": 100}, {})
    por = {f.jurisdiccion: f for f in r.filas}
    assert por["CABA"].impuesto == 6.1 and por["CABA"].impuesto_determinado == 100.0
    assert por["Santa Fe"].avisos and por["Santa Fe"].impuesto == 0


def test_varias_actividades():
    alic = {("A", "CABA"): 3.0, ("B", "CABA"): 1.0}
    r = calcular_cm03("2026-06", {"A": 1000, "B": 2000}, {"CABA": 1.0}, alic, {}, {})
    assert r.filas[0].impuesto == 30 + 20


def test_coeficientes():
    assert validar_coeficientes(COEFS) is None
    assert "suman" in validar_coeficientes({"CABA": 0.5, "Córdoba": 0.4})
    conj = [("2025-04", {"CABA": 1.0}), ("2026-04", COEFS)]
    assert coeficientes_vigentes(conj, "2026-03")[0] == "2025-04"
    assert coeficientes_vigentes(conj, "2026-04")[0] == "2026-04"
    assert coeficientes_vigentes(conj, "2025-01") == (None, None)


def test_jurisdicciones():
    assert canonizar_jurisdiccion("Cdad Auton BsAs") == "CABA"
    assert canonizar_jurisdiccion("buenos aires") == "Buenos Aires"
    assert canonizar_jurisdiccion("Cordoba") == "Córdoba"
    assert canonizar_jurisdiccion("") is None


def test_txt_sifere_formato_oficial():
    import datetime as dt
    from sifere_txt import generar_txt, renglon
    f = dt.date(2026, 9, 30)
    assert renglon("La Pampa", "30-68178305-5", f, 118, 732809, "Factura A", 62.63) == "91130-68178305-530/09/2026011800732809FA00000062,63"
    assert renglon("La Pampa", "30681783055", f, 118, 739765, "Factura A", 8546.31) == "91130-68178305-530/09/2026011800739765FA00008546,31"
    base = dict(jurisdiccion="La Pampa", texto_original="", en_convenio=True, cuit_agente="30-68178305-5", razon_social="X", fecha=f,
                comprobante="Factura A", letra="A", punto_venta=118, numero=732828, importe=357.5)
    filas = [base, dict(base, jurisdiccion="", texto_original="?"), dict(base, en_convenio=False), dict(base, comprobante="Nota de Crédito A"), dict(base, punto_venta=11111)]
    texto, omitidas = generar_txt(filas)
    assert texto == "91130-68178305-530/09/2026011800732828FA00000357,50\r\n" and len(omitidas) == 4


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
