from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Any
from urllib.parse import quote, unquote, urlparse

from wiki_tool.catalog import latest_scan_run


DEFAULT_MAC_DEV_ROOT = "/Users/kogaryu/dev"
DEFAULT_CONFIG = Path("state/devrefs_config.json")
DEV_SCHEME = "dev"


@dataclass(frozen=True)
class DevRef:
    repo: str
    path: str

    @property
    def relative_path(self) -> str:
        return f"{self.repo}/{self.path}" if self.path else self.repo

    @property
    def uri(self) -> str:
        encoded_repo = quote(self.repo, safe="")
        encoded_path = quote(self.path, safe="/")
        return f"{DEV_SCHEME}://{encoded_repo}/{encoded_path}" if encoded_path else f"{DEV_SCHEME}://{encoded_repo}"


def is_dev_uri(value: str) -> bool:
    return value.lower().startswith(f"{DEV_SCHEME}://")


def local_path_to_devref(value: str, *, dev_root: str = DEFAULT_MAC_DEV_ROOT) -> DevRef | None:
    path = value.split("#", 1)[0].split("?", 1)[0].strip().replace("\\", "/")
    root = dev_root.rstrip("/").replace("\\", "/")
    prefix = f"{root}/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix) :].strip("/")
    if not remainder:
        return None
    parts = PurePosixPath(remainder).parts
    if not parts:
        return None
    repo = parts[0]
    repo_path = "/".join(parts[1:])
    return DevRef(repo=repo, path=repo_path)


def parse_dev_uri(value: str) -> DevRef:
    parsed = urlparse(value)
    if parsed.scheme.lower() != DEV_SCHEME or not parsed.netloc:
        raise ValueError(f"not a dev reference: {value!r}")
    repo = unquote(parsed.netloc)
    path = unquote(parsed.path.lstrip("/"))
    return DevRef(repo=repo, path=path)


def load_devref_config(path: Path = DEFAULT_CONFIG) -> dict[str, str | None]:
    config: dict[str, str | None] = {
        "mac": DEFAULT_MAC_DEV_ROOT,
        "windows": None,
    }
    if not path.exists():
        return config
    payload = json.loads(path.read_text())
    roots = payload.get("roots", payload)
    if isinstance(roots, dict):
        if roots.get("mac"):
            config["mac"] = str(roots["mac"])
        if roots.get("windows"):
            config["windows"] = str(roots["windows"])
    return config


def resolve_dev_uri(
    uri: str,
    *,
    platform: str,
    mac_root: str | None = None,
    windows_root: str | None = None,
    config_path: Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    ref = parse_dev_uri(uri)
    config = load_devref_config(config_path)
    root = mac_root or config["mac"] if platform == "mac" else windows_root or config["windows"]
    if not root:
        return {
            "configured": False,
            "error": f"{platform} dev root is not configured",
            "platform": platform,
            "relative_path": ref.relative_path,
            "uri": ref.uri,
        }
    if platform == "windows":
        clean_root = root.rstrip("\\/")
        relative = ref.relative_path.replace("/", "\\")
        path = f"{clean_root}\\{relative}"
    else:
        clean_root = root.rstrip("/")
        path = f"{clean_root}/{ref.relative_path}"
    return {
        "configured": True,
        "path": path,
        "platform": platform,
        "relative_path": ref.relative_path,
        "uri": ref.uri,
    }


def devref_candidates(
    db_path: Path,
    *,
    mac_dev_root: str = DEFAULT_MAC_DEV_ROOT,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT source_path, target_raw, label, link_kind, line
            FROM links
            WHERE resolved = 0
            ORDER BY source_path, line
            """
        ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        raw = str(row["target_raw"])
        ref = local_path_to_devref(raw, dev_root=mac_dev_root)
        if ref is None:
            continue
        candidates.append(
            {
                "category": "local_absolute_path",
                "label": row["label"],
                "line": row["line"],
                "link_kind": row["link_kind"],
                "new_target": ref.uri,
                "old_target": raw,
                "repo": ref.repo,
                "repo_path": ref.path,
                "source_path": row["source_path"],
            }
        )
    if limit is not None:
        candidates = candidates[:limit]
    return candidates


def devref_audit(db_path: Path, *, mac_dev_root: str = DEFAULT_MAC_DEV_ROOT) -> dict[str, Any]:
    candidates = devref_candidates(db_path, mac_dev_root=mac_dev_root)
    repos: dict[str, int] = {}
    files: dict[str, int] = {}
    for candidate in candidates:
        repos[candidate["repo"]] = repos.get(candidate["repo"], 0) + 1
        files[candidate["source_path"]] = files.get(candidate["source_path"], 0) + 1
    return {
        "candidate_count": len(candidates),
        "mac_dev_root": mac_dev_root,
        "repos": sorted(
            [{"repo": repo, "count": count} for repo, count in repos.items()],
            key=lambda item: (-item["count"], item["repo"]),
        ),
        "source_files": sorted(
            [{"source_path": path, "count": count} for path, count in files.items()],
            key=lambda item: (-item["count"], item["source_path"]),
        ),
    }


def build_devref_patch_bundle(
    db_path: Path,
    *,
    mac_dev_root: str = DEFAULT_MAC_DEV_ROOT,
) -> dict[str, Any]:
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    targets = [
        {
            "category": "local_absolute_path",
            "label": candidate["label"],
            "line": candidate["line"],
            "new_target": candidate["new_target"],
            "old_target": candidate["old_target"],
            "path": candidate["source_path"],
            "reason": "Convert machine-specific local dev path to portable dev:// reference",
            "source_path": candidate["source_path"],
            "type": "replace_link_target",
        }
        for candidate in devref_candidates(db_path, mac_dev_root=mac_dev_root)
    ]
    return {
        "backup_manifest": {
            "required_before_apply": True,
            "status": "not_created",
        },
        "bundle_id": f"bundle:devrefs:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at_utc": created_at,
        "rationale": "Replace machine-specific /Users/kogaryu/dev links with portable dev:// references.",
        "source_catalog": source_catalog_metadata(db_path),
        "targets": targets,
    }


def source_catalog_metadata(db_path: Path) -> dict[str, Any]:
    run = latest_scan_run(db_path)
    return {
        "db_path": str(db_path),
        "root": run.get("root") if run else None,
        "run_id": run.get("run_id") if run else None,
        "scanned_at_utc": run.get("scanned_at_utc") if run else None,
    }
