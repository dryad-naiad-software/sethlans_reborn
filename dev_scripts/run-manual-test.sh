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
# Created by Mario Estrella on 8/7/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# !/bin/bash
#
# A mini-script to quickly verify the CPU fallback logic on a multi-GPU worker.
# It creates a project, uploads one asset, and submits three identical, heavy
# jobs with 'render_device: ANY'.
#
# Expected behavior on a dual-GPU worker in split-mode:
# - 2 jobs are claimed by the GPUs.
# - 1 job is claimed by the CPU.

# --- Configuration ---
API_URL = "http://127.0.0.1:7075/api"
# UPDATE THIS PATH if your project is located elsewhere.
BASE_ASSET_PATH = "/home/mestrella/sethlans_reborn/tests/assets"
BMW_ASSET_PATH = "$BASE_ASSET_PATH/bmw27.blend"

# --- Generate Unique Names ---
TIMESTAMP =$(date + "%Y%m%d_%H%M%S")
PROJECT_NAME = "CPU-Fallback-Test-$TIMESTAMP"

# --- Helper Functions ---
function
invoke_api_call()
{
    local
url =$1
local
body =$2

response =$(curl - s - w "\n%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    -d "$body" \
    "$url")

http_code =$(echo "$response" | tail -n1)
response_body =$(echo "$response" | sed '$d')

if [[ $http_code - ge
200 & & $http_code - lt
300]]; then
echo
"$response_body"
else
echo
"❌ Error: API call to '$url' failed." > & 2
echo
"Status Code: $http_code" > & 2
echo
"Response Body: $response_body" > & 2
return 1
fi
}

function
upload_asset()
{
local
name =$1
local
filepath =$2
local
project_id =$3

echo
"  Uploading '$name'..." > & 2

response =$(curl - s - w "\n%{http_code}" -X POST \
    -F "project=$project_id" \
    -F "name=$name" \
    -F "blend_file=@$filepath" \
    "$API_URL/assets/")

http_code =$(echo "$response" | tail -n1)
response_body =$(echo "$response" | sed '$d')

if [[ $http_code -ge 200 & & $http_code -lt 300]]; then
asset_id =$(echo "$response_body" | jq -r '.id')
echo
"  ✅ Asset '$name' uploaded with ID: $asset_id" > & 2
echo
"$asset_id"
else
echo
"❌ Error: API call to upload asset '$name' failed." > & 2
echo
"Status Code: $http_code" > & 2
echo
"Response Body: $response_body" > & 2
return 1
fi
}

# --- Script Body ---
echo
"--- Starting Quick CPU Fallback Test ---"

# 1. Create Project
echo
"[1/3] Creating project '$PROJECT_NAME'..."
PROJECT_PAYLOAD =$(jq - n - -arg name "$PROJECT_NAME" '{name: $name}')
PROJECT_RESPONSE =$(invoke_api_call "$API_URL/projects/" "$PROJECT_PAYLOAD")
if [ $? -ne
0]; then
exit
1;
fi
PROJECT_ID =$(echo "$PROJECT_RESPONSE" | jq -r '.id')
echo
"✅ Project created successfully with ID: $PROJECT_ID"

# 2. Upload Asset
echo
"[2/3] Uploading asset..."
BMW_ASSET_ID =$(upload_asset "BMW-Asset-Fallback-Test-$TIMESTAMP" "$BMW_ASSET_PATH" "$PROJECT_ID")
if [-z "$BMW_ASSET_ID"];
then
echo
"❌ Asset upload failed. Exiting."
exit
1
fi

# 3. Submit Jobs
echo
"[3/3] Submitting 3 'ANY' device jobs to test natural concurrency..."
BASE_FALLBACK_PAYLOAD =$(jq - n \
                         - -arg project_id "$PROJECT_ID" \
                             --argjson asset_id "$BMW_ASSET_ID" \
                             '{project: $project_id, asset_id: $asset_id, output_file_pattern: "natural_fallback_####", start_frame: 1, end_frame: 1, render_device: "ANY", render_settings: {"cycles.samples": 1024}}')

for i in {1..3}; do
PAYLOAD_WITH_NAME=$(echo "$BASE_FALLBACK_PAYLOAD" | jq --arg name "Natural-Fallback-$i-$TIMESTAMP" '. + {name: $name}')
invoke_api_call "$API_URL/jobs/" "$PAYLOAD_WITH_NAME" > / dev / null
done

echo -e "\n🚀 All test jobs are queued! You can now start the worker agent."