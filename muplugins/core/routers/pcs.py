import typing
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from muforge.utils.responses import streaming_list
from pydantic import BaseModel

from ..db import pcs as pcs_db
from ..db.pcs import ActiveAs, CharacterCreate, PCModel
from ..db.users import UserModel
from ..depends import get_acting_pc, get_current_user
from ..events.base import EventBase

router = APIRouter()


@router.get("/", response_model=typing.List[PCModel])
async def get_pcs(
    request: Request, user: Annotated[UserModel, Depends(get_current_user)]
):
    if not user.admin_level > 0:
        raise HTTPException(
            status_code=403, detail="You do not have permission to view all characters."
        )
    db = request.app.state.core.db

    stream = db.stream(pcs_db.list_pcs)

    return streaming_list(stream)


@router.get("/active", response_model=typing.List[PCModel])
async def get_active_pc(user: Annotated[UserModel, Depends(get_current_user)]):
    pass


@router.get("/{pc_id}", response_model=PCModel)
async def get_pc(
    request: Request,
    user: Annotated[UserModel, Depends(get_current_user)],
    pc_id: uuid.UUID,
):
    db = request.app.state.core.db
    async with db.connection() as conn:
        pc = await pcs_db.find_pc_id(conn, pc_id)
    if pc.user_id != user.id and user.admin_level < 1:
        raise HTTPException(
            status_code=403, detail="Player Character does not belong to you."
        )
    return pc


@router.get("/{pc_id}/active", response_model=ActiveAs)
async def get_pc_active_as(
    request: Request,
    user: Annotated[UserModel, Depends(get_current_user)],
    pc_id: uuid.UUID,
):
    acting = await get_acting_pc(request, user, pc_id)
    return acting


@router.get("/{character_id}/events")
async def stream_character_events(
    request: Request,
    user: Annotated[UserModel, Depends(get_current_user)],
    character_id: uuid.UUID,
):
    # We don't use it; but this verifies that user can control character.
    acting = await get_acting_pc(request, user, character_id)
    core = request.app.state.core

    should_start = False
    if not (session := core.active_sessions.get(character_id, None)):
        session_class = core.app.classes["session"]
        session = session_class(core, acting)
        core.active_sessions[character_id] = session
        should_start = True

    async def event_generator():
        id, queue = await session.subscribe()
        graceful = False
        try:
            if should_start:
                await session.start()
            # blocks until a new event
            while item := await queue.get():
                ev_class = item.__class__
                ev_name = core.events_reversed.get(ev_class)
                yield f"event: {ev_name}\ndata: {item.model_dump_json()}\n\n"
            graceful = True
        finally:
            await session.unsubscribe(id)
            if not session.subscriptions and session.active:
                await session.stop(graceful=graceful)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class CommandSubmission(BaseModel):
    command: str


@router.post("/{character_id}/command")
async def submit_command(
    request: Request,
    user: Annotated[UserModel, Depends(get_current_user)],
    character_id: uuid.UUID,
    command: Annotated[CommandSubmission, Body()],
):
    core = request.app.state.core

    if character_id not in user.characters:
        raise HTTPException(
            status_code=403, detail="You do not have permission to use this character."
        )

    if not (session := core.active_sessions.get(character_id, None)):
        raise HTTPException(status_code=404, detail="Character entity not found.")

    await session.execute_command(command.command)

    return {"status": "ok"}


@router.post("/", response_model=PCModel)
async def create_character(
    request: Request,
    user: Annotated[UserModel, Depends(get_current_user)],
    char_data: Annotated[CharacterCreate, Body()],
):
    app = request.app.state.app
    core = request.app.state.core
    db = core.db

    # This override is provided for plugins that want to handle character 
    # creation themselves, such as to add custom data or trigger 
    # custom events.
    if (hooks := core.app.hooks.get("pc.create.override", [])):
        result = None
        for hook in hooks:
            result = await hook(app, db, user, char_data, result)
    else:
        async with db.transaction() as conn:
            result = await pcs_db.create_pc(conn, user, char_data.name)
            for hook in core.app.hooks.get("pc.create", []):
                await hook(app, conn, result)
    return result
