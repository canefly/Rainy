import os
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Tuple
from PySide6.QtCore import QObject, Signal

from .database import db
from .ffmpeg_encoder import (
    TranscodeConfig, TranscodeProgress, TranscodeWorker,
    get_ffmpeg_status, is_file_locked
)
from .logger import get_logger

logger = get_logger("RenderQueue")


@dataclass
class RenderJob:
    id: str
    source_path: str
    config: TranscodeConfig
    status: str = "QUEUED"  # QUEUED | RUNNING | DONE | FAILED | CANCELLED | INTERRUPTED
    progress: float = 0.0
    speed: float = 0.0
    eta_seconds: float = 0.0
    size_before: int = 0
    size_after: int = 0
    output_path: str = ""
    error_msg: str = ""
    summary_msg: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    sort_order: int = 0

    def to_db_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_path": self.source_path,
            "config_json": json.dumps(asdict(self.config)),
            "status": self.status,
            "progress": self.progress,
            "speed": self.speed,
            "eta_seconds": self.eta_seconds,
            "size_before": self.size_before,
            "size_after": self.size_after,
            "output_path": self.output_path,
            "error_msg": self.error_msg,
            "summary_msg": self.summary_msg,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sort_order": self.sort_order,
        }

    @classmethod
    def from_db_dict(cls, d: Dict[str, Any]) -> "RenderJob":
        cfg_raw = {}
        try:
            cfg_raw = json.loads(d.get("config_json") or "{}")
        except Exception:
            cfg_raw = {}
        cfg = TranscodeConfig(
            target_fps=float(cfg_raw.get("target_fps", 0.0)),
            encoder=str(cfg_raw.get("encoder", "auto")),
            crf=int(cfg_raw.get("crf", 18)),
            preset=str(cfg_raw.get("preset", "p6")),
            max_height=cfg_raw.get("max_height"),
            deinterlace=str(cfg_raw.get("deinterlace", "auto")),
            audio_mode=str(cfg_raw.get("audio_mode", "copy")),
            replace_original=bool(cfg_raw.get("replace_original", True)),
            custom_args=list(cfg_raw.get("custom_args") or []),
        )
        return cls(
            id=str(d["id"]),
            source_path=str(d["source_path"]),
            config=cfg,
            status=str(d.get("status", "QUEUED")),
            progress=float(d.get("progress", 0.0)),
            speed=float(d.get("speed", 0.0)),
            eta_seconds=float(d.get("eta_seconds", 0.0)),
            size_before=int(d.get("size_before", 0)),
            size_after=int(d.get("size_after", 0)),
            output_path=str(d.get("output_path", "")),
            error_msg=str(d.get("error_msg", "")),
            summary_msg=str(d.get("summary_msg", "")),
            created_at=float(d.get("created_at", time.time())),
            updated_at=float(d.get("updated_at", time.time())),
            sort_order=int(d.get("sort_order", 0)),
        )


class RenderQueueService(QObject):
    """
    Application-level sequential FFmpeg render queue.
    Runs 1 job at a time, persists jobs to SQLite, and survives modal closing.
    """
    queue_changed = Signal()
    job_added = Signal(object)                     # RenderJob
    job_started = Signal(object)                   # RenderJob
    job_progress = Signal(object, float, str)      # (RenderJob, percent, status_text)
    job_finished = Signal(object, bool, str)       # (RenderJob, success, final_path_or_error)
    batch_finished = Signal(int, int)              # (done_count, failed_count)

    _instance: Optional["RenderQueueService"] = None

    @classmethod
    def instance(cls) -> "RenderQueueService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.jobs: List[RenderJob] = []
        self._active_worker: Optional[TranscodeWorker] = None
        self._active_job: Optional[RenderJob] = None
        self.pause_after_current: bool = False
        self._batch_done_count: int = 0
        self._batch_fail_count: int = 0
        self._load_persisted_jobs()

    def _load_persisted_jobs(self):
        try:
            rows = db.get_render_jobs()
            self.jobs.clear()
            for r in rows:
                job = RenderJob.from_db_dict(r)
                # Jobs left RUNNING or QUEUED from a previous crash/session become INTERRUPTED
                # so nothing auto-starts unexpectedly on app boot, while preserving user's ability to Retry.
                if job.status in ("RUNNING", "QUEUED"):
                    job.status = "INTERRUPTED"
                    job.error_msg = "Interrupted when application closed. Click Retry to resume."
                    job.updated_at = time.time()
                    db.save_render_job(job.to_db_dict())
                self.jobs.append(job)
        except Exception as e:
            logger.warning(f"Failed to load persisted render queue: {e}")

    def get_job_for_path(self, file_path: str) -> Optional[RenderJob]:
        if not file_path:
            return None
        norm = os.path.normcase(os.path.abspath(file_path))
        # Prioritize active/queued jobs, then most recent finished job
        for job in self.jobs:
            if os.path.normcase(os.path.abspath(job.source_path)) == norm and job.status in ("RUNNING", "QUEUED"):
                return job
        for job in reversed(self.jobs):
            if os.path.normcase(os.path.abspath(job.source_path)) == norm:
                return job
        return None

    def is_path_queued_or_running(self, file_path: str) -> bool:
        if not file_path:
            return False
        norm = os.path.normcase(os.path.abspath(file_path))
        return any(
            os.path.normcase(os.path.abspath(j.source_path)) == norm and j.status in ("QUEUED", "RUNNING")
            for j in self.jobs
        )

    def enqueue(self, source_paths: List[str], config: TranscodeConfig) -> Tuple[List[RenderJob], List[Tuple[str, str]]]:
        """
        Enqueues one or more files with the given TranscodeConfig.
        Returns (added_jobs, skipped_reasons [(path, reason)]).
        """
        added: List[RenderJob] = []
        skipped: List[Tuple[str, str]] = []

        max_order = max((j.sort_order for j in self.jobs), default=0)

        for raw_path in source_paths:
            if not raw_path or not os.path.isfile(raw_path):
                skipped.append((raw_path, "File does not exist on disk"))
                continue

            abs_path = os.path.abspath(raw_path)

            if self.is_path_queued_or_running(abs_path):
                skipped.append((abs_path, "Already queued or rendering"))
                continue

            if is_file_locked(abs_path):
                skipped.append((abs_path, "File is locked by another process (e.g. Premiere Pro)"))
                continue

            try:
                sz_before = os.path.getsize(abs_path)
            except Exception:
                sz_before = 0

            max_order += 1
            # Copy config so per-job tweaks don't mutate siblings
            job_cfg = TranscodeConfig(
                target_fps=config.target_fps,
                encoder=config.encoder,
                crf=config.crf,
                preset=config.preset,
                max_height=config.max_height,
                deinterlace=config.deinterlace,
                audio_mode=config.audio_mode,
                replace_original=config.replace_original,
                custom_args=list(config.custom_args or []),
            )
            job = RenderJob(
                id=str(uuid.uuid4()),
                source_path=abs_path,
                config=job_cfg,
                status="QUEUED",
                size_before=sz_before,
                sort_order=max_order,
            )
            self.jobs.append(job)
            db.save_render_job(job.to_db_dict())
            added.append(job)
            self.job_added.emit(job)

        if added:
            self.queue_changed.emit()
            if not self.pause_after_current and self._active_worker is None:
                self._start_next()

        return added, skipped

    def _start_next(self):
        if self._active_worker is not None:
            return
        if self.pause_after_current:
            return

        next_job = next((j for j in self.jobs if j.status == "QUEUED"), None)
        if next_job is None:
            if self._batch_done_count > 0 or self._batch_fail_count > 0:
                done_c, fail_c = self._batch_done_count, self._batch_fail_count
                self._batch_done_count = 0
                self._batch_fail_count = 0
                self.batch_finished.emit(done_c, fail_c)
            return

        # Re-check file lock right before starting
        if is_file_locked(next_job.source_path):
            next_job.status = "FAILED"
            next_job.error_msg = "File is currently locked by Premiere Pro or another player."
            next_job.updated_at = time.time()
            db.save_render_job(next_job.to_db_dict())
            self._batch_fail_count += 1
            self.job_finished.emit(next_job, False, next_job.error_msg)
            self.queue_changed.emit()
            self._start_next()
            return

        self._active_job = next_job
        next_job.status = "RUNNING"
        next_job.progress = 0.0
        next_job.error_msg = ""
        next_job.updated_at = time.time()
        db.save_render_job(next_job.to_db_dict())

        self.job_started.emit(next_job)
        self.queue_changed.emit()

        worker = TranscodeWorker(next_job.source_path, next_job.config, parent=self)
        self._active_worker = worker
        worker.progress.connect(self._on_worker_progress)
        worker.finished.connect(self._on_worker_finished)
        worker.cancelled.connect(self._on_worker_cancelled)
        worker.start()

    def _on_worker_progress(self, percent: float, status_txt: str, prog: TranscodeProgress):
        job = self._active_job
        if not job:
            return
        job.progress = float(percent)
        job.speed = float(prog.speed)
        job.eta_seconds = float(prog.eta_seconds)
        job.updated_at = time.time()
        self.job_progress.emit(job, percent, status_txt)

    def _on_worker_finished(self, success: bool, final_path_or_err: str, summary_msg: str):
        job = self._active_job
        self._active_worker = None
        self._active_job = None

        if job:
            job.updated_at = time.time()
            if success:
                job.status = "DONE"
                job.progress = 100.0
                job.eta_seconds = 0.0
                job.output_path = final_path_or_err
                job.summary_msg = summary_msg
                try:
                    if os.path.isfile(final_path_or_err):
                        job.size_after = os.path.getsize(final_path_or_err)
                except Exception:
                    pass
                self._batch_done_count += 1
            else:
                job.status = "FAILED"
                job.error_msg = final_path_or_err
                job.summary_msg = summary_msg
                self._batch_fail_count += 1

            db.save_render_job(job.to_db_dict())
            self.job_finished.emit(job, success, final_path_or_err)
            self.queue_changed.emit()

        self._start_next()

    def _on_worker_cancelled(self):
        job = self._active_job
        self._active_worker = None
        self._active_job = None

        if job:
            job.status = "CANCELLED"
            job.eta_seconds = 0.0
            job.summary_msg = "Cancelled by user"
            job.updated_at = time.time()
            db.save_render_job(job.to_db_dict())
            self.job_finished.emit(job, False, "Cancelled by user")
            self.queue_changed.emit()

        self._start_next()

    def cancel_job(self, job_id: str):
        if self._active_job and self._active_job.id == job_id:
            if self._active_worker and self._active_worker.isRunning():
                self._active_worker.cancel()
                self._active_worker.wait(1500)
            return

        for job in self.jobs:
            if job.id == job_id and job.status == "QUEUED":
                job.status = "CANCELLED"
                job.summary_msg = "Cancelled before start"
                job.updated_at = time.time()
                db.save_render_job(job.to_db_dict())
                self.queue_changed.emit()
                break

    def remove_job(self, job_id: str):
        if self._active_job and self._active_job.id == job_id:
            self.cancel_job(job_id)
        self.jobs = [j for j in self.jobs if j.id != job_id]
        db.delete_render_job(job_id)
        self.queue_changed.emit()

    def retry_job(self, job_id: str):
        for job in self.jobs:
            if job.id == job_id and job.status in ("FAILED", "CANCELLED", "INTERRUPTED"):
                job.status = "QUEUED"
                job.progress = 0.0
                job.error_msg = ""
                job.summary_msg = ""
                job.updated_at = time.time()
                db.save_render_job(job.to_db_dict())
                self.queue_changed.emit()
                if not self.pause_after_current and self._active_worker is None:
                    self._start_next()
                break

    def retry_all_failed(self):
        any_retried = False
        for job in self.jobs:
            if job.status in ("FAILED", "INTERRUPTED"):
                job.status = "QUEUED"
                job.progress = 0.0
                job.error_msg = ""
                job.summary_msg = ""
                job.updated_at = time.time()
                db.save_render_job(job.to_db_dict())
                any_retried = True
        if any_retried:
            self.queue_changed.emit()
            if not self.pause_after_current and self._active_worker is None:
                self._start_next()

    def move_job(self, job_id: str, direction: int):
        """Moves a QUEUED job up (-1) or down (+1) among queued jobs."""
        queued_indices = [i for i, j in enumerate(self.jobs) if j.status == "QUEUED"]
        curr_idx = next((i for i, j in enumerate(self.jobs) if j.id == job_id and j.status == "QUEUED"), None)
        if curr_idx is None or curr_idx not in queued_indices:
            return
        pos = queued_indices.index(curr_idx)
        target_pos = pos + direction
        if target_pos < 0 or target_pos >= len(queued_indices):
            return
        swap_idx = queued_indices[target_pos]
        self.jobs[curr_idx], self.jobs[swap_idx] = self.jobs[swap_idx], self.jobs[curr_idx]
        for idx, j in enumerate(self.jobs):
            j.sort_order = idx
            db.save_render_job(j.to_db_dict())
        self.queue_changed.emit()

    def clear_finished(self):
        self.jobs = [j for j in self.jobs if j.status in ("QUEUED", "RUNNING")]
        db.clear_finished_render_jobs()
        self.queue_changed.emit()

    def set_pause_after_current(self, paused: bool):
        self.pause_after_current = paused
        self.queue_changed.emit()
        if not paused and self._active_worker is None:
            self._start_next()

    def get_summary_counts(self) -> Dict[str, int]:
        counts = {"queued": 0, "running": 0, "done": 0, "failed": 0, "total": len(self.jobs)}
        for j in self.jobs:
            if j.status == "QUEUED":
                counts["queued"] += 1
            elif j.status == "RUNNING":
                counts["running"] += 1
            elif j.status == "DONE":
                counts["done"] += 1
            elif j.status in ("FAILED", "INTERRUPTED"):
                counts["failed"] += 1
        return counts


render_queue = RenderQueueService.instance()
