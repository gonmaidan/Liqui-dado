"""Pruebas del cálculo F.931 y del TXT SICOSS (python test_f931.py)."""
from f931_calculo import LARGO_REGISTRO, calcular_liquidacion, generar_txt_sicoss, validar_empleado, _cuil_valido

EMPLEADOR = dict(tipo_empleador="2b", hasta25=True, art_pct=1.5, art_fijo=0, scvo=True, actividad=49, zona=0, provincia="CABA")
EMP = dict(cuil="20-12345678-6", apellido_nombre="Pérez Juan", tipo="comun", adherentes=0, obra_social=126205, hijos=2, conyuge=1)


def liquidar(rem, emp=EMP, empleador=EMPLEADOR, periodo="2026-10"):
    return calcular_liquidacion(periodo, empleador, [(emp, rem)], {})


def test_calculo_basico():
    t = liquidar(dict(sueldo=1_000_000, dias=30)).totales
    assert t["aporte_jubilacion"] == 110000.0 and t["aporte_inssjp"] == 30000.0 and t["aporte_os"] == 30000.0
    # base 1.000.000 - detracción 7.003,68 - adicional 10.000 = 982.996,32 x 18%
    assert t["contrib_ss"] == 176939.34
    assert t["contrib_os"] == 60000.0 and t["scvo"] == 424.62


def test_tope_y_minimo():
    r = liquidar(dict(sueldo=10_000_000, dias=30)).empleados[0]
    assert r.imponible == 4769631.49
    r = liquidar(dict(sueldo=50_000, dias=30)).empleados[0]
    assert r.imponible == 146759.98 and r.avisos


def test_no_remunerativo_no_integra_la_base():
    r = liquidar(dict(sueldo=1_000_000, no_remunerativo=200_000, dias=30)).empleados[0]
    assert r.imponible == 1_000_000 and r.rem_total == 1_200_000


def test_sac_sube_tope_y_detraccion():
    r = liquidar(dict(sueldo=1_000_000, sac=500_000, dias=30)).empleados[0]
    assert r.detraccion == round(7003.68 * 1.5, 2)


def test_jubilado_no_paga_jubilacion_ni_obra_social():
    r = liquidar(dict(sueldo=1_000_000, dias=30), emp=dict(EMP, tipo="jubilado")).empleados[0]
    assert r.aporte_jubilacion == 0 and r.aporte_os == 0 and r.aporte_inssjp == 30000.0


def test_txt_largo_fijo_y_campos():
    txt = generar_txt_sicoss(liquidar(dict(sueldo=1_000_000, horas_extras=20_000, dias=30)), EMPLEADOR)
    linea = txt.split("\r\n")[0]
    assert len(linea) == LARGO_REGISTRO == 499 and txt.endswith("\r\n")
    assert linea[:11] == "20123456786" and linea[11:41].strip() == "PEREZ JUAN"
    assert linea[69:81] == "001020000,00"      # remuneración total (pos. 70-81)
    assert linea[319:328] == "000030,00"       # días trabajados (pos. 320-328)


def test_validaciones():
    assert _cuil_valido("20123456786") and not _cuil_valido("20123456781")
    assert validar_empleado(dict(EMP, cuil="20-12345678-1"))
    assert validar_empleado(dict(EMP, obra_social=0))
    assert not validar_empleado(EMP)


if __name__ == "__main__":
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_"):
            f()
            print("ok", nombre)
