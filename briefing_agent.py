#!/usr/bin/env python3
"""
YSL Beauty Intelligence Briefing Agent
---------------------------------------
- Tavily para búsqueda real de artículos con contenido completo
- Groq (llama) para generar el briefing en español
- Memoria entre semanas en memory.json — no repite noticias
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
TEST_MODE          = os.environ.get("TEST_MODE", "false").lower() == "true"

MEMORY_FILE = "memory.json"

# ─── Memoria entre semanas ───────────────────────────────────────────────────

def load_memory() -> dict:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"seen_urls": [], "seen_titles": [], "covered_topics": []}

def save_memory(memory: dict):
    # Mantener solo las últimas 8 semanas para no crecer indefinidamente
    memory["seen_urls"]      = memory["seen_urls"][-200:]
    memory["seen_titles"]    = memory["seen_titles"][-200:]
    memory["covered_topics"] = memory["covered_topics"][-60:]
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)
    log.info(f"Memoria guardada: {len(memory['seen_urls'])} URLs, {len(memory['covered_topics'])} temas")

def filter_seen(articles: list, memory: dict) -> list:
    seen_urls   = set(memory.get("seen_urls", []))
    seen_titles = set(t.lower()[:60] for t in memory.get("seen_titles", []))
    fresh = []
    for a in articles:
        url   = a.get("url", "")
        title = a.get("title", "").lower()[:60]
        if url not in seen_urls and title not in seen_titles:
            fresh.append(a)
    log.info(f"Filtrado memoria: {len(articles)} → {len(fresh)} artículos nuevos")
    return fresh

def update_memory(memory: dict, briefing: dict):
    """Extrae URLs y temas del briefing generado y los guarda en memoria."""
    sections = [
        briefing.get("tendencias", []),
        briefing.get("novedades", []),
        briefing.get("noticias_casas_lujo", []),
        briefing.get("ysl_y_competencia", []),
        briefing.get("digital_social", {}).get("campanas_destacadas", []),
        briefing.get("el_rincon", {}).get("items", []),
    ]
    for section in sections:
        for item in section:
            url   = item.get("url", "")
            title = item.get("titulo", item.get("title", ""))
            if url:
                memory["seen_urls"].append(url)
            if title:
                memory["seen_titles"].append(title)
            # Guardar tema/marca como tópico cubierto
            topic = item.get("marca", item.get("casa", item.get("titulo", "")))
            if topic:
                memory["covered_topics"].append(f"{topic} ({datetime.now().strftime('%Y-%m-%d')})")


# ─── Búsqueda con Tavily ─────────────────────────────────────────────────────
CURRENT_YEAR = datetime.now().year

SEARCH_QUERIES = [
    # YSL y L'Oréal Luxe
    "YSL Beauty Saint Laurent latest news campaign",
    "L'Oreal Luxe luxury beauty news this week",
    # Competencia
    "Dior Beauty Chanel beauty new campaign launch",
    "Tom Ford Givenchy Armani beauty news",
    "Lancôme luxury beauty launch",
    # Tendencias
    "Luxury beauty makeup trend",
    "Luxury perfume fragrance launch news",
    "Luxury fashion house beauty news LVMH Kering",
    # Digital y social
    "Luxury beauty TikTok viral trend campaign",
    "Luxury brand Instagram social media campaign beauty",
    # España
    "YSL belleza lujo España noticias",
    "Belleza lujo tendencia moda España",
    # Hombre
    "Men luxury fragrance grooming skincare launch",
]

def is_within_date_range(date_str: str, days: int = 7) -> bool:
    try:
        article_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        now = datetime.now()

        return (
            now - timedelta(days=days)
            <= article_date
            <= now
        )

    except Exception:
        return False

def tavily_search(query: str, max_results: int = 5) -> list[dict]:
    payload = json.dumps({
        "api_key":     TAVILY_API_KEY,
        "query":       query,
        "topic": "news",
        "search_depth": "advanced",
        "days": 7,
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    articles = []
    for r in data.get("results", []):
        published_date = r.get("published_date")

        if not published_date:
            log.info(f"Sin fecha: {r.get('title')}")
            continue
        
        # Filtrar por rango de fechas: ±7 días
        if not is_within_date_range(published_date, days=7):
            log.info(f"Fuera de rango: {r.get('title')} ({published_date})"
    )
            continue
        
        articles.append({
            "title":       r.get("title", ""),
            "description": (r.get("content") or r.get("raw_content") or r.get("snippet", ""))[:3000],
            "source":      urllib.parse.urlparse(r.get("url", "")).netloc.replace("www.", ""),
            "url":         r.get("url", ""),
            "publishedAt": published_date,
        })
    return articles


def fetch_news(memory: dict) -> list[dict]:
    log.info("Buscando noticias con Tavily...")
    all_articles = []
    seen_urls = set()

    for query in SEARCH_QUERIES:
        try:
            results = tavily_search(query, max_results=4)
            for a in results:
                url = a.get("url", "")
                if url and url not in seen_urls and a.get("title") and "[Removed]" not in a.get("title", ""):
                    seen_urls.add(url)
                    all_articles.append(a)
        except Exception as e:
            log.warning(f"Error en búsqueda '{query}': {e}")

    log.info(f"Total artículos encontrados: {len(all_articles)}")

    # Filtrar los ya vistos en semanas anteriores
    fresh_articles = filter_seen(all_articles, memory)
    return fresh_articles[:40]


# ─── Prompts ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres el asistente semanal de una chica en prácticas como Brand Manager en YSL Beauty España (L'Oréal Luxe).

IMPORTANTE:
Nunca hables de campañas, productos, perfumes, colecciones o colaboraciones de forma genérica.

Siempre menciona:
- nombre exacto
- colección exacta
- perfume exacto
- shade/línea si existe
- celebrity/embajador
- nombre oficial de la campaña
- hashtags o claims relevantes si aparecen

Ejemplo correcto:
"Dior lanzó Rouge Dior Velvet Collection con Anya Taylor-Joy"

Ejemplo incorrecto:
"Dior lanzó una nueva colección de maquillaje"

A partir de las noticias que recibes, genera un reporte semanal en español que cubra:
1. TENDENCIAS — qué está trending en beauty y moda de lujo
2. NOVEDADES — lanzamientos, campañas, colecciones nuevas
3. NOTICIAS IMPORTANTES de casas de lujo (tanto moda como beauty)
4. YSL BEAUTY Y COMPETENCIA — movimientos de YSL, Dior, Chanel, Tom Ford, Givenchy, Armani, Lancôme
5. DIGITAL Y SOCIAL MEDIA — TikTok, Instagram, campañas digitales, radar competencia online, tendencia emergente
6. EL RINCÓN — apartado sobre grooming, fragancias y skincare masculina de lujo (intégralo con naturalidad, con un título creativo diferente cada semana)

Si hay noticias de España, dales prioridad dentro de su sección.
Genera 2 posts de LinkedIn completos listos para publicar — tono de profesional joven con criterio propio.

IMPORTANTE: Las noticias ya han sido pre-filtradas para eliminar temas repetidos de semanas anteriores. Usa solo las noticias del listado.

Cuando menciones una campaña o lanzamiento:
- menciona el nombre exacto
- menciona la marca exacta
- menciona el producto exacto
- menciona la fuente si está disponible

Responde ÚNICAMENTE con JSON válido, sin markdown, sin backticks.

{
  "semana": "DD de MMMM de YYYY",
  "frase_semana": "Una frase que capture el espíritu beauty-lujo de esta semana (máx 15 palabras)",
  "tendencias": [
    {
      "titulo": "Nombre de la tendencia",
      "descripcion": "Qué es, por qué gana fuerza y qué implica (3-4 frases)",
      "es_españa": false
    }
  ],
  "novedades": [
    {
      "marca": "Nombre de la marca",
      "titulo": "Título de la novedad",
      "descripcion": "Incluye SIEMPRE: nombre exacto del producto/campaña/colección, celebrity o embajador si existe, plataforma o formato si aplica, mercado o país si se menciona y detalle concreto del lanzamiento

Nunca hables de forma genérica.",
      "fuente": "Medio",
      "url": "URL",
      "es_españa": false
    }
  ],
  "noticias_casas_lujo": [
    {
      "casa": "Casa de lujo",
      "titulo": "Título",
      "descripcion": "Qué pasó y qué significa (2-3 frases)",
      "fuente": "Medio",
      "url": "URL",
      "es_españa": false
    }
  ],
  "ysl_y_competencia": [
    {
      "marca": "YSL Beauty | Dior | Chanel | Tom Ford | Givenchy | Armani | Lancôme",
      "titulo": "Título",
      "descripcion": "Qué hizo y por qué importa (2-3 frases)",
      "fuente": "Medio",
      "url": "URL",
      "es_españa": false
    }
  ],
  "digital_social": {
    "resumen": "Pulso digital de la semana en beauty-lujo (3-4 frases)",
    "campanas_destacadas": [
      {
        "marca": "Marca",
        "titulo": "Campaña",
        "descripcion": "En qué consiste y por qué funciona (2 frases)",
        "plataforma": "TikTok | Instagram | YouTube | multicanal",
        "es_españa": false
      }
    ],
    "radar_competencia": "Qué están haciendo los competidores de YSL en digital esta semana (2-3 frases)",
    "tendencia_emergente": "La tendencia digital más relevante ahora mismo (2-3 frases)"
  },
  "el_rincon": {
    "titulo": "Título creativo diferente cada semana (ej: Grooming notes, El otro lado del tocador, Para ellos también)",
    "intro": "Frase de introducción natural (1 frase)",
    "items": [
      {
        "marca": "Marca",
        "titulo": "Novedad de grooming/fragancia/skincare masculina de lujo",
        "descripcion": "Breve y relevante (2 frases)",
        "fuente": "Medio",
        "url": "URL"
      }
    ]
  },
  "posts_linkedin": [
    {
      "basado_en": "Noticia o tendencia base",
      "post": "Post completo 150-250 palabras, gancho en primera frase, punto de vista propio, pregunta al final, 3-5 hashtags"
    },
    {
      "basado_en": "Noticia o tendencia base",
      "post": "Segundo post, tono diferente al primero"
    }
  ]
}

Cada sección: entre 2 y 4 items. España primero si hay."""


def generate_briefing(articles: list, memory: dict) -> dict:
    log.info("Generando briefing con Groq...")
    client = Groq(api_key=GROQ_API_KEY)

    covered = memory.get("covered_topics", [])[-20:]
    covered_text = "\n".join(f"- {t}" for t in covered) if covered else "Ninguno aún (primera semana)"

    articles_text = "\n\n".join([
        f"• {a['title']}\n  {a['description']}\n  Fuente: {a['source']} | {a['url']} | {a.get('publishedAt','')[:10]}"
        for a in articles
    ])

    today = datetime.now().strftime("%d de %B de %Y")
    user_prompt = f"""Fecha: {today}

TEMAS YA CUBIERTOS EN SEMANAS ANTERIORES (no repetir):
{covered_text}

NOTICIAS DE ESTA SEMANA:
{articles_text if articles_text else "No se encontraron noticias nuevas — genera el briefing con tendencias generales del sector."}

Genera el reporte semanal completo. Solo JSON."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.4,
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

def spain_badge() -> str:
    return '<span style="background:#f0e8f5;color:#8a6a9b;font-size:10px;font-weight:600;padding:2px 8px;border-radius:20px;margin-left:8px;letter-spacing:0.1em;">🇪🇸 España</span>'

def section_header(title: str, emoji: str, color: str = None) -> str:
    c = color or C_ACCENT
    return f'<div style="font-size:11px;font-weight:600;letter-spacing:0.2em;text-transform:uppercase;color:{c};border-bottom:2px solid {c};padding-bottom:8px;margin-bottom:16px;">{emoji} {title}</div>'

def news_card(item: dict, show_marca: bool = False) -> str:
    es    = item.get("es_españa", False)
    url   = item.get("url", "")
    titulo = item.get("titulo", item.get("title", ""))
    titulo_html = f'<a href="{url}" style="color:{C_TEXT};text-decoration:none;">{titulo}</a>' if url else titulo
    marca = item.get("marca", item.get("casa", ""))

    return f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:16px 20px;margin-bottom:10px;">
      {f'<div style="font-size:11px;font-weight:700;color:{C_ACCENT};letter-spacing:0.1em;text-transform:uppercase;margin-bottom:6px;">{marca}{spain_badge() if es else ""}</div>' if show_marca and marca else ''}
      <div style="font-size:15px;font-weight:600;color:{C_TEXT};line-height:1.35;margin-bottom:8px;">{titulo_html}</div>
      <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.7;">{item.get('descripcion','')}</div>
      <div style="font-size:11px;color:#ccc;margin-top:8px;font-style:italic;">{item.get('fuente','')}</div>
    </div>"""

def trend_card(item: dict) -> str:
    es = item.get("es_españa", False)
    return f"""<div style="background:{C_TAG_BG};border-radius:10px;padding:16px 20px;margin-bottom:10px;border-left:3px solid {C_ACCENT};">
      <div style="font-size:15px;font-weight:600;color:{C_TEXT};margin-bottom:8px;">{item.get('titulo','')}{spain_badge() if es else ''}</div>
      <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.7;">{item.get('descripcion','')}</div>
    </div>"""

def linkedin_card(post_data: dict, num: int) -> str:
    post = post_data.get("post", "").replace("\n", "<br>")
    basado = post_data.get("basado_en", "")
    return f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:20px;margin-bottom:12px;">
      <div style="margin-bottom:12px;">
        <span style="background:{C_ACCENT};color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;margin-right:8px;">Post {num}</span>
        <span style="font-size:11px;color:{C_TEXT_LIGHT};font-style:italic;">Basado en: {basado}</span>
      </div>
      <div style="background:{C_TAG_BG};border-radius:8px;padding:16px;font-size:13px;color:{C_TEXT};line-height:1.75;">{post}</div>
      <div style="margin-top:12px;text-align:right;">
        <a href="https://www.linkedin.com/feed/" style="background:{C_ACCENT};color:#fff;font-size:11px;font-weight:600;padding:7px 16px;border-radius:20px;text-decoration:none;">Publicar en LinkedIn →</a>
      </div>
    </div>"""


def render_email_html(data: dict, recipient_name: str) -> str:
    fecha = data.get("semana", datetime.now().strftime("%d de %B de %Y"))
    frase = data.get("frase_semana", "")

    tendencias_html = "".join([trend_card(t) for t in data.get("tendencias", [])])
    tendencias_section = f"""
    <tr><td style="background:{C_WHITE};padding:24px 28px 8px;">
      {section_header("Tendencias", "📈")}
      {tendencias_html}
    </td></tr>""" if tendencias_html else ""

    novedades_html = "".join([news_card(n, show_marca=True) for n in data.get("novedades", [])])
    novedades_section = f"""
    <tr><td style="background:{C_WHITE};padding:16px 28px 8px;">
      {section_header("Novedades", "✨")}
      {novedades_html}
    </td></tr>""" if novedades_html else ""

    casas_html = "".join([news_card(n, show_marca=True) for n in data.get("noticias_casas_lujo", [])])
    casas_section = f"""
    <tr><td style="background:{C_WHITE};padding:16px 28px 8px;">
      {section_header("Casas de lujo", "🏛️")}
      {casas_html}
    </td></tr>""" if casas_html else ""

    comp_html = "".join([news_card(n, show_marca=True) for n in data.get("ysl_y_competencia", [])])
    comp_section = f"""
    <tr><td style="background:{C_WHITE};padding:16px 28px 8px;">
      {section_header("YSL Beauty & Competencia", "👁️", "#9b6e8a")}
      {comp_html}
    </td></tr>""" if comp_html else ""

    # Digital y social
    digital = data.get("digital_social", {})
    campanas_html = ""
    for c in digital.get("campanas_destacadas", []):
        es = c.get("es_españa", False)
        campanas_html += f"""<div style="background:{C_WHITE};border:1px solid {C_BORDER};border-radius:10px;padding:14px 18px;margin-bottom:10px;">
          <div style="font-size:11px;font-weight:700;color:#7a8fa6;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:4px;">{c.get('marca','')} · {c.get('plataforma','')}{spain_badge() if es else ''}</div>
          <div style="font-size:14px;font-weight:600;color:{C_TEXT};margin-bottom:6px;">{c.get('titulo','')}</div>
          <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.65;">{c.get('descripcion','')}</div>
        </div>"""

    digital_section = f"""
    <tr><td style="background:{C_WHITE};padding:16px 28px 8px;">
      {section_header("Digital & Social Media", "📱", "#7a8fa6")}
      <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.7;margin-bottom:14px;">{digital.get('resumen','')}</div>
      {campanas_html}
      {f'<div style="background:#f0f4f8;border-left:3px solid #7a8fa6;border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:10px;"><div style="font-size:10px;font-weight:700;color:#7a8fa6;letter-spacing:0.1em;">📊 RADAR COMPETENCIA</div><div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.6;margin-top:6px;">{digital.get("radar_competencia","")}</div></div>' if digital.get("radar_competencia") else ''}
      {f'<div style="background:{C_TAG_BG};border-left:3px solid {C_ACCENT};border-radius:0 8px 8px 0;padding:12px 16px;"><div style="font-size:10px;font-weight:700;color:{C_ACCENT};letter-spacing:0.1em;">⚡ TENDENCIA EMERGENTE</div><div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.6;margin-top:6px;">{digital.get("tendencia_emergente","")}</div></div>' if digital.get("tendencia_emergente") else ''}
    </td></tr>""" if digital else ""

    # El Rincón
    rincon = data.get("el_rincon", {})
    rincon_items_html = ""
    for item in rincon.get("items", []):
        url = item.get("url", "")
        titulo = item.get("titulo", "")
        titulo_html = f'<a href="{url}" style="color:{C_TEXT};text-decoration:none;">{titulo}</a>' if url else titulo
        rincon_items_html += f"""<div style="background:#f8f6ff;border:1px solid #e0daf0;border-radius:10px;padding:14px 18px;margin-bottom:10px;">
          <div style="font-size:11px;font-weight:700;color:#8a7ab5;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:4px;">{item.get('marca','')}</div>
          <div style="font-size:14px;font-weight:600;color:{C_TEXT};margin-bottom:6px;">{titulo_html}</div>
          <div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.65;">{item.get('descripcion','')}</div>
          <div style="font-size:11px;color:#ccc;margin-top:6px;font-style:italic;">{item.get('fuente','')}</div>
        </div>"""

    rincon_section = f"""
    <tr><td style="background:{C_WHITE};padding:16px 28px 8px;">
      {section_header(rincon.get('titulo', 'El Rincón'), "🪒", "#8a7ab5")}
      <div style="font-size:13px;color:{C_TEXT_LIGHT};font-style:italic;margin-bottom:14px;">{rincon.get('intro','')}</div>
      {rincon_items_html}
    </td></tr>""" if rincon else ""

    # LinkedIn
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

    <tr><td style="background:{C_WHITE};padding:20px 28px 16px;">
      <div style="font-size:13.5px;color:{C_TEXT_LIGHT};line-height:1.75;">Hola {recipient_name} 💛 Tu novio te ha preparado este correo para que empieces bien la semana — aquí tienes lo más importante del mundo beauty y lujo en los últimos días. Enjoy! 🌟</div>
    </td></tr>

    {tendencias_section}
    {novedades_section}
    {casas_section}
    {comp_section}
    {digital_section}
    {rincon_section}
    {linkedin_section}

    <tr><td style="background:{C_WHITE};border-radius:0 0 12px 12px;padding:16px 28px 24px;">
      <div style="border-top:1px solid {C_BORDER};padding-top:16px;font-size:11px;color:#ccc;text-align:center;">Beauty Briefing semanal · {fecha} · Generado por el mago de Jose Manuel Huertas ✨</div>
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
    recipients = [
    RECIPIENT_EMAIL,
    "jhuertaspresmanes@icloud.com"
]
    msg.attach(MIMEText(html_body, "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, recipients, msg.as_string())
    log.info("✅ Email enviado correctamente")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 55)
    log.info("Beauty Briefing Agent — inicio")
    log.info(f"TEST_MODE: {TEST_MODE}")
    log.info("=" * 55)

    memory   = load_memory()
    articles = fetch_news(memory)
    briefing = generate_briefing(articles, memory)

    # Actualizar memoria con lo cubierto esta semana
    update_memory(memory, briefing)
    save_memory(memory)

    fecha_bonita = datetime.now().strftime("%d de %B de %Y")
    subject = f"✨ Tu beauty briefing · {fecha_bonita}"
    html    = render_email_html(briefing, RECIPIENT_NAME)

    if TEST_MODE:
        log.info("TEST MODE — email no enviado. Briefing generado:")
        log.info(json.dumps(briefing, ensure_ascii=False, indent=2))
    else:
        send_email(html, subject)

    log.info("Agente completado 🎉")

if __name__ == "__main__":
    main()
