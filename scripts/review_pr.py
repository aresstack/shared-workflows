"""Review pull request diffs without executing repository code."""

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MODEL = "gemini-4-neon"
SERVICE_TIER = "flex"
API_TIMEOUT_MS = 900_000
MAX_DIFF_BYTES = 400_000
MAX_REVIEW_CHARS = 50_000


class ReviewError(Exception):
    """Report an actionable failure without logging source code or credentials."""


class StalePullRequest(Exception):
    """Stop reviewing a superseded pull request revision."""


@dataclass(frozen=True)
class PullRequest:
    repository: str
    number: int
    base_sha: str
    head_sha: str

    @classmethod
    def from_environment(cls):
        return cls(os.environ["REPO"], int(os.environ["PR_NUMBER"]),
                   os.environ["BASE_SHA"], os.environ["HEAD_SHA"])


class GitHubClient:
    def __init__(self, token, pull_request):
        self.token = token
        self.pull_request = pull_request
        self.url = ("https://api.github.com/repos/"
                    f"{pull_request.repository}/pulls/{pull_request.number}")

    def _request(self, suffix="", accept="application/vnd.github+json",
                 payload=None, limit=2_000_000):
        headers = {"Authorization": f"Bearer {self.token}",
                   "Accept": accept, "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "aresstack-gemini-review"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = Request(self.url + suffix, data=data, headers=headers)
        try:
            with urlopen(request, timeout=60) as response:
                content = response.read(limit + 1)
        except HTTPError as error:
            raise ReviewError(f"GitHub API returned HTTP {error.code}.") from None
        if len(content) > limit:
            raise ReviewError("GitHub response exceeds the review size limit; "
                              "no partial review was submitted.")
        return content

    def ensure_current(self):
        current = json.loads(self._request())
        expected = self.pull_request
        if (current["state"] != "open"
                or current["head"]["sha"] != expected.head_sha
                or current["base"]["sha"] != expected.base_sha):
            raise StalePullRequest()

    def read_diff(self):
        self.ensure_current()
        diff = self._request(accept="application/vnd.github.v3.diff",
                             limit=MAX_DIFF_BYTES).decode("utf-8", errors="replace")
        self.ensure_current()
        return diff

    def publish_review(self, review):
        self.ensure_current()
        self._request("/reviews", payload={
            "commit_id": self.pull_request.head_sha,
            "event": "COMMENT",
            "body": review,
        })


class GeminiReviewer:
    def __init__(self, client, api_error_type, thinking_level="medium",
                 sleep=time.sleep):
        self.client = client
        self.api_error_type = api_error_type
        self.thinking_level = thinking_level
        self.sleep = sleep

    @classmethod
    def connect(cls, api_key, thinking_level):
        from google import genai
        from google.genai import errors

        client = genai.Client(api_key=api_key, http_options={
            "timeout": API_TIMEOUT_MS, "retry_options": {"attempts": 1}
        })
        return cls(client, errors.APIError, thinking_level)

    def review(self, diff, instructions):
        for attempt in range(2):
            try:
                result = self.client.interactions.create(
                    model=MODEL, input=diff, system_instruction=instructions,
                    service_tier=SERVICE_TIER, store=False,
                    generation_config={"thinking_level": self.thinking_level,
                                       "max_output_tokens": 16384},
                )
            except self.api_error_type as error:
                if getattr(error, "code", None) not in (429, 503) or attempt == 1:
                    raise ReviewError(
                        f"Gemini Flex returned HTTP {getattr(error, 'code', 'unknown')}."
                    ) from None
                print("Gemini Flex temporarily unavailable; retrying in 30 seconds.")
                self.sleep(30)
                continue
            if getattr(result, "status", None) != "completed":
                raise ReviewError("Gemini did not complete the interaction.")
            text = result.output_text
            if not text or not text.strip():
                raise ReviewError("Gemini returned no review text.")
            return text.strip()
        raise ReviewError("Gemini Flex retry budget exhausted.")


def format_review(text):
    # Neutralize mentions and externally loaded Markdown images or HTML.
    safe = text.replace("@", "@\u200b").replace("<", "&lt;").replace(">", "&gt;")
    safe = re.sub(r"!\[", "[", safe)
    if len(safe) > MAX_REVIEW_CHARS:
        raise ReviewError("Gemini review exceeds the publication size limit.")
    return ("## Gemini 4 Neon – KI-Code-Review (Flex)\n\n"
            + safe
            + "\n\n---\nAutomatisierte Hinweise auf Basis des PR-Diffs; "
              "keine Freigabe und kein Ersatz für menschliches Review.")


class ReviewUseCase:
    def __init__(self, github, reviewer, instructions):
        self.github = github
        self.reviewer = reviewer
        self.instructions = instructions

    def execute(self):
        diff = self.github.read_diff()
        if not diff.strip():
            print("No changes to review.")
            return
        review = self.reviewer.review(diff, self.instructions)
        self.github.publish_review(format_review(review))
        print("Gemini Flex review published.")


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ReviewError("GEMINI_API_KEY is missing or unavailable.")
    instructions = (Path(__file__).resolve().parents[1]
                    / "prompts" / "code-review.md").read_text(encoding="utf-8")
    github = GitHubClient(os.environ["GITHUB_TOKEN"], PullRequest.from_environment())
    reviewer = GeminiReviewer.connect(api_key, os.environ.get(
        "GEMINI_THINKING_LEVEL", "medium"))
    ReviewUseCase(github, reviewer, instructions).execute()


if __name__ == "__main__":
    try:
        main()
    except StalePullRequest:
        print("Pull request revision changed; no obsolete review published.")
    except ReviewError as error:
        print(f"Review failed: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        print(f"Review failed: {type(error).__name__}.", file=sys.stderr)
        sys.exit(1)
