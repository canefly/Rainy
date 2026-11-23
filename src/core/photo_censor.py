"""
Local, offline sensitivity check for script photos (gore / bodies / crime scenes).

A small CLIP model runs on-device through onnxruntime and scores each photo against a list
of plain-English descriptions (zero-shot), so nothing ever leaves the machine. Verdicts are
cached in SQLite per file, and a manual per-photo override always wins over the model.
"""
import os
import json
import time
import shutil
import hashlib
import threading
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, QSize, QThreadPool, Qt, Signal
from PySide6.QtGui import QImage, QImageReader

from . import app_paths, net_tls
from .database import db
from .logger import get_logger

logger = get_logger("PhotoCensor")

MODEL_REPO = "Xenova/clip-vit-base-patch32"
MODEL_VISION_FILE = "onnx/vision_model_quantized.onnx"
MODEL_TEXT_FILE = "onnx/text_model_quantized.onnx"
MODEL_TOKENIZER_FILE = "tokenizer.json"
MODEL_FILES = (MODEL_VISION_FILE, MODEL_TEXT_FILE, MODEL_TOKENIZER_FILE)
MODEL_NAME = "CLIP ViT-B/32 (quantized)"
# Pinned to one upstream revision, with size + SHA-256 per file, so a download is either
# byte-for-byte the model this app was tested with or it is rejected.
MODEL_REVISION = "6b7c523703ae26e39be4b03774966f8571c7f523"
MODEL_MANIFEST = (
    (MODEL_VISION_FILE, 89117001, "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"),
    (MODEL_TEXT_FILE, 64504507, "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a"),
    (MODEL_TOKENIZER_FILE, 2224119, "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd"),
)
MODEL_TOTAL_BYTES = sum(size for _, size, _ in MODEL_MANIFEST)
MODEL_SIZE_DISPLAY = "about 155 MB"
MODEL_DIR = os.environ.get("KNOT_CENSOR_MODEL_DIR") or os.path.join(app_paths.models_dir(), "photo_censor")

# What counts as sensitive is just text: edit these lists to retune, no retraining involved.
SENSITIVE_PROMPTS = [
    "a graphic photo of blood and gore",
    "a photo of a dead body",
    "a photo of a corpse at a crime scene",
    "a photo of a bloody crime scene",
    "a photo of a severe bloody injury or open wound",
    "an autopsy photo",
    "a photo of a mutilated body",
    "a photo of explicit nudity",
]
NEUTRAL_PROMPTS = [
    "a portrait photo of a person",
    "a police mugshot",
    "a photo of people talking",
    "a family photo",
    "a photo of a house or building",
    "a photo of a street or road",
    "a photo of a car",
    "a landscape photo",
    "a screenshot of a document or text",
    "a screenshot of a website or social media post",
    "a map",
    "a photo of a courtroom",
    "a photo of police officers",
    "a photo of an everyday object",
    "a logo or icon",
    "a drawing, cartoon or illustration",
    "an abstract pattern, texture or plain colored background",
    "a group photo of people",
    "a selfie",
    "a photo of a room interior",
    "a photo of a gun or knife laid out as evidence",
    "a newspaper clipping",
    "a photo of an animal",
    "a photo of food",
    "a surveillance camera still",
]
DEFAULT_THRESHOLD = 0.5
# Sensitivity presets shown in Preferences: (setting value, label, score needed to censor).
SENSITIVITY_LEVELS = (
    ("relaxed", "Relaxed (fewer false alarms)", 0.65),
    ("balanced", "Balanced", 0.5),
    ("strict", "Strict (censor when in doubt)", 0.35),
)

# First-launch auto-download bookkeeping (setting "photo_censor_autodownload").
AUTODL_PENDING = ""           # never finished: try (again) on launch
AUTODL_DONE = "done"
AUTODL_DECLINED = "declined"  # user cancelled or deleted the model: never auto-download again

DOWNLOAD_OK = "ok"
DOWNLOAD_CANCELLED = "cancelled"
DOWNLOAD_FAILED = "failed"

MANUAL_CENSOR = "censor"
MANUAL_SAFE = "safe"

_INPUT_SIZE = 224
_CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
_CLIP_STD = (0.26862954, 0.26130258, 0.27577711)
_MODEL_TAG = hashlib.sha1(
    json.dumps([MODEL_REPO, MODEL_VISION_FILE, SENSITIVE_PROMPTS, NEUTRAL_PROMPTS]).encode("utf-8")
).hexdigest()[:12]


def _model_file(name: str) -> str:
    return os.path.join(MODEL_DIR, *name.split("/"))


def model_ready() -> bool:
    """All model files present at their exact expected size (a truncated file doesn't count)."""
    try:
        return all(os.path.getsize(_model_file(name)) == size for name, size, _ in MODEL_MANIFEST)
    except OSError:
        return False


def model_disk_bytes() -> int:
    total = 0
    for root, _, files in os.walk(MODEL_DIR):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.0f} MB"


def _pick_embedding(session, outputs):
    """The pooled embedding output of a CLIP ONNX export, whatever the exporter named it."""
    for meta, value in zip(session.get_outputs(), outputs):
        if meta.name.endswith("embeds"):
            return value
    for value in outputs:
        if getattr(value, "ndim", 0) == 2:
            return value
    raise RuntimeError("censor model has no embedding output")


class ClipSensitivityScorer:
    """Zero-shot scorer: probability mass a photo puts on the sensitive descriptions (0..1)."""

    def __init__(self):
        import numpy as np
        import onnxruntime as ort

        self._np = np
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        self._opts = opts
        self._ort = ort
        self._vision = ort.InferenceSession(_model_file(MODEL_VISION_FILE), opts, providers=["CPUExecutionProvider"])
        self._vision_input = self._vision.get_inputs()[0].name
        self._text_embeds = self._load_text_embeds()
        self._n_sensitive = len(SENSITIVE_PROMPTS)

    def _load_text_embeds(self):
        np = self._np
        cache_path = os.path.join(MODEL_DIR, f"text_embeds_{_MODEL_TAG}.npy")
        if os.path.isfile(cache_path):
            try:
                cached = np.load(cache_path)
                if cached.shape[0] == len(SENSITIVE_PROMPTS) + len(NEUTRAL_PROMPTS):
                    return cached
            except Exception:
                pass

        from tokenizers import Tokenizer
        tokenizer = Tokenizer.from_file(_model_file(MODEL_TOKENIZER_FILE))
        tokenizer.no_padding()
        session = self._ort.InferenceSession(_model_file(MODEL_TEXT_FILE), self._opts, providers=["CPUExecutionProvider"])
        input_names = {i.name for i in session.get_inputs()}
        rows = []
        for prompt in SENSITIVE_PROMPTS + NEUTRAL_PROMPTS:
            ids = np.array([tokenizer.encode(prompt).ids], dtype=np.int64)
            feed = {"input_ids": ids}
            if "attention_mask" in input_names:
                feed["attention_mask"] = np.ones_like(ids)
            emb = _pick_embedding(session, session.run(None, feed))[0].astype(np.float32)
            rows.append(emb / (np.linalg.norm(emb) + 1e-12))
        embeds = np.stack(rows)
        try:
            for stale in os.listdir(MODEL_DIR):
                if stale.startswith("text_embeds_") and stale.endswith(".npy"):
                    os.remove(os.path.join(MODEL_DIR, stale))
            np.save(cache_path, embeds)
        except Exception:
            pass
        return embeds

    def _pixels(self, path: str):
        np = self._np
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and min(size.width(), size.height()) > _INPUT_SIZE:
            scale = _INPUT_SIZE / min(size.width(), size.height())
            reader.setScaledSize(QSize(
                max(_INPUT_SIZE, round(size.width() * scale)),
                max(_INPUT_SIZE, round(size.height() * scale)),
            ))
        img = reader.read()
        if img.isNull():
            raise ValueError(f"unreadable image: {path}")
        if min(img.width(), img.height()) != _INPUT_SIZE:
            img = img.scaled(_INPUT_SIZE, _INPUT_SIZE, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        img = img.copy(
            (img.width() - _INPUT_SIZE) // 2, (img.height() - _INPUT_SIZE) // 2, _INPUT_SIZE, _INPUT_SIZE
        ).convertToFormat(QImage.Format_RGB888)
        raw = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes())
        rgb = raw.reshape(_INPUT_SIZE, img.bytesPerLine())[:, : _INPUT_SIZE * 3].reshape(_INPUT_SIZE, _INPUT_SIZE, 3)
        arr = rgb.astype(np.float32) / 255.0
        arr = (arr - np.array(_CLIP_MEAN, dtype=np.float32)) / np.array(_CLIP_STD, dtype=np.float32)
        return arr.transpose(2, 0, 1)[None, ...]

    def score(self, path: str) -> float:
        np = self._np
        emb = _pick_embedding(self._vision, self._vision.run(None, {self._vision_input: self._pixels(path)}))[0]
        emb = emb.astype(np.float32)
        emb /= (np.linalg.norm(emb) + 1e-12)
        logits = 100.0 * (self._text_embeds @ emb)
        probs = np.exp(logits - logits.max())
        probs /= probs.sum()
        return float(probs[: self._n_sensitive].sum())


class _TaskSignals(QObject):
    scored = Signal(str, str, object, float)   # (key, path, mtime_ns, score; score < 0 = failed)
    download_progress = Signal(int, str)       # (percent, human-readable status)
    download_finished = Signal(str, str)       # (DOWNLOAD_* outcome, message)


class _ScoreTask(QRunnable):
    def __init__(self, service: "PhotoCensorService", key: str, path: str):
        super().__init__()
        self._service, self._key, self._path = service, key, path

    def run(self):
        score, mtime_ns = -1.0, 0
        try:
            mtime_ns = os.stat(self._path).st_mtime_ns
            score = float(self._service._get_scorer()(self._path))
        except Exception as e:
            logger.warning("Could not check photo '%s': %s", self._path, e)
        self._service._signals.scored.emit(self._key, self._path, mtime_ns, score)


class _DownloadCancelled(Exception):
    pass


class _DownloadTask(QRunnable):
    """Fetches the model files with live byte progress. Cancellable between chunks, resumes a
    partial file left by a cancel/crash/quit, and verifies every file before accepting it."""

    CHUNK = 1 << 16
    TIMEOUT_S = 20

    def __init__(self, signals: _TaskSignals, cancel: threading.Event):
        super().__init__()
        self._signals = signals
        self._cancel = cancel
        self._done_bytes = 0
        self._last_emit = 0.0

    def _report(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last_emit < 0.2:
            return
        self._last_emit = now
        pct = min(99, int(self._done_bytes * 100 / MODEL_TOTAL_BYTES))
        self._signals.download_progress.emit(pct, f"{_mb(self._done_bytes)} of {_mb(MODEL_TOTAL_BYTES)}")

    def _open(self, name: str, have: int):
        url = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{name}"
        headers = {"User-Agent": "Rainy"}
        if have:
            headers["Range"] = f"bytes={have}-"
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=self.TIMEOUT_S,
                                      context=net_tls.ssl_context())

    def _fetch(self, name: str, size: int, sha256: str):
        dest = _model_file(name)
        part = dest + ".part"
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if have > size:
            os.remove(part)
            have = 0

        if have < size:
            with self._open(name, have) as resp:
                if have and resp.status != 206:      # server ignored the resume request
                    have = 0
                self._done_bytes += have
                with open(part, "ab" if have else "wb") as out:
                    while True:
                        if self._cancel.is_set():
                            raise _DownloadCancelled()
                        chunk = resp.read(self.CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        self._done_bytes += len(chunk)
                        self._report()
        else:
            self._done_bytes += have

        digest = hashlib.sha256()
        with open(part, "rb") as f:
            while block := f.read(1 << 20):
                if self._cancel.is_set():
                    raise _DownloadCancelled()
                digest.update(block)
        if os.path.getsize(part) != size or digest.hexdigest() != sha256:
            self._done_bytes -= os.path.getsize(part)
            os.remove(part)
            raise RuntimeError("The downloaded model file was incomplete or corrupted. Please try again.")
        os.replace(part, dest)

    def run(self):
        outcome, message = DOWNLOAD_FAILED, ""
        try:
            os.makedirs(MODEL_DIR, exist_ok=True)
            todo = []
            for name, size, sha256 in MODEL_MANIFEST:
                dest = _model_file(name)
                if os.path.isfile(dest) and os.path.getsize(dest) == size:
                    self._done_bytes += size
                else:
                    todo.append((name, size, sha256))
            needed = sum(size for _, size, _ in todo)
            if shutil.disk_usage(MODEL_DIR).free < needed + 50_000_000:
                raise RuntimeError(f"Not enough free disk space for the censor model ({_mb(needed)} needed).")
            self._report(force=True)
            for name, size, sha256 in todo:
                self._fetch(name, size, sha256)
            outcome, message = DOWNLOAD_OK, "Automatic photo censoring is ready."
        except _DownloadCancelled:
            outcome, message = DOWNLOAD_CANCELLED, "Download cancelled."
        except urllib.error.URLError as e:
            logger.warning("Censor model download failed (network): %s", e)
            message = "Couldn't reach the download server. Check your internet connection."
        except Exception as e:
            logger.error("Censor model download failed: %s", e)
            message = str(e) or "Download failed."
        self._signals.download_finished.emit(outcome, message)


class PhotoCensorService(QObject):
    """Decides which script photos are hidden behind a blur, and remembers why."""

    photo_changed = Signal(str)           # path whose hidden state may have changed
    all_photos_changed = Signal()         # global change (auto toggle, model arrived)
    model_download_progress = Signal(int, str)      # (percent, "62 MB of 156 MB")
    model_download_finished = Signal(str, str)      # (DOWNLOAD_OK / _CANCELLED / _FAILED, message)
    model_state_changed = Signal()                  # downloading / ready / missing changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._records: Optional[Dict[str, Dict]] = None    # key -> {mtime_ns, score, tag, manual}
        self._pending: set = set()
        self._failed: set = set()
        self._revealed: set = set()        # session only: flagged photos the user chose to look at
        self._force_hidden: set = set()    # session only: unflagged photos the user chose to hide
        self._scorer: Optional[Callable[[str], float]] = None
        self._scorer_lock = threading.Lock()
        self._downloading = False
        self._download_cancel = threading.Event()
        self._download_progress = (0, "")
        self._quit_hooked = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _TaskSignals(self)
        self._signals.scored.connect(self._on_scored)
        self._signals.download_progress.connect(self._on_download_progress)
        self._signals.download_finished.connect(self._on_download_finished)

    # --- configuration ---
    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def model_ready(self) -> bool:
        return self._scorer is not None or model_ready()

    def auto_enabled(self) -> bool:
        return db.get_setting("photo_censor_auto", "1") == "1"

    def set_auto_enabled(self, enabled: bool):
        db.set_setting("photo_censor_auto", "1" if enabled else "0")
        self.all_photos_changed.emit()

    def sensitivity(self) -> str:
        level = db.get_setting("photo_censor_sensitivity", "balanced")
        return level if any(level == key for key, _, _ in SENSITIVITY_LEVELS) else "balanced"

    def set_sensitivity(self, level: str):
        db.set_setting("photo_censor_sensitivity", level)
        self.all_photos_changed.emit()

    def threshold(self) -> float:
        level = self.sensitivity()
        return next((t for key, _, t in SENSITIVITY_LEVELS if key == level), DEFAULT_THRESHOLD)

    def set_scorer(self, scorer: Optional[Callable[[str], float]]):
        """Swap in a scoring function (used by tests; None returns to the real model)."""
        with self._scorer_lock:
            self._scorer = scorer

    def _get_scorer(self) -> Callable[[str], float]:
        with self._scorer_lock:
            if self._scorer is None:
                self._scorer = ClipSensitivityScorer().score
            return self._scorer

    # --- persistence ---
    def _load(self) -> Dict[str, Dict]:
        if self._records is None:
            self._records = {}
            with db._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS photo_sensitivity (
                        file_path TEXT PRIMARY KEY,
                        mtime_ns INTEGER,
                        score REAL,
                        model_tag TEXT,
                        manual TEXT
                    )
                """)
                conn.commit()
                for row in conn.execute("SELECT file_path, mtime_ns, score, model_tag, manual FROM photo_sensitivity"):
                    self._records[row["file_path"]] = {
                        "mtime_ns": row["mtime_ns"], "score": row["score"],
                        "tag": row["model_tag"], "manual": row["manual"],
                    }
        return self._records

    def _save(self, key: str):
        rec = self._load()[key]
        with db._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO photo_sensitivity (file_path, mtime_ns, score, model_tag, manual) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, rec.get("mtime_ns"), rec.get("score"), rec.get("tag"), rec.get("manual")),
            )
            conn.commit()

    def _record(self, key: str) -> Dict:
        return self._load().setdefault(key, {"mtime_ns": None, "score": None, "tag": None, "manual": None})

    # --- verdicts ---
    def manual(self, path: str) -> Optional[str]:
        return self._load().get(self._key(path), {}).get("manual")

    def set_manual(self, path: str, value: Optional[str]):
        """value: MANUAL_CENSOR, MANUAL_SAFE, or None to go back to the automatic verdict."""
        key = self._key(path)
        self._record(key)["manual"] = value
        self._save(key)
        self._revealed.discard(key)
        self._force_hidden.discard(key)
        self.photo_changed.emit(path)

    def auto_score(self, path: str) -> Optional[float]:
        """Cached model score for this exact file version, or None if not checked yet."""
        key = self._key(path)
        rec = self._load().get(key)
        if not rec or rec.get("score") is None or rec.get("tag") != _MODEL_TAG:
            return None
        try:
            if os.stat(path).st_mtime_ns != rec.get("mtime_ns"):
                return None
        except OSError:
            return None
        return float(rec["score"])

    def is_pending(self, path: str) -> bool:
        return self._key(path) in self._pending

    def is_flagged(self, path: str) -> bool:
        """True if this photo should be censored by default. Unchecked photos count as
        flagged while the model is working on them, so nothing graphic flashes on screen."""
        manual = self.manual(path)
        if manual == MANUAL_CENSOR:
            return True
        if manual == MANUAL_SAFE:
            return False
        if not self.auto_enabled():
            return False
        # A verdict already on file still applies even if the model was deleted since.
        score = self.auto_score(path)
        if score is not None:
            return score >= self.threshold()
        if not self.model_ready():
            return False
        return self._queue(path)

    def _queue(self, path: str) -> bool:
        key = self._key(path)
        if key in self._failed:
            return False
        if key not in self._pending:
            self._pending.add(key)
            self._pool.start(_ScoreTask(self, key, path))
        return True

    def rescan(self, path: str):
        key = self._key(path)
        self._failed.discard(key)
        self._record(key)["score"] = None
        if self.model_ready() and self.auto_enabled():
            self._queue(path)
        self.photo_changed.emit(path)

    def _on_scored(self, key: str, path: str, mtime_ns, score: float):
        self._pending.discard(key)
        if score < 0:
            self._failed.add(key)
        else:
            rec = self._record(key)
            rec.update(mtime_ns=int(mtime_ns), score=float(score), tag=_MODEL_TAG)
            self._save(key)
        self.photo_changed.emit(path)

    # --- what the viewer actually shows ---
    def is_hidden(self, path: str) -> bool:
        key = self._key(path)
        if key in self._force_hidden:
            return True
        return self.is_flagged(path) and key not in self._revealed

    def toggle_hidden(self, path: str):
        """The eye button: show a censored photo, or hide a visible one, for this session."""
        key = self._key(path)
        if self.is_hidden(path):
            self._force_hidden.discard(key)
            self._revealed.add(key)
        else:
            self._revealed.discard(key)
            if not self.is_flagged(path):
                self._force_hidden.add(key)
        self.photo_changed.emit(path)

    # --- model download / removal ---
    def is_downloading(self) -> bool:
        return self._downloading

    def download_progress(self) -> Tuple[int, str]:
        return self._download_progress

    def download_model(self) -> bool:
        """Starts (or resumes) the model download. Returns False if there was nothing to start."""
        if self._downloading or model_ready():
            return False
        self._downloading = True
        self._download_cancel.clear()
        self._download_progress = (0, "Starting…")
        app = QCoreApplication.instance()
        if app is not None and not self._quit_hooked:
            # A running download would otherwise keep the process alive after the window closes.
            app.aboutToQuit.connect(self._on_app_quit)
            self._quit_hooked = True
        QThreadPool.globalInstance().start(_DownloadTask(self._signals, self._download_cancel))
        self.model_state_changed.emit()
        return True

    def cancel_download(self):
        """User-initiated cancel: stops the download and never auto-downloads again."""
        if self._downloading:
            db.set_setting("photo_censor_autodownload", AUTODL_DECLINED)
            self._download_cancel.set()

    def _on_app_quit(self):
        # Not a user decision: keep the partial file so the next launch resumes it.
        self._download_cancel.set()

    def _on_download_progress(self, pct: int, text: str):
        self._download_progress = (pct, text)
        self.model_download_progress.emit(pct, text)

    def _on_download_finished(self, outcome: str, message: str):
        self._downloading = False
        if outcome == DOWNLOAD_OK:
            db.set_setting("photo_censor_autodownload", AUTODL_DONE)
        self.model_download_finished.emit(outcome, message)
        self.model_state_changed.emit()
        if outcome == DOWNLOAD_OK:
            self.all_photos_changed.emit()

    def auto_download_pending(self) -> bool:
        """True when maybe_auto_download() would start a download right now. Starts nothing, so the first-run
        window can list the step before it begins."""
        if os.environ.get("KNOT_NO_AUTO_DOWNLOAD"):
            return False
        if self._downloading or model_ready() or not self.auto_enabled():
            return False
        return db.get_setting("photo_censor_autodownload", AUTODL_PENDING) != AUTODL_DECLINED

    def maybe_auto_download(self) -> bool:
        """First-launch behaviour: fetch the model unprompted, once. Skipped if the user turned
        auto-censor off, already has the model, or previously cancelled / deleted it."""
        if not self.auto_download_pending():
            return False
        return self.download_model()

    def delete_model(self) -> bool:
        """Removes the model from disk (and any partial download). Manual censors and verdicts
        already on file are kept; photos not yet checked stop being auto-censored."""
        if self._downloading:
            return False
        self._pool.waitForDone()          # let an in-flight check finish before unloading
        with self._scorer_lock:
            self._scorer = None           # releases the ONNX file handles (Windows locks them)
        import gc
        gc.collect()
        try:
            if os.path.isdir(MODEL_DIR):
                shutil.rmtree(MODEL_DIR)
        except OSError as e:
            logger.error("Could not delete censor model: %s", e)
            return False
        db.set_setting("photo_censor_autodownload", AUTODL_DECLINED)
        self.model_state_changed.emit()
        self.all_photos_changed.emit()
        return True


photo_censor = PhotoCensorService()
