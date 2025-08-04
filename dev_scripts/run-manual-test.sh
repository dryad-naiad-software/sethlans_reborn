#!/bin/bash
#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 8/4/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# This script automates the creation of a new project and a variety of render jobs for manual testing.
# It now submits a heavier workload to stress-test the system:
# 1. A standard single-frame CPU job (low quality).
# 2. A long multi-frame animation job (HD 720p).
# 3. A high-quality, GPU-accelerated tiled render job (Full HD 1080p).
# 4. An animation job using the 'frame_step' feature over a long sequence.
# 5. A GPU-accelerated TILED ANIMATION job.
# 6. A high-sample CPU-only tiled job to test CPU-intensive tiling.
# 7. An EEVEE-based animation for variety.

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
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Logging ---
LOG_FILE="stress_test_$(date +"%Y%m%d_%H%M%S").log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo -e "${BLUE}--- Starting Sethlans Reborn Stress Test ---${NC}"
echo "Full log will be saved to: $LOG_FILE"

# --- Pre-flight Check: Verify asset files exist ---
echo -e "\n${YELLOW}[CHECK] Verifying asset files...${NC}"
for ASSET in "$BMW_ASSET_PATH" "$SIMPLE_SCENE_ASSET_PATH" "$ANIMATION_ASSET_PATH"; do
    if [ ! -f "$ASSET" ]; then
        echo -e "${RED}❌ Error: Asset file not found at '$ASSET'.${NC}"
        echo -e "${YELLOW}Please update the BASE_ASSET_PATH variable in the script.${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✅ All asset files found.${NC}"

# --- Generate Unique Names ---
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
PROJECT_NAME="Stress-Test-Project-$TIMESTAMP"

# --- Job Counters ---
SUBMITTED_JOBS=0
FAILED_JOBS=0

# --- Helper Functions ---
function check_curl_success {
    if ! [[ "$1" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}❌ Error: Invalid HTTP status code received. Curl may have failed before connecting.${NC}"
        FAILED_JOBS=$((FAILED_JOBS + 1))
        return 1
    fi
    if [ "$1" -lt 200 ] || [ "$1" -ge 300 ]; then
        echo -e "${RED}❌ Error: API call failed with status $1.${NC}"
        echo -e "${RED}Response Body: $2${NC}"
        FAILED_JOBS=$((FAILED_JOBS + 1))
        return 1
    fi
    return 0
}

function make_api_call {
    local METHOD="$1"
    local URL="$2"
    shift 2
    local CURL_ARGS=("$@")

    local TMP_BODY
    TMP_BODY=$(mktemp)
    trap 'rm -f "$TMP_BODY"' RETURN

    local HTTP_STATUS
    HTTP_STATUS=$(curl -s -o "$TMP_BODY" -w "%{http_code}" -X "$METHOD" "$URL" "${CURL_ARGS[@]}")

    local RESPONSE_BODY
    RESPONSE_BODY=$(cat "$TMP_BODY")

    if ! check_curl_success "$HTTP_STATUS" "$RESPONSE_BODY"; then
        return 1
    fi

    echo "$RESPONSE_BODY"
}

# --- Script Body ---
echo -e "\n${CYAN}[STEP 1/3] Creating project '$PROJECT_NAME'...${NC}"
PROJECT_PAYLOAD=$(jq -n --arg name "$PROJECT_NAME" '{name: $name}')
RESPONSE_BODY=$(make_api_call "POST" "$API_URL/projects/" -H "Content-Type: application/json" -d "$PROJECT_PAYLOAD")
if [ $? -ne 0 ]; then exit 1; fi
PROJECT_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')
echo -e "${GREEN}✅ Project created successfully with ID: $PROJECT_ID${NC}"

# --- Upload Assets ---
echo -e "\n${CYAN}[STEP 2/3] Uploading assets...${NC}"

function upload_asset {
    local NAME="$1"
    local FILE_PATH="$2"
    local PROJECT_ID="$3"

    echo -e "  Uploading '$NAME'..." >&2
    RESPONSE_BODY=$(make_api_call "POST" "$API_URL/assets/" -F "project=$PROJECT_ID" -F "name=$NAME" -F "blend_file=@$FILE_PATH")
    if [ $? -ne 0 ]; then return 1; fi

    ASSET_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')
    if [ -z "$ASSET_ID" ] || [ "$ASSET_ID" == "null" ]; then
        echo -e "${RED}❌ Error: Failed to get a valid Asset ID from the API response for '$NAME'.${NC}" >&2
        FAILED_JOBS=$((FAILED_JOBS + 1))
        return 1
    fi

    echo -e "${GREEN}  ✅ Asset '$NAME' uploaded with ID: $ASSET_ID${NC}" >&2
    echo "$ASSET_ID"
}

BMW_ASSET_ID=$(upload_asset "BMW-Asset-$TIMESTAMP" "$BMW_ASSET_PATH" "$PROJECT_ID")
SIMPLE_SCENE_ASSET_ID=$(upload_asset "Simple-Scene-Asset-$TIMESTAMP" "$SIMPLE_SCENE_ASSET_PATH" "$PROJECT_ID")
ANIMATION_ASSET_ID=$(upload_asset "Animation-Asset-$TIMESTAMP" "$ANIMATION_ASSET_PATH" "$PROJECT_ID")

# --- Submit Jobs ---
echo -e "\n${CYAN}[STEP 3/3] Submitting render jobs...${NC}"

function submit_job {
    local JOB_TYPE_URL="$1"
    local PAYLOAD="$2"
    local JOB_NAME="$3"

    echo "  Submitting $JOB_NAME..."
    RESPONSE=$(make_api_call "POST" "$JOB_TYPE_URL" -H "Content-Type: application/json" -d "$PAYLOAD")
    if [ $? -eq 0 ]; then
        SUBMITTED_JOBS=$((SUBMITTED_JOBS + 1))
        echo -e "${GREEN}  ✅ Job submitted with ID: $(echo $RESPONSE | jq -r '.id')${NC}"
    fi
}

# --- Job 1: Standard Single-Frame CPU Job ---
CPU_JOB_PAYLOAD=$(jq -n \
    --arg name "CPU-Single-Frame-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$SIMPLE_SCENE_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "cpu_render_####", start_frame: 1, end_frame: 1, render_device: "CPU", render_settings: {"cycles.samples": 16, "render.resolution_percentage": 25}}')
submit_job "$API_URL/jobs/" "$CPU_JOB_PAYLOAD" "standard single-frame CPU job"

# --- Job 2: Long Multi-Frame Animation Job (HD 720p on GPU) ---
ANIM_PAYLOAD=$(jq -n \
    --arg name "Long-Animation-720p-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "anim_render_720p_####", start_frame: 1, end_frame: 75, render_device: "GPU", render_settings: {"cycles.samples": 256, "render.resolution_x": 1280, "render.resolution_y": 720}}')
submit_job "$API_URL/animations/" "$ANIM_PAYLOAD" "long multi-frame animation job (75 frames)"

# --- Job 3: High-Quality GPU Tiled Job (Full HD 1080p) ---
TILED_PAYLOAD=$(jq -n \
    --arg name "GPU-Tiled-Job-1080p-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$BMW_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, final_resolution_x: 1920, final_resolution_y: 1080, tile_count_x: 4, tile_count_y: 4, render_device: "GPU", render_settings: {"cycles.samples": 512}}')
submit_job "$API_URL/tiled-jobs/" "$TILED_PAYLOAD" "high-quality GPU-accelerated tiled job (4x4 tiles)"

# --- Job 4: Animation with Frame Step (long sequence) ---
FRAME_STEP_PAYLOAD=$(jq -n \
    --arg name "Frame-Step-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "frame_step_anim_####", start_frame: 1, end_frame: 100, frame_step: 5, render_device: "GPU", render_settings: {"cycles.samples": 128, "render.resolution_percentage": 75}}')
submit_job "$API_URL/animations/" "$FRAME_STEP_PAYLOAD" "animation with frame step (100 frames, step 5)"

# --- Job 5: Tiled Animation (GPU) ---
TILED_ANIM_PAYLOAD=$(jq -n \
    --arg name "Tiled-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "tiled_anim_####", start_frame: 1, end_frame: 10, tiling_config: "2x2", render_device: "GPU", render_settings: {"cycles.samples": 64, "render.resolution_x": 1024, "render.resolution_y": 576}}')
submit_job "$API_URL/animations/" "$TILED_ANIM_PAYLOAD" "tiled animation job (2x2 tiles)"

# --- Job 6: CPU-intensive Tiled Job ---
CPU_TILED_PAYLOAD=$(jq -n \
    --arg name "CPU-Tiled-Job-High-Samples-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$BMW_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, final_resolution_x: 1280, final_resolution_y: 720, tile_count_x: 3, tile_count_y: 3, render_device: "CPU", render_settings: {"cycles.samples": 1024}}')
submit_job "$API_URL/tiled-jobs/" "$CPU_TILED_PAYLOAD" "CPU-intensive tiled job (3x3 tiles, 1024 samples)"

# --- Job 7: EEVEE Animation ---
EEVEE_ANIM_PAYLOAD=$(jq -n \
    --arg name "EEVEE-Animation-$TIMESTAMP" \
    --arg project_id "$PROJECT_ID" \
    --arg asset_id "$ANIMATION_ASSET_ID" \
    '{name: $name, project: $project_id, asset_id: $asset_id, output_file_pattern: "eevee_anim_####", start_frame: 1, end_frame: 50, render_engine: "BLENDER_EEVEE_NEXT", render_settings: {"eevee.taa_render_samples": 16, "render.resolution_percentage": 50}}')
submit_job "$API_URL/animations/" "$EEVEE_ANIM_PAYLOAD" "EEVEE animation job (50 frames)"

# --- Summary ---
echo -e "\n${BLUE}--- Stress Test Summary ---${NC}"
echo -e "${GREEN}Successfully Submitted Jobs: $SUBMITTED_JOBS${NC}"
if [ "$FAILED_JOBS" -gt 0 ]; then
    echo -e "${RED}Failed Submissions: $FAILED_JOBS${NC}"
    echo -e "\n${YELLOW}🚀 Some jobs failed to submit. Please check the log above. You can now start the worker agent to process the successful jobs.${NC}"
else
    echo -e "\n${YELLOW}🚀 All test jobs are queued! You can now start the worker agent.${NC}"
fi
