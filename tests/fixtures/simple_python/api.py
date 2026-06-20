"""Web-framework handlers — fixtures for the FastAPI/Flask entry-point heuristics
in orgraph/extract/treesitter.py (Change 3 in 0.1.33)."""
# NOTE: These imports may not be installed in CI; the fixture is parsed as text,
# not executed. scip-python will tag the references as unresolved which is fine —
# the decorator regex doesn't need the imports to resolve.
from fastapi import FastAPI, APIRouter   # noqa: F401  (parsed, not run)
from flask import Flask                   # noqa: F401

app = FastAPI()
router = APIRouter()
fapp = Flask(__name__)


@app.get("/items/{item_id}")
async def get_item(item_id: int) -> dict:
    return {"id": item_id}


@app.post("/items")
def create_item() -> dict:
    return {"created": True}


@router.delete("/items/{item_id}")
def delete_item(item_id: int) -> dict:
    return {"deleted": item_id}


# Commented decorators must not poison the registry.
# @app.put("/should-not-be-detected") def commented_handler(): ...
def commented_handler() -> None:
    """Should NOT be tagged with HTTP metadata."""
    return None


@fapp.route("/health")
def healthz() -> str:
    return "ok"


@fapp.route("/users", methods=["POST", "GET"])
def user_route() -> str:
    return "user"
