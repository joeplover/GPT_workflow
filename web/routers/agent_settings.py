"""Private Xiao Meng Agent configuration API."""

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import database
from ..models import User
from ..security import require_actor
from ..services.agent_settings import (
    owned_agent_config,
    public_agent_config,
    save_agent_settings,
)

router = APIRouter(prefix="/api/settings/agent", tags=["private agent settings"])


class AgentConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(default="", max_length=16384)
    model: str = Field(min_length=1, max_length=200)


def private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


@router.get("")
def read_agent_config(response: Response):
    private_response(response)
    with Session(database.get_engine()) as session:
        return public_agent_config(owned_agent_config(session))


@router.put("")
def update_agent_config(body: AgentConfigInput, response: Response):
    private_response(response)
    return save_agent_settings(body.base_url, body.model, body.api_key)


@router.delete("")
def delete_agent_config(response: Response):
    private_response(response)
    with Session(database.get_engine()) as session, session.begin():
        session.execute(
            select(User.id).where(User.id == require_actor().id).with_for_update()
        )
        config = owned_agent_config(session)
        if config is not None:
            session.delete(config)
    return {"deleted": True}
