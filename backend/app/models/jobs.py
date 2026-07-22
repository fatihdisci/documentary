"""Render job records.

Jobs are persisted to disk as they progress, so the render history survives a
restart and an interrupted render can be detected rather than appearing to have
vanished.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import Field

from app.models.base import CamelModel
from app.models.enums import JobPhase, JobStatus, QualityPreset


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobArtifact(CamelModel):
    kind: str
    filename: str
    size_bytes: int = 0
    #: Download URL, relative to the API root.
    url: str = ""


class RenderJob(CamelModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    project_slug: str
    status: JobStatus = JobStatus.QUEUED
    phase: JobPhase = JobPhase.VALIDATE
    quality: QualityPreset = QualityPreset.YOUTUBE_HQ

    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str = "Sırada"

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    #: The process that owns this job. Used to detect interrupted renders after
    #: a restart: a running job whose pid is gone was killed, not completed.
    pid: int | None = None

    output_file: str | None = None
    artifacts: list[JobArtifact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    #: Structured failure information, mirroring the ErrorPayload shape.
    error_code: str | None = None
    error_message: str | None = None
    error_details: str | None = None
    error_suggestion: str | None = None
    log_file: str | None = None

    #: Timing snapshot, so history entries stay meaningful after edits.
    total_duration_seconds: float = 0.0
    scenes_rendered: int = 0
    scenes_reused: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
        }

    @property
    def is_active(self) -> bool:
        return self.status in {JobStatus.QUEUED, JobStatus.RUNNING}

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or _now()
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def estimated_remaining_seconds(self) -> float | None:
        """Linear estimate from elapsed time and progress.

        Deliberately simple and only offered once enough progress exists for the
        number to mean anything.
        """
        if self.status is not JobStatus.RUNNING or self.progress < 0.05:
            return None
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return None
        return max(0.0, elapsed / self.progress - elapsed)


class JobEvent(CamelModel):
    """One server-sent progress update."""

    job_id: str
    status: JobStatus
    phase: JobPhase
    progress: float
    message: str
    elapsed_seconds: float
    estimated_remaining_seconds: float | None = None
    #: Present on the final event of a failed job.
    error_code: str | None = None
    error_message: str | None = None
    error_suggestion: str | None = None
