#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="/Volumes/wiki"
DEST_ROOT="state/wiki_mirror"
EXCLUDES_FILE="config/wiki_mirror_excludes.txt"
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: tools/sync_wiki_mirror.sh [--source /Volumes/wiki] [--dest state/wiki_mirror] [--dry-run]

Creates or refreshes a local working mirror of the NAS wiki. The mirror is
intended for reads only; the NAS remains canonical for editorial storage.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source)
      SOURCE_ROOT="${2:?missing value for --source}"
      shift 2
      ;;
    --dest)
      DEST_ROOT="${2:?missing value for --dest}"
      shift 2
      ;;
    --exclude-from)
      EXCLUDES_FILE="${2:?missing value for --exclude-from}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required but was not found" >&2
  exit 1
fi

if [ ! -d "$SOURCE_ROOT" ]; then
  echo "source wiki root does not exist: $SOURCE_ROOT" >&2
  exit 1
fi

if [ ! -f "$EXCLUDES_FILE" ]; then
  echo "exclude file does not exist: $EXCLUDES_FILE" >&2
  exit 1
fi

case "$DEST_ROOT" in
  state/*|./state/*|"$REPO_ROOT"/state/*) ;;
  *)
    echo "refusing destination outside repo state/: $DEST_ROOT" >&2
    exit 2
    ;;
esac

MARKER_PATH="${DEST_ROOT%/}.mirror_marker"

if [ -d "$DEST_ROOT" ] && [ ! -f "$MARKER_PATH" ]; then
  if [ -n "$(find "$DEST_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo "refusing to sync into non-empty unmarked destination: $DEST_ROOT" >&2
    exit 2
  fi
fi

mkdir -p "$DEST_ROOT"
touch "$MARKER_PATH"

RSYNC_ARGS=(
  -a
  --delete
  --delete-excluded
  --prune-empty-dirs
  --exclude-from "$EXCLUDES_FILE"
)

if [ "$DRY_RUN" -eq 1 ]; then
  RSYNC_ARGS+=(--dry-run --stats)
else
  RSYNC_ARGS+=(--stats)
fi

rsync "${RSYNC_ARGS[@]}" "$SOURCE_ROOT"/ "$DEST_ROOT"/
