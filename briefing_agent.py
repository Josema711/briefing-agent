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
SEARCH_QUERIES = [
    # YSL / L'Oréal Luxe
    "YSL Beauty new campaign launch this week",
    "YSL Beauty upcoming launch 2026",
    "L'Oreal Luxe beauty campaign announced",
    # Competencia directa
    "Dior Beauty new launch this week",
    "Chanel Beauty campaign unveiled",
    "Tom Ford Beauty new fragrance launch",
    "Givenchy Beauty upcoming collection",
    "Armani Beauty campaign starring",
    "Lancome beauty activation launch",
    # Digital
    "Luxury beauty TikTok campaign launched",
    "Luxury beauty Instagram activation",
    "Luxury beauty creator collaboration",
    # Retail / activaciones
    "Luxury beauty pop-up opening",
    "Beauty immersive activation luxury",
    # España
    "Beauty luxury Spain campaign launch",
    "YSL Beauty Spain event",
]

FRESH_KEYWORDS = [
    # lanzamientos reales
    "launches",
    "launched",
    "unveils",
    "unveiled",
    "introduces",
    "introduced",
    "debut",
    "debuts",
    # campañas nuevas
    "new campaign",
    "campaign starring",
    "campaign featuring",
    # colecciones
    "new collection",
    "capsule collection",
    "limited edition",
    "holiday collection",
    "summer collection",
    "fall collection",
    "spring collection",
    # colaboraciones
    "collaboration",
    "partnership",
    "co-created",
    "exclusive drop",
    # retail / eventos
    "pop-up",
    "flagship opening",
    "immersive experience",
    "activation",
    # futuro próximo
    "coming next week",
    "coming this month",
    "set to launch",
    "will launch",
    "scheduled to release",
    # 2026
    "2026 launch",
    "2026 collection",
]


GOOD_SOURCES = [
    "voguebusiness.com",
    "businessoffashion.com",
    "wwd.com",
    "glossy.co",
    "hypebae.com",
    "beautypackaging.com",
    "premiumbeautynews.com",
]

def is_actual_news(article: dict) -> bool:
    text = (
    article.get("title", "") + " " +
    article.get("description", "")
    ).lower()
    # Debe contener señales de novedad
    has_fresh_signal = any(
        keyword in text
        for keyword in FRESH_KEYWORDS
    )
    return has_fresh_signal


def is_within_date_range(date_str: str, days: int = 7) -> bool:
    try:
        article_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        now = datetime.now()

        return (
            now - timedelta(days=7)
            <= article_date
            <= now + imedelta(days=7)
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
            results = tavily_search(query, max_results=5)
            for a in results:
                url = a.get("url", "")
                if not url:
                    continue
                if url in seen_urls:
                    continue
                if not a.get("title"):
                    continue
                if "[Removed]" in a.get("title", ""):
                    continue
                # SOLO noticias realmente nuevas
                if not is_actual_news(a):
                    log.info(f"Descartada por no ser novedad real: {a.get('title')}")
                    continue
    
                seen_urls.add(url)
                all_articles.append(a)
    
        except Exception as e:
            log.warning(f"Error en búsqueda '{query}': {e}")
            
    log.info(f"Noticias válidas encontradas: {len(all_articles)}")
    fresh_articles = filter_seen(all_articles, memory)
    
    # Ordenar por fecha más reciente
    fresh_articles.sort(
        key=lambda x: x.get("publishedAt", ""),
        reverse=True
    )
return fresh_articles[:40]

# ─── Prompts ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
Eres el asistente semanal de intelligence y tendencias para una Brand Manager en prácticas de YSL Beauty España (L'Oréal Luxe).

Tu trabajo NO es resumir internet.
Tu trabajo es seleccionar ÚNICAMENTE:
- lanzamientos NUEVOS
- campañas NUEVAS
- colaboraciones NUEVAS
- activaciones NUEVAS
- eventos NUEVOS
- movimientos estratégicos NUEVOS
- tendencias emergentes REALES

# REGLAS CRÍTICAS

SOLO puedes usar las noticias proporcionadas en el input.

NO inventes información.

NO rellenes huecos.

NO hables de productos históricos, clásicos o evergreen a menos que exista:
- una nueva campaña
- una reformulación
- un nuevo ambassador
- una edición limitada
- una colaboración nueva
- una nueva activación
- un relanzamiento oficial

Ejemplo INCORRECTO:
"Dior Sauvage sigue siendo una fragancia popular"

Ejemplo CORRECTO:
"Dior presentó Sauvage Elixir Absolu con nueva campaña protagonizada por X"

# MUY IMPORTANTE

Nunca hables de forma genérica.

Siempre debes mencionar:
- nombre exacto del producto
- nombre exacto de la colección
- nombre exacto de la campaña
- celebrity / ambassador
- plataforma digital
- país o mercado
- colaboración concreta
- formato concreto del lanzamiento

Ejemplo INCORRECTO:
"Chanel lanzó una nueva colección"

Ejemplo CORRECTO:
"Chanel presentó Les Beiges Golden Hour Collection con campaign film protagonizado por Jennie Kim"

# FILTRADO EDITORIAL

Si una noticia:
- no contiene novedad clara
- es una review
- es un ranking
- es evergreen
- habla de productos antiguos sin novedad
- es contenido SEO
- es demasiado genérica

→ NO la incluyas.

# PRIORIDADES

Prioriza:
1. YSL Beauty
2. L'Oréal Luxe
3. Competidores directos
4. Beauty luxury
5. Moda luxury conectada con beauty
6. Social media y digital
7. España
8. Gen Z / TikTok / creators

# TONO

El tono debe ser:
- elegante
- ejecutivo
- moderno
- inteligente
- insider luxury
- nada corporativo aburrido

Debe sentirse como:
- Vogue Business
- Business of Fashion
- Glossy
- internal trend intelligence memo

# OBJETIVO

La lectora debe poder:
- entender qué está pasando REALMENTE esta semana
- detectar tendencias
- conocer movimientos de competencia
- tener conversación profesional en reuniones
- sacar ideas para marketing

# POSTS DE LINKEDIN

Los posts de LinkedIn deben:
- sonar humanos
- tener punto de vista propio
- evitar clichés de LinkedIn
- parecer escritos por una joven profesional del sector luxury beauty
- incluir reflexión estratégica
- NO sonar generados por IA

# RESPUESTA

Responde ÚNICAMENTE con JSON válido.

Sin markdown.
Sin backticks.
Sin explicaciones.

Formato EXACTO:

{
  "semana": "DD de MMMM de YYYY",
  "frase_semana": "Máximo 15 palabras",
  "tendencias": [
    {
      "titulo": "",
      "descripcion": "",
      "es_españa": false
    }
  ],
  "novedades": [
    {
      "marca": "",
      "titulo": "",
      "descripcion": "",
      "fuente": "",
      "url": "",
      "es_españa": false
    }
  ],
  "noticias_casas_lujo": [
    {
      "casa": "",
      "titulo": "",
      "descripcion": "",
      "fuente": "",
      "url": "",
      "es_españa": false
    }
  ],
  "ysl_y_competencia": [
    {
      "marca": "",
      "titulo": "",
      "descripcion": "",
      "fuente": "",
      "url": "",
      "es_españa": false
    }
  ],
  "digital_social": {
    "resumen": "",
    "campanas_destacadas": [
      {
        "marca": "",
        "titulo": "",
        "descripcion": "",
        "plataforma": "",
        "es_españa": false
      }
    ],
    "radar_competencia": "",
    "tendencia_emergente": ""
  },
  "el_rincon": {
    "titulo": "",
    "intro": "",
    "items": [
      {
        "marca": "",
        "titulo": "",
        "descripcion": "",
        "fuente": "",
        "url": ""
      }
    ]
  },
  "posts_linkedin": [
    {
      "basado_en": "",
      "post": ""
    },
    {
      "basado_en": "",
      "post": ""
    }
  ]
}

# REGLAS FINALES

- Máximo 4 items por sección
- Solo noticias RELEVANTES
- Calidad > cantidad
- Si no hay noticias suficientemente buenas, devuelve menos items
- No rellenes por rellenar
- España primero si aplica
"""


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
        temperature=0.2,
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
