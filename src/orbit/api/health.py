"""Health endpoints.

`/healthz` reports process liveness only. `/readyz` (DB reachability) is added
with the database ticket; see CLAUDE.md §11.4.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe: returns 200 as long as the process is serving."""
    return {"status": "ok"}
