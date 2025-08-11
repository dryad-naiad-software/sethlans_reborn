# dump_project_code.ps1
# Stable version: no parameters, builds manifest in memory, strict chunking with splitting oversized files,
# and includes a sanity‑check summary (files processed, chunk counts, largest chunk size).

# ---------------------------
# CONFIGURATION
# ---------------------------

# Directory names to exclude entirely from the dump
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

# ► NEW: explicit file names (relative) to ignore
$ExcludeFiles = @(
    "chat_template.txt"   # ignore file sitting at the project root
)

# ---------------------------
# HELPER FUNCTIONS
# ---------------------------

function Test-PathContainsExcludedDir {
    param([string]$Path)
    $trimmed = $Path.TrimEnd('\','/')
    $parts   = $trimmed.Split([IO.Path]::DirectorySeparatorChar)
    foreach ($p in $parts) {
        if ($ExcludeNames -contains $p) { return $true }
    }
    return $false
}

# Recursive directory listing that honours both $ExcludeNames and $ExcludeFiles
# Replace your Get-TreeListing with this non-recursive version
function Get-TreeListing {
    param([Parameter(Mandatory)] [object]$Path, [int]$Depth = 0)

    try {
        $rootItem = Get-Item -LiteralPath $Path -ErrorAction Stop
        $root     = $rootItem.FullName

        function local:PathHasExcludedSegment([string]$rel) {
            if ([string]::IsNullOrEmpty($rel)) { return $false }
            foreach ($seg in ($rel -split '[\\/]+')) {
                if ($ExcludeNames -contains $seg) { return $true }
            }
            return $false
        }

        # Get all dirs/files once; skip reparse points (symlinks/junctions) to avoid cycles
        $dirs = Get-ChildItem -LiteralPath $root -Directory -Recurse -ErrorAction SilentlyContinue |
                Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
                Sort-Object FullName

        $files = Get-ChildItem -LiteralPath $root -File -Recurse -ErrorAction SilentlyContinue |
                 Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
                 Sort-Object FullName

        # Emit directories first (in path order)
        foreach ($d in $dirs) {
            $rel = $d.FullName.Substring($root.Length + 1)
            if (local:PathHasExcludedSegment $rel) { continue }
            $depth  = ($rel -split '[\\/]+').Count - 1
            $indent = '  ' * $depth
            $script:manifestLines += ("{0}|-- {1}" -f $indent, $d.Name)
        }

        # Then emit files (skip excluded paths and explicit file-name excludes)
        foreach ($f in $files) {
            $rel = $f.FullName.Substring($root.Length + 1)
            if (local:PathHasExcludedSegment ([System.IO.Path]::GetDirectoryName($rel))) { continue }
            if ($ExcludeFiles -contains $f.Name) { continue }
            $depth  = ($rel -split '[\\/]+').Count - 1
            $indent = '  ' * $depth
            $script:manifestLines += ("{0}|-- {1}" -f $indent, $f.Name)
        }
    } catch {
        $script:manifestLines += ("WARNING: Failed to list directory at {0}: {1}" -f $Path, $_.Exception.Message)
    }
}


# ---------------------------
# PROJECT ROOT DETECTION
# ---------------------------

$Current      = Get-Location
$currentPath  = $Current.Path
if ((Split-Path -Leaf $currentPath) -ieq 'dev_scripts') {
    $ProjectRootPath = Split-Path -Parent $currentPath
    if (-not $ProjectRootPath) { $ProjectRootPath = $currentPath }
} else {
    $ProjectRootPath = $currentPath
}
$ProjectRoot  = Get-Item -LiteralPath $ProjectRootPath  # normalize for .Name and .ToString()

# ---------------------------
# OUTPUT LOCATIONS
# ---------------------------

$OutputChunkDir = Join-Path $ProjectRoot "project_code_dump_chunks"
New-Item -ItemType Directory -Path $OutputChunkDir -Force | Out-Null
$ManifestFile   = Join-Path $OutputChunkDir "project_dump_manifest.txt"

# ---------------------------
# MANIFEST HEADER
# ---------------------------

$manifestLines = @()
$manifestLines += "--- START OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---"
$manifestLines += "Timestamp: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$manifestLines += ""
$manifestLines += "**INSTRUCTIONS FOR PROCESSING THIS CODEBASE:**"
$manifestLines += "1. **READ THIS MANIFEST FIRST.** It provides an overview and list of chunks."
$manifestLines += "2. **ACKNOWLEDGE RECEIPT OF THIS MANIFEST.** Confirm you understand the process."
$manifestLines += "3. **REQUEST CHUNKS SEQUENTIALLY.** After acknowledging, ask for the content of each chunk in order."
$manifestLines += "4. **CONFIRM RECEIPT OF EACH CHUNK.**"
$manifestLines += ""
$manifestLines += "--- END OF PROJECT CODE DUMP MANIFEST AND INSTRUCTIONS ---"
$manifestLines += ""

# ---------------------------
# DIRECTORY LISTING
# ---------------------------

$manifestLines += "--- START OF DIRECTORY LISTING ---"
$manifestLines += ("Project Root: {0}" -f $ProjectRoot.Name)
Get-TreeListing -Path $ProjectRoot.FullName -Depth 0   # use .FullName to pass a string path
$manifestLines += "--- END OF DIRECTORY LISTING ---"

# ---------------------------
# GIT LOG
# ---------------------------

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

# ---------------------------
# FILE SELECTION & CHUNKING
# ---------------------------

$FilesToDump = Get-ChildItem -Path $ProjectRoot -Recurse -Include "*.py", "*.ini", "*.txt" |
    Where-Object {
        -not (Test-PathContainsExcludedDir $_.Directory.FullName) -and
        ($ExcludeFiles -notcontains $_.Name)                # ► NEW filter
    } |
    Sort-Object FullName

$filesFound     = $FilesToDump.Count
$filesProcessed = 0
$filesFailed    = 0

$MaxChunkSizeChars = 100000
$chunks            = @()
$currentChunk      = ""

function Split-FormattedFileIntoPieces {
    param([string]$text, [int]$maxSize)
    $pieces = @()
    if ($text.Length -le $maxSize) { return ,$text }

    $lines  = $text -split "`n"
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
                    $slice   = $line.Substring($i, [Math]::Min($maxSize, $line.Length - $i))
                    $pieces += $slice
                    $i      += $slice.Length
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
    $pieces  = Split-FormattedFileIntoPieces -text $wrapped -maxSize $MaxChunkSizeChars

    foreach ($piece in $pieces) {
        if ($currentChunk.Length -gt 0 -and ($currentChunk.Length + $piece.Length) -gt $MaxChunkSizeChars) {
            $chunks += [pscustomobject]@{ Content = $currentChunk }
            $currentChunk = ""
        }
        $currentChunk += $piece
    }
}

if ($currentChunk.Length -gt 0) { $chunks += [pscustomobject]@{ Content = $currentChunk } }

# ---------------------------
# WRITE CHUNK FILES
# ---------------------------

$TotalChunks   = $chunks.Count
$chunkFileNames = @()
$chunkSizes     = @()

for ($i = 0; $i -lt $TotalChunks; $i++) {
    $num   = $i + 1
    $name  = "project_code_chunk_{0:02d}.txt" -f $num
    $path  = Join-Path $OutputChunkDir $name
    $header = "--- START OF CHUNK $num OF $TotalChunks ---"
    $footer = "`n--- END OF CHUNK $num OF $TotalChunks ---"
    $full   = $header + "`n" + $chunks[$i].Content + $footer

    Set-Content -LiteralPath $path -Value $full -Encoding Utf8

    $chunkFileNames += $name
    $chunkSizes     += $full.Length
    Write-Host "  Generated $name"
}

# ---------------------------
# SANITY‑CHECK SUMMARY
# ---------------------------

$largestChunkSize = 0
if ($chunkSizes.Count -gt 0) { $largestChunkSize = ($chunkSizes | Measure-Object -Maximum).Maximum }
$averageChunkSize = 0
if ($chunkSizes.Count -gt 0) { $averageChunkSize = [Math]::Round(($chunkSizes | Measure-Object -Average).Average) }
$totalChars       = ($chunkSizes | Measure-Object -Sum).Sum

$manifestLines += ""
$manifestLines += "**SANITY CHECK SUMMARY:**"
$manifestLines += ("Files matching patterns found: {0}"        -f $filesFound)
$manifestLines += ("Files successfully read: {0}"               -f $filesProcessed)
$manifestLines += ("Files failed to read: {0}"                   -f $filesFailed)
$manifestLines += ("Total chunks produced: {0}"                  -f $TotalChunks)
$manifestLines += ("Largest chunk size (chars): {0}"            -f $largestChunkSize)
$manifestLines += ("Average chunk size (chars): {0}"            -f $averageChunkSize)
$manifestLines += ("Total characters across chunks: {0}"        -f $totalChars)

# Append chunk list
$manifestLines += ""
$manifestLines += "**CODE CHUNKS TO PROVIDE (in order):**"
foreach ($n in $chunkFileNames) { $manifestLines += $n }

# ---------------------------
# WRITE MANIFEST
# ---------------------------

try {
    $manifestLines | Set-Content -LiteralPath $ManifestFile -Encoding Utf8
    Write-Host "Manifest written to: $ManifestFile"
} catch {
    Write-Error "Failed to write manifest: $($_.Exception.Message)"
}

# Optional console summary
Write-Host "Chunks written to: $OutputChunkDir"
