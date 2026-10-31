"""
f931.py
---------
Formulario 931 (cargas sociales) por cliente:

  /clientes/<id>/f931                      -> resumen: empleador, empleados, elegir período
  /clientes/<id>/f931/empleador            -> datos del cliente como empleador + tipos de empleado
  /clientes/<id>/f931/empleados/nuevo|<e>  -> alta / edición de empleados
  /clientes/<id>/f931/<AAAA-MM>            -> remuneraciones del mes (se guardan)
  /clientes/<id>/f931/<AAAA-MM>/pdf        -> PDF con el modelo de presentación
  /clientes/<id>/f931/<AAAA-MM>/txt        -> TXT para importar en SICOSS
  /f931/parametros                         -> alícuotas, detracciones y topes (editables por el estudio)

Todo se filtra por el estudio logueado (igual que el resto de la app).
"""

import io
import os
import re
import uuid
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from auth import estudio_required
from clientes import _cliente_del_estudio_o_404
from extensions import db
from f931_calculo import (
    BASES_ANSES, CONDICIONES, DEFECTO_POR_TIPO, MODALIDADES, SITUACIONES, PARAMETROS_DEFECTO, TIPOS_EMPLEADO, TIPOS_EMPLEADOR,
    _cuil_valido, base_anses, calcular_liquidacion, generar_txt_sicoss, validar_empleado,
)
from f931_pdf import generar_pdf_f931
from models import Empleado, EmpleadorF931, ObraSocial, ParametroF931, RemuneracionMensual

f931_bp = Blueprint("f931", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "_tmp_web")
os.makedirs(TMP_DIR, exist_ok=True)

RE_PERIODO = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ------------------------------------------------------------------ helpers
def _num(texto, defecto=0.0) -> float:
    """'1.234,56' / '1234.56' / '' -> float."""
    t = (texto or "").strip().replace("$", "").replace(" ", "")
    if not t:
        return defecto
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    return float(t)


def _periodo_o_404(periodo: str) -> str:
    if not RE_PERIODO.match(periodo):
        abort(404)
    return periodo


def _empleador(cliente) -> EmpleadorF931:
    emp = cliente.empleador_f931
    if emp is None:
        emp = EmpleadorF931(cliente_id=cliente.id)
        db.session.add(emp)
        db.session.commit()
    return emp


def _empleado_o_404(cliente, empleado_id: int) -> Empleado:
    e = db.session.get(Empleado, empleado_id)
    if e is None or e.cliente_id != cliente.id:
        abort(404)
    return e


def _params_estudio() -> dict:
    return {p.clave: p.valor for p in ParametroF931.query.filter_by(estudio_id=current_user.id).all()}


def _bases_periodo(periodo: str):
    """(minima, maxima, exacta) con lo que el estudio cargó para ese período, o el dato de ANSES."""
    ps = _params_estudio()
    mn, mx = ps.get(f"base_min:{periodo}"), ps.get(f"base_max:{periodo}")
    if mn and mx:
        return mn, mx, True
    return base_anses(periodo)


def _guardar_param(clave: str, valor: float):
    p = ParametroF931.query.filter_by(estudio_id=current_user.id, clave=clave).first()
    if p:
        p.valor = valor
    else:
        db.session.add(ParametroF931(estudio_id=current_user.id, clave=clave, valor=valor))


def _periodo_anterior(periodo: str) -> str:
    a, m = int(periodo[:4]), int(periodo[5:])
    return f"{a - 1}-12" if m == 1 else f"{a}-{m - 1:02d}"


def _remuneracion_de(empleado, periodo):
    return RemuneracionMensual.query.filter_by(empleado_id=empleado.id, periodo=periodo).first()


def _ultima_remuneracion_antes_de(empleado, periodo):
    return (
        RemuneracionMensual.query.filter(
            RemuneracionMensual.empleado_id == empleado.id, RemuneracionMensual.periodo < periodo
        ).order_by(RemuneracionMensual.periodo.desc()).first()
    )


def _calcular(cliente, periodo):
    """Calcula con lo guardado. Devuelve (resultado, empleador_dict, problemas)."""
    empleador = _empleador(cliente)
    activos = Empleado.query.filter_by(cliente_id=cliente.id, activo=True).order_by(Empleado.apellido_nombre).all()
    pares, problemas = [], []
    for e in activos:
        rem = _remuneracion_de(e, periodo)
        if rem is None:
            problemas.append(f"{e.apellido_nombre}: no cargaste su remuneración de {periodo}.")
            continue
        d = e.como_dict()
        problemas += validar_empleado(d)
        pares.append((d, rem.como_dict()))
    if not activos:
        problemas.append("El cliente no tiene empleados cargados.")
    mn, mx, exacta = _bases_periodo(periodo)
    resultado = calcular_liquidacion(
        periodo, empleador.como_dict(), pares, _params_estudio(),
        base_minima=mn if exacta else None, base_maxima=mx if exacta else None,
    )
    return resultado, empleador.como_dict(), problemas


# ------------------------------------------------------------------ pantallas
@f931_bp.route("/clientes/<int:cliente_id>/f931")
@estudio_required
def inicio(cliente_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    empleador = _empleador(cliente)
    empleados = Empleado.query.filter_by(cliente_id=cliente.id).order_by(Empleado.apellido_nombre).all()
    hoy = date.today()
    periodo_sugerido = request.args.get("periodo") or _periodo_anterior(f"{hoy.year}-{hoy.month:02d}")
    return render_template(
        "f931_inicio.html", cliente=cliente, empleador=empleador, empleados=empleados,
        tipos=TIPOS_EMPLEADO, tipos_empleador=TIPOS_EMPLEADOR, periodo_sugerido=periodo_sugerido,
    )


@f931_bp.route("/clientes/<int:cliente_id>/f931/ir", methods=["POST"])
@estudio_required
def ir_a_periodo(cliente_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    periodo = request.form.get("periodo", "").strip()
    if not RE_PERIODO.match(periodo):
        flash("El período tiene que tener el formato AAAA-MM (por ejemplo 2026-09).")
        return redirect(url_for("f931.inicio", cliente_id=cliente.id))
    return redirect(url_for("f931.liquidacion", cliente_id=cliente.id, periodo=periodo))


@f931_bp.route("/clientes/<int:cliente_id>/f931/empleador", methods=["GET", "POST"])
@estudio_required
def config_empleador(cliente_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    emp = _empleador(cliente)
    if request.method == "POST":
        f = request.form
        try:
            tipo = f.get("tipo_empleador", "2b")
            if tipo not in TIPOS_EMPLEADOR:
                raise ValueError("Elegí el tipo de empleador.")
            tipos = [t for t in f.getlist("tipos_empleado") if t in TIPOS_EMPLEADO]
            if not tipos:
                raise ValueError("Tildá al menos un tipo de empleado.")
            emp.tipo_empleador = tipo
            emp.hasta25 = f.get("hasta25") == "1"
            emp.sector_ampliado = f.get("sector_ampliado") == "1"
            emp.scvo = f.get("scvo") == "1"
            emp.art_pct = _num(f.get("art_pct"))
            emp.art_fijo = _num(f.get("art_fijo"))
            emp.actividad = "".join(c for c in f.get("actividad", "") if c.isdigit())[:3]
            emp.zona = "".join(c for c in f.get("zona", "0") if c.isdigit())[:2] or "0"
            emp.provincia = f.get("provincia", "").strip()[:50]
            emp.tipo_empresa = "".join(c for c in f.get("tipo_empresa", "0") if c.isdigit())[:1] or "0"
            emp.tipos_empleado = ",".join(tipos)
        except ValueError as e:
            flash(str(e) if "Elegí" in str(e) or "Tildá" in str(e) else "Alguno de los importes no es un número válido.")
            return redirect(url_for("f931.config_empleador", cliente_id=cliente.id))
        db.session.commit()
        flash("Datos del empleador guardados.")
        return redirect(url_for("f931.inicio", cliente_id=cliente.id))
    return render_template(
        "f931_empleador.html", cliente=cliente, emp=emp, tipos=TIPOS_EMPLEADO, tipos_empleador=TIPOS_EMPLEADOR,
    )


def _leer_empleado(form, cliente, empleador, empleado=None):
    """Valida el formulario de empleado. Devuelve (valores, error)."""
    cuil = "".join(c for c in form.get("cuil", "") if c.isdigit())
    nombre = form.get("apellido_nombre", "").strip()
    tipo = form.get("tipo", "comun")
    if len(cuil) != 11:
        return None, "El CUIL tiene que tener 11 dígitos."
    if not _cuil_valido(cuil):
        return None, "El CUIL no es válido (dígito verificador incorrecto). Revisalo."
    if not nombre:
        return None, "Falta apellido y nombre."
    if tipo not in empleador.tipos:
        return None, "Ese tipo de empleado no está habilitado para este cliente."
    existente = Empleado.query.filter_by(cliente_id=cliente.id, cuil=cuil_formateado(cuil)).first()
    if existente and (empleado is None or existente.id != empleado.id):
        return None, "Ya hay un empleado con ese CUIL."
    por_defecto = DEFECTO_POR_TIPO[tipo]
    try:
        valores = dict(
            cuil=cuil_formateado(cuil), apellido_nombre=nombre[:60], tipo=tipo,
            conyuge=form.get("conyuge") == "1", hijos=max(0, int(form.get("hijos") or 0)),
            situacion=int(form.get("situacion") or 1),
            condicion=_codigo_elegido(form, "condicion", por_defecto["condicion"]),
            modalidad=_codigo_elegido(form, "modalidad", por_defecto["modalidad"]),
            obra_social=_obra_social_elegida(form),
            adherentes=max(0, int(form.get("adherentes") or 0)),
        )
    except ValueError:
        return None, "Hijos, adherentes y códigos tienen que ser números."
    if tipo != "jubilado" and len(valores["obra_social"]) != 6:
        return None, "El código de obra social tiene 6 dígitos (ej. 126205). Si no lo sabés, consultalo en el aplicativo SICOSS."
    return valores, None


def _codigo_elegido(form, campo, defecto) -> int:
    """Desplegable con opción 'otro': si eligió 'otro' se toma el número escrito aparte."""
    elegido = form.get(campo, "")
    if elegido == "otro":
        elegido = form.get(campo + "_otro", "")
    return int(elegido) if elegido.strip() else defecto


def _obra_social_elegida(form) -> str:
    elegido = form.get("obra_social", "")
    if elegido == "otra":
        return "".join(c for c in form.get("obra_social_codigo", "") if c.isdigit())[:6]
    return "".join(c for c in elegido if c.isdigit())[:6]


def _recordar_obra_social(form, codigo: str):
    """Si se cargó una obra social nueva con nombre, queda en el desplegable para la próxima vez."""
    nombre = form.get("obra_social_nombre", "").strip()
    if form.get("obra_social") != "otra" or len(codigo) != 6 or not nombre:
        return
    if not ObraSocial.query.filter_by(estudio_id=current_user.id, codigo=codigo).first():
        db.session.add(ObraSocial(estudio_id=current_user.id, codigo=codigo, nombre=nombre[:80]))


def _contexto_empleado(cliente, empleador, e, nuevo, empleado=None):
    obras = ObraSocial.query.filter_by(estudio_id=current_user.id).order_by(ObraSocial.nombre).all()
    return dict(
        cliente=cliente, empleador=empleador, e=e, nuevo=nuevo, empleado=empleado, tipos=TIPOS_EMPLEADO,
        situaciones=SITUACIONES, defectos=DEFECTO_POR_TIPO, condiciones=CONDICIONES, modalidades=MODALIDADES,
        obras=obras, codigos_obras={o.codigo for o in obras},
    )


def cuil_formateado(d: str) -> str:
    return f"{d[:2]}-{d[2:10]}-{d[10]}"


@f931_bp.route("/clientes/<int:cliente_id>/f931/empleados/nuevo", methods=["GET", "POST"])
@estudio_required
def empleado_nuevo(cliente_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    empleador = _empleador(cliente)
    if request.method == "POST":
        valores, error = _leer_empleado(request.form, cliente, empleador)
        if error:
            flash(error)
            return render_template("f931_empleado.html", **_contexto_empleado(cliente, empleador, request.form, True))
        _recordar_obra_social(request.form, valores["obra_social"])
        db.session.add(Empleado(cliente_id=cliente.id, **valores))
        db.session.commit()
        flash(f"Empleado {valores['apellido_nombre']} agregado.")
        return redirect(url_for("f931.inicio", cliente_id=cliente.id))
    return render_template("f931_empleado.html", **_contexto_empleado(cliente, empleador, {}, True))


@f931_bp.route("/clientes/<int:cliente_id>/f931/empleados/<int:empleado_id>", methods=["GET", "POST"])
@estudio_required
def empleado_editar(cliente_id, empleado_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    empleador = _empleador(cliente)
    e = _empleado_o_404(cliente, empleado_id)
    if request.method == "POST":
        valores, error = _leer_empleado(request.form, cliente, empleador, empleado=e)
        if error:
            flash(error)
            return render_template("f931_empleado.html", **_contexto_empleado(cliente, empleador, request.form, False, e))
        _recordar_obra_social(request.form, valores["obra_social"])
        for k, v in valores.items():
            setattr(e, k, v)
        e.activo = request.form.get("activo") == "1"
        db.session.commit()
        flash("Empleado actualizado.")
        return redirect(url_for("f931.inicio", cliente_id=cliente.id))
    datos = {
        "cuil": e.cuil, "apellido_nombre": e.apellido_nombre, "tipo": e.tipo, "conyuge": "1" if e.conyuge else "",
        "hijos": e.hijos, "situacion": e.situacion, "condicion": e.condicion, "modalidad": e.modalidad,
        "obra_social": e.obra_social, "adherentes": e.adherentes, "activo": "1" if e.activo else "",
    }
    return render_template("f931_empleado.html", **_contexto_empleado(cliente, empleador, datos, False, e))


@f931_bp.route("/clientes/<int:cliente_id>/f931/empleados/<int:empleado_id>/eliminar", methods=["POST"])
@estudio_required
def empleado_eliminar(cliente_id, empleado_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    e = _empleado_o_404(cliente, empleado_id)
    nombre = e.apellido_nombre
    db.session.delete(e)
    db.session.commit()
    flash(f"Empleado {nombre} eliminado (con sus remuneraciones cargadas).")
    return redirect(url_for("f931.inicio", cliente_id=cliente.id))


@f931_bp.route("/clientes/<int:cliente_id>/f931/<periodo>", methods=["GET", "POST"])
@estudio_required
def liquidacion(cliente_id, periodo):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    _periodo_o_404(periodo)
    empleados = Empleado.query.filter_by(cliente_id=cliente.id, activo=True).order_by(Empleado.apellido_nombre).all()

    if request.method == "POST":
        errores = []
        try:
            mn, mx = _num(request.form.get("base_min")), _num(request.form.get("base_max"))
            if mn and mx:
                if mn >= mx:
                    raise ValueError("La base mínima tiene que ser menor que la máxima.")
                _guardar_param(f"base_min:{periodo}", mn)
                _guardar_param(f"base_max:{periodo}", mx)
        except ValueError as ex:
            errores.append(str(ex) if "base" in str(ex) else "Las bases mínima y máxima no son números válidos.")
        for e in empleados:
            p = f"e{e.id}_"
            try:
                valores = {k: _num(request.form.get(p + k)) for k in RemuneracionMensual.CONCEPTOS}
                dias = int(request.form.get(p + "dias") or 30)
                horas = int(request.form.get(p + "cant_horas_extras") or 0)
                if any(v < 0 for v in valores.values()) or not (0 <= dias <= 30) or horas < 0:
                    raise ValueError
            except ValueError:
                errores.append(f"{e.apellido_nombre}: revisá los importes (no pueden ser negativos), los días (0 a 30) y las horas.")
                continue
            rem = _remuneracion_de(e, periodo) or RemuneracionMensual(empleado_id=e.id, periodo=periodo)
            for k, v in valores.items():
                setattr(rem, k, v)
            rem.dias, rem.cant_horas_extras = dias, horas
            db.session.add(rem)
        if errores:
            db.session.rollback()
            for m in errores:
                flash(m)
        else:
            db.session.commit()
            flash("Remuneraciones guardadas.")
        return redirect(url_for("f931.liquidacion", cliente_id=cliente.id, periodo=periodo))

    filas = []
    for e in empleados:
        guardada = _remuneracion_de(e, periodo)
        previa = None if guardada else _ultima_remuneracion_antes_de(e, periodo)
        fuente = guardada or previa
        valores = fuente.como_dict() if fuente else {k: 0 for k in RemuneracionMensual.CONCEPTOS} | {"dias": 30, "cant_horas_extras": 0}
        if previa:  # los conceptos eventuales no se arrastran de un mes al otro
            for k in ("horas_extras", "vacaciones", "premios", "sac"):
                valores[k] = 0
            valores["cant_horas_extras"] = 0
        filas.append({"empleado": e, "v": valores, "copiada": bool(previa), "guardada": bool(guardada)})
    mn, mx, exacta = _bases_periodo(periodo)
    return render_template(
        "f931_liquidacion.html", cliente=cliente, periodo=periodo, filas=filas,
        base_min=mn, base_max=mx, base_exacta=exacta, tipos=TIPOS_EMPLEADO,
        listo=bool(filas) and all(f["guardada"] for f in filas),
    )


@f931_bp.route("/clientes/<int:cliente_id>/f931/<periodo>/pdf")
@estudio_required
def descargar_pdf(cliente_id, periodo):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    _periodo_o_404(periodo)
    resultado, empleador, problemas = _calcular(cliente, periodo)
    if not resultado.empleados:
        for m in problemas:
            flash(m)
        return redirect(url_for("f931.liquidacion", cliente_id=cliente.id, periodo=periodo))
    path = os.path.join(TMP_DIR, f"f931_{current_user.id}_{uuid.uuid4().hex}.pdf")
    try:
        generar_pdf_f931(resultado, cliente, empleador, path, estudio=current_user.nombre)
        with open(path, "rb") as fh:
            datos = io.BytesIO(fh.read())
    finally:
        if os.path.exists(path):
            os.remove(path)
    return send_file(datos, mimetype="application/pdf", as_attachment=True, download_name=f"F931_{periodo}_{cliente.cuit}.pdf")


@f931_bp.route("/clientes/<int:cliente_id>/f931/<periodo>/txt")
@estudio_required
def descargar_txt(cliente_id, periodo):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    _periodo_o_404(periodo)
    resultado, empleador, problemas = _calcular(cliente, periodo)
    if problemas:
        for m in problemas:
            flash(m)
        return redirect(url_for("f931.liquidacion", cliente_id=cliente.id, periodo=periodo))
    try:
        texto = generar_txt_sicoss(resultado, empleador)
    except ValueError as e:
        flash(f"No se pudo armar el TXT: {e}")
        return redirect(url_for("f931.liquidacion", cliente_id=cliente.id, periodo=periodo))
    return send_file(
        io.BytesIO(texto.encode("latin-1", errors="replace")), mimetype="text/plain", as_attachment=True,
        download_name=f"SICOSS_{periodo}_{cliente.cuit}.txt",
    )


@f931_bp.route("/f931/parametros", methods=["GET", "POST"])
@estudio_required
def parametros():
    guardados = _params_estudio()
    if request.method == "POST":
        try:
            for clave, (defecto, _, _) in PARAMETROS_DEFECTO.items():
                texto = request.form.get(clave, "").strip()
                if texto == "":
                    continue
                valor = _num(texto)
                if valor < 0:
                    raise ValueError
                if abs(valor - defecto) < 1e-9:
                    ParametroF931.query.filter_by(estudio_id=current_user.id, clave=clave).delete()
                else:
                    _guardar_param(clave, valor)
        except ValueError:
            db.session.rollback()
            flash("Alguno de los valores no es un número válido.")
            return redirect(url_for("f931.parametros"))
        db.session.commit()
        flash("Parámetros guardados.")
        return redirect(url_for("f931.parametros"))
    filas = [
        {"clave": k, "descripcion": d, "unidad": u, "defecto": v, "valor": guardados.get(k, v), "cambiado": k in guardados}
        for k, (v, d, u) in PARAMETROS_DEFECTO.items()
    ]
    return render_template("f931_parametros.html", filas=filas, bases=sorted(BASES_ANSES.items()))
