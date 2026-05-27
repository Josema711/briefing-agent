#!/usr/bin/env python3
"""
YSL Beauty Intelligence Briefing Agent
---------------------------------------
- Busca noticias worldwide de lujo, beauty y moda
- Prioriza noticias de España si las hay
- Cubre: tendencias, novedades, noticias de casas de lujo, YSL Beauty y competencia
- Genera 2 posts de LinkedIn listos para publicar
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
from groq import Groq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("briefing_agent.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

def get_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise EnvironmentError(f"Variable '{key}' no definida.")
    return val

GROQ_API_KEY       = get_env("GROQ_API_KEY")
NEWS_API_KEY       = get_env("NEWS_API_KEY")
GMAIL_USER         = get_env("GMAIL_USER")
GMAIL_APP_PASSWORD = get_env("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = get_env("RECIPIENT_EMAIL")
RECIPIENT_NAME     = get_env("RECIPIENT_NAME")
TEST_MODE          = os.environ.get("TEST_MODE", "false").lower() == "true"

# ─── Búsquedas ───────────────────────────────────────────────────────────────

QUERIES = [
    # YSL Beauty y L'Oréal Luxe
    "YSL Beauty Saint Laurent campaign launch",
    "L'Oreal Luxe luxury beauty news",
    # Competencia directa
    "Dior Beauty Chanel beauty campaign 2025",
    "Tom Ford beauty Givenchy Armani beauty",
    "Lancôme luxury beauty launch",
    # Tendencias beauty y moda lujo
    "luxury beauty trend makeup fragrance",
    "luxury fashion house beauty collaboration",
    "perfume fragrance luxury launch 2025",
    # Casas de lujo (moda + beauty)
    "LVMH Kering luxury brand news",
    "luxury fashion beauty news",
]

ES_QUERIES = [
    "YSL belleza lujo España",
    "belleza lujo tendencia moda España",
    "moda lujo beauty noticias España",
]

def fetch_articles(queries: list, language: str = "en", sources: str = None, max_per_query: int = 5) -> list[dict]:
    articles = []
    seen_urls = set()
    date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    for query in queries:
        params = {
            "q": query,
            "from": date_from,
            "sortBy": "relevancy",
            "language": language,
            "pageSize": max_per_query,
            "apiKey": NEWS_API_KEY,
        }
        if sources:
            params["sources"] = sources

        url = f"https://newsapi.org/v2/everything?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                for art in data.get("articles", []):
                    u = art.get("url", "")
                    title = art.get("title", "")
                    if u and u not in seen_urls and title and "[Removed]" not in title:
                        seen_urls.add(u)
                        articles.append({
                            "title":       title,
                            "description": art.get("description", "") or "",
                            "source":      art.get("source", {}).get("name", ""),
                            "url":         u,
                            "publishedAt": art.get("publishedAt", ""),
                        })
        except Exception as e:
            log.warning(f"Error buscando '{query}': {e}")

    return articles


def fetch_news() -> tuple[list, list]:
    log.info("Buscando noticias worldwide de lujo y beauty...")
    global_articles = fetch_articles(QUERIES, language="en", max_per_query=5)
    log.info(f"Artículos globales: {len(global_articles)}")

    log.info("Buscando noticias en España...")
    seen = {a["url"] for a in global_articles}
    es_raw = fetch_articles(ES_QUERIES, language="es", max_per_query=5)
    es_articles = [a for a in es_raw if a["url"] not in seen]
    log.info(f"Artículos España: {len(es_articles)}")

    return global_articles[:30], es_articles[:10]


# ─── Prompts ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres el asistente semanal de una chica en prácticas como Brand Manager en YSL Beauty España (L'Oréal Luxe).

A partir de las noticias que recibes, genera un reporte semanal en español que cubra:
1. TENDENCIAS — qué está trending en beauty y moda de lujo
2. NOVEDADES — lanzamientos, campañas, colecciones nuevas
3. NOTICIAS IMPORTANTES de casas de lujo (tanto moda como beauty)
4. YSL BEAUTY Y COMPETENCIA — movimientos de YSL, Dior, Chanel, Tom Ford, Givenchy, Armani, Lancôme

Si hay noticias de España, dales prioridad y colócalas primero dentro de cada sección.

Además genera exactamente 2 posts de LinkedIn completos y listos para publicar, basados en las noticias más interesantes. Los posts deben sonar como escritos por una profesional joven del sector, con criterio y punto de vista propio — no como un comunicado de prensa.

Tono general: cercano, directo, profesional sin ser aburrido.

Responde ÚNICAMENTE con JSON válido, sin markdown, sin backticks.

{
  "semana": "DD de MMMM de YYYY",
  "frase_semana": "Una frase que capture el espíritu beauty-lujo de esta semana (máx 15 palabras)",

  "tendencias": [
    {
      "titulo": "Nombre de la tendencia en español",
      "descripcion": "Qué es, por qué está ganando fuerza ahora y qué implica para el sector (3-4 frases)",
      "es_españa": true/false
    }
  ],

  "novedades": [
    {
      "marca": "Nombre de la marca",
      "titulo": "Título claro de la novedad",
      "descripcion": "Qué han lanzado o anunciado y por qué es relevante (2-3 frases)",
      "fuente": "Nombre del medio",
      "url": "URL",
      "es_españa": true/false
    }
  ],

  "noticias_casas_lujo": [
    {
      "casa": "Nombre de la casa de lujo",
      "titulo": "Título de la noticia",
      "descripcion": "Qué pasó y qué significa para el sector (2-3 frases)",
      "fuente": "Nombre del medio",
      "url": "URL",
      "es_españa": true/false
    }
  ],

  "ysl_y_competencia": [
    {
      "marca": "YSL Beauty | Dior Beauty | Chanel | Tom Ford | Givenchy | Armani Beauty | Lancôme | otra",
      "titulo": "Título de la noticia",
      "descripcion": "Qué hizo esta marca esta semana y por qué importa (2-3 frases)",
      "fuente": "Nombre del medio",
      "url": "URL",
      "es_españa": true/false
    }
  ],

  "posts_linkedin": [
    {
      "basado_en": "Título de la noticia o tendencia en la que se basa",
      "post": "El post completo de LinkedIn, listo para copiar y publicar. Entre 150-250 palabras. Debe tener gancho en la primera frase, desarrollar un punto de vista propio, y terminar con una pregunta o reflexión que invite a la interacción. Incluir 3-5 hashtags al final."
    },
    {
      "basado_en": "Título de la noticia o tendencia en la que se basa",
      "post": "Segundo post completo. Diferente tono o formato al primero — puede ser más personal, más analítico, o más provocador."
    }
  ]
}

Cada sección puede tener entre 2 y 4 items. Si no hay noticias suficientes para una sección, incluye solo las que haya (mínimo 1).
Si una noticia es de España, ponla la primera dentro de su sección."""


def generate_briefing(global_articles: list, es_articles: list) -> dict:
    log.info("Generando briefing con Groq...")
    client = Groq(api_key=GROQ_API_KEY)

    def fmt(articles):
        if not articles:
            return "No se encontraron artículos."
        return "\n\n".join([
            f"• {a['title']}\n  {a['description']}\n  Fuente: {a['source']} | {a['url']} | {a['publishedAt'][:10]}"
            for a in articles
        ])

    today = datetime.now().strftime("%d de %B de %Y")
    user_prompt = f"""Fecha: {today}

=== NOTICIAS WORLDWIDE (inglés) ===
{fmt(global_articles)}

=== NOTICIAS DE ESPAÑA (priorizar) ===
{fmt(es_articles)}

Genera el reporte semanal completo con los 2 posts de LinkedIn. Solo JSON."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=4096,
    )

    text = response.choices[0].message.content
    if not text.strip():
        raise ValueError("Groq no devolvió contenido")

    clean = text.replace("```json", "").replace("```", "").strip()
    briefing = json.loads(clean)
    log.info("Briefing generado correctamente")
    return briefing


# ─── Paleta nude/terracota ───────────────────────────────────────────────────

C_BG         = "#faf8f5"
C_WHITE      = "#ffffff"
C_HEADER_BG  = "#f0ebe3"
C_ACCENT     = "#c17f5e"
C_ACCENT2    = "#d4a090"
C_TEXT       = "#2d2d2d"
C_TEXT_LIGHT = "#7a6f6a"
C_BORDER     = "#e8e0d8"
C_TAG_BG     = "#f5ede8"
C_GREEN_BG   = "#f0f7f2"
C_GREEN      = "#5a8a6a"

def spain_badge() -> str:
    return f'<span style="background:#f0e8f5;color:#8a6a9b;font-size:10px;font-weight:600;padding:2px 8px;border-radius:20px;margin-left:8px;letter-spacing:0.1em;">🇪🇸 España</span>'

def section_header(title: str, emoji: str, color: str = None) -> str:
    c = color or C_ACCENT
    return f"""<div style="font-size:11px;font-weight:600;letter-spacing:0.2em;text-transform:uppercase;color:{c};border-bottom:2px solid {c};padding-bottom:8px;margin-bottom:16px;">{emoji} {title}</div>"""

def news_card(item: dict, show_marca: bool = False) -> str:
    es = item.get("es_españa", False)
    url = item.get("url", "")
    titulo = item.get("titulo", item.get("title", ""))
    titulo_html = f'<a href="{url}" style="color:{C_TEXT};text-decoration:none;">{titulo}</a>' if url else titulo
    marca = item.get("marca", item.get("casa", ""))

    return f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:16px 20px;margin-bottom:10px;">
      {f'<div style="font-size:11px;font-weight:700;color:{C_ACCENT};letter-spacing:0.1em;text-transform:uppercase;margin-bottom:6px;">{marca}{spain_badge() if es else ""}</div>' if show_marca and marca else (spain_badge() if es else "")}
      <div style="font-size:15px;font-weight:600;color:{C_TEXT};line-height:1.35;margin-bottom:8px;">{titulo_html}</div>
      <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.7;">{item.get('descripcion', '')}</div>
      <div style="font-size:11px;color:#ccc;margin-top:8px;font-style:italic;">{item.get('fuente', '')}</div>
    </div>"""

def trend_card(item: dict) -> str:
    es = item.get("es_españa", False)
    return f"""<div style="background:{C_TAG_BG};border-radius:10px;padding:16px 20px;margin-bottom:10px;border-left:3px solid {C_ACCENT};">
      <div style="font-size:15px;font-weight:600;color:{C_TEXT};margin-bottom:8px;">{item.get('titulo', '')}{spain_badge() if es else ''}</div>
      <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.7;">{item.get('descripcion', '')}</div>
    </div>"""

def linkedin_card(post_data: dict, num: int) -> str:
    post = post_data.get("post", "").replace("\n", "<br>")
    basado = post_data.get("basado_en", "")
    return f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:20px;margin-bottom:12px;">
      <div style="display:flex;align-items:center;margin-bottom:12px;">
        <div style="background:{C_ACCENT};color:#fff;font-size:11px;font-weight:700;width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin-right:10px;text-align:center;line-height:24px;">{num}</div>
        <div style="font-size:11px;color:{C_TEXT_LIGHT};font-style:italic;">Inspirado en: {basado}</div>
      </div>
      <div style="background:{C_TAG_BG};border-radius:8px;padding:16px;font-size:13px;color:{C_TEXT};line-height:1.75;">{post}</div>
      <div style="margin-top:10px;text-align:right;">
        <a href="https://www.linkedin.com/post/new/" style="background:{C_ACCENT};color:#fff;font-size:11px;font-weight:600;padding:6px 14px;border-radius:20px;text-decoration:none;letter-spacing:0.05em;">Publicar en LinkedIn →</a>
      </div>
    </div>"""


def render_email_html(data: dict, recipient_name: str) -> str:
    fecha = data.get("semana", datetime.now().strftime("%d de %B de %Y"))
    frase = data.get("frase_semana", "")

    # Tendencias
    tendencias_html = "".join([trend_card(t) for t in data.get("tendencias", [])])
    tendencias_section = f"""
    <tr><td style="background:{C_WHITE};padding:24px 28px 8px;">
      {section_header("Tendencias", "📈")}
      {tendencias_html}
    </td></tr>""" if tendencias_html else ""

    # Novedades
    novedades_html = "".join([news_card(n, show_marca=True) for n in data.get("novedades", [])])
    novedades_section = f"""
    <tr><td style="background:{C_WHITE};padding:16px 28px 8px;">
      {section_header("Novedades", "✨")}
      {novedades_html}
    </td></tr>""" if novedades_html else ""

    # Noticias casas de lujo
    casas_html = "".join([news_card(n, show_marca=True) for n in data.get("noticias_casas_lujo", [])])
    casas_section = f"""
    <tr><td style="background:{C_WHITE};padding:16px 28px 8px;">
      {section_header("Casas de lujo", "🏛️")}
      {casas_html}
    </td></tr>""" if casas_html else ""

    # YSL y competencia
    comp_html = "".join([news_card(n, show_marca=True) for n in data.get("ysl_y_competencia", [])])
    comp_section = f"""
    <tr><td style="background:{C_WHITE};padding:16px 28px 8px;">
      {section_header("YSL Beauty & Competencia", "👁️", "#9b6e8a")}
      {comp_html}
    </td></tr>""" if comp_html else ""

    # Posts LinkedIn
    linkedin_posts = data.get("posts_linkedin", [])
    linkedin_cards = "".join([linkedin_card(p, i+1) for i, p in enumerate(linkedin_posts)])
    linkedin_section = f"""
    <tr><td style="background:{C_TAG_BG};padding:24px 28px;">
      {section_header("Tus posts de LinkedIn esta semana", "💼", C_ACCENT)}
      <div style="font-size:12px;color:{C_TEXT_LIGHT};margin-bottom:16px;margin-top:-10px;">Listos para copiar y publicar — edítalos como quieras</div>
      {linkedin_cards}
    </td></tr>""" if linkedin_cards else ""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Beauty Briefing Semanal</title>
</head>
<body style="margin:0;padding:0;background:{C_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{C_BG};padding:24px 0 40px;">
  <tr><td align="center">
  <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

    <tr><td style="background:{C_HEADER_BG};border-radius:12px 12px 0 0;padding:28px 28px 22px;">
      <div style="font-size:11px;font-weight:500;letter-spacing:0.2em;text-transform:uppercase;color:{C_TEXT_LIGHT};margin-bottom:6px;">Beauty Briefing · {fecha}</div>
      <div style="font-size:26px;font-weight:700;color:{C_TEXT};letter-spacing:-0.02em;line-height:1.2;">Tu reporte semanal<br>de lujo y beauty</div>
      {f'<div style="font-size:13px;color:{C_ACCENT};margin-top:10px;font-style:italic;">{frase}</div>' if frase else ''}
    </td></tr>

    <tr><td style="background:{C_WHITE};padding:18px 28px 8px;">
<div style="font-size:13.5px;color:{C_TEXT};line-height:1.75;">Hola {recipient_name} 💛<br><br>Tu novio te ha preparado este correo para que empieces bien la semana — aquí tienes lo más importante en tendencias, novedades, YSL y competencia, y tus dos posts de LinkedIn listos para publicar.<br><br><span style="font-size:12px;color:{C_ACCENT};font-style:italic;">(te quiero)</span></div>    </td></tr>

    {tendencias_section}
    {novedades_section}
    {casas_section}
    {comp_section}
    {linkedin_section}

    <tr><td style="background:{C_WHITE};border-radius:0 0 12px 12px;padding:16px 28px 24px;">
      <div style="border-top:1px solid {C_BORDER};padding-top:16px;font-size:11px;color:#ccc;text-align:center;">Beauty Briefing semanal · {fecha} · Generado con IA</div>
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
    msg["From"]    = f"Beauty Briefing <{GMAIL_USER}>"
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
    log.info("✅ Email enviado correctamente")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 55)
    log.info("Beauty Briefing Agent — inicio")
    log.info(f"TEST_MODE: {TEST_MODE}")
    log.info("=" * 55)

    global_articles, es_articles = fetch_news()
    briefing     = generate_briefing(global_articles, es_articles)
    fecha_bonita = datetime.now().strftime("%d de %B de %Y")
    subject      = f"✨ Tu beauty briefing · {fecha_bonita}"
    html         = render_email_html(briefing, RECIPIENT_NAME)

    if TEST_MODE:
        log.info("TEST MODE — email no enviado. Briefing generado:")
        log.info(json.dumps(briefing, ensure_ascii=False, indent=2))
    else:
        send_email(html, subject)

    log.info("Agente completado 🎉")

if __name__ == "__main__":
    main()
