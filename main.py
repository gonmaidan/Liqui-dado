"""
main.py
--------
Punto de entrada. Corré este script pasándole los archivos de ventas y
compras de un cliente (cargados a mano con la plantilla, o exportados
de AFIP) y te genera:
  - salidas/<cliente>_<periodo>_liquidacion.xlsx
  - salidas/<cliente>_<periodo>_resumen.pdf

Ejemplos:
    python main.py --cliente "Kiosco Don Jose" --periodo "2026-08" \
        --ventas datos/ventas_agosto.csv --compras datos/compras_agosto.csv

    python main.py --cliente "Panaderia Sol" --periodo "2026-08" \
        --ventas datos/ventas.xlsx --compras datos/compras.xlsx --estudio "Nombre del Estudio"
"""

import argparse
import os
import sys

from calculo_iva import cargar_comprobantes, calcular_liquidacion
from reporte_excel import generar_excel
from reporte_pdf import generar_pdf
from afip_libro_iva import generar_archivos_afip, DatoAfipFaltante


def main():
    parser = argparse.ArgumentParser(description="Genera la liquidación de IVA de un cliente.")
    parser.add_argument("--cliente", required=True, help="Nombre del cliente, para el nombre de archivo y el PDF")
    parser.add_argument("--periodo", required=True, help='Período fiscal, ej: "2026-08"')
    parser.add_argument("--ventas", required=True, help="Archivo CSV o XLSX con los comprobantes de ventas")
    parser.add_argument("--compras", required=True, help="Archivo CSV o XLSX con los comprobantes de compras")
    parser.add_argument("--estudio", default="Estudio Contable", help="Nombre a mostrar en el PDF")
    parser.add_argument("--salida", default="salidas", help="Carpeta donde guardar los archivos generados")
    args = parser.parse_args()

    os.makedirs(args.salida, exist_ok=True)

    try:
        ventas = cargar_comprobantes(args.ventas)
        compras = cargar_comprobantes(args.compras)
    except ValueError as e:
        print(f"\nError al leer los archivos: {e}\n", file=sys.stderr)
        sys.exit(1)

    resultado = calcular_liquidacion(args.periodo, ventas, compras)

    nombre_base = f"{args.cliente.strip().replace(' ', '_')}_{args.periodo}"
    path_excel = os.path.join(args.salida, f"{nombre_base}_liquidacion.xlsx")
    path_pdf = os.path.join(args.salida, f"{nombre_base}_resumen.pdf")

    generar_excel(resultado, ventas, compras, path_excel)
    generar_pdf(resultado, args.cliente, path_pdf, estudio=args.estudio)

    generados = [path_excel, path_pdf]
    try:
        generados += generar_archivos_afip(ventas, compras, args.salida, nombre_base)
    except DatoAfipFaltante as e:
        print(f"\nAviso: no se generaron los TXT de AFIP ({e})\n", file=sys.stderr)

    print(f"\nCliente: {args.cliente}  |  Período: {args.periodo}")
    print(f"  Débito Fiscal:  $ {resultado.debito_fiscal:,.2f}")
    print(f"  Crédito Fiscal: $ {resultado.credito_fiscal:,.2f}")
    print(f"  Saldo:          $ {resultado.saldo:,.2f}  "
          f"({'A PAGAR' if resultado.saldo > 0 else 'A FAVOR' if resultado.saldo < 0 else 'SIN SALDO'})")
    print("\nGenerado:")
    for path in generados:
        print(f"  - {path}")
    print()


if __name__ == "__main__":
    main()
