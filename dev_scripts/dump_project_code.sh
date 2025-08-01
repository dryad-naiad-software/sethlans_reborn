#!/usr/bin/env bash
set -euo pipefail

# Stable version: no parameters, builds manifest in memory, strict chunking with splitting oversized files,
# and includes a sanity-check summary (files processed, chunk counts, largest chunk size).

# Determine project root. If run from "dev_scripts" directory, treat its parent as the project root.
current_path="$(pwd)"
base_name="$(basename "$current_path")"
if [[ "$base_name" == "dev_scripts" ]]; then
    project_root_path="$(dirname "$current_path")"
    if [[ -z "$project_root_path" ]]; then
        project_root_path="$current_path"
    fi
else
    project_root_path="$current_path"
fi

# Normalize project root (resolve symlinks)
project_root="$(cd "$project_root_path" && pwd)"

output_chunk_dir="$project_root/project_code_dump_chunks"
mkdir -p "$output_chunk_dir"
manifest_file="$output_chunk_dir/project_dump_manifest.txt"

# Excluded directory names
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

# Helper to decide if a path contains an excluded component
path_contains_excluded_dir() {
    local p="$1"
    IFS='/' read -ra parts <<< "${p%/}"
    for comp in "${parts[@]}"; do
        for excl in "${exclude_names[@]}"; do
            if [[ "$comp" == "$excl" ]]; then
                return 0
            fi
        done
    done
    return 1
}

# Accumulate manifest lines in an array
manifest_lines=()
manifest_lines+=("--- START OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---")
manifest_lines+=("Timestamp: $(date +"%Y-%m-%d %H:%M:%S")")
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

# Directory listing
manifest_lines+=("--- START OF DIRECTORY LISTING ---")
manifest_lines+=("Project Root: $(basename "$project_root")")

# Recursive tree listing function
get_tree_listing() {
    local dir="$1"
    local depth="$2"
    local indent
    indent=$(printf '  %.0s' $(seq 1 "$depth"))
    local entries
    # Sort: directories first, then names
    while IFS= read -rd $'\0' entry; do
        name="$(basename "$entry")"
        if [[ -d "$entry" ]]; then
            # skip excluded top-level dirs
            for excl in "${exclude_names[@]}"; do
                if [[ "$name" == "$excl" ]]; then
                    continue 2
                fi
            done
            manifest_lines+=("${indent}|-- $name")
            get_tree_listing "$entry" $((depth + 1))
        else
            dir_of_entry="$(dirname "$entry")"
            if path_contains_excluded_dir "$dir_of_entry"; then
                continue
            fi
            manifest_lines+=("${indent}|-- $name")
        fi
    done < <(find "$dir" -maxdepth 1 -mindepth 1 -print0 | sort -z | xargs -0 -n1 | while read -r p; do echo -n "$p"; printf '\0'; done)
}

get_tree_listing "$project_root" 0
manifest_lines+=("--- END OF DIRECTORY LISTING ---")
manifest_lines+=("")

# Git log
manifest_lines+=("--- START OF GIT LOG ---")
if command -v git >/dev/null 2>&1 && [[ -d "$project_root/.git" ]]; then
    pushd "$project_root" >/dev/null
    if git log --pretty=fuller &>/dev/null; then
        git_log_output="$(git log --pretty=fuller 2>&1 || true)"
        while IFS= read -r line; do
            manifest_lines+=("$line")
        done <<< "$git_log_output"
    else
        manifest_lines+=("ERROR: Could not get git log. Repository may not have any commits or is corrupted.")
    fi
    popd >/dev/null
else
    manifest_lines+=("ERROR: Could not get git log. Ensure Git is installed and repository is initialized.")
fi
manifest_lines+=("--- END OF GIT LOG ---")

# Find files to dump
mapfile -t files_to_dump < <(find "$project_root" -type f \( -name "*.py" -o -name "*.ini" -o -name "*.txt" \) | sort)

files_found=${#files_to_dump[@]}
files_processed=0
files_failed=0

max_chunk_size=40000
declare -a chunks
current_chunk=""

split_formatted_file_into_pieces() {
    local text="$1"
    local max="$2"
    local -n __out_array=$3

    if [[ ${#text} -le $max ]]; then
        __out_array=("$text")
        return
    fi

    IFS=$'\n' read -d '' -ra lines <<< "$text" || true
    buffer=""
    for line in "${lines[@]}"; do
        if [[ -n "$buffer" ]]; then
            candidate="$buffer"$'\n'"$line"
        else
            candidate="$line"
        fi
        if [[ ${#candidate} -le $max ]]; then
            buffer="$candidate"
        else
            if [[ -n "$buffer" ]]; then
                __out_array+=("$buffer")
            fi
            if [[ ${#line} -le $max ]]; then
                buffer="$line"
            else
                # split the line itself
                local i=0
                local len=${#line}
                while [[ $i -lt $len ]]; do
                    slice="${line:$i:$max}"
                    __out_array+=("$slice")
                    i=$((i + ${#slice}))
                done
                buffer=""
            fi
        fi
    done
    if [[ -n "$buffer" ]]; then
        __out_array+=("$buffer")
    fi
}

# Process each file
for file in "${files_to_dump[@]}"; do
    relative_path="${file#$project_root/}"
    # Skip if in excluded dir
    if path_contains_excluded_dir "$(dirname "$file")"; then
        continue
    fi

    if ! content="$(< "$file")"; then
        echo "Warning: Failed to read $relative_path" >&2
        files_failed=$((files_failed + 1))
        continue
    fi
    files_processed=$((files_processed + 1))
    wrapped=$'\n'"--- FILE_START: $relative_path ---"$'\n'"$content"$'\n'"--- FILE_END: $relative_path ---"
    pieces=()
    split_formatted_file_into_pieces "$wrapped" "$max_chunk_size" pieces

    for piece in "${pieces[@]}"; do
        if [[ -n "$current_chunk" && $(( ${#current_chunk} + ${#piece} )) -gt $max_chunk_size ]]; then
            chunks+=("$current_chunk")
            current_chunk="$piece"
        else
            current_chunk+="$piece"
        fi
    done
done

if [[ -n "$current_chunk" ]]; then
    chunks+=("$current_chunk")
fi

total_chunks=${#chunks[@]}
chunk_file_names=()
declare -a chunk_sizes=()

# Write chunk files
for i in "${!chunks[@]}"; do
    num=$((i + 1))
    name=$(printf "project_code_chunk_%02d.txt" "$num")
    path="$output_chunk_dir/$name"
    header="--- START OF CHUNK $num OF $total_chunks ---"
    footer=$'\n'"--- END OF CHUNK $num OF $total_chunks ---"
    full="${header}"$'\n'"${chunks[$i]}${footer}"
    printf "%s" "$full" > "$path"
    chunk_file_names+=("$name")
    chunk_sizes+=("${#full}")
    echo "  Generated $name"
done

# Sanity-check summary
largest_chunk_size=0
average_chunk_size=0
total_chars=0

if [[ ${#chunk_sizes[@]} -gt 0 ]]; then
    for sz in "${chunk_sizes[@]}"; do
        (( total_chars += sz ))
        if (( sz > largest_chunk_size )); then
            largest_chunk_size=$sz
        fi
    done
    average_chunk_size=$(( (total_chars + total_chunks/2) / total_chunks ))  # rounded
fi

manifest_lines+=("")
manifest_lines+=("**SANITY CHECK SUMMARY:**")
manifest_lines+=("Files matching patterns found: $files_found")
manifest_lines+=("Files successfully read: $files_processed")
manifest_lines+=("Files failed to read: $files_failed")
manifest_lines+=("Total chunks produced: $total_chunks")
manifest_lines+=("Largest chunk size (chars): $largest_chunk_size")
manifest_lines+=("Average chunk size (chars): $average_chunk_size")
manifest_lines+=("Total characters across chunks: $total_chars")
manifest_lines+=("")
manifest_lines+=("**CODE CHUNKS TO PROVIDE (in order):**")
for n in "${chunk_file_names[@]}"; do
    manifest_lines+=("$n")
done

# Write manifest to disk
{
    for line in "${manifest_lines[@]}"; do
        printf '%s\n' "$line"
    done
} > "$manifest_file"

echo "Manifest written to: $manifest_file"
echo "Chunks written to: $output_chunk_dir"
