"""
Core data models for the Socratic Research Thinking Training System (v2.0).
Strictly decouples:
1. Document / Claim / Evidence (Read-only paper annotation data)
2. TrainingBlueprint (AI-generated, teacher-editable 8-stage teaching blueprint)
3. ResearchState (Student session & research proposal state)
"""

from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class Evidence(BaseModel):
    evidence_ref: str = Field(..., description="Unique evidence reference, e.g. doc_0001:ev_2")
    raw_quote: str = Field(..., description="Original quote with raw newlines for offset provenance")
    display_quote: str = Field(..., description="Cleaned quote for UI display with ellipses and normalized whitespace")
    offset_start: Optional[int] = None
    offset_end: Optional[int] = None
    section: Optional[str] = None
    page: Optional[int] = None


class Claim(BaseModel):
    claim_id: str = Field(..., description="Unique claim ID, e.g. doc_0001:point_01:item_1")
    point: str = Field(..., description="Point number, e.g. '01', '32', '70'")
    label: str = Field(..., description="Point human-readable label")
    role: str = Field(..., description="Semantic role, e.g. research_problem, theoretical_framework, novelty_claim")
    content: str = Field(..., description="Normalized claim content/value")
    reporting_status: Literal["reported", "not_reported", "not_applicable"] = "reported"
    evidence_refs: List[str] = Field(default_factory=list)
    source_path: str = Field(..., description="JSON provenance path, e.g. v2.facts[0] or point_annotations.01.items[1]")


class QualityFlag(BaseModel):
    flag_code: str = Field(..., description="e.g. GATE_POINT_CONFLICT:point_33")
    severity: Literal["warning", "error", "info"] = "warning"
    point: Optional[str] = None
    gate_name: Optional[str] = None
    description: str = Field(..., description="Detailed human-readable explanation of the conflict or issue")
    suggestion: str = Field(..., description="Suggested action for teacher/reviewer")


class AdaptedDocument(BaseModel):
    document_id: str = Field(..., description="Internal document ID, e.g. doc_0001")
    paper_id: str = Field(..., description="Display title/identifier of the paper")
    doc_sha256: Optional[str] = None
    schema_version_source: str = "lis-mas-v0.1"
    research_paradigm: List[str] = Field(default_factory=list, description="e.g. ['design_science']")
    paradigm_display: str = Field(default="设计科学 (Design Science)", description="Human-readable paradigm")
    spoiler_blacklist: List[str] = Field(
        default_factory=list,
        description="Proprietary terms, theories, and mechanisms forbidden during EXPLORE/PROBE/CHOOSE"
    )
    crime_scene_materials: List[Claim] = Field(
        default_factory=list,
        description="Allowed materials for opening problem (point 06 context, point 01 problem statement)"
    )
    claims: List[Claim] = Field(default_factory=list)
    evidence: Dict[str, Evidence] = Field(default_factory=dict, description="Map of evidence_ref -> Evidence")
    point_statuses: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Summary of all 76 points status (reported / not_reported / not_applicable)"
    )
    routing_gates: Dict[str, Any] = Field(default_factory=dict)
    quality_flags: List[QualityFlag] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)


# ==========================================
# 2. TrainingBlueprint Models (Question Builder Output)
# ==========================================

class ChoiceOption(BaseModel):
    key: str = Field(..., description="A, B, C, D, or E")
    dimension: str = Field(..., description="Dimension label, e.g. 资源/基础设施层, 知识表示/组织层")
    text: str = Field(..., description="Option statement")
    follow_up_hook: str = Field(
        ...,
        description="Pre-configured Socratic follow-up hook targeting typical blind spots of this option"
    )


class DiagnosticRubricItem(BaseModel):
    rubric_id: str = Field(..., description="e.g. stage_1_rubric_1")
    criterion: str = Field(..., description="Logical checkpoint condition")
    probe_prompt: str = Field(..., description="Socratic probe question if the student fails this check")


class ExpertAnchor(BaseModel):
    point: str
    label: str
    claim_id: str
    summary: str
    display_quote: str
    raw_quote: str


class StageBlueprint(BaseModel):
    stage_number: int = Field(..., ge=1, le=8)
    stage_code: str = Field(..., description="e.g. problem_discovery, gap_identification, etc.")
    title: str = Field(..., description="e.g. 第 1 关：发现问题（案发现场）")
    subtitle: str = Field(..., description="Short pedagogical purpose")
    paradigm_focus: Optional[str] = Field(None, description="Adaptive focus based on research paradigm (esp. stages 5 & 7)")
    forbidden_demands: List[str] = Field(default_factory=list, description="Things strictly not demanded for this paradigm")
    opening_prompt_template: str = Field(
        ...,
        description="Concrete tension opening question (Stage 1) or dynamic slot template with {{previous_stage_output}} (Stages 2-8)"
    )
    opening_formula_breakdown: Optional[Dict[str, Optional[str]]] = Field(
        None,
        description="For Stage 1: role, counter_intuitive_conflict, diagnostic_question"
    )
    options_type_label: str = Field(default="四维等权辨析选择题", description="Label for the scaffold options")
    options: List[ChoiceOption] = Field(default_factory=list)
    diagnostic_rubrics: List[DiagnosticRubricItem] = Field(default_factory=list)
    expert_anchors: List[ExpertAnchor] = Field(default_factory=list)
    same_topic_comparison_guide: str = Field(
        default="",
        description="Guidance when student path is aligned with paper (同题对照)"
    )
    structural_skeleton_guide: str = Field(
        default="",
        description="Guidance when student path diverges from paper (思维骨架对照)"
    )


class TrainingBlueprint(BaseModel):
    blueprint_id: str
    document_id: str
    paper_id: str
    generated_by: str = Field(default="deepseek-chat", description="Model or source used to generate")
    generated_at: str
    research_paradigm: List[str]
    paradigm_adaptation_note: str
    spoiler_blacklist: List[str]
    micro_sessions: Dict[str, List[int]] = Field(
        default_factory=lambda: {
            "full_8_stages": [1, 2, 3, 4, 5, 6, 7, 8],
            "breakthrough_session": [1, 2],      # 破题局
            "architecture_session": [3, 4, 5],   # 架构局
            "devil_advocate_session": [6, 7]     # 挑刺局
        }
    )
    stages: List[StageBlueprint] = Field(default_factory=list)
    spoiler_audit_passed: bool = True
    spoiler_violations: List[str] = Field(default_factory=list)
