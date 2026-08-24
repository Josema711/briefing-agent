#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import smtplib
import html
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
from zoneinfo import ZoneInfo
from groq import Groq

# ─────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("briefing_agent.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# VARIABLES DE ENTORNO
# ─────────────────────────────────────────────────────

def get_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise EnvironmentError(f"Variable '{key}' no definida.")
    return val

TEST_MODE          = os.environ.get("TEST_MODE", "false").lower() == "true"
GROQ_API_KEY       = get_env("GROQ_API_KEY")
TAVILY_API_KEY     = get_env("TAVILY_API_KEY")
GMAIL_USER         = os.environ.get("GMAIL_USER", "").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
RECIPIENT_EMAIL    = os.environ.get("RECIPIENT_EMAIL", "").strip()
RECIPIENT_NAME     = os.environ.get("RECIPIENT_NAME", "").strip() or "there"
CC_EMAIL           = os.environ.get("CC_EMAIL", "").strip()
GROQ_MODELS        = [
    model.strip()
    for model in (
        os.environ.get("GROQ_MODELS", "").strip()
        or "openai/gpt-oss-120b,qwen/qwen3.6-27b,openai/gpt-oss-20b"
    ).split(",")
    if model.strip()
]
MEMORY_FILE        = "memory.json"
SPAIN_TZ           = ZoneInfo("Europe/Madrid")

if not TEST_MODE:
    GMAIL_USER         = get_env("GMAIL_USER")
    GMAIL_APP_PASSWORD = get_env("GMAIL_APP_PASSWORD")
    RECIPIENT_EMAIL    = get_env("RECIPIENT_EMAIL")


# ─────────────────────────────────────────────────────
# MEMORIA ENTRE SEMANAS
# Guarda URLs y títulos ya vistos para no repetir noticias.
# Se actualiza automáticamente al final de cada ejecución.
# ─────────────────────────────────────────────────────

def load_memory() -> dict:
    """Carga el archivo de memoria. Si no existe, devuelve estructura vacía."""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"seen_urls": [], "seen_titles": [], "covered_topics": []}


def save_memory(memory: dict):
    """Guarda la memoria recortando a los últimos 400 registros (~8 semanas)."""
    memory["seen_urls"] = list(dict.fromkeys(memory.get("seen_urls", [])))[-400:]
    memory["seen_titles"] = list(dict.fromkeys(memory.get("seen_titles", [])))[-400:]
    memory["covered_topics"] = list(
        dict.fromkeys(memory.get("covered_topics", []))
    )[-120:]
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)
    log.info(f"Memoria guardada: {len(memory['seen_urls'])} URLs")


def normalize_title(title: str) -> str:
    """Normaliza un título para comparación: minúsculas, sin símbolos, máx 90 chars."""
    title = title.lower()
    title = re.sub(r"[^a-zA-Z0-9 ]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()[:90]


def filter_seen(articles: list, memory: dict) -> list:
    """Elimina artículos ya vistos en semanas anteriores."""
    seen_urls   = set(memory.get("seen_urls", []))
    seen_titles = set(normalize_title(t) for t in memory.get("seen_titles", []))
    fresh = []
    for a in articles:
        url   = a.get("url", "")
        title = normalize_title(a.get("title", ""))
        if not url or url in seen_urls or title in seen_titles:
            continue
        fresh.append(a)
    log.info(f"Filtrado memoria: {len(articles)} -> {len(fresh)}")
    return fresh


def update_memory(memory: dict, briefing: dict):
    """Añade a la memoria las URLs y temas cubiertos en el briefing de esta semana."""
    sections = [
        briefing.get("tendencias", []),
        briefing.get("novedades", []),
        briefing.get("noticias_casas_lujo", []),
        briefing.get("ysl_y_competencia", []),
        briefing.get("radar_moda_cultura", []),
        briefing.get("agenda_semana", []),
        briefing.get("no_perder_de_vista", []),
        briefing.get("el_rincon", {}).get("items", []),
    ]
    for section in sections:
        for item in section:
            url   = item.get("url", "")
            title = item.get("titulo", item.get("tema", item.get("idea", "")))
            if url:
                memory["seen_urls"].append(url)
            if title:
                memory["seen_titles"].append(title)
            topic = item.get("marca") or item.get("casa") or item.get("tema") or title
            if topic:
                memory["covered_topics"].append(
                    f"{topic} ({datetime.now(SPAIN_TZ).strftime('%Y-%m-%d')})"
                )

    for item in briefing.get("ideas_accionables", []):
        idea = item.get("idea", "")
        based_on = item.get("basado_en", "")
        if idea:
            memory["seen_titles"].append(idea)
        if based_on:
            memory["covered_topics"].append(
                f"{based_on} ({datetime.now(SPAIN_TZ).strftime('%Y-%m-%d')})"
            )


# ─────────────────────────────────────────────────────
# CONFIGURACIÓN DE BÚSQUEDA
# ─────────────────────────────────────────────────────

# Queries para Tavily: beauty, L'Oreal Luxe, fragancias, moda, retail, inversiones,
# colaboraciones, activaciones y movimientos de negocio utiles para proponer ideas.
SEARCH_QUERIES = [
    "L'Oreal Luxe YSL Beauty campaign launch fragrance makeup ambassador",
    "Dior Chanel Prada Valentino Armani Givenchy luxury beauty launch campaign",
    "luxury fragrance makeup skincare launch limited edition collaboration",
    "beauty celebrity creator ambassador music film fashion collaboration",
    "YSL Beauty L'Oreal Luxe Spain Madrid Barcelona activation pop-up event",
    "luxury beauty retail Sephora pop-up immersive experience Europe",
    "beauty TikTok Instagram social commerce creator campaign luxury brand",
    "LVMH Kering Puig Estee Lauder beauty investment acquisition partnership",
    "fashion week runway beauty makeup fragrance trend luxury",
    "Gen Z female luxury consumer beauty fashion culture trend",
    "luxury fashion pop culture celebrity red carpet campaign",
    "premium skincare fragrance bodycare grooming luxury trend",
    "luxury beauty launch announced upcoming next week Europe",
    "luxury retail beauty fashion Madrid Paris London event next week",
]

WEEKLY_ANGLES = [
    "cultura pop, celebrities y creadores que pueden inspirar campanas beauty",
    "retail, pop-ups y experiencias fisicas que una marca podria replicar en Espana",
    "fragancias, storytelling sensorial y rituales de lujo",
    "moda, pasarela, street style y codigos esteticos transferibles a beauty",
    "TikTok, Instagram, social commerce y formatos de contenido que estan funcionando",
    "inversiones, adquisiciones y movimientos de negocio que cambian el tablero beauty",
    "Gen Z, comunidades femeninas y nuevas formas de deseo aspiracional",
    "España, Madrid, Barcelona, Francia y activaciones cercanas al mercado de L'Oreal Luxe",
]

# Fuentes prioritarias — aparecen primero en el briefing
TOP_SOURCES = [
    "voguebusiness.com",
    "vogue.com",
    "harpersbazaar.com",
    "elle.com",
    "wwd.com",
    "businessoffashion.com",
    "glossy.co",
    "fashionista.com",
]

# Fuentes aceptables — se incluyen pero con menor prioridad
GOOD_SOURCES = TOP_SOURCES + [
    "beautypackaging.com",
    "premiumbeautynews.com",
    "cosmeticsbusiness.com",
    "hypebae.com",
    "allure.com",
    "forbes.com",
    "retaildive.com",
    "retailexchange.co.uk",
    "theindustry.beauty",
    "globalcosmeticsnews.com",
    "cosmeticsdesign-europe.com",
    "marketingdive.com",
    "adweek.com",
    "campaignlive.co.uk",
    "perfumerflavorist.com",
]

# Palabras clave que indican contenido SEO o evergreen — se descartan
BAD_KEYWORDS = [
    "best beauty looks",
    "shopping list",
    "memorial day sale",
    "sale",
    "review",
    "ranking",
    "best products",
    "editor favorites",
    "gift guide",
    "trend tracker",
    "how to",
    "tutorial",
    "evergreen",
]


def is_actual_news(article: dict) -> bool:
    """Devuelve False si el artículo parece contenido SEO o evergreen."""
    text = (article.get("title", "") + " " + article.get("description", "")).lower()
    return not any(k in text for k in BAD_KEYWORDS)


def is_within_date_range(date_str: str, days: int = 10) -> bool:
    """Comprueba que el artículo es de los últimos N días."""
    try:
        article_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return article_date >= datetime.now() - timedelta(days=days)
    except Exception:
        return False


def get_editorial_window() -> str:
    """Describe el periodo editorial que debe cubrir el briefing."""
    today = datetime.now(SPAIN_TZ).date()
    last_monday = today - timedelta(days=7)
    next_sunday = today + timedelta(days=6)
    return (
        f"Periodo editorial: de {last_monday.strftime('%Y-%m-%d')} "
        f"a {next_sunday.strftime('%Y-%m-%d')}. "
        "Prioriza lo ocurrido en los ultimos 7 dias y lo anunciado para esta semana."
    )


def get_weekly_angle() -> str:
    """Rota el enfoque editorial para que el briefing no suene igual cada lunes."""
    week_number = int(datetime.now(SPAIN_TZ).strftime("%U"))
    return WEEKLY_ANGLES[week_number % len(WEEKLY_ANGLES)]


# ─────────────────────────────────────────────────────
# BÚSQUEDA CON TAVILY
# ─────────────────────────────────────────────────────

def tavily_search(query: str, max_results: int = 8) -> list:
    """Lanza una búsqueda en Tavily y devuelve los artículos filtrados."""
    payload = json.dumps({
        "api_key":            TAVILY_API_KEY,
        "query":              query,
        "topic":              "news",
        "search_depth":       "advanced",
        "days":               14,
        "max_results":        max_results,
        "include_answer":     False,
        "include_raw_content": False,
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

        # Descartar si no tiene fecha o es demasiado antiguo
        if not published_date or not is_within_date_range(published_date, days=14):
            continue

        source = urllib.parse.urlparse(r.get("url", "")).netloc.replace("www.", "")

        # IMPORTANTE: article se define ANTES de usarlo
        article = {
            "title":       r.get("title", ""),
            "description": (r.get("content") or r.get("raw_content") or "")[:3500],
            "source":      source,
            "url":         r.get("url", ""),
            "publishedAt": published_date,
        }

        # Descartar si no tiene título o parece SEO
        if not article["title"] or not is_actual_news(article):
            continue

        articles.append(article)

    return articles


def fetch_news(memory: dict) -> list:
    """
    Ejecuta todas las queries, deduplica por URL,
    ordena priorizando TOP_SOURCES y filtra los ya vistos en memoria.
    """
    log.info("Buscando noticias con Tavily...")
    all_articles = []
    seen_urls    = set()

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

    def sort_key(article: dict):
        source = article.get("source", "")
        source_rank = 0 if source in TOP_SOURCES else 1 if source in GOOD_SOURCES else 2
        try:
            date_rank = -datetime.strptime(article.get("publishedAt", "")[:10], "%Y-%m-%d").timestamp()
        except Exception:
            date_rank = 0
        return (source_rank, date_rank)

    # Ordenar: fuentes premium primero, luego fuentes buenas, y dentro de cada grupo lo mas reciente.
    all_articles.sort(key=sort_key)

    log.info(f"Noticias validas encontradas: {len(all_articles)}")

    # Filtrar los ya vistos en semanas anteriores y limitar a 60
    fresh = filter_seen(all_articles, memory)
    return fresh[:60]


# ─────────────────────────────────────────────────────
# SYSTEM PROMPT
# Define el rol, las reglas editoriales y el formato JSON de salida.
# ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """
Eres una analista senior de intelligence para una Brand Manager en practicas de YSL Beauty Espana, dentro de L'Oreal Luxe.

Objetivo: que el lunes a las 8:30 pueda entrar a trabajar sabiendo que ha pasado en beauty, lujo, fragancias, moda, retail, social, inversiones y colaboraciones, y pueda proponer ideas concretas para YSL Beauty/L'Oreal.

No es un boletin solo de L'Oreal. L'Oreal/YSL tienen prioridad cuando aparezcan, pero el briefing debe cubrir todo lo que pueda darle ideas frescas: moda de lujo, cultura femenina, celebrities, pasarela, retail, pop-ups, musica, cine, redes, marcas competidoras, inversiones y cambios de consumo.

Selecciona UNICAMENTE piezas con informacion accionable:
- lanzamientos nuevos o anunciados
- campanas nuevas, embajadores, celebrities, creadores, shootings o plataformas concretas
- colaboraciones entre beauty, moda, musica, cine, retail, gaming, deporte o cultura
- activaciones, pop-ups, eventos, experiencias, retail media, Sephora/department stores
- inversiones, adquisiciones, resultados, movimientos de grupos como L'Oreal, LVMH, Kering, Puig, Estee Lauder
- senales de tendencia que permitan proponer una idea de campana o contenido

Reglas duras:
- SOLO usa las noticias proporcionadas. NO inventes datos, marcas, fechas, nombres ni cifras.
- Cada item debe incluir nombres propios: marca, producto/coleccion/campana, persona, mercado, plataforma, retailer o grupo empresarial cuando existan.
- Cada descripcion debe tener 2 frases: primera con el hecho concreto; segunda con por que importa o que idea puede inspirar para YSL/L'Oreal.
- Prohibido texto generico: "la marca refuerza su posicion", "la belleza evoluciona", "sigue siendo tendencia", "apuesta por la innovacion".
- Si una noticia no tiene detalle concreto, no la incluyas.
- Prioriza Espana, Francia, Europa y movimientos globales relevantes para lujo.
- Incluye tambien moda/lujo aunque no sea beauty si ofrece una idea transferible a beauty.
- Tono: ejecutivo, preciso, moderno, insider luxury. Cero relleno.
- Incluye 2 ideas accionables para YSL/L'Oreal basadas en noticias concretas, con una accion que pudiera proponer una persona en practicas.
- Incluye una seccion "para_comentar_con_jefa" con 3 bullets muy concretos que pueda decir en una reunion sin sonar generica.
- Variedad semanal: evita repetir las mismas marcas, temas y frases de semanas anteriores si hay alternativas nuevas.
- Escribe corto: descripciones de 25-45 palabras. Posts de LinkedIn de maximo 120 palabras cada uno.

POSTS LINKEDIN:
- Dos posts completos, humanos y con punto de vista propio.
- Cada post debe basarse en una noticia concreta del briefing y evitar cliches.
- Deben sonar a joven profesional de luxury beauty, no a nota de prensa.

Responde UNICAMENTE con JSON valido. Sin markdown. Sin backticks.

{
  "semana": "DD de MMMM de YYYY",
  "frase_semana": "Maximo 15 palabras, con una idea concreta",
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
  "radar_moda_cultura": [{"marca": "", "titulo": "", "descripcion": "", "fuente": "", "url": "", "es_espana": false}],
  "agenda_semana": [{"fecha": "", "titulo": "", "descripcion": "", "fuente": "", "url": "", "es_espana": false}],
  "ideas_accionables": [
    {"idea": "", "basado_en": "", "accion_para_ysl": ""}
  ],
  "para_comentar_con_jefa": [
    {"tema": "", "por_que_importa": "", "frase_util": ""}
  ],
  "no_perder_de_vista": [{"tema": "", "descripcion": "", "fuente": "", "url": ""}],
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

Maximo 3 items por seccion. En agenda, no_perder_de_vista e ideas_accionables, maximo 2. Calidad y precision > cantidad. Espana primero si aplica.
"""


# ─────────────────────────────────────────────────────
# GENERACIÓN DEL BRIEFING CON GROQ
# ─────────────────────────────────────────────────────

def generate_briefing(articles: list, memory: dict) -> dict:
    """
    Envía las noticias a Groq con el system prompt
    y devuelve el briefing como diccionario Python.
    Usa presupuestos progresivamente mas compactos para respetar
    el limite gratuito de Groq.
    """
    if not articles:
        raise RuntimeError("No se encontraron noticias validas; se cancela el envio.")

    log.info("Generando briefing con Groq...")
    client = Groq(api_key=GROQ_API_KEY)

    today = datetime.now(SPAIN_TZ).strftime("%d de %B de %Y")
    editorial_window = get_editorial_window()
    weekly_angle = get_weekly_angle()

    attempts = [
        {"articles": 20, "desc_chars": 260, "covered": 12, "titles": 18, "max_tokens": 3600, "items": 3},
        {"articles": 14, "desc_chars": 200, "covered": 8, "titles": 12, "max_tokens": 3000, "items": 2},
        {"articles": 10, "desc_chars": 150, "covered": 5, "titles": 8, "max_tokens": 2400, "items": 2},
    ]

    last_error = None
    for idx, budget in enumerate(attempts, start=1):
        covered = memory.get("covered_topics", [])[-budget["covered"]:]
        covered_text = "\n".join(f"- {t}" for t in covered)
        recent_titles = memory.get("seen_titles", [])[-budget["titles"]:]
        recent_titles_text = "\n".join(f"- {t}" for t in recent_titles)

        articles_text = "\n\n".join([
            f"TITULO: {a['title']}\n"
            f"FUENTE: {a['source']} | {a['publishedAt'][:10]}\n"
            f"URL: {a['url']}\n"
            f"CONTENIDO: {a['description'][:budget['desc_chars']]}"
            for a in articles[:budget["articles"]]
        ])

        user_prompt = f"""Fecha: {today}
{editorial_window}
Enfoque editorial extra de esta semana: {weekly_angle}

TEMAS YA CUBIERTOS:
{covered_text}

TITULARES/IDEAS RECIENTES QUE NO DEBES REPETIR:
{recent_titles_text}

NOTICIAS:
{articles_text}

Genera un briefing semanal fresco. Maximo {budget['items']} items por seccion. Si dos noticias cuentan basicamente la misma historia, usa solo la mas concreta y convierte la otra en contexto, no en otra tarjeta. Devuelve JSON completo y valido; no cortes cadenas a medias."""

        retry_compact = False
        for model in GROQ_MODELS:
            log.info(
                "Groq intento %s con %s: %s articulos, %s chars/articulo, max_tokens=%s",
                idx,
                model,
                budget["articles"],
                budget["desc_chars"],
                budget["max_tokens"],
            )

            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": user_prompt},
                    ],
                    temperature=0.25,
                    max_tokens=budget["max_tokens"],
                    response_format={"type": "json_object"},
                )

                text = response.choices[0].message.content or ""
                clean = text.replace("```json", "").replace("```", "").strip()
                briefing = json.loads(clean)
                log.info("Briefing generado correctamente con %s", model)
                return briefing
            except json.JSONDecodeError as e:
                last_error = e
                retry_compact = True
                log.warning(
                    "Groq devolvio JSON incompleto en el intento %s: %s. "
                    "Reintentando compacto...",
                    idx,
                    e,
                )
                break
            except Exception as e:
                last_error = e
                error_text = str(e)
                if any(
                    marker in error_text
                    for marker in ("model_not_found", "does not exist", "do not have access", "403")
                ):
                    log.warning("Modelo Groq no disponible (%s); probando fallback.", model)
                    continue
                if any(
                    marker in error_text
                    for marker in ("413", "Request too large", "TPM", "429")
                ):
                    retry_compact = True
                    log.warning(
                        "Groq rechazo el intento %s por tamano o cuota. "
                        "Reintentando compacto...",
                        idx,
                    )
                    break
                log.error("Error generando briefing: %s", error_text)
                raise

        if retry_compact:
            continue

    raise last_error


def build_fallback_briefing(articles: list) -> dict:
    """Genera una edición básica sin IA para no perder el envío semanal."""
    def to_item(article: dict) -> dict:
        description = re.sub(r"\s+", " ", article.get("description", "")).strip()
        if len(description) > 320:
            description = description[:317].rstrip() + "..."
        return {
            "marca": article.get("source", ""),
            "casa": article.get("source", ""),
            "titulo": article.get("title", ""),
            "descripcion": description or "Consulta la fuente para ampliar la noticia.",
            "fuente": article.get("source", ""),
            "url": article.get("url", ""),
            "es_espana": any(
                marker in (
                    article.get("title", "") + " " + article.get("description", "")
                ).lower()
                for marker in ("spain", "españa", "madrid", "barcelona")
            ),
        }

    selected = [to_item(article) for article in articles[:9]]
    return {
        "semana": datetime.now(SPAIN_TZ).strftime("%d de %B de %Y"),
        "frase_semana": "Edición de respaldo: noticias verificadas sin resumen de IA.",
        "tendencias": [],
        "novedades": selected[:3],
        "noticias_casas_lujo": selected[3:6],
        "ysl_y_competencia": selected[6:9],
        "digital_social": {},
        "radar_moda_cultura": [],
        "agenda_semana": [],
        "ideas_accionables": [],
        "para_comentar_con_jefa": [],
        "no_perder_de_vista": [],
        "el_rincon": {},
        "posts_linkedin": [],
    }


# ─────────────────────────────────────────────────────
# DISEÑO DEL EMAIL — PALETA NUDE / TERRACOTA
# ─────────────────────────────────────────────────────

C_BG         = "#faf8f5"   # Fondo general crema
C_WHITE      = "#ffffff"
C_HEADER_BG  = "#f0ebe3"   # Beige cálido para el header
C_ACCENT     = "#c17f5e"   # Terracota principal
C_TEXT       = "#2d2d2d"   # Texto principal casi negro
C_TEXT_LIGHT = "#7a6f6a"   # Texto secundario gris cálido
C_BORDER     = "#e8e0d8"   # Bordes suaves
C_TAG_BG     = "#f5ede8"   # Fondo de etiquetas y tarjetas de tendencia


def sanitize_for_html(value, key: str = ""):
    """Escapa texto generado y permite solo URLs HTTP(S) en el email."""
    if isinstance(value, dict):
        return {k: sanitize_for_html(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_html(item, key) for item in value]
    if isinstance(value, str):
        if key == "url":
            parsed = urllib.parse.urlparse(value)
            return html.escape(value, quote=True) if parsed.scheme in {"http", "https"} else ""
        return html.escape(value, quote=True)
    return value


def spain_badge() -> str:
    """Badge visual para noticias de España."""
    return (
        '<span style="'
        "background:#f0e8f5;"
        "color:#8a6a9b;"
        "font-size:10px;"
        "font-weight:600;"
        "padding:2px 8px;"
        "border-radius:20px;"
        'margin-left:8px;">ES</span>'
    )


def section_header(title: str, emoji: str, color: str = None) -> str:
    """Genera el encabezado de cada sección del email."""
    c = color or C_ACCENT
    return (
        f'<div style="'
        f"font-size:11px;"
        f"font-weight:600;"
        f"letter-spacing:0.2em;"
        f"text-transform:uppercase;"
        f"color:{c};"
        f"border-bottom:2px solid {c};"
        f"padding-bottom:8px;"
        f'margin-bottom:16px;">'
        f"{emoji} {title}"
        f"</div>"
    )


def news_card(item: dict, show_marca: bool = False) -> str:
    """Tarjeta de noticia estándar: marca, título, descripción y fuente."""
    es          = item.get("es_espana", item.get("es_españa", False))
    url         = item.get("url", "")
    titulo      = item.get("titulo", item.get("title", item.get("tema", "")))
    titulo_html = (
        f'<a href="{url}" style="color:{C_TEXT};text-decoration:none;">{titulo}</a>'
        if url else titulo
    )
    marca = item.get("marca", item.get("casa", ""))

    marca_html = ""
    if show_marca and marca:
        marca_html = (
            f'<div style="'
            f"font-size:11px;"
            f"font-weight:700;"
            f"color:{C_ACCENT};"
            f"letter-spacing:0.1em;"
            f"text-transform:uppercase;"
            f'margin-bottom:6px;">'
            f"{marca}{spain_badge() if es else ''}"
            f"</div>"
        )

    return f"""
    <div style="
        background:{C_WHITE};
        border:1px solid {C_BORDER};
        border-radius:10px;
        padding:16px 20px;
        margin-bottom:10px;">
      {marca_html}
      <div style="
          font-size:15px;
          font-weight:600;
          color:{C_TEXT};
          line-height:1.35;
          margin-bottom:8px;">
        {titulo_html}
      </div>
      <div style="
          font-size:13px;
          color:{C_TEXT_LIGHT};
          line-height:1.7;">
        {item.get("descripcion", "")}
      </div>
      <div style="
          font-size:11px;
          color:#ccc;
          margin-top:8px;
          font-style:italic;">
        {item.get("fuente", "")}
      </div>
    </div>"""


def trend_card(item: dict) -> str:
    """Tarjeta para tendencias: fondo nude con borde terracota."""
    es = item.get("es_espana", item.get("es_españa", False))
    return f"""
    <div style="
        background:{C_TAG_BG};
        border-radius:10px;
        padding:16px 20px;
        margin-bottom:10px;
        border-left:3px solid {C_ACCENT};">
      <div style="
          font-size:15px;
          font-weight:600;
          color:{C_TEXT};
          margin-bottom:8px;">
        {item.get("titulo", "")}{spain_badge() if es else ""}
      </div>
      <div style="
          font-size:13px;
          color:{C_TEXT_LIGHT};
          line-height:1.7;">
        {item.get("descripcion", "")}
      </div>
    </div>"""


def agenda_card(item: dict) -> str:
    """Tarjeta para eventos, lanzamientos o hitos previstos esta semana."""
    es = item.get("es_espana", item.get("es_españa", False))
    url = item.get("url", "")
    titulo = item.get("titulo", "")
    titulo_html = (
        f'<a href="{url}" style="color:{C_TEXT};text-decoration:none;">{titulo}</a>'
        if url else titulo
    )
    return f"""
    <div style="
        background:{C_WHITE};
        border:1px solid {C_BORDER};
        border-radius:8px;
        padding:14px 18px;
        margin-bottom:10px;">
      <div style="
          font-size:11px;
          font-weight:700;
          color:{C_ACCENT};
          text-transform:uppercase;
          margin-bottom:5px;">
        {item.get("fecha", "Esta semana")}{spain_badge() if es else ""}
      </div>
      <div style="
          font-size:14px;
          font-weight:600;
          color:{C_TEXT};
          margin-bottom:6px;">
        {titulo_html}
      </div>
      <div style="
          font-size:13px;
          color:{C_TEXT_LIGHT};
          line-height:1.65;">
        {item.get("descripcion", "")}
      </div>
      <div style="
          font-size:11px;
          color:#ccc;
          margin-top:6px;
          font-style:italic;">
        {item.get("fuente", "")}
      </div>
    </div>"""


def boss_talk_card(item: dict) -> str:
    """Tarjeta con un punto listo para comentar en reunion."""
    return f"""
    <div style="
        background:#f7f7f4;
        border:1px solid {C_BORDER};
        border-radius:8px;
        padding:14px 18px;
        margin-bottom:10px;">
      <div style="
          font-size:14px;
          font-weight:700;
          color:{C_TEXT};
          margin-bottom:6px;">
        {item.get("tema", "")}
      </div>
      <div style="
          font-size:13px;
          color:{C_TEXT_LIGHT};
          line-height:1.6;
          margin-bottom:8px;">
        {item.get("por_que_importa", "")}
      </div>
      <div style="
          font-size:13px;
          color:{C_TEXT};
          line-height:1.65;
          font-style:italic;">
        "{item.get("frase_util", "")}"
      </div>
    </div>"""


def linkedin_card(post_data: dict, num: int) -> str:
    """Tarjeta de post de LinkedIn con botón para publicar directamente."""
    post   = post_data.get("post", "").replace("\n", "<br>")
    basado = post_data.get("basado_en", "")
    return f"""
    <div style="
        background:{C_WHITE};
        border:1px solid {C_BORDER};
        border-radius:10px;
        padding:20px;
        margin-bottom:12px;">
      <div style="margin-bottom:12px;">
        <span style="
            background:{C_ACCENT};
            color:#fff;
            font-size:11px;
            font-weight:700;
            padding:3px 10px;
            border-radius:20px;
            margin-right:8px;">
          Post {num}
        </span>
        <span style="
            font-size:11px;
            color:{C_TEXT_LIGHT};
            font-style:italic;">
          Basado en: {basado}
        </span>
      </div>
      <div style="
          background:{C_TAG_BG};
          border-radius:8px;
          padding:16px;
          font-size:13px;
          color:{C_TEXT};
          line-height:1.75;">
        {post}
      </div>
      <div style="margin-top:12px;text-align:right;">
        <a href="https://www.linkedin.com/feed/"
           style="
               background:{C_ACCENT};
               color:#fff;
               font-size:11px;
               font-weight:600;
               padding:7px 16px;
               border-radius:20px;
               text-decoration:none;">
          Publicar en LinkedIn
        </a>
      </div>
    </div>"""


# ─────────────────────────────────────────────────────
# RENDER DEL EMAIL HTML COMPLETO
# ─────────────────────────────────────────────────────

def render_email_html(data: dict, recipient_name: str) -> str:
    """Construye el HTML completo del email a partir del JSON del briefing."""
    data = sanitize_for_html(data)
    recipient_name = html.escape(recipient_name, quote=True)
    fecha = data.get("semana", datetime.now().strftime("%d de %B de %Y"))
    frase = data.get("frase_semana", "")

    # ── Sección Tendencias ──────────────────────────────
    tendencias_html = "".join([trend_card(t) for t in data.get("tendencias", [])])
    tendencias_section = (
        f'<tr><td style="background:{C_WHITE};padding:24px 28px 8px;">'
        f'{section_header("Tendencias", "📈")}'
        f'{tendencias_html}'
        f'</td></tr>'
    ) if tendencias_html else ""

    # ── Sección Novedades ───────────────────────────────
    novedades_html = "".join([news_card(n, True) for n in data.get("novedades", [])])
    novedades_section = (
        f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">'
        f'{section_header("Novedades", "✨")}'
        f'{novedades_html}'
        f'</td></tr>'
    ) if novedades_html else ""

    # ── Sección Casas de lujo ───────────────────────────
    casas_html = "".join([news_card(n, True) for n in data.get("noticias_casas_lujo", [])])
    casas_section = (
        f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">'
        f'{section_header("Casas de lujo", "🏛️")}'
        f'{casas_html}'
        f'</td></tr>'
    ) if casas_html else ""

    # ── Sección YSL & Competencia ───────────────────────
    comp_html = "".join([news_card(n, True) for n in data.get("ysl_y_competencia", [])])
    comp_section = (
        f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">'
        f'{section_header("YSL Beauty & Competencia", "👁️", "#9b6e8a")}'
        f'{comp_html}'
        f'</td></tr>'
    ) if comp_html else ""

    # ── Sección Digital & Social Media ─────────────────
    digital      = data.get("digital_social", {})
    campanas_html = ""

    for c in digital.get("campanas_destacadas", []):
        es = c.get("es_espana", c.get("es_españa", False))
        campanas_html += f"""
        <div style="
            background:{C_WHITE};
            border:1px solid {C_BORDER};
            border-radius:10px;
            padding:14px 18px;
            margin-bottom:10px;">
          <div style="
              font-size:11px;
              font-weight:700;
              color:#7a8fa6;
              text-transform:uppercase;
              margin-bottom:4px;">
            {c.get("marca", "")} - {c.get("plataforma", "")}{spain_badge() if es else ""}
          </div>
          <div style="
              font-size:14px;
              font-weight:600;
              color:{C_TEXT};
              margin-bottom:6px;">
            {c.get("titulo", "")}
          </div>
          <div style="
              font-size:13px;
              color:{C_TEXT_LIGHT};
              line-height:1.65;">
            {c.get("descripcion", "")}
          </div>
        </div>"""

    radar_html = ""
    if digital.get("radar_competencia"):
        radar_html = f"""
        <div style="
            background:#f0f4f8;
            border-left:3px solid #7a8fa6;
            border-radius:0 8px 8px 0;
            padding:12px 16px;
            margin-bottom:10px;">
          <div style="
              font-size:10px;
              font-weight:700;
              color:#7a8fa6;">
            RADAR COMPETENCIA
          </div>
          <div style="
              font-size:13px;
              color:{C_TEXT_LIGHT};
              line-height:1.6;
              margin-top:6px;">
            {digital.get("radar_competencia", "")}
          </div>
        </div>"""

    tendencia_emergente_html = ""
    if digital.get("tendencia_emergente"):
        tendencia_emergente_html = f"""
        <div style="
            background:{C_TAG_BG};
            border-left:3px solid {C_ACCENT};
            border-radius:0 8px 8px 0;
            padding:12px 16px;">
          <div style="
              font-size:10px;
              font-weight:700;
              color:{C_ACCENT};">
            TENDENCIA EMERGENTE
          </div>
          <div style="
              font-size:13px;
              color:{C_TEXT_LIGHT};
              line-height:1.6;
              margin-top:6px;">
            {digital.get("tendencia_emergente", "")}
          </div>
        </div>"""

    digital_section = (
        f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">'
        f'{section_header("Digital & Social Media", "📱", "#7a8fa6")}'
        f'<div style="font-size:13px;color:{C_TEXT_LIGHT};line-height:1.7;margin-bottom:14px;">'
        f'{digital.get("resumen", "")}'
        f'</div>'
        f'{campanas_html}'
        f'{radar_html}'
        f'{tendencia_emergente_html}'
        f'</td></tr>'
    ) if digital else ""

    # ── Sección Moda & cultura ─────────────────────────
    moda_html = "".join([news_card(n, True) for n in data.get("radar_moda_cultura", [])])
    moda_section = (
        f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">'
        f'{section_header("Radar moda, lujo y cultura", ">>", "#6f7f6f")}'
        f'{moda_html}'
        f'</td></tr>'
    ) if moda_html else ""

    # ── Sección Agenda de la semana ────────────────────
    agenda_html = "".join([agenda_card(a) for a in data.get("agenda_semana", [])])
    agenda_section = (
        f'<tr><td style="background:{C_TAG_BG};padding:20px 28px 10px;">'
        f'{section_header("Agenda de esta semana", "CAL", C_ACCENT)}'
        f'{agenda_html}'
        f'</td></tr>'
    ) if agenda_html else ""

    # ── Sección Ideas accionables ──────────────────────
    ideas_html = ""
    for idea in data.get("ideas_accionables", []):
        ideas_html += f"""
        <div style="
            background:#fffaf6;
            border:1px solid {C_BORDER};
            border-left:3px solid {C_ACCENT};
            border-radius:8px;
            padding:14px 18px;
            margin-bottom:10px;">
          <div style="
              font-size:14px;
              font-weight:700;
              color:{C_TEXT};
              margin-bottom:6px;">
            {idea.get("idea", "")}
          </div>
          <div style="
              font-size:12px;
              color:{C_TEXT_LIGHT};
              line-height:1.6;
              margin-bottom:8px;">
            Basado en: {idea.get("basado_en", "")}
          </div>
          <div style="
              font-size:13px;
              color:{C_TEXT};
              line-height:1.65;">
            {idea.get("accion_para_ysl", "")}
          </div>
        </div>"""

    ideas_section = (
        f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">'
        f'{section_header("Ideas accionables para YSL/L Oreal", "*", C_ACCENT)}'
        f'{ideas_html}'
        f'</td></tr>'
    ) if ideas_html else ""

    # ── Sección Para comentar con su jefa ──────────────
    jefa_html = "".join([boss_talk_card(i) for i in data.get("para_comentar_con_jefa", [])])
    jefa_section = (
        f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">'
        f'{section_header("Para comentar con tu jefa", "TALK", "#59656f")}'
        f'{jefa_html}'
        f'</td></tr>'
    ) if jefa_html else ""

    # ── Sección No perder de vista ─────────────────────
    watch_html = "".join([news_card(n, False) for n in data.get("no_perder_de_vista", [])])
    watch_section = (
        f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">'
        f'{section_header("No perder de vista", "!", "#8a7ab5")}'
        f'{watch_html}'
        f'</td></tr>'
    ) if watch_html else ""

    # ── Sección El Rincón (guiño masculino) ────────────
    rincon            = data.get("el_rincon", {})
    rincon_items_html = ""

    for item in rincon.get("items", []):
        url         = item.get("url", "")
        titulo      = item.get("titulo", "")
        titulo_html = (
            f'<a href="{url}" style="color:{C_TEXT};text-decoration:none;">{titulo}</a>'
            if url else titulo
        )
        rincon_items_html += f"""
        <div style="
            background:#f8f6ff;
            border:1px solid #e0daf0;
            border-radius:10px;
            padding:14px 18px;
            margin-bottom:10px;">
          <div style="
              font-size:11px;
              font-weight:700;
              color:#8a7ab5;
              text-transform:uppercase;
              margin-bottom:4px;">
            {item.get("marca", "")}
          </div>
          <div style="
              font-size:14px;
              font-weight:600;
              color:{C_TEXT};
              margin-bottom:6px;">
            {titulo_html}
          </div>
          <div style="
              font-size:13px;
              color:{C_TEXT_LIGHT};
              line-height:1.65;">
            {item.get("descripcion", "")}
          </div>
          <div style="
              font-size:11px;
              color:#ccc;
              margin-top:6px;
              font-style:italic;">
            {item.get("fuente", "")}
          </div>
        </div>"""

    rincon_section = (
        f'<tr><td style="background:{C_WHITE};padding:16px 28px 8px;">'
        f'{section_header(rincon.get("titulo", "El Rincon"), "🪒", "#8a7ab5")}'
        f'<div style="font-size:13px;color:{C_TEXT_LIGHT};font-style:italic;margin-bottom:14px;">'
        f'{rincon.get("intro", "")}'
        f'</div>'
        f'{rincon_items_html}'
        f'</td></tr>'
    ) if rincon else ""

    # ── Sección Posts LinkedIn ──────────────────────────
    linkedin_posts = data.get("posts_linkedin", [])
    linkedin_cards = "".join([linkedin_card(p, i + 1) for i, p in enumerate(linkedin_posts)])
    linkedin_section = (
        f'<tr><td style="background:{C_TAG_BG};padding:24px 28px;">'
        f'{section_header("Tus posts de LinkedIn esta semana", "💼", C_ACCENT)}'
        f'<div style="font-size:12px;color:{C_TEXT_LIGHT};margin-bottom:16px;margin-top:-10px;">'
        f'Listos para copiar y publicar'
        f'</div>'
        f'{linkedin_cards}'
        f'</td></tr>'
    ) if linkedin_cards else ""

    # ── HTML completo del email ─────────────────────────
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Beauty Briefing Semanal</title>
</head>
<body style="margin:0;padding:0;background:{C_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{C_BG};padding:24px 0 40px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

          <!-- HEADER -->
          <tr>
            <td style="background:{C_HEADER_BG};border-radius:12px 12px 0 0;padding:28px 28px 22px;">
              <div style="font-size:11px;font-weight:500;letter-spacing:0.2em;text-transform:uppercase;color:{C_TEXT_LIGHT};margin-bottom:6px;">
                Beauty Briefing - {fecha}
              </div>
              <div style="font-size:26px;font-weight:700;color:{C_TEXT};letter-spacing:-0.02em;line-height:1.2;">
                Tu reporte semanal<br>de lujo y beauty
              </div>
              {f'<div style="font-size:13px;color:{C_ACCENT};margin-top:10px;font-style:italic;">{frase}</div>' if frase else ""}
            </td>
          </tr>

          <!-- SALUDO -->
          <tr>
            <td style="background:{C_WHITE};padding:20px 28px 16px;">
              <div style="font-size:13.5px;color:{C_TEXT};line-height:1.75;">
                Hola {recipient_name} 💛<br><br>
                Tu novio te ha preparado este radar para que empieces la semana con contexto,
                ideas y referencias frescas: beauty, lujo, fragancias, moda, cultura, social,
                retail y movimientos que pueden inspirar propuestas para YSL Beauty y L'Oreal.<br><br>
                <span style="font-size:12px;color:{C_ACCENT};font-style:italic;">(te quiero)</span>
              </div>
            </td>
          </tr>

          <!-- SECCIONES -->
          {tendencias_section}
          {novedades_section}
          {casas_section}
          {comp_section}
          {digital_section}
          {moda_section}
          {agenda_section}
          {ideas_section}
          {jefa_section}
          {watch_section}
          {rincon_section}
          {linkedin_section}

          <!-- FOOTER -->
          <tr>
            <td style="background:{C_WHITE};border-radius:0 0 12px 12px;padding:16px 28px 24px;">
              <div style="border-top:1px solid {C_BORDER};padding-top:16px;font-size:11px;color:#ccc;text-align:center;">
                Beauty Briefing semanal - {fecha} - Generado por el mago, el titan, el animal,
                maestro, genio, unico e irremplazable Jose Manuel Huertas
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ─────────────────────────────────────────────────────
# ENVÍO DEL EMAIL VÍA GMAIL SMTP
# ─────────────────────────────────────────────────────

def send_email(html_body: str, subject: str):
    """Envía el email al destinatario principal y, opcionalmente, a una copia."""
    log.info(f"Enviando email a {RECIPIENT_EMAIL}...")
    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Beauty Briefing <{GMAIL_USER}>"
    msg["To"]      = RECIPIENT_EMAIL

    recipients = [RECIPIENT_EMAIL]
    if CC_EMAIL:
        recipients.append(CC_EMAIL)
        msg["Cc"] = CC_EMAIL

    plain_body = html.unescape(re.sub(r"<[^>]+>", " ", html_body))
    plain_body = re.sub(r"\s+", " ", plain_body).strip()
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, recipients, msg.as_string())

    log.info("Email enviado correctamente")


# ─────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────

def main():
    log.info("=" * 55)
    log.info("Beauty Briefing Agent - inicio")
    log.info(f"TEST_MODE: {TEST_MODE}")
    log.info("=" * 55)

    # 1. Cargar memoria de semanas anteriores
    memory = load_memory()

    # 2. Buscar noticias nuevas con Tavily
    articles = fetch_news(memory)
    if not articles:
        raise RuntimeError("Tavily no devolvio noticias validas; se cancela el envio.")

    # 3. Generar el briefing con Groq
    try:
        briefing = generate_briefing(articles, memory)
    except Exception:
        log.exception("Groq no disponible; se enviara una edicion de respaldo sin IA.")
        briefing = build_fallback_briefing(articles)

    # 4. Renderizar el email
    fecha_bonita = datetime.now(SPAIN_TZ).strftime("%d de %B de %Y")
    subject      = f"Tu beauty briefing - {fecha_bonita}"
    html         = render_email_html(briefing, RECIPIENT_NAME)

    if TEST_MODE:
        log.info("TEST MODE - email no enviado. Briefing generado:")
        log.info(json.dumps(briefing, ensure_ascii=False, indent=2))
        with open("briefing_preview.html", "w", encoding="utf-8") as preview:
            preview.write(html)
        log.info("Vista previa guardada en briefing_preview.html; memoria sin cambios.")
    else:
        send_email(html, subject)
        # Solo marcar noticias como vistas después de confirmar el envío.
        update_memory(memory, briefing)
        save_memory(memory)

    log.info("Agente completado")


if __name__ == "__main__":
    main()
