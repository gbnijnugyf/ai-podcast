import os
import unittest
from unittest.mock import MagicMock, patch

import requests

from src.topic_searcher import TopicSearcher


def session_with(result):
    session = MagicMock()
    session.__enter__.return_value = session
    if isinstance(result, Exception):
        session.get.side_effect = result
    else:
        session.get.return_value = result
    return session


class BingFallbackTests(unittest.TestCase):
    def setUp(self):
        self.searcher = TopicSearcher.__new__(TopicSearcher)
        self.searcher.timeout = 3
        self.response = MagicMock(text="search result")

    def test_bing_com_is_primary_and_cn_is_fallback(self):
        sessions = [
            session_with(requests.ConnectionError("primary unavailable")),
            session_with(self.response),
        ]
        with patch.dict(os.environ, {}, clear=True), \
                patch("src.topic_searcher.requests.Session", side_effect=sessions):
            result = self.searcher._fetch_bing("/search?q=test", {}, "网页")
        self.assertEqual(result, "search result")
        self.assertTrue(sessions[0].get.call_args.args[0].startswith("https://bing.com/"))
        self.assertTrue(sessions[1].get.call_args.args[0].startswith("https://cn.bing.com/"))

    def test_cloud_proxy_is_used_before_direct_access(self):
        sessions = [
            session_with(requests.exceptions.ProxyError("proxy unavailable")),
            session_with(self.response),
        ]
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid"}, clear=True), \
                patch("src.topic_searcher.requests.Session", side_effect=sessions):
            result = self.searcher._fetch_bing("/search?q=test", {}, "网页")
        self.assertEqual(result, "search result")
        self.assertTrue(sessions[0].trust_env)
        self.assertFalse(sessions[1].trust_env)
        self.assertEqual(sessions[0].get.call_args.args[0], sessions[1].get.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
