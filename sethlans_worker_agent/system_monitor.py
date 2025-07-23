# sethlans_worker_agent/system_monitor.py

# ... (Your existing header) ...

import platform
import socket
import datetime
import requests
import json

from . import config
from .tool_manager import tool_manager_instance

import logging  # <-- NEW IMPORT

logger = logging.getLogger(__name__)  # <-- Get a logger for this module

# Global variable to store worker's own info once registered
WORKER_INFO = {}


def get_system_info():
    """
    Gathers basic system information and available managed tools for the heartbeat.
    """
    hostname = socket.gethostname()
    ip_address = None
    try:
        ip_address = socket.gethostbyname(hostname)
    except socket.gaierror:
        pass

    os_info = platform.system()
    if os_info == 'Windows':
        os_info += f" {platform.release()}"
    elif os_info == 'Linux':
        os_info += f" {platform.version()}"

    available_tools = tool_manager_instance.scan_for_blender_versions()

    return {
        "hostname": hostname,
        "ip_address": ip_address,
        "os": os_info,
        "available_tools": available_tools
    }


def send_heartbeat(system_info):
    """
    Sends a heartbeat to the Django Manager's heartbeat API endpoint.
    Updates the global WORKER_INFO with the worker's ID received from the manager.
    """
    global WORKER_INFO
    heartbeat_url = f"{config.MANAGER_API_URL}heartbeat/"
    try:
        logger.info(f"Sending heartbeat to {heartbeat_url}...")  # <-- Changed print to logger.info
        logger.debug(f"Heartbeat payload: {json.dumps(system_info, indent=2)}")  # <-- Changed print to logger.debug

        response = requests.post(heartbeat_url, json=system_info, timeout=5)
        response.raise_for_status()

        response_data = response.json()
        logger.info(f"Heartbeat successful: HTTP {response.status_code}")  # <-- Changed print to logger.info
        WORKER_INFO = response_data
        logger.info(
            f"Worker registered as ID: {WORKER_INFO.get('id')}, Hostname: {WORKER_INFO.get('hostname')}")  # <-- Changed print to logger.info

    except requests.exceptions.Timeout:
        logger.error("Heartbeat timed out after 5 seconds.")  # <-- Changed print to logger.error
    except requests.exceptions.RequestException as e:
        logger.error(f"Heartbeat failed - {e}")  # <-- Changed print to logger.error
    except json.JSONDecodeError:
        logger.error("Failed to decode JSON response from heartbeat.")  # <-- Changed print to logger.error
    except Exception as e:
        logger.error(f"An unexpected error occurred during heartbeat: {e}")  # <-- Changed print to logger.error