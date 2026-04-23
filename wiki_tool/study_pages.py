from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
from typing import Any

from wiki_tool.catalog import DEFAULT_DB
from wiki_tool.markdown import normalize_name
from wiki_tool.study_quality import audit_book_entry
from wiki_tool.study_streams import (
    DEFAULT_BOOK_MANIFEST,
    DEFAULT_DEFINITION_CARDS,
    DEFAULT_READER_STREAM,
    DEFAULT_STUDY_DIR,
    DEFAULT_STUDY_SELECTION,
    DEFAULT_STUDY_SHELF,
    DEFAULT_STUDY_SOURCE_ROOT,
    MATERIALIZED_STATUSES,
    load_jsonl,
    format_chapter_label,
    is_display_quality_card_term,
    normalize_display_title,
    resolve_index_book,
    study_inventory,
    validate_selection,
    validate_shelf,
)


DEFAULT_STUDY_PAGES_WIKI_ROOT = Path("state/wiki_mirror")
DEFAULT_STUDY_PAGES_PROJECT = Path("projects/math_library")
DEFAULT_STUDY_PAGES_STATE = Path("projects/math_library/state/navigation_index.json")
DEFAULT_STUDY_DASHBOARD_PROJECT = Path("projects/study_dashboard")
DEFAULT_STUDY_DASHBOARD_STATE = Path("projects/study_dashboard/state/navigation_index.json")
DEFAULT_STUDY_PAGES_DEFINITIONS = Path("projects/math_library/definitions/README.md")
DEFAULT_STUDY_PAGES_RESULTS = Path("projects/math_library/results/README.md")
DEFAULT_STUDY_PAGES_DEFINITIONS_BY_LETTER = Path("projects/math_library/definitions/by_letter")
DEFAULT_STUDY_PAGES_RESULTS_BY_LETTER = Path("projects/math_library/results/by_letter")
WIKI_REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_NOTE_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
DEFINITION_SOURCE_KINDS = {"definition_heading", "inline_definition", "strict_concept"}
RESULT_SOURCE_KINDS = {"named_theorem", "named_lemma", "named_proposition", "named_corollary"}


def study_dashboard_app_roots() -> dict[str, dict[str, Any]]:
    workspace_root = WIKI_REPO_ROOT.parent
    vox_root = workspace_root / "vox"
    discoflash_root = workspace_root / "discoflash"
    return {
        "vox": {
            "root": vox_root,
            "entrypoint": vox_root / "app" / "main.py",
        },
        "discoflash": {
            "root": discoflash_root,
            "entrypoint": discoflash_root / "app" / "main.py",
        },
    }


def study_dashboard_launch_metadata(selection_key: str) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for app_name, app_info in study_dashboard_app_roots().items():
        root = Path(app_info["root"])
        entrypoint = Path(app_info["entrypoint"])
        available = root.exists() and entrypoint.exists()
        if app_name == "vox":
            command_root = root / "app"
            base = f"cd {shell_quote_path(command_root)} && python3 main.py --study-selection '{selection_key}'"
        else:
            command_root = root
            base = f"cd {shell_quote_path(command_root)} && python3 app/main.py --study-selection '{selection_key}'"
        metadata[app_name] = {
            "available": available,
            "fresh": base if available else None,
            "resume": f"{base} --resume" if available else None,
        }
    return metadata


def shell_quote_path(path: Path) -> str:
    return shlex.quote(str(path))


def study_dashboard_progress_path(app_name: str, app_root: Path) -> Path:
    if app_name == "vox":
        return app_root / ".session_memory" / "reading_progress.json"
    return app_root / ".session_memory" / "definition_matching_progress.json"


def study_dashboard_event_log_path(app_name: str, app_root: Path) -> Path:
    del app_name
    return app_root / ".session_memory" / "study_events.jsonl"


def study_dashboard_completion_path(app_name: str, app_root: Path) -> Path:
    del app_name
    return app_root / ".session_memory" / "study_completion.json"


def study_dashboard_review_path(app_name: str, app_root: Path) -> Path:
    del app_name
    return app_root / ".session_memory" / "study_review.json"


def build_dashboard_progress_overlay(books: list[dict[str, Any]]) -> dict[str, Any]:
    known_keys: set[str] = set()
    for book in books:
        known_keys.add(study_selection_key(str(book["document_id"]), None))
        for chapter in book["chapters"]:
            known_keys.add(study_selection_key(str(book["document_id"]), str(chapter["chapter_id"])))

    selection_progress: dict[str, dict[str, Any]] = {
        key: {
            "vox_progress": idle_vox_progress(),
            "discoflash_progress": idle_discoflash_progress(),
        }
        for key in known_keys
    }
    apps: dict[str, Any] = {}

    for app_name, app_info in study_dashboard_app_roots().items():
        root = Path(app_info["root"])
        entrypoint = Path(app_info["entrypoint"])
        available = root.exists() and entrypoint.exists()
        progress_path = study_dashboard_progress_path(app_name, root)
        app_summary = {
            "available": available,
            "progress_path": str(progress_path),
            "last_selection_key": None,
            "resume_available_count": 0,
            "load_error": None,
        }
        if not available:
            app_summary["status"] = "unavailable"
            empty_progress = unavailable_vox_progress() if app_name == "vox" else unavailable_discoflash_progress()
            for key in selection_progress:
                selection_progress[key][f"{app_name}_progress"] = dict(empty_progress)
            apps[app_name] = app_summary
            continue

        if not progress_path.exists():
            app_summary["status"] = "idle"
            apps[app_name] = app_summary
            continue

        try:
            payload = json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception:
            app_summary["status"] = "idle"
            app_summary["load_error"] = "invalid_json"
            apps[app_name] = app_summary
            continue

        if not isinstance(payload, dict):
            app_summary["status"] = "idle"
            app_summary["load_error"] = "invalid_payload"
            apps[app_name] = app_summary
            continue

        last_selection_key = payload.get("last_selection_key")
        if isinstance(last_selection_key, str) and last_selection_key.strip():
            app_summary["last_selection_key"] = last_selection_key

        if app_name == "vox":
            positions = payload.get("positions")
            if not isinstance(positions, dict):
                positions = {}
            resumable = 0
            for key, position in positions.items():
                if key not in selection_progress or not isinstance(position, dict):
                    continue
                progress = normalize_vox_progress(position, is_last_selection=(key == last_selection_key))
                selection_progress[key]["vox_progress"] = progress
                if progress["status"] == "resume_available":
                    resumable += 1
            app_summary["resume_available_count"] = resumable
            app_summary["status"] = "resume_available" if resumable > 0 else "idle"
        else:
            sessions = payload.get("sessions")
            if not isinstance(sessions, dict):
                sessions = {}
            resumable = 0
            for key, session in sessions.items():
                if key not in selection_progress or not isinstance(session, dict):
                    continue
                progress = normalize_discoflash_progress(session, is_last_selection=(key == last_selection_key))
                selection_progress[key]["discoflash_progress"] = progress
                if progress["status"] == "resume_available":
                    resumable += 1
            app_summary["resume_available_count"] = resumable
            app_summary["status"] = "resume_available" if resumable > 0 else "idle"

        apps[app_name] = app_summary

    return {
        "apps": apps,
        "selection_progress": selection_progress,
    }


def idle_vox_progress() -> dict[str, Any]:
    return {
        "status": "idle",
        "sentence_index": None,
        "sentence_count": None,
        "progress_percent": None,
        "chapter_label": None,
        "updated_at_utc": None,
        "is_last_selection": False,
    }


def unavailable_vox_progress() -> dict[str, Any]:
    progress = idle_vox_progress()
    progress["status"] = "unavailable"
    return progress


def idle_discoflash_progress() -> dict[str, Any]:
    return {
        "status": "idle",
        "mode": None,
        "matched_count": None,
        "total_count": None,
        "correct_count": None,
        "answered_count": None,
        "remaining_count": None,
        "updated_at_utc": None,
        "is_last_selection": False,
    }


def unavailable_discoflash_progress() -> dict[str, Any]:
    progress = idle_discoflash_progress()
    progress["status"] = "unavailable"
    return progress


def idle_completion() -> dict[str, Any]:
    return {
        "status": "idle",
        "completed_at_utc": None,
        "source": None,
        "payload": None,
        "is_last_completed": False,
    }


def unavailable_completion() -> dict[str, Any]:
    completion = idle_completion()
    completion["status"] = "unavailable"
    return completion


def idle_review() -> dict[str, Any]:
    return {
        "status": "idle",
        "stage_index": None,
        "last_reviewed_at_utc": None,
        "next_due_at_utc": None,
        "due_now": False,
        "source": None,
        "payload": None,
        "is_last_reviewed": False,
    }


def unavailable_review() -> dict[str, Any]:
    review = idle_review()
    review["status"] = "unavailable"
    return review


def normalize_completion(record: dict[str, Any], *, is_last_completed: bool) -> dict[str, Any]:
    return {
        "status": "completed",
        "completed_at_utc": none_if_blank(record.get("completed_at_utc")),
        "source": none_if_blank(record.get("source")),
        "payload": dict(record.get("payload", {})) if isinstance(record.get("payload"), dict) else {},
        "is_last_completed": is_last_completed,
    }


def normalize_review(record: dict[str, Any], *, is_last_reviewed: bool) -> dict[str, Any]:
    next_due_at = none_if_blank(record.get("next_due_at_utc"))
    return {
        "status": "review_scheduled",
        "stage_index": coerce_int(record.get("stage_index")),
        "last_reviewed_at_utc": none_if_blank(record.get("last_reviewed_at_utc")),
        "next_due_at_utc": next_due_at,
        "due_now": bool(next_due_at and parse_utc_timestamp(next_due_at) <= datetime.now(UTC)),
        "source": none_if_blank(record.get("source")),
        "payload": dict(record.get("payload", {})) if isinstance(record.get("payload"), dict) else {},
        "is_last_reviewed": is_last_reviewed,
    }


def normalize_vox_progress(position: dict[str, Any], *, is_last_selection: bool) -> dict[str, Any]:
    sentence_index = coerce_int(position.get("sentence_index"))
    sentence_count = coerce_int(position.get("sentence_count"))
    progress_percent = None
    if sentence_index is not None and sentence_count and sentence_count > 0:
        progress_percent = int((min(sentence_index + 1, sentence_count) / sentence_count) * 100)
    return {
        "status": "resume_available",
        "sentence_index": sentence_index,
        "sentence_count": sentence_count,
        "progress_percent": progress_percent,
        "chapter_label": none_if_blank(position.get("chapter_label")),
        "updated_at_utc": none_if_blank(position.get("updated_at_utc")),
        "is_last_selection": is_last_selection,
    }


def normalize_discoflash_progress(session: dict[str, Any], *, is_last_selection: bool) -> dict[str, Any]:
    mode = str(session.get("mode", "")).strip() or None
    pair_ids = session.get("pair_ids")
    total_count = len(pair_ids) if isinstance(pair_ids, list) else None
    matched_count = None
    correct_count = None
    answered_count = None
    remaining_count = None
    if mode == "tap":
        tap_state = session.get("tap_state")
        if isinstance(tap_state, dict):
            matched_ids = tap_state.get("matched_ids")
            matched_count = len(matched_ids) if isinstance(matched_ids, list) else 0
    elif mode == "quiz":
        quiz_state = session.get("quiz_state")
        if isinstance(quiz_state, dict):
            correct_count = coerce_int(quiz_state.get("correct"))
            remaining_ids = quiz_state.get("remaining_ids")
            remaining_count = len(remaining_ids) if isinstance(remaining_ids, list) else None
            if total_count is not None and remaining_count is not None:
                answered_count = total_count - remaining_count
    return {
        "status": "resume_available",
        "mode": mode,
        "matched_count": matched_count,
        "total_count": total_count,
        "correct_count": correct_count,
        "answered_count": answered_count,
        "remaining_count": remaining_count,
        "updated_at_utc": none_if_blank(session.get("updated_at_utc")),
        "is_last_selection": is_last_selection,
    }


def coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def none_if_blank(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def format_vox_progress(progress: dict[str, Any]) -> str:
    status = str(progress.get("status") or "idle")
    if status == "unavailable":
        return "unavailable"
    if status != "resume_available":
        return "idle"
    sentence_index = coerce_int(progress.get("sentence_index"))
    sentence_count = coerce_int(progress.get("sentence_count"))
    if sentence_index is None or sentence_count is None or sentence_count <= 0:
        return "resume available"
    current = min(sentence_index + 1, sentence_count)
    percent = progress.get("progress_percent")
    percent_text = f" ({percent}%)" if isinstance(percent, int) else ""
    return f"resume {current}/{sentence_count}{percent_text}"


def format_discoflash_progress(progress: dict[str, Any]) -> str:
    status = str(progress.get("status") or "idle")
    if status == "unavailable":
        return "unavailable"
    if status != "resume_available":
        return "idle"
    mode = str(progress.get("mode") or "").strip()
    if mode == "tap":
        matched = coerce_int(progress.get("matched_count"))
        total = coerce_int(progress.get("total_count"))
        if matched is not None and total is not None:
            return f"resume tap {matched}/{total}"
        return "resume tap"
    if mode == "quiz":
        correct = coerce_int(progress.get("correct_count"))
        remaining = coerce_int(progress.get("remaining_count"))
        if correct is not None and remaining is not None:
            return f"resume quiz {correct} correct, {remaining} remaining"
        return "resume quiz"
    return "resume available"


def build_dashboard_completion_overlay(books: list[dict[str, Any]]) -> dict[str, Any]:
    known_chapter_keys: set[str] = set()
    for book in books:
        for chapter in book["chapters"]:
            known_chapter_keys.add(study_selection_key(str(book["document_id"]), str(chapter["chapter_id"])))

    selection_completion: dict[str, dict[str, Any]] = {
        key: {
            "vox_completion": idle_completion(),
            "discoflash_completion": idle_completion(),
        }
        for key in known_chapter_keys
    }
    apps: dict[str, Any] = {}

    for app_name, app_info in study_dashboard_app_roots().items():
        root = Path(app_info["root"])
        entrypoint = Path(app_info["entrypoint"])
        available = root.exists() and entrypoint.exists()
        completion_path = study_dashboard_completion_path(app_name, root)
        app_summary = {
            "available": available,
            "completion_path": str(completion_path),
            "last_completed_selection_key": None,
            "completed_count": 0,
            "load_error": None,
        }
        if not available:
            app_summary["status"] = "unavailable"
            empty_completion = unavailable_completion()
            for key in selection_completion:
                selection_completion[key][f"{app_name}_completion"] = dict(empty_completion)
            apps[app_name] = app_summary
            continue

        if not completion_path.exists():
            app_summary["status"] = "idle"
            apps[app_name] = app_summary
            continue

        try:
            payload = json.loads(completion_path.read_text(encoding="utf-8"))
        except Exception:
            app_summary["status"] = "idle"
            app_summary["load_error"] = "invalid_json"
            apps[app_name] = app_summary
            continue

        if not isinstance(payload, dict):
            app_summary["status"] = "idle"
            app_summary["load_error"] = "invalid_payload"
            apps[app_name] = app_summary
            continue

        last_completed_selection_key = payload.get("last_completed_selection_key")
        if isinstance(last_completed_selection_key, str) and last_completed_selection_key.strip():
            app_summary["last_completed_selection_key"] = last_completed_selection_key

        completed = payload.get("completed")
        if not isinstance(completed, dict):
            completed = {}
        completed_count = 0
        for key, record in completed.items():
            if key not in selection_completion or not isinstance(record, dict):
                continue
            completion = normalize_completion(record, is_last_completed=(key == last_completed_selection_key))
            selection_completion[key][f"{app_name}_completion"] = completion
            if completion["status"] == "completed":
                completed_count += 1
        app_summary["completed_count"] = completed_count
        app_summary["status"] = "completed" if completed_count > 0 else "idle"
        apps[app_name] = app_summary

    return {
        "apps": apps,
        "selection_completion": selection_completion,
    }


def build_dashboard_review_overlay(books: list[dict[str, Any]]) -> dict[str, Any]:
    known_chapter_keys: set[str] = set()
    for book in books:
        for chapter in book["chapters"]:
            known_chapter_keys.add(study_selection_key(str(book["document_id"]), str(chapter["chapter_id"])))

    selection_reviews: dict[str, dict[str, Any]] = {
        key: {
            "vox_review": idle_review(),
            "discoflash_review": idle_review(),
        }
        for key in known_chapter_keys
    }
    apps: dict[str, Any] = {}

    for app_name, app_info in study_dashboard_app_roots().items():
        root = Path(app_info["root"])
        entrypoint = Path(app_info["entrypoint"])
        available = root.exists() and entrypoint.exists()
        review_path = study_dashboard_review_path(app_name, root)
        app_summary = {
            "available": available,
            "review_path": str(review_path),
            "last_reviewed_selection_key": None,
            "due_review_count": 0,
            "load_error": None,
        }
        if not available:
            app_summary["status"] = "unavailable"
            empty_review = unavailable_review()
            for key in selection_reviews:
                selection_reviews[key][f"{app_name}_review"] = dict(empty_review)
            apps[app_name] = app_summary
            continue
        if not review_path.exists():
            app_summary["status"] = "idle"
            apps[app_name] = app_summary
            continue
        try:
            payload = json.loads(review_path.read_text(encoding="utf-8"))
        except Exception:
            app_summary["status"] = "idle"
            app_summary["load_error"] = "invalid_json"
            apps[app_name] = app_summary
            continue
        if not isinstance(payload, dict):
            app_summary["status"] = "idle"
            app_summary["load_error"] = "invalid_payload"
            apps[app_name] = app_summary
            continue
        last_reviewed_selection_key = payload.get("last_reviewed_selection_key")
        if isinstance(last_reviewed_selection_key, str) and last_reviewed_selection_key.strip():
            app_summary["last_reviewed_selection_key"] = last_reviewed_selection_key
        reviews = payload.get("reviews")
        if not isinstance(reviews, dict):
            reviews = {}
        due_count = 0
        for key, record in reviews.items():
            if key not in selection_reviews or not isinstance(record, dict):
                continue
            review = normalize_review(record, is_last_reviewed=(key == last_reviewed_selection_key))
            selection_reviews[key][f"{app_name}_review"] = review
            if review["due_now"]:
                due_count += 1
        app_summary["due_review_count"] = due_count
        app_summary["status"] = "review_due" if due_count > 0 else ("scheduled" if reviews else "idle")
        apps[app_name] = app_summary

    return {
        "apps": apps,
        "selection_reviews": selection_reviews,
    }


def parse_utc_timestamp(value: object) -> datetime:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def reverse_utc_sort_tuple(value: object) -> tuple[int, int, int, int, int]:
    updated = parse_utc_timestamp(value)
    return (
        -updated.toordinal(),
        -updated.hour,
        -updated.minute,
        -updated.second,
        -updated.microsecond,
    )


def continue_entry_sort_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if entry.get("is_last_selection") else 1,
        reverse_utc_sort_tuple(entry.get("updated_at_utc")),
        normalize_name(str(entry.get("book_title") or "")),
        normalize_name(str(entry.get("chapter_label") or "")),
    )


def continue_entry_progress_summary(kind: str, entry: dict[str, Any]) -> str:
    if kind == "vox_resume":
        summary = format_vox_progress(entry["vox_progress"])
    elif kind == "discoflash_resume":
        summary = format_discoflash_progress(entry["discoflash_progress"])
    else:
        summary = "fresh recommendation"
    if entry.get("is_last_selection"):
        return f"{summary}; last active"
    return summary


def activity_entry_sort_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if entry.get("is_last_selection") else 1,
        reverse_utc_sort_tuple(entry.get("updated_at_utc")),
        normalize_name(str(entry.get("book_title") or "")),
        normalize_name(str(entry.get("chapter_label") or "")),
        normalize_name(str(entry.get("app") or "")),
    )


def build_selection_metadata_map(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for book in packet["books"]:
        whole_key = study_selection_key(str(book["document_id"]), None)
        whole_commands = study_dashboard_launch_metadata(whole_key)
        metadata[whole_key] = {
            "selection_key": whole_key,
            "document_id": book["document_id"],
            "book_title": book["book_title"],
            "chapter_id": None,
            "chapter_label": None,
            "math_library_path": book["page_paths"]["book"],
            "vox_commands": whole_commands["vox"],
            "discoflash_commands": whole_commands["discoflash"],
        }
        for chapter in book["chapters"]:
            chapter_key = study_selection_key(str(book["document_id"]), str(chapter["chapter_id"]))
            chapter_commands = study_dashboard_launch_metadata(chapter_key)
            metadata[chapter_key] = {
                "selection_key": chapter_key,
                "document_id": book["document_id"],
                "book_title": book["book_title"],
                "chapter_id": chapter["chapter_id"],
                "chapter_label": chapter_label_text(chapter),
                "math_library_path": chapter["page_path"],
                "vox_commands": chapter_commands["vox"],
                "discoflash_commands": chapter_commands["discoflash"],
            }
    return metadata


def format_vox_event_summary(event_type: str, payload: dict[str, Any]) -> str:
    sentence_index = coerce_int(payload.get("sentence_index"))
    sentence_count = coerce_int(payload.get("sentence_count"))
    progress_percent = coerce_int(payload.get("progress_percent"))
    prefix = {
        "session_started": "started",
        "session_resumed": "resumed",
        "session_checkpoint": "checkpoint",
        "session_completed": "completed",
        "review_completed": "review completed",
    }.get(event_type, "activity")
    if sentence_index is None or sentence_count is None or sentence_count <= 0:
        return prefix
    current = min(sentence_index + 1, sentence_count)
    percent_text = f" ({progress_percent}%)" if progress_percent is not None else ""
    return f"{prefix} {current}/{sentence_count}{percent_text}"


def format_discoflash_event_summary(event_type: str, payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode") or "").strip()
    prefix = {
        "session_started": "started",
        "session_resumed": "resumed",
        "session_checkpoint": "checkpoint",
        "session_completed": "completed",
        "review_completed": "review completed",
    }.get(event_type, "activity")
    if mode == "tap":
        matched = coerce_int(payload.get("matched_count"))
        total = coerce_int(payload.get("total_count"))
        if matched is not None and total is not None:
            return f"{prefix} tap {matched}/{total}"
        return f"{prefix} tap"
    if mode == "quiz":
        correct = coerce_int(payload.get("correct_count"))
        remaining = coerce_int(payload.get("remaining_count"))
        if correct is not None and remaining is not None:
            return f"{prefix} quiz {correct} correct, {remaining} remaining"
        return f"{prefix} quiz"
    return prefix


def build_dashboard_event_history(packet: dict[str, Any]) -> dict[str, Any]:
    selection_metadata = build_selection_metadata_map(packet)
    recent_vox: list[dict[str, Any]] = []
    recent_discoflash: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []

    for app_name, app_info in study_dashboard_app_roots().items():
        root = Path(app_info["root"])
        entrypoint = Path(app_info["entrypoint"])
        if not root.exists() or not entrypoint.exists():
            continue
        events_path = study_dashboard_event_log_path(app_name, root)
        if not events_path.exists():
            continue
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        last_selection_key = packet["app_progress"][app_name]["last_selection_key"]
        for raw_line in lines:
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            selection_key = none_if_blank(event.get("selection_key"))
            if selection_key is None or selection_key not in selection_metadata:
                continue
            event_type = none_if_blank(event.get("event_type"))
            if event_type not in {"session_started", "session_resumed", "session_checkpoint", "session_completed", "review_completed"}:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            metadata = selection_metadata[selection_key]
            preferred_command = metadata[app_name + "_commands"]["fresh"] if event_type == "session_completed" else metadata[app_name + "_commands"]["resume"]
            progress_summary = (
                format_vox_event_summary(event_type, payload)
                if app_name == "vox"
                else format_discoflash_event_summary(event_type, payload)
            )
            entry = {
                **metadata,
                "event_id": none_if_blank(event.get("event_id")) or "",
                "app": app_name,
                "kind": f"{app_name}_recent",
                "event_type": event_type,
                "is_last_selection": selection_key == last_selection_key,
                "occurred_at_utc": none_if_blank(event.get("occurred_at_utc")),
                "updated_at_utc": none_if_blank(event.get("occurred_at_utc")),
                "progress_summary": progress_summary,
                "preferred_command": preferred_command,
            }
            if event_type == "session_completed":
                completed.append(entry)
            elif app_name == "vox":
                recent_vox.append(entry)
            else:
                recent_discoflash.append(entry)

    recent_vox.sort(key=activity_entry_sort_key)
    recent_discoflash.sort(key=activity_entry_sort_key)
    completed.sort(key=activity_entry_sort_key)
    return {
        "recent_activity": {
            "vox": recent_vox,
            "discoflash": recent_discoflash,
            "merged": sorted(recent_vox + recent_discoflash, key=activity_entry_sort_key),
        },
        "recently_completed": completed,
    }


def build_continue_studying(packet: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    vox_resume: list[dict[str, Any]] = []
    discoflash_resume: list[dict[str, Any]] = []
    fresh_recommendations: list[dict[str, Any]] = []

    for book in packet["books"]:
        book_has_progress = False
        whole_book_key = study_selection_key(str(book["document_id"]), None)
        whole_commands = study_dashboard_launch_metadata(whole_book_key)
        whole_base = {
            "book_title": book["book_title"],
            "chapter_id": None,
            "chapter_label": None,
            "document_id": book["document_id"],
            "has_source_note": bool(book["has_source_note"]),
            "math_library_path": book["page_paths"]["book"],
            "selection_key": whole_book_key,
            "vox_commands": whole_commands["vox"],
            "discoflash_commands": whole_commands["discoflash"],
            "vox_progress": book["vox_progress"],
            "discoflash_progress": book["discoflash_progress"],
        }
        if book["vox_progress"]["status"] == "resume_available":
            book_has_progress = True
            entry = dict(whole_base)
            entry.update(
                {
                    "kind": "vox_resume",
                    "is_last_selection": bool(book["vox_progress"].get("is_last_selection")),
                    "preferred_command": whole_commands["vox"]["resume"],
                    "updated_at_utc": book["vox_progress"].get("updated_at_utc"),
                }
            )
            entry["progress_summary"] = continue_entry_progress_summary("vox_resume", entry)
            vox_resume.append(entry)
        if book["discoflash_progress"]["status"] == "resume_available":
            book_has_progress = True
            entry = dict(whole_base)
            entry.update(
                {
                    "kind": "discoflash_resume",
                    "is_last_selection": bool(book["discoflash_progress"].get("is_last_selection")),
                    "preferred_command": whole_commands["discoflash"]["resume"],
                    "updated_at_utc": book["discoflash_progress"].get("updated_at_utc"),
                }
            )
            entry["progress_summary"] = continue_entry_progress_summary("discoflash_resume", entry)
            discoflash_resume.append(entry)

        for chapter in book["chapters"]:
            chapter_key = study_selection_key(str(book["document_id"]), str(chapter["chapter_id"]))
            chapter_commands = study_dashboard_launch_metadata(chapter_key)
            chapter_base = {
                "book_title": book["book_title"],
                "chapter_id": chapter["chapter_id"],
                "chapter_label": chapter_label_text(chapter),
                "document_id": book["document_id"],
                "has_source_note": bool(book["has_source_note"]),
                "math_library_path": chapter["page_path"],
                "selection_key": chapter_key,
                "vox_commands": chapter_commands["vox"],
                "discoflash_commands": chapter_commands["discoflash"],
                "vox_progress": chapter["vox_progress"],
                "discoflash_progress": chapter["discoflash_progress"],
            }
            if chapter["vox_progress"]["status"] == "resume_available":
                book_has_progress = True
                entry = dict(chapter_base)
                entry.update(
                    {
                        "kind": "vox_resume",
                        "is_last_selection": bool(chapter["vox_progress"].get("is_last_selection")),
                        "preferred_command": chapter_commands["vox"]["resume"],
                        "updated_at_utc": chapter["vox_progress"].get("updated_at_utc"),
                    }
                )
                entry["progress_summary"] = continue_entry_progress_summary("vox_resume", entry)
                vox_resume.append(entry)
            if chapter["discoflash_progress"]["status"] == "resume_available":
                book_has_progress = True
                entry = dict(chapter_base)
                entry.update(
                    {
                        "kind": "discoflash_resume",
                        "is_last_selection": bool(chapter["discoflash_progress"].get("is_last_selection")),
                        "preferred_command": chapter_commands["discoflash"]["resume"],
                        "updated_at_utc": chapter["discoflash_progress"].get("updated_at_utc"),
                    }
                )
                entry["progress_summary"] = continue_entry_progress_summary("discoflash_resume", entry)
                discoflash_resume.append(entry)

        if not book_has_progress and book["completion_counts"]["combined"] == 0:
            entry = dict(whole_base)
            entry.update(
                {
                    "kind": "fresh_recommendation",
                    "is_last_selection": False,
                    "preferred_command": whole_commands["vox"]["fresh"],
                    "updated_at_utc": None,
                }
            )
            entry["progress_summary"] = continue_entry_progress_summary("fresh_recommendation", entry)
            fresh_recommendations.append(entry)

    vox_resume.sort(key=continue_entry_sort_key)
    discoflash_resume.sort(key=continue_entry_sort_key)
    fresh_recommendations.sort(
        key=lambda entry: (
            0 if entry.get("has_source_note") else 1,
            normalize_name(str(entry.get("book_title") or "")),
        )
    )
    return {
        "vox_resume": vox_resume,
        "discoflash_resume": discoflash_resume,
        "fresh_recommendations": fresh_recommendations,
    }


def is_resumable_progress(progress: dict[str, Any]) -> bool:
    return str(progress.get("status") or "") == "resume_available"


def is_completed_vox_chapter(progress: dict[str, Any]) -> bool:
    if not is_resumable_progress(progress):
        return False
    sentence_index = coerce_int(progress.get("sentence_index"))
    sentence_count = coerce_int(progress.get("sentence_count"))
    if sentence_index is None or sentence_count is None or sentence_count <= 0:
        return False
    return sentence_index + 1 >= sentence_count


def build_next_up(packet: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for book in packet["books"]:
        if not book["chapters"]:
            continue
        target_index = next(
            (index for index, chapter in enumerate(book["chapters"]) if not chapter_has_completion(chapter)),
            None,
        )
        if target_index is None or target_index == 0:
            continue
        target = book["chapters"][target_index]
        if is_resumable_progress(target["vox_progress"]) or is_resumable_progress(target["discoflash_progress"]):
            continue

        target_key = study_selection_key(str(book["document_id"]), str(target["chapter_id"]))
        target_commands = study_dashboard_launch_metadata(target_key)
        source_chapter = book["chapters"][target_index - 1]
        source_timestamp = max(
            [
                source_chapter.get("vox_completion", {}).get("completed_at_utc"),
                source_chapter.get("discoflash_completion", {}).get("completed_at_utc"),
            ],
            key=parse_utc_timestamp,
        )
        entries.append(
            {
                "selection_key": target_key,
                "document_id": book["document_id"],
                "book_title": book["book_title"],
                "source_selection_key": study_selection_key(str(book["document_id"]), str(source_chapter["chapter_id"])),
                "source_chapter_id": source_chapter["chapter_id"],
                "source_chapter_label": chapter_label_text(source_chapter),
                "target_chapter_id": target["chapter_id"],
                "target_chapter_label": chapter_label_text(target),
                "updated_at_utc": source_timestamp,
                "reason": f"after completing {chapter_label_text(source_chapter)}",
                "math_library_path": target["page_path"],
                "vox_commands": target_commands["vox"],
                "discoflash_commands": target_commands["discoflash"],
                "discoflash_supported": int(target.get("card_count") or 0) > 0,
                "preferred_command": target_commands["vox"]["fresh"],
            }
        )

    entries.sort(
        key=lambda entry: (
            0 if entry["selection_key"] == packet["app_progress"]["vox"]["last_selection_key"] else 1,
            reverse_utc_sort_tuple(entry["updated_at_utc"]),
            normalize_name(str(entry["book_title"])),
            normalize_name(str(entry["target_chapter_label"])),
        )
    )
    return entries


def chapter_latest_completion_timestamp(chapter: dict[str, Any]) -> str | None:
    timestamps = [
        chapter.get("vox_completion", {}).get("completed_at_utc"),
        chapter.get("discoflash_completion", {}).get("completed_at_utc"),
    ]
    return max(timestamps, key=parse_utc_timestamp)


def combine_chapter_review(chapter: dict[str, Any]) -> dict[str, Any]:
    vox_review = chapter.get("vox_review", {})
    discoflash_review = chapter.get("discoflash_review", {})
    active = []
    if isinstance(vox_review, dict) and vox_review.get("status") == "review_scheduled":
        active.append(("vox", vox_review))
    if isinstance(discoflash_review, dict) and discoflash_review.get("status") == "review_scheduled":
        active.append(("discoflash", discoflash_review))
    if not active:
        return idle_review()
    source_app = active[0][0] if len(active) == 1 else "both"
    next_due = min((review.get("next_due_at_utc") for _, review in active), key=parse_utc_timestamp)
    last_reviewed = max((review.get("last_reviewed_at_utc") for _, review in active), key=parse_utc_timestamp)
    stage_index = max(coerce_int(review.get("stage_index")) or 0 for _, review in active)
    due_now = any(bool(review.get("due_now")) for _, review in active)
    return {
        "status": "review_scheduled",
        "source_app": source_app,
        "stage_index": stage_index,
        "last_reviewed_at_utc": last_reviewed,
        "next_due_at_utc": next_due,
        "due_now": due_now,
    }


def format_review_status(review: dict[str, Any]) -> str:
    if not isinstance(review, dict) or review.get("status") != "review_scheduled":
        return "none"
    stage_index = coerce_int(review.get("stage_index"))
    next_due = none_if_blank(review.get("next_due_at_utc")) or "unknown"
    due = " due-now" if review.get("due_now") else ""
    if stage_index is None:
        return f"scheduled {next_due}{due}"
    return f"stage {stage_index} | {next_due}{due}"


def book_latest_completion_timestamp(book: dict[str, Any]) -> str | None:
    if not book.get("completed_chapters"):
        return None
    return max(
        (chapter_latest_completion_timestamp(chapter) for chapter in book["completed_chapters"]),
        key=parse_utc_timestamp,
    )


def book_latest_activity_timestamp(book: dict[str, Any]) -> str | None:
    timestamps: list[str | None] = [
        book.get("vox_progress", {}).get("updated_at_utc"),
        book.get("discoflash_progress", {}).get("updated_at_utc"),
    ]
    for chapter in book.get("chapters", []):
        timestamps.append(chapter.get("vox_progress", {}).get("updated_at_utc"))
        timestamps.append(chapter.get("discoflash_progress", {}).get("updated_at_utc"))
    return max(timestamps, key=parse_utc_timestamp)


def chapter_source_app(chapter: dict[str, Any]) -> str:
    status = chapter_completion_status(chapter)
    if status == "both":
        return "both"
    if status == "vox":
        return "vox"
    if status == "discoflash":
        return "discoflash"
    return "no"


def build_study_journal(packet: dict[str, Any]) -> dict[str, Any]:
    books_in_progress: list[dict[str, Any]] = []
    per_book: dict[str, dict[str, Any]] = {}

    for book in packet["books"]:
        active_vox = bool(book["vox_progress"].get("status") == "resume_available") or any(
            chapter["vox_progress"].get("status") == "resume_available" for chapter in book["chapters"]
        )
        active_discoflash = bool(book["discoflash_progress"].get("status") == "resume_available") or any(
            chapter["discoflash_progress"].get("status") == "resume_available" for chapter in book["chapters"]
        )
        first_incomplete = next((chapter for chapter in book["chapters"] if not chapter_has_completion(chapter)), None)
        latest_activity = book_latest_activity_timestamp(book)
        latest_completion = book_latest_completion_timestamp(book)
        summary = {
            "document_id": book["document_id"],
            "book_title": book["book_title"],
            "completed_chapters": book["completion_counts"]["combined"],
            "chapter_count": book["chapter_count"],
            "active_vox": active_vox,
            "active_discoflash": active_discoflash,
            "last_activity_at_utc": latest_activity,
            "last_completion_at_utc": latest_completion,
            "next_incomplete_chapter": chapter_label_text(first_incomplete) if first_incomplete else None,
            "math_library_path": book["page_paths"]["book"],
            "selection_key": study_selection_key(str(book["document_id"]), None),
        }
        per_book[str(book["document_id"])] = summary
        if active_vox or active_discoflash or book["completion_counts"]["combined"] > 0:
            books_in_progress.append(summary)

    books_in_progress.sort(
        key=lambda item: (
            0 if item["active_vox"] or item["active_discoflash"] else 1,
            reverse_utc_sort_tuple(item["last_activity_at_utc"] or item["last_completion_at_utc"]),
            normalize_name(str(item["book_title"])),
        )
    )
    vox_last_activity = max(
        (
            max(
                [
                    book_latest_activity_timestamp(book),
                    book_latest_completion_timestamp(book),
                ],
                key=parse_utc_timestamp,
            )
            for book in packet["books"]
            if per_book[str(book["document_id"])]["active_vox"]
            or per_book[str(book["document_id"])]["completed_chapters"] > 0
        ),
        key=parse_utc_timestamp,
        default=None,
    )
    discoflash_last_activity = max(
        (
            max(
                [
                    book_latest_activity_timestamp(book),
                    book_latest_completion_timestamp(book),
                ],
                key=parse_utc_timestamp,
            )
            for book in packet["books"]
            if per_book[str(book["document_id"])]["active_discoflash"]
            or per_book[str(book["document_id"])]["completed_chapters"] > 0
        ),
        key=parse_utc_timestamp,
        default=None,
    )
    return {
        "summary": {
            "books_with_active_resume": sum(
                1
                for summary in per_book.values()
                if summary["active_vox"] or summary["active_discoflash"]
            ),
            "books_with_completion": sum(1 for summary in per_book.values() if summary["completed_chapters"] > 0),
            "fully_completed_books": packet["fully_completed_book_count"],
            "total_completed_chapters": packet["completed_chapter_count"],
            "vox_last_activity_at_utc": vox_last_activity,
            "discoflash_last_activity_at_utc": discoflash_last_activity,
        },
        "books_in_progress": books_in_progress,
        "per_book": per_book,
    }


def build_review_queue(packet: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for book in packet["books"]:
        for chapter in book["chapters"]:
            review = chapter.get("combined_review", {})
            if review.get("status") != "review_scheduled" or not review.get("due_now"):
                continue
            if is_resumable_progress(chapter["vox_progress"]) or is_resumable_progress(chapter["discoflash_progress"]):
                continue
            selection_key = study_selection_key(str(book["document_id"]), str(chapter["chapter_id"]))
            commands = study_dashboard_launch_metadata(selection_key)
            entries.append(
                {
                    "selection_key": selection_key,
                    "document_id": book["document_id"],
                    "book_title": book["book_title"],
                    "chapter_id": chapter["chapter_id"],
                    "chapter_label": chapter_label_text(chapter),
                    "source_app": review.get("source_app") or "no",
                    "stage_index": review.get("stage_index"),
                    "next_due_at_utc": review.get("next_due_at_utc"),
                    "math_library_path": chapter["page_path"],
                    "vox_commands": commands["vox"],
                    "discoflash_commands": commands["discoflash"],
                    "discoflash_supported": int(chapter.get("card_count") or 0) > 0,
                }
            )
    entries.sort(
        key=lambda entry: (
            parse_utc_timestamp(entry["next_due_at_utc"]),
            normalize_name(str(entry["book_title"])),
            normalize_name(str(entry["chapter_label"])),
        )
    )
    return entries


def study_page_summary(
    db_path: Path = DEFAULT_DB,
    *,
    source_root: Path = DEFAULT_STUDY_SOURCE_ROOT,
    output_dir: Path = DEFAULT_STUDY_DIR,
    wiki_root: Path = DEFAULT_STUDY_PAGES_WIKI_ROOT,
    shelf: str = DEFAULT_STUDY_SHELF,
    selection: str = DEFAULT_STUDY_SELECTION,
) -> dict[str, Any]:
    packet = build_page_packet(
        db_path,
        source_root=source_root,
        output_dir=output_dir,
        wiki_root=wiki_root,
        shelf=shelf,
        selection=selection,
    )
    return {
        "book_count": packet["book_count"],
        "blocked_count": packet["blocked_count"],
        "books": [page_book_summary(book) for book in packet["books"]],
        "catalog_db": str(db_path),
        "dashboard_output_dir": str((wiki_root / DEFAULT_STUDY_DASHBOARD_PROJECT).resolve()),
        "dashboard_state_index": str((wiki_root / DEFAULT_STUDY_DASHBOARD_STATE).resolve()),
        "definition_source_count": packet["definition_index"]["source_count"],
        "definition_term_count": packet["definition_index"]["term_count"],
        "flashcard_ready_count": packet["flashcard_ready_count"],
        "generated_at_utc": packet["generated_at_utc"],
        "manifest_only_count": packet["manifest_only_count"],
        "note_backed_count": packet["note_backed_count"],
        "output_dir": str((wiki_root / DEFAULT_STUDY_PAGES_PROJECT).resolve()),
        "reader_ready_count": packet["reader_ready_count"],
        "result_source_count": packet["result_index"]["source_count"],
        "result_term_count": packet["result_index"]["term_count"],
        "selection": packet["selection"],
        "shelf": packet["shelf"],
        "source_root": packet["source_root"],
        "state_index": str((wiki_root / DEFAULT_STUDY_PAGES_STATE).resolve()),
    }


def study_page_show(
    db_path: Path = DEFAULT_DB,
    identifier: str = "",
    *,
    source_root: Path = DEFAULT_STUDY_SOURCE_ROOT,
    output_dir: Path = DEFAULT_STUDY_DIR,
    wiki_root: Path = DEFAULT_STUDY_PAGES_WIKI_ROOT,
    shelf: str = DEFAULT_STUDY_SHELF,
    selection: str = DEFAULT_STUDY_SELECTION,
) -> dict[str, Any]:
    packet = build_page_packet(
        db_path,
        source_root=source_root,
        output_dir=output_dir,
        wiki_root=wiki_root,
        shelf=shelf,
        selection=selection,
    )
    book = resolve_index_book(packet["books"], identifier)
    return {
        "book": book,
        "book_markdown": render_book_page(book, wiki_root=wiki_root),
        "catalog_db": str(db_path),
        "generated_at_utc": packet["generated_at_utc"],
        "selection": packet["selection"],
        "shelf": packet["shelf"],
        "source_root": packet["source_root"],
    }


def build_study_pages(
    db_path: Path = DEFAULT_DB,
    *,
    source_root: Path = DEFAULT_STUDY_SOURCE_ROOT,
    output_dir: Path = DEFAULT_STUDY_DIR,
    wiki_root: Path = DEFAULT_STUDY_PAGES_WIKI_ROOT,
    shelf: str = DEFAULT_STUDY_SHELF,
    selection: str = DEFAULT_STUDY_SELECTION,
) -> dict[str, Any]:
    packet = build_page_packet(
        db_path,
        source_root=source_root,
        output_dir=output_dir,
        wiki_root=wiki_root,
        shelf=shelf,
        selection=selection,
    )
    files: list[str] = []
    project_root = wiki_root / DEFAULT_STUDY_PAGES_PROJECT
    dashboard_root = wiki_root / DEFAULT_STUDY_DASHBOARD_PROJECT
    books_root = project_root / "books"
    definitions_root = project_root / "definitions"
    results_root = project_root / "results"
    state_root = project_root / "state"
    dashboard_books_root = dashboard_root / "books"
    dashboard_state_root = dashboard_root / "state"
    if books_root.exists():
        shutil.rmtree(books_root)
    if definitions_root.exists():
        shutil.rmtree(definitions_root)
    if results_root.exists():
        shutil.rmtree(results_root)
    if dashboard_books_root.exists():
        shutil.rmtree(dashboard_books_root)
    books_root.mkdir(parents=True, exist_ok=True)
    definitions_root.mkdir(parents=True, exist_ok=True)
    results_root.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)
    dashboard_books_root.mkdir(parents=True, exist_ok=True)
    dashboard_state_root.mkdir(parents=True, exist_ok=True)

    hub_path = project_root / "README.md"
    hub_path.write_text(render_hub_page(packet, wiki_root=wiki_root), encoding="utf-8")
    files.append(str(hub_path))

    definitions_hub_path = wiki_root / DEFAULT_STUDY_PAGES_DEFINITIONS
    definitions_hub_path.parent.mkdir(parents=True, exist_ok=True)
    definitions_hub_path.write_text(render_term_index_hub(packet, kind="definitions", wiki_root=wiki_root), encoding="utf-8")
    files.append(str(definitions_hub_path))

    results_hub_path = wiki_root / DEFAULT_STUDY_PAGES_RESULTS
    results_hub_path.parent.mkdir(parents=True, exist_ok=True)
    results_hub_path.write_text(render_term_index_hub(packet, kind="results", wiki_root=wiki_root), encoding="utf-8")
    files.append(str(results_hub_path))

    dashboard_hub_path = dashboard_root / "README.md"
    dashboard_hub_path.write_text(render_study_dashboard_hub(packet, wiki_root=wiki_root), encoding="utf-8")
    files.append(str(dashboard_hub_path))

    for letter, group in packet["definition_index"]["by_letter"].items():
        letter_path = wiki_root / group["page_path"]
        letter_path.parent.mkdir(parents=True, exist_ok=True)
        letter_path.write_text(
            render_term_index_letter_page(packet, kind="definitions", letter=letter, wiki_root=wiki_root),
            encoding="utf-8",
        )
        files.append(str(letter_path))

    for letter, group in packet["result_index"]["by_letter"].items():
        letter_path = wiki_root / group["page_path"]
        letter_path.parent.mkdir(parents=True, exist_ok=True)
        letter_path.write_text(
            render_term_index_letter_page(packet, kind="results", letter=letter, wiki_root=wiki_root),
            encoding="utf-8",
        )
        files.append(str(letter_path))

    for book in packet["books"]:
        book_path = wiki_root / book["page_paths"]["book"]
        book_path.parent.mkdir(parents=True, exist_ok=True)
        book_path.write_text(render_book_page(book, wiki_root=wiki_root), encoding="utf-8")
        files.append(str(book_path))
        for chapter in book["chapters"]:
            if not chapter.get("page_path"):
                continue
            chapter_path = wiki_root / chapter["page_path"]
            chapter_path.parent.mkdir(parents=True, exist_ok=True)
            chapter_path.write_text(render_chapter_page(book, chapter, wiki_root=wiki_root), encoding="utf-8")
            files.append(str(chapter_path))

        dashboard_book_path = dashboard_book_page_path(document_id=str(book["document_id"]))
        (wiki_root / dashboard_book_path).parent.mkdir(parents=True, exist_ok=True)
        (wiki_root / dashboard_book_path).write_text(
            render_study_dashboard_book_page(book, wiki_root=wiki_root),
            encoding="utf-8",
        )
        files.append(str(wiki_root / dashboard_book_path))

    state_index_path = wiki_root / DEFAULT_STUDY_PAGES_STATE
    state_index_path.parent.mkdir(parents=True, exist_ok=True)
    state_index_path.write_text(json.dumps(navigation_index(packet), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files.append(str(state_index_path))

    dashboard_state_index_path = wiki_root / DEFAULT_STUDY_DASHBOARD_STATE
    dashboard_state_index_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_state_index_path.write_text(
        json.dumps(study_dashboard_navigation_index(packet), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    files.append(str(dashboard_state_index_path))

    update_root_index(wiki_root)
    update_math_sources_hub(wiki_root)
    files.extend(
        [
            str(wiki_root / "index.md"),
            str(wiki_root / "sources" / "math" / "README.md"),
        ]
    )
    return {
        "book_count": packet["book_count"],
        "blocked_count": packet["blocked_count"],
        "catalog_db": str(db_path),
        "dashboard_output_dir": str(dashboard_root.resolve()),
        "dashboard_state_index": str(dashboard_state_index_path),
        "file_count": len(files),
        "files": files,
        "flashcard_ready_count": packet["flashcard_ready_count"],
        "generated_at_utc": packet["generated_at_utc"],
        "note_backed_count": packet["note_backed_count"],
        "output_dir": str(project_root.resolve()),
        "reader_ready_count": packet["reader_ready_count"],
        "definition_source_count": packet["definition_index"]["source_count"],
        "definition_term_count": packet["definition_index"]["term_count"],
        "result_source_count": packet["result_index"]["source_count"],
        "result_term_count": packet["result_index"]["term_count"],
        "selection": packet["selection"],
        "shelf": packet["shelf"],
        "source_root": packet["source_root"],
        "state_index": str(state_index_path),
    }


def build_page_packet(
    db_path: Path,
    *,
    source_root: Path,
    output_dir: Path,
    wiki_root: Path,
    shelf: str,
    selection: str,
) -> dict[str, Any]:
    normalized_shelf = validate_shelf(shelf)
    normalized_selection = validate_selection(selection)
    source_root = source_root.resolve()
    wiki_root = wiki_root.resolve()
    shelf_root = output_dir / normalized_shelf
    inventory = study_inventory(
        db_path,
        source_root=source_root,
        shelf=normalized_shelf,
        selection=normalized_selection,
    )
    manifest_books = discover_manifest_books(shelf_root)
    if not inventory["books"] and not manifest_books:
        raise ValueError(f"study materials not built in {shelf_root}; run `wiki study build` first")
    merged = merge_inventory_and_manifests(inventory["books"], manifest_books)
    books = [
        build_page_book(wiki_root=wiki_root, shelf_root=shelf_root, merged_book=book)
        for book in merged
    ]
    progress_overlay = build_dashboard_progress_overlay(books)
    apply_dashboard_progress_overlay(books, progress_overlay["selection_progress"])
    completion_overlay = build_dashboard_completion_overlay(books)
    apply_dashboard_completion_overlay(books, completion_overlay["selection_completion"])
    review_overlay = build_dashboard_review_overlay(books)
    apply_dashboard_review_overlay(books, review_overlay["selection_reviews"])
    definition_index = build_term_index(books, kind="definitions")
    result_index = build_term_index(books, kind="results")
    continue_studying = build_continue_studying({"books": books})
    event_history = build_dashboard_event_history(
        {
            "app_progress": progress_overlay["apps"],
            "books": books,
        }
    )
    journal_packet_input = {
        "books": books,
        "recent_activity": event_history["recent_activity"],
        "completed_chapter_count": sum(book["completion_counts"]["combined"] for book in books),
        "fully_completed_book_count": sum(
            1
            for book in books
            if book["chapter_count"] > 0 and book["completion_counts"]["combined"] == book["chapter_count"]
        ),
    }
    study_journal = build_study_journal(journal_packet_input)
    apply_book_journal_overlay(books, event_history=event_history, study_journal=study_journal)
    review_queue = build_review_queue({"books": books})
    next_up = build_next_up(
        {
            "app_progress": progress_overlay["apps"],
            "books": books,
        }
    )
    return {
        "app_progress": progress_overlay["apps"],
        "app_completion": completion_overlay["apps"],
        "app_review": review_overlay["apps"],
        "book_count": len(books),
        "blocked_count": sum(1 for book in books if not book["reader_ready"] or not book["flashcard_ready"]),
        "books": books,
        "study_journal": study_journal,
        "catalog_db": str(db_path),
        "continue_studying": continue_studying,
        "definition_index": definition_index,
        "flashcard_ready_count": sum(1 for book in books if book["flashcard_ready"]),
        "generated_at_utc": utc_now(),
        "manifest_only_count": sum(1 for book in books if not book["has_source_note"]),
        "next_up": next_up,
        "note_backed_count": sum(1 for book in books if book["has_source_note"]),
        "completed_chapter_count": sum(book["completion_counts"]["combined"] for book in books),
        "fully_completed_book_count": sum(
            1
            for book in books
            if book["chapter_count"] > 0 and book["completion_counts"]["combined"] == book["chapter_count"]
        ),
        "output_dir": str(shelf_root.resolve()),
        "recent_activity": event_history["recent_activity"],
        "recently_completed": event_history["recently_completed"],
        "review_queue": review_queue,
        "reader_ready_count": sum(1 for book in books if book["reader_ready"]),
        "result_index": result_index,
        "selection": normalized_selection,
        "shelf": normalized_shelf,
        "source_root": str(source_root),
        "wiki_root": str(wiki_root),
    }


def discover_manifest_books(shelf_root: Path) -> dict[str, dict[str, Any]]:
    books: dict[str, dict[str, Any]] = {}
    if not shelf_root.exists():
        return books
    for manifest_path in sorted(shelf_root.glob(f"*/{DEFAULT_BOOK_MANIFEST}"), key=lambda path: path.parent.name):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        document_id = str(manifest["document_id"])
        books[document_id] = {
            "book_title": str(manifest.get("book_title") or document_id),
            "definition_card_count": int(manifest.get("definition_card_count") or 0),
            "document_id": document_id,
            "files": dict(manifest.get("files") or {}),
            "has_source_note": bool(manifest.get("has_source_note")),
            "manifest": manifest,
            "manifest_path": str(manifest_path),
            "note_path": manifest.get("note_path"),
            "row_count": int(manifest.get("row_count") or 0),
            "status": str(manifest.get("status") or "built"),
            "title_source": str(manifest.get("title_source") or "book_manifest"),
        }
    return books


def merge_inventory_and_manifests(
    inventory_books: list[dict[str, Any]],
    manifest_books: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for inventory_book in inventory_books:
        document_id = str(inventory_book["document_id"])
        manifest_book = manifest_books.get(document_id)
        merged.append(merge_book_entry(inventory_book, manifest_book))
        seen.add(document_id)
    for document_id, manifest_book in sorted(manifest_books.items()):
        if document_id in seen:
            continue
        merged.append(
            {
                "book_title": manifest_book["book_title"],
                "definition_card_count": manifest_book["definition_card_count"],
                "document_id": document_id,
                "extract_root": str(manifest_book["manifest"].get("extract_root") or ""),
                "has_source_note": manifest_book["has_source_note"],
                "manifest": manifest_book["manifest"],
                "manifest_path": manifest_book["manifest_path"],
                "note_path": manifest_book["note_path"],
                "row_count": manifest_book["row_count"],
                "status": manifest_book["status"],
                "title_source": manifest_book["title_source"],
            }
        )
    merged.sort(key=lambda item: (normalize_name(str(item["book_title"])), str(item["document_id"])))
    return merged


def merge_book_entry(inventory_book: dict[str, Any], manifest_book: dict[str, Any] | None) -> dict[str, Any]:
    if manifest_book is None:
        inventory_status = str(inventory_book.get("status") or "")
        synthetic_status = "partial" if inventory_status == "partial" else "built" if inventory_status == "ready" else inventory_status
        return {
            "book_title": str(inventory_book["book_title"]),
            "definition_card_count": 0,
            "document_id": str(inventory_book["document_id"]),
            "extract_root": str(inventory_book.get("extract_root") or ""),
            "has_source_note": bool(inventory_book.get("has_source_note")),
            "manifest": {},
            "manifest_path": str((Path(str(inventory_book.get("extract_root") or "")) / DEFAULT_BOOK_MANIFEST)),
            "note_path": inventory_book.get("note_path"),
            "row_count": 0,
            "status": synthetic_status,
            "title_source": str(inventory_book.get("title_source") or ("source_note" if inventory_book.get("has_source_note") else "book_manifest")),
        }
    merged = {
        "book_title": manifest_book["book_title"],
        "definition_card_count": manifest_book["definition_card_count"],
        "document_id": manifest_book["document_id"],
        "extract_root": str(inventory_book.get("extract_root") or manifest_book["manifest"].get("extract_root") or ""),
        "has_source_note": bool(inventory_book.get("has_source_note") or manifest_book["has_source_note"]),
        "manifest": manifest_book["manifest"],
        "manifest_path": manifest_book["manifest_path"],
        "note_path": inventory_book.get("note_path") or manifest_book["note_path"],
        "row_count": manifest_book["row_count"],
        "status": manifest_book["status"],
        "title_source": manifest_book["title_source"],
    }
    if merged["status"] not in MATERIALIZED_STATUSES:
        inventory_status = str(inventory_book.get("status") or "")
        if inventory_status in {"ready", "partial"}:
            merged["status"] = "partial" if inventory_status == "partial" else "built"
    return merged


def build_page_book(*, wiki_root: Path, shelf_root: Path, merged_book: dict[str, Any]) -> dict[str, Any]:
    detail = audit_book_entry(shelf_root, merged_book)
    document_id = str(detail["document_id"])
    page_paths = {
        "book": str(DEFAULT_STUDY_PAGES_PROJECT / "books" / document_id / "README.md"),
        "chapter_root": str(DEFAULT_STUDY_PAGES_PROJECT / "books" / document_id / "chapters"),
    }
    book = dict(detail)
    book["page_paths"] = page_paths
    book["artifact_links"] = artifact_links(
        Path(str(merged_book.get("manifest_path") or "")),
        wiki_root / page_paths["book"],
    )
    book["note_enrichment"] = load_note_enrichment(
        wiki_root=wiki_root,
        note_path=book.get("note_path"),
        page_path=wiki_root / page_paths["book"],
    )
    book["chapters"] = build_chapters_for_book(
        book,
        shelf_root=shelf_root,
        wiki_root=wiki_root,
    )
    book["chapter_count"] = len(book["chapters"])
    return book


def apply_dashboard_progress_overlay(
    books: list[dict[str, Any]],
    selection_progress: dict[str, dict[str, Any]],
) -> None:
    for book in books:
        whole_key = study_selection_key(str(book["document_id"]), None)
        whole_progress = selection_progress.get(
            whole_key,
            {
                "vox_progress": idle_vox_progress(),
                "discoflash_progress": idle_discoflash_progress(),
            },
        )
        book["vox_progress"] = dict(whole_progress["vox_progress"])
        book["discoflash_progress"] = dict(whole_progress["discoflash_progress"])
        for chapter in book["chapters"]:
            chapter_key = study_selection_key(str(book["document_id"]), str(chapter["chapter_id"]))
            chapter_progress = selection_progress.get(
                chapter_key,
                {
                    "vox_progress": idle_vox_progress(),
                    "discoflash_progress": idle_discoflash_progress(),
                },
            )
            chapter["vox_progress"] = dict(chapter_progress["vox_progress"])
            chapter["discoflash_progress"] = dict(chapter_progress["discoflash_progress"])


def apply_dashboard_completion_overlay(
    books: list[dict[str, Any]],
    selection_completion: dict[str, dict[str, Any]],
) -> None:
    for book in books:
        vox_count = 0
        discoflash_count = 0
        combined_count = 0
        completed_chapters: list[dict[str, Any]] = []
        for chapter in book["chapters"]:
            chapter_key = study_selection_key(str(book["document_id"]), str(chapter["chapter_id"]))
            chapter_completion = selection_completion.get(
                chapter_key,
                {
                    "vox_completion": idle_completion(),
                    "discoflash_completion": idle_completion(),
                },
            )
            chapter["vox_completion"] = dict(chapter_completion["vox_completion"])
            chapter["discoflash_completion"] = dict(chapter_completion["discoflash_completion"])
            completion_status = chapter_completion_status(chapter)
            chapter["completion_status"] = completion_status
            chapter["combined_completion"] = chapter_has_completion(chapter)
            if chapter["vox_completion"]["status"] == "completed":
                vox_count += 1
            if chapter["discoflash_completion"]["status"] == "completed":
                discoflash_count += 1
            if chapter["combined_completion"]:
                combined_count += 1
                completed_chapters.append(chapter)
        book["completion_counts"] = {
            "vox": vox_count,
            "discoflash": discoflash_count,
            "combined": combined_count,
        }
        book["completed_chapters"] = sorted(
            completed_chapters,
            key=lambda chapter: completed_chapter_sort_key(chapter),
        )


def apply_dashboard_review_overlay(
    books: list[dict[str, Any]],
    selection_reviews: dict[str, dict[str, Any]],
) -> None:
    for book in books:
        for chapter in book["chapters"]:
            chapter_key = study_selection_key(str(book["document_id"]), str(chapter["chapter_id"]))
            chapter_review = selection_reviews.get(
                chapter_key,
                {
                    "vox_review": idle_review(),
                    "discoflash_review": idle_review(),
                },
            )
            chapter["vox_review"] = dict(chapter_review["vox_review"])
            chapter["discoflash_review"] = dict(chapter_review["discoflash_review"])
            chapter["combined_review"] = combine_chapter_review(chapter)


def apply_book_journal_overlay(
    books: list[dict[str, Any]],
    *,
    event_history: dict[str, Any],
    study_journal: dict[str, Any],
) -> None:
    recent_by_book: dict[str, list[dict[str, Any]]] = {}
    for entry in event_history["recent_activity"]["merged"] + event_history["recently_completed"]:
        document_id = str(entry.get("document_id") or "")
        if not document_id:
            continue
        recent_by_book.setdefault(document_id, []).append(entry)
    for book in books:
        document_id = str(book["document_id"])
        book_recent = sorted(recent_by_book.get(document_id, []), key=activity_entry_sort_key)[:5]
        journal_summary = dict(study_journal["per_book"].get(document_id, {}))
        book["recent_activity_entries"] = book_recent
        book["journal_summary"] = journal_summary


def chapter_has_completion(chapter: dict[str, Any]) -> bool:
    return (
        chapter.get("vox_completion", {}).get("status") == "completed"
        or chapter.get("discoflash_completion", {}).get("status") == "completed"
    )


def chapter_completion_status(chapter: dict[str, Any]) -> str:
    vox_completed = chapter.get("vox_completion", {}).get("status") == "completed"
    disco_completed = chapter.get("discoflash_completion", {}).get("status") == "completed"
    if vox_completed and disco_completed:
        return "both"
    if vox_completed:
        return "vox"
    if disco_completed:
        return "discoflash"
    return "no"


def completed_chapter_sort_key(chapter: dict[str, Any]) -> tuple[Any, ...]:
    timestamps = [
        chapter.get("vox_completion", {}).get("completed_at_utc"),
        chapter.get("discoflash_completion", {}).get("completed_at_utc"),
    ]
    newest = max(timestamps, key=parse_utc_timestamp)
    return (
        reverse_utc_sort_tuple(newest),
        normalize_name(chapter_label_text(chapter)),
    )


def format_completion_status(chapter: dict[str, Any]) -> str:
    return chapter_completion_status(chapter)


def artifact_links(manifest_path: Path, book_page_path: Path) -> dict[str, str]:
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = dict(manifest.get("files") or {})
    links = {}
    for key, path in files.items():
        if not path:
            continue
        target = Path(path)
        if not target.is_absolute():
            target = Path.cwd() / target
        target = target.resolve()
        links[key] = relative_link(book_page_path, target)
    discoflash_path = manifest_path.parent / "discoflash_definition_matching.txt"
    if discoflash_path.exists():
        links["discoflash"] = relative_link(book_page_path, discoflash_path.resolve())
    return links


def build_chapters_for_book(book: dict[str, Any], *, shelf_root: Path, wiki_root: Path) -> list[dict[str, Any]]:
    if book["status"] not in MATERIALIZED_STATUSES:
        return []
    book_root = shelf_root / str(book["document_id"])
    reader_path = book_root / DEFAULT_READER_STREAM
    if not reader_path.exists():
        return []
    rows = load_jsonl(reader_path)
    cards_path = book_root / DEFAULT_DEFINITION_CARDS
    cards = load_jsonl(cards_path) if cards_path.exists() else []
    chapters: list[dict[str, Any]] = []
    chapter_order: list[str] = []
    chapter_rows: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        chapter_id = str(row.get("chapter_id") or "")
        if not chapter_id:
            continue
        if chapter_id not in chapter_rows:
            chapter_order.append(chapter_id)
            chapter_rows[chapter_id] = []
        chapter_rows[chapter_id].append(row)
    for chapter_id in chapter_order:
        rows_for_chapter = chapter_rows[chapter_id]
        chapter_cards = [card for card in cards if str(card.get("chapter_id") or "") == chapter_id]
        first_row = rows_for_chapter[0]
        chapter_page_path = DEFAULT_STUDY_PAGES_PROJECT / "books" / str(book["document_id"]) / "chapters" / f"{chapter_id}.md"
        chapters.append(
            {
                "card_count": len(chapter_cards),
                "cards": chapter_cards,
                "chapter_id": chapter_id,
                "chapter_number": first_row.get("chapter_number"),
                "chapter_title": normalize_display_title(str(first_row.get("chapter_title") or chapter_id)),
                "page_path": str(chapter_page_path),
                "row_count": len(rows_for_chapter),
                "rows": rows_for_chapter,
            }
        )
    return chapters


def load_note_enrichment(*, wiki_root: Path, note_path: str | None, page_path: Path) -> dict[str, Any] | None:
    if not note_path:
        return None
    path = wiki_root / note_path
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    title_match = SOURCE_NOTE_TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else normalize_display_title(path.stem)
    why = extract_note_section(text, "Why This Source Matters")
    related = extract_related_concepts(text, note_path=note_path, page_path=page_path, wiki_root=wiki_root)
    return {
        "note_link": relative_link(page_path, path),
        "note_path": note_path,
        "related_concepts": related,
        "title": title,
        "why_this_source_matters": first_paragraph(why),
    }


def extract_note_section(text: str, heading: str) -> str:
    matches = list(SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1).strip() != heading:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[start:end].strip()
    return ""


def extract_related_concepts(text: str, *, note_path: str, page_path: Path, wiki_root: Path) -> list[dict[str, str]]:
    related = extract_note_section(text, "Related Concepts")
    links: list[dict[str, str]] = []
    note_file = wiki_root / note_path
    for line in related.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        match = MARKDOWN_LINK_RE.search(stripped)
        if not match:
            continue
        label, target = match.groups()
        target_path = (note_file.parent / target).resolve()
        links.append(
            {
                "label": label,
                "href": relative_link(page_path, target_path),
            }
        )
    return links


def render_hub_page(packet: dict[str, Any], *, wiki_root: Path) -> str:
    project_link = relative_link(
        wiki_root / DEFAULT_STUDY_PAGES_PROJECT / "README.md",
        wiki_root / "projects" / "library_operations" / "README.md",
    )
    source_hub_link = relative_link(
        wiki_root / DEFAULT_STUDY_PAGES_PROJECT / "README.md",
        wiki_root / "sources" / "math" / "README.md",
    )
    lines = [
        "# Math Library Hub",
        "",
        "This page is the generated navigation hub for the local math study corpus.",
        "",
        f"- generated_at_utc: `{packet['generated_at_utc']}`",
        f"- source_root: `{packet['source_root']}`",
        f"- study_output_dir: `{packet['output_dir']}`",
        f"- selection: `{packet['selection']}`",
        f"- total_books: `{packet['book_count']}`",
        f"- note_backed_books: `{packet['note_backed_count']}`",
        f"- manifest_only_books: `{packet['manifest_only_count']}`",
        f"- reader_ready_books: `{packet['reader_ready_count']}`",
        f"- flashcard_ready_books: `{packet['flashcard_ready_count']}`",
        f"- blocked_books: `{packet['blocked_count']}`",
        f"- definition_terms: `{packet['definition_index']['term_count']}` from `{packet['definition_index']['source_count']}` source cards",
        f"- named_results: `{packet['result_index']['term_count']}` from `{packet['result_index']['source_count']}` source cards",
        "",
        "## Navigation",
        "",
        f"- [Study Dashboard]({relative_link(wiki_root / DEFAULT_STUDY_PAGES_PROJECT / 'README.md', wiki_root / DEFAULT_STUDY_DASHBOARD_PROJECT / 'README.md')})",
        f"- [Math Source Notes]({source_hub_link})",
        f"- [Definitions Index]({relative_link(wiki_root / DEFAULT_STUDY_PAGES_PROJECT / 'README.md', wiki_root / DEFAULT_STUDY_PAGES_DEFINITIONS)})",
        f"- [Results Index]({relative_link(wiki_root / DEFAULT_STUDY_PAGES_PROJECT / 'README.md', wiki_root / DEFAULT_STUDY_PAGES_RESULTS)})",
        f"- [Library Operations Hub]({project_link})",
        "",
    ]
    lines.extend(render_book_list_section("Curated Note Books", [book for book in packet["books"] if book["has_source_note"]], wiki_root=wiki_root))
    lines.extend(render_book_list_section("Manifest-Only Books", [book for book in packet["books"] if not book["has_source_note"] and book["reader_ready"]], wiki_root=wiki_root))
    lines.extend(render_book_list_section("Blocked Books", [book for book in packet["books"] if not book["reader_ready"] or not book["flashcard_ready"]], wiki_root=wiki_root))
    return "\n".join(lines).strip() + "\n"


def render_continue_section(
    title: str,
    entries: list[dict[str, Any]],
    *,
    dashboard_path: Path,
    wiki_root: Path,
    app_name: str | None = None,
    fresh: bool = False,
    limit: int = 5,
) -> list[str]:
    lines = [f"### {title}", ""]
    selected = entries[:limit]
    if not selected:
        lines.extend(["- none", ""])
        return lines
    if fresh:
        lines.extend(
            [
                "| book | reason | vox fresh | discoflash fresh | math library |",
                "|---|---|---|---|---|",
            ]
        )
        for entry in selected:
            math_link = relative_link(dashboard_path, wiki_root / entry["math_library_path"])
            reason = "note-backed" if entry.get("has_source_note") else "manifest-only"
            vox_command = (
                f"`{entry['vox_commands']['fresh']}`" if entry["vox_commands"]["available"] else "unavailable"
            )
            discoflash_command = (
                f"`{entry['discoflash_commands']['fresh']}`"
                if entry["discoflash_commands"]["available"]
                else "unavailable"
            )
            lines.append(
                f"| {entry['book_title']} | {reason} | {vox_command} | {discoflash_command} | [overview]({math_link}) |"
            )
    else:
        lines.extend(
            [
                "| selection | progress | command | math library |",
                "|---|---|---|---|",
            ]
        )
        for entry in selected:
            label = entry["book_title"]
            if entry.get("chapter_label"):
                label = f"{label} — {entry['chapter_label']}"
            math_link = relative_link(dashboard_path, wiki_root / entry["math_library_path"])
            command_meta = entry[f"{app_name}_commands"] if app_name else {}
            command = f"`{command_meta['resume']}`" if command_meta.get("available") else "unavailable"
            lines.append(f"| {label} | {entry['progress_summary']} | {command} | [open]({math_link}) |")
    lines.append("")
    return lines


def render_recent_activity_section(
    title: str,
    entries: list[dict[str, Any]],
    *,
    dashboard_path: Path,
    wiki_root: Path,
    limit: int,
) -> list[str]:
    lines = [f"### {title}", ""]
    selected = entries[:limit]
    if not selected:
        lines.extend(["- none", ""])
        return lines
    lines.extend(
        [
            "| selection | updated | progress | command | math library |",
            "|---|---|---|---|---|",
        ]
    )
    for entry in selected:
        label = entry["book_title"]
        if entry.get("chapter_label"):
            label = f"{label} — {entry['chapter_label']}"
        updated = str(entry.get("updated_at_utc") or "unknown")
        math_link = relative_link(dashboard_path, wiki_root / entry["math_library_path"])
        preferred_command = entry.get("preferred_command")
        command = f"`{preferred_command}`" if preferred_command else "unavailable"
        lines.append(
            f"| {label} | `{updated}` | {entry['progress_summary']} | {command} | [open]({math_link}) |"
        )
    lines.append("")
    return lines


def render_recently_completed_section(
    entries: list[dict[str, Any]],
    *,
    dashboard_path: Path,
    wiki_root: Path,
    limit: int = 10,
) -> list[str]:
    lines = ["## Recently Completed", ""]
    selected = entries[:limit]
    if not selected:
        lines.extend(["- none", ""])
        return lines
    lines.extend(
        [
            "| selection | completed | summary | command | math library |",
            "|---|---|---|---|---|",
        ]
    )
    for entry in selected:
        label = entry["book_title"]
        if entry.get("chapter_label"):
            label = f"{label} — {entry['chapter_label']}"
        updated = str(entry.get("updated_at_utc") or "unknown")
        math_link = relative_link(dashboard_path, wiki_root / entry["math_library_path"])
        preferred_command = entry.get("preferred_command")
        command = f"`{preferred_command}`" if preferred_command else "unavailable"
        lines.append(
            f"| {label} | `{updated}` | {entry['progress_summary']} | {command} | [open]({math_link}) |"
        )
    lines.append("")
    return lines


def render_completed_chapters_section(book: dict[str, Any], *, dashboard_path: Path, wiki_root: Path) -> list[str]:
    lines = ["## Completed Chapters", ""]
    if not book["completed_chapters"]:
        lines.extend(["- none", ""])
        return lines
    lines.extend(
        [
            "| chapter | completed via | completed_at | review | vox fresh | discoflash fresh | math library |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for chapter in book["completed_chapters"]:
        selection_key = study_selection_key(str(book["document_id"]), str(chapter["chapter_id"]))
        commands = study_dashboard_launch_metadata(selection_key)
        math_link = relative_link(dashboard_path, wiki_root / chapter["page_path"]) if chapter.get("page_path") else None
        completed_at = chapter_latest_completion_timestamp(chapter)
        math_cell = f"[chapter]({math_link})" if math_link else "n/a"
        vox_command = f"`{commands['vox']['fresh']}`" if commands["vox"]["available"] else "unavailable"
        if int(chapter.get("card_count") or 0) > 0 and commands["discoflash"]["available"]:
            discoflash_command = f"`{commands['discoflash']['fresh']}`"
        else:
            discoflash_command = "n/a"
        lines.append(
            f"| {chapter_label_text(chapter)} | `{format_completion_status(chapter)}` | `{completed_at or 'unknown'}` | "
            f"`{format_review_status(chapter.get('combined_review', {}))}` | {vox_command} | {discoflash_command} | {math_cell} |"
        )
    lines.append("")
    return lines


def render_study_journal_section(packet: dict[str, Any], *, dashboard_path: Path, wiki_root: Path) -> list[str]:
    journal = packet["study_journal"]
    summary = journal["summary"]
    lines = [
        "## Study Journal",
        "",
        f"- books_with_active_resume: `{summary['books_with_active_resume']}`",
        f"- books_with_completion: `{summary['books_with_completion']}`",
        f"- fully_completed_books: `{summary['fully_completed_books']}`",
        f"- total_completed_chapters: `{summary['total_completed_chapters']}`",
        f"- vox_last_activity_at_utc: `{summary['vox_last_activity_at_utc'] or 'none'}`",
        f"- discoflash_last_activity_at_utc: `{summary['discoflash_last_activity_at_utc'] or 'none'}`",
        "",
        "### Books In Progress",
        "",
    ]
    entries = journal["books_in_progress"][:10]
    if not entries:
        lines.extend(["- none", ""])
        return lines
    lines.extend(
        [
            "| book | completed | active vox | active discoflash | last activity | next incomplete | math library |",
            "|---|---:|---|---|---|---|---|",
        ]
    )
    for entry in entries:
        math_link = relative_link(dashboard_path, wiki_root / entry["math_library_path"])
        lines.append(
            f"| {entry['book_title']} | {entry['completed_chapters']}/{entry['chapter_count']} | "
            f"`{'yes' if entry['active_vox'] else 'no'}` | "
            f"`{'yes' if entry['active_discoflash'] else 'no'}` | "
            f"`{entry['last_activity_at_utc'] or entry['last_completion_at_utc'] or 'none'}` | "
            f"{entry['next_incomplete_chapter'] or 'none'} | [overview]({math_link}) |"
        )
    lines.append("")
    return lines


def render_review_queue_section(entries: list[dict[str, Any]], *, dashboard_path: Path, wiki_root: Path, limit: int = 5) -> list[str]:
    lines = ["## Review Queue", ""]
    selected = entries[:limit]
    if not selected:
        lines.extend(["- none", ""])
        return lines
    lines.extend(
        [
            "| book | chapter | due via | stage | next_due | vox fresh | discoflash fresh | math library |",
            "|---|---|---|---:|---|---|---|---|",
        ]
    )
    for entry in selected:
        math_link = relative_link(dashboard_path, wiki_root / entry["math_library_path"])
        vox_command = f"`{entry['vox_commands']['fresh']}`" if entry["vox_commands"]["available"] else "unavailable"
        if entry["discoflash_supported"] and entry["discoflash_commands"]["available"]:
            discoflash_command = f"`{entry['discoflash_commands']['fresh']}`"
        else:
            discoflash_command = "n/a"
        lines.append(
            f"| {entry['book_title']} | {entry['chapter_label']} | `{entry['source_app']}` | "
            f"`{entry['stage_index'] if entry['stage_index'] is not None else 'n/a'}` | "
            f"`{entry['next_due_at_utc'] or 'unknown'}` | {vox_command} | {discoflash_command} | [chapter]({math_link}) |"
        )
    lines.append("")
    return lines


def render_book_recent_activity_section(book: dict[str, Any], *, dashboard_path: Path, wiki_root: Path, limit: int = 5) -> list[str]:
    lines = ["## Recent Activity For This Book", ""]
    entries = list(book.get("recent_activity_entries") or [])[:limit]
    if not entries:
        lines.extend(["- none", ""])
        return lines
    lines.extend(
        [
            "| app | event | updated | progress | command | math library |",
            "|---|---|---|---|---|---|",
        ]
    )
    for entry in entries:
        math_link = relative_link(dashboard_path, wiki_root / entry["math_library_path"])
        preferred_command = entry.get("preferred_command")
        command = f"`{preferred_command}`" if preferred_command else "unavailable"
        lines.append(
            f"| `{entry['app']}` | `{entry['event_type']}` | `{entry['updated_at_utc'] or 'unknown'}` | "
            f"{entry['progress_summary']} | {command} | [open]({math_link}) |"
        )
    lines.append("")
    return lines


def render_next_up_section(entries: list[dict[str, Any]], *, dashboard_path: Path, wiki_root: Path, limit: int = 5) -> list[str]:
    lines = ["## Next Up", "", "### Suggested next chapters", ""]
    selected = entries[:limit]
    if not selected:
        lines.extend(["- none", ""])
        return lines
    lines.extend(
        [
            "| book | next chapter | why | vox fresh | discoflash fresh | math library |",
            "|---|---|---|---|---|---|",
        ]
    )
    for entry in selected:
        math_link = relative_link(dashboard_path, wiki_root / entry["math_library_path"])
        vox_command = f"`{entry['vox_commands']['fresh']}`" if entry["vox_commands"]["available"] else "unavailable"
        if entry["discoflash_supported"] and entry["discoflash_commands"]["available"]:
            discoflash_command = f"`{entry['discoflash_commands']['fresh']}`"
        else:
            discoflash_command = "n/a"
        lines.append(
            f"| {entry['book_title']} | {entry['target_chapter_label']} | {entry['reason']} | "
            f"{vox_command} | {discoflash_command} | [chapter]({math_link}) |"
        )
    lines.append("")
    return lines


def render_study_dashboard_hub(packet: dict[str, Any], *, wiki_root: Path) -> str:
    dashboard_path = wiki_root / DEFAULT_STUDY_DASHBOARD_PROJECT / "README.md"
    app_metadata = study_dashboard_launch_metadata("sample::__entire__")
    app_progress = packet["app_progress"]
    app_completion = packet["app_completion"]
    app_review = packet["app_review"]
    continue_studying = packet["continue_studying"]
    recent_activity = packet["recent_activity"]
    recently_completed = packet["recently_completed"]
    next_up = packet["next_up"]
    lines = [
        "# Study Dashboard",
        "",
        "This page is the generated cross-app study dashboard for the local math corpus.",
        "",
        f"- generated_at_utc: `{packet['generated_at_utc']}`",
        f"- total_books: `{packet['book_count']}`",
        f"- total_chapters: `{sum(book['chapter_count'] for book in packet['books'])}`",
        f"- reader_ready_books: `{packet['reader_ready_count']}`",
        f"- flashcard_ready_books: `{packet['flashcard_ready_count']}`",
        f"- note_backed_books: `{packet['note_backed_count']}`",
        f"- manifest_only_books: `{packet['manifest_only_count']}`",
        "",
        "## Navigation",
        "",
        f"- [Math Library Hub]({relative_link(dashboard_path, wiki_root / DEFAULT_STUDY_PAGES_PROJECT / 'README.md')})",
        f"- [Definitions Index]({relative_link(dashboard_path, wiki_root / DEFAULT_STUDY_PAGES_DEFINITIONS)})",
        f"- [Results Index]({relative_link(dashboard_path, wiki_root / DEFAULT_STUDY_PAGES_RESULTS)})",
        "",
        "## Cross-App Selection IDs",
        "",
        "- Whole book: `<document_id>::__entire__`",
        "- Chapter: `<document_id>::<chapter_id>`",
        "- These IDs are stable across the generated wiki, `vox`, and `discoflash`.",
        f"- `vox` launch commands: `{'available' if app_metadata['vox']['available'] else 'unavailable'}`",
        f"- `discoflash` launch commands: `{'available' if app_metadata['discoflash']['available'] else 'unavailable'}`",
        f"- `vox` resumable selections: `{app_progress['vox']['resume_available_count']}`",
        f"- `discoflash` resumable selections: `{app_progress['discoflash']['resume_available_count']}`",
        f"- `vox` last_selection_key: `{app_progress['vox']['last_selection_key'] or 'none'}`",
        f"- `discoflash` last_selection_key: `{app_progress['discoflash']['last_selection_key'] or 'none'}`",
        f"- `vox` completed chapters: `{app_completion['vox']['completed_count']}`",
        f"- `discoflash` completed chapters: `{app_completion['discoflash']['completed_count']}`",
        f"- combined completed chapters: `{packet['completed_chapter_count']}`",
        f"- `vox` due reviews: `{app_review['vox']['due_review_count']}`",
        f"- `discoflash` due reviews: `{app_review['discoflash']['due_review_count']}`",
        f"- combined due reviews: `{len(packet['review_queue'])}`",
        f"- fully completed books: `{packet['fully_completed_book_count']}`",
        "",
        "## Continue Studying",
        "",
    ]
    lines.extend(
        render_continue_section(
            "Continue in vox",
            continue_studying["vox_resume"],
            dashboard_path=dashboard_path,
            wiki_root=wiki_root,
            app_name="vox",
        )
    )
    lines.extend(
        render_continue_section(
            "Continue in discoflash",
            continue_studying["discoflash_resume"],
            dashboard_path=dashboard_path,
            wiki_root=wiki_root,
            app_name="discoflash",
        )
    )
    lines.extend(
        render_continue_section(
            "Start Fresh",
            continue_studying["fresh_recommendations"],
            dashboard_path=dashboard_path,
            wiki_root=wiki_root,
            fresh=True,
        )
    )
    lines.extend(render_study_journal_section(packet, dashboard_path=dashboard_path, wiki_root=wiki_root))
    lines.extend(
        [
            "## Recent Activity",
            "",
            "This reflects append-only local study events. Older activity from before event logging was added will not appear here.",
            "",
        ]
    )
    lines.extend(
        render_recent_activity_section(
            "Recent in vox",
            recent_activity["vox"],
            dashboard_path=dashboard_path,
            wiki_root=wiki_root,
            limit=5,
        )
    )
    lines.extend(
        render_recent_activity_section(
            "Recent in discoflash",
            recent_activity["discoflash"],
            dashboard_path=dashboard_path,
            wiki_root=wiki_root,
            limit=5,
        )
    )
    lines.extend(
        render_recent_activity_section(
            "Recent across apps",
            recent_activity["merged"],
            dashboard_path=dashboard_path,
            wiki_root=wiki_root,
            limit=10,
        )
    )
    lines.extend(render_recently_completed_section(recently_completed, dashboard_path=dashboard_path, wiki_root=wiki_root))
    lines.extend(render_next_up_section(next_up, dashboard_path=dashboard_path, wiki_root=wiki_root))
    lines.extend(render_review_queue_section(packet["review_queue"], dashboard_path=dashboard_path, wiki_root=wiki_root))
    lines.extend(
        [
        "## Books",
        "",
        "| book | document_id | chapters | rows | cards | reader | flashcards | note | whole-book selection | math library |",
        "|---|---|---:|---:|---:|---|---|---|---|---|",
        ]
    )
    for book in packet["books"]:
        dashboard_book = dashboard_book_page_path(document_id=str(book["document_id"]))
        dashboard_link = relative_link(dashboard_path, wiki_root / dashboard_book)
        math_library_link = relative_link(dashboard_path, wiki_root / book["page_paths"]["book"])
        lines.append(
            "| "
            f"[{book['book_title']}]({dashboard_link}) | "
            f"`{book['document_id']}` | "
            f"{book['chapter_count']} | "
            f"{book['row_count']} | "
            f"{book['definition_card_count']} | "
            f"`{'yes' if book['reader_ready'] else 'no'}` | "
            f"`{'yes' if book['flashcard_ready'] else 'no'}` | "
            f"`{'yes' if book['has_source_note'] else 'no'}` | "
            f"`{study_selection_key(str(book['document_id']), None)}` | "
            f"[overview]({math_library_link}) |"
        )
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_study_dashboard_book_page(book: dict[str, Any], *, wiki_root: Path) -> str:
    dashboard_path = wiki_root / dashboard_book_page_path(document_id=str(book["document_id"]))
    math_library_book_path = wiki_root / book["page_paths"]["book"]
    whole_book_key = study_selection_key(str(book["document_id"]), None)
    whole_book_commands = study_dashboard_launch_metadata(whole_book_key)
    lines = [
        f"# {book['book_title']}",
        "",
        f"- document_id: `{book['document_id']}`",
        f"- status: `{book['status']}`",
        f"- qa_status: `{book['qa_status']}`",
        f"- reader_ready: `{'true' if book['reader_ready'] else 'false'}`",
        f"- flashcard_ready: `{'true' if book['flashcard_ready'] else 'false'}`",
        f"- has_source_note: `{'true' if book['has_source_note'] else 'false'}`",
        f"- row_count: `{book['row_count']}`",
        f"- definition_card_count: `{book['definition_card_count']}`",
        f"- completed_chapters: `{book['completion_counts']['combined']}` / `{book['chapter_count']}`",
        "",
        "## Navigation",
        "",
        f"- [Study Dashboard]({relative_link(dashboard_path, wiki_root / DEFAULT_STUDY_DASHBOARD_PROJECT / 'README.md')})",
        f"- [Math Library Overview]({relative_link(dashboard_path, math_library_book_path)})",
        "",
        "## Study Status",
        "",
        f"- completed_chapters: `{book['completion_counts']['combined']}` / `{book['chapter_count']}`",
        f"- active_vox: `{'true' if book.get('journal_summary', {}).get('active_vox') else 'false'}`",
        f"- active_discoflash: `{'true' if book.get('journal_summary', {}).get('active_discoflash') else 'false'}`",
        f"- first_incomplete_chapter: `{book.get('journal_summary', {}).get('next_incomplete_chapter') or 'none'}`",
        f"- last_activity_at_utc: `{book.get('journal_summary', {}).get('last_activity_at_utc') or 'none'}`",
        f"- last_completion_at_utc: `{book.get('journal_summary', {}).get('last_completion_at_utc') or 'none'}`",
        f"- due_reviews: `{sum(1 for chapter in book['chapters'] if chapter.get('combined_review', {}).get('due_now'))}`",
        "",
        "## Whole-Book Selection",
        "",
        f"- selection_key: `{whole_book_key}`",
        f"- `vox` progress: {format_vox_progress(book['vox_progress'])}",
        f"- `discoflash` progress: {format_discoflash_progress(book['discoflash_progress'])}",
        f"- completed via `vox`: `{book['completion_counts']['vox']}` chapters",
        f"- completed via `discoflash`: `{book['completion_counts']['discoflash']}` chapters",
        f"- combined completed: `{book['completion_counts']['combined']}` / `{book['chapter_count']}` chapters",
        "",
        "## App Launch Commands",
        "",
        f"- `vox` fresh: `{whole_book_commands['vox']['fresh']}`" if whole_book_commands["vox"]["available"] else "- `vox` fresh: unavailable",
        f"- `vox` resume: `{whole_book_commands['vox']['resume']}`" if whole_book_commands["vox"]["available"] else "- `vox` resume: unavailable",
        f"- `discoflash` fresh: `{whole_book_commands['discoflash']['fresh']}`" if whole_book_commands["discoflash"]["available"] else "- `discoflash` fresh: unavailable",
        f"- `discoflash` resume: `{whole_book_commands['discoflash']['resume']}`" if whole_book_commands["discoflash"]["available"] else "- `discoflash` resume: unavailable",
        "- Fresh launch is the default. Add `--resume` to use saved app state for the same selection when it exists.",
        "",
        "## Chapters",
        "",
        "| chapter | rows | cards | selection_key | completed | review | vox | discoflash | vox fresh | discoflash fresh | math library page |",
        "|---|---:|---:|---|---|---|---|---|---|---|---|",
    ]
    for chapter in book["chapters"]:
        chapter_key = study_selection_key(str(book["document_id"]), str(chapter["chapter_id"]))
        chapter_commands = study_dashboard_launch_metadata(chapter_key)
        chapter_link = relative_link(dashboard_path, wiki_root / chapter["page_path"]) if chapter.get("page_path") else ""
        page_cell = f"[chapter]({chapter_link})" if chapter_link else "n/a"
        vox_cell = f"`{chapter_commands['vox']['fresh']}`" if chapter_commands["vox"]["available"] else "unavailable"
        discoflash_cell = (
            f"`{chapter_commands['discoflash']['fresh']}`" if chapter_commands["discoflash"]["available"] else "unavailable"
        )
        lines.append(
            f"| {chapter_label_text(chapter)} | {chapter['row_count']} | {chapter['card_count']} | `{chapter_key}` | `{format_completion_status(chapter)}` | `{format_review_status(chapter.get('combined_review', {}))}` | {format_vox_progress(chapter['vox_progress'])} | {format_discoflash_progress(chapter['discoflash_progress'])} | {vox_cell} | {discoflash_cell} | {page_cell} |"
        )
    if not book["chapters"]:
        lines.extend(["- no chapters available", ""])
    else:
        lines.append("")
    lines.extend(render_book_recent_activity_section(book, dashboard_path=dashboard_path, wiki_root=wiki_root))
    lines.extend(render_completed_chapters_section(book, dashboard_path=dashboard_path, wiki_root=wiki_root))
    return "\n".join(lines).strip() + "\n"


def render_book_list_section(title: str, books: list[dict[str, Any]], *, wiki_root: Path) -> list[str]:
    lines = [f"## {title}", ""]
    if not books:
        lines.extend(["- none", ""])
        return lines
    lines.extend(
        [
            "| book | status | readiness | chapters | cards | note |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for book in books:
        book_link = relative_link(
            wiki_root / DEFAULT_STUDY_PAGES_PROJECT / "README.md",
            wiki_root / book["page_paths"]["book"],
        )
        readiness = f"reader=`{'yes' if book['reader_ready'] else 'no'}` / flashcards=`{'yes' if book['flashcard_ready'] else 'no'}`"
        note_flag = "yes" if book["has_source_note"] else "no"
        lines.append(
            f"| [{book['book_title']}]({book_link}) | `{book['status']}` | {readiness} | {book['chapter_count']} | {book['definition_card_count']} | {note_flag} |"
        )
    lines.append("")
    return lines


def render_book_page(book: dict[str, Any], *, wiki_root: Path) -> str:
    lines = [
        f"# {book['book_title']}",
        "",
        f"- document_id: `{book['document_id']}`",
        f"- status: `{book['status']}`",
        f"- qa_status: `{book['qa_status']}`",
        f"- title_source: `{book['title_source']}`",
        f"- has_source_note: `{'true' if book['has_source_note'] else 'false'}`",
        f"- reader_ready: `{'true' if book['reader_ready'] else 'false'}`",
        f"- flashcard_ready: `{'true' if book['flashcard_ready'] else 'false'}`",
        f"- chapter_count: `{book['chapter_count']}`",
        f"- row_count: `{book['row_count']}`",
        f"- definition_card_count: `{book['definition_card_count']}`",
        "",
        f"- [Math Library Hub]({relative_link(wiki_root / book['page_paths']['book'], wiki_root / DEFAULT_STUDY_PAGES_PROJECT / 'README.md')})",
        f"- [Definitions Index]({relative_link(wiki_root / book['page_paths']['book'], wiki_root / DEFAULT_STUDY_PAGES_DEFINITIONS)})",
        f"- [Results Index]({relative_link(wiki_root / book['page_paths']['book'], wiki_root / DEFAULT_STUDY_PAGES_RESULTS)})",
    ]
    if book["artifact_links"]:
        lines.extend(["", "## Study Artifacts", ""])
        for label, href in sorted(book["artifact_links"].items()):
            lines.append(f"- [{label.replace('_', ' ').title()}]({href})")
    if book["blocked_reasons"]["reader"] or book["blocked_reasons"]["flashcard"]:
        lines.extend(["", "## Readiness", ""])
        lines.append(f"- reader_blocked_reasons: `{', '.join(book['blocked_reasons']['reader']) or 'none'}`")
        lines.append(f"- flashcard_blocked_reasons: `{', '.join(book['blocked_reasons']['flashcard']) or 'none'}`")
    note = book.get("note_enrichment")
    if note:
        lines.extend(
            [
                "",
                "## Curated Source Note",
                "",
                f"- [Source Note]({note['note_link']})",
            ]
        )
        if note["why_this_source_matters"]:
            lines.extend(["", "### Why This Source Matters", "", note["why_this_source_matters"]])
        if note["related_concepts"]:
            lines.extend(["", "### Related Concepts", ""])
            for concept in note["related_concepts"]:
                lines.append(f"- [{concept['label']}]({concept['href']})")
    if book["chapters"]:
        lines.extend(["", "## Chapters", "", "| chapter | rows | cards | page |", "|---|---:|---:|---|"])
        for chapter in book["chapters"]:
            chapter_href = relative_link(wiki_root / book["page_paths"]["book"], wiki_root / chapter["page_path"])
            chapter_label = chapter_label_text(chapter)
            lines.append(
                f"| {chapter_label} | {chapter['row_count']} | {chapter['card_count']} | [open]({chapter_href}) |"
            )
    else:
        lines.extend(["", "## Chapters", "", "- no chapter pages generated"])
    return "\n".join(lines).strip() + "\n"


def render_chapter_page(book: dict[str, Any], chapter: dict[str, Any], *, wiki_root: Path) -> str:
    visible_cards = [
        card
        for card in chapter["cards"]
        if is_display_quality_card_term(
            str(card.get("term") or ""),
            source_kind=str(card.get("card_source_kind") or ""),
        )
    ]
    lines = [
        f"# {book['book_title']} — {chapter_label_text(chapter)}",
        "",
        f"- document_id: `{book['document_id']}`",
        f"- chapter_id: `{chapter['chapter_id']}`",
        f"- row_count: `{chapter['row_count']}`",
        f"- definition_card_count: `{chapter['card_count']}`",
        "",
        f"- [Book Overview]({relative_link(wiki_root / chapter['page_path'], wiki_root / book['page_paths']['book'])})",
        f"- [Math Library Hub]({relative_link(wiki_root / chapter['page_path'], wiki_root / DEFAULT_STUDY_PAGES_PROJECT / 'README.md')})",
    ]
    if visible_cards:
        lines.extend(["", "## Key Definitions", ""])
        for card in visible_cards:
            lines.append(f"- **{card['term']}** (`{card.get('card_source_kind', 'unknown')}`)")
    lines.extend(["", "## Reader Text", ""])
    last_heading = None
    for row in chapter["rows"]:
        heading = section_heading_for_row(row, chapter["chapter_title"])
        if heading and heading != last_heading:
            lines.extend(["", f"### {heading}", ""])
            last_heading = heading
        lines.append(str(row.get("reader_text") or "").strip())
        lines.append("")
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def section_heading_for_row(row: dict[str, Any], chapter_title: str) -> str:
    title_path = str(row.get("title_path") or "").strip()
    if not title_path:
        return ""
    parts = [normalize_display_title(part.strip()) for part in title_path.split(">") if part.strip()]
    if parts and normalize_name(parts[0]) == normalize_name(chapter_title):
        parts = parts[1:]
    if not parts:
        return ""
    return " > ".join(parts)


def build_term_index(books: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    allowed_source_kinds = DEFINITION_SOURCE_KINDS if kind == "definitions" else RESULT_SOURCE_KINDS
    entries: list[dict[str, Any]] = []
    for book in books:
        chapter_lookup = {str(chapter["chapter_id"]): chapter for chapter in book["chapters"]}
        for chapter in book["chapters"]:
            for card in chapter["cards"]:
                source_kind = str(card.get("card_source_kind") or "")
                if source_kind not in allowed_source_kinds:
                    continue
                term = str(card.get("term") or "").strip()
                if not is_display_quality_card_term(term, source_kind=source_kind):
                    continue
                definition = compact_definition_excerpt(str(card.get("definition") or ""))
                if not definition:
                    continue
                chapter_id = str(card.get("chapter_id") or chapter["chapter_id"] or "")
                chapter_detail = chapter_lookup.get(chapter_id, chapter)
                entries.append(
                    {
                        "book_title": str(book["book_title"]),
                        "book_page_path": str(book["page_paths"]["book"]),
                        "chapter_label": chapter_label_text(chapter_detail) if chapter_detail else "Book-level",
                        "chapter_number": chapter_detail.get("chapter_number") if chapter_detail else None,
                        "chapter_page_path": str(chapter_detail.get("page_path") or "") if chapter_detail else "",
                        "definition": definition,
                        "document_id": str(book["document_id"]),
                        "source_kind": source_kind,
                        "term": term,
                    }
                )

    groups: dict[str, dict[str, Any]] = {}
    for entry in entries:
        normalized_key = normalize_term_key(entry["term"])
        if not normalized_key:
            continue
        group = groups.setdefault(
            normalized_key,
            {
                "display_term": entry["term"],
                "normalized_key": normalized_key,
                "sources": [],
            },
        )
        if display_term_sort_key(entry["term"]) < display_term_sort_key(str(group["display_term"])):
            group["display_term"] = entry["term"]
        group["sources"].append(entry)

    sorted_groups = sorted(
        groups.values(),
        key=lambda item: (normalize_name(str(item["display_term"])), str(item["display_term"])),
    )
    for group in sorted_groups:
        group["sources"].sort(
            key=lambda item: (
                normalize_name(str(item["book_title"])),
                item["chapter_number"] if item["chapter_number"] is not None else 10**9,
                normalize_name(str(item["chapter_label"])),
                str(item["source_kind"]),
                normalize_name(str(item["definition"])),
            )
        )
        group["book_count"] = len({str(source["document_id"]) for source in group["sources"]})
        group["source_count"] = len(group["sources"])
        group["letter"] = letter_bucket(str(group["display_term"]))

    by_letter: dict[str, dict[str, Any]] = {}
    for group in sorted_groups:
        letter = str(group["letter"])
        bucket = by_letter.setdefault(
            letter,
            {
                "entries": [],
                "page_path": str(letter_page_path(kind, letter)),
            },
        )
        bucket["entries"].append(group)

    return {
        "by_letter": by_letter,
        "groups": sorted_groups,
        "source_count": len(entries),
        "term_count": len(sorted_groups),
    }


def render_term_index_hub(packet: dict[str, Any], *, kind: str, wiki_root: Path) -> str:
    index = packet["definition_index"] if kind == "definitions" else packet["result_index"]
    title = "Definitions Index" if kind == "definitions" else "Results Index"
    description = (
        "Cross-book glossary view built from generated definition cards."
        if kind == "definitions"
        else "Cross-book named theorem and result index built from generated result cards."
    )
    hub_path = wiki_root / (DEFAULT_STUDY_PAGES_DEFINITIONS if kind == "definitions" else DEFAULT_STUDY_PAGES_RESULTS)
    lines = [
        f"# {title}",
        "",
        description,
        "",
        f"- generated_at_utc: `{packet['generated_at_utc']}`",
        f"- normalized_terms: `{index['term_count']}`",
        f"- source_cards: `{index['source_count']}`",
        "",
        "## Navigation",
        "",
        f"- [Math Library Hub]({relative_link(hub_path, wiki_root / DEFAULT_STUDY_PAGES_PROJECT / 'README.md')})",
        "",
        "## By Letter",
        "",
    ]
    if not index["by_letter"]:
        lines.extend(["- none", ""])
        return "\n".join(lines).strip() + "\n"
    for letter, group in sorted(index["by_letter"].items()):
        href = relative_link(hub_path, wiki_root / group["page_path"])
        lines.append(f"- [{letter}]({href}) ({len(group['entries'])} terms)")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_term_index_letter_page(packet: dict[str, Any], *, kind: str, letter: str, wiki_root: Path) -> str:
    index = packet["definition_index"] if kind == "definitions" else packet["result_index"]
    bucket = index["by_letter"][letter]
    page_path = wiki_root / bucket["page_path"]
    hub_path = wiki_root / (DEFAULT_STUDY_PAGES_DEFINITIONS if kind == "definitions" else DEFAULT_STUDY_PAGES_RESULTS)
    title = "Definitions" if kind == "definitions" else "Results"
    lines = [
        f"# {title} — {letter}",
        "",
        f"- entry_count: `{len(bucket['entries'])}`",
        "",
        "## Navigation",
        "",
        f"- [{title} Index]({relative_link(page_path, hub_path)})",
        f"- [Math Library Hub]({relative_link(page_path, wiki_root / DEFAULT_STUDY_PAGES_PROJECT / 'README.md')})",
        "",
    ]
    for entry in bucket["entries"]:
        lines.extend(
            [
                f"## {entry['display_term']}",
                "",
                f"- source_count: `{entry['source_count']}`",
                f"- book_count: `{entry['book_count']}`",
                "",
            ]
        )
        for source in entry["sources"]:
            book_link = relative_link(page_path, wiki_root / source["book_page_path"])
            if source["chapter_page_path"]:
                chapter_link = relative_link(page_path, wiki_root / source["chapter_page_path"])
                location = f"[{source['book_title']}]({book_link}) / [{source['chapter_label']}]({chapter_link})"
            else:
                location = f"[{source['book_title']}]({book_link}) / {source['chapter_label']}"
            lines.append(f"- {location} — `{source['source_kind']}` — {source['definition']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def normalize_term_key(term: str) -> str:
    cleaned = re.sub(r"\s+", " ", term.strip().strip("`'\".,;:()[]{}")).strip()
    return normalize_name(cleaned)


def display_term_sort_key(term: str) -> tuple[int, int, str]:
    cleaned = re.sub(r"\s+", " ", term.strip())
    return (len(cleaned), sum(1 for char in cleaned if char.isalnum()), normalize_name(cleaned))


def compact_definition_excerpt(text: str, *, limit: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    truncated = cleaned[: limit - 3].rstrip(" ,;:")
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated + "..."


def letter_bucket(term: str) -> str:
    stripped = term.strip()
    if not stripped:
        return "#"
    first = stripped[0].upper()
    if "A" <= first <= "Z":
        return first
    if first.isdigit():
        return "0-9"
    return "#"


def letter_page_path(kind: str, letter: str) -> Path:
    root = DEFAULT_STUDY_PAGES_DEFINITIONS_BY_LETTER if kind == "definitions" else DEFAULT_STUDY_PAGES_RESULTS_BY_LETTER
    slug = letter.lower().replace("#", "symbols")
    return root / f"{slug}.md"


def chapter_label_text(chapter: dict[str, Any]) -> str:
    return format_chapter_label(
        chapter_number=chapter.get("chapter_number"),
        chapter_title=str(chapter.get("chapter_title") or chapter.get("chapter_id") or ""),
    )


def study_selection_key(document_id: str, chapter_id: str | None) -> str:
    return f"{document_id}::{chapter_id or '__entire__'}"


def dashboard_book_page_path(*, document_id: str) -> Path:
    return DEFAULT_STUDY_DASHBOARD_PROJECT / "books" / f"{document_id}.md"


def first_paragraph(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    return cleaned.split("\n\n", 1)[0].strip()


def navigation_index(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "book_count": packet["book_count"],
        "books": [
            {
                "book_title": book["book_title"],
                "chapter_count": book["chapter_count"],
                "definition_card_count": book["definition_card_count"],
                "document_id": book["document_id"],
                "flashcard_ready": book["flashcard_ready"],
                "has_source_note": book["has_source_note"],
                "note_path": book.get("note_path"),
                "page_paths": {
                    "book": book["page_paths"]["book"],
                    "chapters": [chapter["page_path"] for chapter in book["chapters"] if chapter.get("page_path")],
                },
                "qa_status": book["qa_status"],
                "reader_ready": book["reader_ready"],
                "row_count": book["row_count"],
                "status": book["status"],
            }
            for book in packet["books"]
        ],
        "definition_source_count": packet["definition_index"]["source_count"],
        "definition_term_count": packet["definition_index"]["term_count"],
        "flashcard_ready_count": packet["flashcard_ready_count"],
        "generated_at_utc": packet["generated_at_utc"],
        "index_paths": {
            "dashboard": str(DEFAULT_STUDY_DASHBOARD_PROJECT / "README.md"),
            "definitions": str(DEFAULT_STUDY_PAGES_DEFINITIONS),
            "results": str(DEFAULT_STUDY_PAGES_RESULTS),
        },
        "note_backed_count": packet["note_backed_count"],
        "reader_ready_count": packet["reader_ready_count"],
        "result_source_count": packet["result_index"]["source_count"],
        "result_term_count": packet["result_index"]["term_count"],
        "selection": packet["selection"],
        "shelf": packet["shelf"],
        "source_root": packet["source_root"],
        "wiki_root": packet["wiki_root"],
    }


def study_dashboard_navigation_index(packet: dict[str, Any]) -> dict[str, Any]:
    app_roots = study_dashboard_app_roots()
    return {
        "apps": {
            name: {
                "available": bool(Path(info["root"]).exists() and Path(info["entrypoint"]).exists()),
                "last_selection_key": packet["app_progress"][name]["last_selection_key"],
                "last_reviewed_selection_key": packet["app_review"][name]["last_reviewed_selection_key"],
                "load_error": packet["app_progress"][name]["load_error"],
                "progress_path": packet["app_progress"][name]["progress_path"],
                "review_path": packet["app_review"][name]["review_path"],
                "resume_available_count": packet["app_progress"][name]["resume_available_count"],
                "due_review_count": packet["app_review"][name]["due_review_count"],
                "status": packet["app_progress"][name]["status"],
            }
            for name, info in app_roots.items()
        },
        "app_completion": packet["app_completion"],
        "app_review": packet["app_review"],
        "book_count": packet["book_count"],
        "books": [
            {
                "book_title": book["book_title"],
                "chapter_count": book["chapter_count"],
                "chapters": [
                    {
                        "card_count": chapter["card_count"],
                        "chapter_id": chapter["chapter_id"],
                        "chapter_label": chapter_label_text(chapter),
                        "completion_status": chapter["completion_status"],
                        "discoflash_completion": chapter["discoflash_completion"],
                        "discoflash_commands": study_dashboard_launch_metadata(
                            study_selection_key(str(book["document_id"]), str(chapter["chapter_id"]))
                        )["discoflash"],
                        "discoflash_progress": chapter["discoflash_progress"],
                        "discoflash_review": chapter["discoflash_review"],
                        "page_path": chapter["page_path"],
                        "review_status": chapter.get("combined_review", {}),
                        "row_count": chapter["row_count"],
                        "selection_key": study_selection_key(str(book["document_id"]), str(chapter["chapter_id"])),
                        "vox_completion": chapter["vox_completion"],
                        "vox_commands": study_dashboard_launch_metadata(
                            study_selection_key(str(book["document_id"]), str(chapter["chapter_id"]))
                        )["vox"],
                        "vox_progress": chapter["vox_progress"],
                        "vox_review": chapter["vox_review"],
                    }
                    for chapter in book["chapters"]
                ],
                "completed_chapters": [
                    {
                        "chapter_id": chapter["chapter_id"],
                        "chapter_label": chapter_label_text(chapter),
                        "completion_status": chapter["completion_status"],
                        "page_path": chapter["page_path"],
                        "review_status": chapter.get("combined_review", {}),
                        "selection_key": study_selection_key(str(book["document_id"]), str(chapter["chapter_id"])),
                    }
                    for chapter in book["completed_chapters"]
                ],
                "completion_counts": book["completion_counts"],
                "dashboard_page_path": str(dashboard_book_page_path(document_id=str(book["document_id"]))),
                "definition_card_count": book["definition_card_count"],
                "discoflash_commands": study_dashboard_launch_metadata(
                    study_selection_key(str(book["document_id"]), None)
                )["discoflash"],
                "discoflash_progress": book["discoflash_progress"],
                "document_id": book["document_id"],
                "flashcard_ready": book["flashcard_ready"],
                "has_source_note": book["has_source_note"],
                "math_library_page_path": book["page_paths"]["book"],
                "qa_status": book["qa_status"],
                "reader_ready": book["reader_ready"],
                "row_count": book["row_count"],
                "selection_key": study_selection_key(str(book["document_id"]), None),
                "status": book["status"],
                "study_journal": book.get("journal_summary", {}),
                "recent_activity_entries": book.get("recent_activity_entries", []),
                "vox_commands": study_dashboard_launch_metadata(study_selection_key(str(book["document_id"]), None))["vox"],
                "vox_progress": book["vox_progress"],
            }
            for book in packet["books"]
        ],
        "completed_chapter_count": packet["completed_chapter_count"],
        "continue_studying": packet["continue_studying"],
        "dashboard_hub_path": str(DEFAULT_STUDY_DASHBOARD_PROJECT / "README.md"),
        "flashcard_ready_count": packet["flashcard_ready_count"],
        "fully_completed_book_count": packet["fully_completed_book_count"],
        "generated_at_utc": packet["generated_at_utc"],
        "math_library_hub_path": str(DEFAULT_STUDY_PAGES_PROJECT / "README.md"),
        "next_up": packet["next_up"],
        "note_backed_count": packet["note_backed_count"],
        "recent_activity": packet["recent_activity"],
        "recently_completed": packet["recently_completed"],
        "review_queue": packet["review_queue"],
        "reader_ready_count": packet["reader_ready_count"],
        "selection": packet["selection"],
        "shelf": packet["shelf"],
        "source_root": packet["source_root"],
        "study_journal": packet["study_journal"],
        "total_chapter_count": sum(book["chapter_count"] for book in packet["books"]),
        "wiki_root": packet["wiki_root"],
    }


def page_book_summary(book: dict[str, Any]) -> dict[str, Any]:
    return {
        "book_title": book["book_title"],
        "chapter_count": book["chapter_count"],
        "definition_card_count": book["definition_card_count"],
        "document_id": book["document_id"],
        "flashcard_ready": book["flashcard_ready"],
        "has_source_note": book["has_source_note"],
        "page_paths": book["page_paths"],
        "qa_status": book["qa_status"],
        "reader_ready": book["reader_ready"],
        "status": book["status"],
        "title_source": book["title_source"],
    }


def update_root_index(wiki_root: Path) -> None:
    index_path = wiki_root / "index.md"
    if not index_path.exists():
        return
    content = index_path.read_text(encoding="utf-8")
    dashboard_entry = "- [Study Dashboard](projects/study_dashboard/README.md)"
    entry = "- [Math Library Hub](projects/math_library/README.md)"
    if dashboard_entry not in content:
        if entry in content:
            content = content.replace(entry, dashboard_entry + "\n" + entry, 1)
        else:
            content = content.rstrip() + "\n\n## Study\n\n" + dashboard_entry + "\n"
    if entry in content:
        index_path.write_text(content, encoding="utf-8")
        return
    anchor = "- [Computational Math Project Hub](projects/computational_math/README.md)"
    if anchor in content:
        content = content.replace(anchor, anchor + "\n" + entry, 1)
    else:
        content = content.rstrip() + "\n\n## Math Library\n\n" + entry + "\n"
    index_path.write_text(content, encoding="utf-8")


def update_math_sources_hub(wiki_root: Path) -> None:
    path = wiki_root / "sources" / "math" / "README.md"
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    entry = "- [Generated Math Library Hub](../../projects/math_library/README.md)"
    if entry in content:
        return
    anchor = "- [Book-to-Concept Bridge Map](book_to_concept_bridge_map.md)"
    if anchor in content:
        content = content.replace(anchor, anchor + "\n" + entry, 1)
    else:
        content = content.rstrip() + "\n\n## Navigation\n\n" + entry + "\n"
    path.write_text(content, encoding="utf-8")


def relative_link(from_path: Path, to_path: Path) -> str:
    return PurePosixPath(os.path.relpath(str(to_path.resolve()), start=str(from_path.parent.resolve()))).as_posix()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
