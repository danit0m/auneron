import os
import time
from pathlib import Path


def identity(payload):
    return {"result": payload["value"]}


def double(payload):
    return {"result": payload["value"] * 2}


def increment(payload):
    return {"result": payload["value"] + 1}


def fail(_payload):
    raise ValueError("private worker detail")


def delayed_write(payload):
    time.sleep(payload["delay_seconds"])
    Path(payload["path"]).write_text(
        "worker-survived",
        encoding="utf-8",
    )
    return {"result": 1}


def environment_probe(_payload):
    return {
        "api_key_present": bool(os.environ.get("API_KEY")),
        "database_url_present": bool(os.environ.get("DATABASE_URL")),
        "test_api_key_present": bool(os.environ.get("TEST_API_KEY")),
        "test_database_url_present": bool(
            os.environ.get("TEST_DATABASE_URL")
        ),
    }


def oversized(payload):
    return {"result": "x" * payload["size"]}
