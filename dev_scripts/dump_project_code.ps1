# dump_project_code.ps1
# Stable version: no parameters, builds manifest in memory, strict chunking with splitting oversized files,
# and includes a sanity-check summary (files processed, chunk counts, largest chunk size).

# Determine project root. If the script is run from "dev_scripts" directory, treat its parent as the project root.
$Current = Get-Location
$currentPath = $Current.Path
if ((Split-Path -Leaf $currentPath) -ieq 'dev_scripts') {
    $ProjectRootPath = Split-Path -Parent $currentPath
    if (-not $ProjectRootPath) {
        # fallback to current if something weird happens
        $ProjectRootPath = $currentPath
    }
} else {
    $ProjectRootPath = $currentPath
}
# Normalize to an item so later .Name and ToString() work consistently
$ProjectRoot = Get-Item -LiteralPath $ProjectRootPath

$OutputChunkDir = Join-Path $ProjectRoot "project_code_dump_chunks"
New-Item -ItemType Directory -Path $OutputChunkDir -Force | Out-Null
$ManifestFile = Join-Path $OutputChunkDir "project_dump_manifest.txt"

$ExcludeNames = @(
    "venv",
    ".idea",
    "venv_worker",
    ".pytest_cache",
    "__pycache__",
    "managed_tools",
    "render_test_output",
    "project_code_dump_chunks",
    "dev_scripts"
)

function Test-PathContainsExcludedDir {
    param([string]$Path)
    $trimmed = $Path.TrimEnd('\','/')
    $parts = $trimmed.Split([IO.Path]::DirectorySeparatorChar)
    foreach ($p in $parts) {
        if ($ExcludeNames -contains $p) { return $true }
    }
    return $false
}

# Accumulate manifest lines
$manifestLines = @()

# Header / overview
$manifestLines += "--- START OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---"
$manifestLines += "Timestamp: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$manifestLines += ""
$manifestLines += "**INSTRUCTIONS FOR PROCESSING THIS CODEBASE:**"
$manifestLines += "1. **READ THIS MANIFEST FIRST.** It provides an overview and list of chunks."
$manifestLines += "2. **ACKNOWLEDGE RECEIPT OF THIS MANIFEST.** Confirm you understand the process."
$manifestLines += "3. **REQUEST CHUNKS SEQUENTIALLY.** After acknowledging, ask for the content of each chunk in order."
$manifestLines += "4. **CONFIRM RECEIPT OF EACH CHUNK.**"
$manifestLines += ""
$manifestLines += "**PROJECT OVERVIEW:**"
$manifestLines += "Project Name: Sethlans Reborn - Distributed Blender Rendering System"
$manifestLines += "Core Goal: Leverage multiple worker machines for Blender rendering jobs."
$manifestLines += "Architecture: Django Manager backend + Python Worker Agents."
$manifestLines += "Development Focus: Backend unit testing; frontend planned in Angular."
$manifestLines += ""
$manifestLines += "**CURRENT CODEBASE STATE:**"
$manifestLines += "Dump includes all .py, .ini, and .txt files as of the timestamp."
$manifestLines += "Unit test suite for 'sethlans_worker_agent' is partially complete (6 passing tests)."
$manifestLines += "Test 'generate_and_cache_blender_download_info' is currently paused due to mocking complexity."
$manifestLines += ""
$manifestLines += "--- END OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---"
$manifestLines += ""

# Directory listing
$manifestLines += "--- START OF DIRECTORY LISTING ---"
$manifestLines += ("Project Root: {0}" -f $ProjectRoot.Name)

function Get-TreeListing {
    param([string]$Path, [int]$Depth = 0)
    $indent = "  " * $Depth
    try {
        Get-ChildItem -LiteralPath $Path | Sort-Object @{Expression='PSIsContainer'; Descending=$true}, Name | ForEach-Object {
            if ($_.PSIsContainer -and $ExcludeNames -contains $_.Name) { return }
            if (-not $_.PSIsContainer -and (Test-PathContainsExcludedDir $_.Directory.FullName)) { return }

            if ($_.PSIsContainer) {
                $manifestLines += ("{0}|-- {1}" -f $indent, $_.Name)
                Get-TreeListing -Path $_.FullName -Depth ($Depth + 1)
            } else {
                $manifestLines += ("{0}|-- {1}" -f $indent, $_.Name)
            }
        }
    } catch {
        $manifestLines += ("WARNING: Failed to list directory at {0}: {1}" -f $Path, $_.Exception.Message)
    }
}

Get-TreeListing -Path $ProjectRoot -Depth 0
$manifestLines += "--- END OF DIRECTORY LISTING ---"

# Git log
$manifestLines += ""
$manifestLines += "--- START OF GIT LOG ---"
try {
    Push-Location $ProjectRoot
    $gitLogOutput = git log --pretty=fuller 2>&1
    $manifestLines += $gitLogOutput
    Pop-Location
} catch {
    $manifestLines += "ERROR: Could not get git log. Ensure Git is installed and repository is initialized."
    $manifestLines += $_.Exception.Message
}
$manifestLines += "--- END OF GIT LOG ---"

# Chunking logic
$FilesToDump = Get-ChildItem -Path $ProjectRoot -Recurse -Include "*.py", "*.ini", "*.txt" |
    Where-Object { -not (Test-PathContainsExcludedDir $_.Directory.FullName) } |
    Sort-Object FullName

$filesFound = $FilesToDump.Count
$filesProcessed = 0
$filesFailed = 0

$MaxChunkSizeChars = 40000
$chunks = @()
$currentChunk = ""

function Split-FormattedFileIntoPieces {
    param([string]$text, [int]$maxSize)
    $pieces = @()
    if ($text.Length -le $maxSize) { return ,$text }

    $lines = $text -split "`n"
    $buffer = ""
    foreach ($line in $lines) {
        $candidate = if ($buffer) { "$buffer`n$line" } else { $line }
        if ($candidate.Length -le $maxSize) {
            $buffer = $candidate
        } else {
            if ($buffer) { $pieces += $buffer }
            if ($line.Length -le $maxSize) {
                $buffer = $line
            } else {
                $i = 0
                while ($i -lt $line.Length) {
                    $slice = $line.Substring($i, [Math]::Min($maxSize, $line.Length - $i))
                    $pieces += $slice
                    $i += $slice.Length
                }
                $buffer = ""
            }
        }
    }
    if ($buffer) { $pieces += $buffer }
    return $pieces
}

foreach ($file in $FilesToDump) {
    $relativePath = $file.FullName.Replace($ProjectRoot.ToString() + "\", "")
    try {
        $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding Utf8
        $filesProcessed++
    } catch {
        Write-Warning ("Failed to read {0}: {1}" -f $relativePath, $_.Exception.Message)
        $filesFailed++
        continue
    }
    $wrapped = "`n--- FILE_START: $relativePath ---`n$content`n--- FILE_END: $relativePath ---"
    $pieces = Split-FormattedFileIntoPieces -text $wrapped -maxSize $MaxChunkSizeChars

    foreach ($piece in $pieces) {
        if ($currentChunk.Length -gt 0 -and ($currentChunk.Length + $piece.Length) -gt $MaxChunkSizeChars) {
            $chunks += [pscustomobject]@{ Content = $currentChunk }
            $currentChunk = ""
        }
        $currentChunk += $piece
    }
}

if ($currentChunk.Length -gt 0) {
    $chunks += [pscustomobject]@{ Content = $currentChunk }
}

# Write chunk files
$TotalChunks = $chunks.Count
$chunkFileNames = @()
$chunkSizes = @()

for ($i = 0; $i -lt $TotalChunks; $i++) {
    $num = $i + 1
    $name = "project_code_chunk_{0:02d}.txt" -f $num
    $path = Join-Path $OutputChunkDir $name
    $header = "--- START OF CHUNK $num OF $TotalChunks ---"
    $footer = "`n--- END OF CHUNK $num OF $TotalChunks ---"
    $full = $header + "`n" + $chunks[$i].Content + $footer
    Set-Content -LiteralPath $path -Value $full -Encoding Utf8

    $chunkFileNames += $name
    $chunkSizes += $full.Length
    Write-Host "  Generated $name"
}

# Sanity-check summary
$largestChunkSize = 0
if ($chunkSizes.Count -gt 0) {
    $largestChunkSize = ($chunkSizes | Measure-Object -Maximum).Maximum
}
$averageChunkSize = 0
if ($chunkSizes.Count -gt 0) {
    $averageChunkSize = [Math]::Round(($chunkSizes | Measure-Object -Average).Average)
}
$totalChars = ($chunkSizes | Measure-Object -Sum).Sum

$manifestLines += ""
$manifestLines += "**SANITY CHECK SUMMARY:**"
$manifestLines += ("Files matching patterns found: {0}" -f $filesFound)
$manifestLines += ("Files successfully read: {0}" -f $filesProcessed)
$manifestLines += ("Files failed to read: {0}" -f $filesFailed)
$manifestLines += ("Total chunks produced: {0}" -f $TotalChunks)
$manifestLines += ("Largest chunk size (chars): {0}" -f $largestChunkSize)
$manifestLines += ("Average chunk size (chars): {0}" -f $averageChunkSize)
$manifestLines += ("Total characters across chunks: {0}" -f $totalChars)

# Append chunk list to manifest
$manifestLines += ""
$manifestLines += "**CODE CHUNKS TO PROVIDE (in order):**"
foreach ($n in $chunkFileNames) {
    $manifestLines += $n
}

# === FIXED PART: actually write the manifest to disk ===
try {
    $manifestLines | Set-Content -LiteralPath $ManifestFile -Encoding Utf8
    Write-Host "Manifest written to: $ManifestFile"
} catch {
    Write-Error "Failed to write manifest: $($_.Exception.Message)"
}

# Optional: output summary to console as well
Write-Host "Chunks written to: $OutputChunkDir"
