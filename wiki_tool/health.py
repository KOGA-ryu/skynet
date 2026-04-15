from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable

from wiki_tool.aliases import DEFAULT_ALIAS_MAP
from wiki_tool.catalog import DEFAULT_DB, DEFAULT_WIKI_ROOT, audit_summary, scan_wiki
from wiki_tool.harness import DEFAULT_SPEC_DIR, validate_harness_specs


DEFAULT_TESTS_DIR = Path("tests")


def run_health(
    *,
    wiki_root: Path = DEFAULT_WIKI_ROOT,
    db_path: Path = DEFAULT_DB,
    alias_map_path: Path | None = DEFAULT_ALIAS_MAP,
    spec_dir: Path = DEFAULT_SPEC_DIR,
    tests_dir: Path = DEFAULT_TESTS_DIR,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    steps: list[dict[str, Any]] = []

    scan_step = run_step(
        "scan",
        lambda: {"scan": scan_wiki(wiki_root, db_path, alias_map_path=alias_map_path).__dict__},
    )
    steps.append(scan_step)

    steps.append(
        run_step(
            "audit",
            lambda: audit_health(db_path),
        )
    )

    steps.append(
        run_step(
            "harness_validate",
            lambda: harness_health(spec_dir),
        )
    )

    steps.append(
        run_step(
            "unit_tests",
            lambda: unit_test_health(tests_dir),
        )
    )

    ended = datetime.now(UTC)
    status = "pass" if all(step["status"] == "pass" for step in steps) else "fail"
    return {
        "ended_at_utc": ended.isoformat(timespec="seconds"),
        "duration_seconds": round((ended - started).total_seconds(), 3),
        "inputs": {
            "alias_map": str(alias_map_path) if alias_map_path is not None else None,
            "db": str(db_path),
            "spec_dir": str(spec_dir),
            "tests_dir": str(tests_dir),
            "wiki_root": str(wiki_root),
        },
        "started_at_utc": started.isoformat(timespec="seconds"),
        "status": status,
        "steps": steps,
    }


def run_step(name: str, func: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = func()
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "duration_seconds": round(time.perf_counter() - started, 3),
            "error": str(exc),
            "name": name,
            "status": "fail",
        }
    status = str(result.pop("status", "pass"))
    return {
        "duration_seconds": round(time.perf_counter() - started, 3),
        "name": name,
        "status": status,
        **result,
    }


def audit_health(db_path: Path) -> dict[str, Any]:
    summary = audit_summary(db_path)
    return {
        "audit": summary,
        "status": "pass" if summary.get("status") == "pass" else "fail",
    }


def harness_health(spec_dir: Path) -> dict[str, Any]:
    validation = validate_harness_specs(spec_dir)
    return {
        "harness": validation,
        "status": "pass" if validation["valid"] else "fail",
    }


def unit_test_health(tests_dir: Path) -> dict[str, Any]:
    command = [sys.executable, "-m", "unittest", "discover", "-s", str(tests_dir)]
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
    )
    return {
        "command": command,
        "return_code": completed.returncode,
        "status": "pass" if completed.returncode == 0 else "fail",
        "stderr": completed.stderr,
        "stdout": completed.stdout,
    }
