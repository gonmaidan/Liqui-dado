"""
f931_calculo.py
-----------------
Cálculo de aportes y contribuciones del F.931 (SICOSS) y generación del TXT
de importación de SICOSS. No depende de Flask ni de la base: trabaja con
diccionarios simples, así se puede probar solo.

IMPORTANTE: las alícuotas, detracciones y topes cambian por ley/resolución.
Los valores de PARAMETROS_DEFECTO y BASES_ANSES son los vigentes al momento de
armar esta versión (oct-2026) y el estudio los puede editar en pantalla. El
resultado es una ESTIMACIÓN para controlar contra lo que calcula SICOSS: la
declaración jurada la presenta el contador desde SICOSS / Declaración en línea.
"""

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Parámetros (editables por el estudio en la pantalla "Parámetros F.931")
# --------------------------------------------------------------------------
# clave: (valor por defecto, descripción, unidad)
PARAMETROS_DEFECTO = {
    "aporte_jubilacion": (11.0, "Aporte del trabajador — Jubilación (SIPA)", "%"),
    "aporte_inssjp": (3.0, "Aporte del trabajador — INSSJP (PAMI)", "%"),
    "aporte_os": (3.0, "Aporte del trabajador — Obra social", "%"),
    "aporte_os_adherente": (1.5, "Aporte adicional de obra social por cada adherente", "%"),
    "contrib_art2a": (20.4, "Contribución patronal unificada — Art. 2 inc. a) (servicios/comercio que superan límites MiPyME)", "%"),
    "contrib_art2b": (18.0, "Contribución patronal unificada — Art. 2 inc. b) (resto, con certificado MiPyME)", "%"),
    "contrib_os": (6.0, "Contribución patronal — Obra social", "%"),
    "detraccion": (7003.68, "Detracción por trabajador (Ley 27.541 art. 22)", "$"),
    "detraccion_ampliada": (17509.20, "Detracción ampliada por trabajador (textil, calzado, salud, agro, etc.)", "$"),
    "detraccion_hasta25": (10000.0, "Detracción adicional por empleador con hasta 25 trabajadores", "$"),
    "scvo": (424.62, "Seguro de Vida Obligatorio, por trabajador por mes", "$"),
    "factor_sac": (1.5, "Factor del tope y de la detracción en el mes que incluye SAC", "x"),
}

# Jubilados que siguen trabajando (condición 2): qué aportes pagan. VERIFICAR
# contra la normativa vigente antes de usar; se pueden cambiar acá o en pantalla.
PARAMETROS_JUBILADO = {
    "aporte_jubilacion": 0.0,
    "aporte_inssjp": 3.0,
    "aporte_os": 0.0,
    "contrib_os": 0.0,
}

# Base imponible mínima y máxima (Resoluciones ANSES). Fuente: Res. ANSES 186/2026
# (jul-2026) y 284/2026 (oct-2026). Para otros períodos el estudio las carga a mano.
BASES_ANSES = {
    "2026-07": (138757.90, 4509567.41),
    "2026-10": (146759.98, 4769631.49),
}

TIPOS_EMPLEADO = {
    "comun": "Relación de dependencia común",
    "jubilado": "Jubilado que sigue trabajando",
    "parcial": "Jornada parcial",
}
# Valores sugeridos de SICOSS para cada tipo: condición, modalidad de contratación.
DEFECTO_POR_TIPO = {
    "comun": {"condicion": 1, "modalidad": 8},
    "jubilado": {"condicion": 2, "modalidad": 8},
    "parcial": {"condicion": 1, "modalidad": 1},
}

# --------------------------------------------------------------------------
# Tablas de códigos de SICOSS (SOLO INFORMATIVAS: se muestran en los desplegables
# y se informan tal cual en el TXT; el cálculo de aportes NO depende de ellas, depende
# del "tipo de empleado"). Fuentes: tablas T03 de ARCA (RG 1584/2003 para modalidades,
# RG 3757 para situación de revista) y manual SICOSS para condición. Son listas parciales
# o históricas: para cualquier otro código se elige "Otro código" y se escribe.
# --------------------------------------------------------------------------
CONDICIONES = {
    1: "Activo, mayor de 18 años (servicios comunes)",
    2: "Jubilado",
    5: "Trabajador rural con derecho a jubilación (Ley 26.727)",
    10: "Pensionado / pensión no contributiva (ver tabla ARCA)",
    11: "Pensionado / pensión no contributiva (ver tabla ARCA)",
    14: "Jubilado — docentes universitarios, investigadores científicos/tecnológicos",
}
MODALIDADES = {
    8: "A tiempo completo indeterminado / trabajo permanente",
    1: "A tiempo parcial indeterminado / permanente",
    22: "A tiempo completo determinado (contrato a plazo fijo)",
    21: "A tiempo parcial determinado (contrato a plazo fijo)",
    11: "Trabajo de temporada",
    12: "Trabajo eventual",
    14: "Nuevo período de prueba",
    7: "Período de prueba (Leyes 24.465 y 25.013)",
    23: "Personal no permanente (Ley 22.248)",
    24: "Personal de la construcción (Ley 22.250)",
    25: "Empleo público provincial",
    13: "Trabajo formación",
    10: "Pasantías (Ley 25.165)",
    27: "Pasantías (Decreto 1.227/01)",
    26: "Beneficiario de programa de empleo, capacitación y recuperación productiva",
    2: "Becarios — residencias médicas (Ley 22.127)",
    3: "De aprendizaje (Ley 25.013)",
    9: "Práctica laboral para jóvenes",
    28: "Programa Jefes y Jefas de Hogar",
    29: "Decreto 1.212/03 — aportante autónomo",
    0: "Contrato modalidad promovida — reducción 0%",
    50: "Contrato modalidad promovida — reducción 50%",
    100: "Contrato modalidad promovida — reducción 100%",
}
SITUACIONES = {
    1: "Activo",
    5: "Licencia por maternidad",
    12: "Licencia por vacaciones",
    13: "Licencia sin goce de haberes",
    9: "Suspendido (art. 223 bis LCT)",
    6: "Suspensiones otras causales",
    10: "Licencia por excedencia",
    14: "Reserva de puesto",
    18: "ILT — primeros 10 días",
    19: "ILT — días 11 y siguientes",
    20: "Trabajador siniestrado en nómina de ART",
    32: "Licencia por paternidad",
    33: "Licencia por fuerza mayor (art. 221 LCT)",
    31: "Activo — funciones en el exterior",
    2: "Baja otras causales",
    7: "Baja por despido",
    0: "Baja por fallecimiento",
}

TIPOS_EMPLEADOR = {
    "2a": "Art. 2 inc. a) — servicios/comercio que superan el límite MiPyME (20,40%)",
    "2b": "Art. 2 inc. b) — resto del sector privado con certificado MiPyME (18%)",
}


def base_anses(periodo: str):
    """(minima, maxima, exacta). Si no hay dato del período, usa el último anterior conocido (exacta=False)."""
    if periodo in BASES_ANSES:
        return (*BASES_ANSES[periodo], True)
    anteriores = [p for p in BASES_ANSES if p <= periodo]
    clave = max(anteriores) if anteriores else min(BASES_ANSES)
    return (*BASES_ANSES[clave], False)


def _r(x) -> float:
    return round(float(x or 0) + 1e-9, 2)


# --------------------------------------------------------------------------
# Cálculo por empleado y total
# --------------------------------------------------------------------------
@dataclass
class ResultadoEmpleado:
    empleado: object
    remuneracion: dict
    rem_total: float = 0.0
    remunerativo: float = 0.0
    no_remunerativo: float = 0.0
    imponible: float = 0.0        # con tope y mínimo (Rem. imponible 1 a 4)
    imponible_sin_tope: float = 0.0
    detraccion: float = 0.0
    aporte_jubilacion: float = 0.0
    aporte_inssjp: float = 0.0
    aporte_os: float = 0.0
    contrib_ss: float = 0.0       # se completa en calcular_liquidacion (depende de la detracción total)
    contrib_os: float = 0.0
    art: float = 0.0
    scvo: float = 0.0
    avisos: list = field(default_factory=list)


@dataclass
class ResultadoLiquidacion:
    periodo: str
    base_minima: float
    base_maxima: float
    base_exacta: bool
    empleados: list
    detraccion_adicional_aplicada: float = 0.0
    totales: dict = field(default_factory=dict)
    avisos: list = field(default_factory=list)


def calcular_liquidacion(periodo, empleador, empleados_y_rem, params, base_minima=None, base_maxima=None) -> ResultadoLiquidacion:
    """
    empleador: dict con tipo_empleador ('2a'|'2b'), hasta25 (bool), sector_ampliado (bool),
               art_pct (float), art_fijo (float), scvo (bool).
    empleados_y_rem: lista de (empleado_dict, remuneracion_dict). El empleado trae
               tipo ('comun'|'jubilado'|'parcial'), adherentes (int); la remuneración trae
               sueldo, adicionales, horas_extras, zona, vacaciones, premios, sac,
               no_remunerativo, dias.
    params: {clave: valor} (usa PARAMETROS_DEFECTO para lo que falte).
    """
    p = {k: v[0] for k, v in PARAMETROS_DEFECTO.items()}
    p.update(params or {})
    minima, maxima, exacta = base_anses(periodo)
    minima = base_minima if base_minima else minima
    maxima = base_maxima if base_maxima else maxima
    if base_minima and base_maxima:
        exacta = True

    resultado = ResultadoLiquidacion(periodo, minima, maxima, exacta, [])
    if not exacta:
        resultado.avisos.append(
            f"No tengo la base imponible mínima/máxima de ANSES de {periodo}; usé la del último período conocido. "
            f"Cargá los valores del período en la pantalla."
        )

    tasa_ss = p["contrib_art2a"] if empleador.get("tipo_empleador") == "2a" else p["contrib_art2b"]
    det_base = p["detraccion_ampliada"] if empleador.get("sector_ampliado") else p["detraccion"]

    suma_base_ss, suma_det = 0.0, 0.0
    for emp, rem in empleados_y_rem:
        r = ResultadoEmpleado(empleado=emp, remuneracion=rem)
        tipo = emp.get("tipo", "comun")
        pj = dict(p)
        if tipo == "jubilado":
            pj.update(PARAMETROS_JUBILADO)

        remunerativo = sum(_r(rem.get(k)) for k in ("sueldo", "adicionales", "horas_extras", "zona", "vacaciones", "premios", "sac"))
        r.remunerativo = _r(remunerativo)
        r.no_remunerativo = _r(rem.get("no_remunerativo"))
        r.rem_total = _r(r.remunerativo + r.no_remunerativo)

        dias = max(0, min(int(rem.get("dias") or 30), 30))
        tiene_sac = _r(rem.get("sac")) > 0
        factor = p["factor_sac"] if tiene_sac else 1.0

        piso = minima * dias / 30.0 if tipo != "parcial" else 0.0
        tope = maxima * factor
        r.imponible_sin_tope = _r(max(r.remunerativo, piso))
        r.imponible = _r(min(r.imponible_sin_tope, tope))
        if r.remunerativo < piso - 0.005:
            r.avisos.append(f"La remuneración está por debajo de la base mínima; se tomó la mínima (${piso:,.2f}).")
        if r.imponible_sin_tope > tope + 0.005:
            r.avisos.append("La remuneración supera el tope máximo; los aportes se calculan sobre el tope.")

        r.detraccion = _r(min(det_base * factor * dias / 30.0, r.imponible))
        r.aporte_jubilacion = _r(r.imponible * pj["aporte_jubilacion"] / 100)
        r.aporte_inssjp = _r(r.imponible * pj["aporte_inssjp"] / 100)
        pct_os = pj["aporte_os"] + pj["aporte_os_adherente"] * int(emp.get("adherentes") or 0) * (1 if pj["aporte_os"] else 0)
        r.aporte_os = _r(r.imponible * pct_os / 100)
        r.contrib_os = _r(r.imponible * pj["contrib_os"] / 100)
        r.art = _r(r.imponible_sin_tope * float(empleador.get("art_pct") or 0) / 100 + float(empleador.get("art_fijo") or 0))
        r.scvo = _r(p["scvo"]) if empleador.get("scvo") else 0.0

        suma_base_ss += r.imponible
        suma_det += r.detraccion
        resultado.empleados.append(r)

    # Contribución unificada sobre la base neta de la detracción (la detracción
    # adicional de $10.000 es por empleador, no por empleado).
    base_neta = max(suma_base_ss - suma_det, 0.0)
    if empleador.get("hasta25") and len(empleados_y_rem) <= 25:
        extra = min(p["detraccion_hasta25"], base_neta)
        resultado.detraccion_adicional_aplicada = _r(extra)
        base_neta -= extra
    contrib_ss_total = _r(base_neta * tasa_ss / 100)

    # Reparto de la contribución entre empleados (proporcional) solo para mostrarla por línea.
    base_netas = [max(r.imponible - r.detraccion, 0.0) for r in resultado.empleados]
    suma_netas = sum(base_netas) or 1.0
    asignado = 0.0
    for i, r in enumerate(resultado.empleados):
        r.contrib_ss = _r(contrib_ss_total * base_netas[i] / suma_netas)
        asignado += r.contrib_ss
    if resultado.empleados:  # absorber el centavo de redondeo en el último
        resultado.empleados[-1].contrib_ss = _r(resultado.empleados[-1].contrib_ss + contrib_ss_total - asignado)

    suma = lambda attr: _r(sum(getattr(r, attr) for r in resultado.empleados))
    t = {
        "rem_total": suma("rem_total"),
        "remunerativo": suma("remunerativo"),
        "no_remunerativo": suma("no_remunerativo"),
        "imponible": suma("imponible"),
        "aporte_jubilacion": suma("aporte_jubilacion"),
        "aporte_inssjp": suma("aporte_inssjp"),
        "aporte_os": suma("aporte_os"),
        "contrib_ss": contrib_ss_total,
        "contrib_os": suma("contrib_os"),
        "art": suma("art"),
        "scvo": suma("scvo"),
        "detraccion": _r(suma_det + resultado.detraccion_adicional_aplicada),
    }
    t["aportes_ss"] = _r(t["aporte_jubilacion"] + t["aporte_inssjp"])
    t["total_ss"] = _r(t["aportes_ss"] + t["contrib_ss"])             # Seguridad social (aportes + contribuciones)
    t["total_os"] = _r(t["aporte_os"] + t["contrib_os"])              # Obra social
    t["total"] = _r(t["total_ss"] + t["total_os"] + t["art"] + t["scvo"])
    resultado.totales = t
    return resultado


# --------------------------------------------------------------------------
# TXT de importación SICOSS (registro de largo fijo)
# --------------------------------------------------------------------------
# (nombre, longitud, tipo) en el orden del diseño de registro. N = numérico
# (ceros a la izquierda), C = texto (espacios a la derecha), D = importe con
# coma decimal y 2 decimales (ceros a la izquierda). Suma 499 (posiciones 1 a 499, según el diseño publicado).
DISENO_SICOSS = [
    ("cuil", 11, "N"), ("apellido_nombre", 30, "C"), ("conyuge", 1, "N"), ("hijos", 2, "N"),
    ("situacion", 2, "N"), ("condicion", 2, "N"), ("actividad", 3, "N"), ("zona", 2, "N"),
    ("aporte_adicional_ss", 5, "D"), ("modalidad", 3, "N"), ("obra_social", 6, "N"),
    ("adherentes", 2, "N"), ("rem_total", 12, "D"), ("rem_imp1", 12, "D"),
    ("asig_familiares", 9, "D"), ("aporte_voluntario", 9, "D"), ("adicional_os", 9, "D"),
    ("exc_aportes_ss", 9, "D"), ("exc_aportes_os", 9, "D"), ("provincia", 50, "C"),
    ("rem_imp2", 12, "D"), ("rem_imp3", 12, "D"), ("rem_imp4", 12, "D"),
    ("siniestrado", 2, "N"), ("reduccion", 1, "N"), ("recomposicion_lrt", 9, "D"),
    ("tipo_empresa", 1, "N"), ("aporte_adicional_os", 9, "D"), ("regimen", 1, "N"),
    ("sit_rev1", 2, "N"), ("dia_sit_rev1", 2, "N"), ("sit_rev2", 2, "N"), ("dia_sit_rev2", 2, "N"),
    ("sit_rev3", 2, "N"), ("dia_sit_rev3", 2, "N"),
    ("sueldo_adicionales", 12, "D"), ("sac", 12, "D"), ("horas_extras", 12, "D"),
    ("zona_desfavorable", 12, "D"), ("vacaciones", 12, "D"), ("dias_trabajados", 9, "D"),
    ("rem_imp5", 12, "D"), ("convencionado", 1, "N"), ("rem_imp6", 12, "D"),
    ("tipo_operacion", 1, "N"), ("adicionales", 12, "D"), ("premios", 12, "D"),
    ("rem_imp8", 12, "D"), ("rem_imp7", 12, "D"), ("cant_horas_extras", 3, "N"),
    ("no_remunerativos", 12, "D"), ("maternidad", 12, "D"), ("rectificacion", 9, "D"),
    ("rem_imp9", 12, "D"), ("contrib_tarea_dif", 9, "D"), ("horas_trabajadas", 3, "N"),
    ("seguro_vida", 1, "N"), ("detraer_ley27430", 12, "D"), ("incremento_salarial", 12, "D"),
    ("rem_imp11", 12, "D"),
]
LARGO_REGISTRO = sum(n for _, n, _ in DISENO_SICOSS)
assert LARGO_REGISTRO == 499, LARGO_REGISTRO


def _sin_acentos(texto: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def formatear_campo(valor, largo: int, tipo: str) -> str:
    if tipo == "C":
        s = _sin_acentos(str(valor or "")).upper()
        return s[:largo].ljust(largo)
    if tipo == "N":
        s = "".join(ch for ch in str(int(float(valor or 0))) if ch.isdigit())
        if len(s) > largo:
            raise ValueError(f"El valor {valor!r} no entra en {largo} posiciones.")
        return s.zfill(largo)
    s = f"{abs(float(valor or 0)):.2f}".replace(".", ",")
    if len(s) > largo:
        raise ValueError(f"El importe {valor!r} no entra en {largo} posiciones.")
    return s.zfill(largo)


def registro_sicoss(r: ResultadoEmpleado, empleador: dict, cliente_zona: str = "") -> str:
    emp, rem = r.empleado, r.remuneracion
    sin_tope = r.imponible_sin_tope
    valores = {
        "cuil": "".join(ch for ch in emp["cuil"] if ch.isdigit()),
        "apellido_nombre": emp["apellido_nombre"],
        "conyuge": 1 if emp.get("conyuge") else 0,
        "hijos": int(emp.get("hijos") or 0),
        "situacion": int(emp.get("situacion") or 1),
        "condicion": int(emp.get("condicion") or 1),
        "actividad": int(empleador.get("actividad") or 0),
        "zona": int(empleador.get("zona") or 0),
        "modalidad": int(emp.get("modalidad") or 8),
        "obra_social": 0 if emp.get("tipo") == "jubilado" else int(emp.get("obra_social") or 0),
        "adherentes": int(emp.get("adherentes") or 0),
        "rem_total": r.rem_total,
        "rem_imp1": r.imponible, "rem_imp2": r.imponible, "rem_imp3": r.imponible, "rem_imp4": r.imponible,
        "rem_imp5": sin_tope, "rem_imp9": sin_tope,
        "provincia": empleador.get("provincia") or "",
        "tipo_empresa": int(empleador.get("tipo_empresa") or 0),
        "regimen": 1,
        "sit_rev1": int(emp.get("situacion") or 1), "dia_sit_rev1": 1,
        "sueldo_adicionales": _r(rem.get("sueldo")) + _r(rem.get("adicionales")),
        "sac": _r(rem.get("sac")), "horas_extras": _r(rem.get("horas_extras")),
        "zona_desfavorable": _r(rem.get("zona")), "vacaciones": _r(rem.get("vacaciones")),
        "premios": _r(rem.get("premios")),
        "dias_trabajados": int(rem.get("dias") or 30),
        "cant_horas_extras": int(rem.get("cant_horas_extras") or 0),
        "no_remunerativos": r.no_remunerativo,
        "seguro_vida": 1 if empleador.get("scvo") else 0,
        "detraer_ley27430": r.detraccion,
    }
    # Los días trabajados van como "N,00" en el campo de 9 posiciones.
    linea = "".join(formatear_campo(valores.get(n, 0), largo, tipo) for n, largo, tipo in DISENO_SICOSS)
    assert len(linea) == LARGO_REGISTRO, len(linea)
    return linea


def generar_txt_sicoss(resultado: ResultadoLiquidacion, empleador: dict) -> str:
    """Una línea por empleado, terminadas en CRLF (formato de texto de Windows que espera SICOSS)."""
    return "".join(registro_sicoss(r, empleador) + "\r\n" for r in resultado.empleados)


def validar_empleado(emp: dict) -> list:
    """Problemas que impedirían una importación limpia en SICOSS."""
    problemas = []
    cuil = "".join(ch for ch in str(emp.get("cuil", "")) if ch.isdigit())
    if len(cuil) != 11 or not _cuil_valido(cuil):
        problemas.append(f"CUIL {emp.get('cuil')!r} inválido (revisá el número y el dígito verificador).")
    if not str(emp.get("apellido_nombre", "")).strip():
        problemas.append("Falta apellido y nombre.")
    if emp.get("tipo") != "jubilado" and not int(emp.get("obra_social") or 0):
        problemas.append(f"{emp.get('apellido_nombre')}: falta el código de obra social (6 dígitos).")
    return problemas


def _cuil_valido(cuil: str) -> bool:
    pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    resto = sum(int(d) * w for d, w in zip(cuil[:10], pesos)) % 11
    dv = 11 - resto
    dv = 0 if dv == 11 else 9 if dv == 10 else dv
    return dv == int(cuil[10])
