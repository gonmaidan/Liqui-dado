"""
calculo_iva.py
----------------
Lógica de cálculo de la liquidación de IVA a partir de los comprobantes
de ventas y compras de un cliente.

No depende de Excel/PDF: solo trabaja con DataFrames de pandas, para que
se pueda testear y reutilizar desde otros scripts (Ganancias, IIBB, etc.)
"""

from dataclasses import dataclass
import pandas as pd


# Columnas mínimas que el script necesita encontrar en cada archivo.
# Si tu archivo (por ej. exportado de AFIP) usa otros nombres de columna,
# mapealos en COLUMNAS_ALIAS más abajo en vez de tocar el resto del código.
COLUMNAS_REQUERIDAS = ["fecha", "tipo_comprobante", "cuit", "razon_social", "neto_gravado", "iva"]

# Columnas opcionales: si no están en el archivo, se completan en 0 y no
# rompen nada de lo que ya funciona. No todos los clientes tienen
# percepciones, así que no tiene sentido pedirlas siempre.
COLUMNAS_OPCIONALES = ["percepcion_iva", "percepcion_iibb"]

# Alias frecuentes que trae el "Libro IVA Digital" / "Mis Comprobantes" de AFIP.
# Agregá acá cualquier nombre de columna nuevo que te aparezca al importar.
COLUMNAS_ALIAS = {
    "fecha emisión": "fecha",
    "fecha de emision": "fecha",
    "tipo": "tipo_comprobante",
    "tipo de comprobante": "tipo_comprobante",
    "nro. doc. receptor": "cuit",
    "nro doc receptor": "cuit",
    "cuit receptor": "cuit",
    "denominación receptor": "razon_social",
    "denominacion receptor": "razon_social",
    "denominación emisor": "razon_social",
    "imp. neto gravado": "neto_gravado",
    "imp neto gravado": "neto_gravado",
    "iva liquidado": "iva",
}


@dataclass
class ResultadoIVA:
    periodo: str
    debito_fiscal: float          # IVA de las ventas
    credito_fiscal: float         # IVA de las compras
    saldo: float                  # débito - crédito (positivo = a pagar, negativo = a favor)
    total_ventas_neto: float
    total_compras_neto: float
    cant_comprobantes_ventas: int
    cant_comprobantes_compras: int


def _normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """Pasa las columnas a minúscula/sin espacios y aplica los alias conocidos."""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.rename(columns=COLUMNAS_ALIAS)
    return df


def cargar_comprobantes(path: str) -> pd.DataFrame:
    """
    Carga un archivo de comprobantes (CSV o Excel) y normaliza sus columnas.
    Sirve tanto para un archivo cargado a mano con la plantilla como para
    un export de AFIP (Libro IVA Digital / Mis Comprobantes).
    """
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path, encoding="utf-8-sig")
    else:
        df = pd.read_excel(path)

    df = _normalizar_columnas(df)

    faltantes = [c for c in COLUMNAS_REQUERIDAS if c not in df.columns]
    if faltantes:
        raise ValueError(
            f"Al archivo '{path}' le faltan las columnas {faltantes}. "
            f"Columnas encontradas: {list(df.columns)}. "
            f"Si tu archivo usa otros nombres, agregalos a COLUMNAS_ALIAS en calculo_iva.py."
        )

    # Descarto filas vacías o de leyenda (como las que trae la plantilla:
    # "↑ La fila amarilla es un EJEMPLO...", la fila en blanco que separa,
    # etc.) — se reconocen porque no tienen nada cargado en tipo_comprobante,
    # que es un dato que un comprobante real siempre tiene.
    df["tipo_comprobante"] = df["tipo_comprobante"].apply(
        lambda v: str(v).strip() if pd.notna(v) else ""
    )
    df = df[df["tipo_comprobante"] != ""].reset_index(drop=True)

    # Las opcionales que no vinieron en el archivo se crean en 0, para que
    # el resto del código (y afip_libro_iva.py) las pueda usar siempre sin
    # tener que preguntar si existen.
    for col in COLUMNAS_OPCIONALES:
        if col not in df.columns:
            df[col] = 0.0

    # Limpieza numérica básica: por si vienen como texto con comas/puntos argentinos
    for col in ("neto_gravado", "iva", *COLUMNAS_OPCIONALES):
        if df[col].dtype == object:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(".", "", regex=False)   # separador de miles
                .str.replace(",", ".", regex=False)  # coma decimal -> punto
            )
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    return df


COLUMNAS_COMPROBANTE = [
    "fecha", "tipo_comprobante", "punto_venta", "numero_comprobante",
    "cuit", "razon_social", "neto_gravado", "iva",
    "percepcion_iva", "percepcion_iibb",
]


def dataframe_vacio() -> pd.DataFrame:
    """DataFrame con las columnas correctas pero sin filas — para cuando no se sube Excel."""
    return pd.DataFrame(columns=COLUMNAS_COMPROBANTE)


def comprobantes_a_dataframe(comprobantes) -> pd.DataFrame:
    """
    Convierte una lista de objetos Comprobante (de models.py, cargados por
    escaneo de QR) al mismo formato de DataFrame que usan calcular_liquidacion
    y afip_libro_iva.py — así da igual si un comprobante vino de un Excel
    subido a mano o de un QR escaneado.
    """
    if not comprobantes:
        return dataframe_vacio()
    filas = [
        {
            "fecha": c.fecha,
            "tipo_comprobante": c.tipo_comprobante,
            "punto_venta": c.punto_venta,
            "numero_comprobante": c.numero_comprobante,
            "cuit": c.cuit,
            "razon_social": c.razon_social,
            "neto_gravado": float(c.neto_gravado),
            "iva": float(c.iva),
            "percepcion_iva": float(c.percepcion_iva or 0),
            "percepcion_iibb": float(c.percepcion_iibb or 0),
        }
        for c in comprobantes
    ]
    return pd.DataFrame(filas, columns=COLUMNAS_COMPROBANTE)


def _clave_comprobante(df: pd.DataFrame) -> pd.Series:
    """Clave (tipo, punto de venta, número) para detectar el mismo comprobante en dos fuentes."""
    tipo = df["tipo_comprobante"].astype(str).str.strip().str.lower()
    pv = pd.to_numeric(df["punto_venta"], errors="coerce").fillna(-1).astype(int).astype(str)
    nro = pd.to_numeric(df["numero_comprobante"], errors="coerce").fillna(-1).astype(int).astype(str)
    return tipo + "|" + pv + "|" + nro


def combinar_sin_duplicados(excel_df: pd.DataFrame, escaneados_df: pd.DataFrame):
    """
    Une el Excel subido con lo escaneado. Si un comprobante escaneado ya está
    en el Excel (mismo tipo, punto de venta y número), se descarta el
    escaneado para no contarlo dos veces (ej: se re-subió un Excel exportado).
    Devuelve (dataframe_combinado, cantidad_de_escaneados_omitidos).
    """
    if excel_df.empty or escaneados_df.empty:
        return pd.concat([excel_df, escaneados_df], ignore_index=True), 0
    claves_excel = set(_clave_comprobante(excel_df))
    repetidos = _clave_comprobante(escaneados_df).isin(claves_excel)
    omitidos = int(repetidos.sum())
    return pd.concat([excel_df, escaneados_df[~repetidos]], ignore_index=True), omitidos


def calcular_liquidacion(periodo: str, ventas: pd.DataFrame, compras: pd.DataFrame) -> ResultadoIVA:
    """Calcula débito fiscal, crédito fiscal y saldo a partir de ventas y compras ya cargadas."""
    debito_fiscal = round(float(ventas["iva"].sum()), 2)
    credito_fiscal = round(float(compras["iva"].sum()), 2)
    saldo = round(debito_fiscal - credito_fiscal, 2)

    return ResultadoIVA(
        periodo=periodo,
        debito_fiscal=debito_fiscal,
        credito_fiscal=credito_fiscal,
        saldo=saldo,
        total_ventas_neto=round(float(ventas["neto_gravado"].sum()), 2),
        total_compras_neto=round(float(compras["neto_gravado"].sum()), 2),
        cant_comprobantes_ventas=len(ventas),
        cant_comprobantes_compras=len(compras),
    )
