"""
percepciones_pdf.py
---------------------
Intenta leer, del texto de un PDF de factura, el neto gravado, el IVA y las
percepciones (IVA e IIBB por jurisdicción), para proponerlos ya cargados.

Cada proveedor arma su PDF de forma distinta, así que NO se confía en la
posición ni en el formato de la factura. En su lugar se usa el importe total
del QR como ancla y se busca, entre todos los importes que aparecen en el
texto, una combinación que cierre exacto:

    total = neto + IVA (neto x alícuota) + percepciones

El neto y el IVA tienen que estar impresos en la factura; las percepciones
salen de los importes que están junto a rótulos de "percepción / IIBB / ...".
Si no hay una combinación que cierre, no se propone nada (se carga a mano).
"""

import itertools
import re
from dataclasses import dataclass, field
from typing import Optional

TOLERANCIA_REDONDEO = 0.10  # centavos de diferencia tolerados entre el detalle y el total

# Número con separadores de miles/decimales en cualquiera de los dos formatos
# (1.234,56 / 1,234.56 / 1234.5 / 1234,56). Los enteros sueltos se ignoran.
_NUMERO = re.compile(r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})*[.,]\d{1,2}|\d+[.,]\d{1,2})(?![\d])(\s*%)?")

_RE_ETIQUETA = re.compile(
    r"perc|ii\.?\s?bb|iibb|ing(?:resos)?\.?\s*brutos|otros\s+tributos|impuesto\s+pais|\brg\s?\d{3,4}",
    re.IGNORECASE,
)
_RE_IIBB = re.compile(r"ii\.?\s?bb|iibb|ing(?:resos)?\.?\s*brutos", re.IGNORECASE)
_RE_IVA = re.compile(r"\biva\b", re.IGNORECASE)

# (regex, nombre). Las más específicas primero: "Cdad Auton BsAs" es CABA, no Buenos Aires.
_JURISDICCIONES = [
    (r"cdad|ciudad|caba|c\.a\.b\.a|capital\s+federal|agip", "CABA"),
    (r"bs\.?\s?as|buenos\s+aires|\bba\b|bsas|\bpba\b|arba", "Buenos Aires"),
    (r"mendoza|mdza|\bmza\b", "Mendoza"),
    (r"c[oó]rdoba|\bcba\b|\bcor\b", "Córdoba"),
    (r"santa\s+fe|\bs\.?\s?fe\b", "Santa Fe"),
    (r"tucum[aá]n|\btuc\b", "Tucumán"),
    (r"entre\s+r[ií]os|\ber\b", "Entre Ríos"),
    (r"r[ií]o\s+negro|\br\.?\s?n\.?(?=\W|$)", "Río Negro"),
    (r"neuqu[eé]n|\bnqn\b", "Neuquén"),
    (r"salta", "Salta"),
    (r"jujuy", "Jujuy"),
    (r"chaco", "Chaco"),
    (r"corrientes", "Corrientes"),
    (r"misiones", "Misiones"),
    (r"formosa", "Formosa"),
    (r"catamarca", "Catamarca"),
    (r"la\s+rioja", "La Rioja"),
    (r"san\s+juan", "San Juan"),
    (r"san\s+luis", "San Luis"),
    (r"la\s+pampa", "La Pampa"),
    (r"chubut", "Chubut"),
    (r"santa\s+cruz", "Santa Cruz"),
    (r"tierra\s+del\s+fuego", "Tierra del Fuego"),
    (r"santiago\s+del\s+estero|\bsgo\b", "Santiago del Estero"),
]


@dataclass
class Percepciones:
    alicuota: float
    neto: float
    iva: float
    percepcion_iva: float
    iibb: list = field(default_factory=list)  # [(jurisdiccion, importe)]
    diferencia: float = 0.0  # lo que el detalle difiere del total de percepciones (centavos de redondeo)


def _a_numero(texto: str) -> float:
    """'1.234,56' / '1,234.56' / '182906.8' -> float."""
    t = texto.strip()
    if "." in t and "," in t:
        decimal = "." if t.rfind(".") > t.rfind(",") else ","
    elif "," in t or "." in t:
        sep = "," if "," in t else "."
        # un único separador seguido de 1-2 dígitos es decimal; de 3, miles
        decimal = sep if len(t.split(sep)[-1]) <= 2 else None
    else:
        decimal = None
    if decimal:
        miles = "." if decimal == "," else ","
        t = t.replace(miles, "").replace(decimal, ".")
    else:
        t = t.replace(".", "").replace(",", "")
    return float(t)


def _jurisdiccion(etiqueta: str) -> Optional[str]:
    for patron, nombre in _JURISDICCIONES:
        if re.search(patron, etiqueta, re.IGNORECASE):
            return nombre
    return None


def _tokens(texto: str):
    """[(valor, termina_en_porcentaje)] de una línea."""
    return [(_a_numero(m.group(1)), bool(m.group(2))) for m in _NUMERO.finditer(texto)]


_RE_MONEDA = re.compile(r"\b(?:ars|usd|u\$s|pesos)\b|[$]", re.IGNORECASE)


def _sin_numeros(texto: str) -> str:
    """Quita importes y símbolos de moneda ("1.234,56 ARS" -> ""), dejando solo el rótulo."""
    return re.sub(r"[\s\-:]+$", "", _RE_MONEDA.sub(" ", _NUMERO.sub(" ", texto))).strip()


def _extraer(texto: str):
    """Devuelve (todos_los_importes, candidatas) donde candidatas = [(etiqueta, importe)]."""
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    todos, candidatas, vistas = set(), [], set()

    for i, linea in enumerate(lineas):
        for valor, es_pct in _tokens(linea):
            if not es_pct and valor > 0:
                todos.add(round(valor, 2))

        # Una línea puede traer varios renglones: "IIBB BA 0.10% 4944.45 - IIBB CABA 0.50% 24722.21".
        for segmento in re.split(r"\s+-\s+", linea):
            toks = [v for v, pct in _tokens(segmento) if not pct]
            etiqueta = _sin_numeros(segmento)
            if toks and re.search(r"[A-Za-z]", etiqueta):
                if _RE_ETIQUETA.search(etiqueta) and toks[-1] > 0:
                    clave = (i, etiqueta)
                    if clave not in vistas:
                        vistas.add(clave)
                        candidatas.append((etiqueta, round(toks[-1], 2)))
            elif toks and not re.search(r"[A-Za-z]", etiqueta):
                # Línea solo con números: el rótulo es la última línea de texto de arriba (hasta 3 líneas).
                for atras in range(1, 4):
                    if i - atras < 0:
                        break
                    previa = lineas[i - atras]
                    if re.search(r"[A-Za-z]", _sin_numeros(previa)):
                        etiqueta_prev = _sin_numeros(previa)
                        if _RE_ETIQUETA.search(etiqueta_prev) and toks[-1] > 0:
                            clave = (i - atras, etiqueta_prev)  # un rótulo, un importe (el último)
                            candidatas = [c for c in candidatas if c[2:] != (clave,)]
                            candidatas.append((etiqueta_prev, round(toks[-1], 2), clave))
                        break
    candidatas = [(c[0], c[1]) for c in candidatas]
    return todos, candidatas


def _hay(valor: float, importes: set, tol: float = 0.011) -> Optional[float]:
    for x in importes:
        if abs(x - valor) <= tol:
            return x
    return None


def detectar(texto: str, total: float, alicuota_elegida: float, alicuotas: list) -> Optional[Percepciones]:
    """
    Busca neto, IVA y percepciones que cierren contra `total`. Prueba primero la
    alícuota elegida y después las otras (por si la factura es de otra alícuota).
    Devuelve None si no encuentra una combinación confiable.
    """
    if not texto or total <= 0:
        return None
    importes, candidatas = _extraer(texto)
    importes.discard(round(total, 2))  # el total no puede ser el neto ni el IVA

    orden = [alicuota_elegida] + [a for a in alicuotas if a != alicuota_elegida and a > 0]
    for alicuota in orden:
        mejor = None
        for neto in sorted(importes, reverse=True):
            iva = _hay(round(neto * alicuota / 100, 2), importes)
            if iva is None or iva == neto:
                continue
            percep_total = round(total - neto - iva, 2)
            if percep_total < -0.011 or percep_total > total * 0.35:
                continue
            solucion = _itemizar(percep_total, candidatas, importes_prohibidos={neto, iva})
            if solucion is None:
                continue
            puntaje, items, dif = solucion
            if mejor is None or puntaje > mejor[0]:
                mejor = (puntaje, Percepciones(
                    alicuota=alicuota, neto=neto, iva=iva,
                    percepcion_iva=round(sum(v for t, _, v in items if t == "iva"), 2),
                    iibb=[(j or "Sin especificar", v) for t, j, v in items if t == "iibb"],
                    diferencia=dif,
                ))
        if mejor:
            return mejor[1]
    return None


def _itemizar(percep_total: float, candidatas: list, importes_prohibidos: set):
    """
    Busca qué importes rotulados suman `percep_total`. Devuelve (puntaje, items, diferencia)
    con items = [(tipo, jurisdiccion, importe)], o None. Con total 0 → sin percepciones.
    """
    if abs(percep_total) <= 0.011:
        return (100, [], 0.0)

    clasificadas = []
    for etiqueta, importe in candidatas[:16]:
        if importe in importes_prohibidos:
            continue
        if _RE_IIBB.search(etiqueta):
            clasificadas.append(("iibb", _jurisdiccion(etiqueta), importe, etiqueta))
        elif _RE_IVA.search(etiqueta) and re.search(r"perc", etiqueta, re.IGNORECASE):
            clasificadas.append(("iva", None, importe, etiqueta))
        else:
            clasificadas.append(("otro", None, importe, etiqueta))

    mejor = None
    for tam in range(1, min(6, len(clasificadas)) + 1):
        for combo in itertools.combinations(clasificadas, tam):
            suma = round(sum(c[2] for c in combo), 2)
            dif = round(percep_total - suma, 2)
            if abs(dif) > TOLERANCIA_REDONDEO:
                continue
            if any(c[0] == "otro" for c in combo) and tam > 1:
                continue  # los totales genéricos ("Percepciones", "Otros tributos") solo valen solos
            conocidas = sum(1 for c in combo if c[0] == "iva" or (c[0] == "iibb" and c[1]))
            if tam == 1 and combo[0][0] == "otro":
                conocidas = 0
            exactitud = 1 if dif == 0 else 0
            puntaje = conocidas * 10 + exactitud * 5 + tam
            if mejor is None or puntaje > mejor[0]:
                items = [(c[0] if c[0] != "otro" else "iibb", c[1], c[2]) for c in combo]
                mejor = (puntaje, items, dif)
    return mejor
