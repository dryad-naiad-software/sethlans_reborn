# This script automates the creation of a new project and a variety of render jobs for manual testing.
# It now submits a heavier workload to stress-test the system:
# 1. A standard single-frame CPU job (low quality).
# 2. A long multi-frame animation job (HD 720p).
# 3. A high-quality, GPU-accelerated tiled render job (Full HD 1080p).
# 4. An animation job using the 'frame_step' feature over a long sequence.
# 5. A GPU-accelerated TILED ANIMATION job (new).

# --- Configuration ---
$apiUrl = "http://127.0.0.1:7075/api"
$baseAssetPath = "C:\Users\mestrella\Projects\sethlans_reborn\tests\assets"
$bmwAssetPath = Join-Path $baseAssetPath "bmw27.blend"
$simpleSceneAssetPath = Join-Path $baseAssetPath "test_scene.blend"
$animationAssetPath = Join-Path $baseAssetPath "animation.blend"

# --- Generate Unique Names ---
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$projectName = "Stress-Test-Project-$timestamp"

# --- Script Body ---
try {
    # Step 1: Create a new Project
    Write-Host "Creating project '$projectName'..." -ForegroundColor Cyan
    $projectPayload = @{ name = $projectName } | ConvertTo-Json
    $projectResponse = Invoke-RestMethod -Uri "$apiUrl/projects/" -Method Post -ContentType 'application/json' -Body $projectPayload
    $projectId = $projectResponse.id
    Write-Host "✅ Project created successfully with ID: $projectId" -ForegroundColor Green

    # Step 2: Upload all required .blend files as Assets
    Write-Host "`nUploading assets..." -ForegroundColor Cyan

    # Function to simplify asset uploading
    function Upload-Asset {
        param(
            [string]$Name,
            [string]$FilePath,
            [string]$ProjectId
        )
        Write-Host "  Uploading '$Name'..."
        $assetForm = @{
            project = $ProjectId
            name    = $Name
            blend_file = Get-Item -Path $FilePath
        }
        $assetResponse = Invoke-RestMethod -Uri "$apiUrl/assets/" -Method Post -Form $assetForm
        Write-Host "  ✅ Asset '$Name' uploaded with ID: $($assetResponse.id)" -ForegroundColor Green
        return $assetResponse.id
    }

    $bmwAssetId = Upload-Asset -Name "BMW-Asset-$timestamp" -FilePath $bmwAssetPath -ProjectId $projectId
    $simpleSceneAssetId = Upload-Asset -Name "Simple-Scene-Asset-$timestamp" -FilePath $simpleSceneAssetPath -ProjectId $projectId
    $animationAssetId = Upload-Asset -Name "Animation-Asset-$timestamp" -FilePath $animationAssetPath -ProjectId $projectId

    # Step 3: Submit a variety of jobs
    Write-Host "`nSubmitting render jobs..." -ForegroundColor Cyan

    # --- Job 1: Standard Single-Frame CPU Job ---
    Write-Host "  Submitting standard single-frame CPU job..."
    $cpuJobPayload = @{
        name                 = "CPU-Single-Frame-$timestamp"
        project              = $projectId
        asset_id             = $simpleSceneAssetId
        output_file_pattern  = "cpu_render_####"
        start_frame          = 1
        end_frame            = 1
        render_device        = "CPU"
        render_settings      = @{
            "cycles.samples" = 16
            "render.resolution_percentage" = 25
        }
    } | ConvertTo-Json
    $cpuJobResponse = Invoke-RestMethod -Uri "$apiUrl/jobs/" -Method Post -ContentType 'application/json' -Body $cpuJobPayload
    Write-Host "  ✅ CPU job submitted with ID: $($cpuJobResponse.id)" -ForegroundColor Green

    # --- Job 2: Long Multi-Frame Animation Job (HD 720p on GPU) ---
    Write-Host "  Submitting long multi-frame animation job (75 frames)..."
    $animationPayload = @{
        name                 = "Long-Animation-720p-$timestamp"
        project              = $projectId
        asset_id             = $animationAssetId
        output_file_pattern  = "anim_render_720p_####"
        start_frame          = 1
        end_frame            = 75 # Increased frame count
        render_device        = "GPU" # Target GPU
        render_settings      = @{
            "cycles.samples" = 256 # Higher quality
            "render.resolution_x" = 1280
            "render.resolution_y" = 720
        }
    } | ConvertTo-Json
    $animResponse = Invoke-RestMethod -Uri "$apiUrl/animations/" -Method Post -ContentType 'application/json' -Body $animationPayload
    Write-Host "  ✅ Animation job submitted with ID: $($animResponse.id)" -ForegroundColor Green

    # --- Job 3: High-Quality GPU Tiled Job (Full HD 1080p) ---
    Write-Host "  Submitting high-quality GPU-accelerated tiled job (4x4 tiles)..."
    $tiledJobPayload = @{
        name                 = "GPU-Tiled-Job-1080p-$timestamp"
        project              = $projectId
        asset_id             = $bmwAssetId
        final_resolution_x   = 1920 # Full HD
        final_resolution_y   = 1080
        tile_count_x         = 4 # Increased tile count
        tile_count_y         = 4
        render_device        = "GPU"
        render_settings      = @{
            "cycles.samples" = 512 # Higher quality samples
        }
    } | ConvertTo-Json
    $tiledJobResponse = Invoke-RestMethod -Uri "$apiUrl/tiled-jobs/" -Method Post -ContentType 'application/json' -Body $tiledJobPayload
    Write-Host "  ✅ Tiled job submitted with ID: $($tiledJobResponse.id)" -ForegroundColor Green

    # --- Job 4: Animation with Frame Step (long sequence) ---
    Write-Host "  Submitting animation with frame step (100 frames, step 5)..."
    $frameStepPayload = @{
        name                 = "Frame-Step-Animation-$timestamp"
        project              = $projectId
        asset_id             = $animationAssetId
        output_file_pattern  = "frame_step_anim_####"
        start_frame          = 1
        end_frame            = 100 # Increased frame count
        frame_step           = 5 # Render every 5th frame
        render_device        = "GPU"
        render_settings      = @{
            "cycles.samples" = 128
            "render.resolution_percentage" = 75
        }
    } | ConvertTo-Json
    $frameStepResponse = Invoke-RestMethod -Uri "$apiUrl/animations/" -Method Post -ContentType 'application/json' -Body $frameStepPayload
    Write-Host "  ✅ Frame step animation submitted with ID: $($frameStepResponse.id)" -ForegroundColor Green

    # --- Job 5: Tiled Animation (NEW) ---
    Write-Host "  Submitting tiled animation job (2x2 tiles)..."
    $tiledAnimationPayload = @{
        name                 = "Tiled-Animation-$timestamp"
        project              = $projectId
        asset_id             = $animationAssetId
        output_file_pattern  = "tiled_anim_####"
        start_frame          = 1
        end_frame            = 10 # Short but complex
        tiling_config        = "2x2"
        render_device        = "GPU"
        render_settings      = @{
            "cycles.samples" = 64
            "render.resolution_x" = 1024
            "render.resolution_y" = 576
        }
    } | ConvertTo-Json
    $tiledAnimResponse = Invoke-RestMethod -Uri "$apiUrl/animations/" -Method Post -ContentType 'application/json' -Body $tiledAnimationPayload
    Write-Host "  ✅ Tiled animation submitted with ID: $($tiledAnimResponse.id)" -ForegroundColor Green

    Write-Host "`n🚀 All test jobs are queued! You can now start the worker agent." -ForegroundColor Yellow

}
catch {
    Write-Host "❌ An error occurred:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.Exception.Response) {
        $errorBody = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($errorBody)
        $errorText = $reader.ReadToEnd()
        Write-Host "Server Response: $errorText" -ForegroundColor Red
    }
}
