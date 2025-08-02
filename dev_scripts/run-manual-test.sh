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
# CORRECTED: Use a valid Linux path. Update if your project is elsewhere.
BASE_ASSET_PATH="/home/mestrella/sethlans_reborn/tests/assets"
BMW_ASSET_PATH="$BASE_ASSET_PATH/bmw27.blend"
SIMPLE_SCENE_ASSET_PATH="$BASE_ASSET_PATH/test_scene.blend"
ANIMATION_ASSET_PATH="$BASE_ASSET_PATH/animation.blend"

# --- Colors for output ---
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# --- Pre-flight Check: Verify asset files exist ---
for ASSET in "$BMW_ASSET_PATH" "$SIMPLE_SCENE_ASSET_PATH" "$ANIMATION_ASSET_PATH"; do
    if [ ! -f "$ASSET" ]; then
        echo -e "${RED}❌ Error: Asset file not found at '$ASSET'.${NC}"
        echo -e "${YELLOW}Please update the BASE_ASSET_PATH variable in the script.${NC}"
        exit 1
    fi
done

# --- Generate Unique Names ---
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
PROJECT_NAME="Stress-Test-Project-$TIMESTAMP"

# --- Helper Functions ---
function check_curl_success {
    # This function checks if the HTTP status code ($1) is a success code (2xx).
    # If it's not, it prints the error and the response body ($2) and exits.
    if ! [[ "$1" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}❌ Error: Invalid HTTP status code received. Curl may have failed before connecting.${NC}"
        exit 1
    fi
    if [ "$1" -lt 200 ] || [ "$1" -ge 300 ]; then
        echo -e "${RED}❌ Error: API call failed with status $1.${NC}"
        echo -e "${RED}Response Body: $2${NC}"
        exit 1
    fi
}

function make_api_call {
    # A robust function to make a curl request, separating the response body
    # from the HTTP status code to prevent parsing errors.
    local METHOD="$1"
    local URL="$2"
    shift 2
    local CURL_ARGS=("$@")

    local TMP_BODY
    TMP_BODY=$(mktemp)
    # Ensure the temporary file is removed when the function exits
    trap 'rm -f "$TMP_BODY"' RETURN

    local HTTP_STATUS
    HTTP_STATUS=$(curl -s -o "$TMP_BODY" -w "%{http_code}" -X "$METHOD" "$URL" "${CURL_ARGS[@]}")

    local RESPONSE_BODY
    RESPONSE_BODY=$(cat "$TMP_BODY")

    check_curl_success "$HTTP_STATUS" "$RESPONSE_BODY"

    # Return the body by echoing it
    echo "$RESPONSE_BODY"
}


# --- Script Body ---
echo -e "${CYAN}Creating project '$PROJECT_NAME'...${NC}"
PROJECT_PAYLOAD=$(jq -n --arg name "$PROJECT_NAME" '{name: $name}')
RESPONSE_BODY=$(make_api_call "POST" "$API_URL/projects/" -H "Content-Type: application/json" -d "$PROJECT_PAYLOAD")
PROJECT_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')
echo -e "${GREEN}✅ Project created successfully with ID: $PROJECT_ID${NC}"


# --- Upload Assets ---
echo -e "\n${CYAN}Uploading assets...${NC}"

function upload_asset {
    local NAME="$1"
    local FILE_PATH="$2"
    local PROJECT_ID="$3"

    # CORRECTED: Redirect progress messages to stderr (>&2) so they appear on the
    # console but are not captured by the command substitution that calls this function.
    echo -e "  Uploading '$NAME'..." >&2
    RESPONSE_BODY=$(make_api_call "POST" "$API_URL/assets/" -F "project=$PROJECT_ID" -F "name=$NAME" -F "blend_file=@$FILE_PATH")

    # Explicitly check if the response body is valid JSON and contains an ID
    ASSET_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')
    if [ -z "$ASSET_ID" ] || [ "$ASSET_ID" == "null" ]; then
        echo -e "${RED}❌ Error: Failed to get a valid Asset ID from the API response for '$NAME'.${NC}" >&2
        echo -e "${RED}Response Body: $RESPONSE_BODY${NC}" >&2
        exit 1
    fi

    echo -e "${GREEN}  ✅ Asset '$NAME' uploaded with ID: $ASSET_ID${NC}" >&2
    # Return ONLY the ID to stdout so it can be captured by the calling variable.
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
CPU_JOB_RESPONSE=$(make_api_call "POST" "$API_URL/jobs/" -H "Content-Type: application/json" -d "$CPU_JOB_PAYLOAD")
echo -e "${GREEN}  ✅ CPU job submitted with ID: $(echo $CPU_JOB_RESPONSE | jq -r '.id')${NC}"

# --- Job 2: Long Multi-Frame Animation Job (HD 720p on GPU) ---
echo "  Submitting long multi-frame animation job (75 frames)..."
ANIM_PAYLOAD=$(jq -n \
    --arg name "Long-Animation-720p-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "anim_render_720p_####", start_frame: 1, end_frame: 75, render_device: "GPU", render_settings: {"cycles.samples": 256, "render.resolution_x": 1280, "render.resolution_y": 720}}')
ANIM_RESPONSE=$(make_api_call "POST" "$API_URL/animations/" -H "Content-Type: application/json" -d "$ANIM_PAYLOAD")
echo -e "${GREEN}  ✅ Animation job submitted with ID: $(echo $ANIM_RESPONSE | jq -r '.id')${NC}"

# --- Job 3: High-Quality GPU Tiled Job (Full HD 1080p) ---
echo "  Submitting high-quality GPU-accelerated tiled job (4x4 tiles)..."
TILED_PAYLOAD=$(jq -n \
    --arg name "GPU-Tiled-Job-1080p-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$BMW_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, final_resolution_x: 1920, final_resolution_y: 1080, tile_count_x: 4, tile_count_y: 4, render_device: "GPU", render_settings: {"cycles.samples": 512}}')
TILED_RESPONSE=$(make_api_call "POST" "$API_URL/tiled-jobs/" -H "Content-Type: application/json" -d "$TILED_PAYLOAD")
echo -e "${GREEN}  ✅ Tiled job submitted with ID: $(echo $TILED_RESPONSE | jq -r '.id')${NC}"

# --- Job 4: Animation with Frame Step (long sequence) ---
echo "  Submitting animation with frame step (100 frames, step 5)..."
FRAME_STEP_PAYLOAD=$(jq -n \
    --arg name "Frame-Step-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "frame_step_anim_####", start_frame: 1, end_frame: 100, frame_step: 5, render_device: "GPU", render_settings: {"cycles.samples": 128, "render.resolution_percentage": 75}}')
FRAME_STEP_RESPONSE=$(make_api_call "POST" "$API_URL/animations/" -H "Content-Type: application/json" -d "$FRAME_STEP_PAYLOAD")
echo -e "${GREEN}  ✅ Frame step animation submitted with ID: $(echo $FRAME_STEP_RESPONSE | jq -r '.id')${NC}"

# --- Job 5: Tiled Animation (NEW) ---
echo "  Submitting tiled animation job (2x2 tiles)..."
TILED_ANIM_PAYLOAD=$(jq -n \
    --arg name "Tiled-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "tiled_anim_####", start_frame: 1, end_frame: 10, tiling_config: "2x2", render_device: "GPU", render_settings: {"cycles.samples": 64, "render.resolution_x": 1024, "render.resolution_y": 576}}')
TILED_ANIM_RESPONSE=$(make_api_call "POST" "$API_URL/animations/" -H "Content-Type: application/json" -d "$TILED_ANIM_PAYLOAD")
echo -e "${GREEN}  ✅ Tiled animation submitted with ID: $(echo $TILED_ANIM_RESPONSE | jq -r '.id')${NC}"


echo -e "\n${YELLOW}🚀 All test jobs are queued! You can now start the worker agent.${NC}"
