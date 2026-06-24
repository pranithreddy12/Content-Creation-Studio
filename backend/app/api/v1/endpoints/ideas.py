from fastapi import APIRouter

router = APIRouter()


@router.get("/_ping")
async def _ping() -> dict:
    return {"module": "ideas", "ok": True}
