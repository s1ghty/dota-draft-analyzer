#!/bin/sh
# Serves this folder locally so app/index.html can fetch() data/*.json --
# opening index.html directly via file:// is blocked by Chrome's CORS policy
# for local file fetches. No install needed, just Python's stdlib server.
cd "$(dirname "$0")"
echo "Open http://localhost:8000/app/index.html"
python3 -m http.server 8000
