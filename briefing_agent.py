#!/usr/bin/env python3
"""
YSL Beauty Intelligence Briefing Agent
---------------------------------------
Prioridad: medios españoles (vogue.es, elle.es, harpersbazaar.es)
Complemento: noticias internacionales solo si son suficientemente relevantes
Foco: estar al día, ideas para LinkedIn, perspectiva de alguien en prácticas en YSL Beauty España
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

# ─── Búsquedas ───────────────────────────────────────────────────────────────

# Fuentes españolas principales
ES_SOURCES = "vogue.es,elle.es,harpersbazaar.es"

ES_QUERIES = [
    "belleza maquillaje tendencia",
    "perfume fragancia lujo",
    "YSL L'Oreal belleza España",
    "colaboracion belleza moda",
    "beauty marketing campaña",
]

# Búsquedas internacionales — solo lo más relevante del sector
INTL_QUERIES = [
    "YSL Beauty campaign collaboration 2025",
    "luxury beauty trend launch",
    "Dior Chanel beauty marketing",
    "beauty influencer luxury brand",
    "fragrance perfume launch luxury",
]

INTL_SOURCES = "vogue.com,harpersbazaar.com,elle.com,wwd.com,beautyinc.com"


def fetch_articles(queries: list, sources: str = None, language: str = "es", max_per_query: int = 5) -> list[dict]:
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
    # 1. Fuentes españolas — prioritarias
    log.info("Buscando en vogue.es, elle.es, harpersbazaar.es...")
    es_articles = fetch_articles(ES_QUERIES, sources=ES_SOURCES, language="es", max_per_query=6)
    log.info(f"Artículos españoles: {len(es_articles)}")

    # 2. Internacional — complemento, solo si aporta
    log.info("Buscando fuentes internacionales de referencia...")
    seen = {a["url"] for a in es_articles}
    intl_raw = fetch_articles(INTL_QUERIES, sources=INTL_SOURCES, language="en", max_per_query=5)
    intl_articles = [a for a in intl_raw if a["url"] not in seen]
    log.info(f"Artículos internacionales candidatos: {len(intl_articles)}")

    return es_articles[:20], intl_articles[:20]


# ─── Prompts ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres el asistente semanal de una chica que está haciendo prácticas como Brand Manager en YSL Beauty España (L'Oréal Luxe). Trabaja en marketing de belleza de lujo — fragancias y maquillaje.

Tu objetivo es ayudarla a:
1. Estar al día de lo que pasa en su sector en España y en el mundo
2. Tener ideas concretas para proponer en el trabajo
3. Generar contenido interesante para su LinkedIn personal (está construyendo su perfil profesional)

Recibes dos tipos de artículos:
- Artículos españoles: de vogue.es, elle.es, harpersbazaar.es — estos son la BASE del briefing
- Artículos internacionales: solo inclúyelos si son realmente relevantes, novedosos o útiles para alguien en su posición. Si no aportan nada especial, ignóralos.

El tono es cercano y directo — como si una compañera más experta te contara lo importante de la semana. Nada de lenguaje corporativo.

Responde ÚNICAMENTE con JSON válido, sin markdown, sin backticks.

{
  "semana": "DD de MMMM de YYYY",
  "frase_semana": "Una frase corta y directa que capture el espíritu beauty de esta semana (máx 15 palabras)",
  "articulos_es": [
    {
      "categoria": "TENDENCIAS | CAMPAÑAS | COLABORACIONES | FRAGANCIAS | MAQUILLAJE | MODA & BEAUTY",
      "titulo": "Título en español, claro",
      "resumen": "Qué pasó y por qué le importa a alguien en YSL Beauty España. Directo y útil (2-3 frases)",
      "idea_trabajo": "Una idea concreta que podría proponer o aplicar en su trabajo inspirada en esta noticia",
      "para_linkedin": "Frase o ángulo para un post de LinkedIn — algo que demuestre que está al día y tiene criterio",
      "fuente": "Nombre del medio",
      "url": "URL"
    }
  ],
  "articulos_intl": [
    {
      "categoria": "TENDENCIAS | CAMPAÑAS | COLABORACIONES | FRAGANCIAS | MAQUILLAJE | COMPETENCIA",
      "titulo": "Título en español",
      "por_que_importa": "Por qué esta noticia internacional merece estar aquí — qué aporta que no se ve en los medios españoles (2-3 frases)",
      "fuente": "Nombre del medio",
      "url": "URL"
    }
  ],
  "tendencia_semana": "La tendencia más relevante de esta semana explicada en 3 frases. Algo que realmente esté pasando, no genérico",
  "ideas_linkedin": [
    {
      "tema": "Tema concreto para un post",
      "angulo": "El punto de vista interesante, no el obvio",
      "primera_frase": "Primera línea del post lista para usar"
    }
  ],
  "idea_semana": "La idea más accionable de la semana — algo que podría proponer en una reunión o implementar en sus tareas esta semana"
}

Artículos españoles: incluye entre 4 y 6, los más relevantes e interesantes.
Artículos internacionales: solo los que de verdad aporten algo diferente. Pueden ser 0 si ninguno merece estar.
Ideas LinkedIn: entre 2 y 3 ideas, concretas y basadas en las noticias de esta semana."""


def generate_briefing(es_articles: list, intl_articles: list) -> dict:
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

=== ARTÍCULOS ESPAÑOLES (vogue.es, elle.es, harpersbazaar.es) ===
{fmt(es_articles)}

=== ARTÍCULOS INTERNACIONALES (solo incluir si aportan algo real) ===
{fmt(intl_articles)}

Genera el briefing semanal. Responde solo con el JSON."""

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
    log.info(f"Briefing: {len(briefing.get('articulos_es', []))} ES, {len(briefing.get('articulos_intl', []))} intl")
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

CAT_COLORS = {
    "TENDENCIAS":    "#b5725a",
    "CAMPAÑAS":      "#9b6e8a",
    "COLABORACIONES":"#7a8fa6",
    "BELLEZA":       "#c17f5e",
    "FRAGANCIAS":    "#9b8ab5",
    "MAQUILLAJE":    "#b5728a",
    "MODA & BEAUTY": "#7a9b8a",
    "COMPETENCIA":   "#8a8a8a",
}

def cat_color(cat: str) -> str:
    return CAT_COLORS.get(cat.upper(), C_ACCENT)

def article_card_es(art: dict) -> str:
    cat   = art.get("categoria", "").upper()
    color = cat_color(cat)
    url   = art.get("url", "")
    titulo = art.get("titulo", "")
    titulo_html = f'<a href="{url}" style="color:{C_TEXT};text-decoration:none;">{titulo}</a>' if url else titulo
    idea  = art.get("idea_trabajo", "")
    linkedin = art.get("para_linkedin", "")

    return f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:18px 20px;margin-bottom:12px;">
      <div style="display:inline-block;background:{C_TAG_BG};color:{color};font-size:10px;font-weight:600;letter-spacing:0.15em;text-transform:uppercase;padding:3px 10px;border-radius:20px;margin-bottom:10px;">{cat}</div>
      <div style="font-size:16px;font-weight:600;color:{C_TEXT};line-height:1.35;margin-bottom:8px;">{titulo_html}</div>
      <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.7;margin-bottom:10px;">{art.get('resumen', '')}</div>
      {f'<div style="background:#f0f7f2;border-left:3px solid #7a9b8a;padding:8px 12px;border-radius:0 6px 6px 0;font-size:12px;color:#3a5a4a;margin-bottom:8px;">💡 <strong>Idea:</strong> {idea}</div>' if idea else ''}
      {f'<div style="background:{C_TAG_BG};border-left:3px solid {C_ACCENT2};padding:8px 12px;border-radius:0 6px 6px 0;font-size:12px;color:{C_ACCENT};font-style:italic;">💼 <strong>LinkedIn:</strong> {linkedin}</div>' if linkedin else ''}
      <div style="font-size:11px;color:#bbb;margin-top:10px;font-style:italic;">{art.get('fuente', '')}</div>
    </div>"""

def article_card_intl(art: dict) -> str:
    cat   = art.get("categoria", "").upper()
    color = cat_color(cat)
    url   = art.get("url", "")
    titulo = art.get("titulo", "")
    titulo_html = f'<a href="{url}" style="color:{C_TEXT};text-decoration:none;">{titulo}</a>' if url else titulo

    return f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:14px 18px;margin-bottom:10px;">
      <div style="display:inline-block;background:{C_TAG_BG};color:{color};font-size:10px;font-weight:600;letter-spacing:0.15em;text-transform:uppercase;padding:3px 10px;border-radius:20px;margin-bottom:8px;">{cat}</div>
      <div style="font-size:14px;font-weight:600;color:{C_TEXT};line-height:1.35;margin-bottom:6px;">{titulo_html}</div>
      <div style="font-size:12.5px;color:{C_TEXT_LIGHT};line-height:1.65;">{art.get('por_que_importa', '')}</div>
      <div style="font-size:11px;color:#bbb;margin-top:8px;font-style:italic;">{art.get('fuente', '')}</div>
    </div>"""

def render_email_html(data: dict, recipient_name: str) -> str:
    fecha = data.get("semana", datetime.now().strftime("%d de %B de %Y"))
    frase = data.get("frase_semana", "")

    # Artículos españoles
    es_html = "".join([article_card_es(a) for a in data.get("articulos_es", [])])

    # Artículos internacionales (solo si los hay)
    intl_items = data.get("articulos_intl", [])
    intl_html = "".join([article_card_intl(a) for a in intl_items])
    intl_section = f"""
    <tr><td style="background:{C_WHITE};padding:0 28px 20px;">
      <div style="border-top:1px solid {C_BORDER};padding-top:20px;">
        <div style="font-size:11px;font-weight:600;letter-spacing:0.2em;text-transform:uppercase;color:{C_TEXT_LIGHT};margin-bottom:4px;">Internacional</div>
        <div style="font-size:12px;color:{C_TEXT_LIGHT};margin-bottom:14px;">Lo que merece la pena saber del resto del mundo</div>
        {intl_html}
      </div>
    </td></tr>""" if intl_html else ""

    # Tendencia
    tendencia = data.get("tendencia_semana", "")
    tendencia_section = f"""
    <tr><td style="background:{C_WHITE};padding:0 28px 20px;">
      <div style="background:{C_TAG_BG};border-radius:10px;padding:18px 20px;">
        <div style="font-size:11px;font-weight:600;letter-spacing:0.2em;text-transform:uppercase;color:{C_ACCENT};margin-bottom:8px;">📈 Tendencia de la semana</div>
        <div style="font-size:13.5px;color:{C_TEXT};line-height:1.7;">{tendencia}</div>
      </div>
    </td></tr>""" if tendencia else ""

    # Ideas LinkedIn
    linkedin_items = data.get("ideas_linkedin", [])
    linkedin_cards = ""
    for idea in linkedin_items:
        linkedin_cards += f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:14px 18px;margin-bottom:10px;">
          <div style="font-size:13px;font-weight:600;color:{C_TEXT};margin-bottom:4px;">{idea.get('tema', '')}</div>
          <div style="font-size:12px;color:{C_TEXT_LIGHT};margin-bottom:10px;">{idea.get('angulo', '')}</div>
          <div style="background:{C_TAG_BG};border-radius:8px;padding:10px 14px;font-size:13px;color:{C_ACCENT};font-style:italic;">"{idea.get('primera_frase', '')}"</div>
        </div>"""
    linkedin_section = f"""
    <tr><td style="background:{C_WHITE};padding:0 28px 20px;">
      <div style="border-top:1px solid {C_BORDER};padding-top:20px;">
        <div style="font-size:11px;font-weight:600;letter-spacing:0.2em;text-transform:uppercase;color:{C_ACCENT};margin-bottom:14px;">💼 Ideas para LinkedIn esta semana</div>
        {linkedin_cards}
      </div>
    </td></tr>""" if linkedin_cards else ""

    # Idea de la semana
    idea_semana = data.get("idea_semana", "")
    idea_section = f"""
    <tr><td style="background:{C_WHITE};padding:0 28px 28px;">
      <div style="background:{C_ACCENT};border-radius:10px;padding:18px 20px;">
        <div style="font-size:11px;font-weight:600;letter-spacing:0.2em;text-transform:uppercase;color:rgba(255,255,255,0.7);margin-bottom:8px;">✨ La idea de la semana</div>
        <div style="font-size:14px;color:#fff;line-height:1.65;">{idea_semana}</div>
      </div>
    </td></tr>""" if idea_semana else ""

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

    <!-- Header -->
    <tr><td style="background:{C_HEADER_BG};border-radius:12px 12px 0 0;padding:28px 28px 22px;">
      <div style="font-size:11px;font-weight:500;letter-spacing:0.2em;text-transform:uppercase;color:{C_TEXT_LIGHT};margin-bottom:6px;">Beauty Briefing · {fecha}</div>
      <div style="font-size:26px;font-weight:700;color:{C_TEXT};letter-spacing:-0.02em;line-height:1.2;">Lo que necesitas<br>saber esta semana</div>
      {f'<div style="font-size:13px;color:{C_ACCENT};margin-top:10px;font-style:italic;">{frase}</div>' if frase else ''}
    </td></tr>

    <!-- Saludo -->
    <tr><td style="background:{C_WHITE};padding:20px 28px 8px;">
      <div style="font-size:14px;color:{C_TEXT_LIGHT};line-height:1.6;">Hola {recipient_name} 👋 Aquí tienes lo más interesante de la semana en belleza — empezando por lo que publican <strong style="color:{C_TEXT};">Vogue, Harper's Bazaar y Elle España</strong>, y lo mejor del mundo si merece la pena.</div>
    </td></tr>

    <!-- Separador España -->
    <tr><td style="background:{C_WHITE};padding:20px 28px 14px;">
      <div style="font-size:11px;font-weight:600;letter-spacing:0.2em;text-transform:uppercase;color:{C_TEXT_LIGHT};border-bottom:2px solid {C_ACCENT};padding-bottom:8px;display:inline-block;">
        🇪🇸 Vogue · Harper's Bazaar · Elle España
      </div>
    </td></tr>

    <!-- Artículos España -->
    <tr><td style="background:{C_WHITE};padding:0 28px 20px;">
      {es_html if es_html else f'<div style="color:{C_TEXT_LIGHT};font-size:13px;font-style:italic;">No se encontraron artículos españoles esta semana.</div>'}
    </td></tr>

    <!-- Tendencia -->
    {tendencia_section}

    <!-- Internacional -->
    {intl_section}

    <!-- LinkedIn -->
    {linkedin_section}

    <!-- Idea semana -->
    {idea_section}

    <!-- Footer -->
    <tr><td style="background:{C_WHITE};border-radius:0 0 12px 12px;padding:0 28px 24px;">
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

    es_articles, intl_articles = fetch_news()
    briefing     = generate_briefing(es_articles, intl_articles)
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
