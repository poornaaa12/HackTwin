#!/usr/bin/env python3
"""HackTwin entry point: starts the application server and web UI.

Run: python3 app.py --port 8080   then open http://127.0.0.1:8080

The original Phase 7 dashboard (official .tst runs only) is still available
with: python3 dashboard_server.py --port 8081
"""

from api_server import main


if __name__ == "__main__":
    main()
