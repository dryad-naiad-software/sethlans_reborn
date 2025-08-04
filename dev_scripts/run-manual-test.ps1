<#
.SYNOPSIS
    This script automates the creation of a new project and a variety of render jobs for manual stress testing.
.DESCRIPTION
    This script submits a heavy workload to stress-test the Sethlans Reborn system. It creates a new project,
    uploads all necessary assets, and then queues a diverse set of 7 render jobs, including:
    - A standard single-frame CPU job.
    - A long multi-frame animation job (HD 720p).
    - A high-quality, GPU-accelerated tiled render job (Full HD 1080p).
    - An animation job using the 'frame_step' feature.
    - A GPU-accelerated TILED ANIMATION job.
    - A high-sample CPU-only tiled job.
    - An EEVEE-based animation.
.NOTES
    Author: Mario Estrella
    Date: 2025-08-04
    Project: Sethlans Reborn
#>

# --- Configuration ---
$apiUrl = "http://127.0.0.1:7075/api"
# UPDATE THIS PATH if your project is located elsewhere.
$baseAssetPath = "C:\Users\mestrella\sethlans_reborn\tests\assets"
$bmwAssetPath = Join-Path $baseAssetPath "bmw27.blend"
$simpleSceneAssetPath = Join-Path $baseAssetPath "test_scene.blend"
$animationAssetPath = Join-Path $baseAssetPath "animation.blend"

# --- Logging ---
$timestampLog = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = "stress_test_$($timestampLog).log"
Start-Transcript -Path $logFile -Append

Write-Host "`n--- Starting Sethlans Reborn Stress Test ---" -ForegroundColor Cyan
Write-Host "Full log will be saved to: $logFile"

# --- Pre-flight Check: Verify asset files exist ---
Write-Host "`n[CHECK] Verifying asset files..." -ForegroundColor Yellow
$assetPaths = @($bmwAssetPath, $simpleSceneAssetPath, $animationAssetPath)
$assetsFound = $true
foreach ($asset in $assetPaths) {
    if (-not (Test-Path $asset -PathType Leaf)) {
        Write-Host "❌ Error: Asset file not found at '$asset'." -ForegroundColor Red
        $assetsFound = $false
    }
}

if (-not $assetsFound) {
    Write-Host "Please update the `$baseAssetPath variable in the script." -ForegroundColor Yellow
    exit 1
}
Write-Host "✅ All asset files found." -ForegroundColor Green

# --- Generate Unique Names ---
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$projectName = "Stress-Test-Project-$timestamp"

# --- Job Counters ---
$submittedJobs = 0
$failedJobs = 0

# --- Helper Function ---
function Invoke-ApiCall {
    param(
        [string]$Method,
        [string]$Url,
        [hashtable]$Headers = @{},
        [object]$Body,
        [string]$FormFileField,
        [string]$FormFilePath
    )

    $params = @{
        Method = $Method
        Uri = $Url
        Headers = $Headers
    }

    if ($Body) {
        $params.Body = ($Body | ConvertTo-Json -Depth 5)
        $params.ContentType = "application/json"
    }

    if ($FormFileField) {
        $form = New-Object System.Net.Http.MultipartFormDataContent
        foreach ($key in $Body.Keys) {
            $form.Add((New-Object System.Net.Http.StringContent($Body[$key])), $key)
        }
        $fileContent = [System.IO.File]::ReadAllBytes($FormFilePath)
        $fileStream = New-Object System.IO.MemoryStream(,$fileContent)
        $form.Add((New-Object System.Net.Http.StreamContent($fileStream)), $FormFileField, (Split-Path $FormFilePath -Leaf))
        $params.Body = $form
        $params.Remove("ContentType") # Let Invoke-RestMethod handle it
    }

    try {
        $response = Invoke-RestMethod @params
        $submittedJobs++
        return $response
    } catch {
        Write-Host "❌ Error: API call to '$Url' failed." -ForegroundColor Red
        Write-Host "Status Code: $($_.Exception.Response.StatusCode.value__)" -ForegroundColor Red
        Write-Host "Response Body: $($_.Exception.Response.GetResponseStream() | ForEach-Object { (New-Object System.IO.StreamReader($_)).ReadToEnd() })" -ForegroundColor Red
        $failedJobs++
        return $null
    }
}

# --- Script Body ---
Write-Host "`n[STEP 1/3] Creating project '$projectName'..." -ForegroundColor Cyan
$projectPayload = @{ name = $projectName }
$project = Invoke-ApiCall -Method "POST" -Url "$apiUrl/projects/" -Body $projectPayload
if (-not $project) { exit 1 }
$projectId = $project.id
Write-Host "✅ Project created successfully with ID: $projectId" -ForegroundColor Green

# --- Upload Assets ---
Write-Host "`n[STEP 2/3] Uploading assets..." -ForegroundColor Cyan

function Upload-Asset {
    param(
        [string]$Name,
        [string]$FilePath,
        [string]$ProjectId
    )
    Write-Host "  Uploading '$Name'..."
    $body = @{
        project = $ProjectId
        name = $Name
    }
    $asset = Invoke-ApiCall -Method "POST" -Url "$apiUrl/assets/" -Body $body -FormFileField "blend_file" -FormFilePath $FilePath
    if ($asset) {
        Write-Host "  ✅ Asset '$Name' uploaded with ID: $($asset.id)" -ForegroundColor Green
        return $asset.id
    }
    return $null
}

$bmwAssetId = Upload-Asset -Name "BMW-Asset-$timestamp" -FilePath $bmwAssetPath -ProjectId $projectId
$simpleSceneAssetId = Upload-Asset -Name "Simple-Scene-Asset-$timestamp" -FilePath $simpleSceneAssetPath -ProjectId $projectId
$animationAssetId = Upload-Asset -Name "Animation-Asset-$timestamp" -FilePath $animationAssetPath -ProjectId $projectId

# --- Submit Jobs ---
Write-Host "`n[STEP 3/3] Submitting render jobs..." -ForegroundColor Cyan

# --- Job 1: Standard Single-Frame CPU Job ---
Write-Host "  Submitting standard single-frame CPU job..."
$cpuJobPayload = @{
    name = "CPU-Single-Frame-$timestamp"
    project = $projectId
    asset_id = $simpleSceneAssetId
    output_file_pattern = "cpu_render_####"
    start_frame = 1
    end_frame = 1
    render_device = "CPU"
    render_settings = @{ "cycles.samples" = 16; "render.resolution_percentage" = 25 }
}
Invoke-ApiCall -Method "POST" -Url "$apiUrl/jobs/" -Body $cpuJobPayload | Out-Null

# --- Job 2: Long Multi-Frame Animation Job (HD 720p on GPU) ---
Write-Host "  Submitting long multi-frame animation job (75 frames)..."
$animPayload = @{
    name = "Long-Animation-720p-$timestamp"
    project = $projectId
    asset_id = $animationAssetId
    output_file_pattern = "anim_render_720p_####"
    start_frame = 1
    end_frame = 75
    render_device = "GPU"
    render_settings = @{ "cycles.samples" = 256; "render.resolution_x" = 1280; "render.resolution_y" = 720 }
}
Invoke-ApiCall -Method "POST" -Url "$apiUrl/animations/" -Body $animPayload | Out-Null

# --- Job 3: High-Quality GPU Tiled Job (Full HD 1080p) ---
Write-Host "  Submitting high-quality GPU-accelerated tiled job (4x4 tiles)..."
$tiledPayload = @{
    name = "GPU-Tiled-Job-1080p-$timestamp"
    project = $projectId
    asset_id = $bmwAssetId
    final_resolution_x = 1920
    final_resolution_y = 1080
    tile_count_x = 4
    tile_count_y = 4
    render_device = "GPU"
    render_settings = @{ "cycles.samples" = 512 }
}
Invoke-ApiCall -Method "POST" -Url "$apiUrl/tiled-jobs/" -Body $tiledPayload | Out-Null

# --- Job 4: Animation with Frame Step (long sequence) ---
Write-Host "  Submitting animation with frame step (100 frames, step 5)..."
$frameStepPayload = @{
    name = "Frame-Step-Animation-$timestamp"
    project = $projectId
    asset_id = $animationAssetId
    output_file_pattern = "frame_step_anim_####"
    start_frame = 1
    end_frame = 100
    frame_step = 5
    render_device = "GPU"
    render_settings = @{ "cycles.samples" = 128; "render.resolution_percentage" = 75 }
}
Invoke-ApiCall -Method "POST" -Url "$apiUrl/animations/" -Body $frameStepPayload | Out-Null

# --- Job 5: Tiled Animation (GPU) ---
Write-Host "  Submitting tiled animation job (2x2 tiles)..."
$tiledAnimPayload = @{
    name = "Tiled-Animation-$timestamp"
    project = $projectId
    asset_id = $animationAssetId
    output_file_pattern = "tiled_anim_####"
    start_frame = 1
    end_frame = 10
    tiling_config = "2x2"
    render_device = "GPU"
    render_settings = @{ "cycles.samples" = 64; "render.resolution_x" = 1024; "render.resolution_y" = 576 }
}
Invoke-ApiCall -Method "POST" -Url "$apiUrl/animations/" -Body $tiledAnimPayload | Out-Null

# --- Job 6: CPU-intensive Tiled Job ---
Write-Host "  Submitting CPU-intensive tiled job (3x3 tiles, 1024 samples)..."
$cpuTiledPayload = @{
    name = "CPU-Tiled-Job-High-Samples-$timestamp"
    project = $projectId
    asset_id = $bmwAssetId
    final_resolution_x = 1280
    final_resolution_y = 720
    tile_count_x = 3
    tile_count_y = 3
    render_device = "CPU"
    render_settings = @{ "cycles.samples" = 1024 }
}
Invoke-ApiCall -Method "POST" -Url "$apiUrl/tiled-jobs/" -Body $cpuTiledPayload | Out-Null

# --- Job 7: EEVEE Animation ---
Write-Host "  Submitting EEVEE animation job (50 frames)..."
$eeveeAnimPayload = @{
    name = "EEVEE-Animation-$timestamp"
    project = $projectId
    asset_id = $animationAssetId
    output_file_pattern = "eevee_anim_####"
    start_frame = 1
    end_frame = 50
    render_engine = "BLENDER_EEVEE_NEXT"
    render_settings = @{ "eevee.taa_render_samples" = 16; "render.resolution_percentage" = 50 }
}
Invoke-ApiCall -Method "POST" -Url "$apiUrl/animations/" -Body $eeveeAnimPayload | Out-Null


# --- Summary ---
Write-Host "`n--- Stress Test Summary ---" -ForegroundColor Cyan
Write-Host "Successfully Submitted Jobs: $submittedJobs" -ForegroundColor Green
if ($failedJobs -gt 0) {
    Write-Host "Failed Submissions: $failedJobs" -ForegroundColor Red
    Write-Host "`n🚀 Some jobs failed to submit. Please check the log above. You can now start the worker agent to process the successful jobs." -ForegroundColor Yellow
} else {
    Write-Host "`n🚀 All test jobs are queued! You can now start the worker agent." -ForegroundColor Yellow
}

Stop-Transcript
