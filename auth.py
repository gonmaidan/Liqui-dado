"""
auth.py
---------
Login/logout de los dos roles de la plataforma:

  - Estudio: /login  (el que usan los estudios contables para entrar a
    app-liquidar)
  - Admin:   /admin/login  (el dueño de la plataforma, panel de gestión)

Son dos formularios y dos sesiones distintas a propósito: nadie con un
login de Estudio puede terminar en el panel de Admin ni viceversa.
"""

from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from extensions import db, login_manager
from models import Estudio, Admin
from email_utils import enviar_email

auth_bp = Blueprint("auth", __name__)


@login_manager.user_loader
def cargar_usuario(id_prefijado: str):
    """
    Flask-Login guarda en la sesión lo que devuelve get_id(). Acá lo
    interpretamos de vuelta: "estudio-3" -> Estudio con id 3,
    "admin-1" -> Admin con id 1.
    """
    try:
        tipo, id_str = id_prefijado.split("-", 1)
        id_num = int(id_str)
    except (ValueError, AttributeError):
        return None

    if tipo == "estudio":
        return db.session.get(Estudio, id_num)
    if tipo == "admin":
        return db.session.get(Admin, id_num)
    return None


def estudio_required(vista):
    """Protege una ruta para que solo entre un Estudio logueado (no un Admin)."""
    @wraps(vista)
    @login_required
    def envoltorio(*args, **kwargs):
        if not isinstance(current_user, Estudio):
            flash("Esa sección es solo para estudios. Iniciá sesión con tu cuenta de estudio.")
            return redirect(url_for("auth.login"))
        return vista(*args, **kwargs)
    return envoltorio


def admin_required(vista):
    """Protege una ruta para que solo entre el Admin (no un Estudio)."""
    @wraps(vista)
    @login_required
    def envoltorio(*args, **kwargs):
        if not isinstance(current_user, Admin):
            flash("Esa sección es solo para el administrador de la plataforma.")
            return redirect(url_for("auth.admin_login"))
        return vista(*args, **kwargs)
    return envoltorio


# ---------------------------------------------------------------------------
# Login / logout de Estudio
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if isinstance(current_user, Estudio):
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        estudio = Estudio.query.filter_by(email=email).first()
        if estudio is None or not estudio.check_password(password):
            flash("Email o contraseña incorrectos.")
            return redirect(url_for("auth.login"))
        if not estudio.activo:
            flash("Esta cuenta está suspendida. Contactate con la administración.")
            return redirect(url_for("auth.login"))

        login_user(estudio)
        return redirect(url_for("index"))

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Login / logout de Admin
# ---------------------------------------------------------------------------

@auth_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if isinstance(current_user, Admin):
        return redirect(url_for("admin.panel"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        admin = Admin.query.filter_by(email=email).first()
        if admin is None or not admin.check_password(password):
            flash("Email o contraseña incorrectos.")
            return redirect(url_for("auth.admin_login"))

        login_user(admin)
        return redirect(url_for("admin.panel"))

    return render_template("admin_login.html")


@auth_bp.route("/admin/logout")
@login_required
def admin_logout():
    logout_user()
    return redirect(url_for("auth.admin_login"))


# ---------------------------------------------------------------------------
# "Olvidé mi contraseña" — un solo flujo, sirve para Estudio y para Admin
# ---------------------------------------------------------------------------

def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="restablecer-password")


TOKEN_VALIDO_SEGUNDOS = 60 * 60  # 1 hora


@auth_bp.route("/recuperar", methods=["GET", "POST"])
def recuperar():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        estudio = Estudio.query.filter_by(email=email).first()
        admin = None if estudio else Admin.query.filter_by(email=email).first()

        if estudio or admin:
            tipo = "estudio" if estudio else "admin"
            id_usuario = estudio.id if estudio else admin.id
            token = _serializer().dumps({"tipo": tipo, "id": id_usuario})
            link = url_for("auth.restablecer", token=token, _external=True)
            enviar_email(
                destinatario=email,
                asunto="Recuperar tu contraseña — app-liquidar",
                cuerpo=(
                    f"Entrá a este link para elegir una contraseña nueva "
                    f"(válido por 1 hora):\n\n{link}\n\n"
                    f"Si no pediste esto, ignorá este email — tu contraseña actual sigue funcionando."
                ),
            )

        # Mismo mensaje exista o no la cuenta, para no revelar qué emails están registrados.
        flash("Si ese email está registrado, te enviamos un link para restablecer la contraseña.")
        return redirect(url_for("auth.login"))

    return render_template("recuperar.html")


@auth_bp.route("/restablecer/<token>", methods=["GET", "POST"])
def restablecer(token):
    try:
        datos = _serializer().loads(token, max_age=TOKEN_VALIDO_SEGUNDOS)
    except SignatureExpired:
        flash("Ese link venció (dura 1 hora). Pedí uno nuevo.")
        return redirect(url_for("auth.recuperar"))
    except BadSignature:
        flash("Ese link no es válido.")
        return redirect(url_for("auth.recuperar"))

    modelo = Estudio if datos["tipo"] == "estudio" else Admin
    usuario = db.session.get(modelo, datos["id"])
    if usuario is None:
        flash("Esa cuenta ya no existe.")
        return redirect(url_for("auth.recuperar"))

    if request.method == "POST":
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if len(password) < 8:
            flash("La contraseña tiene que tener al menos 8 caracteres.")
            return redirect(request.url)
        if password != password2:
            flash("Las dos contraseñas no coinciden.")
            return redirect(request.url)

        usuario.set_password(password)
        db.session.commit()
        flash("Contraseña actualizada. Ya podés iniciar sesión.")
        destino = "auth.login" if datos["tipo"] == "estudio" else "auth.admin_login"
        return redirect(url_for(destino))

    return render_template("restablecer.html", token=token)


# ---------------------------------------------------------------------------
# Cambiar contraseña estando logueado (sin pasar por email)
# ---------------------------------------------------------------------------

@auth_bp.route("/cuenta", methods=["GET", "POST"])
@login_required
def cuenta():
    if request.method == "POST":
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        nueva2 = request.form.get("nueva2", "")

        if not current_user.check_password(actual):
            flash("La contraseña actual no es correcta.")
            return redirect(url_for("auth.cuenta"))
        if len(nueva) < 8:
            flash("La contraseña nueva tiene que tener al menos 8 caracteres.")
            return redirect(url_for("auth.cuenta"))
        if nueva != nueva2:
            flash("Las dos contraseñas nuevas no coinciden.")
            return redirect(url_for("auth.cuenta"))

        current_user.set_password(nueva)
        db.session.commit()
        flash("Contraseña actualizada.")
        destino = "index" if isinstance(current_user, Estudio) else "admin.panel"
        return redirect(url_for(destino))

    return render_template("cuenta.html")
