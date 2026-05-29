#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import smtplib
import json
import os
import logging
import sys
import urllib.request
import urllib.parse
import re
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
TAVILY_API_KEY     = get_env("TAVILY_API_KEY")
GMAIL_USER         = get_env("GMAIL_USER")
GMAIL_APP_PASSWORD = get_env("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = get_env("RECIPIENT_EMAIL")
RECIPIENT_NAME     = get_env("RECIPIENT_NAME")
TEST_MODE = os.environ.get("TEST_MODE", "false").lower() == "true"
MEMORY_FILE = "memory.json"

def load_memory() -> dict:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"seen_urls": [], "seen_titles": [], "covered_topics": []}

def save_memory(memory: dict):
    memory["seen_urls"] = memory["seen_urls"][-400:]
    memory["seen_titles"] = memory["seen_titles"][-400:]
    memory["covered_topics"] = memory["covered_topics"][-120:]
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)
    log.info(f"Memoria guardada: {len(memory['seen_urls'])} URLs")

def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[^a-zA-Z0-9 ]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()[:90]

def filter_seen(articles: list, memory: dict) -> list:
    seen_urls = set(memory.get("seen_urls", []))
    seen_titles = set(normalize_title(t) for t in memory.get("seen_titles", []))
    fresh = []
    for a in articles:
        url = a.get("url", "")
        title = normalize_title(a.get("title", ""))
        if not url or url in seen_urls or title in seen_titles:
            continue
        fresh.append(a)
    log.info(f"Filtrado memoria: {len(articles)} -> {len(fresh)}")
    return fresh

def update_memory(memory: dict, briefing: dict):
    sections = [
        briefing.get("tendencias", []),
        briefing.get("novedades", []),
        briefing.get("noticias_casas_lujo", []),
        briefing.get("ysl_y_competencia", []),
        briefing.get("el_rincon", {}).get("items", []),
    ]
    for section in sections:
        for item in section:
            url = item.get("url", "")
            title = item.get("titulo", "")
            if url:
                memory["seen_urls"].append(url)
            if title:
                memory["seen_titles"].append(title)
            topic = item.get("marca") or item.get("casa") or title
            if topic:
                memory["covered_topics"].append(f"{topic} ({datetime.now().strftime('%Y-%m-%d')})")

SEARCH_QUERIES = [
    "YSL Beauty new campaign",
    "YSL Beauty fragrance launch",
    "YSL Beauty makeup launch",
    "YSL Beauty ambassador",
    "Chanel Beauty campaign",
    "Chanel Beauty launch",
    "Dior Beauty launch",
    "Dior Beauty celebrity campaign",
    "Prada Beauty campaign",
    "Valentino Beauty launch",
    "Armani Beauty celebrity",
    "luxury fragrance launch 2026",
    "celebrity fragrance campaign luxury",
    "beauty creator collaboration luxury",
    "beauty TikTok campaign luxury",
    "beauty pop-up luxury brand",
    "immersive beauty activation",
    "YSL Beauty Spain",
    "beauty luxury Spain Madrid",
    "fashion beauty collaboration luxury",
]

TOP_SOURCES = [
    "voguebusiness.com", "vogue.com", "harpersbazaar.com",
    "elle.com", "wwd.com", "businessoffashion.com",
    "glossy.co", "fashionista.com",
]

GOOD_SOURCES = TOP_SOURCES + [
    "beautypackaging.com", "premiumbeautynews.com",
    "cosmeticsbusiness.com", "hypebae.com", "allure.com",
    "forbes.com", "retaildive.com", "retailexchange.co.uk",
]

BAD_KEYWORDS = [
    "best beauty looks", "shopping list", "memorial day sale",
    "sale", "review", "ranking", "best products",
    "editor favorites", "gift guide", "trend tracker",
    "how to", "tutorial", "evergreen",
]

def is_actual_news(article: dict) -> bool:
    text = (article.get("title", "") + " " + article.get("description", "")).lower()
    return not any(k in text for k in BAD_KEYWORDS)

def is_within_date_range(date_str: str, days: int = 10) -> bool:
    try:
        article_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return article_date >= datetime.now() - timedelta(days=days)
    except Exception:
        return True

def tavily_search(query: str, max_results: int = 8) -> list:
    payload = json.dumps({
        "api_key": TAVILY_API_KEY,
        "query": query,
        "topic": "news",
        "search_depth": "advanced",
        "days": 10,
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    articles = []
    for r in data.get("results", []):
        published_date = r.get("published_date")
        if not published_date or not is_within_date_range(published_date):
            continue

        source = urllib.parse.urlparse(r.get("url", "")).netloc.replace("www.", "")

        # FIX: article se define ANTES de usarlo
        article = {
            "title": r.get("title", ""),
            "description": (r.get("content") or r.get("raw_content") or "")[:3500],
            "source": source,
            "url": r.get("url", ""),
            "publishedAt": published_date,
        }

        if not article["title"] or not is_actual_news(article):
            continue

        articles.append(article)

    return articles

def fetch_news(memory: dict) -> list:
    log.info("Buscando noticias con Tavily...")
    all_articles = []
    seen_urls = set()

    for query in SEARCH_QUERIES:
        try:
            results = tavily_search(query)
            for a in results:
                url = a.get("url")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                all_articles.append(a)
        except Exception as e:
            log.warning(f"Error busqueda '{query}': {e}")

    all_articles.sort(key=lambda x: (0 if x.get("source","") in TOP_SOURCES else 1, x.get("publishedAt","")))
    log.info(f"Noticias validas encontradas: {len(all_articles)}")
    fresh = filter_seen(all_articles, memory)
    return fresh[:45]

SYSTEM_PROMPT = """
Eres el asistente semanal de intelligence y tendencias para una Brand Manager en practicas de YSL Beauty Espana (L'Oreal Luxe).

Tu trabajo es seleccionar UNICAMENTE:
- lanzamientos NUEVOS
- campanas NUEVAS
- colaboraciones NUEVAS
- activaciones NUEVAS
- movimientos estrategicos NUEVOS
- tendencias emergentes REALES

REGLAS:
- SOLO usa las noticias proporcionadas. NO inventes nada.
- Siempre menciona nombre exacto de producto/coleccion/campana, celebrity, plataforma, mercado.
- Prohibido frases vagas como "la sostenibilidad sigue creciendo" o "las marcas innovan".
- Si no hay detalles concretos: NO incluyas la noticia.
- Tono: elegante, ejecutivo, moderno, insider luxury. Como Vogue Business o Business of Fashion.

POSTS LINKEDIN:
- Sonar humanos, punto de vista propio y completo, evitar cliches, parecer escritos por joven profesional luxury beauty.

Responde UNICAMENTE con JSON valido. Sin markdown. Sin backticks.

{
  "semana": "DD de MMMM de YYYY",
  "frase_semana": "Maximo 15 palabras",
  "tendencias": [{"titulo": "", "descripcion": "", "es_espana": false}],
  "novedades": [{"marca": "", "titulo": "", "descripcion": "", "fuente": "", "url": "", "es_espana": false}],
  "noticias_casas_lujo": [{"casa": "", "titulo": "", "descripcion": "", "fuente": "", "url": "", "es_espana": false}],
  "ysl_y_competencia": [{"marca": "", "titulo": "", "descripcion": "", "fuente": "", "url": "", "es_espana": false}],
  "digital_social": {
    "resumen": "",
    "campanas_destacadas": [{"marca": "", "titulo": "", "descripcion": "", "plataforma": "", "es_espana": false}],
    "radar_competencia": "",
    "tendencia_emergente": ""
  },
  "el_rincon": {
    "titulo": "",
    "intro": "",
    "items": [{"marca": "", "titulo": "", "descripcion": "", "fuente": "", "url": ""}]
  },
  "posts_linkedin": [
    {"basado_en": "", "post": ""},
    {"basado_en": "", "post": ""}
  ]
}

Maximo 4 items por seccion. Calidad > cantidad. Espana primero si aplica.
"""

def generate_briefing(articles: list, memory: dict) -> dict:
    log.info("Generando briefing con Groq...")
    client = Groq(api_key=GROQ_API_KEY)

    covered = memory.get("covered_topics", [])[-25:]
    covered_text = "\n".join(f"- {t}" for t in covered)

    # FIX: maximo 25 articulos y contenido recortado a 300 chars
    articles_text = "\n\n".join([
        f"TITULO: {a['title']}\nFUENTE: {a['source']} | {a['publishedAt'][:10]}\nURL: {a['url']}\nCONTENIDO: {a['description'][:300]}"
        for a in articles[:25]
    ])

    today = datetime.now().strftime("%d de %B de %Y")
    user_prompt = f"""Fecha: {today}

TEMAS YA CUBIERTOS:
{covered_text}

NOTICIAS:
{articles_text}

Genera el briefing semanal."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.25,
        max_tokens=4096,
    )

    text = response.choices[0].message.content
    clean = text.replace("```json", "").replace("```", "").strip()

    try:
        briefing = json.loads(clean)
    except Exception:
        log.error(clean)
        raise

    log.info("Briefing generado correctamente")
    return briefing

C_BG         = "#faf8f5"
C_WHITE      = "#ffffff"
C_HEADER_BG  = "#f0ebe3"
C_ACCENT     = "#c17f5e"
C_TEXT       = "#2d2d2d"
C_TEXT_LIGHT = "#7a6f6a"
C_BORDER     = "#e8e0d8"
C_TAG_BG     = "#f5ede8"

def spain_badge() -> str:
    return '<span style="background:#f0e8f5;color:#8a6a9b;font-size:10px;font-weight:600;padding:2px 8px;border-radius:20px;margin-left:8px;">ES</span>'

def section_header(title: str, emoji: str, color: str = None) -> str:
    c = color or C_ACCENT
    return f'<div style="font-size:11px;font-weight:600;letter-spacing:0.2em;text-transform:uppercase;color:{c};border-bottom:2px solid {c};padding-bottom:8px;margin-bottom:16px;">{emoji} {title}</div>'

def news_card(item: dict, show_marca: bool = False) -> str:
    es     = item.get("es_espana", item.get("es_españa", False))
    url    = item.get("url", "")
    titulo = item.get("titulo", item.get("title", ""))
    titulo_html = f'<a href="{url}" style="color:{C_TEXT};text-decoration:none;">{titulo}</a>' if url else titulo
    marca  = item.get("marca", item.get("casa", ""))
    return f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:16px 20px;margin-bottom:10px;">
      {f'<div style="font-size:11px;font-weight:700;color:{C_ACCENT};letter-spacing:0.1em;text-transform:uppercase;margin-bottom:6px;">{marca}{spain_badge() if es else ""}</div>' if show_marca and marca else ""}
      <div style="font-size:15px;font-weight:600;color:{C_TEXT};line-height:1.35;margin-bottom:8px;">{titulo_html}</div>
      <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.7;">{item.get("descripcion","")}</div>
      <div style="font-size:11px;color:#ccc;margin-top:8px;font-style:italic;">{item.get("fuente","")}</div>
    </div>"""

def trend_card(item: dict) -> str:
    es = item.get("es_espana", item.get("es_españa", False))
    return f"""<div style="background:{C_TAG_BG};border-radius:10px;padding:16px 20px;margin-bottom:10px;border-left:3px solid {C_ACCENT};">
      <div style="font-size:15px;font-weight:600;color:{C_TEXT};margin-bottom:8px;">{item.get("titulo","")}{spain_badge() if es else ""}</div>
      <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.7;">{item.get("descripcion","")}</div>
    </div>"""

def linkedin_card(post_data: dict, num: int) -> str:
    post   = post_data.get("post", "").replace("\n", "<br>")
    basado = post_data.get("basado_en", "")
    return f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:20px;margin-bottom:12px;">
      <div style="margin-bottom:12px;">
        <span style="background:{C_ACCENT};color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;margin-right:8px;">Post {num}</span>
        <span style="font-size:11px;color:{C_TEXT_LIGHT};font-style:italic;">Basado en: {basado}</span>
      </div>
      <div style="background:{C_TAG_BG};border-radius:8px;padding:16px;font-size:13px;color:{C_TEXT};line-height:1.75;">{post}</div>
      <div style="margin-top:12px;text-align:right;">
        <a href="https://www.linkedin.com/feed/" style="background:{C_ACCENT};color:#fff;font-size:11px;font-weight:600;padding:7px 16px;border-radius:20px;text-decoration:none;">Publicar en LinkedIn</a>
      </div>
    </div>"""

def render_email_html(data: dict, recipient_name: str) -> str:
    fecha = data.get("semana", datetime.now().strftime("%d de %B de %Y"))
    frase = data.get("frase_semana", "")

    tendencias_html = "".join([trend_card(t) for t in data.get("tendencias", [])])
    tendencias_section = f'<tr><td style="background:{C_WHITE};padding:24px 28px 8px;">{section_header("Tendencias","Aumento")}{tendencias_html}</td></tr>' if tendencias_html else ""

    novedades_html = "".join([news_card(n, True) for n in data.get("novedades", [])])
    novedades_section = f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">{section_header("Novedades","Estrella")}{novedades_html}</td></tr>' if novedades_html else ""

    casas_html = "".join([news_card(n, True) for n in data.get("noticias_casas_lujo", [])])
    casas_section = f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">{section_header("Casas de lujo","Templo")}{casas_html}</td></tr>' if casas_html else ""

    comp_html = "".join([news_card(n, True) for n in data.get("ysl_y_competencia", [])])
    comp_section = f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">{section_header("YSL Beauty & Competencia","Ojo","#9b6e8a")}{comp_html}</td></tr>' if comp_html else ""

    digital = data.get("digital_social", {})
    campanas_html = ""
    for c in digital.get("campanas_destacadas", []):
        es = c.get("es_espana", c.get("es_españa", False))
        campanas_html += f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:14px 18px;margin-bottom:10px;">
          <div style="font-size:11px;font-weight:700;color:#7a8fa6;text-transform:uppercase;margin-bottom:4px;">{c.get("marca","")} - {c.get("plataforma","")}{spain_badge() if es else ""}</div>
          <div style="font-size:14px;font-weight:600;color:{C_TEXT};margin-bottom:6px;">{c.get("titulo","")}</div>
          <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.65;">{c.get("descripcion","")}</div>
        </div>"""

    digital_section = f"""<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">
      {section_header("Digital & Social Media","Movil","#7a8fa6")}
      <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.7;margin-bottom:14px;">{digital.get("resumen","")}</div>
      {campanas_html}
      {f'<div style="background:#f0f4f8;border-left:3px solid #7a8fa6;border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:10px;"><div style="font-size:10px;font-weight:700;color:#7a8fa6;">RADAR COMPETENCIA</div><div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.6;margin-top:6px;">{digital.get("radar_competencia","")}</div></div>' if digital.get("radar_competencia") else ""}
      {f'<div style="background:{C_TAG_BG};border-left:3px solid {C_ACCENT};border-radius:0 8px 8px 0;padding:12px 16px;"><div style="font-size:10px;font-weight:700;color:{C_ACCENT};">TENDENCIA EMERGENTE</div><div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.6;margin-top:6px;">{digital.get("tendencia_emergente","")}</div></div>' if digital.get("tendencia_emergente") else ""}
    </td></tr>""" if digital else ""

    rincon = data.get("el_rincon", {})
    rincon_items_html = ""
    for item in rincon.get("items", []):
        url = item.get("url", "")
        titulo = item.get("titulo", "")
        titulo_html = f'<a href="{url}" style="color:{C_TEXT};text-decoration:none;">{titulo}</a>' if url else titulo
        rincon_items_html += f"""<div style="background:#f8f6ff;border:1px solid #e0daf0;border-radius:10px;padding:14px 18px;margin-bottom:10px;">
          <div style="font-size:11px;font-weight:700;color:#8a7ab5;text-transform:uppercase;margin-bottom:4px;">{item.get("marca","")}</div>
          <div style="font-size:14px;font-weight:600;color:{C_TEXT};margin-bottom:6px;">{titulo_html}</div>
          <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.65;">{item.get("descripcion","")}</div>
          <div style="font-size:11px;color:#ccc;margin-top:6px;font-style:italic;">{item.get("fuente","")}</div>
        </div>"""

    rincon_section = f"""<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">
      {section_header(rincon.get("titulo","El Rincon"),"Navaja","#8a7ab5")}
      <div style="font-size:13px;color:{C_TEXT_LIGHT};font-style:italic;margin-bottom:14px;">{rincon.get("intro","")}</div>
      {rincon_items_html}
    </td></tr>""" if rincon else ""

    linkedin_posts = data.get("posts_linkedin", [])
    linkedin_cards = "".join([linkedin_card(p, i+1) for i, p in enumerate(linkedin_posts)])
    linkedin_section = f"""<tr><td style="background:{C_TAG_BG};padding:24px 28px;">
      {section_header("Tus posts de LinkedIn esta semana","Maletin",C_ACCENT)}
      <div style="font-size:12px;color:{C_TEXT_LIGHT};margin-bottom:16px;margin-top:-10px;">Listos para copiar y publicar</div>
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
    <div style="font-size:11px;font-weight:500;letter-spacing:0.2em;text-transform:uppercase;color:{C_TEXT_LIGHT};margin-bottom:6px;">Beauty Briefing - {fecha}</div>
    <div style="font-size:26px;font-weight:700;color:{C_TEXT};letter-spacing:-0.02em;line-height:1.2;">Tu reporte semanal<br>de lujo y beauty</div>
    {f'<div style="font-size:13px;color:{C_ACCENT};margin-top:10px;font-style:italic;">{frase}</div>' if frase else ""}
  </td></tr>
  <tr><td style="background:{C_WHITE};padding:20px 28px 16px;">
    <div style="font-size:13.5px;color:{C_TEXT_LIGHT};line-height:1.75;">Hola {recipient_name} <br><br>Tu novio te ha preparado este correo para que empieces bien la semana - aqui tienes lo mas importante en tendencias, novedades, YSL y competencia, y tus dos posts de LinkedIn listos para publicar.<br><br><span style="font-size:12px;color:{C_ACCENT};font-style:italic;">(te quiero)</span></div>
  </td></tr>
  {tendencias_section}
  {novedades_section}
  {casas_section}
  {comp_section}
  {digital_section}
  {rincon_section}
  {linkedin_section}
  <tr><td style="background:{C_WHITE};border-radius:0 0 12px 12px;padding:16px 28px 24px;">
    <div style="border-top:1px solid {C_BORDER};padding-top:16px;font-size:11px;color:#ccc;text-align:center;">Beauty Briefing semanal - {fecha} - Generado por el mago, el titan, el animal, maestro, genio, unico e irremplazable Jose Manuel Huertas</div>
  </td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""

def send_email(html_body: str, subject: str):
    log.info(f"Enviando email a {RECIPIENT_EMAIL}...")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Beauty Briefing <{GMAIL_USER}>"
    msg["To"]      = RECIPIENT_EMAIL
    recipients = [RECIPIENT_EMAIL, "jhuertaspresmanes@icloud.com"]
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, recipients, msg.as_string())
    log.info("Email enviado correctamente")

def main():
    log.info("=" * 55)
    log.info("Beauty Briefing Agent - inicio")
    log.info(f"TEST_MODE: {TEST_MODE}")
    log.info("=" * 55)

    memory   = load_memory()
    articles = fetch_news(memory)
    briefing = generate_briefing(articles, memory)

    update_memory(memory, briefing)
    save_memory(memory)

    fecha_bonita = datetime.now().strftime("%d de %B de %Y")
    subject = f"Tu beauty briefing - {fecha_bonita}"
    html    = render_email_html(briefing, RECIPIENT_NAME)

    if TEST_MODE:
        log.info("TEST MODE - email no enviado. Briefing generado:")
        log.info(json.dumps(briefing, ensure_ascii=False, indent=2))
    else:
        send_email(html, subject)

    log.info("Agente completado")

if __name__ == "__main__":
    main()
