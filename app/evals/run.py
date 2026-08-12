"""CLI entry point for the eval harness (Phase 5c):

    python -m app.evals.run

Requires a real LLM_MODEL (and EMBEDDING_MODEL, for Search cases; and
JUDGE_MODEL, for any llm_judge-scored case) API key set in the
environment (same `.env` the app itself reads via app/config.py) — this
makes real, billable calls. Never run automatically by `pytest` or
`docker compose up`; see README.md's "Eval harness (Phase 5c)" section.
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.control.audit import configure_app_logging
from app.evals.runner import EvalRunner, load_cases, render_json, render_markdown

REPORTS_DIR = Path(__file__).parent / "reports"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the AI Orchestrator eval harness against the real, configured pipeline. "
        "Requires a real API key and makes real, billable LLM/embedding calls."
    )
    parser.add_argument(
        "--cases-dir", type=Path, default=None, help="Directory of case YAML files (default: app/evals/cases)"
    )
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Report format.")
    parser.add_argument(
        "--out", type=Path, default=None, help="Report output path (default: app/evals/reports/<timestamp>.<ext>)"
    )
    return parser.parse_args(argv)


async def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    settings = get_settings()
    configure_app_logging(settings.log_level)

    cases = load_cases(args.cases_dir)
    print(f"Loaded {len(cases)} eval case(s) from {args.cases_dir or 'app/evals/cases'}.")
    if not cases:
        print("No cases found — nothing to run.")
        return 0

    runner = EvalRunner(settings)
    report = await runner.run_all(cases)

    ext = "md" if args.format == "markdown" else "json"
    out_path = args.out or (REPORTS_DIR / f"eval-report-{report.generated_at.replace(':', '-')}.{ext}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = render_markdown(report) if args.format == "markdown" else render_json(report)
    out_path.write_text(content, encoding="utf-8")

    print(f"\n{report.passed}/{report.total} cases passed ({report.pass_rate:.0%}).")
    if report.average_score is not None:
        print(f"Average score: {report.average_score:.2f}")
    print(f"Report written to {out_path}")

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
