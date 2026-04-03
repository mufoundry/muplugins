import typing
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from muforge.utils.responses import streaming_list

from ..db import pcs as pcs_db
from ..db import users as users_db
from ..db.pcs import PCModel
from ..db.users import UserModel
from ..depends import get_current_user

router = APIRouter()


@router.get("/", response_model=typing.List[UserModel])
async def get_users(
    request: Request, user: Annotated[UserModel, Depends(get_current_user)]
):
    db = request.app.state.core.db
    if user.admin_level < 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions."
        )

    stream = db.stream(users_db.list_users)
    return streaming_list(stream)


@router.get("/{user_id}", response_model=UserModel)
async def get_user(
    request: Request,
    user_id: uuid.UUID,
    user: Annotated[UserModel, Depends(get_current_user)],
):
    db = request.app.state.core.db
    if user.admin_level < 1 and user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions."
        )

    async with db.connection() as conn:
        found = await users_db.get_user(conn, user_id)
    return found


@router.get("/{user_id}/pcs", response_model=typing.List[PCModel])
async def get_user_characters(request: Request,
    user_id: uuid.UUID, user: Annotated[UserModel, Depends(get_current_user)]
):
    if user.id != user_id and user.admin_level < 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions."
        )

    db = request.app.state.core.db
    async with db.connection() as conn:
        target_user = await users_db.get_user(conn, user_id)
    stream = db.stream(pcs_db.list_pcs_user, target_user)
    return streaming_list(stream)

