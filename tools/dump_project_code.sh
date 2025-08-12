#!/usr/bin/env bash
# dump_project_code.sh
# Builds a manifest and chunked text files of the codebase for LLM ingestion.
# Bash port of dump_project_code.ps1

set -euo pipefail

#######################################
# CONFIGURATION
#######################################

# Directory names to exclude entirely from the dump
exclude_names=(
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

# Explicit file basenames to ignore
exclude_files=(
  "chat_template.txt"
)

# Max characters per chunk file
MAX_CHARS=40000

#######################################
# HELPERS
#######################################

contains_in_array() {
  local needle="$1"; shift
  local e
  for e in "$@"; do
    [[ "$e" == "$needle" ]] && return 0
  done
  return 1
}

# Build a reusable prune clause for `find` (prints an array via echo)
# Usage: eval "set -- $(build_prune_clause)"; find ... "$@"
build_prune_clause() {
  printf '('
  printf ' -type d ('
  local first=1
  local name
  for name in "${exclude_names[@]}"; do
    if (( first )); then
      printf " -name '%s'" "$name"
      first=0
    else
      printf " -o -name '%s'" "$name"
    fi
  done
  printf ' ) -prune ) -o'
}

# Get project root:
# If CWD leaf is 'tools', use its parent; else use CWD.
get_project_root() {
  local cwd root
  cwd="$(pwd)"
  if [[ "$(basename "$cwd")" == "dev_scripts" ]]; then
    root="$(dirname "$cwd")"
  else
    root="$cwd"
  fi
  printf '%s' "$root"
}

#######################################
# DIRECTORY LISTING (non-recursive function equivalent)
#######################################

append_directory_listing() {
  # Writes directory tree lines into the manifest via $manifest_file
  local root="$1"
  local prune_clause
  prune_clause="$(build_prune_clause)"

  {
    echo "--- START OF DIRECTORY LISTING ---"
    echo "Project Root: $(basename "$root")"

    # Directories first
    # shellcheck disable=SC2046
    find "$root" $(eval echo "$prune_clause") -type d -print0 \
      | sort -z \
      | while IFS= read -r -d '' d; do
          # Skip root itself
          [[ "$d" == "$root" ]] && continue
          rel="${d#$root/}"
          # depth = number of / in rel
          depth=$(( $(grep -o "/" <<<"$rel" | wc -l) ))
          indent="$(printf '%*s' $((depth * 2)) '')"
          echo "${indent}|-- $(basename "$d")"
        done

    # Files next
    # shellcheck disable=SC2046
    find "$root" $(eval echo "$prune_clause") -type f -print0 \
      | sort -z \
      | while IFS= read -r -d '' f; do
          base="$(basename "$f")"
          # Skip explicit file-name excludes
          if contains_in_array "$base" "${exclude_files[@]}"; then
            continue
          fi
          rel="${f#$root/}"
          depth=$(( $(grep -o "/" <<<"$rel" | wc -l) ))
          indent="$(printf '%*s' $((depth * 2)) '')"
          echo "${indent}|-- $base"
        done

    echo "--- END OF DIRECTORY LISTING ---"
  } >> "$manifest_file"
}

#######################################
# FILE SELECTION
#######################################

collect_files_to_dump() {
  # Prints NUL-delimited list of files to dump on stdout
  local root="$1"
  local prune_clause
  prune_clause="$(build_prune_clause)"

  # shellcheck disable=SC2046
  find "$root" $(eval echo "$prune_clause") -type f \
    \( -name '*.py' -o -name '*.ini' -o -name '*.txt' \) -print0 \
    | while IFS= read -r -d '' f; do
        base="$(basename "$f")"
        if contains_in_array "$base" "${exclude_files[@]}"; then
          continue
        fi
        printf '%s\0' "$f"
      done
}

#######################################
# CHUNKING
#######################################

# Append wrapped content to chunk temp files, splitting as needed.
# Uses global: current_chunk_tmp, current_chunk_len, chunks_tmp_files[]
append_wrapped_to_chunks() {
  local wrapped="$1"
  # Split into pieces <= MAX_CHARS, preserving lines when possible
  # We pass content via stdin to awk to avoid quoting issues.
  printf '%s' "$wrapped" \
  | awk -v max="$MAX_CHARS" '
    BEGIN { buf="" }
    {
      line=$0
      cand = (buf=="" ? line : buf "\n" line)
      if (length(cand) <= max) {
        buf=cand
        next
      }
      if (buf!="") { print buf; buf=""; }
      # If a single line is too long, split it within the line
      while (length(line) > max) {
        print substr(line,1,max)
        line = substr(line,max+1)
      }
      buf=line
    }
    END { if (buf!="") print buf; }
  ' | while IFS= read -r piece; do
        local piece_len=${#piece}
        if (( current_chunk_len > 0 && current_chunk_len + piece_len > MAX_CHARS )); then
          chunks_tmp_files+=("$current_chunk_tmp")
          current_chunk_tmp="$(mktemp)"
          current_chunk_len=0
        fi
        printf '%s' "$piece" >> "$current_chunk_tmp"
        current_chunk_len=$(( current_chunk_len + piece_len ))
        # Re-add newline dropped by read (awk prints lines without trailing \n in this pipeline)
        printf '\n' >> "$current_chunk_tmp"
        current_chunk_len=$(( current_chunk_len + 1 ))
      done
}

#######################################
# MAIN
#######################################

project_root="$(get_project_root)"
output_chunk_dir="$project_root/project_code_dump_chunks"
mkdir -p "$output_chunk_dir"
manifest_file="$output_chunk_dir/project_dump_manifest.txt"

# Manifest header
{
  echo "--- START OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---"
  date +"Timestamp: %Y-%m-%d %H:%M:%S"
  echo
  echo "**INSTRUCTIONS FOR PROCESSING THIS CODEBASE:**"
  echo "1. **READ THIS MANIFEST FIRST.** It provides an overview and list of chunks."
  echo "2. **ACKNOWLEDGE RECEIPT OF THIS MANIFEST.** Confirm you understand the process."
  echo "3. **REQUEST CHUNKS SEQUENTIALLY.** After acknowledging, ask for the content of each chunk in order."
  echo "4. **CONFIRM RECEIPT OF EACH CHUNK.**"
  echo
  echo "--- END OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---"
  echo
} > "$manifest_file"

# Directory listing
append_directory_listing "$project_root"

# Git log
{
  echo
  echo "--- START OF GIT LOG ---"
  if git -C "$project_root" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$project_root" log --pretty=fuller 2>&1
  else
    echo "ERROR: Could not get git log. Ensure Git is installed and repository is initialized."
  fi
  echo "--- END OF GIT LOG ---"
} >> "$manifest_file"

# File selection
mapfile -d '' files_to_dump < <(collect_files_to_dump "$project_root")
files_found=${#files_to_dump[@]}
files_processed=0
files_failed=0

# Chunk assembly to temp files
chunks_tmp_files=()
current_chunk_tmp="$(mktemp)"
current_chunk_len=0

for f in "${files_to_dump[@]}"; do
  rel="${f#$project_root/}"
  if ! content="$(cat -- "$f" 2>/dev/null)"; then
    printf 'Failed to read %s\n' "$rel" >&2
    files_failed=$((files_failed + 1))
    continue
  fi
  files_processed=$((files_processed + 1))
  wrapped=$'\n'"--- FILE_START: $rel ---"$'\n'"$content"$'\n'"--- FILE_END: $rel ---"
  append_wrapped_to_chunks "$wrapped"
done

# Flush last chunk if it has content
if (( current_chunk_len > 0 )); then
  chunks_tmp_files+=("$current_chunk_tmp")
else
  rm -f -- "$current_chunk_tmp"
fi

# Write final chunk files with headers/footers
total_chunks=${#chunks_tmp_files[@]}
chunk_file_names=()
chunk_sizes=()   # in characters (approx. via wc -m)

if (( total_chunks > 0 )); then
  for ((i=0; i<total_chunks; i++)); do
    num=$((i+1))
    name=$(printf 'project_code_chunk_%02d.txt' "$num")
    path="$output_chunk_dir/$name"
    header="--- START OF CHUNK $num OF $total_chunks ---"
    footer=$'\n'"--- END OF CHUNK $num OF $total_chunks ---"

    {
      printf '%s\n' "$header"
      cat -- "${chunks_tmp_files[$i]}"
      printf '%s' "$footer"
    } > "$path"

    # Record stats
    chunk_file_names+=("$name")
    size_chars=$(wc -m < "$path" | tr -d '[:space:]')
    chunk_sizes+=("$size_chars")

    echo "  Generated $name"
  done
fi

# Cleanup temp files
for tf in "${chunks_tmp_files[@]}"; do rm -f -- "$tf"; done || true

# Sanity-check summary
largest_chunk_size=0
average_chunk_size=0
total_chars=0

if (( ${#chunk_sizes[@]} > 0 )); then
  for s in "${chunk_sizes[@]}"; do
    (( s > largest_chunk_size )) && largest_chunk_size="$s"
    total_chars=$(( total_chars + s ))
  done
  # Integer average
  average_chunk_size=$(( total_chars / ${#chunk_sizes[@]} ))
fi

{
  echo
  echo "**SANITY CHECK SUMMARY:**"
  printf 'Files matching patterns found: %d\n' "$files_found"
  printf 'Files successfully read: %d\n' "$files_processed"
  printf 'Files failed to read: %d\n' "$files_failed"
  printf 'Total chunks produced: %d\n' "$total_chunks"
  printf 'Largest chunk size (chars): %d\n' "$largest_chunk_size"
  printf 'Average chunk size (chars): %d\n' "$average_chunk_size"
  printf 'Total characters across chunks: %d\n' "$total_chars"
  echo
  echo "**CODE CHUNKS TO PROVIDE (in order):**"
  for n in "${chunk_file_names[@]}"; do
    echo "$n"
  done
} >> "$manifest_file"

echo "Manifest written to: $manifest_file"
echo "Chunks written to: $output_chunk_dir"
