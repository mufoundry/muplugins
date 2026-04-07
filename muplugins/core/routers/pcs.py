import typing
import uuid
import asyncio
from typing import Annotated

import jwt
import orjson
from fastapi import APIRouter, Body, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
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
        id, queue = await session.subscribe(request)
        graceful = False
        try:
            if should_start:
                await session.start()
            # blocks until a new event
            while item := await queue.get():
                yield f"event: {item.event_type()}\ndata: {item.model_dump_json()}\n\n"
            graceful = True
        finally:
            await session.unsubscribe(id)
            if not session.subscriptions and session.active:
                await session.stop(graceful=graceful)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

class CommandSubmission(BaseModel):
    command: str

@router.websocket("/{character_id}/session")
async def websocket_session(
    websocket: WebSocket,
    character_id: uuid.UUID,
    token: str | None = None,
):
    # Manual token validation since OAuth2PasswordBearer doesn't work with WebSockets
    if not token:
        token = websocket.query_params.get("token")
        if not token:
            await websocket.close(code=4001, reason="Missing token")
            return
    
    request = websocket

    core = request.app.state.core
    jwt_manager = core.jwt_manager
    jwt_settings = jwt_manager.jwt_settings
    try:
        payload = jwt.decode(token, jwt_settings["secret"], algorithms=[jwt_settings["algorithm"]])
        user_id = payload.get("sub", None)
        if user_id is None:
            await websocket.close(code=4001, reason="Invalid token")
            return
    except jwt.PyJWTError as e:
        await websocket.close(code=4001, reason="Invalid token")
        return

    db = core.db
    async with db.connection() as conn:
        user_row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    if user_row is None:
        await websocket.close(code=4001, reason="Invalid token")
        return

    user = UserModel(**user_row)
    acting = await get_acting_pc(request, user, character_id)

    await websocket.accept()

    should_start = False
    if not (session := core.active_sessions.get(character_id, None)):
        session_class = core.app.classes["session"]
        session = session_class(core, acting)
        core.active_sessions[character_id] = session
        should_start = True
    
    shutdown_event = asyncio.Event()
    shutdown_reason = None

    id, queue = await session.subscribe(request)
    graceful = False

    async def run_receive():
        """
        Receive events from client, submit as commands to session.
        """
        try:
            while True:
                received = await websocket.receive_bytes()
                if not received:
                    continue
                data = orjson.loads(received)
                command = data.get("command", None)
                if command is not None:
                    res = await session.execute_command(command)
                    if isinstance(res, dict):
                        await websocket.send_json({"response": res})
        except WebSocketDisconnect:
            shutdown_reason = "client_disconnect"
            shutdown_event.set()
    
    class EventSend(BaseModel):
        event: str
        data: dict

    async def run_send():
        """
        Read queue, send events to client.
        """
        try:
            while item := await queue.get():
                ev_type = item.event_type()
                to_dict = item.model_dump()
                send = EventSend(event=ev_type, data=to_dict)
                dumped = send.model_dump_json()
                await websocket.send_bytes(dumped.encode())
        except asyncio.CancelledError:
            pass
    

    if should_start:
        await session.start()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(run_receive())
        tg.create_task(run_send())

        await shutdown_event.wait()

    await session.unsubscribe(id)
    if not session.subscriptions and session.active:
        await session.stop(graceful=graceful)


@router.post("/{character_id}/command")
async def submit_command(
    request: Request,
    user: Annotated[UserModel, Depends(get_current_user)],
    character_id: uuid.UUID,
    command: Annotated[CommandSubmission, Body()],
):
    core = request.app.state.core
    acting = await get_acting_pc(request, user, character_id)

    if not (session := core.active_sessions.get(character_id, None)):
        raise HTTPException(status_code=404, detail="Character entity not found.")

    res = await session.execute_command(command.command)

    if isinstance(res, dict):
        return res
    
    return {"status": "ok"}


@router.post("/", response_model=PCModel)
async def create_character(
    request: Request,
    user: Annotated[UserModel, Depends(get_current_user)],
    char_data: Annotated[CharacterCreate, Body()],
):
    app = request.app.state.game
    core = request.app.state.core
    db = core.db

    async with db.transaction() as conn:
        return await pcs_db.create_pc(core, conn, user, char_data)
