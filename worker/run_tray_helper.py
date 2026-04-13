# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Tray helper entry point.

The tray helper is a separate process from the main worker agent,
communicating via HTTPS to 127.0.0.1:8081. The tray helper module
itself is specified in worker-host-integration.md Q9. This file is
just the launcher stub.
"""

import signal
import sys


def _handle_shutdown(signum, frame):
    """Handle graceful shutdown on SIGTERM/SIGINT."""
    print("Tray helper shutting down...", file=sys.stderr)
    sys.exit(0)


def main():
    """Launch the tray helper main loop."""
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    # TODO: Import and launch the tray helper main loop once
    # the tray helper module (worker-host-integration.md Q9) is
    # implemented. For now, this is a placeholder.
    print(
        "Sethlans tray helper stub. "
        "The tray helper module is not yet implemented.",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
