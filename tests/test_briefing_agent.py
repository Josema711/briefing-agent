import importlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


os.environ.setdefault("TEST_MODE", "true")
os.environ.setdefault("GROQ_API_KEY", "test-groq")
os.environ.setdefault("TAVILY_API_KEY", "test-tavily")

agent = importlib.import_module("briefing_agent")


class MemoryTests(unittest.TestCase):
    def test_save_memory_deduplicates_and_preserves_latest_order(self):
        memory = {
            "seen_urls": ["https://a.test", "https://a.test", "https://b.test"],
            "seen_titles": ["A", "A", "B"],
            "covered_topics": ["Tema", "Tema", "Otro"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            with patch.object(agent, "MEMORY_FILE", str(memory_path)):
                agent.save_memory(memory)
            saved = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["seen_urls"], ["https://a.test", "https://b.test"])
        self.assertEqual(saved["seen_titles"], ["A", "B"])
        self.assertEqual(saved["covered_topics"], ["Tema", "Otro"])

    def test_invalid_date_is_not_considered_recent(self):
        self.assertFalse(agent.is_within_date_range("not-a-date"))

    def test_recent_date_is_considered_recent(self):
        recent = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        self.assertTrue(agent.is_within_date_range(recent))

    def test_recent_rfc2822_date_is_considered_recent(self):
        recent = (datetime.now() - timedelta(days=2)).strftime(
            "%a, %d %b %Y 10:30:00 GMT"
        )
        self.assertTrue(agent.is_within_date_range(recent))

    def test_business_sales_headline_is_not_treated_as_seo(self):
        article = {
            "title": "Luxury beauty sales rise after fragrance launches",
            "description": "Quarterly results and market analysis.",
        }
        self.assertTrue(agent.is_actual_news(article))

    def test_gift_guide_headline_is_treated_as_seo(self):
        self.assertFalse(agent.is_actual_news({"title": "The ultimate beauty gift guide"}))


class TavilyTests(unittest.TestCase):
    @patch("briefing_agent.urllib.request.urlopen")
    def test_search_accepts_result_without_published_date(self, urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "results": [
                    {
                        "title": "YSL Beauty launches a new fragrance campaign",
                        "url": "https://example.com/story",
                        "content": "A new campaign launches across Europe.",
                    }
                ]
            }
        ).encode("utf-8")
        response.__enter__.return_value = response
        urlopen.return_value = response

        results = agent.tavily_search("luxury beauty news")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["publishedAt"], "")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["search_depth"], "basic")
        self.assertIn("start_date", payload)
        self.assertNotIn("api_key", payload)
        self.assertTrue(request.headers["Authorization"].startswith("Bearer "))


class RenderingTests(unittest.TestCase):
    def test_render_escapes_text_and_rejects_unsafe_url(self):
        data = {
            "semana": "Semana <script>",
            "novedades": [
                {
                    "marca": "Marca & Co",
                    "titulo": "<b>Titulo</b>",
                    "descripcion": "Texto",
                    "url": "javascript:alert(1)",
                }
            ],
        }

        rendered = agent.render_email_html(data, "Nombre <admin>")

        self.assertIn("Semana &lt;script&gt;", rendered)
        self.assertIn("Nombre &lt;admin&gt;", rendered)
        self.assertIn("&lt;b&gt;Titulo&lt;/b&gt;", rendered)
        self.assertNotIn("javascript:alert", rendered)

    def test_fallback_briefing_contains_source_articles(self):
        articles = [
            {
                "title": f"Noticia {index}",
                "description": "Detalle verificable",
                "source": "example.com",
                "url": f"https://example.com/{index}",
            }
            for index in range(9)
        ]

        fallback = agent.build_fallback_briefing(articles)

        self.assertEqual(len(fallback["novedades"]), 3)
        self.assertEqual(len(fallback["noticias_casas_lujo"]), 3)
        self.assertEqual(len(fallback["ysl_y_competencia"]), 3)
        self.assertIn("sin resumen de IA", fallback["frase_semana"])


class MainFlowTests(unittest.TestCase):
    @patch.object(agent, "save_memory")
    @patch.object(agent, "update_memory")
    @patch.object(agent, "send_email")
    @patch.object(agent, "render_email_html", return_value="<html>preview</html>")
    @patch.object(agent, "generate_briefing", return_value={"semana": "test"})
    @patch.object(agent, "fetch_news", return_value=[{"title": "news"}])
    @patch.object(agent, "load_memory", return_value={})
    def test_test_mode_does_not_send_or_update_memory(
        self,
        _load_memory,
        _fetch_news,
        _generate_briefing,
        _render,
        send_email,
        update_memory,
        save_memory,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.getcwd()
            try:
                os.chdir(temp_dir)
                with patch.object(agent, "TEST_MODE", True):
                    agent.main()
                self.assertTrue((Path(temp_dir) / "briefing_preview.html").exists())
            finally:
                os.chdir(previous)

        send_email.assert_not_called()
        update_memory.assert_not_called()
        save_memory.assert_not_called()

    @patch.object(agent, "save_memory")
    @patch.object(agent, "update_memory")
    @patch.object(agent, "send_email")
    @patch.object(agent, "render_email_html", return_value="<html>email</html>")
    @patch.object(agent, "generate_briefing", return_value={"semana": "test"})
    @patch.object(agent, "fetch_news", return_value=[{"title": "news"}])
    @patch.object(agent, "load_memory", return_value={})
    def test_production_updates_memory_only_after_send(
        self,
        _load_memory,
        _fetch_news,
        _generate_briefing,
        _render,
        send_email,
        update_memory,
        save_memory,
    ):
        with patch.object(agent, "TEST_MODE", False):
            agent.main()

        send_email.assert_called_once()
        update_memory.assert_called_once()
        save_memory.assert_called_once()

    @patch.object(agent, "save_memory")
    @patch.object(agent, "update_memory")
    @patch.object(agent, "send_email", side_effect=RuntimeError("smtp down"))
    @patch.object(agent, "render_email_html", return_value="<html>email</html>")
    @patch.object(agent, "generate_briefing", return_value={"semana": "test"})
    @patch.object(agent, "fetch_news", return_value=[{"title": "news"}])
    @patch.object(agent, "load_memory", return_value={})
    def test_failed_send_does_not_update_memory(
        self,
        _load_memory,
        _fetch_news,
        _generate_briefing,
        _render,
        _send_email,
        update_memory,
        save_memory,
    ):
        with patch.object(agent, "TEST_MODE", False):
            with self.assertRaisesRegex(RuntimeError, "smtp down"):
                agent.main()

        update_memory.assert_not_called()
        save_memory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
