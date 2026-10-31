"""
models.py
-----------
Modelo de datos multi-tenant:

  Estudio (inquilino / login)
     └── Cliente (contribuyente al que se le liquida el IVA)
           └── Liquidacion (registro histórico de cada corrida — solo
                             metadata; los archivos no se guardan en el
                             servidor, se generan al vuelo y se descargan)

  Admin (dueño de la plataforma, login separado, NO ve datos de clientes)

Aislamiento: toda consulta de Cliente/Liquidacion se filtra siempre por
estudio_id == current_user.id dentro de las rutas (ver app.py). El Admin
solo puede leer el modelo Estudio y un conteo de clientes, nunca el
detalle de Cliente ni de Liquidacion.
"""

from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db


def _ahora():
    return datetime.now(timezone.utc)


class Estudio(UserMixin, db.Model):
    """Es quien inicia sesión para usar la app. Un login por estudio contable."""

    __tablename__ = "estudios"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    fecha_alta = db.Column(db.DateTime, default=_ahora, nullable=False)

    clientes = db.relationship("Cliente", backref="estudio", cascade="all, delete-orphan")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        # Prefijo para distinguir de un Admin con el mismo id numérico
        # (ver user_loader en auth.py).
        return f"estudio-{self.id}"

    @property
    def is_active(self):
        return self.activo


class Admin(UserMixin, db.Model):
    """Dueño de la plataforma. Login separado del de los estudios."""

    __tablename__ = "admins"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return f"admin-{self.id}"


class Cliente(db.Model):
    """Un contribuyente del estudio (a quien se le liquida el IVA)."""

    __tablename__ = "clientes"

    id = db.Column(db.Integer, primary_key=True)
    estudio_id = db.Column(db.Integer, db.ForeignKey("estudios.id"), nullable=False)
    cuit = db.Column(db.String(13), nullable=False)
    razon_social = db.Column(db.String(160), nullable=False)
    fecha_alta = db.Column(db.DateTime, default=_ahora, nullable=False)
    # "fisica" | "juridica": decide qué obligaciones se ofrecen para tildar.
    tipo_persona = db.Column(db.String(10), default="fisica", nullable=False)

    liquidaciones = db.relationship("Liquidacion", backref="cliente", cascade="all, delete-orphan")
    obligaciones = db.relationship("ClienteObligacion", backref="cliente", cascade="all, delete-orphan")
    empleados = db.relationship("Empleado", backref="cliente", cascade="all, delete-orphan")
    empleador_f931 = db.relationship("EmpleadorF931", backref="cliente", uselist=False, cascade="all, delete-orphan")
    cm_jurisdicciones = db.relationship("CmJurisdiccion", backref="cliente", cascade="all, delete-orphan")
    cm_actividades = db.relationship("CmActividad", backref="cliente", cascade="all, delete-orphan", order_by="CmActividad.orden")
    cm_coeficientes = db.relationship("CmCoeficiente", backref="cliente", cascade="all, delete-orphan")
    cm_ingresos = db.relationship("CmIngreso", backref="cliente", cascade="all, delete-orphan")
    cm_creditos = db.relationship("CmCredito", backref="cliente", cascade="all, delete-orphan")

    @property
    def claves_obligaciones(self) -> set:
        return {o.obligacion for o in self.obligaciones}

    __table_args__ = (
        db.UniqueConstraint("estudio_id", "cuit", name="uq_cliente_por_estudio"),
    )


class ClienteObligacion(db.Model):
    """Obligación (impuesto/tarea) que el estudio trabaja para un cliente. Ver obligaciones.py."""

    __tablename__ = "cliente_obligaciones"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    obligacion = db.Column(db.String(30), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("cliente_id", "obligacion", name="uq_obligacion_por_cliente"),
    )


class Liquidacion(db.Model):
    """
    Registro histórico de cada liquidación generada — solo metadata
    (quién, cuándo, qué período, qué saldo dio). Los archivos (Excel/PDF/
    TXT) NO se guardan acá: se generan al vuelo en /generar y se
    descargan directo, para no depender de almacenamiento persistente.
    """

    __tablename__ = "liquidaciones"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    periodo = db.Column(db.String(7), nullable=False)  # "2026-08"
    saldo = db.Column(db.Numeric(15, 2), nullable=False)
    fecha_generada = db.Column(db.DateTime, default=_ahora, nullable=False)


class Comprobante(db.Model):
    """
    Un comprobante (factura, nota de débito/crédito) de un cliente para un
    período determinado. Se carga escaneando el QR de AFIP de la factura
    (ver qr_scanner.py / escaner.py) — a futuro también podría cargarse a
    mano desde el panel, sin pasar por Excel.

    Conviven con los comprobantes subidos por Excel/CSV: al generar la
    liquidación (/generar en app.py) se combinan ambas fuentes para ese
    cliente y período.
    """

    __tablename__ = "comprobantes"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)  # "venta" | "compra"
    periodo = db.Column(db.String(7), nullable=False, index=True)  # "2026-08"

    fecha = db.Column(db.Date, nullable=False)
    tipo_comprobante = db.Column(db.String(40), nullable=False)
    punto_venta = db.Column(db.Integer, nullable=False)
    numero_comprobante = db.Column(db.Integer, nullable=False)
    cuit = db.Column(db.String(13), nullable=False)
    razon_social = db.Column(db.String(160), nullable=False)

    neto_gravado = db.Column(db.Numeric(15, 2), nullable=False)
    iva = db.Column(db.Numeric(15, 2), nullable=False)
    percepcion_iva = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    # Total de percepciones de IIBB del comprobante = suma de las filas de
    # PercepcionIibb (una por jurisdicción). Se guarda sumado acá para que
    # el cálculo y los TXT de AFIP, que trabajan con un solo importe, no
    # tengan que enterarse de que se cargó por jurisdicción.
    percepcion_iibb = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    # Columna de la versión anterior (una sola jurisdicción en texto libre).
    # Ya no se escribe; se deja para no romper bases creadas antes y para
    # mostrar comprobantes viejos. Lo nuevo va en PercepcionIibb.
    jurisdiccion_iibb = db.Column(db.String(60), nullable=True)

    origen = db.Column(db.String(10), default="qr", nullable=False)  # "qr" | "manual"
    fecha_carga = db.Column(db.DateTime, default=_ahora, nullable=False)

    cliente = db.relationship(
        "Cliente", backref=db.backref("comprobantes", cascade="all, delete-orphan")
    )
    percepciones_iibb = db.relationship(
        "PercepcionIibb",
        backref="comprobante",
        cascade="all, delete-orphan",
        order_by="PercepcionIibb.id",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "cliente_id", "tipo", "punto_venta", "numero_comprobante", "tipo_comprobante",
            name="uq_comprobante_por_cliente",
        ),
    )

    @property
    def detalle_iibb(self) -> str:
        """Texto con el desglose de IIBB por jurisdicción, ej: 'Buenos Aires: 1500.00 | CABA: 300.00'."""
        if self.percepciones_iibb:
            return " | ".join(
                f"{p.jurisdiccion or 'Sin especificar'}: {float(p.importe):.2f}"
                for p in self.percepciones_iibb
            )
        return self.jurisdiccion_iibb or ""


class PercepcionIibb(db.Model):
    """
    Una percepción de Ingresos Brutos de un comprobante, con su jurisdicción.
    Un comprobante puede tener varias (ej: Buenos Aires y CABA).
    """

    __tablename__ = "percepciones_iibb"

    id = db.Column(db.Integer, primary_key=True)
    comprobante_id = db.Column(db.Integer, db.ForeignKey("comprobantes.id"), nullable=False)
    jurisdiccion = db.Column(db.String(60), nullable=True)
    importe = db.Column(db.Numeric(15, 2), nullable=False)


class ContraparteConocida(db.Model):
    """
    Memoria de 'CUIT -> razón social' por estudio: cuando se escanea un QR
    de un CUIT ya visto antes (para cualquier cliente de ese estudio), se
    completa sola la razón social en vez de tener que escribirla de nuevo.
    """

    __tablename__ = "contrapartes_conocidas"

    id = db.Column(db.Integer, primary_key=True)
    estudio_id = db.Column(db.Integer, db.ForeignKey("estudios.id"), nullable=False)
    cuit = db.Column(db.String(13), nullable=False)
    razon_social = db.Column(db.String(160), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("estudio_id", "cuit", name="uq_contraparte_por_estudio"),
    )


class Vencimiento(db.Model):
    """
    Calendario base de vencimientos de impuestos (lo carga el Admin una vez
    por mes a partir de la resolución de ARCA). Es información pública y
    común a todos los estudios: no contiene datos de ningún cliente.

    terminaciones: dígitos finales de CUIT a los que aplica, pegados
    ("01" = CUIT terminados en 0 o 1). Vacío = aplica a todos.
    """

    __tablename__ = "vencimientos"

    id = db.Column(db.Integer, primary_key=True)
    impuesto = db.Column(db.String(120), nullable=False)
    fecha = db.Column(db.Date, nullable=False, index=True)
    terminaciones = db.Column(db.String(10), default="", nullable=False)
    # Clave de obligaciones.py (ej: "iva"). None = sin clasificar: no se muestra a ningún cliente.
    obligacion = db.Column(db.String(30), nullable=True, index=True)

    ajustes = db.relationship("VencimientoAjuste", backref="vencimiento",
                              cascade="all, delete-orphan")

    def aplica_a(self, cuit: str) -> bool:
        if not self.terminaciones:
            return True
        digitos = "".join(ch for ch in (cuit or "") if ch.isdigit())
        return bool(digitos) and digitos[-1] in self.terminaciones

    @property
    def terminaciones_texto(self) -> str:
        return ", ".join(self.terminaciones) if self.terminaciones else "Todas"


class VencimientoAjuste(db.Model):
    """
    Cambio de fecha hecho por un estudio sobre un vencimiento base (ej: ARCA
    prorrogó la fecha). Es propio de cada estudio: solo lo ven y lo usan los
    clientes de ese estudio; el calendario base queda intacto.
    """

    __tablename__ = "vencimientos_ajustes"

    id = db.Column(db.Integer, primary_key=True)
    estudio_id = db.Column(db.Integer, db.ForeignKey("estudios.id"), nullable=False)
    vencimiento_id = db.Column(db.Integer, db.ForeignKey("vencimientos.id"), nullable=False)
    fecha_nueva = db.Column(db.Date, nullable=False)
    nota = db.Column(db.String(200), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("estudio_id", "vencimiento_id", name="uq_ajuste_por_estudio"),
    )


# --------------------------------------------------------------------------
# F.931 (cargas sociales / SICOSS)
# --------------------------------------------------------------------------
class EmpleadorF931(db.Model):
    """Datos del cliente como empleador para el F.931 (uno por cliente)."""

    __tablename__ = "empleadores_f931"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False, unique=True)
    tipo_empleador = db.Column(db.String(3), default="2b", nullable=False)   # "2a" | "2b"
    hasta25 = db.Column(db.Boolean, default=True, nullable=False)            # detracción adicional (hasta 25 trabajadores)
    sector_ampliado = db.Column(db.Boolean, default=False, nullable=False)   # detracción ampliada
    art_pct = db.Column(db.Numeric(6, 3), default=0, nullable=False)         # alícuota variable ART (% de la masa salarial)
    art_fijo = db.Column(db.Numeric(12, 2), default=0, nullable=False)       # suma fija ART por trabajador
    scvo = db.Column(db.Boolean, default=True, nullable=False)               # Seguro de Vida Obligatorio
    actividad = db.Column(db.String(3), default="", nullable=False)          # código de actividad (ARCA)
    zona = db.Column(db.String(2), default="0", nullable=False)
    provincia = db.Column(db.String(50), default="", nullable=False)
    tipo_empresa = db.Column(db.String(1), default="0", nullable=False)
    # Tipos de empleado que el estudio habilitó para este cliente (separados por coma).
    tipos_empleado = db.Column(db.String(80), default="comun", nullable=False)

    @property
    def tipos(self) -> list:
        return [t for t in (self.tipos_empleado or "").split(",") if t]

    def como_dict(self) -> dict:
        return {
            "tipo_empleador": self.tipo_empleador, "hasta25": self.hasta25,
            "sector_ampliado": self.sector_ampliado, "art_pct": float(self.art_pct or 0),
            "art_fijo": float(self.art_fijo or 0), "scvo": self.scvo,
            "actividad": self.actividad, "zona": self.zona, "provincia": self.provincia,
            "tipo_empresa": self.tipo_empresa,
        }


class Empleado(db.Model):
    __tablename__ = "empleados"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    cuil = db.Column(db.String(13), nullable=False)
    apellido_nombre = db.Column(db.String(60), nullable=False)
    tipo = db.Column(db.String(10), default="comun", nullable=False)   # comun | jubilado | parcial
    conyuge = db.Column(db.Boolean, default=False, nullable=False)
    hijos = db.Column(db.Integer, default=0, nullable=False)
    situacion = db.Column(db.Integer, default=1, nullable=False)       # código de situación SICOSS (1 = activo)
    condicion = db.Column(db.Integer, default=1, nullable=False)       # código de condición SICOSS
    modalidad = db.Column(db.Integer, default=8, nullable=False)       # modalidad de contratación
    obra_social = db.Column(db.String(6), default="", nullable=False)  # código de obra social (6 dígitos)
    adherentes = db.Column(db.Integer, default=0, nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)

    remuneraciones = db.relationship("RemuneracionMensual", backref="empleado", cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("cliente_id", "cuil", name="uq_empleado_por_cliente"),)

    def como_dict(self) -> dict:
        return {
            "cuil": self.cuil, "apellido_nombre": self.apellido_nombre, "tipo": self.tipo,
            "conyuge": self.conyuge, "hijos": self.hijos, "situacion": self.situacion,
            "condicion": self.condicion, "modalidad": self.modalidad,
            "obra_social": int("".join(c for c in (self.obra_social or "") if c.isdigit()) or 0),
            "adherentes": self.adherentes,
        }


class RemuneracionMensual(db.Model):
    """Conceptos de un empleado en un período (se guardan para no retipear al mes siguiente)."""

    __tablename__ = "remuneraciones_mensuales"

    id = db.Column(db.Integer, primary_key=True)
    empleado_id = db.Column(db.Integer, db.ForeignKey("empleados.id"), nullable=False)
    periodo = db.Column(db.String(7), nullable=False)  # AAAA-MM
    sueldo = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    adicionales = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    horas_extras = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    cant_horas_extras = db.Column(db.Integer, default=0, nullable=False)
    zona = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    vacaciones = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    premios = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    sac = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    no_remunerativo = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    dias = db.Column(db.Integer, default=30, nullable=False)

    __table_args__ = (db.UniqueConstraint("empleado_id", "periodo", name="uq_rem_empleado_periodo"),)

    CONCEPTOS = ("sueldo", "adicionales", "horas_extras", "zona", "vacaciones", "premios", "sac", "no_remunerativo")

    def como_dict(self) -> dict:
        d = {k: float(getattr(self, k) or 0) for k in self.CONCEPTOS}
        d["dias"] = self.dias
        d["cant_horas_extras"] = self.cant_horas_extras
        return d


class ParametroF931(db.Model):
    """Alícuota/importe que el estudio cambió respecto del valor por defecto (ver f931_calculo.py)."""

    __tablename__ = "parametros_f931"

    id = db.Column(db.Integer, primary_key=True)
    estudio_id = db.Column(db.Integer, db.ForeignKey("estudios.id"), nullable=False)
    clave = db.Column(db.String(40), nullable=False)
    valor = db.Column(db.Float, nullable=False)

    __table_args__ = (db.UniqueConstraint("estudio_id", "clave", name="uq_parametro_estudio"),)


class ObraSocial(db.Model):
    """Obras sociales que el estudio usa (código de 6 dígitos de SICOSS + nombre), para elegirlas de un desplegable."""

    __tablename__ = "obras_sociales_f931"

    id = db.Column(db.Integer, primary_key=True)
    estudio_id = db.Column(db.Integer, db.ForeignKey("estudios.id"), nullable=False)
    codigo = db.Column(db.String(6), nullable=False)
    nombre = db.Column(db.String(80), nullable=False)

    __table_args__ = (db.UniqueConstraint("estudio_id", "codigo", name="uq_obra_social_por_estudio"),)


# --------------------------------------------------------------------------
# Convenio Multilateral (CM03)
# --------------------------------------------------------------------------
class CmJurisdiccion(db.Model):
    """Jurisdicción donde el cliente tributa por Convenio Multilateral (+ mínimo opcional)."""

    __tablename__ = "cm_jurisdicciones"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    jurisdiccion = db.Column(db.String(30), nullable=False)
    minimo = db.Column(db.Numeric(15, 2), default=0, nullable=False)

    __table_args__ = (db.UniqueConstraint("cliente_id", "jurisdiccion", name="uq_cm_jur_cliente"),)


class CmActividad(db.Model):
    __tablename__ = "cm_actividades"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    nombre = db.Column(db.String(80), nullable=False)
    orden = db.Column(db.Integer, default=0, nullable=False)

    alicuotas = db.relationship("CmAlicuota", backref="actividad", cascade="all, delete-orphan")
    ingresos = db.relationship("CmIngreso", backref="actividad", cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("cliente_id", "nombre", name="uq_cm_act_cliente"),)


class CmAlicuota(db.Model):
    __tablename__ = "cm_alicuotas"

    id = db.Column(db.Integer, primary_key=True)
    actividad_id = db.Column(db.Integer, db.ForeignKey("cm_actividades.id"), nullable=False)
    jurisdiccion = db.Column(db.String(30), nullable=False)
    alicuota = db.Column(db.Float, nullable=False)

    __table_args__ = (db.UniqueConstraint("actividad_id", "jurisdiccion", name="uq_cm_alic"),)


class CmCoeficiente(db.Model):
    """Un coeficiente unificado de una jurisdicción, vigente desde un período (conjunto = misma vigencia)."""

    __tablename__ = "cm_coeficientes"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    vigencia_desde = db.Column(db.String(7), nullable=False)  # AAAA-MM
    jurisdiccion = db.Column(db.String(30), nullable=False)
    coeficiente = db.Column(db.Float, nullable=False)

    __table_args__ = (db.UniqueConstraint("cliente_id", "vigencia_desde", "jurisdiccion", name="uq_cm_coef"),)


class CmIngreso(db.Model):
    """Ingreso computable del mes de una actividad (lo que se distribuye por coeficiente)."""

    __tablename__ = "cm_ingresos"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    periodo = db.Column(db.String(7), nullable=False)
    actividad_id = db.Column(db.Integer, db.ForeignKey("cm_actividades.id"), nullable=False)
    importe = db.Column(db.Numeric(15, 2), default=0, nullable=False)

    __table_args__ = (db.UniqueConstraint("cliente_id", "periodo", "actividad_id", name="uq_cm_ingreso"),)


class CmCredito(db.Model):
    """Deducciones del mes por jurisdicción (retenciones, SIRCREB, percepciones, pagos a cuenta, saldo anterior)."""

    __tablename__ = "cm_creditos"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    periodo = db.Column(db.String(7), nullable=False)
    jurisdiccion = db.Column(db.String(30), nullable=False)
    retenciones = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    sircreb = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    percepciones = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    pagos = db.Column(db.Numeric(15, 2), default=0, nullable=False)
    saldo_anterior = db.Column(db.Numeric(15, 2), default=0, nullable=False)

    __table_args__ = (db.UniqueConstraint("cliente_id", "periodo", "jurisdiccion", name="uq_cm_credito"),)
