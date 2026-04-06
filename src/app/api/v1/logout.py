from fastapi import APIRouter, Response

router = APIRouter(tags=["web-auth"])


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie("refresh_token")
    return {"message": "Logged out successfully."}
