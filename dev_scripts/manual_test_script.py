import requests
import time
import os
import io
from pathlib import Path
from PIL import Image

# Import the config module to get the API URL
from sethlans_worker_agent import config as worker_config

# --- Configuration ---
API_BASE_URL = worker_config.MANAGER_API_URL.rstrip('/')
PROJECT_ROOT = Path(__file__).resolve().parent
# Make sure this path points to a .blend file with an animation
ASSET_FILE_PATH = PROJECT_ROOT / "tests" / "assets" / "animation.blend"
OUTPUT_DIR = PROJECT_ROOT / "manual_test_output"


def create_project(session):
    """Creates a new project for the test run."""
    project_name = f"Manual-Test-Project-{int(time.time())}"
    print(f"1. Creating project: '{project_name}'...")
    response = session.post(f"{API_BASE_URL}/projects/", json={"name": project_name})
    response.raise_for_status()
    project_data = response.json()
    print(f"   Project created successfully with ID: {project_data['id']}\n")
    return project_data['id']


def upload_asset(session, project_id):
    """Uploads the .blend file as a new asset."""
    asset_name = f"Manual-Test-Asset-{int(time.time())}"
    print(f"2. Uploading asset: '{asset_name}'...")
    if not ASSET_FILE_PATH.exists():
        raise FileNotFoundError(f"Asset file not found at: {ASSET_FILE_PATH}")

    with open(ASSET_FILE_PATH, 'rb') as f:
        payload = {"name": asset_name, "project": project_id}
        files = {"blend_file": (ASSET_FILE_PATH.name, f, "application/octet-stream")}
        response = session.post(f"{API_BASE_URL}/assets/", data=payload, files=files)
        response.raise_for_status()
        asset_data = response.json()
        print(f"   Asset uploaded successfully with ID: {asset_data['id']}\n")
        return asset_data['id']


def submit_tiled_animation(session, project_id, asset_id):
    """Submits the tiled animation job."""
    anim_name = f"Manual-Tiled-Animation-{int(time.time())}"
    print(f"3. Submitting Tiled Animation job: '{anim_name}'...")
    payload = {
        "name": anim_name,
        "project": project_id,
        "asset_id": asset_id,
        "output_file_pattern": "manual_test_####",  # <-- ADDED THIS REQUIRED FIELD
        "start_frame": 1,
        "end_frame": 2,  # A short 2-frame animation
        "tiling_config": "2x2",  # 2x2 grid = 4 tiles per frame
        "render_settings": {
            "cycles.samples": 32,
            "render.resolution_x": 400,
            "render.resolution_y": 400
        }
    }
    response = session.post(f"{API_BASE_URL}/animations/", json=payload)
    response.raise_for_status()
    anim_data = response.json()
    print(f"   Job submitted successfully. Animation ID: {anim_data['id']}\n")
    return anim_data['id']


def poll_for_completion(session, anim_id):
    """Polls the API until the animation is marked as DONE."""
    print("4. Polling for job completion (this may take a few minutes)...")
    anim_url = f"{API_BASE_URL}/animations/{anim_id}/"
    for i in range(300):  # Timeout after ~5 minutes
        response = session.get(anim_url)
        response.raise_for_status()
        data = response.json()
        status = data.get('status')
        progress = data.get('progress')
        print(f"   [{i*2}s] Status: {status}, Progress: {progress}")
        if status in ["DONE", "ERROR"]:
            print(f"\n   Polling complete. Final status: {status}\n")
            return data
        time.sleep(2)
    raise TimeoutError("Animation job did not complete within the time limit.")


def download_outputs(session, final_data):
    """Downloads the final assembled frames."""
    print("5. Downloading final assembled frames...")
    OUTPUT_DIR.mkdir(exist_ok=True)
    frames = final_data.get('frames', [])
    if not frames:
        print("   No frames found in the final job data.")
        return

    for frame in frames:
        if frame.get('status') == 'DONE' and frame.get('output_file'):
            url = frame['output_file']
            frame_num = frame['frame_number']
            filename = f"final_frame_{frame_num:04d}.png"
            local_path = OUTPUT_DIR / filename

            print(f"   Downloading frame {frame_num} from {url}...")
            response = session.get(url)
            response.raise_for_status()

            with open(local_path, 'wb') as f:
                f.write(response.content)

            # Basic verification with Pillow
            with Image.open(local_path) as img:
                print(f"   Saved to '{local_path}'. Dimensions: {img.size[0]}x{img.size[1]}")
        else:
            print(f"   Frame {frame.get('frame_number')} was not completed successfully.")
    print("\nScript finished.")


def main():
    """Run the manual test workflow."""
    session = requests.Session()
    try:
        project_id = create_project(session)
        asset_id = upload_asset(session, project_id)
        anim_id = submit_tiled_animation(session, project_id, asset_id)
        final_data = poll_for_completion(session, anim_id)
        if final_data.get('status') == 'DONE':
            download_outputs(session, final_data)
        else:
            print("Animation job failed. See server logs for details.")
    except Exception as e:
        print(f"\nAn error occurred: {e}")


if __name__ == "__main__":
    main()