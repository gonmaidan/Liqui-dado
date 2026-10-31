"""
cm_calculo.py
---------------
Cálculo de la declaración jurada mensual de Convenio Multilateral (CM03), sin
depender de Flask ni de la base.

Mecánica (Régimen General):
  1. Ingresos computables del mes, por actividad.
  2. Base imponible de cada jurisdicción = ingresos x coeficiente unificado de la jurisdicción
     (el coeficiente sale de la CM05 anual y tiene 4 decimales; suman 1).
  3. Impuesto determinado = base x alícuota (la de cada actividad en esa jurisdicción),
     con el mínimo de la jurisdicción si el estudio cargó uno.
  4. Se descuentan, jurisdicción por jurisdicción: retenciones, retenciones bancarias (SIRCREB),
     percepciones, pagos a cuenta y saldo a favor de períodos anteriores.
  5. Saldo a pagar, o saldo a favor que queda en esa jurisdicción (no se compensa con otra).

Las alícuotas, mínimos y coeficientes los carga el estudio: no hay valores por defecto,
porque cambian por jurisdicción, actividad y año. La presentación se hace en SIFERE web.
"""

from dataclasses import dataclass, field

from percepciones_pdf import _jurisdiccion as _jurisdiccion_en_texto

JURISDICCIONES = [
    "CABA", "Buenos Aires", "Catamarca", "Chaco", "Chubut", "Córdoba", "Corrientes", "Entre Ríos",
    "Formosa", "Jujuy", "La Pampa", "La Rioja", "Mendoza", "Misiones", "Neuquén", "Río Negro",
    "Salta", "San Juan", "San Luis", "Santa Cruz", "Santa Fe", "Santiago del Estero",
    "Tierra del Fuego", "Tucumán",
]

TOLERANCIA_COEFICIENTES = 0.0002  # los coeficientes tienen 4 decimales y pueden sumar 0,9999 / 1,0001


def canonizar_jurisdiccion(texto):
    """'Cdad Auton BsAs' / 'Bs. As.' / 'CABA' -> nombre canónico de JURISDICCIONES, o None."""
    t = (texto or "").strip()
    if not t:
        return None
    for j in JURISDICCIONES:
        if t.lower() == j.lower():
            return j
    return _jurisdiccion_en_texto(t)


def _r(x) -> float:
    return round(float(x or 0) + 1e-9, 2)


def validar_coeficientes(coefs: dict):
    """Devuelve un mensaje de error o None. coefs: {jurisdiccion: coeficiente}."""
    if not coefs:
        return "Cargá al menos un coeficiente."
    if any(c < 0 or c > 1 for c in coefs.values()):
        return "Cada coeficiente tiene que estar entre 0 y 1 (por ejemplo 0,3050)."
    suma = sum(coefs.values())
    if abs(suma - 1) > TOLERANCIA_COEFICIENTES:
        return f"Los coeficientes suman {suma:.4f} y tienen que sumar 1,0000."
    return None


def coeficientes_vigentes(conjuntos: list, periodo: str):
    """
    conjuntos: [(vigencia_desde 'AAAA-MM', {jurisdiccion: coef})]. Devuelve (vigencia, coefs) del
    último conjunto cuya vigencia es <= periodo, o (None, None).
    """
    validos = [c for c in conjuntos if c[0] <= periodo]
    if not validos:
        return None, None
    return max(validos, key=lambda c: c[0])


@dataclass
class FilaJurisdiccion:
    jurisdiccion: str
    coeficiente: float
    base: float = 0.0
    detalle: list = field(default_factory=list)   # [(actividad, base, alicuota, impuesto)]
    impuesto: float = 0.0                          # determinado por alícuota
    minimo: float = 0.0
    impuesto_determinado: float = 0.0              # max(impuesto, mínimo)
    retenciones: float = 0.0
    sircreb: float = 0.0
    percepciones: float = 0.0
    pagos: float = 0.0
    saldo_anterior: float = 0.0
    deducciones: float = 0.0
    saldo: float = 0.0                             # >0 a pagar, <0 a favor
    avisos: list = field(default_factory=list)


@dataclass
class ResultadoCM:
    periodo: str
    vigencia_coef: str
    ingresos: dict
    total_ingresos: float
    filas: list
    totales: dict
    avisos: list = field(default_factory=list)


def calcular_cm03(periodo, ingresos, coeficientes, alicuotas, minimos, creditos, vigencia_coef=""):
    """
    ingresos: {actividad: importe del mes}
    coeficientes: {jurisdiccion: coef}
    alicuotas: {(actividad, jurisdiccion): % }
    minimos: {jurisdiccion: importe}
    creditos: {jurisdiccion: dict(retenciones, sircreb, percepciones, pagos, saldo_anterior)}
    """
    total = _r(sum(ingresos.values()))
    resultado = ResultadoCM(periodo, vigencia_coef, dict(ingresos), total, [], {})
    for jur, coef in sorted(coeficientes.items(), key=lambda kv: JURISDICCIONES.index(kv[0]) if kv[0] in JURISDICCIONES else 99):
        fila = FilaJurisdiccion(jur, coef)
        for act, ingreso in ingresos.items():
            base = _r(ingreso * coef)
            if (act, jur) not in alicuotas:
                if base:
                    fila.avisos.append(f"Falta la alícuota de «{act}» en {jur}: se tomó 0%.")
                alic = 0.0
            else:
                alic = alicuotas[(act, jur)]
            imp = _r(base * alic / 100)
            fila.detalle.append((act, base, alic, imp))
            fila.base = _r(fila.base + base)
            fila.impuesto = _r(fila.impuesto + imp)
        fila.minimo = _r(minimos.get(jur, 0))
        fila.impuesto_determinado = max(fila.impuesto, fila.minimo) if total > 0 else fila.impuesto
        c = creditos.get(jur, {})
        fila.retenciones, fila.sircreb = _r(c.get("retenciones")), _r(c.get("sircreb"))
        fila.percepciones, fila.pagos = _r(c.get("percepciones")), _r(c.get("pagos"))
        fila.saldo_anterior = _r(c.get("saldo_anterior"))
        fila.deducciones = _r(fila.retenciones + fila.sircreb + fila.percepciones + fila.pagos + fila.saldo_anterior)
        fila.saldo = _r(fila.impuesto_determinado - fila.deducciones)
        resultado.filas.append(fila)

    f = resultado.filas
    s = lambda attr: _r(sum(getattr(x, attr) for x in f))
    resultado.totales = {
        "base": s("base"), "impuesto_determinado": s("impuesto_determinado"), "deducciones": s("deducciones"),
        "a_pagar": _r(sum(x.saldo for x in f if x.saldo > 0)),
        "a_favor": _r(-sum(x.saldo for x in f if x.saldo < 0)),
    }
    if abs(resultado.totales["base"] - total) > 0.05 * max(len(f), 1):
        resultado.avisos.append("La suma de las bases por jurisdicción difiere de los ingresos por redondeo de los coeficientes.")
    return resultado
