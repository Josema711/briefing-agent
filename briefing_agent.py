#!/usr/bin/env python3
"""
YSL Beauty Intelligence Briefing Agent
---------------------------------------
100% gratuito:
- NewsAPI para buscar noticias reales
- Groq (llama) para analizar y redactar el briefing
- Gmail SMTP para enviar el email
"""

import smtplib
import json
import os
import logging
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
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
        raise EnvironmentError(f"Variable '{key}' no definida. Revisa los GitHub Secrets.")
    return val

GROQ_API_KEY       = get_env("GROQ_API_KEY")
NEWS_API_KEY       = get_env("NEWS_API_KEY")
GMAIL_USER         = get_env("GMAIL_USER")
GMAIL_APP_PASSWORD = get_env("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = get_env("RECIPIENT_EMAIL")
RECIPIENT_NAME     = get_env("RECIPIENT_NAME")
TEST_MODE          = os.environ.get("TEST_MODE", "false").lower() == "true"

# ─── Buscar noticias con NewsAPI ─────────────────────────────────────────────

SEARCH_QUERIES = [
    "YSL Beauty fragrance makeup",
    "luxury beauty collaboration campaign",
    "Dior Chanel beauty launch",
    "L'Oreal luxury beauty marketing",
    "Tom Ford Givenchy Armani beauty",
    "luxury beauty TikTok influencer",
    "perfume fragrance launch 2025",
    "beauty marketing strategy luxury",
]

def fetch_news() -> list[dict]:
    log.info("Buscando noticias con NewsAPI...")
    all_articles = []
    seen_urls = set()

    date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    for query in SEARCH_QUERIES:
        params = urllib.parse.urlencode({
            "q": query,
            "from": date_from,
            "sortBy": "relevancy",
            "language": "en",
            "pageSize": 5,
            "apiKey": NEWS_API_KEY,
        })
        url = f"https://newsapi.org/v2/everything?{params}"

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                for art in data.get("articles", []):
                    u = art.get("url", "")
                    if u and u not in seen_urls and art.get("title") and "[Removed]" not in art.get("title", ""):
                        seen_urls.add(u)
                        all_articles.append({
                            "title":       art.get("title", ""),
                            "description": art.get("description", "") or "",
                            "source":      art.get("source", {}).get("name", ""),
                            "url":         u,
                            "publishedAt": art.get("publishedAt", ""),
                        })
        except Exception as e:
            log.warning(f"Error buscando '{query}': {e}")
            continue

    log.info(f"Noticias encontradas: {len(all_articles)}")
    return all_articles[:40]  # Máximo 40 para no exceder tokens


# ─── Analizar y redactar con Groq ────────────────────────────────────────────

SYSTEM_PROMPT = """Eres un agente de inteligencia estratégica de marketing de lujo, especializado en YSL Beauty y L'Oréal Luxe.

Tu destinataria es una Brand Manager de YSL Beauty (fragancias y maquillaje) que necesita este briefing para:
- Estar al día de su industria
- Inspiración para su trabajo diario
- Contenido para compartir en LinkedIn
- Anticipar movimientos del mercado y competencia

A partir de las noticias reales que te doy, genera un briefing semanal completo.

Responde ÚNICAMENTE con un objeto JSON válido, sin markdown, sin texto adicional, sin backticks.

Estructura exacta:
{
  "semana": "DD MMM YYYY",
  "insight_semana": "Observación estratégica profunda del momento actual del sector (máx 35 palabras)",
  "estado_del_mercado": "Pulso general del sector esta semana basado en las noticias (3-4 frases)",
  "articulos": [
    {
      "categoria": "COLABORACIONES | TENDENCIAS | CAMPAÑAS | COMPETENCIA | INFLUENCERS | RETAIL & DIGITAL | FRAGANCIAS | MAQUILLAJE",
      "titulo": "Título claro de la noticia",
      "que_paso": "Qué ocurrió exactamente (2-3 frases, solo hechos)",
      "por_que_importa": "Análisis estratégico: qué significa para el sector (2-3 frases)",
      "angulo_ysl": "Cómo afecta a YSL Beauty específicamente (1-2 frases)",
      "linkedin_hook": "Primera frase provocadora para un post de LinkedIn sobre esto",
      "fuente": "Nombre del medio",
      "url": "URL de la noticia"
    }
  ],
  "competencia_radar": {
    "resumen": "Movimientos de Dior, Chanel, Givenchy, Tom Ford, Armani, Lancôme esta semana (3-4 frases)",
    "amenaza_oportunidad": "El movimiento competitivo más relevante y qué sugiere para YSL"
  },
  "tendencia_emergente": {
    "nombre": "Nombre corto de la tendencia",
    "descripcion": "Qué es y por qué gana fuerza ahora (3-4 frases)",
    "relevancia_practica": "Cómo podría aplicarse en YSL Beauty"
  },
  "digital_social": "Qué está pasando en TikTok, Instagram y LinkedIn en beauty-lujo esta semana (3-4 frases)",
  "accion_sugerida": "Acción concreta y accionable para esta semana para una Brand Manager de YSL",
  "para_linkedin": {
    "tema": "Tema más potente para LinkedIn esta semana",
    "angulo": "Ángulo interesante, no el obvio",
    "opening_line": "Primera línea del post lista para usar"
  },
  "frase_inspiracion": "Cita de un creativo, diseñador o directivo del sector belleza-lujo"
}

Selecciona entre 6 y 8 noticias de las que te doy, las más relevantes para YSL Beauty. Usa solo noticias del listado proporcionado."""


def generate_briefing(articles: list[dict]) -> dict:
    log.info("Generando briefing con Groq...")

    articles_text = "\n\n".join([
        f"- Título: {a['title']}\n  Descripción: {a['description']}\n  Fuente: {a['source']}\n  URL: {a['url']}\n  Fecha: {a['publishedAt']}"
        for a in articles
    ])

    today = datetime.now().strftime("%A %d de %B de %Y")
    user_prompt = f"""Fecha de hoy: {today}

Aquí tienes las noticias de los últimos 7 días relacionadas con el sector beauty de lujo. Genera el briefing semanal para la Brand Manager de YSL Beauty:

{articles_text}

Recuerda: usa SOLO las noticias del listado, selecciona las más relevantes para YSL Beauty y responde únicamente con el JSON."""

    payload = json.dumps({
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens":  4096,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type":  "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())

    text_content = data["choices"][0]["message"]["content"]

    if not text_content.strip():
        raise ValueError("Groq no devolvió contenido")

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
          <div style="font-size:10px;letter-spacing:0.2em;text-transform:uppercase;color:{color};font-weight:600;margin-bottom:8px;">{icon} {cat}</div>
          <div style="font-family:'Georgia',serif;font-size:17px;font-weight:400;color:#1a1a1a;line-height:1.3;margin-bottom:10px;">{titulo_html}</div>
          <div style="font-size:13px;color:#333;line-height:1.7;margin-bottom:6px;">
            <strong style="font-size:10px;letter-spacing:0.1em;text-transform:uppercase;color:#999;">Qué pasó</strong><br>{art.get('que_paso', '')}
          </div>
          <div style="font-size:13px;color:#444;line-height:1.7;margin-bottom:6px;margin-top:10px;">
            <strong style="font-size:10px;letter-spacing:0.1em;text-transform:uppercase;color:#999;">Por qué importa</strong><br>{art.get('por_que_importa', '')}
          </div>
          <div style="font-size:12.5px;color:{color};line-height:1.6;margin-top:10px;font-style:italic;">✦ <strong>Ángulo YSL:</strong> {art.get('angulo_ysl', '')}</div>
          {f'<div style="background:#faf7f2;border-left:3px solid #c9a84c;padding:10px 14px;margin-top:10px;border-radius:0 6px 6px 0;font-size:12px;color:#5a4a1a;font-style:italic;">💼 <strong>LinkedIn hook:</strong> {linkedin_hook}</div>' if linkedin_hook else ''}
          <div style="font-size:11px;color:#bbb;margin-top:10px;">{art.get('fuente', '')}</div>
        </div>"""

    competencia = data.get("competencia_radar", {})
    competencia_html = f"""
    <tr><td style="background:#fff;padding:0 36px 24px;">
      <div style="height:0.5px;background:#e8e0d4;margin-bottom:20px;"></div>
      <div style="font-size:10px;letter-spacing:0.3em;text-transform:uppercase;color:#4a9eca;margin-bottom:12px;">👁️ Radar competencia</div>
      <div style="font-size:13px;color:#333;line-height:1.7;margin-bottom:10px;">{competencia.get('resumen', '')}</div>
      <div style="background:#f0f6ff;border-left:3px solid #4a9eca;padding:12px 16px;border-radius:0 6px 6px 0;font-size:12.5px;color:#1a3a5c;font-style:italic;">⚡ {competencia.get('amenaza_oportunidad', '')}</div>
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
      <div style="background:#fff;border:1px solid #e8e0d4;border-radius:8px;padding:14px 18px;font-family:'Georgia',serif;font-size:15px;color:#1a1a1a;font-style:italic;line-height:1.5;">"{linkedin.get('opening_line', '')}"</div>
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
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>YSL Intelligence Briefing</title></head>
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
      <div style="font-size:10px;color:#444;text-align:center;margin-top:4px;">Powered by Groq + NewsAPI · 100% gratuito</div>
    </td></tr>
  </table>
  </td></tr>
</table>
</body></html>"""


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

    articles     = fetch_news()
    briefing     = generate_briefing(articles)
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
