#!/usr/bin/env python3
"""
YSL Beauty Intelligence Briefing Agent
---------------------------------------
Usa Anthropic Claude API con búsqueda web integrada.
Lee credenciales desde variables de entorno (GitHub Secrets en producción).
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

ANTHROPIC_API_KEY  = get_env("ANTHROPIC_API_KEY")
GMAIL_USER         = get_env("GMAIL_USER")
GMAIL_APP_PASSWORD = get_env("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = get_env("RECIPIENT_EMAIL")
RECIPIENT_NAME     = get_env("RECIPIENT_NAME")
TEST_MODE          = os.environ.get("TEST_MODE", "false").lower() == "true"

# ─── Prompts ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres un agente de inteligencia estratégica de marketing de lujo, especializado en YSL Beauty y el universo L'Oréal Luxe.

Tu destinataria es una Brand Manager de YSL Beauty que trabaja tanto en perfumería/fragancias como en maquillaje/cosmética. Necesita este briefing para:
- Estar al día de todo lo relevante en su industria
- Encontrar inspiración y referencias para su trabajo diario
- Tener contenido y ángulos interesantes para compartir en LinkedIn
- Anticipar movimientos del mercado y de la competencia

Usas búsqueda web para encontrar noticias REALES y recientes de los últimos 7 días. Nunca inventes noticias.

Responde ÚNICAMENTE con un objeto JSON válido, sin markdown, sin texto adicional, sin backticks.

Estructura exacta:
{
  "semana": "DD MMM YYYY",
  "insight_semana": "Una observación estratégica profunda sobre el momento actual del sector lujo-belleza. No un titular, sino una lectura del mercado que una experta valoraría (máx 35 palabras)",
  "estado_del_mercado": "Párrafo breve (3-4 frases) con el pulso general del sector esta semana: qué está pasando a nivel macro, qué conversación domina la industria, qué tensiones o oportunidades emergen",
  "articulos": [
    {
      "categoria": "COLABORACIONES | TENDENCIAS | CAMPAÑAS | COMPETENCIA | INFLUENCERS | RETAIL & DIGITAL | FRAGANCIAS | MAQUILLAJE",
      "titulo": "Título claro y descriptivo de la noticia",
      "que_paso": "Descripción factual de lo ocurrido (2-3 frases). Solo hechos.",
      "por_que_importa": "Análisis estratégico: qué significa esto para el sector, qué patrón revela, qué implica a medio plazo (2-3 frases)",
      "angulo_ysl": "Cómo afecta o debería afectar específicamente a YSL Beauty o L'Oréal Luxe. Qué puede aprender o hacer al respecto (1-2 frases)",
      "linkedin_hook": "Una frase provocadora o insight que podría usar para abrir un post de LinkedIn sobre esta noticia",
      "fuente": "Nombre del medio",
      "url": "URL si está disponible, si no cadena vacía"
    }
  ],
  "competencia_radar": {
    "resumen": "Qué están haciendo esta semana los competidores clave (Dior Beauty, Chanel, Givenchy, Tom Ford, Armani Beauty, Lancôme). Movimientos destacados (3-4 frases)",
    "amenaza_oportunidad": "El movimiento competitivo más relevante de la semana y qué respuesta o inspiración sugiere para YSL"
  },
  "tendencia_emergente": {
    "nombre": "Nombre corto de la tendencia",
    "descripcion": "Qué es, de dónde viene, por qué está ganando fuerza ahora (3-4 frases)",
    "relevancia_practica": "Cómo podría materializarse esta tendencia en campañas, producto o comunicación para YSL Beauty"
  },
  "digital_social": "Qué está pasando esta semana en TikTok, Instagram y LinkedIn en el espacio beauty-lujo: formatos que funcionan, creadores que despuntan, conversaciones que dominan (3-4 frases)",
  "accion_sugerida": "Una acción concreta, específica y accionable esta semana. No genérica — algo real que una Brand Manager de YSL podría hacer o proponer en su equipo basándose en las noticias de esta semana",
  "para_linkedin": {
    "tema": "El tema de la semana más potente para compartir en LinkedIn desde una perspectiva experta",
    "angulo": "El ángulo o punto de vista que haría destacar el post (no el obvio, sino el interesante)",
    "opening_line": "Primera línea del post lista para usar — que enganche y genere curiosidad"
  },
  "frase_inspiracion": "Cita o frase relacionada con creatividad, belleza, lujo o estrategia. Puede ser de un creativo, diseñador, directivo del sector o pensador relevante"
}

Incluye entre 6 y 8 artículos, equilibrados entre fragancias y maquillaje, y entre los distintos competidores. Prioriza siempre noticias reales de los últimos 7 días."""


def build_user_prompt() -> str:
    today = datetime.now().strftime("%A %d de %B de %Y")
    return f"""Genera el briefing semanal de inteligencia para una Brand Manager de YSL Beauty en L'Oréal Luxe.
Fecha de hoy: {today}.

Busca noticias reales de los últimos 7 días en estas áreas:

FRAGANCIAS & PERFUMERÍA:
- Lanzamientos de fragancias de lujo (YSL, Dior, Chanel, Givenchy, Tom Ford, Maison Margiela, Armani)
- Colaboraciones de perfumería con artistas, celebrities o marcas de moda
- Tendencias olfativas emergentes y movimientos en el mercado de nicho
- Campañas de comunicación de fragancias destacadas
- Innovaciones en packaging, retail experience o storytelling de fragancia

MAQUILLAJE & COSMÉTICA DE LUJO:
- Lanzamientos de maquillaje de lujo y campañas asociadas
- Movimientos de YSL Beauté (Rouge Sur Mesure, Libre, Black Opium, cualquier novedad)
- Tendencias de maquillaje que están ganando tracción en redes sociales
- Colaboraciones beauty con artistas, diseñadores o influencers de lujo
- Innovaciones en fórmulas, tecnología beauty o experiencia de compra

MARKETING & ESTRATEGIA:
- Campañas de marketing de lujo especialmente creativas o disruptivas
- Movimientos estratégicos de Dior Beauty, Chanel Beauty, Givenchy Beauty, Tom Ford Beauty, Armani Beauty, Lancôme
- Ambassador deals y fichajes de embajadores en el sector lujo
- Estrategias digitales y de redes sociales de marcas de lujo
- Noticias de L'Oréal Group relevantes para el segmento de lujo

DIGITAL & CULTURA:
- Tendencias en TikTok e Instagram relacionadas con beauty de lujo
- Creadores de contenido beauty de lujo que están despuntando
- Colaboraciones entre moda y belleza de lujo
- Momentos culturales (alfombras rojas, fashion weeks, eventos) con relevancia beauty
- Conversaciones en LinkedIn sobre marketing de lujo y belleza

Asegúrate de que todas las noticias sean reales, verificadas y de los últimos 7 días."""


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
        "COLABORACIONES":   "#7c6fcd",
        "TENDENCIAS":       "#c9a84c",
        "CAMPAÑAS":         "#d4537e",
        "COMPETENCIA":      "#4a9eca",
        "INFLUENCERS":      "#e8854a",
        "RETAIL & DIGITAL": "#5aad8a",
        "FRAGANCIAS":       "#9b6fa8",
        "MAQUILLAJE":       "#d4537e",
    }
    cat_icons = {
        "COLABORACIONES":   "🤝",
        "TENDENCIAS":       "📈",
        "CAMPAÑAS":         "📣",
        "COMPETENCIA":      "👁️",
        "INFLUENCERS":      "⭐",
        "RETAIL & DIGITAL": "📱",
        "FRAGANCIAS":       "🌸",
        "MAQUILLAJE":       "💄",
    }

    for art in data.get("articulos", []):
        cat    = art.get("categoria", "").upper()
        color  = cat_colors.get(cat, "#888")
        icon   = cat_icons.get(cat, "•")
        url    = art.get("url", "")
        titulo = art.get("titulo", "")
        titulo_html   = f'<a href="{url}" style="color:#1a1a1a;text-decoration:none;">{titulo}</a>' if url else titulo
        linkedin_hook = art.get("linkedin_hook", "")

        articulos_html += f"""
        <div style="border:1px solid #e8e0d4;border-radius:8px;padding:18px 22px;margin-bottom:14px;background:#fff;">
          <div style="font-size:10px;letter-spacing:0.2em;text-transform:uppercase;color:{color};font-weight:600;margin-bottom:8px;">
            {icon} {cat}
          </div>
          <div style="font-family:'Georgia',serif;font-size:17px;font-weight:400;color:#1a1a1a;line-height:1.3;margin-bottom:10px;">
            {titulo_html}
          </div>
          <div style="font-size:13px;color:#333;line-height:1.7;margin-bottom:6px;">
            <strong style="font-size:10px;letter-spacing:0.1em;text-transform:uppercase;color:#999;">Qué pasó</strong><br>
            {art.get('que_paso', '')}
          </div>
          <div style="font-size:13px;color:#444;line-height:1.7;margin-bottom:6px;margin-top:10px;">
            <strong style="font-size:10px;letter-spacing:0.1em;text-transform:uppercase;color:#999;">Por qué importa</strong><br>
            {art.get('por_que_importa', '')}
          </div>
          <div style="font-size:12.5px;color:{color};line-height:1.6;margin-top:10px;font-style:italic;">
            ✦ <strong>Ángulo YSL:</strong> {art.get('angulo_ysl', '')}
          </div>
          {f'<div style="background:#faf7f2;border-left:3px solid #c9a84c;padding:10px 14px;margin-top:10px;border-radius:0 6px 6px 0;font-size:12px;color:#5a4a1a;font-style:italic;">💼 <strong>LinkedIn hook:</strong> {linkedin_hook}</div>' if linkedin_hook else ''}
          <div style="font-size:11px;color:#bbb;margin-top:10px;">
            {art.get('fuente', '')}
          </div>
        </div>"""

    competencia = data.get("competencia_radar", {})
    competencia_html = f"""
    <tr><td style="background:#fff;padding:0 36px 24px;">
      <div style="height:0.5px;background:#e8e0d4;margin-bottom:20px;"></div>
      <div style="font-size:10px;letter-spacing:0.3em;text-transform:uppercase;color:#4a9eca;margin-bottom:12px;">👁️ Radar competencia</div>
      <div style="font-size:13px;color:#333;line-height:1.7;margin-bottom:10px;">{competencia.get('resumen', '')}</div>
      <div style="background:#f0f6ff;border-left:3px solid #4a9eca;padding:12px 16px;border-radius:0 6px 6px 0;font-size:12.5px;color:#1a3a5c;font-style:italic;">
        ⚡ {competencia.get('amenaza_oportunidad', '')}
      </div>
    </td></tr>""" if competencia else ""

    tendencia = data.get("tendencia_emergente", {})
    tendencia_html = f"""
    <tr><td style="background:#fff;padding:0 36px 24px;">
      <div style="height:0.5px;background:#e8e0d4;margin-bottom:20px;"></div>
      <div style="font-size:10px;letter-spacing:0.3em;text-transform:uppercase;color:#c9a84c;margin-bottom:6px;">📡 Tendencia emergente</div>
      <div style="font-family:'Georgia',serif;font-size:16px;color:#1a1a1a;margin-bottom:10px;">{tendencia.get('nombre', '')}</div>
      <div style="font-size:13px;color:#444;line-height:1.7;margin-bottom:10px;">{tendencia.get('descripcion', '')}</div>
      <div style="font-size:12.5px;color:#7a5a00;font-style:italic;">✦ {tendencia.get('relevancia_practica', '')}</div>
    </td></tr>""" if tendencia else ""

    digital = data.get("digital_social", "")
    digital_html = f"""
    <tr><td style="background:#fff;padding:0 36px 24px;">
      <div style="height:0.5px;background:#e8e0d4;margin-bottom:20px;"></div>
      <div style="font-size:10px;letter-spacing:0.3em;text-transform:uppercase;color:#5aad8a;margin-bottom:10px;">📱 Digital & Social esta semana</div>
      <div style="font-size:13px;color:#333;line-height:1.7;">{digital}</div>
    </td></tr>""" if digital else ""

    linkedin = data.get("para_linkedin", {})
    linkedin_html = f"""
    <tr><td style="background:#faf7f2;padding:24px 36px;">
      <div style="font-size:10px;letter-spacing:0.3em;text-transform:uppercase;color:#c9a84c;margin-bottom:12px;">💼 Para tu LinkedIn esta semana</div>
      <div style="font-size:13px;color:#333;margin-bottom:6px;"><strong>Tema:</strong> {linkedin.get('tema', '')}</div>
      <div style="font-size:13px;color:#555;margin-bottom:12px;"><strong>Ángulo:</strong> {linkedin.get('angulo', '')}</div>
      <div style="background:#fff;border:1px solid #e8e0d4;border-radius:8px;padding:14px 18px;font-family:'Georgia',serif;font-size:15px;color:#1a1a1a;font-style:italic;line-height:1.5;">
        "{linkedin.get('opening_line', '')}"
      </div>
    </td></tr>""" if linkedin else ""

    frase = data.get("frase_inspiracion", "")
    frase_html = f"""<tr><td style="background:#0a0a0a;padding:20px 36px;">
      <div style="font-family:Georgia,serif;font-size:14px;font-style:italic;color:#c9a84c;text-align:center;">&ldquo;{frase}&rdquo;</div>
    </td></tr>""" if frase else ""

    estado = data.get("estado_del_mercado", "")
    estado_html = f"""
    <tr><td style="background:#fff;padding:0 36px 24px;">
      <div style="font-size:13px;color:#555;line-height:1.75;font-style:italic;border-left:3px solid #e8e0d4;padding-left:16px;">{estado}</div>
    </td></tr>""" if estado else ""

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
    <tr><td style="background:#fff;padding:24px 36px 8px;">
      <div style="font-family:'Georgia',serif;font-size:15px;color:#333;font-style:italic;">Bonjour, {recipient_name} —</div>
    </td></tr>
    <tr><td style="background:#fff;padding:12px 36px 24px;">
      <div style="border-left:3px solid #c9a84c;padding:14px 18px;background:#faf7f2;border-radius:0 6px 6px 0;">
        <div style="font-size:10px;letter-spacing:0.25em;text-transform:uppercase;color:#c9a84c;font-weight:600;margin-bottom:8px;">Insight de la semana</div>
        <div style="font-family:'Georgia',serif;font-size:17px;font-style:italic;color:#1a1a1a;line-height:1.55;">{data.get('insight_semana', '')}</div>
      </div>
    </td></tr>
    {estado_html}
    <tr><td style="background:#fff;padding:0 36px;">
      <div style="height:0.5px;background:#e8e0d4;"></div>
      <div style="text-align:center;margin:-8px 0 16px;">
        <span style="background:#fff;padding:0 12px;font-size:10px;letter-spacing:0.3em;text-transform:uppercase;color:#c9a84c;">Noticias clave</span>
      </div>
    </td></tr>
    <tr><td style="background:#fff;padding:0 36px 24px;">{articulos_html}</td></tr>
    {competencia_html}
    {tendencia_html}
    {digital_html}
    <tr><td style="background:#fff;padding:0 36px 28px;">
      <div style="background:#f0faf5;border:1px solid #c5e8d5;border-radius:8px;padding:18px 22px;">
        <div style="font-size:10px;letter-spacing:0.25em;text-transform:uppercase;color:#2d7a52;font-weight:600;margin-bottom:8px;">✅ Acción para esta semana</div>
        <div style="font-size:13.5px;color:#2d4a3a;line-height:1.7;">{data.get('accion_sugerida', '')}</div>
      </div>
    </td></tr>
    {linkedin_html}
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

    briefing     = generate_briefing()
    fecha_bonita = datetime.now().strftime("%d %b %Y")
    subject      = f"✦ YSL Intelligence Briefing · {fecha_bonita}"
    html         = render_email_html(briefing, RECIPIENT_NAME)

    if TEST_MODE:
        log.info("TEST MODE — email no enviado. Briefing generado:")
        log.info(json.dumps(briefing, ensure_ascii=False, indent=2))
    else:
        send_email(html, subject)

    log.info("Agente completado 🎉")

if __name__ == "__main__":
    main()
