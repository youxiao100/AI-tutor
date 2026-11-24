"""
One service, two surfaces:
1. Question bank — paper JSON becomes a reviewable training blueprint.
2. Answering — the student frontend reads that blueprint and records confirmed output.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from backend.question_bank.models import AdaptedDocument, TrainingBlueprint, StageBlueprint
from backend.question_bank.adapter import adapt_lis_mas_json
from backend.question_bank.golden_blueprint import build_golden_lib3_blueprint
from backend.question_bank.live_blueprint import build_live_blueprint
from backend.question_bank.question_builder import (
    generate_training_blueprint_via_deepseek,
    regenerate_single_stage_via_deepseek,
    build_question_generator_prompts,
    audit_and_scrub_spoilers,
)
from backend.answering import (
    acknowledge_compare,
    choose_option,
    confirm_output,
    inactive_view,
    public_view,
    request_inspiration,
    start_attempt,
    submit_text,
)
from backend.auth import (
    COOKIE_NAME,
    MAX_UPLOAD_BYTES,
    acquire_llm_slot,
    admin_username,
    assert_same_origin,
    client_key,
    create_session,
    credentials_match,
    drop_session,
    hit_limit,
    login_configured,
    release_llm_slot,
    session_state,
)

app = FastAPI(
    title="引导式科研思维训练系统 — 后端题库与前端答题",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_SAMPLE_JSON = BASE_DIR / "data" / "lib3_sample.json"
FALLBACK_JSON_PATH = Path("/home/antigravity/面向知识熵减的智慧图书馆：Lib3范式研究_黄晨_(1).json")
DEFAULT_JSON_PATH = REPO_SAMPLE_JSON if REPO_SAMPLE_JSON.exists() else FALLBACK_JSON_PATH
CACHED_BP_PATH = BASE_DIR / "backend" / "question_bank" / "cached_lib3_blueprint.json"
QUESTION_BANK_PAGE = BASE_DIR / "frontend" / "question_bank" / "index.html"
ANSWERING_PAGE = BASE_DIR / "frontend" / "answering" / "answer.html"
LOGIN_PAGE = BASE_DIR / "frontend" / "login.html"

# Read-only seed copied into each login session. Request handlers never mutate it.
SEED: Dict[str, Any] = {
    "documents": {},
    "blueprints": {},
    "active_doc_id": "doc_0001",
}


def init_default_document():
    if not DEFAULT_JSON_PATH.exists() or SEED["documents"]:
        return
    with open(DEFAULT_JSON_PATH, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
    doc = adapt_lis_mas_json(raw_data, doc_id="doc_0001")
    SEED["documents"]["doc_0001"] = doc

    loaded = False
    if CACHED_BP_PATH.exists():
        try:
            with open(CACHED_BP_PATH, "r", encoding="utf-8") as bf:
                bp_data = json.load(bf)
            SEED["blueprints"]["doc_0001"] = TrainingBlueprint(**bp_data)
            loaded = True
        except Exception as e:
            print("Warning: Could not load cached blueprint:", e)

    if not loaded:
        SEED["blueprints"]["doc_0001"] = build_golden_lib3_blueprint(doc)


def clone_seed() -> Dict[str, Any]:
    init_default_document()
    return {
        "documents": {key: value.model_copy(deep=True) for key, value in SEED["documents"].items()},
        "blueprints": {key: value.model_copy(deep=True) for key, value in SEED["blueprints"].items()},
        "active_doc_id": SEED["active_doc_id"],
        "upload_count": 0,
    }


@app.on_event("startup")
def on_startup():
    init_default_document()
    if not login_configured():
        print("WORKSHOP_PASSWORD is not set; workshop login is closed.")


class LoginRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class AnswerStartRequest(BaseModel):
    document_id: str = ""
    micro_session: str = "full_8_stages"


class AnswerTextRequest(BaseModel):
    text: str = Field(default="", max_length=2000)


class AnswerChooseRequest(BaseModel):
    key: str = Field(max_length=4)
    custom_text: str = Field(default="", max_length=500)


class AnswerConfirmRequest(BaseModel):
    confirmed_output: str = Field(max_length=2000)


class GenerateBlueprintRequest(BaseModel):
    document_id: str = "doc_0001"
    custom_blacklist: Optional[List[str]] = None


class RegenerateStageRequest(BaseModel):
    document_id: str = "doc_0001"
    stage_number: int = Field(ge=1, le=8)
    teacher_instruction: str = Field(default="", max_length=1500)
    previous_stage_student_output: str = Field(default="", max_length=2000)


class UpdateStageRequest(BaseModel):
    document_id: str = "doc_0001"
    stage: StageBlueprint


class UpdateBlacklistRequest(BaseModel):
    document_id: str = "doc_0001"
    spoiler_blacklist: List[str] = Field(max_length=64)


def require_session(request: Request) -> dict:
    assert_same_origin(request)
    return session_state(request)


def owned_document(store: dict, document_id: str) -> AdaptedDocument:
    doc = store["documents"].get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="未找到这篇论文，或它不属于当前登录会话")
    return doc


def server_api_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def cookie_secure(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    scheme = forwarded.split(",")[0].strip() if forwarded else request.url.scheme
    return scheme == "https"


def _clean_blacklist(terms: List[str]) -> List[str]:
    cleaned = []
    for term in terms:
        text = str(term).strip()
        if not text or len(text) > 48:
            raise HTTPException(status_code=400, detail="剧透词需为 1–48 个字符")
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


@app.post("/api/login")
def api_login(req: LoginRequest, request: Request):
    assert_same_origin(request)
    hit_limit(f"login:{client_key(request)}", limit=8, window_seconds=600)
    if not login_configured():
        raise HTTPException(status_code=503, detail="登录尚未配置")
    if not credentials_match(req.username.strip(), req.password):
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    token = create_session(clone_seed())
    response = JSONResponse({"ok": True, "username": admin_username()})
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request),
        max_age=12 * 60 * 60,
        path="/",
    )
    return response


@app.post("/api/logout")
def api_logout(request: Request):
    assert_same_origin(request)
    drop_session(request.cookies.get(COOKIE_NAME))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.post("/api/reset-golden-blueprint")
def api_reset_golden_blueprint(req: GenerateBlueprintRequest, store: dict = Depends(require_session)):
    doc = owned_document(store, req.document_id)
    store["blueprints"][req.document_id] = build_golden_lib3_blueprint(doc)
    return {"blueprint": store["blueprints"][req.document_id].model_dump()}


@app.get("/api/state")
def get_current_state(document_id: Optional[str] = None, store: dict = Depends(require_session)):
    doc_id = document_id or store["active_doc_id"]
    doc = owned_document(store, doc_id)
    bp: Optional[TrainingBlueprint] = store["blueprints"].get(doc_id)
    sys_prompt, usr_prompt = build_question_generator_prompts(doc)
    return {
        "active_doc_id": doc_id,
        "document": doc.model_dump(),
        "blueprint": bp.model_dump() if bp else None,
        "prompts_preview": {
            "system_prompt": sys_prompt,
            "user_prompt": usr_prompt,
        },
        "available_documents": [
            {"document_id": key, "paper_id": value.paper_id, "paradigm_display": value.paradigm_display}
            for key, value in store["documents"].items()
        ],
    }


@app.post("/api/upload-json")
async def upload_paper_json(file: UploadFile = File(...), store: dict = Depends(require_session)):
    hit_limit(f"upload:{id(store)}", limit=10, window_seconds=600)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="JSON 文件不能超过 1.5 MB")
    try:
        raw_data = json.loads(content.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="JSON 解析失败")

    if not isinstance(raw_data, dict) or ("point_annotations" not in raw_data and "v2" not in raw_data):
        raise HTTPException(status_code=400, detail="上传的 JSON 不符合 lis-mas 标注格式（缺少 point_annotations 或 v2 字段）")

    store["upload_count"] = int(store.get("upload_count", 0)) + 1
    doc_id = f"doc_u{store['upload_count']:04d}"
    doc = adapt_lis_mas_json(raw_data, doc_id=doc_id)
    store["documents"][doc_id] = doc
    store["active_doc_id"] = doc_id
    try:
        blueprint = build_live_blueprint(doc)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"这份 JSON 没法整理成试题：{exc}") from exc
    store["blueprints"][doc_id] = blueprint
    store.pop("answer", None)
    return {
        "message": f"已按《{doc.paper_id}》实时整理 {len(blueprint.stages)} 关，题库审校和前端答题都改用这一份",
        "document_id": doc_id,
        "document": doc.model_dump(),
        "blueprint": blueprint.model_dump(),
    }


@app.post("/api/update-blacklist")
def update_blacklist(req: UpdateBlacklistRequest, store: dict = Depends(require_session)):
    doc = owned_document(store, req.document_id)
    doc.spoiler_blacklist = _clean_blacklist(req.spoiler_blacklist)
    bp: Optional[TrainingBlueprint] = store["blueprints"].get(req.document_id)
    if bp:
        bp.spoiler_blacklist = list(doc.spoiler_blacklist)
        violations = audit_and_scrub_spoilers(bp.stages, doc.spoiler_blacklist)
        bp.spoiler_violations = violations
        bp.spoiler_audit_passed = len(violations) == 0
    return {
        "spoiler_blacklist": doc.spoiler_blacklist,
        "blueprint": bp.model_dump() if bp else None,
    }


@app.post("/api/generate-blueprint")
def api_generate_blueprint(req: GenerateBlueprintRequest, store: dict = Depends(require_session)):
    doc = owned_document(store, req.document_id)
    api_key = server_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="DeepSeek 密钥尚未配置，当前仍可使用内置蓝图")
    hit_limit(f"generate:{id(store)}", limit=3, window_seconds=3600)
    hit_limit("generate:global", limit=6, window_seconds=3600)
    blacklist = _clean_blacklist(req.custom_blacklist) if req.custom_blacklist is not None else None
    acquire_llm_slot()
    try:
        bp = generate_training_blueprint_via_deepseek(
            doc=doc,
            api_key=api_key,
            custom_blacklist=blacklist,
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="DeepSeek 动态出题生成失败")
    finally:
        release_llm_slot()
    store["blueprints"][req.document_id] = bp
    return {"blueprint": bp.model_dump()}


@app.post("/api/regenerate-stage")
def api_regenerate_stage(req: RegenerateStageRequest, store: dict = Depends(require_session)):
    doc = owned_document(store, req.document_id)
    bp: Optional[TrainingBlueprint] = store["blueprints"].get(req.document_id)
    if not bp:
        raise HTTPException(status_code=404, detail="请先生成或加载 8 关训练蓝图")
    api_key = server_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="DeepSeek 密钥尚未配置，当前仍可使用内置蓝图")
    hit_limit(f"regen:{id(store)}", limit=8, window_seconds=3600)
    hit_limit("regen:global", limit=20, window_seconds=3600)
    acquire_llm_slot()
    try:
        new_stage = regenerate_single_stage_via_deepseek(
            doc=doc,
            current_blueprint=bp,
            stage_number=req.stage_number,
            teacher_instruction=req.teacher_instruction,
            previous_stage_student_output=req.previous_stage_student_output,
            api_key=api_key,
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="单关重生成失败")
    finally:
        release_llm_slot()
    bp.stages = [new_stage if stage.stage_number == req.stage_number else stage for stage in bp.stages]
    violations = audit_and_scrub_spoilers(bp.stages, bp.spoiler_blacklist[:15])
    bp.spoiler_violations = violations
    bp.spoiler_audit_passed = len(violations) == 0
    return {"stage": new_stage.model_dump(), "blueprint": bp.model_dump()}


@app.post("/api/save-stage")
def api_save_stage(req: UpdateStageRequest, store: dict = Depends(require_session)):
    owned_document(store, req.document_id)
    bp: Optional[TrainingBlueprint] = store["blueprints"].get(req.document_id)
    if not bp:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    if req.stage.stage_number < 1 or req.stage.stage_number > 8:
        raise HTTPException(status_code=400, detail="关卡编号无效")
    bp.stages = [req.stage if stage.stage_number == req.stage.stage_number else stage for stage in bp.stages]
    violations = audit_and_scrub_spoilers(bp.stages, bp.spoiler_blacklist[:15])
    bp.spoiler_violations = violations
    bp.spoiler_audit_passed = len(violations) == 0
    return {"blueprint": bp.model_dump()}


def _answer_catalog(doc, blueprint):
    titles = [{"stage_number": stage.stage_number, "title": stage.title} for stage in blueprint.stages]
    source = "刚上传的 JSON" if str(blueprint.blueprint_id).startswith("bp_live_") else "内置演示蓝图"
    return inactive_view(doc.paradigm_display, doc.document_id, source, titles)


def _answer_bundle(store: dict, document_id: Optional[str] = None):
    doc_id = (document_id or store.get("active_doc_id") or "doc_0001").strip()
    doc = store["documents"].get(doc_id)
    blueprint = store["blueprints"].get(doc_id)
    if not doc or not blueprint:
        raise HTTPException(status_code=404, detail="当前没有可作答的训练蓝图")
    return doc, blueprint


def _active_answer(store: dict):
    attempt = store.get("answer")
    if not attempt:
        raise HTTPException(status_code=400, detail="请先开始一次答题")
    blueprint = store["blueprints"].get(attempt["document_id"])
    if blueprint is None:
        store.pop("answer", None)
        raise HTTPException(status_code=404, detail="这份答题对应的蓝图已经不在了")
    return attempt, blueprint


@app.get("/api/answer")
def api_answer_state(store: dict = Depends(require_session)):
    attempt = store.get("answer")
    if not attempt:
        doc, blueprint = _answer_bundle(store)
        return _answer_catalog(doc, blueprint)
    blueprint = store["blueprints"].get(attempt["document_id"])
    if blueprint is None:
        store.pop("answer", None)
        doc, blueprint = _answer_bundle(store)
        return _answer_catalog(doc, blueprint)
    return public_view(attempt, blueprint)


@app.post("/api/answer/start")
def api_answer_start(req: AnswerStartRequest, request: Request, store: dict = Depends(require_session)):
    hit_limit(f"answer:{client_key(request)}", limit=120, window_seconds=600)
    _doc, blueprint = _answer_bundle(store, req.document_id)
    try:
        attempt = start_attempt(blueprint, req.micro_session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store["answer"] = attempt
    return public_view(attempt, blueprint)


@app.post("/api/answer/submit")
def api_answer_submit(req: AnswerTextRequest, request: Request, store: dict = Depends(require_session)):
    hit_limit(f"answer:{client_key(request)}", limit=120, window_seconds=600)
    attempt, blueprint = _active_answer(store)
    try:
        submit_text(attempt, blueprint, req.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_view(attempt, blueprint)


@app.post("/api/answer/choose")
def api_answer_choose(req: AnswerChooseRequest, request: Request, store: dict = Depends(require_session)):
    hit_limit(f"answer:{client_key(request)}", limit=120, window_seconds=600)
    attempt, blueprint = _active_answer(store)
    try:
        choose_option(attempt, blueprint, req.key, req.custom_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_view(attempt, blueprint)


@app.post("/api/answer/inspiration")
def api_answer_inspiration(request: Request, store: dict = Depends(require_session)):
    hit_limit(f"answer:{client_key(request)}", limit=120, window_seconds=600)
    attempt, blueprint = _active_answer(store)
    try:
        request_inspiration(attempt, blueprint)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_view(attempt, blueprint)


@app.post("/api/answer/confirm")
def api_answer_confirm(req: AnswerConfirmRequest, request: Request, store: dict = Depends(require_session)):
    hit_limit(f"answer:{client_key(request)}", limit=120, window_seconds=600)
    attempt, blueprint = _active_answer(store)
    try:
        confirm_output(attempt, blueprint, req.confirmed_output)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_view(attempt, blueprint)


@app.post("/api/answer/acknowledge")
def api_answer_acknowledge(req: AnswerTextRequest, request: Request, store: dict = Depends(require_session)):
    hit_limit(f"answer:{client_key(request)}", limit=120, window_seconds=600)
    attempt, blueprint = _active_answer(store)
    try:
        acknowledge_compare(attempt, blueprint, req.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_view(attempt, blueprint)


@app.post("/api/answer/reset")
def api_answer_reset(store: dict = Depends(require_session)):
    store.pop("answer", None)
    doc, blueprint = _answer_bundle(store)
    return _answer_catalog(doc, blueprint)


@app.get("/login")
def serve_login():
    return FileResponse(LOGIN_PAGE)


@app.get("/answer")
def serve_answer(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    try:
        if token:
            session_state(request)
        else:
            raise HTTPException(status_code=401, detail="请先登录")
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    if ANSWERING_PAGE.exists():
        return FileResponse(str(ANSWERING_PAGE))
    raise HTTPException(status_code=404, detail="答题页还没有准备好")


@app.get("/")
def serve_index(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    try:
        if token:
            session_state(request)
        else:
            raise HTTPException(status_code=401, detail="请先登录")
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    if QUESTION_BANK_PAGE.exists():
        return FileResponse(str(QUESTION_BANK_PAGE))
    return {"message": "Question bank page not found yet."}
