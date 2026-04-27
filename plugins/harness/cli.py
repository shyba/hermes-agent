"""PATH-friendly harness commands for shells and external agents."""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Sequence

from . import command, runner


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "status":
        print(command.handle_slash("status"))
        return 0
    if args.command == "run":
        print(command.handle_slash(f"run {shlex.join(args.task)}"))
        return 0
    if args.command == "check":
        return _run_check(args.sensor_id, strict_exit_code=args.strict_exit_code)
    parser.print_help()
    return 0


def check_main(argv: Sequence[str] | None = None) -> int:
    check_argv = ["check"]
    if argv is None:
        check_argv.extend(sys.argv[1:])
    else:
        check_argv.extend(argv)
    return main(check_argv)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-harness",
        description="Inspect and validate the active Hermes harness run.",
    )
    subcommands = parser.add_subparsers(dest="command")

    subcommands.add_parser("status", help="show the active harness run")

    run_parser = subcommands.add_parser("run", help="start a harness run")
    run_parser.add_argument("task", nargs="+", help="OpenSpec change id/path or task text")

    check_parser = subcommands.add_parser(
        "check",
        aliases=("validate",),
        help="run harness validation checks",
    )
    check_parser.add_argument("sensor_id", nargs="?", help="optional sensor id to run")
    check_parser.add_argument(
        "--strict-exit-code",
        action="store_true",
        help="exit non-zero unless finalization is complete",
    )

    parser.set_defaults(command="status")
    return parser


def _run_check(sensor_id: str | None, *, strict_exit_code: bool) -> int:
    try:
        output = runner.run_check(sensor_id=sensor_id)
    except Exception as exc:
        print(f"Harness check error: {exc}")
        return 1 if strict_exit_code else 0

    print(output)
    if strict_exit_code and "Finalization: complete" not in output:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
