"""
FastAPI Backend for the Socratic Research Thinking Training System (v2.0).
Provides endpoints for:
1. JSON Upload / Adaptation / Sanitization & Gate-Point Conflict Inspection
2. Four-Step Dynamic Question Builder (Full 8-stage Blueprint & Single-Stage Live Regeneration via DeepSeek API)
3. Spoiler Blacklist Editing & Live Spoiler Firewall Audit
4. Interactive Socratic Prompt & Slot Assembly Preview
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.models import AdaptedDocument, TrainingBlueprint, StageBlueprint
from backend.adapter import adapt_lis_mas_json
from backend.golden_blueprint import build_golden_lib3_blueprint
from backend.question_builder import (
    generate_training_blueprint_via_deepseek,
    regenerate_single_stage_via_deepseek,
    build_question_generator_prompts,
    audit_and_scrub_spoilers,
    DEFAULT_DEEPSEEK_API_KEY
)

app = FastAPI(
    title="引导式科研思维训练系统 v2.0 - 数据清洗与四步动态出题引擎工作台",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_SAMPLE_JSON = BASE_DIR / "data" / "lib3_sample.json"
FALLBACK_JSON_PATH = Path("/home/antigravity/面向知识熵减的智慧图书馆：Lib3范式研究_黄晨_(1).json")
DEFAULT_JSON_PATH = REPO_SAMPLE_JSON if REPO_SAMPLE_JSON.exists() else FALLBACK_JSON_PATH
CACHED_BP_PATH = BASE_DIR / "backend" / "cached_lib3_blueprint.json"

# In-memory store for active documents and blueprints
STORE: Dict[str, Any] = {
    "documents": {},       # doc_id -> AdaptedDocument
    "raw_jsons": {},       # doc_id -> raw dict
    "blueprints": {},      # doc_id -> TrainingBlueprint
    "active_doc_id": "doc_0001"
}


def init_default_document():
    if DEFAULT_JSON_PATH.exists():
        with open(DEFAULT_JSON_PATH, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        doc = adapt_lis_mas_json(raw_data, doc_id="doc_0001")
        STORE["documents"]["doc_0001"] = doc
        STORE["raw_jsons"]["doc_0001"] = raw_data

        loaded = False
        if CACHED_BP_PATH.exists():
            try:
                with open(CACHED_BP_PATH, "r", encoding="utf-8") as bf:
                    bp_data = json.load(bf)
                STORE["blueprints"]["doc_0001"] = TrainingBlueprint(**bp_data)
                loaded = True
            except Exception as e:
                print("Warning: Could not load cached blueprint:", e)

        if not loaded:
            golden_bp = build_golden_lib3_blueprint(doc)
            STORE["blueprints"]["doc_0001"] = golden_bp
            with open(CACHED_BP_PATH, "w", encoding="utf-8") as bf:
                bf.write(golden_bp.model_dump_json(indent=2))


@app.on_event("startup")
def on_startup():
    init_default_document()


class GenerateBlueprintRequest(BaseModel):
    document_id: str = "doc_0001"
    custom_blacklist: Optional[List[str]] = None
    api_key: Optional[str] = None


class RegenerateStageRequest(BaseModel):
    document_id: str = "doc_0001"
    stage_number: int
    teacher_instruction: str = ""
    previous_stage_student_output: str = ""
    api_key: Optional[str] = None


class UpdateStageRequest(BaseModel):
    document_id: str = "doc_0001"
    stage: StageBlueprint


class UpdateBlacklistRequest(BaseModel):
    document_id: str = "doc_0001"
    spoiler_blacklist: List[str]


@app.post("/api/reset-golden-blueprint")
def api_reset_golden_blueprint(req: GenerateBlueprintRequest):
    doc: AdaptedDocument = STORE["documents"].get(req.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    golden_bp = build_golden_lib3_blueprint(doc)
    STORE["blueprints"][req.document_id] = golden_bp
    if req.document_id == "doc_0001":
        with open(CACHED_BP_PATH, "w", encoding="utf-8") as bf:
            bf.write(golden_bp.model_dump_json(indent=2))
    return {"blueprint": golden_bp.model_dump()}


@app.get("/api/state")
def get_current_state(document_id: Optional[str] = None):
    doc_id = document_id or STORE["active_doc_id"]
    if doc_id not in STORE["documents"]:
        init_default_document()
    doc: AdaptedDocument = STORE["documents"].get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="未找到对应的论文 JSON 数据")

    # Reload cached blueprint if it just finished generating in background
    if doc_id not in STORE["blueprints"] and CACHED_BP_PATH.exists() and doc_id == "doc_0001":
        try:
            with open(CACHED_BP_PATH, "r", encoding="utf-8") as bf:
                bp_data = json.load(bf)
            STORE["blueprints"]["doc_0001"] = TrainingBlueprint(**bp_data)
        except Exception:
            pass

    bp: Optional[TrainingBlueprint] = STORE["blueprints"].get(doc_id)
    sys_prompt, usr_prompt = build_question_generator_prompts(doc)

    return {
        "active_doc_id": doc_id,
        "document": doc.model_dump(),
        "blueprint": bp.model_dump() if bp else None,
        "prompts_preview": {
            "system_prompt": sys_prompt,
            "user_prompt": usr_prompt
        },
        "available_documents": [
            {"document_id": k, "paper_id": v.paper_id, "paradigm_display": v.paradigm_display}
            for k, v in STORE["documents"].items()
        ]
    }


@app.post("/api/upload-json")
async def upload_paper_json(file: UploadFile = File(...)):
    try:
        content = await file.read()
        raw_data = json.loads(content.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"JSON 解析失败: {str(e)}")

    if "point_annotations" not in raw_data and "v2" not in raw_data:
        raise HTTPException(status_code=400, detail="上传的 JSON 不符合 lis-mas 标注格式（缺少 point_annotations 或 v2 字段）")

    doc_id = f"doc_{len(STORE['documents']) + 1:04d}"
    doc = adapt_lis_mas_json(raw_data, doc_id=doc_id)
    STORE["documents"][doc_id] = doc
    STORE["raw_jsons"][doc_id] = raw_data
    STORE["active_doc_id"] = doc_id

    return {
        "message": f"成功导入并清洗《{doc.paper_id}》",
        "document_id": doc_id,
        "document": doc.model_dump()
    }


@app.post("/api/update-blacklist")
def update_blacklist(req: UpdateBlacklistRequest):
    doc: AdaptedDocument = STORE["documents"].get(req.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.spoiler_blacklist = req.spoiler_blacklist
    bp: Optional[TrainingBlueprint] = STORE["blueprints"].get(req.document_id)
    if bp:
        bp.spoiler_blacklist = req.spoiler_blacklist
        violations = audit_and_scrub_spoilers(bp.stages, req.spoiler_blacklist)
        bp.spoiler_violations = violations
        bp.spoiler_audit_passed = (len(violations) == 0)

    return {
        "spoiler_blacklist": doc.spoiler_blacklist,
        "blueprint": bp.model_dump() if bp else None
    }


@app.post("/api/generate-blueprint")
def api_generate_blueprint(req: GenerateBlueprintRequest):
    doc: AdaptedDocument = STORE["documents"].get(req.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    api_key = req.api_key or DEFAULT_DEEPSEEK_API_KEY
    try:
        bp = generate_training_blueprint_via_deepseek(
            doc=doc,
            api_key=api_key,
            custom_blacklist=req.custom_blacklist
        )
        STORE["blueprints"][req.document_id] = bp
        if req.document_id == "doc_0001":
            with open(CACHED_BP_PATH, "w", encoding="utf-8") as f:
                f.write(bp.model_dump_json(indent=2))
        return {"blueprint": bp.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DeepSeek 动态出题生成失败: {str(e)}")


@app.post("/api/regenerate-stage")
def api_regenerate_stage(req: RegenerateStageRequest):
    doc: AdaptedDocument = STORE["documents"].get(req.document_id)
    bp: Optional[TrainingBlueprint] = STORE["blueprints"].get(req.document_id)
    if not doc or not bp:
        raise HTTPException(status_code=404, detail="请先生成或加载 8 关训练蓝图")

    api_key = req.api_key or DEFAULT_DEEPSEEK_API_KEY
    try:
        new_stage = regenerate_single_stage_via_deepseek(
            doc=doc,
            current_blueprint=bp,
            stage_number=req.stage_number,
            teacher_instruction=req.teacher_instruction,
            previous_stage_student_output=req.previous_stage_student_output,
            api_key=api_key
        )
        # Replace stage in blueprint
        updated_stages = []
        for s in bp.stages:
            if s.stage_number == req.stage_number:
                updated_stages.append(new_stage)
            else:
                updated_stages.append(s)
        bp.stages = updated_stages
        violations = audit_and_scrub_spoilers(bp.stages, bp.spoiler_blacklist[:15])
        bp.spoiler_violations = violations
        bp.spoiler_audit_passed = (len(violations) == 0)

        if req.document_id == "doc_0001":
            with open(CACHED_BP_PATH, "w", encoding="utf-8") as f:
                f.write(bp.model_dump_json(indent=2))

        return {"stage": new_stage.model_dump(), "blueprint": bp.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"单关重生成失败: {str(e)}")


@app.post("/api/save-stage")
def api_save_stage(req: UpdateStageRequest):
    bp: Optional[TrainingBlueprint] = STORE["blueprints"].get(req.document_id)
    if not bp:
        raise HTTPException(status_code=404, detail="Blueprint not found")

    updated_stages = []
    for s in bp.stages:
        if s.stage_number == req.stage.stage_number:
            updated_stages.append(req.stage)
        else:
            updated_stages.append(s)
    bp.stages = updated_stages
    violations = audit_and_scrub_spoilers(bp.stages, bp.spoiler_blacklist[:15])
    bp.spoiler_violations = violations
    bp.spoiler_audit_passed = (len(violations) == 0)

    if req.document_id == "doc_0001":
        with open(CACHED_BP_PATH, "w", encoding="utf-8") as f:
            f.write(bp.model_dump_json(indent=2))

    return {"blueprint": bp.model_dump()}


# Serve Frontend
FRONTEND_DIR = BASE_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
def serve_index():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Frontend index.html not found yet."}
