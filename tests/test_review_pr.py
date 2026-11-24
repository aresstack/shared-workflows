import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from review_pr import (GeminiReviewer, ReviewError, ReviewUseCase,
                       StalePullRequest, format_review)


class ApiError(Exception):
    def __init__(self, code):
        self.code = code


class ReviewerTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.sleep = Mock()
        self.reviewer = GeminiReviewer(self.client, ApiError, sleep=self.sleep)
        self.response = SimpleNamespace(status="completed", output_text="Befund")

    def test_flex_request_and_stateless_mode(self):
        self.client.interactions.create.return_value = self.response
        self.assertEqual("Befund", self.reviewer.review("diff", "instructions"))
        args = self.client.interactions.create.call_args.kwargs
        self.assertEqual("gemini-4-neon", args["model"])
        self.assertEqual("flex", args["service_tier"])
        self.assertFalse(args["store"])
        self.assertEqual("medium", args["generation_config"]["thinking_level"])

    def test_retry_keeps_flex_tier(self):
        self.client.interactions.create.side_effect = [ApiError(503), self.response]
        self.reviewer.review("diff", "instructions")
        self.sleep.assert_called_once_with(30)
        self.assertEqual(2, self.client.interactions.create.call_count)
        for call in self.client.interactions.create.call_args_list:
            self.assertEqual("flex", call.kwargs["service_tier"])

    def test_non_retryable_error(self):
        self.client.interactions.create.side_effect = ApiError(400)
        with self.assertRaises(ReviewError):
            self.reviewer.review("diff", "instructions")
        self.sleep.assert_not_called()

    def test_empty_response_is_failure(self):
        self.client.interactions.create.return_value = SimpleNamespace(
            status="completed", output_text="")
        with self.assertRaises(ReviewError):
            self.reviewer.review("diff", "instructions")

    def test_stale_diff_never_reaches_model(self):
        github, reviewer = Mock(), Mock()
        github.read_diff.side_effect = StalePullRequest()
        with self.assertRaises(StalePullRequest):
            ReviewUseCase(github, reviewer, "rules").execute()
        reviewer.review.assert_not_called()
        github.publish_review.assert_not_called()

    def test_review_neutralizes_mentions_and_html(self):
        body = format_review("@someone <img> ![image](https://example.org)")
        self.assertNotIn("@someone", body)
        self.assertNotIn("<img>", body)
        self.assertNotIn("![", body)


if __name__ == "__main__":
    unittest.main()
