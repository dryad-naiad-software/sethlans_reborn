#!/bin/bash
#
# .SYNOPSIS
#     This script automates the creation of a new project and a variety of render jobs for manual stress testing.
# .DESCRIPTION
#     This script submits a heavy workload to stress-test the Sethlans Reborn system. It creates a new project,
#     uploads all necessary assets, and then queues a diverse set of 9 render jobs.
# .NOTES
#     Author: Mario Estrella
#     Date: 2025-08-07
#     Project: Sethlans Reborn

# --- Configuration ---
API_URL="http://127.0.0.1:7075/api"
# UPDATE THIS PATH if your project is located elsewhere.
BASE_ASSET_PATH="/home/mestrella/sethlans_reborn/tests/assets"
BMW_ASSET_PATH="$BASE_ASSET_PATH/bmw27.blend"
SIMPLE_SCENE_ASSET_PATH="$BASE_ASSET_PATH/test_scene.blend"
ANIMATION_ASSET_PATH="$BASE_ASSET_PATH/animation.blend"

# --- Logging ---
TIMESTAMP_LOG=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="stress_test_${TIMESTAMP_LOG}.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo -e "\n--- Starting Sethlans Reborn Stress Test ---"
echo "Full log will be saved to: $LOG_FILE"

# --- Pre-flight Check: Verify asset files exist ---
echo -e "\n[CHECK] Verifying asset files..."
ASSET_PATHS=("$BMW_ASSET_PATH" "$SIMPLE_SCENE_ASSET_PATH" "$ANIMATION_ASSET_PATH")
ASSETS_FOUND=true
for asset in "${ASSET_PATHS[@]}"; do
    if [ ! -f "$asset" ]; then
        echo "❌ Error: Asset file not found at '$asset'."
        ASSETS_FOUND=false
    fi
done

if [ "$ASSETS_FOUND" = false ]; then
    echo "Please update the BASE_ASSET_PATH variable in the script."
    exit 1
fi
echo "✅ All asset files found."

# --- Generate Unique Names ---
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
PROJECT_NAME="Stress-Test-Project-$TIMESTAMP"

# --- Job Counters ---
SUBMITTED_JOBS=0
FAILED_JOBS=0

# --- Helper Functions ---
# Handles JSON POST requests
invoke_api_call() {
    local url=$1
    local body=$2

    response=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -d "$body" \
        "$url")

    http_code=$(echo "$response" | tail -n1)
    response_body=$(echo "$response" | sed '$d')

    if [[ $http_code -ge 200 && $http_code -lt 300 ]]; then
        ((SUBMITTED_JOBS++))
        echo "$response_body"
    else
        echo "❌ Error: API call to '$url' failed." >&2
        echo "Status Code: $http_code" >&2
        echo "Response Body: $response_body" >&2
        ((FAILED_JOBS++))
        return 1
    fi
}

# Handles multipart/form-data (file upload) requests
upload_asset() {
    local name=$1
    local filepath=$2
    local project_id=$3

    echo "  Uploading '$name'..."

    response=$(curl -s -w "\n%{http_code}" -X POST \
        -F "project=$project_id" \
        -F "name=$name" \
        -F "blend_file=@$filepath" \
        "$API_URL/assets/")

    http_code=$(echo "$response" | tail -n1)
    response_body=$(echo "$response" | sed '$d')

    if [[ $http_code -ge 200 && $http_code -lt 300 ]]; then
        ((SUBMITTED_JOBS++))
        asset_id=$(echo "$response_body" | jq -r '.id')
        echo "  ✅ Asset '$name' uploaded with ID: $asset_id"
        echo "$asset_id"
    else
        echo "❌ Error: API call to upload asset '$name' failed." >&2
        echo "Status Code: $http_code" >&2
        echo "Response Body: $response_body" >&2
        ((FAILED_JOBS++))
        return 1
    fi
}


# --- Script Body ---
echo -e "\n[STEP 1/3] Creating project '$PROJECT_NAME'..."
PROJECT_PAYLOAD=$(jq -n --arg name "$PROJECT_NAME" '{name: $name}')
PROJECT_RESPONSE=$(invoke_api_call "$API_URL/projects/" "$PROJECT_PAYLOAD")
if [ $? -ne 0 ]; then exit 1; fi
PROJECT_ID=$(echo "$PROJECT_RESPONSE" | jq -r '.id')
echo "✅ Project created successfully with ID: $PROJECT_ID"

# --- Upload Assets ---
echo -e "\n[STEP 2/3] Uploading assets..."
BMW_ASSET_ID=$(upload_asset "BMW-Asset-$TIMESTAMP" "$BMW_ASSET_PATH" "$PROJECT_ID")
SIMPLE_SCENE_ASSET_ID=$(upload_asset "Simple-Scene-Asset-$TIMESTAMP" "$SIMPLE_SCENE_ASSET_PATH" "$PROJECT_ID")
ANIMATION_ASSET_ID=$(upload_asset "Animation-Asset-$TIMESTAMP" "$ANIMATION_ASSET_PATH" "$PROJECT_ID")
if [ -z "$BMW_ASSET_ID" ] || [ -z "$SIMPLE_SCENE_ASSET_ID" ] || [ -z "$ANIMATION_ASSET_ID" ]; then
    echo "❌ Asset upload failed. Exiting."
    exit 1
fi

# --- Submit Jobs ---
echo -e "\n[STEP 3/3] Submitting render jobs..."

# --- Job 1: GPU Saturation and CPU Fallback Test ---
echo -e "\n  --- Submitting Dual-GPU Saturation and CPU Fallback Test ---"
echo "    Submitting job to saturate GPU 0..."
GPU_SATURATE_PAYLOAD_1=$(jq -n \
    --arg name "GPU-Saturate-1-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$BMW_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "gpu_saturate_1_####", start_frame: 1, end_frame: 1, render_device: "GPU", render_settings: {"cycles.samples": 1024}}')
invoke_api_call "$API_URL/jobs/" "$GPU_SATURATE_PAYLOAD_1" > /dev/null

echo "    Submitting job to saturate GPU 1..."
GPU_SATURATE_PAYLOAD_2=$(jq -n \
    --arg name "GPU-Saturate-2-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$BMW_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "gpu_saturate_2_####", start_frame: 1, end_frame: 1, render_device: "GPU", render_settings: {"cycles.samples": 1024}}')
invoke_api_call "$API_URL/jobs/" "$GPU_SATURATE_PAYLOAD_2" > /dev/null

echo "    Submitting 'ANY' device job to test CPU fallback (4 frames)..."
CPU_FALLBACK_PAYLOAD=$(jq -n \
    --arg name "CPU-Fallback-Anim-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "cpu_fallback_####", start_frame: 1, end_frame: 4, render_device: "ANY", render_settings: {"cycles.samples": 500}}')
invoke_api_call "$API_URL/animations/" "$CPU_FALLBACK_PAYLOAD" > /dev/null

# --- Job 2: Standard Single-Frame CPU Job ---
echo -e "\n  Submitting standard single-frame CPU job..."
CPU_JOB_PAYLOAD=$(jq -n \
    --arg name "CPU-Single-Frame-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$SIMPLE_SCENE_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "cpu_render_####", start_frame: 1, end_frame: 1, render_device: "CPU", render_settings: {"cycles.samples": 16, "render.resolution_percentage": 25}}')
invoke_api_call "$API_URL/jobs/" "$CPU_JOB_PAYLOAD" > /dev/null

# --- Job 3: Long Multi-Frame Animation Job ---
echo "  Submitting long multi-frame animation job (75 frames)..."
ANIM_PAYLOAD=$(jq -n \
    --arg name "Long-Animation-720p-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "anim_render_720p_####", start_frame: 1, end_frame: 75, render_device: "ANY", render_settings: {"cycles.samples": 256, "render.resolution_x": 1280, "render.resolution_y": 720}}')
invoke_api_call "$API_URL/animations/" "$ANIM_PAYLOAD" > /dev/null

# --- Job 4: High-Quality GPU Tiled Job ---
echo "  Submitting high-quality GPU-accelerated tiled job (4x4 tiles)..."
TILED_PAYLOAD=$(jq -n \
    --arg name "GPU-Tiled-Job-1080p-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$BMW_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, final_resolution_x: 1920, final_resolution_y: 1080, tile_count_x: 4, tile_count_y: 4, render_device: "GPU", render_settings: {"cycles.samples": 512}}')
invoke_api_call "$API_URL/tiled-jobs/" "$TILED_PAYLOAD" > /dev/null

# --- Job 5: Animation with Frame Step ---
echo "  Submitting animation with frame step (100 frames, step 5)..."
FRAME_STEP_PAYLOAD=$(jq -n \
    --arg name "Frame-Step-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "frame_step_anim_####", start_frame: 1, end_frame: 100, frame_step: 5, render_device: "ANY", render_settings: {"cycles.samples": 128, "render.resolution_percentage": 75}}')
invoke_api_call "$API_URL/animations/" "$FRAME_STEP_PAYLOAD" > /dev/null

# --- Job 6: Tiled Animation (GPU) ---
echo "  Submitting tiled animation job (2x2 tiles)..."
TILED_ANIM_PAYLOAD=$(jq -n \
    --arg name "Tiled-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "tiled_anim_####", start_frame: 1, end_frame: 10, tiling_config: "2x2", render_device: "GPU", render_settings: {"cycles.samples": 64, "render.resolution_x": 1024, "render.resolution_y": 576}}')
invoke_api_call "$API_URL/animations/" "$TILED_ANIM_PAYLOAD" > /dev/null

# --- Job 7: CPU-intensive Tiled Job ---
echo "  Submitting CPU-intensive tiled job (3x3 tiles, 1024 samples)..."
CPU_TILED_PAYLOAD=$(jq -n \
    --arg name "CPU-Tiled-Job-High-Samples-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$BMW_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, final_resolution_x: 1280, final_resolution_y: 720, tile_count_x: 3, tile_count_y: 3, render_device: "CPU", render_settings: {"cycles.samples": 1024}}')
invoke_api_call "$API_URL/tiled-jobs/" "$CPU_TILED_PAYLOAD" > /dev/null

# --- Job 8: EEVEE Animation ---
echo "  Submitting EEVEE animation job (50 frames)..."
EEVEE_ANIM_PAYLOAD=$(jq -n \
    --arg name "EEVEE-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "eevee_anim_####", start_frame: 1, end_frame: 50, render_engine: "BLENDER_EEVEE_NEXT", render_device: "ANY", render_settings: {"eevee.taa_render_samples": 16, "render.resolution_percentage": 50}}')
invoke_api_call "$API_URL/animations/" "$EEVEE_ANIM_PAYLOAD" > /dev/null

# --- Job 9: Very Long Animation (GPU) ---
echo "  Submitting VERY long animation job (150 frames, 500 samples)..."
VERY_LONG_ANIM_PAYLOAD=$(jq -n \
    --arg name "Very-Long-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "very_long_anim_####", start_frame: 1, end_frame: 150, render_device: "GPU", render_settings: {"cycles.samples": 500}}')
invoke_api_call "$API_URL/animations/" "$VERY_LONG_ANIM_PAYLOAD" > /dev/null


# --- Summary ---
echo -e "\n--- Stress Test Summary ---"
echo "Successfully Submitted Jobs: $SUBMITTED_JOBS"
if [ $FAILED_JOBS -gt 0 ]; then
    echo "Failed Submissions: $FAILED_JOBS"
    echo -e "\n🚀 Some jobs failed to submit. Please check the log above. You can now start the worker agent to process the successful jobs."
else
    echo -e "\n🚀 All test jobs are queued! You can now start the worker agent."
fi