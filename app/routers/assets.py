import hashlib
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_admin_user
from app.models import Asset, Questionnaire, Role, User
from app.schemas import AssetOut

router = APIRouter(prefix="/api/admin/assets", tags=["assets"])

_SAFE_INLINE_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
        "application/pdf",
    }
)


def _safe_media_type(mime_type: str | None, file_name: str) -> tuple[str, str]:
    """Return (media_type, content_disposition) that is safe for inline serving."""
    safe_name = file_name.replace('"', "_") if file_name else "download"
    if mime_type and mime_type.lower() in _SAFE_INLINE_TYPES:
        return mime_type, f'inline; filename="{safe_name}"'
    return "application/octet-stream", f'attachment; filename="{safe_name}"'


def _is_superadmin(user: User) -> bool:
    return user.role == Role.SUPERADMIN


def _get_accessible_questionnaire(
    db: Session,
    *,
    questionnaire_id: str,
    user: User,
) -> Questionnaire:
    questionnaire = db.get(Questionnaire, questionnaire_id)
    if not questionnaire:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Questionnaire not found")
    if not _is_superadmin(user) and questionnaire.owner_admin_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Questionnaire access denied")
    return questionnaire


def _to_asset_out(asset: Asset, *, owner_username: str) -> AssetOut:
    return AssetOut(
        id=asset.id,
        owner_username=owner_username,
        file_name=asset.file_name,
        original_path=asset.original_path,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        width=asset.width,
        height=asset.height,
        sha256_hex=asset.sha256_hex,
        created_at=asset.created_at,
    )


@router.post("/upload", response_model=list[AssetOut])
async def upload_assets(
    files: list[UploadFile] = File(...),
    questionnaire_id: str = Form(...),
    paths: list[str] | None = Form(default=None),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> list[AssetOut]:
    questionnaire = _get_accessible_questionnaire(
        db,
        questionnaire_id=questionnaire_id.strip(),
        user=current_user,
    )

    existing_count = db.scalar(
        select(func.count(Asset.id)).where(Asset.questionnaire_id == questionnaire.id)
    ) or 0
    if existing_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This questionnaire already has assets. Delete all assets first to re-upload.",
        )

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files uploaded")

    if paths is not None and len(paths) != len(files):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="If provided, paths must match files length",
        )

    created_assets: list[Asset] = []
    for index, uploaded in enumerate(files):
        payload = await uploaded.read()
        if not payload:
            continue

        uploaded_name = (uploaded.filename or "").strip().replace("\\", "/")
        declared_path = paths[index].strip() if paths else ""
        normalized_path = declared_path.replace("\\", "/") if declared_path else None
        if not normalized_path and "/" in uploaded_name:
            normalized_path = uploaded_name

        file_name = (
            PurePosixPath(normalized_path).name
            if normalized_path
            else PurePosixPath(uploaded_name).name
        )
        if not file_name:
            file_name = f"asset-{index + 1}"

        asset = Asset(
            owner_user_id=current_user.id,
            questionnaire_id=questionnaire.id,
            file_name=file_name,
            original_path=normalized_path,
            mime_type=uploaded.content_type or "application/octet-stream",
            size_bytes=len(payload),
            width=None,
            height=None,
            sha256_hex=hashlib.sha256(payload).hexdigest(),
            blob_data=payload,
        )
        db.add(asset)
        created_assets.append(asset)

    if not created_assets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No non-empty files were uploaded",
        )

    db.commit()
    for asset in created_assets:
        db.refresh(asset)
    return [_to_asset_out(asset, owner_username=current_user.username) for asset in created_assets]


@router.get("", response_model=list[AssetOut])
def list_assets(
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
    questionnaire_id: str | None = Query(default=None),
    limit: int = 200,
) -> list[AssetOut]:
    bounded_limit = max(1, min(limit, 1000))
    stmt = (
        select(Asset, User.username)
        .join(User, User.id == Asset.owner_user_id)
        .order_by(desc(Asset.created_at))
        .limit(bounded_limit)
    )
    if questionnaire_id:
        questionnaire = _get_accessible_questionnaire(
            db,
            questionnaire_id=questionnaire_id.strip(),
            user=current_user,
        )
        stmt = stmt.where(Asset.questionnaire_id == questionnaire.id)
    if not _is_superadmin(current_user):
        stmt = stmt.where(Asset.owner_user_id == current_user.id)
    rows = db.execute(stmt).all()
    return [_to_asset_out(asset, owner_username=owner_username) for asset, owner_username in rows]


@router.delete("/questionnaire/{questionnaire_id}")
def delete_assets_for_questionnaire(
    questionnaire_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    questionnaire = _get_accessible_questionnaire(
        db,
        questionnaire_id=questionnaire_id.strip(),
        user=current_user,
    )
    db.execute(delete(Asset).where(Asset.questionnaire_id == questionnaire.id))
    db.commit()
    return {"ok": True}


@router.get("/{asset_id}", response_model=AssetOut)
def get_asset_metadata(
    asset_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> AssetOut:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if not _is_superadmin(current_user) and asset.owner_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Asset access denied")
    owner_username = db.scalar(select(User.username).where(User.id == asset.owner_user_id))
    return _to_asset_out(asset, owner_username=owner_username or "")


@router.get("/{asset_id}/content")
def get_asset_content(
    asset_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> Response:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if not _is_superadmin(current_user) and asset.owner_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Asset access denied")

    media_type, disposition = _safe_media_type(asset.mime_type, asset.file_name)
    return Response(
        content=asset.blob_data,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )
