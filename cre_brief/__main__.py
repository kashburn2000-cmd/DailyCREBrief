"""Command-line entry point: ``python -m cre_brief``.

    python -m cre_brief                 # build and send to RECIPIENTS
    python -m cre_brief --no-send       # dry run: render + print, never send
    python -m cre_brief --no-send -v    # dry run with debug logging
    python -m cre_brief --print-html    # (with --no-send) also dump HTML to stdout
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import ConfigError
from .gemini import GeminiError


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # requests/urllib3 are chatty at DEBUG; keep them at WARNING.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cre_brief",
        description="Build and send the CRE Finance Brief daily newsletter.",
    )
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="Dry run: render and print the brief instead of emailing it.",
    )
    parser.add_argument(
        "--print-html",
        action="store_true",
        help="With --no-send, also print the raw HTML to stdout.",
    )
    parser.add_argument(
        "--out",
        default="out",
        help="Directory for the rendered HTML/text preview on a dry run (default: out). "
             "Use '' to skip writing files.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("cre_brief")

    # Import after logging is configured so module-load messages are formatted.
    from .brief import run

    try:
        return run(
            send=not args.no_send,
            out_dir=(args.out or None),
            print_html=args.print_html,
        )
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2
    except GeminiError as exc:
        log.error("Gemini error: %s", exc)
        return 3
    except KeyboardInterrupt:  # pragma: no cover
        log.error("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
