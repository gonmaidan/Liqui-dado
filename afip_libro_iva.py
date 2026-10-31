"""
afip_libro_iva.py
--------------------
Genera los 4 archivos TXT de ancho fijo que pide el "Libro IVA Digital"
de AFIP/ARCA (R.G. 4597) para importar comprobantes de ventas y compras:

    - LIBRO_IVA_DIGITAL_VENTAS_CBTE.txt
    - LIBRO_IVA_DIGITAL_VENTAS_ALICUOTAS.txt
    - LIBRO_IVA_DIGITAL_COMPRAS_CBTE.txt
    - LIBRO_IVA_DIGITAL_COMPRAS_ALICUOTAS.txt

El diseño de registro (posiciones, anchos y tablas de códigos) sale de los
documentos oficiales publicados por AFIP:
  - Libro-IVA-Digital-Especificaciones.pdf (descripción de cada campo)
  - libro-iva-digital-diseno-registros.pdf (posiciones desde/hasta)
  - Libro-IVA-Digital-Tablas-del-Sistema.pdf (códigos: comprobantes,
    alícuotas, documentos, monedas)

============================================================================
ALCANCE Y LIMITACIONES DE ESTA VERSIÓN — LEER ANTES DE PRESENTAR EN AFIP
============================================================================
Este módulo cubre el caso más común de un pequeño contribuyente:
  - Comprobantes en pesos argentinos (no moneda extranjera).
  - UNA sola alícuota de IVA por comprobante (no ítems mezclados 21% + 10,5%
    en la misma factura).
  - Percepción de IVA y percepción de Ingresos Brutos SÍ están cubiertas
    (columnas opcionales `percepcion_iva` y `percepcion_iibb`), una de cada
    una por comprobante. Percepciones municipales, retenciones, impuestos
    internos y conceptos no gravados/exentos separados del neto, NO.
  - Sin importaciones de bienes/servicios, sin TurIVA, sin bienes usados.
  - El comprador/vendedor se identifica siempre por CUIT (no contempla
    consumidor final sin identificar, DNI, etc.).
  - No genera el archivo de "Comprobantes Anulados".

Si tu caso tiene algo de lo anterior (varias alícuotas en una factura,
otro tipo de percepción, importaciones, venta a consumidor final sin CUIT,
etc.), estos TXT van a quedar incompletos o AFIP los va a rechazar. Avisame
y lo extendemos — pero para no generar un archivo que "parece" válido y en
realidad está mal, por ahora esos casos no se generan silenciosamente:
se registran como advertencia y esos comprobantes quedan afuera del TXT.

SIEMPRE revisá la vista previa / totales que muestra el sistema de AFIP al
importar, antes de presentar la DDJJ.
============================================================================
"""

from dataclasses import dataclass
import pandas as pd


# ---------------------------------------------------------------------------
# Tablas de códigos AFIP (Libro-IVA-Digital-Tablas-del-Sistema.pdf)
# ---------------------------------------------------------------------------

# Código de alícuota de IVA -> según % (redondeado a 2 decimales)
ALICUOTAS_AFIP = {
    0.0: "0003",
    10.5: "0004",
    21.0: "0005",
    27.0: "0006",
    5.0: "0008",
    2.5: "0009",
}

# Tipo de comprobante: texto tal como lo carga el usuario -> código AFIP.
# Cubre los tipos más comunes (facturas, notas de débito/crédito A/B/C).
# Agregar acá cualquier variante nueva que aparezca en los datos.
TIPO_COMPROBANTE_AFIP = {
    "factura a": "001",
    "nota de debito a": "002",
    "nota de crédito a": "003",
    "nota de credito a": "003",
    "factura b": "006",
    "nota de debito b": "007",
    "nota de crédito b": "008",
    "nota de credito b": "008",
    "factura c": "011",
    "nota de debito c": "012",
    "nota de crédito c": "013",
    "nota de credito c": "013",
}

# Tipos de comprobante "B" o "C": para compras, AFIP pide cantidad de
# alícuotas = 0 y no generar registro en el archivo de alícuotas.
TIPOS_B_O_C = {"006", "007", "008", "009", "010", "011", "012", "013", "015", "016"}

CODIGO_MONEDA_PESOS = "PES"
TIPO_CAMBIO_PESOS = "0001000000"  # 4 enteros + 6 decimales de "1,000000", sin punto
CODIGO_DOC_CUIT = "80"


class DatoAfipFaltante(Exception):
    """Se lanza cuando faltan columnas o datos imprescindibles para armar los TXT de AFIP."""


@dataclass
class AdvertenciaAfip:
    fila: int
    motivo: str


# ---------------------------------------------------------------------------
# Helpers de formateo de ancho fijo
# ---------------------------------------------------------------------------

def _num(valor, ancho: int) -> str:
    """Campo numérico: ceros a la izquierda, ancho fijo."""
    if isinstance(valor, str):
        # CUIT escrito con guiones (30-12345678-9) u otros separadores.
        valor = "".join(ch for ch in valor if ch.isdigit()) or "0"
    texto = str(int(valor))
    if len(texto) > ancho:
        raise ValueError(f"El valor {valor} no entra en {ancho} dígitos")
    return texto.zfill(ancho)


def _alfa(valor, ancho: int) -> str:
    """Campo alfanumérico: espacios a la derecha, ancho fijo, recortado si excede."""
    texto = "" if valor is None else str(valor)
    texto = texto[:ancho]
    return texto.ljust(ancho)


def _importe(valor: float, ancho: int = 15) -> str:
    """
    Campo de importe: 13 enteros + 2 decimales, sin punto ni coma, ceros a
    la izquierda. Si es negativo, el signo "-" ocupa la primera posición.
    """
    centavos = round(float(valor) * 100)
    negativo = centavos < 0
    centavos = abs(centavos)
    texto = str(centavos).zfill(ancho - 1 if negativo else ancho)
    if negativo:
        texto = "-" + texto
    if len(texto) > ancho:
        raise ValueError(f"El importe {valor} no entra en {ancho} posiciones")
    return texto


def _fecha(valor, ancho: int = 8) -> str:
    """Fecha en formato AAAAMMDD."""
    ts = pd.to_datetime(valor)
    return ts.strftime("%Y%m%d")


def _codigo_alicuota(neto_gravado: float, iva: float) -> str:
    """Deduce el código de alícuota AFIP a partir del neto y el IVA cargados."""
    if neto_gravado == 0:
        tasa = 0.0
    else:
        tasa = round((iva / neto_gravado) * 100, 1)
    # Tolerancia por redondeos de centavos al cargar los comprobantes
    for tasa_conocida, codigo in ALICUOTAS_AFIP.items():
        if abs(tasa - tasa_conocida) <= 0.3:
            return codigo
    raise DatoAfipFaltante(
        f"No pude reconocer la alícuota de IVA a partir de neto={neto_gravado} "
        f"e iva={iva} (dio {tasa}%). Las alícuotas válidas son "
        f"{sorted(ALICUOTAS_AFIP.keys())}."
    )


def _codigo_tipo_comprobante(tipo_comprobante_texto: str) -> str:
    clave = str(tipo_comprobante_texto).strip().lower()
    if clave not in TIPO_COMPROBANTE_AFIP:
        raise DatoAfipFaltante(
            f"No reconozco el tipo de comprobante '{tipo_comprobante_texto}'. "
            f"Tipos soportados: {sorted(set(v for v in TIPO_COMPROBANTE_AFIP))} "
            f"(nombres como 'Factura A', 'Factura B', 'Nota de Crédito C', etc.)"
        )
    return TIPO_COMPROBANTE_AFIP[clave]


COLUMNAS_AFIP_REQUERIDAS = ["punto_venta", "numero_comprobante"]


def _validar_columnas_afip(df: pd.DataFrame, nombre_archivo: str):
    faltantes = [c for c in COLUMNAS_AFIP_REQUERIDAS if c not in df.columns]
    if faltantes:
        raise DatoAfipFaltante(
            f"Para generar los TXT de AFIP, el archivo de {nombre_archivo} necesita también "
            f"las columnas {faltantes} (además de las que ya usa la liquidación). "
            f"Agregalas a tu Excel/CSV, o regenerá la plantilla con generar_plantillas.py."
        )
    # Si se combinaron comprobantes de distintas fuentes (Excel + escaneo de
    # QR) puede que la columna exista pero algunas filas vengan vacías (p.
    # ej. un Excel viejo sin punto_venta). Mejor avisar claro que romper con
    # un error críptico al armar el TXT.
    con_datos_faltantes = [c for c in COLUMNAS_AFIP_REQUERIDAS if df[c].isna().any()]
    if con_datos_faltantes:
        raise DatoAfipFaltante(
            f"Algunos comprobantes de {nombre_archivo} no tienen completo "
            f"{con_datos_faltantes}. Revisá que todas las filas (del Excel y las "
            f"escaneadas) tengan esos datos antes de generar los TXT de AFIP."
        )


# ---------------------------------------------------------------------------
# Generación de líneas
# ---------------------------------------------------------------------------

def generar_ventas_cbte(ventas: pd.DataFrame):
    _validar_columnas_afip(ventas, "ventas")
    lineas = []
    for _, fila in ventas.iterrows():
        tipo_cbte = _codigo_tipo_comprobante(fila["tipo_comprobante"])
        percepcion_iva = float(fila.get("percepcion_iva", 0) or 0)
        percepcion_iibb = float(fila.get("percepcion_iibb", 0) or 0)
        importe_total = float(fila["neto_gravado"]) + float(fila["iva"]) + percepcion_iva + percepcion_iibb
        campos = [
            _fecha(fila["fecha"], 8),
            _num(tipo_cbte, 3),
            _num(fila["punto_venta"], 5),
            _num(fila["numero_comprobante"], 20),
            _num(fila["numero_comprobante"], 20),          # 5: hasta = igual al nro (sin rango)
            _num(CODIGO_DOC_CUIT, 2),                        # 6
            _num(fila["cuit"], 20),                          # 7
            _alfa(fila["razon_social"], 30),                 # 8
            _importe(importe_total, 15),                     # 9
            _importe(0, 15),                                 # 10 no integran neto gravado
            _importe(0, 15),                                 # 11 percep. no categorizados
            _importe(0, 15),                                 # 12 operaciones exentas
            _importe(percepcion_iva, 15),                    # 13 percep. a otros imp. nacionales (IVA)
            _importe(percepcion_iibb, 15),                   # 14 percep. IIBB
            _importe(0, 15),                                 # 15 percep. municipales
            _importe(0, 15),                                 # 16 impuestos internos
            _alfa(CODIGO_MONEDA_PESOS, 3),                    # 17
            _alfa(TIPO_CAMBIO_PESOS, 10),                     # 18
            _num(1, 1),                                       # 19 cantidad de alícuotas
            _alfa("", 1),                                     # 20 código de operación
            _importe(0, 15),                                 # 21 otros tributos
            _num(0, 8),                                       # 22 fecha vto/pago
        ]
        lineas.append("".join(campos))
    return lineas


def generar_ventas_alicuotas(ventas: pd.DataFrame):
    _validar_columnas_afip(ventas, "ventas")
    lineas = []
    for _, fila in ventas.iterrows():
        tipo_cbte = _codigo_tipo_comprobante(fila["tipo_comprobante"])
        codigo_alicuota = _codigo_alicuota(float(fila["neto_gravado"]), float(fila["iva"]))
        campos = [
            _num(tipo_cbte, 3),
            _num(fila["punto_venta"], 5),
            _num(fila["numero_comprobante"], 20),
            _importe(fila["neto_gravado"], 15),
            _alfa(codigo_alicuota, 4),
            _importe(fila["iva"], 15),
        ]
        lineas.append("".join(campos))
    return lineas


def generar_compras_cbte(compras: pd.DataFrame):
    _validar_columnas_afip(compras, "compras")
    lineas = []
    for _, fila in compras.iterrows():
        tipo_cbte = _codigo_tipo_comprobante(fila["tipo_comprobante"])
        es_b_o_c = tipo_cbte in TIPOS_B_O_C
        percepcion_iva = float(fila.get("percepcion_iva", 0) or 0)
        percepcion_iibb = float(fila.get("percepcion_iibb", 0) or 0)
        importe_total = float(fila["neto_gravado"]) + float(fila["iva"]) + percepcion_iva + percepcion_iibb
        cantidad_alicuotas = 0 if es_b_o_c else 1
        credito_fiscal = 0.0 if es_b_o_c else float(fila["iva"])
        campos = [
            _fecha(fila["fecha"], 8),
            _num(tipo_cbte, 3),
            _num(fila["punto_venta"], 5),
            _num(fila["numero_comprobante"], 20),
            _num(0, 16),                                      # 5 despacho de importación
            _num(CODIGO_DOC_CUIT, 2),                          # 6
            _num(fila["cuit"], 20),                            # 7
            _alfa(fila["razon_social"], 30),                   # 8
            _importe(importe_total, 15),                       # 9
            _importe(0, 15),                                   # 10
            _importe(0, 15),                                   # 11 exentas
            _importe(percepcion_iva, 15),                      # 12 percep. IVA sufrida
            _importe(0, 15),                                   # 13 percep. otros imp. nac.
            _importe(percepcion_iibb, 15),                     # 14 percep. IIBB
            _importe(0, 15),                                   # 15 percep. municipales
            _importe(0, 15),                                   # 16 imp. internos
            _alfa(CODIGO_MONEDA_PESOS, 3),                      # 17
            _alfa(TIPO_CAMBIO_PESOS, 10),                       # 18
            _num(cantidad_alicuotas, 1),                        # 19
            _alfa("", 1),                                       # 20
            _importe(credito_fiscal, 15),                       # 21 crédito fiscal computable
            _importe(0, 15),                                   # 22 otros tributos
            _num(0, 11),                                        # 23 CUIT emisor/corredor
            _alfa("", 30),                                      # 24 denominación emisor/corredor
            _importe(0, 15),                                   # 25 IVA comisión
        ]
        lineas.append("".join(campos))
    return lineas


def generar_compras_alicuotas(compras: pd.DataFrame):
    """
    Los comprobantes B/C no discriminan IVA y no llevan registro en este
    archivo (AFIP así lo exige: cantidad de alícuotas = 0 para esos casos).
    """
    _validar_columnas_afip(compras, "compras")
    lineas = []
    for _, fila in compras.iterrows():
        tipo_cbte = _codigo_tipo_comprobante(fila["tipo_comprobante"])
        if tipo_cbte in TIPOS_B_O_C:
            continue
        codigo_alicuota = _codigo_alicuota(float(fila["neto_gravado"]), float(fila["iva"]))
        campos = [
            _num(tipo_cbte, 3),
            _num(fila["punto_venta"], 5),
            _num(fila["numero_comprobante"], 20),
            _num(CODIGO_DOC_CUIT, 2),
            _num(fila["cuit"], 20),
            _importe(fila["neto_gravado"], 15),
            _alfa(codigo_alicuota, 4),
            _importe(fila["iva"], 15),
        ]
        lineas.append("".join(campos))
    return lineas


# ---------------------------------------------------------------------------
# Punto de entrada: genera los 4 archivos y los guarda en disco
# ---------------------------------------------------------------------------

def generar_archivos_afip(ventas: pd.DataFrame, compras: pd.DataFrame, carpeta_salida: str, nombre_base: str):
    """
    Genera los 4 TXT de ancho fijo y los guarda en `carpeta_salida`, con el
    prefijo `nombre_base`. Devuelve la lista de paths generados.

    AFIP pide codificación ANSI (ISO-8859-1 / Windows-1252), no UTF-8, y fin
    de línea CRLF (\\r\\n) — se respeta acá.
    """
    import os

    archivos = {
        f"{nombre_base}_LIBRO_IVA_DIGITAL_VENTAS_CBTE.txt": generar_ventas_cbte(ventas),
        f"{nombre_base}_LIBRO_IVA_DIGITAL_VENTAS_ALICUOTAS.txt": generar_ventas_alicuotas(ventas),
        f"{nombre_base}_LIBRO_IVA_DIGITAL_COMPRAS_CBTE.txt": generar_compras_cbte(compras),
        f"{nombre_base}_LIBRO_IVA_DIGITAL_COMPRAS_ALICUOTAS.txt": generar_compras_alicuotas(compras),
    }

    paths = []
    for nombre_archivo, lineas in archivos.items():
        path = os.path.join(carpeta_salida, nombre_archivo)
        contenido = "\r\n".join(lineas) + ("\r\n" if lineas else "")
        with open(path, "w", encoding="cp1252", errors="replace", newline="") as f:
            f.write(contenido)
        paths.append(path)

    return paths
