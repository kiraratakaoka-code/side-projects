#!/usr/bin/env python3
"""Summarize a chunk of text (e.g. a paper abstract or notes) into key bullet points using Claude."""

import argparse
import sys

import anthropic

MODEL = "claude-opus-5"

SYSTEM_PROMPT = (
    "You are a research assistant. Summarize the text the user gives you into "
    "clear, concise bullet points that capture the key ideas, findings, and "
    "conclusions. Use markdown \"-\" bullets, 3 to 7 bullets, no preamble or "
    "closing remarks."
)


def summarize(text: str, model: str = MODEL) -> str:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    return next(block.text for block in response.content if block.type == "text")


def read_input(args: argparse.Namespace) -> str:
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            return f.read()
    if args.text:
        return args.text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("No input text provided. Use --file, --text, or pipe text via stdin.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="?", help="Text to summarize (or use --file / stdin)")
    parser.add_argument("--file", "-f", help="Path to a text file to summarize")
    parser.add_argument("--model", default=MODEL, help=f"Claude model to use (default: {MODEL})")
    args = parser.parse_args()

    text = read_input(args)

    try:
        summary = summarize(text, model=args.model)
    except anthropic.AuthenticationError:
        raise SystemExit("Authentication failed. Set ANTHROPIC_API_KEY or run `ant auth login`.")
    except anthropic.RateLimitError as e:
        retry_after = e.response.headers.get("retry-after", "a few seconds")
        raise SystemExit(f"Rate limited. Retry after {retry_after}.")
    except anthropic.APIConnectionError:
        raise SystemExit("Network error while contacting the Claude API. Check your connection.")
    except anthropic.APIStatusError as e:
        raise SystemExit(f"API error ({e.status_code}): {e.message}")

    print(summary)


if __name__ == "__main__":
    main()
