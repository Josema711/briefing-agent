# Beauty Briefing Agent

Agente que genera y envía automáticamente un briefing semanal de belleza y lujo **cada lunes a las 07:17 (Europe/Madrid)** usando GitHub Actions — sin servidores, sin cron local, 100% automático.

## Qué hace

Cada lunes por la mañana, sin que nadie haga nada:

1. **Busca noticias reales** con Tavily
2. **Filtra lo ya visto** — gracias a la memoria entre semanas, nunca repite una noticia
3. **Genera el briefing** con Groq, usando modelos actuales con fallback automático
4. **Envía el email** con diseño editorial en tonos nude y terracota
5. **Guarda la memoria** automáticamente en el repo para la semana siguiente

Si todos los modelos de Groq están temporalmente fuera de servicio, se envía una edición básica con las noticias y sus enlaces en lugar de perder el correo semanal.

## Qué incluye el email

- 📈 **Tendencias** — qué está trending en beauty y moda de lujo
- ✨ **Novedades** — lanzamientos y campañas nuevas
- 🏛️ **Casas de lujo** — noticias importantes de LVMH, Kering y las grandes maisons
- 👁️ **YSL Beauty & Competencia** — foco en YSL, Dior, Chanel, Tom Ford, Givenchy, Armani, Lancôme
- 📱 **Digital & Social Media** — TikTok, Instagram, radar competencia digital, tendencia emergente
- 🪒 **El Rincón** — apartado semanal de grooming, fragancias y skincare masculina de lujo
- 💼 **2 posts de LinkedIn** completos y listos para publicar

Si hay noticias de España, aparecen primero con badge 🇪🇸 dentro de su sección.

## Stack

| Herramienta | Para qué | Coste |
|---|---|---|
| **Tavily API** | Búsqueda de noticias | 1.000 créditos gratuitos/mes |
| **Groq API** | IA (`openai/gpt-oss-120b` + fallbacks) | Cuota gratuita |
| **Gmail SMTP** | Envío del email | Gratis |
| **GitHub Actions** | Automatización del cron semanal + logs | Gratis |

**Coste total: 0€/mes**

---

## Setup

### 1. Clonar o subir el repo a GitHub

Asegúrate de tener esta estructura:

```
briefing-agent/
├── .github/
│   └── workflows/
│       └── weekly_briefing.yml
├── briefing_agent.py
├── memory.json
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

### 2. Conseguir las API keys gratuitas

**Tavily** (búsqueda de noticias):
1. Ve a [tavily.com](https://tavily.com) → Sign up
2. Dashboard → API Keys → copia tu key (`tvly-...`)

**Groq** (la IA):
1. Ve a [console.groq.com](https://console.groq.com) → Sign up con Google
2. API Keys → Create API key → copia tu key (`gsk_...`)

**Gmail App Password** (para enviar el email):
1. [myaccount.google.com](https://myaccount.google.com) → Seguridad
2. Verificación en 2 pasos → actívala si no está
3. Contraseñas de aplicaciones → crea una → nombre: `Beauty Briefing`
4. Copia los 16 caracteres generados

### 3. Añadir los Secrets en GitHub

Ve a tu repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Valor |
|---|---|
| `TAVILY_API_KEY` | Tu key de Tavily (`tvly-...`) |
| `GROQ_API_KEY` | Tu key de Groq (`gsk_...`) |
| `GMAIL_USER` | El Gmail desde el que se envía |
| `GMAIL_APP_PASSWORD` | Los 16 caracteres del App Password |
| `RECIPIENT_EMAIL` | El correo de la destinataria |
| `RECIPIENT_NAME` | Su nombre (para el saludo) |
| `CC_EMAIL` | Opcional: dirección que recibirá una copia visible |

Opcionalmente puedes crear una variable de repositorio llamada `GROQ_MODELS` para cambiar el orden de modelos. Si no existe, se usa:

```text
openai/gpt-oss-120b,qwen/qwen3.6-27b,openai/gpt-oss-20b
```

### 4. Probar antes del lunes

En GitHub → **Actions → YSL Beauty Intelligence Briefing → Run workflow**

- `test_mode: true` → genera el briefing, no envía email ni modifica la memoria, y sube una vista previa HTML
- `test_mode: false` → envío real

---

## Cómo funciona la memoria

Después de cada envío confirmado, el agente guarda en `memory.json` las URLs y temas cubiertos esa semana. La siguiente semana los filtra antes de buscar, así el contenido siempre es fresco.

El archivo se actualiza solo — el workflow hace un `git commit` automático cada lunes con el mensaje `Update memory [skip ci]`. No tienes que tocar nada.

Guarda hasta **8 semanas de histórico** (~200 artículos) y luego borra lo más antiguo automáticamente.

---

## Horario

El cron utiliza directamente la zona horaria `Europe/Madrid`, por lo que no hay que cambiarlo entre verano e invierno:

```yaml
- cron: '17 7 * * 1'
  timezone: 'Europe/Madrid'
```

---

## Ver los logs

GitHub guarda los logs de cada ejecución automáticamente:

**Actions → click en el run → "send-briefing"**

Si falla, GitHub sube el `briefing_agent.log` como artefacto descargable (guardado 30 días).

---

## Desarrollo local

```bash
# Crea un archivo .env con tus credenciales (ver .env.example)
cp .env.example .env

# Instala dependencias
pip install -r requirements.txt

# Ejecuta en modo test (no envía email)
TEST_MODE=true python briefing_agent.py

# Pruebas unitarias
python -m unittest discover -s tests -v

# Ejecuta con envío real
python briefing_agent.py
```

---

## Estructura del proyecto

```
briefing-agent/
├── .github/
│   └── workflows/
│       └── weekly_briefing.yml   # GitHub Actions — lunes 07:17 Europe/Madrid
├── briefing_agent.py             # Script principal
├── memory.json                   # Memoria entre semanas (se actualiza solo)
├── requirements.txt              # groq
├── .env.example                  # Template para desarrollo local
├── .gitignore                    # .env y logs excluidos
├── tests/                        # Pruebas sin APIs ni envío real
└── README.md
```

> ⚠️ No subas nunca el archivo `.env` a GitHub — está en el `.gitignore` por defecto.
