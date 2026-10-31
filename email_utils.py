"""
email_utils.py
----------------
Envío de emails por SMTP. Sirve con cualquier proveedor que hable SMTP
(Resend, Mailgun, etc.) — no depende de un SDK propietario.

Si no hay SMTP_HOST configurado (por ejemplo, en desarrollo local sin
.env completo), en vez de fallar imprime el email por consola. Así el
flujo de "olvidé mi contraseña" se puede probar sin tener una cuenta de
email todavía: el link para restablecer aparece en la terminal.
"""

import os
import smtplib
from email.message import EmailMessage


def enviar_email(destinatario: str, asunto: str, cuerpo: str):
    host = os.environ.get("SMTP_HOST")

    if not host:
        print("\n=== EMAIL (SMTP no configurado — mostrando acá en vez de enviarlo) ===")
        print(f"Para: {destinatario}\nAsunto: {asunto}\n\n{cuerpo}")
        print("========================================================================\n")
        return

    puerto = int(os.environ.get("SMTP_PORT", "587"))
    usuario = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    remitente = os.environ.get("EMAIL_FROM", usuario)

    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = remitente
    mensaje["To"] = destinatario
    mensaje.set_content(cuerpo)

    with smtplib.SMTP(host, puerto) as server:
        server.starttls()
        server.login(usuario, password)
        server.send_message(mensaje)
