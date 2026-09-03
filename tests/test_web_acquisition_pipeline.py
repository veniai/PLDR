from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from pldr_api.extraction import (
    assess_extraction,
    extract_page,
    near_duplicate_similarity,
    paragraph_spans,
)
from pldr_api.importers import (
    FetchedPublicText,
    ReaderFallbackError,
    _pinned_public_destination,
    fetch_public_text_response,
)
from pldr_api.security import UnsafeUrlError


class AcquisitionPipelineTest(unittest.TestCase):
    def test_dns_answers_are_validated_and_connection_url_is_ip_pinned(self):
        public_answer = (2, 1, 6, "", ("93.184.216.34", 443))
        private_answer = (2, 1, 6, "", ("127.0.0.1", 443))
        with patch("pldr_api.importers.socket.getaddrinfo", return_value=[public_answer]):
            pinned, host, sni = _pinned_public_destination("https://example.org/path?q=1")
        self.assertEqual(pinned, "https://93.184.216.34/path?q=1")
        self.assertEqual((host, sni), ("example.org", "example.org"))

        with patch(
            "pldr_api.importers.socket.getaddrinfo",
            return_value=[public_answer, private_answer],
        ):
            with self.assertRaises(UnsafeUrlError):
                _pinned_public_destination("https://example.org/path")

    def test_article_extraction_preserves_paragraphs_and_metadata(self):
        html = """
        <html><head><title>航运通告</title>
        <meta property="article:published_time" content="2026-08-15T08:00:00Z"></head>
        <body><nav>菜单 菜单</nav><article>
        <p>第一段说明一艘商船按计划通过目标海域，并由公开通告记录。通告同时说明航线没有关闭，相关信息来自当天公开发布的航行公告。</p>
        <p>第二段补充主管部门正在核对相关信息，后续仍需持续更新。报道区分了已经观察到的通行事实与仍待确认的事故原因。</p>
        <p>第三段提供足够长度，使正文质量判定依据文章内容而不是页面导航。材料还说明后续公告会作为同一专题的新版本保存。</p>
        </article></body></html>
        """
        page = extract_page(html, url="https://news.example/article")
        self.assertEqual(page.title, "航运通告")
        self.assertEqual(page.published_at.date().isoformat(), "2026-08-15")
        self.assertNotIn("菜单", page.body)
        self.assertGreaterEqual(len(paragraph_spans(page.body)), 3)
        self.assertEqual(assess_extraction(page).status, "usable")

    def test_near_duplicate_detection_is_conservative(self):
        original = "这是一篇关于海上无人作战系统测试的公开报道。" * 30
        repost = original + "转载编辑补充了一句来源说明。"
        unrelated = "另一篇材料讨论粮食价格与区域降雨。" * 30
        self.assertGreater(near_duplicate_similarity(original, repost), 0.82)
        self.assertLess(near_duplicate_similarity(original, unrelated), 0.2)

    def test_reader_is_used_only_after_eligible_direct_failure(self):
        rendered = FetchedPublicText(
            "https://public.example/article", "<article>" + "正文" * 100 + "</article>",
            200, "text/html", 219, "jina_reader", {},
        )
        with patch.dict(os.environ, {"PLDR_READER_FALLBACK_ENABLED": "true"}), patch(
            "pldr_api.importers._fetch_public_text_response",
            new=AsyncMock(side_effect=httpx.ReadTimeout("direct timeout")),
        ), patch(
            "pldr_api.importers._fetch_reader_html_response", new=AsyncMock(return_value=rendered)
        ) as reader:
            result = asyncio.run(fetch_public_text_response("https://public.example/article"))
        self.assertEqual(result.fetch_method, "jina_reader")
        reader.assert_awaited_once()

        with patch.dict(os.environ, {"PLDR_READER_FALLBACK_ENABLED": "true"}), patch(
            "pldr_api.importers._fetch_public_text_response",
            new=AsyncMock(side_effect=UnsafeUrlError("private target")),
        ), patch(
            "pldr_api.importers._fetch_reader_html_response", new=AsyncMock()
        ) as reader:
            with self.assertRaises(UnsafeUrlError):
                asyncio.run(fetch_public_text_response("https://public.example/article"))
        reader.assert_not_awaited()

    def test_low_quality_direct_page_fails_when_reader_also_fails(self):
        challenge = FetchedPublicText(
            "https://public.example/article",
            "<html><body>Access denied " + "navigation " * 30 + "</body></html>",
            200,
            "text/html",
            360,
        )
        with patch.dict(os.environ, {"PLDR_READER_FALLBACK_ENABLED": "true"}), patch(
            "pldr_api.importers._fetch_public_text_response",
            new=AsyncMock(return_value=challenge),
        ), patch(
            "pldr_api.importers._fetch_reader_html_response",
            new=AsyncMock(side_effect=httpx.ReadTimeout("reader timeout")),
        ):
            with self.assertRaises(ReaderFallbackError) as context:
                asyncio.run(fetch_public_text_response("https://public.example/article"))
        self.assertIn("not usable", str(context.exception.direct_error))


if __name__ == "__main__":
    unittest.main()
