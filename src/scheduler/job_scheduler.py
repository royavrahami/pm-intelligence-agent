"""
Job Scheduler – PM Intelligence Agent.
Drives the agent's recurring execution using APScheduler.

The scheduler runs the CoreAgent on a fixed interval (default: every 6 hours).
APScheduler is used in blocking mode for simplicity and reliability
in container deployments.
"""

from __future__ import annotations

import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from rich.console import Console

from src.agent.core_agent import CoreAgent
from src.config.settings import settings

logger = logging.getLogger(__name__)
console = Console()


def _run_agent_job() -> None:
    """
    APScheduler job function – instantiates and runs the PM intelligence agent.
    Catches all exceptions to prevent the scheduler from terminating.
    """
    try:
        agent = CoreAgent()
        report_path = agent.run()
        if report_path:
            logger.info("PM Agent job completed. Report: %s", report_path)
        else:
            logger.info("PM Agent job completed. No report generated (not enough data yet).")
    except Exception as exc:
        logger.exception("PM Agent job raised an unhandled exception: %s", exc)


class AgentScheduler:
    """
    Wraps APScheduler to run the PM Intelligence Agent on a recurring schedule.

    Args:
        interval_hours: How often to run the agent (in hours).
    """

    def __init__(self, interval_hours: Optional[int] = None) -> None:
        self._interval_hours = interval_hours or settings.schedule_interval_hours
        self._scheduler = BlockingScheduler(timezone="UTC")
        self._register_signal_handlers()

    def start(self) -> None:
        """
        Start the scheduler loop.
        Runs the agent immediately on startup, then every N hours.
        """
        trigger = IntervalTrigger(hours=self._interval_hours)
        self._scheduler.add_job(
            func=_run_agent_job,
            trigger=trigger,
            id="pm_intelligence_agent",
            name="PM Intelligence Agent",
            next_run_time=datetime.now(timezone.utc),  # Run immediately on startup
            max_instances=1,   # Prevent overlapping runs
            coalesce=True,     # Skip missed runs instead of queuing them
        )

        console.print(
            f"\n[bold green]PM Intelligence Agent scheduler started[/bold green]\n"
            f"  Interval: every [bold]{self._interval_hours}[/bold] hours\n"
            f"  Next run:  [bold]now[/bold]\n"
            f"  Press Ctrl+C to stop\n"
        )
        logger.info("PM Scheduler started. Agent will run every %d hours.", self._interval_hours)

        self._scheduler.start()

    def _register_signal_handlers(self) -> None:
        """Gracefully shut down the scheduler on SIGINT/SIGTERM."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._shutdown)

    def _shutdown(self, signum, frame) -> None:
        logger.info("Received signal %d – shutting down PM scheduler", signum)
        console.print("\n[yellow]Shutting down PM Intelligence Agent...[/yellow]")
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        sys.exit(0)
