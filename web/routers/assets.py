from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services.assets import purge_image

router = APIRouter(prefix="/api/projects", tags=["image management"])


class PurgeImageInput(BaseModel):
    image_url: str = Field(min_length=1, max_length=1024)


@router.delete("/{project_id}/assets")
def delete_image(project_id: str, body: PurgeImageInput):
    return purge_image(project_id, body.image_url)
