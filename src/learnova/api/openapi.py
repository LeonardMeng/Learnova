"""Print the API's OpenAPI description, so the frontend can generate its types without a running
server: `uv run python -m learnova.api.openapi > frontend/openapi.json`."""

import json

from learnova.api.app import create_app

if __name__ == "__main__":
    print(json.dumps(create_app(lambda: None).openapi(), indent=2))
