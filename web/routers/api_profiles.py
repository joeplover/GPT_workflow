"""User-only named URL/Key settings. Keys are write-only through the API."""

import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import database
from ..models import User, UserApiProfile
from ..security import require_actor
from ..services.api_profiles import encrypted_profile_key, owned_profile

router = APIRouter(prefix="/api/settings/api-profiles", tags=["private settings"])


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(default="", max_length=16384)


class SelectionInput(BaseModel):
    profile_id: str = Field(default="", max_length=100)


def private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


def public_profile(profile):
    return {
        "id": profile.id,
        "name": profile.name,
        "base_url": profile.base_url,
        "has_api_key": bool(profile.api_key),
        "selected": profile.selected,
    }


def user_profiles(session):
    return list(
        session.scalars(
            select(UserApiProfile)
            .where(UserApiProfile.user_id == require_actor().id)
            .order_by(UserApiProfile.created_at, UserApiProfile.id)
        )
    )


def lock_user(session):
    # Serialize changes so two concurrent requests cannot select two profiles.
    session.execute(
        select(User.id).where(User.id == require_actor().id).with_for_update()
    )


def validated_values(body):
    name, url, key = (
        body.name.strip(),
        body.base_url.strip().rstrip("/"),
        body.api_key.strip(),
    )
    try:
        parts = urlsplit(url)
        valid = parts.scheme in {"http", "https"} and parts.hostname and parts.port != 0
    except ValueError:
        valid = False
    if not name:
        raise HTTPException(400, "请填写配置名称。")
    if (
        not valid
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or any(c.isspace() for c in url)
    ):
        raise HTTPException(
            400, "请填写有效的 HTTP/HTTPS 接口地址，不含账号、密码、查询参数或片段。"
        )
    if any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise HTTPException(400, "API Key 不能包含换行或控制字符。")
    return name, url, key


@router.get("")
def list_profiles(response: Response):
    private_response(response)
    with Session(database.get_engine()) as session:
        profiles = user_profiles(session)
        return {
            "profiles": [public_profile(p) for p in profiles],
            "selected_id": next((p.id for p in profiles if p.selected), ""),
        }


@router.post("", status_code=201)
def create_profile(body: ProfileInput, response: Response):
    private_response(response)
    name, url, key = validated_values(body)
    if not key:
        raise HTTPException(400, "新增配置时请填写 API Key。")
    with Session(database.get_engine()) as session, session.begin():
        lock_user(session)
        profiles = user_profiles(session)
        if any(p.name.casefold() == name.casefold() for p in profiles):
            raise HTTPException(409, "已有同名配置，请换一个名称。")
        profile = UserApiProfile(
            id=uuid.uuid4().hex,
            user_id=require_actor().id,
            name=name,
            base_url=url,
            api_key="",
            selected=not profiles,
        )
        profile.api_key = encrypted_profile_key(profile, key)
        session.add(profile)
        session.flush()
        return public_profile(profile)


@router.post("/selection")
def select_profile(body: SelectionInput, response: Response):
    private_response(response)
    with Session(database.get_engine()) as session, session.begin():
        lock_user(session)
        if body.profile_id:
            owned_profile(session, body.profile_id)
        for profile in user_profiles(session):
            profile.selected = profile.id == body.profile_id
        return {"selected_id": body.profile_id}


@router.post("/{profile_id}")
def update_profile(profile_id: str, body: ProfileInput, response: Response):
    private_response(response)
    name, url, key = validated_values(body)
    with Session(database.get_engine()) as session, session.begin():
        lock_user(session)
        profile = owned_profile(session, profile_id)
        if any(
            p.id != profile.id and p.name.casefold() == name.casefold()
            for p in user_profiles(session)
        ):
            raise HTTPException(409, "已有同名配置，请换一个名称。")
        # Never silently send an existing secret to a newly edited endpoint.
        if url != profile.base_url and not key:
            raise HTTPException(
                400, "修改接口地址时请同时填写 API Key，确认这一组配置。"
            )
        profile.name, profile.base_url = name, url
        if key:
            profile.api_key = encrypted_profile_key(profile, key)
        return public_profile(profile)


@router.delete("/{profile_id}")
def delete_profile(profile_id: str, response: Response):
    private_response(response)
    with Session(database.get_engine()) as session, session.begin():
        lock_user(session)
        session.delete(owned_profile(session, profile_id))
        return {"deleted": True}
