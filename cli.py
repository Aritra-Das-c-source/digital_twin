"""Repository-level entry point for Digital Twin operations.

Run ``py cli.py`` on Windows or ``python3 cli.py`` on macOS/Linux.  Calling it
without a subcommand opens the interactive Python shell; all documented
subcommands remain available for automation.
"""

from bottlenecks_prediction.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
