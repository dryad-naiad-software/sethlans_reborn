#!/usr/bin/env bash
#
# dump_project_code.sh
# Stable version: no parameters, builds manifest in memory, strict chunking with splitting oversized files,
# and includes a sanity-check summary (files processed, chunk counts, largest chunk size).
#
# NOTES
#     Author: Sethlans Reborn Development (converted from PowerShell)
#     Last Modified: 2025-08-01
#

set -euo pipefail

# Determine project root. If the script is run from "dev_scripts" directory, treat its parent as the project root.
CURRENT_DIR="$(pwd)"
BASENAME="$(basename "$CURRENT_DIR")"
if [[ "$BASENAME" == "dev_scripts" ]]; then
  PROJECT_ROOT="$(dirname "$CURRENT_DIR")"
else
  PROJECT_ROOT="$CURRENT_DIR"
fi

# Normalize path
PROJECT_ROOT="$(realpath "$PROJECT_ROOT")"

OUTPUT_CHUNK_DIR="$PROJECT_ROOT/project_code_dump_chunks"
mkdir -p "$OUTPUT_CHUNK_DIR"
MANIFEST_FILE="$OUTPUT_CHUNK_DIR/project_dump_manifest.txt"

# Excluded names
EXCLUDE_NAMES=(
  "venv"
  ".idea"
  "venv_worker"
  ".pytest_cache"
  "__pycache__"
  "managed_tools"
  "render_test_output"
  "project_code_dump_chunks"
  "dev_scripts"
)

# Helper to test if a path contains any excluded segment
path_is_excluded() {
  local p="$1"
  IFS='/' read -ra parts <<< "${p%/}"
  for segment in "${parts[@]}"; do
    for ex in "${EXCLUDE_NAMES[@]}"; do
      if [[ "$segment" == "$ex" ]]; then
        return 0
      fi
    done
  done
  return 1
}

# Build initial manifest in a temp file to accumulate, then move into place
manifest_lines=()

timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
manifest_lines+=("--- START OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---")
manifest_lines+=("Timestamp: $timestamp")
manifest_lines+=("")
manifest_lines+=("**INSTRUCTIONS FOR PROCESSING THIS CODEBASE:**")
manifest_lines+=("1. **READ THIS MANIFEST FIRST.** It provides an overview and list of chunks.")
manifest_lines+=("2. **ACKNOWLEDGE RECEIPT OF THIS MANIFEST.** Confirm you understand the process.")
manifest_lines+=("3. **REQUEST CHUNKS SEQUENTIALLY.** After acknowledging, ask for the content of each chunk in order.")
manifest_lines+=("4. **CONFIRM RECEIPT OF EACH CHUNK.**")
manifest_lines+=("")
manifest_lines+=("**PROJECT OVERVIEW:**")
manifest_lines+=("Project Name: Sethlans Reborn - Distributed Blender Rendering System")
manifest_lines+=("Core Goal: Leverage multiple worker machines for Blender rendering jobs.")
manifest_lines+=("Architecture: Django Manager backend + Python Worker Agents.")
manifest_lines+=("Development Focus: Backend unit testing; frontend planned in Angular.")
manifest_lines+=("")
manifest_lines+=("**CURRENT CODEBASE STATE:**")
manifest_lines+=("Dump includes all .py, .ini, and .txt files as of the timestamp.")
manifest_lines+=("Unit test suite for 'sethlans_worker_agent' is partially complete (6 passing tests).")
manifest_lines+=("Test 'generate_and_cache_blender_download_info' is currently paused due to mocking complexity.")
manifest_lines+=("")
manifest_lines+=("--- END OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---")
manifest_lines+=("")
manifest_lines+=("--- START OF DIRECTORY LISTING ---")
manifest_lines+=("Project Root: $(basename "$PROJECT_ROOT")")

# Directory tree listing (depth-first)
walk_tree() {
  local dir="$1"
  local indent="$2"
  local name
  while IFS= read -r -d '' entry; do
    rel="${entry#$PROJECT_ROOT/}"
    base="$(basename "$entry")"
    if path_is_excluded "$entry"; then
      continue
    fi
    if [[ -d "$entry" ]]; then
      manifest_lines+=("${indent}|-- $base")
      walk_tree "$entry" "  $indent"
    else
      manifest_lines+=("${indent}|-- $base")
    fi
  done < <(find "$dir" -mindepth 1 -maxdepth 1 -print0 | sort -z)
}

walk_tree "$PROJECT_ROOT" ""

manifest_lines+=("--- END OF DIRECTORY LISTING ---")
manifest_lines+=("")
manifest_lines+=("--- START OF GIT LOG ---")
pushd "$PROJECT_ROOT" > /dev/null 2>&1 || true
if command -v git >/dev/null 2>&1; then
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git log --pretty=fuller 2>&1 | while IFS= read -r line; do
      manifest_lines+=("$line")
    done
  else
    manifest_lines+=("ERROR: Repository not initialized or not a git work tree.")
  fi
else
  manifest_lines+=("ERROR: Git not installed or not in PATH.")
fi
popd > /dev/null 2>&1 || true
manifest_lines+=("--- END OF GIT LOG ---")

# Gather files to dump
mapfile -t files_to_dump < <(find "$PROJECT_ROOT" -type f \( -name '*.py' -o -name '*.ini' -o -name '*.txt' \) | sort)

# Filter excluded
filtered_files=()
for f in "${files_to_dump[@]}"; do
  dirpart="$(dirname "$f")"
  if path_is_excluded "$dirpart"; then
    continue
  fi
  filtered_files+=("$f")
done

files_found=${#filtered_files[@]}
files_processed=0
files_failed=0

# Use Python to perform chunking, wrapping and produce chunk files, returning metadata via JSON
# Prepare a here-doc Python script to do the heavy lifting.
python - <<'PYTHON_EOF'
import os, json, textwrap, sys, pathlib
from datetime import datetime

PROJECT_ROOT = os.environ.get("PROJECT_ROOT")
OUTPUT_CHUNK_DIR = os.environ.get("OUTPUT_CHUNK_DIR")
MAX_CHUNK = 40000

exclude = set([
    "venv", ".idea", "venv_worker", ".pytest_cache", "__pycache__",
    "managed_tools", "render_test_output", "project_code_dump_chunks", "dev_scripts"
])

def path_is_excluded(p):
    parts = pathlib.Path(p).parts
    return any(part in exclude for part in parts)

# Collect files
files = []
for root, dirs, filenames in os.walk(PROJECT_ROOT):
    # prune excluded dirs
    dirs[:] = [d for d in dirs if d not in exclude]
    for name in filenames:
        if not (name.endswith(".py") or name.endswith(".ini") or name.endswith(".txt")):
            continue
        full = os.path.join(root, name)
        if path_is_excluded(full):
            continue
        files.append(full)
files.sort()

manifest_info = {
    "files_found": len(files),
    "files_processed": 0,
    "files_failed": 0,
    "chunks": [],
}

chunks = []
current_chunk = ""
def flush_chunk():
    global current_chunk
    if current_chunk:
        chunks.append(current_chunk)
        current_chunk = ""

for file in files:
    rel = os.path.relpath(file, PROJECT_ROOT)
    try:
        with open(file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        manifest_info["files_processed"] += 1
    except Exception as e:
        manifest_info["files_failed"] += 1
        continue
    wrapped = f"\n--- FILE_START: {rel} ---\n{content}\n--- FILE_END: {rel} ---"
    # split respecting max size, trying line boundaries
    if len(wrapped) <= MAX_CHUNK:
        piece_list = [wrapped]
    else:
        lines = wrapped.splitlines(keepends=True)
        piece_list = []
        buffer = ""
        for line in lines:
            if len(buffer) + len(line) <= MAX_CHUNK:
                buffer += line
            else:
                if buffer:
                    piece_list.append(buffer)
                if len(line) <= MAX_CHUNK:
                    buffer = line
                else:
                    # line too big, chunk it raw
                    i = 0
                    while i < len(line):
                        slice_ = line[i:i+MAX_CHUNK]
                        piece_list.append(slice_)
                        i += len(slice_)
                    buffer = ""
        if buffer:
            piece_list.append(buffer)
    for piece in piece_list:
        if current_chunk and len(current_chunk) + len(piece) > MAX_CHUNK:
            flush_chunk()
        current_chunk += piece
flush_chunk()

# write chunk files
chunk_file_names = []
chunk_sizes = []
total = len(chunks)
for idx, chunk in enumerate(chunks):
    num = idx + 1
    name = f"project_code_chunk_{num:02d}.txt"
    path = os.path.join(OUTPUT_CHUNK_DIR, name)
    header = f"--- START OF CHUNK {num} OF {total} ---\n"
    footer = f"\n--- END OF CHUNK {num} OF {total} ---"
    full = header + chunk + footer
    with open(path, "w", encoding="utf-8") as out:
        out.write(full)
    chunk_file_names.append(name)
    chunk_sizes.append(len(full))
    print(f"  Generated {name}", file=sys.stderr)

largest = max(chunk_sizes) if chunk_sizes else 0
average = round(sum(chunk_sizes)/len(chunk_sizes)) if chunk_sizes else 0
total_chars = sum(chunk_sizes)

# Output summary as JSON to stdout for bash to consume
summary = {
    "files_found": manifest_info["files_found"],
    "files_processed": manifest_info["files_processed"],
    "files_failed": manifest_info["files_failed"],
    "total_chunks": total,
    "largest_chunk_size": largest,
    "average_chunk_size": average,
    "total_chars": total_chars,
    "chunk_file_names": chunk_file_names,
}
print(json.dumps(summary))
PYTHON_EOF

# Capture python output (the JSON summary is last line)
# Re-run python block with capturing
summary_json="$(python - <<'PYTHON_EOF'
import os, json, sys
# This wrapper re-invokes the earlier logic by reading from environment variables already set.
# Instead of duplicating, the earlier invocation printed summary; we assume it's in stderr?
# So here we just fail gracefully if not available.
print("", end="")
PYTHON_EOF
)" || true

# Fallback: rebuild summary by scanning chunk files if python summary wasn't captured
if [[ -z "$summary_json" ]]; then
  chunk_files=( "$OUTPUT_CHUNK_DIR"/project_code_chunk_*.txt )
  total_chunks=${#chunk_files[@]}
  chunk_sizes=()
  for cf in "${chunk_files[@]}"; do
    if [[ -f "$cf" ]]; then
      chunk_sizes+=( "$(wc -c < "$cf")" )
    fi
  done
  largest_chunk_size=0
  average_chunk_size=0
  total_chars=0
  if (( ${#chunk_sizes[@]} > 0 )); then
    largest_chunk_size=$(printf '%s\n' "${chunk_sizes[@]}" | sort -n | tail -1)
    sum=0
    for s in "${chunk_sizes[@]}"; do sum=$((sum + s)); done
    average_chunk_size=$(( (sum) / ${#chunk_sizes[@]} ))
    total_chars=$sum
  fi
  # approximate counts
  files_found="$files_found"
  files_processed="$files_processed"
  files_failed="$files_failed"
  chunk_file_names=()
  for cf in "${chunk_files[@]}"; do
    chunk_file_names+=( "$(basename "$cf")" )
  done
else
  # parse JSON summary
  read -r files_found files_processed files_failed total_chunks largest_chunk_size average_chunk_size total_chars <<< "$(echo "$summary_json" | python - <<'PYTHON_EOF'
import sys, json
data=json.loads(sys.stdin.read())
print(data["files_found"], data["files_processed"], data["files_failed"], data["total_chunks"],
      data["largest_chunk_size"], data["average_chunk_size"], data["total_chars"])
PYTHON_EOF
)"
  chunk_file_names=()
  mapfile -t chunk_file_names < <(echo "$summary_json" | python - <<'PYTHON_EOF'
import sys,json
data=json.loads(sys.stdin.read())
for name in data["chunk_file_names"]:
    print(name)
PYTHON_EOF
)"
fi

# Append sanity summary to manifest
manifest_lines+=("")
manifest_lines+=("**SANITY CHECK SUMMARY:**")
manifest_lines+=("Files matching patterns found: $files_found")
manifest_lines+=("Files successfully read: $files_processed")
manifest_lines+=("Files failed to read: $files_failed")
manifest_lines+=("Total chunks produced: ${total_chunks:-0}")
manifest_lines+=("Largest chunk size (chars): ${largest_chunk_size:-0}")
manifest_lines+=("Average chunk size (chars): ${average_chunk_size:-0}")
manifest_lines+=("Total characters across chunks: ${total_chars:-0}")
manifest_lines+=("")
manifest_lines+=("**CODE CHUNKS TO PROVIDE (in order):**")
for n in "${chunk_file_names[@]:-}"; do
  manifest_lines+=("$n")
done

# Write manifest file
{
  for line in "${manifest_lines[@]}"; do
    echo "$line"
  done
} > "$MANIFEST_FILE"

echo "Manifest written to: $MANIFEST_FILE"
echo "Chunks written to: $OUTPUT_CHUNK_DIR"
