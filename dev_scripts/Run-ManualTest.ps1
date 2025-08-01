# This script automates the creation of a new project, asset, and tiled job for testing.

# --- Configuration ---
$apiUrl = "http://127.0.0.1:8000/api"
$blendFilePath = "C:\Users\mestrella\Projects\sethlans_reborn\tests\assets\bmw27.blend"

# --- Generate Unique Names ---
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$projectName = "Auto-Project-$timestamp"
$tiledJobName = "Auto-Tiled-Job-$timestamp"
$assetName = "BMW-Asset-$timestamp"

# --- Script Body ---
try {
    # Step 1: Create a new Project
    Write-Host "Creating project '$projectName'..." -ForegroundColor Cyan
    $projectPayload = @{ name = $projectName } | ConvertTo-Json
    $projectResponse = Invoke-RestMethod -Uri "$apiUrl/projects/" -Method Post -ContentType 'application/json' -Body $projectPayload
    $projectId = $projectResponse.id
    Write-Host "✅ Project created successfully with ID: $projectId" -ForegroundColor Green

    # Step 2: Upload the .blend file as an Asset
    Write-Host "Uploading asset '$assetName'..." -ForegroundColor Cyan
    $assetForm = @{
        project = $projectId
        name    = $assetName
        blend_file = Get-Item -Path $blendFilePath
    }
    # Use -Form for multipart/form-data file uploads
    $assetResponse = Invoke-RestMethod -Uri "$apiUrl/assets/" -Method Post -Form $assetForm
    $assetId = $assetResponse.id
    Write-Host "✅ Asset uploaded successfully with ID: $assetId" -ForegroundColor Green

    # Step 3: Submit the Tiled Job
    Write-Host "Submitting tiled job '$tiledJobName'..." -ForegroundColor Cyan
    $tiledJobPayload = @{
        name                 = $tiledJobName
        project              = $projectId
        asset_id             = $assetId
        final_resolution_x   = 1280
        final_resolution_y   = 720
        tile_count_x         = 2
        tile_count_y         = 2
        render_settings      = @{
            "cycles.samples" = 100
        }
    } | ConvertTo-Json

    $jobResponse = Invoke-RestMethod -Uri "$apiUrl/tiled-jobs/" -Method Post -ContentType 'application/json' -Body $tiledJobPayload
    $tiledJobId = $jobResponse.id
    Write-Host "✅ Tiled job submitted successfully with ID: $tiledJobId" -ForegroundColor Green

    Write-Host "`n🚀 Test job is queued! You can now start the worker agent." -ForegroundColor Yellow

}
catch {
    Write-Host "❌ An error occurred:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
}