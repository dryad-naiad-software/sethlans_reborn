#!/bin/bash

# This script automates the creation of a new project and a variety of render jobs for manual testing.
# It now submits a heavier workload to stress-test the system:
# 1. A standard single-frame CPU job (low quality).
# 2. A long multi-frame animation job (HD 720p).
# 3. A high-quality, GPU-accelerated tiled render job (Full HD 1080p).
# 4. An animation job using the 'frame_step' feature over a long sequence.
# 5. A GPU-accelerated TILED ANIMATION job.

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Configuration ---
API_URL="http://127.0.0.1:7075/api"
# IMPORTANT: Update this path to the correct location on your system.
BASE_ASSET_PATH="/c/Users/mestrella/Projects/sethlans_reborn/tests/assets"
BMW_ASSET_PATH="$BASE_ASSET_PATH/bmw27.blend"
SIMPLE_SCENE_ASSET_PATH="$BASE_ASSET_PATH/test_scene.blend"
ANIMATION_ASSET_PATH="$BASE_ASSET_PATH/animation.blend"

# --- Colors for output ---
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# --- Generate Unique Names ---
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
PROJECT_NAME="Stress-Test-Project-$TIMESTAMP"

# --- Helper Functions ---
function check_curl_success {
    if [ "$1" -lt 200 ] || [ "$1" -ge 300 ]; then
        echo -e "${RED}❌ Error: API call failed with status $1.${NC}"
        echo -e "${RED}Response Body: $2${NC}"
        exit 1
    fi
}

# --- Script Body ---
echo -e "${CYAN}Creating project '$PROJECT_NAME'...${NC}"
PROJECT_PAYLOAD=$(jq -n --arg name "$PROJECT_NAME" '{name: $name}')
PROJECT_RESPONSE=$(curl -s -w "%{http_code}" -X POST "$API_URL/projects/" \
    -H "Content-Type: application/json" \
    -d "$PROJECT_PAYLOAD")
HTTP_STATUS=$(tail -n1 <<< "$PROJECT_RESPONSE")
RESPONSE_BODY=$(sed '$ d' <<< "$PROJECT_RESPONSE")
check_curl_success "$HTTP_STATUS" "$RESPONSE_BODY"
PROJECT_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')
echo -e "${GREEN}✅ Project created successfully with ID: $PROJECT_ID${NC}"


# --- Upload Assets ---
echo -e "\n${CYAN}Uploading assets...${NC}"

function upload_asset {
    local NAME="$1"
    local FILE_PATH="$2"
    local PROJECT_ID="$3"

    echo -e "  Uploading '$NAME'..."
    RESPONSE=$(curl -s -w "%{http_code}" -X POST "$API_URL/assets/" \
        -F "project=$PROJECT_ID" \
        -F "name=$NAME" \
        -F "blend_file=@$FILE_PATH")

    HTTP_STATUS=$(tail -n1 <<< "$RESPONSE")
    RESPONSE_BODY=$(sed '$ d' <<< "$RESPONSE")
    check_curl_success "$HTTP_STATUS" "$RESPONSE_BODY"

    ASSET_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')
    echo -e "${GREEN}  ✅ Asset '$NAME' uploaded with ID: $ASSET_ID${NC}"
    # Return the ID by echoing it
    echo "$ASSET_ID"
}

BMW_ASSET_ID=$(upload_asset "BMW-Asset-$TIMESTAMP" "$BMW_ASSET_PATH" "$PROJECT_ID")
SIMPLE_SCENE_ASSET_ID=$(upload_asset "Simple-Scene-Asset-$TIMESTAMP" "$SIMPLE_SCENE_ASSET_PATH" "$PROJECT_ID")
ANIMATION_ASSET_ID=$(upload_asset "Animation-Asset-$TIMESTAMP" "$ANIMATION_ASSET_PATH" "$PROJECT_ID")


# --- Submit Jobs ---
echo -e "\n${CYAN}Submitting render jobs...${NC}"

# --- Job 1: Standard Single-Frame CPU Job ---
echo "  Submitting standard single-frame CPU job..."
CPU_JOB_PAYLOAD=$(jq -n \
    --arg name "CPU-Single-Frame-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$SIMPLE_SCENE_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "cpu_render_####", start_frame: 1, end_frame: 1, render_device: "CPU", render_settings: {"cycles.samples": 16, "render.resolution_percentage": 25}}')
CPU_JOB_RESPONSE=$(curl -s -X POST "$API_URL/jobs/" -H "Content-Type: application/json" -d "$CPU_JOB_PAYLOAD" | jq)
echo -e "${GREEN}  ✅ CPU job submitted with ID: $(echo $CPU_JOB_RESPONSE | jq -r '.id')${NC}"

# --- Job 2: Long Multi-Frame Animation Job (HD 720p on GPU) ---
echo "  Submitting long multi-frame animation job (75 frames)..."
ANIM_PAYLOAD=$(jq -n \
    --arg name "Long-Animation-720p-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "anim_render_720p_####", start_frame: 1, end_frame: 75, render_device: "GPU", render_settings: {"cycles.samples": 256, "render.resolution_x": 1280, "render.resolution_y": 720}}')
ANIM_RESPONSE=$(curl -s -X POST "$API_URL/animations/" -H "Content-Type: application/json" -d "$ANIM_PAYLOAD" | jq)
echo -e "${GREEN}  ✅ Animation job submitted with ID: $(echo $ANIM_RESPONSE | jq -r '.id')${NC}"

# --- Job 3: High-Quality GPU Tiled Job (Full HD 1080p) ---
echo "  Submitting high-quality GPU-accelerated tiled job (4x4 tiles)..."
TILED_PAYLOAD=$(jq -n \
    --arg name "GPU-Tiled-Job-1080p-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$BMW_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, final_resolution_x: 1920, final_resolution_y: 1080, tile_count_x: 4, tile_count_y: 4, render_device: "GPU", render_settings: {"cycles.samples": 512}}')
TILED_RESPONSE=$(curl -s -X POST "$API_URL/tiled-jobs/" -H "Content-Type: application/json" -d "$TILED_PAYLOAD" | jq)
echo -e "${GREEN}  ✅ Tiled job submitted with ID: $(echo $TILED_RESPONSE | jq -r '.id')${NC}"

# --- Job 4: Animation with Frame Step (long sequence) ---
echo "  Submitting animation with frame step (100 frames, step 5)..."
FRAME_STEP_PAYLOAD=$(jq -n \
    --arg name "Frame-Step-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "frame_step_anim_####", start_frame: 1, end_frame: 100, frame_step: 5, render_device: "GPU", render_settings: {"cycles.samples": 128, "render.resolution_percentage": 75}}')
FRAME_STEP_RESPONSE=$(curl -s -X POST "$API_URL/animations/" -H "Content-Type: application/json" -d "$FRAME_STEP_PAYLOAD" | jq)
echo -e "${GREEN}  ✅ Frame step animation submitted with ID: $(echo $FRAME_STEP_RESPONSE | jq -r '.id')${NC}"

# --- Job 5: Tiled Animation (NEW) ---
echo "  Submitting tiled animation job (2x2 tiles)..."
TILED_ANIM_PAYLOAD=$(jq -n \
    --arg name "Tiled-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "tiled_anim_####", start_frame: 1, end_frame: 10, tiling_config: "2x2", render_device: "GPU", render_settings: {"cycles.samples": 64, "render.resolution_x": 1024, "render.resolution_y": 576}}')
TILED_ANIM_RESPONSE=$(curl -s -X POST "$API_URL/animations/" -H "Content-Type: application/json" -d "$TILED_ANIM_PAYLOAD" | jq)
echo -e "${GREEN}  ✅ Tiled animation submitted with ID: $(echo $TILED_ANIM_RESPONSE | jq -r '.id')${NC}"


echo -e "\n${YELLOW}🚀 All test jobs are queued! You can now start the worker agent.${NC}"
