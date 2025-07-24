# dump_project_code.ps1
# This script first outputs a clear directory listing of the project (excluding specified dirs),
# then divides the content of .py, .ini, and .txt files into manageable chunks (excluding specified dirs).
# It ensures that each chunk ends cleanly at the end of a file.
# It creates a manifest file and multiple chunk files for easier digestion by a chat model.

# Define the base directory of your project (current directory when run)
$ProjectRoot = Get-Location

# Define the output directory for chunks and manifest
$OutputChunkDir = Join-Path $ProjectRoot "project_code_dump_chunks"
# Ensure the output directory exists
New-Item -ItemType Directory -Path $OutputChunkDir -Force | Out-Null

# Define the manifest file path
$ManifestFile = Join-Path $OutputChunkDir "project_dump_manifest.txt"

# Clear existing manifest file content
Clear-Content $ManifestFile -ErrorAction SilentlyContinue

# --- List of directories/patterns to exclude ---
$ExcludeNames = @(
    "venv",
    ".idea",
    "venv_worker",
    ".pytest_cache",
    "__pycache__",
    "project_code_dump_chunks" # Exclude the output directory itself
)

# Helper function to check if a path contains an excluded directory name
function Test-PathContainsExcludedDir {
    param([string]$Path)
    $normalizedPath = $Path -replace [regex]::Escape($ProjectRoot.ToString()), ""
    $pathParts = $normalizedPath.TrimStart('\').Split('\')

    foreach ($part in $pathParts) {
        if ($ExcludeNames -contains $part) {
            return $true
        }
    }
    return $false
}


Add-Content -LiteralPath $ManifestFile -Value "--- START OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---"
Add-Content -LiteralPath $ManifestFile -Value "Timestamp: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Content -LiteralPath $ManifestFile -Value "`n**INSTRUCTIONS FOR PROCESSING THIS CODEBASE:**"
Add-Content -LiteralPath $ManifestFile -Value "1. **READ THIS MANIFEST FIRST.** It provides an overview and list of chunks."
Add-Content -LiteralPath $ManifestFile -Value "2. **ACKNOWLEDGE RECEIPT OF THIS MANIFEST.** Confirm you understand the process."
Add-Content -LiteralPath $ManifestFile -Value "3. **REQUEST CHUNKS SEQUENTIALLY.** After acknowledging, ask for the content of 'project_code_chunk_01.txt', then 'project_code_chunk_02.txt', and so on, until all chunks listed below have been provided."
Add-Content -LiteralPath $ManifestFile -Value "4. **CONFIRM RECEIPT OF EACH CHUNK.** After each chunk is provided, please acknowledge that you have received and processed its content before requesting the next."
Add-Content -LiteralPath $ManifestFile -Value "`n**PROJECT OVERVIEW:**"
Add-Content -LiteralPath $ManifestFile -Value "Project Name: Sethlans Reborn - Distributed Blender Rendering System"
Add-Content -LiteralPath $ManifestFile -Value "Core Goal: Leverage multiple worker machines for Blender rendering jobs."
Add-Content -LiteralPath $ManifestFile -Value "Architecture: Consists of a Django Manager (Backend) and Python Worker Agents (Clients)."
Add-Content -LiteralPath $ManifestFile -Value "Development Focus: Currently focusing on building out and unit testing the **backend functionality**. The **frontend will eventually be written in Angular**."
Add-Content -LiteralPath $ManifestFile -Value "`n**CURRENT CODEBASE STATE:**"
Add-Content -LiteralPath $ManifestFile -Value "This dump contains all .py, .ini, and .txt files from the project as of the timestamp above."
Add-Content -LiteralPath $ManifestFile -Value "The unit test suite for the 'sethlans_worker_agent' components is partially complete and currently stable (passing 6 tests in test_tool_manager.py and others)."
Add-Content -LiteralPath $ManifestFile -Value "The last complex test we were working on ('generate_and_cache_blender_download_info' in tool_manager.py) was paused due to intricate mocking issues, so that test (Test 7 in test_tool_manager.py) is NOT expected to pass in the current code, but is stable in the previous commit. We will revisit it later."
Add-Content -LiteralPath $ManifestFile -Value "`n--- END OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---"


# --- Add Directory Listing ---
Add-Content -LiteralPath $ManifestFile -Value "`n--- START OF DIRECTORY LISTING ---"
Add-Content -LiteralPath $ManifestFile -Value "Project Root: $($ProjectRoot.Name)\"

# Define a recursive function for clearer tree-like listing
function Get-TreeListing {
    param (
        [string]$Path,
        [int]$Depth = 0
    )
    $indent = "  " * $Depth
    Get-ChildItem -Path $Path | ForEach-Object {
        $relativePath = $_.FullName.Replace($ProjectRoot.ToString() + "\", "")
        if (Test-PathContainsExcludedDir $_.FullName) {
            # Skip this item and its children if its full path contains an excluded dir
            return
        }

        if ($_.PSIsContainer) {
            Add-Content -LiteralPath $ManifestFile -Value "$($indent)|-- $($_.Name)\"
            Get-TreeListing -Path $_.FullName -Depth ($Depth + 1)
        } else {
            Add-Content -LiteralPath $ManifestFile -Value "$($indent)|-- $($_.Name)"
        }
    }
}

# Start the recursive listing from the project root
Get-TreeListing -Path $ProjectRoot -Depth 0

Add-Content -LiteralPath $ManifestFile -Value "--- END OF DIRECTORY LISTING ---"
# --- End Directory Listing ---

# --- Collect File Contents for Chunking ---
# Filter files to dump, excluding specified directories and __pycache__ etc.
$FilesToDump = Get-ChildItem -Path $ProjectRoot -Recurse -Include "*.py", "*.ini", "*.txt" |
               Where-Object { -not (Test-PathContainsExcludedDir $_.Directory.FullName) } | # Exclude based on parent directory
               Sort-Object FullName # Sort for consistent order

$CurrentChunkContent = ""
$CurrentChunkNumber = 1
$ChunkFileNames = @() # To store names for the manifest
$MaxChunkSizeChars = 60000 # Aim for approx 60KB per chunk. Adjust as needed if chunks are still too large.

Write-Host "Processing files for chunking..."

foreach ($file in $FilesToDump) {
    $relativePath = $file.FullName.Replace($ProjectRoot.ToString() + "\", "")
    $fileContent = Get-Content $file.FullName -Raw -Encoding Utf8 # Ensure consistent encoding
    $formattedFileContent = "`n--- FILE_START: $relativePath ---`n$fileContent`n--- FILE_END: $relativePath ---"

    # Check if adding this file would exceed the max chunk size
    # If current chunk is not empty AND adding this file would make it too big,
    # or if this single file is larger than MaxChunkSize (it will become its own chunk)
    if ($CurrentChunkContent.Length -gt 0 -and ($CurrentChunkContent.Length + $formattedFileContent.Length) -gt $MaxChunkSizeChars) {
        # Save the current chunk before adding the new file
        $ChunkFileName = "project_code_chunk_{0:02d}.txt" -f $CurrentChunkNumber
        $ChunkFilePath = Join-Path $OutputChunkDir $ChunkFileName

        # Add chunk headers/footers to the file
        Add-Content -LiteralPath $ChunkFilePath -Value "--- START OF CHUNK $($ChunkNumber) OF [TOTAL_CHUNKS_PLACEHOLDER] ---" -Encoding Utf8
        Add-Content -LiteralPath $ChunkFilePath -Value $CurrentChunkContent -Encoding Utf8
        Add-Content -LiteralPath $ChunkFilePath -Value "`n--- END OF CHUNK $($ChunkNumber) OF [TOTAL_CHUNKS_PLACEHOLDER] ---" -Encoding Utf8

        $ChunkFileNames += $ChunkFileName # Add to list for manifest
        $CurrentChunkContent = "" # Reset for next chunk
        $CurrentChunkNumber++
        Write-Host "  Generated $ChunkFileName"
    }

    # Add current file content to the current chunk
    $CurrentChunkContent += $formattedFileContent
}

# Add the last chunk if there's any remaining content
if ($CurrentChunkContent.Length -gt 0) {
    $ChunkFileName = "project_code_chunk_{0:02d}.txt" -f $CurrentChunkNumber
    $ChunkFilePath = Join-Path $OutputChunkDir $ChunkFileName

    Add-Content -LiteralPath $ChunkFilePath -Value "--- START OF CHUNK $($CurrentChunkNumber) OF [TOTAL_CHUNKS_PLACEHOLDER] ---" -Encoding Utf8
    Add-Content -LiteralPath $ChunkFilePath -Value $CurrentChunkContent -Encoding Utf8
    Add-Content -LiteralPath $ChunkFilePath -Value "`n--- END OF CHUNK $($CurrentChunkNumber) OF [TOTAL_CHUNKS_PLACEHOLDER] ---" -Encoding Utf8

    $ChunkFileNames += $ChunkFileName
    $CurrentChunkNumber++
    Write-Host "  Generated $ChunkFileName (last chunk)"
}

# --- Update Manifest with Total Chunks and chunk file names ---
$TotalChunksFinal = $ChunkFileNames.Count

# Re-read manifest to update TOTAL_CHUNKS_PLACEHOLDER
$ManifestContent = Get-Content $ManifestFile -Raw -Encoding Utf8
$ManifestContent = $ManifestContent.Replace("[TOTAL_CHUNKS_PLACEHOLDER]", $TotalChunksFinal)
Set-Content -LiteralPath $ManifestFile -Value $ManifestContent -Encoding Utf8

# Add chunk file names to the manifest
Add-Content -LiteralPath $ManifestFile -Value "`n**CODE CHUNKS TO PROVIDE (in order):**" -Encoding Utf8
foreach ($name in $ChunkFileNames) {
    Add-Content -LiteralPath $ManifestFile -Value "$name" -Encoding Utf8
}
Add-Content -LiteralPath $ManifestFile -Value "`n--- END OF PROJECT CODE DUMP ---" -Encoding Utf8

Write-Host "`nAll .py, .ini, and .txt file contents saved to: $OutputChunkDir"
Write-Host "First, open and copy the content of '$ManifestFile' and paste it into your new chat."
Write-Host "Then, sequentially provide the contents of each 'project_code_chunk_XX.txt' file listed in the manifest."