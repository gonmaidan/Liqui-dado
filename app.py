"""
app.py
--------
app-liquidar como servicio hospedado (multi-tenant): cada estudio contable
inicia sesión y solo ve y liquida a sus propios clientes.

Variables de entorno (ver .env.example):
  SECRET_KEY    - clave para firmar la sesión (obligatoria en producción)
  DATABASE_URL  - conexión a Postgres (Neon). Si no está, usa un
                  sqlite local (liqui_dado.db) para desarrollo.

Para desarrollo local:
    flask --app app init-db          # crea las tablas
    flask --app app crear-admin      # crea tu usuario de administrador
    python app.py                    # corre en http://127.0.0.1:5050

En Render, el proceso lo levanta gunicorn (ver Procfile), no este bloque
__main__.
"""

import io
import os
import zipfile

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, render_template, request, send_file, flash, redirect, url_for, abort
from flask_login import current_user

load_dotenv()

from extensions import db, login_manager
from obligaciones import obligaciones_para
from models import Estudio, Admin, Cliente, Liquidacion, Comprobante
from auth import auth_bp, estudio_required
from admin import admin_bp
from clientes import clientes_bp
from escaner import escaner_bp
from vencimientos import vencimientos_bp
from f931 import f931_bp
from cm import cm_bp

from calculo_iva import cargar_comprobantes, calcular_liquidacion, dataframe_vacio, comprobantes_a_dataframe, combinar_sin_duplicados
from reporte_excel import generar_excel
from reporte_pdf import generar_pdf
from afip_libro_iva import generar_archivos_afip, DatoAfipFaltante

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "_tmp_web")
os.makedirs(TMP_DIR, exist_ok=True)


def crear_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "cambiar-en-produccion-por-una-clave-larga-random")

    database_url = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'liqui_dado.db')}")
    # Render/Neon a veces dan la URL con el prefijo viejo "postgres://"; SQLAlchemy 2.x pide "postgresql://".
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    # Si la URL trae un driver explícito (ej. "postgresql+psycopg://"), la dejamos con el que
    # tenemos instalado (psycopg2, ver requirements.txt).
    # Las versiones nuevas de SQLAlchemy usan "psycopg" (v3) cuando la URL es solo "postgresql://";
    # nosotros instalamos psycopg2, así que lo pedimos de forma explícita.
    for prefijo in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgresql+asyncpg://", "postgresql://"):
        if database_url.startswith(prefijo):
            database_url = "postgresql+psycopg2://" + database_url[len(prefijo):]
            break
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

    db.init_app(app)
    login_manager.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(clientes_bp)
    app.register_blueprint(escaner_bp)
    app.register_blueprint(vencimientos_bp)
    app.register_blueprint(f931_bp)
    app.register_blueprint(cm_bp)

    registrar_comandos_cli(app)
    registrar_rutas_liquidacion(app)

    # En el plan gratis de Render no hay Shell/SSH, así que no se puede
    # correr `flask init-db` / `flask crear-admin` a mano ahí. Para no
    # depender de eso: al arrancar, si hace falta, se crean las tablas
    # solas, y si hay credenciales de admin en variables de entorno y
    # todavía no existe ningún admin, se crea automáticamente.
    with app.app_context():
        db.create_all()
        _agregar_columnas_faltantes()
        _bootstrap_admin_inicial()

    return app


def _agregar_columnas_faltantes():
    """
    db.create_all() crea tablas nuevas pero no agrega columnas a tablas que
    ya existían. Para las bases creadas con versiones anteriores, agrega acá
    las columnas nuevas (funciona igual en SQLite y en Postgres) y clasifica
    los vencimientos viejos que todavía no tienen obligación.
    """
    from sqlalchemy import inspect, text
    from models import Vencimiento
    from obligaciones import detectar_obligacion

    inspector = inspect(db.engine)
    tablas = set(inspector.get_table_names())
    pendientes = [
        ("clientes", "tipo_persona", "VARCHAR(10) NOT NULL DEFAULT 'fisica'"),
        ("vencimientos", "obligacion", "VARCHAR(30)"),
    ]
    for tabla, columna, ddl in pendientes:
        if tabla in tablas and columna not in {c["name"] for c in inspector.get_columns(tabla)}:
            db.session.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {ddl}"))
            db.session.commit()

    for v in Vencimiento.query.filter(Vencimiento.obligacion.is_(None)).all():
        v.obligacion = detectar_obligacion(v.impuesto)
    db.session.commit()


def _bootstrap_admin_inicial():
    """
    Crea el primer Admin desde variables de entorno, solo si todavía no
    existe ninguno — así es seguro que esto corra en cada arranque/deploy,
    nunca pisa una contraseña ya puesta a mano.
    """
    if Admin.query.first() is not None:
        return

    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")
    if not email or not password:
        return

    nombre = os.environ.get("ADMIN_NOMBRE", "Administrador")
    admin = Admin(email=email.strip().lower(), nombre=nombre)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    print(f"Admin inicial creado desde variables de entorno: {email}")


# Impuestos que ya se pueden liquidar -> endpoint de su formulario.
LIQUIDABLES = {"iva": "liquidar_iva", "f931": "f931.inicio", "convenio_multilateral": "cm.inicio"}


def _cliente_propio_o_404(cliente_id: int) -> Cliente:
    cliente = db.session.get(Cliente, cliente_id)
    if cliente is None or cliente.estudio_id != current_user.id:
        abort(404)
    return cliente


def registrar_rutas_liquidacion(app: Flask):

    @app.route("/", methods=["GET"])
    @estudio_required
    def index():
        clientes = (
            Cliente.query.filter_by(estudio_id=current_user.id)
            .order_by(Cliente.razon_social)
            .all()
        )
        return render_template("index.html", clientes=clientes)

    @app.route("/liquidar/<int:cliente_id>", methods=["GET"])
    @estudio_required
    def elegir_impuesto(cliente_id):
        """Paso 2: con el cliente ya elegido, qué impuesto se va a trabajar."""
        cliente = _cliente_propio_o_404(cliente_id)
        tildadas = cliente.claves_obligaciones
        opciones = [
            {
                "clave": clave, "nombre": nombre,
                "disponible": clave in LIQUIDABLES,
                "url": url_for(LIQUIDABLES[clave], cliente_id=cliente.id) if clave in LIQUIDABLES else None,
            }
            for clave, nombre in obligaciones_para(cliente.tipo_persona)
            if clave in tildadas
        ]
        # Primero los que ya se pueden liquidar.
        opciones.sort(key=lambda o: not o["disponible"])
        return render_template("elegir_impuesto.html", cliente=cliente, opciones=opciones)

    @app.route("/liquidar/<int:cliente_id>/iva", methods=["GET"])
    @estudio_required
    def liquidar_iva(cliente_id):
        """Paso 3 (IVA): período y archivos opcionales; el POST va a /generar."""
        cliente = _cliente_propio_o_404(cliente_id)
        return render_template("liquidar_iva.html", cliente=cliente)

    @app.route("/generar", methods=["POST"])
    @estudio_required
    def generar():
        cliente_id = request.form.get("cliente_id", "")
        periodo = request.form.get("periodo", "").strip()
        archivo_ventas = request.files.get("ventas")
        archivo_compras = request.files.get("compras")

        if not cliente_id or not periodo:
            flash("Elegí un cliente y completá el período.")
            return redirect(url_for("index"))

        # Nunca confiar en el cliente_id "a ciegas": tiene que ser un
        # cliente que pertenezca al estudio logueado, si no, 404 (misma
        # regla que en clientes.py).
        cliente = Cliente.query.filter_by(id=cliente_id, estudio_id=current_user.id).first()
        if cliente is None:
            flash("Ese cliente no existe o no pertenece a tu estudio.")
            return redirect(url_for("index"))

        nombre_base = f"{cliente.razon_social.replace(' ', '_')}_{periodo}"

        # Los archivos de Excel/CSV son opcionales: si no se suben, se usan
        # los comprobantes que ya estén cargados por escaneo de QR para ese
        # cliente y período (ver escaner.py). Si se suben Y hay escaneados,
        # se combinan ambas fuentes.
        if archivo_ventas and archivo_ventas.filename:
            path_ventas = os.path.join(TMP_DIR, f"{nombre_base}_ventas_{archivo_ventas.filename}")
            archivo_ventas.save(path_ventas)
            try:
                ventas_excel = cargar_comprobantes(path_ventas)
            except ValueError as e:
                flash(f"Error al leer el archivo de ventas: {e}")
                return redirect(url_for("liquidar_iva", cliente_id=cliente.id))
            finally:
                os.remove(path_ventas)
        else:
            ventas_excel = dataframe_vacio()

        if archivo_compras and archivo_compras.filename:
            path_compras = os.path.join(TMP_DIR, f"{nombre_base}_compras_{archivo_compras.filename}")
            archivo_compras.save(path_compras)
            try:
                compras_excel = cargar_comprobantes(path_compras)
            except ValueError as e:
                flash(f"Error al leer el archivo de compras: {e}")
                return redirect(url_for("liquidar_iva", cliente_id=cliente.id))
            finally:
                os.remove(path_compras)
        else:
            compras_excel = dataframe_vacio()

        ventas_escaneadas = comprobantes_a_dataframe(
            Comprobante.query.filter_by(cliente_id=cliente.id, tipo="venta", periodo=periodo).all()
        )
        compras_escaneadas = comprobantes_a_dataframe(
            Comprobante.query.filter_by(cliente_id=cliente.id, tipo="compra", periodo=periodo).all()
        )

        ventas, omit_v = combinar_sin_duplicados(ventas_excel, ventas_escaneadas)
        compras, omit_c = combinar_sin_duplicados(compras_excel, compras_escaneadas)
        if omit_v or omit_c:
            flash(
                f"Se omitieron {omit_v} venta(s) y {omit_c} compra(s) escaneadas porque "
                "ya estaban en el Excel subido (para no contarlas dos veces)."
            )

        if ventas.empty and compras.empty:
            flash(
                "No hay comprobantes para liquidar: subí un archivo de ventas/compras, "
                "o cargá facturas escaneando su QR desde la ficha del cliente."
            )
            return redirect(url_for("liquidar_iva", cliente_id=cliente.id))

        resultado = calcular_liquidacion(periodo, ventas, compras)

        path_excel = os.path.join(TMP_DIR, f"{nombre_base}_liquidacion.xlsx")
        path_pdf = os.path.join(TMP_DIR, f"{nombre_base}_resumen.pdf")
        generar_excel(resultado, ventas, compras, path_excel)
        generar_pdf(resultado, cliente.razon_social, path_pdf, estudio=current_user.nombre)

        paths_afip = []
        try:
            paths_afip = generar_archivos_afip(ventas, compras, TMP_DIR, nombre_base)
        except DatoAfipFaltante as e:
            flash(f"Se generó la liquidación, pero no los TXT de AFIP: {e}")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.write(path_excel, arcname=f"{nombre_base}_liquidacion.xlsx")
            zf.write(path_pdf, arcname=f"{nombre_base}_resumen.pdf")
            for path_txt in paths_afip:
                zf.write(path_txt, arcname=os.path.basename(path_txt))
        buffer.seek(0)

        os.remove(path_excel)
        os.remove(path_pdf)
        for path_txt in paths_afip:
            os.remove(path_txt)

        # Guardo solo el registro histórico (no los archivos): quién liquidó
        # qué período y con qué saldo, para que el estudio tenga un historial.
        db.session.add(Liquidacion(cliente_id=cliente.id, periodo=periodo, saldo=resultado.saldo))
        db.session.commit()

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"{nombre_base}.zip",
            mimetype="application/zip",
        )


def registrar_comandos_cli(app: Flask):
    """Comandos `flask --app app <comando>` para setup inicial (ver README)."""

    @app.cli.command("init-db")
    def init_db():
        """Crea todas las tablas en la base configurada por DATABASE_URL."""
        db.create_all()
        print("Tablas creadas.")

    @app.cli.command("crear-admin")
    def crear_admin():
        """Crea (o actualiza la contraseña de) el usuario administrador de la plataforma."""
        import getpass

        email = input("Email del admin: ").strip().lower()
        nombre = input("Nombre: ").strip()
        password = getpass.getpass("Contraseña: ")

        admin = Admin.query.filter_by(email=email).first()
        if admin is None:
            admin = Admin(email=email, nombre=nombre)
            db.session.add(admin)
        admin.set_password(password)
        db.session.commit()
        print(f"Listo: admin '{email}' creado/actualizado.")


app = crear_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=os.environ.get("FLASK_DEBUG") == "1")
