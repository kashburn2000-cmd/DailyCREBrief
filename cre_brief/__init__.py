"""CRE Finance Brief — a $0, GitHub-Actions-scheduled CRE/CMBS finance newsletter.

The package is intentionally small and dependency-light:

    config      env/config loading and the editable FRED series + RSS feed lists
    fred        FRED client; builds "The Tape" rate table from ground-truth numbers
    feeds       RSS fetching, validation, time-window filtering and dedupe
    gemini      thin Gemini REST client with exponential backoff on HTTP 429
    synthesize  builds the strict prompt and parses Gemini's structured sections
    render      HTML + plaintext email rendering
    mailer      Resend delivery
    models      shared dataclasses
    brief       end-to-end orchestration (called by ``python -m cre_brief``)
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
