"""Scheduled jobs package."""
from __future__ import annotations


class JobError(RuntimeError):
    """Raised when a job hits a material failure that should fail the run
    (non-zero exit) so the scheduler/CI surfaces it (e.g. GitHub email)."""
