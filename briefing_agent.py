#!/usr/bin/env python3
"""
YSL Beauty Intelligence Briefing Agent
---------------------------------------
Lee credenciales desde variables de entorno (GitHub Secrets en producción,
archivo .env en desarrollo local).
"""

import anthropic
import smtplib
import json
import os
import logging
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("briefing_agent.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ─── Config desde entorno ────────────────────────────────────────────────────

def get_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise EnvironmentError(f"Variable de entorno '{key}' no definida. Revisa los GitHub Secrets.")
    return val

ANTHROPIC_API_KEY = get_env("ANTHROPIC_API_KEY")
GMAIL_USER        = get_env("GMAIL_USER")
GMAIL_APP_PASSWORD = get_env("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL   = get_env("RECIPIENT_EMAIL")
RECIPIENT_NAME    = get_env("RECIPIENT_NAME")
TEST_MODE         = os.environ.get("TEST_MODE", "false").lower() == "true"

# ─── Prompts ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres un agente de inteligencia de marketing de lujo especializado en YSL Beauty y L'Oréal Group.
Tu misión es crear briefings semanales ultra-curados para una Brand Manager de YSL Beauty.
Usas búsqueda web para encontrar noticias REALES y recientes de los últimos 7 días.

Responde ÚNICAMENTE con un objeto JSON válido, sin markdown, sin texto adicional, sin backticks.

Estructura exacta:
{
  "semana": "DD MMM YYYY",
  "insight_semana": "Frase estratégica clave del momento del sector (máx 25 palabras)",
  "articulos": [
    {
      "categoria": "COLABORACIONES | TENDENCIAS | CAMPAÑAS | COMPETENCIA | INFLUENCERS | RETAIL & DIGITAL",
      "titulo": "Título de la noticia",
      "resumen": "Qué pasó, por qué importa, qué implica para YSL/L'Oréal (2-3 frases)",
      "fuente": "Nombre del medio",
      "url": "URL si está disponible, si no cadena vacía",
      "relevancia_ysl": "Por qué importa específicamente para marketing de YSL Beauty (1 frase)"
    }
  ],
  "tendencia_emergente": "Tendencia que debe estar en el radar esta semana (2-3 frases)",
  "accion_sugerida": "Acción concreta y accionable para esta semana basada en las noticias",
  "frase_inspiracion": "Frase inspiradora breve relacionada con belleza, moda o creatividad"
}

Incluye entre 5 y 8 artículos. Prioriza noticias de los últimos 7 días."""

def build_user_prompt() -> str:
    today = datetime.now().strftime("%A %d de %B de %Y")
    return f"""Genera el briefing semanal de inteligencia para Brand Manager de YSL Beauty en L'Oréal.
Fecha de hoy: {today}.

Busca noticias recientes (últimos 7 días) sobre:
- Colaboraciones de marcas de lujo y belleza (YSL, Dior, Chanel, Givenchy, Tom Ford, Armani Beauty)
- Campañas de marketing de belleza de lujo destacadas
- Tendencias en redes sociales: TikTok beauty, Instagram, colaboraciones con creadores
- Movimientos estratégicos de competidores directos de YSL Beauty
- Lanzamientos de productos en perfumería y cosmética de lujo
- Influencer marketing y ambassador deals en el sector lujo
- Novedades en retail beauty y estrategia digital
- Tendencias Gen Z y millennial en belleza de lujo
- Noticias de L'Oréal Group relevantes para las marcas de lujo

Asegúrate de que las noticias sean reales, verificadas y de los últimos 7 días."""

# ─── Generar briefing con Claude + web search ────────────────────────────────

def generate_briefing() -> dict:
    log.info("Generando briefing con Claude + web search...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": build_user_prompt()}],
    )

    text_content = ""
    for block in response.content:
        if block.type == "text":
            text_content += block.text

    if not text_content.strip():
        raise ValueError("Claude no devolvió contenido de texto")

    clean = text_content.replace("```json", "").replace("```", "").strip()
    briefing = json.loads(clean)
    log.info(f"Briefing generado: {len(briefing.get('articulos', []))} artículos")
    return briefing

# ─── Renderizar HTML del email ───────────────────────────────────────────────

def render_email_html(data: dict, recipient_name: str) -> str:
    fecha = data.get("semana", datetime.now().strftime("%d %b %Y"))
    articulos_html = ""

    cat_colors = {
        "COLABORACIONES": "#7c6fcd",
        "TENDENCIAS": "#c9a84c",
        "CAMPAÑAS": "#d4537e",
        "COMPETENCIA": "#4a9eca",
        "INFLUENCERS": "#e8854a",
        "RETAIL & DIGITAL": "#5aad8a",
    }
    cat_icons = {
        "COLABORACIONES": "🤝",
        "TENDENCIAS": "📈",
        "CAMPAÑAS": "📣",
        "COMPETENCIA": "👁️",
        "INFLUENCERS": "⭐",
        "RETAIL & DIGITAL": "📱",
    }

    for art in data.get("articulos", []):
        cat = art.get("categoria", "").upper()
        color = cat_colors.get(cat, "#888")
        icon = cat_icons.get(cat, "•")
        url = art.get("url", "")
        titulo = art.get("titulo", "")
        titulo_html = f'<a href="{url}" style="color:#1a1a1a;text-decoration:none;">{titulo}</a>' if url else titulo

        articulos_html += f"""
        <div style="border:1px solid #e8e0d4;border-radius:8px;padding:18px 22px;margin-bottom:14px;background:#fff;">
          <div style="font-size:10px;letter-spacing:0.2em;text-transform:uppercase;color:{color};font-weight:600;margin-bottom:8px;">
            {icon} {cat}
          </div>
          <div style="font-family:'Georgia',serif;font-size:17px;font-weight:400;color:#1a1a1a;line-height:1.3;margin-bottom:8px;">
            {titulo_html}
          </div>
          <div style="font-size:13px;color:#555;line-height:1.65;margin-bottom:8px;">
            {art.get('resumen', '')}
          </div>
          <div style="font-size:12px;color:{color};font-style:italic;">
            ✦ {art.get('relevancia_ysl', '')}
          </div>
          <div style="font-size:11px;color:#999;margin-top:6px;">
            {art.get('fuente', '')}
          </div>
        </div>"""

    frase = data.get("frase_inspiracion", "")
    frase_html = f"""<tr><td style="background:#0a0a0a;padding:20px 36px;">
      <div style="font-family:Georgia,serif;font-size:14px;font-style:italic;color:#c9a84c;text-align:center;letter-spacing:0.05em;">
        &ldquo;{frase}&rdquo;
      </div>
    </td></tr>""" if frase else ""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YSL Intelligence Briefing</title>
</head>
<body style="margin:0;padding:0;background:#f5f0ea;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f0ea;padding:32px 0;">
  <tr><td align="center">
  <table width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;">
    <tr><td style="background:#0a0a0a;padding:32px 36px 28px;border-radius:10px 10px 0 0;">
      <div style="font-family:'Georgia',serif;font-size:11px;letter-spacing:0.4em;color:#c9a84c;text-transform:uppercase;margin-bottom:8px;">L'Oréal Luxe · YSL Beauty</div>
      <div style="font-family:'Georgia',serif;font-size:28px;font-weight:300;color:#fff;letter-spacing:0.08em;line-height:1.1;">Intelligence Briefing</div>
      <div style="font-size:11px;letter-spacing:0.15em;color:#888;text-transform:uppercase;margin-top:8px;">{fecha} · Edición semanal</div>
    </td></tr>
    <tr><td style="background:#fff;padding:24px 36px 0;">
      <div style="font-family:'Georgia',serif;font-size:15px;color:#333;font-style:italic;">Bonjour, {recipient_name} —</div>
    </td></tr>
    <tr><td style="background:#fff;padding:18px 36px 24px;">
      <div style="border-left:3px solid #c9a84c;padding:12px 16px;background:#faf7f2;border-radius:0 6px 6px 0;">
        <div style="font-size:10px;letter-spacing:0.25em;text-transform:uppercase;color:#c9a84c;font-weight:600;margin-bottom:6px;">Insight de la semana</div>
        <div style="font-family:'Georgia',serif;font-size:16px;font-style:italic;color:#1a1a1a;line-height:1.5;">{data.get('insight_semana', '')}</div>
      </div>
    </td></tr>
    <tr><td style="background:#fff;padding:0 36px;">
      <div style="height:0.5px;background:#e8e0d4;"></div>
      <div style="text-align:center;margin:-8px 0 16px;">
        <span style="background:#fff;padding:0 12px;font-size:10px;letter-spacing:0.3em;text-transform:uppercase;color:#c9a84c;">Noticias clave</span>
      </div>
    </td></tr>
    <tr><td style="background:#fff;padding:0 36px 24px;">{articulos_html}</td></tr>
    <tr><td style="background:#fff;padding:0 36px 24px;">
      <div style="height:0.5px;background:#e8e0d4;margin-bottom:20px;"></div>
      <div style="font-size:10px;letter-spacing:0.3em;text-transform:uppercase;color:#c9a84c;margin-bottom:10px;">📡 En el radar</div>
      <div style="font-size:13.5px;color:#444;line-height:1.65;">{data.get('tendencia_emergente', '')}</div>
    </td></tr>
    <tr><td style="background:#fff;padding:0 36px 28px;">
      <div style="background:#f0faf5;border:1px solid #c5e8d5;border-radius:8px;padding:16px 20px;">
        <div style="font-size:10px;letter-spacing:0.25em;text-transform:uppercase;color:#2d7a52;font-weight:600;margin-bottom:6px;">✅ Acción para esta semana</div>
        <div style="font-size:13px;color:#2d4a3a;line-height:1.6;">{data.get('accion_sugerida', '')}</div>
      </div>
    </td></tr>
    {frase_html}
    <tr><td style="background:#1a1a1a;padding:20px 36px;border-radius:0 0 10px 10px;">
      <div style="font-size:10px;color:#666;text-align:center;letter-spacing:0.1em;">YSL BEAUTY INTELLIGENCE · GENERADO CON IA · {fecha}</div>
      <div style="font-size:10px;color:#444;text-align:center;margin-top:4px;">Powered by Claude + Anthropic Web Search</div>
    </td></tr>
  </table>
  </td></tr>
</table>
</body>
</html>"""

# ─── Enviar email ────────────────────────────────────────────────────────────

def send_email(html_body: str, subject: str):
    log.info(f"Enviando email a {RECIPIENT_EMAIL}...")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"YSL Intelligence <{GMAIL_USER}>"
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
    log.info("✅ Email enviado correctamente")

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 55)
    log.info("YSL Briefing Agent — inicio")
    log.info(f"TEST_MODE: {TEST_MODE}")
    log.info("=" * 55)

    briefing = generate_briefing()
    fecha_bonita = datetime.now().strftime("%d %b %Y")
    subject = f"✦ YSL Intelligence Briefing · {fecha_bonita}"
    html = render_email_html(briefing, RECIPIENT_NAME)

    if TEST_MODE:
        log.info("TEST MODE — email no enviado. Briefing generado:")
        log.info(json.dumps(briefing, ensure_ascii=False, indent=2))
    else:
        send_email(html, subject)

    log.info("Agente completado 🎉")

if __name__ == "__main__":
    main()
