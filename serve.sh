#!/usr/bin/env bash
# Serve the reader app from the project root
cd "$(dirname "$0")"
echo "Serving on http://localhost:8000"
python -m http.server 8000