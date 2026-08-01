"""FastAPI app: authenticated multi-user browsing and file-restore from
Rescuezilla images, with per-user image sharing and BitLocker unlock."""
from __future__ import annotations

import os
import urllib.parse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import config, filebrowse
from .auth import current_user, require_admin, router as auth_router
from .metadata import list_images
from .mounter import mounter
from .store import store

_HERE = os.path.dirname(os.path.abspath(__file__))
_STATIC = os.path.join(os.path.dirname(_HERE), "static")

app = FastAPI(title="Rescuezilla Web GUI")
app.add_middleware(SessionMiddleware, secret_key=store.secret,
                   https_only=False, same_site="lax")
app.include_router(auth_router)


# ---- request models -------------------------------------------------------

class MountRequest(BaseModel):
    image_path: str
    part: str


class UnmountRequest(BaseModel):
    id: str


class UserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class PasswordRequest(BaseModel):
    username: str
    password: str


class ShareRequest(BaseModel):
    image_name: str
    username: str


class BitlockerRequest(BaseModel):
    image_name: str
    part: str
    value: str
    key_type: str = "recovery"


# ---- pages ----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    with open(os.path.join(_STATIC, "index.html")) as fh:
        return HTMLResponse(fh.read())


# ---- images / mounts ------------------------------------------------------

def _validated_image_name(image_path: str) -> str:
    """Ensure image_path is a real image dir under IMAGES_DIR; return its name."""
    base = os.path.realpath(config.IMAGES_DIR)
    real = os.path.realpath(image_path)
    if real != base and not real.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="image outside images dir")
    return os.path.basename(real.rstrip(os.sep))


@app.get("/api/images")
def api_images(user: str = Depends(current_user)) -> JSONResponse:
    images = list_images(config.IMAGES_DIR)
    admin = store.is_admin(user)
    out = []
    for img in images:
        if not store.can_access(user, img.name):
            continue
        d = img.to_dict()
        # Annotate BitLocker key availability + (admin) sharing info.
        for p in d["partitions"]:
            p["has_bitlocker_key"] = store.has_bitlocker_key(img.name, p["name"])
        if admin:
            d["shared_with"] = store.shared_with(img.name)
        out.append(d)
    return JSONResponse({"images_dir": config.IMAGES_DIR, "images": out,
                         "is_admin": admin})


@app.post("/api/mount")
def api_mount(req: MountRequest, user: str = Depends(current_user)) -> JSONResponse:
    name = _validated_image_name(req.image_path)
    if not store.can_access(user, name):
        raise HTTPException(status_code=403, detail="not permitted for this image")
    try:
        m = mounter.start(req.image_path, req.part)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse(_mount_dict(m))


@app.get("/api/mounts")
def api_mounts(user: str = Depends(current_user)) -> JSONResponse:
    mounts = [m for m in mounter.list() if store.can_access(user, m.image_name)]
    return JSONResponse({"mounts": [_mount_dict(m) for m in mounts]})


@app.post("/api/unmount")
def api_unmount(req: UnmountRequest, user: str = Depends(current_user)) -> JSONResponse:
    m = mounter.get(req.id)
    if not m or not store.can_access(user, m.image_name):
        raise HTTPException(status_code=404, detail="unknown mount id")
    mounter.unmount(req.id)
    return JSONResponse({"ok": True})


# ---- file browsing --------------------------------------------------------

@app.get("/api/browse")
def api_browse(id: str, path: str = "", user: str = Depends(current_user)) -> JSONResponse:
    root = _require_mounted(id, user)
    try:
        entries = filebrowse.list_dir(root, path)
    except (ValueError, NotADirectoryError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse({"path": path, "entries": entries})


@app.get("/api/download")
def api_download(id: str, path: str, user: str = Depends(current_user)) -> FileResponse:
    root = _require_mounted(id, user)
    try:
        full = filebrowse.resolve_file(root, path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(full, filename=os.path.basename(full),
                        media_type="application/octet-stream")


@app.get("/api/download-folder")
def api_download_folder(id: str, path: str = "",
                        user: str = Depends(current_user)) -> StreamingResponse:
    root = _require_mounted(id, user)
    try:
        stream = filebrowse.zip_folder(root, path)
    except (ValueError, NotADirectoryError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    name = (os.path.basename(path.rstrip("/")) or "root") + ".zip"
    disp = "attachment; filename*=UTF-8''" + urllib.parse.quote(name)
    return StreamingResponse(stream, media_type="application/zip",
                             headers={"Content-Disposition": disp})


# ---- admin: users ---------------------------------------------------------

@app.get("/api/admin/users")
def admin_list_users(_: str = Depends(require_admin)) -> JSONResponse:
    return JSONResponse({"users": store.list_users()})


@app.post("/api/admin/users")
def admin_add_user(req: UserRequest, _: str = Depends(require_admin)) -> JSONResponse:
    if req.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="role must be admin or user")
    store.add_user(req.username, req.password, req.role)
    return JSONResponse({"ok": True})


@app.post("/api/admin/users/password")
def admin_set_password(req: PasswordRequest,
                       _: str = Depends(require_admin)) -> JSONResponse:
    try:
        store.set_password(req.username, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return JSONResponse({"ok": True})


@app.post("/api/admin/users/delete")
def admin_delete_user(req: ShareRequest,
                      admin: str = Depends(require_admin)) -> JSONResponse:
    if req.username == admin:
        raise HTTPException(status_code=400, detail="cannot delete yourself")
    store.delete_user(req.username)
    return JSONResponse({"ok": True})


# ---- admin: sharing -------------------------------------------------------

@app.post("/api/admin/share")
def admin_share(req: ShareRequest, _: str = Depends(require_admin)) -> JSONResponse:
    if store.role(req.username) is None:
        raise HTTPException(status_code=404, detail="no such user")
    store.share(req.image_name, req.username)
    return JSONResponse({"shared_with": store.shared_with(req.image_name)})


@app.post("/api/admin/unshare")
def admin_unshare(req: ShareRequest, _: str = Depends(require_admin)) -> JSONResponse:
    store.unshare(req.image_name, req.username)
    return JSONResponse({"shared_with": store.shared_with(req.image_name)})


# ---- admin: BitLocker keys ------------------------------------------------

@app.post("/api/admin/bitlocker")
def admin_set_bitlocker(req: BitlockerRequest,
                        _: str = Depends(require_admin)) -> JSONResponse:
    if req.key_type not in ("recovery", "password", "bek"):
        raise HTTPException(status_code=400, detail="invalid key_type")
    store.set_bitlocker_key(req.image_name, req.part, req.value, req.key_type)
    return JSONResponse({"ok": True})


@app.post("/api/admin/bitlocker/delete")
def admin_delete_bitlocker(req: BitlockerRequest,
                           _: str = Depends(require_admin)) -> JSONResponse:
    store.delete_bitlocker_key(req.image_name, req.part)
    return JSONResponse({"ok": True})


@app.on_event("shutdown")
def _cleanup() -> None:
    mounter.shutdown()


# ---- helpers --------------------------------------------------------------

def _mount_dict(m) -> dict:
    return {
        "id": m.id,
        "image_path": m.image_path,
        "image_name": m.image_name,
        "part": m.part,
        "fstype": m.fstype,
        "state": m.state,
        "message": m.message,
        "mountpoint": m.mountpoint,
    }


def _require_mounted(mount_id: str, user: str) -> str:
    m = mounter.get(mount_id)
    if not m or not store.can_access(user, m.image_name):
        raise HTTPException(status_code=404, detail="unknown mount id")
    if m.state != "mounted" or not m.mountpoint:
        raise HTTPException(status_code=409, detail=f"mount not ready ({m.state})")
    return m.mountpoint


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
