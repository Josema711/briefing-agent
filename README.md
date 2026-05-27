# YSL Beauty Intelligence Briefing Agent

Agente que genera y envía automáticamente un briefing semanal de marketing de lujo **cada domingo a las 9AM** usando GitHub Actions — sin servidores, sin cron local, 100% gratuito.

## Arquitectura

```
GitHub Actions (cron domingo 9AM)
        ↓
briefing_agent.py
        ↓
Claude API + Web Search  →  JSON estructurado
        ↓
Gmail SMTP  →  Email HTML en bandeja de tu novia
```

---

## Setup en 10 minutos

### 1. Subir a GitHub

```bash
git init
git add .
git commit -m "YSL briefing agent"
git remote add origin https://github.com/TU_USUARIO/ysl-briefing-agent.git
git push -u origin main
```

### 2. Añadir los Secrets en GitHub

Ve a tu repo → **Settings → Secrets and variables → Actions → New repository secret**

Añade estos 5 secrets:

| Secret | Valor |
|--------|-------|
| `ANTHROPIC_API_KEY` | La API key de la amiga de tu novia |
| `GMAIL_USER` | El Gmail desde el que se envía |
| `GMAIL_APP_PASSWORD` | App Password de Gmail (ver paso 3) |
| `RECIPIENT_EMAIL` | El correo de tu novia |
| `RECIPIENT_NAME` | Su nombre (para el saludo) |

> ✅ Los secrets están **encriptados** en GitHub. Ni tú los puedes ver una vez guardados. La amiga de tu novia puede rotar la key cuando quiera sin tocar el código.

### 3. Crear Gmail App Password

Gmail no permite SMTP con tu contraseña normal:

1. [myaccount.google.com](https://myaccount.google.com) → **Seguridad**
2. Activa **Verificación en 2 pasos** si no está
3. Busca **Contraseñas de aplicaciones**
4. Crea una: App "Correo", Dispositivo "Otro" → escribe `YSL Agent`
5. Copia los 16 caracteres → pégalos como `GMAIL_APP_PASSWORD`

### 4. Probar antes del domingo

En GitHub → **Actions → YSL Beauty Intelligence Briefing → Run workflow**

Aparece un botón "Run workflow" manual. Puedes elegir `test_mode = true` para que genere el briefing pero **no envíe el email** (útil para ver si la API funciona sin gastar créditos de envío).

---

## Ver logs

GitHub guarda los logs de cada ejecución automáticamente:

**Actions → click en el run → "send-briefing"** → ves todo en tiempo real.

Si falla, GitHub sube automáticamente el `briefing_agent.log` como artefacto descargable (guardado 30 días).

---

## Horario

El cron está configurado para **07:00 UTC**, que equivale a:
- 🌞 **09:00 AM en verano** (España, UTC+2)
- ⚠️ En invierno (octubre–marzo) llega a las 8AM. Para mantener las 9AM en invierno, cambia el cron en `.github/workflows/weekly_briefing.yml` a `'0 8 * * 0'`

---

## Gestionar la API key de la amiga

La API key vive **solo en GitHub Secrets**, nunca en el código. Para rotarla:

1. La amiga genera una nueva key en [console.anthropic.com](https://console.anthropic.com)
2. Tú (o ella, si tiene acceso al repo) va a Settings → Secrets → actualiza `ANTHROPIC_API_KEY`
3. Listo, sin tocar nada más

---

## Estructura del proyecto

```
ysl-briefing-agent/
├── .github/
│   └── workflows/
│       └── weekly_briefing.yml   # GitHub Actions (el cron)
├── briefing_agent.py             # Script principal
├── requirements.txt              # anthropic
├── .env.example                  # Template para desarrollo local
├── .gitignore                    # .env y logs excluidos
└── README.md
```

## Desarrollo local

```bash
cp .env.example .env
# Edita .env con tus valores reales

# Instala dependencias
pip install -r requirements.txt

# Ejecuta
python briefing_agent.py

# Para probar sin enviar email
TEST_MODE=true python briefing_agent.py
```
