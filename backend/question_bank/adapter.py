"""
Real JSON Data Adapter & Sanitizer (Section 3 of v2.0 spec).
Handles:
1. Strict 3-state semantics: reported / not_reported / not_applicable
2. v2.facts partial coverage fallback to point_annotations & overview
3. Gate vs. Point conflict interception (e.g., routing_gates.kos_gate_reason vs point_33)
4. Display Quote Sanitization (removing abnormal PDF newlines, normalizing whitespace, adding ellipses)
5. Automatic Spoiler Blacklist & Crime Scene Materials separation
"""

import re
from typing import Dict, Any, List, Tuple, Optional
from backend.question_bank.models import AdaptedDocument, Claim, Evidence, QualityFlag


POINT_ROLE_MAP = {
    "01": "research_problem",
    "02": "research_proposition",
    "03": "theoretical_framework",
    "04": "research_paradigm",
    "05": "sub_discipline_keywords",
    "06": "research_context",
    "07": "study_objective",
    "08": "novelty_claim",
    "32": "feature_representation",
    "33": "knowledge_organization_tool",
    "61": "formula_name",
    "62": "formula_expression",
    "63": "formula_variable",
    "65": "weighting_scheme",
    "66": "transformation_operator",
    "68": "claimed_output",
    "70": "key_finding",
    "71": "relationship_direction",
    "72": "literature_comparison",
    "74": "theoretical_implication",
    "75": "practical_implication",
    "76": "limitation_and_future_work",
}


def sanitize_display_quote(raw_quote: str) -> str:
    """
    Cleans PDF parsing artifacts from quotes:
    - Removes abnormal line breaks inside CJK or between CJK and punctuation/Latin
    - Collapses multiple spaces
    - Adds leading/trailing ellipses (`……`) if the quote appears to be a truncated sentence fragment
    """
    if not raw_quote:
        return ""

    text = raw_quote.strip()

    # Check if it looks like a multi-line mathematical formula (e.g. contains '=' and '(1)' or fraction lines)
    is_formula = bool(re.search(r"\([1-9]\)\s*$", text) or ("=" in text and "\n" in text and len(text) < 80))
    if is_formula:
        # For formulas, collapse newlines into clean spaces or slash notation so it reads cleanly inline
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned_formula = " ".join(lines)
        cleaned_formula = re.sub(r"\s+", " ", cleaned_formula)
        return cleaned_formula

    # Remove newlines between CJK characters (PDF line wrap artifact)
    # e.g., "试图构建一套具备自我更新能力\n的" -> "试图构建一套具备自我更新能力的"
    text = re.sub(r"([\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])\s*\n+\s*([\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])", r"\1\2", text)
    # Remove newlines between CJK and ASCII/numbers
    text = re.sub(r"([\u4e00-\u9fff])\s*\n+\s*([A-Za-z0-9\(（“\"])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z0-9\)）”\"])\s*\n+\s*([\u4e00-\u9fff])", r"\1\2", text)
    # Replace any remaining newlines/tabs with a single space
    text = re.sub(r"[\r\n\t\u3000]+", " ", text)
    # Collapse multiple spaces
    text = re.sub(r" {2,}", " ", text).strip()

    # Remove footnote markers like ①, ② or citation brackets like [16] if at very end? Keep them for accuracy,
    # but check sentence boundaries for ellipses.
    if not text:
        return ""

    # Determine if leading ellipsis is needed
    # If starts with lowercase or connecting words or mid-sentence fragment
    needs_leading = False
    if text[0] in ("，", "。", "、", "；", "：", ",", ";", "与", "的", "了", "和", "及"):
        needs_leading = True
    elif len(text) < 45 and not text.endswith(("。", "！", "？", ".", "!", "?")):
        needs_leading = True

    # Determine if trailing ellipsis is needed
    needs_trailing = not text.endswith(("。", "！", "？", ".", "!", "?", "”", "\"", "）", ")"))
    # Also if it ends with a dangling particle like "具备自我更新能力的" or "认知稀缺的"
    if text.endswith(("的", "与", "和", "在", "了", "，", "、", ":", "：")):
        needs_trailing = True

    prefix = "……" if needs_leading and not text.startswith("……") else ""
    suffix = "……" if needs_trailing and not text.endswith("……") else ""

    return f"{prefix}{text}{suffix}"


def detect_paradigm(point_annotations: Dict[str, Any], routing_gates: Dict[str, Any]) -> Tuple[List[str], str]:
    """
    Identifies the paper's research paradigm from point 04 and routing gates.
    Returns (paradigm_codes, paradigm_display).
    """
    p04 = point_annotations.get("04", {})
    items = p04.get("items", [])
    text_blob = " ".join([str(it.get("value", "")) + " " + str(it.get("quote", "")) for it in items]).lower()

    paradigms = []
    displays = []

    if "设计科学" in text_blob or "design science" in text_blob:
        paradigms.append("design_science")
        displays.append("设计科学 (Design Science)")
    if "理论" in text_blob or "概念" in text_blob or "theoretical" in text_blob:
        paradigms.append("theoretical_conceptual")
        displays.append("理论/概念研究 (Theoretical/Conceptual)")
    if "实证" in text_blob or "定量" in text_blob or "empirical" in text_blob:
        paradigms.append("quantitative_empirical")
        displays.append("定量实证研究 (Quantitative Empirical)")
    if "定性" in text_blob or "案例" in text_blob or "qualitative" in text_blob:
        paradigms.append("qualitative")
        displays.append("定性研究 (Qualitative)")
    if "文献计量" in text_blob or "综述" in text_blob or "bibliometric" in text_blob:
        paradigms.append("bibliometric_review")
        displays.append("文献计量/系统综述 (Bibliometric/Systematic Review)")

    if not paradigms:
        # Fallback inference from routing gates
        if routing_gates.get("has_bibliometric_or_corpus_data"):
            paradigms.append("bibliometric_review")
            displays.append("文献计量/语料研究")
        elif routing_gates.get("has_human_subjects_or_survey"):
            paradigms.append("quantitative_empirical")
            displays.append("实证调查研究")
        else:
            paradigms.append("theoretical_conceptual")
            displays.append("理论与设计研究")

    return paradigms, " / ".join(displays)


def extract_initial_spoiler_blacklist(raw_json: Dict[str, Any], point_annotations: Dict[str, Any]) -> List[str]:
    """
    Extracts proprietary terms, author-coined acronyms, specific theories, and core mechanisms
    from point 05 (keywords), point 08 (novelty), point 03 (theories), point 02 (propositions),
    and point 32/61/66 (formulas/operators) to form the baseline Spoiler Blacklist.
    """
    blacklist = set()

    # Common generic terms that should NOT be blacklisted because they are part of the domain/crime scene
    generic_whitelist = {
        "智慧图书馆", "知识管理", "未来图书馆", "图书馆", "数字图书馆", "传统图书馆",
        "人工智能", "AI", "大模型", "知识图谱", "信息检索", "数据库", "信息爆炸", "认知过载",
        "设计科学", "研究", "系统", "范式"
    }

    # 1. Extract from point 05 quote (Keywords line often has space-separated keywords)
    p05 = point_annotations.get("05", {})
    for it in p05.get("items", []):
        q = it.get("quote", "")
        if "关键词" in q:
            parts = re.split(r"[\s\u3000、，,；;]+", q.replace("关键词", "").strip())
            for p in parts:
                p_clean = p.strip("“”\"'()（）[]")
                if p_clean and len(p_clean) >= 2 and p_clean not in generic_whitelist:
                    blacklist.add(p_clean)
                    if p_clean.endswith("范式") and len(p_clean) > 2:
                        blacklist.add(p_clean[:-2].strip())

    # 2. Extract uppercase/alphanumeric proprietary acronyms from point 02, 03, 07, 08, 32, 61, 66
    target_points = ["02", "03", "07", "08", "32", "61", "63", "65", "66", "70"]
    for pt in target_points:
        p_obj = point_annotations.get(pt, {})
        for it in p_obj.get("items", []):
            combined = f"{it.get('value', '')} {it.get('quote', '')}"
            # Find English/acronym terms like Lib3, W123-CVP, ICSP, SAM, Lsig, Troncale
            acronyms = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,15}", combined)
            for ac in acronyms:
                if ac.endswith("-"):
                    continue
                if ac not in {"The", "And", "For", "Information", "Flux", "Entropy", "Noise", "Design", "Science", "OWL", "CVP"}:
                    blacklist.add(ac)

            # Find quoted proprietary terms in Chinese quotes “...”
            quoted_terms = re.findall(r"“([^”]{2,12})”", combined)
            for qt in quoted_terms:
                qt_clean = re.sub(r"\s+", "", qt)
                if qt_clean not in generic_whitelist and qt_clean not in {
                    "资源容器", "信息不足", "有序性不足", "管理文献", "知识元", "细粒度知识抽取",
                    "辩证", "交叉", "筛选", "目的地", "可证伪潜力", "批判强度", "解释广度"
                }:
                    blacklist.add(qt_clean)

            # Specific theory names in point 03
            if pt == "03":
                if "波普尔" in combined or "世界3" in combined or "世界 3" in combined:
                    blacklist.add("波普尔世界3")
                    blacklist.add("世界3理论")
                if "遗传算法" in combined:
                    blacklist.add("变异—交叉—选择")

            if pt in ("08", "66"):
                if "辩证" in combined and "交叉" in combined and "筛选" in combined:
                    blacklist.add("辩证/交叉/筛选算子")
                    blacklist.add("辩证算子")
                    blacklist.add("交叉算子")
                    blacklist.add("筛选算子")
            if "逼真度" in combined:
                blacklist.add("逼真度")
            if "SAM三元组" in combined or "SAM" in combined:
                blacklist.add("SAM三元组")
            if "知识熵减" in combined:
                blacklist.add("知识熵减")
            if "知识原子化" in combined or "知识原子" in combined:
                blacklist.add("知识原子化")
                blacklist.add("知识原子")

    # Sort putting key recognizable terms first
    priority_order = [
        "Lib3", "知识熵减", "知识原子化", "知识原子", "SAM三元组", "SAM",
        "W123-CVP", "ICSP", "波普尔世界3", "辩证/交叉/筛选算子", "逼真度", "Lsig"
    ]
    sorted_list = []
    for p in priority_order:
        if p in blacklist:
            sorted_list.append(p)
            blacklist.remove(p)
    for rem in sorted(blacklist):
        sorted_list.append(rem)

    return sorted_list


def check_gate_point_conflicts(
    routing_gates: Dict[str, Any],
    point_annotations: Dict[str, Any]
) -> List[QualityFlag]:
    """
    Executes cross-inspection between routing_gates and point_annotations (Section 3.1.3).
    Specifically detects cases like routing_gates.kos_gate_reason stating OWL/KOS design exists,
    while point_annotations['33'] (知识组织工具或映射) is marked as 'not_reported'.
    """
    flags: List[QualityFlag] = []

    # Check 1: KOS Gate vs Point 33 (and Worker_C points 30-39)
    has_kos = routing_gates.get("has_qualitative_or_kos_coding", False)
    kos_reason = str(routing_gates.get("kos_gate_reason", ""))
    p33 = point_annotations.get("33", {})
    if has_kos and ("OWL" in kos_reason or "本体" in kos_reason or "语义关系" in kos_reason or "三元组" in kos_reason):
        if p33.get("status") == "not_reported":
            flags.append(QualityFlag(
                flag_code="GATE_POINT_CONFLICT:point_33",
                severity="warning",
                point="33",
                gate_name="has_qualitative_or_kos_coding / kos_gate_reason",
                description=(
                    f"路由门控 (kos_gate_reason) 明确判定：『{kos_reason}』，"
                    f"但 Point 33（{p33.get('label', '知识组织工具或映射')}）却被标记为 not_reported（未提取到条目）。"
                ),
                suggestion="建议教师在审校后台手动将 OWL 类/属性及 SAM 语义关系补录至 Point 33，或在出题时降级使用 Point 32/61 的证据替代。"
            ))

    # Check 2: Quant Gate vs Worker_D_methods / Worker_D_results
    has_quant = routing_gates.get("has_quantitative_or_algorithm", False)
    quant_reason = str(routing_gates.get("quant_gate_reason", ""))
    if has_quant and ("未报告统计实验" in quant_reason or "概念性" in quant_reason):
        flags.append(QualityFlag(
            flag_code="CONCEPTUAL_FORMULA_ONLY:point_61_68",
            severity="info",
            point="61-68",
            gate_name="has_quantitative_or_algorithm / quant_gate_reason",
            description=(
                f"定量/算法门控已开启，但门控理由指出：『{quant_reason}』。"
                "表明文中公式为概念性/启发式公式，无实证实验或统计检验数据。"
            ),
            suggestion="系统已自动将第 5 关定调为『机制与概念模型构建』、第 7 关定调为『验证与落地路线规划』，严禁强求学生设计 p 值检验或问卷样本。"
        ))

    # Check 3: v2.facts coverage gap check
    return flags


def adapt_lis_mas_json(raw_json: Dict[str, Any], doc_id: str = "doc_0001") -> AdaptedDocument:
    """
    Main adapter entry point: transforms a raw `lis-mas-v0.1` JSON into a clean,
    unified `AdaptedDocument` with full fallback and quote sanitization.
    """
    paper_id = raw_json.get("paper_id", "未知学术论文")
    doc_sha256 = raw_json.get("doc_sha256")
    schema_version = raw_json.get("schema_version", "lis-mas-v0.1")
    point_annotations = raw_json.get("point_annotations", {})
    routing_gates = raw_json.get("routing_gates", {})
    v2_block = raw_json.get("v2", {})

    # 1. Build Evidence Map from v2.evidence first
    evidence_map: Dict[str, Evidence] = {}
    quote_to_ev_ref: Dict[Tuple[str, Optional[int]], str] = {}

    v2_evidence_list = v2_block.get("evidence", [])
    for ev in v2_evidence_list:
        raw_id = ev.get("evidence_id", f"ev-{len(evidence_map)+1}")
        # Normalize ev-2 -> doc_0001:ev_2
        norm_id = f"{doc_id}:{raw_id.replace('-', '_')}"
        raw_q = ev.get("quote", "") or ""
        disp_q = sanitize_display_quote(raw_q)
        start_pos = ev.get("start")
        end_pos = ev.get("end")

        evidence_obj = Evidence(
            evidence_ref=norm_id,
            raw_quote=raw_q,
            display_quote=disp_q,
            offset_start=start_pos,
            offset_end=end_pos,
            section=ev.get("section"),
            page=ev.get("page")
        )
        evidence_map[norm_id] = evidence_obj
        quote_to_ev_ref[(raw_q.strip(), start_pos)] = norm_id

    # 2. Primary extraction from v2.facts
    claims: List[Claim] = []
    covered_points_in_v2 = set()
    seen_claim_signatures = set()

    v2_facts_list = v2_block.get("facts", [])
    for idx, fact in enumerate(v2_facts_list):
        pt = str(fact.get("field") or fact.get("legacy_point") or "")
        if not pt:
            continue
        pt_padded = pt.zfill(2)
        covered_points_in_v2.add(pt_padded)

        val = (fact.get("value") or "").strip()
        if not val:
            continue

        pt_meta = point_annotations.get(pt_padded, {})
        label = pt_meta.get("label", f"Point {pt_padded}")
        role = POINT_ROLE_MAP.get(pt_padded, f"point_{pt_padded}")

        ev_refs = []
        for raw_ev_id in fact.get("evidence_ids", []):
            norm_ev_id = f"{doc_id}:{str(raw_ev_id).replace('-', '_')}"
            if norm_ev_id in evidence_map:
                ev_refs.append(norm_ev_id)

        sig = (pt_padded, val)
        if sig in seen_claim_signatures:
            continue
        seen_claim_signatures.add(sig)

        claim_id = f"{doc_id}:point_{pt_padded}:v2_{fact.get('fact_id', idx+1)}"
        claims.append(Claim(
            claim_id=claim_id,
            point=pt_padded,
            label=label,
            role=role,
            content=val,
            reporting_status="reported",
            evidence_refs=ev_refs,
            source_path=f"v2.facts[{idx}]"
        ))

    # 3. Fallback to point_annotations for uncovered points (e.g. Router points 01-08, 70-76)
    fallback_points_used = []
    point_statuses: Dict[str, Dict[str, Any]] = {}

    for pt_key in sorted(point_annotations.keys(), key=lambda x: int(x) if x.isdigit() else 999):
        pt_obj = point_annotations[pt_key]
        pt_padded = str(pt_obj.get("point", pt_key)).zfill(2)
        status = pt_obj.get("status", "not_applicable")
        label = pt_obj.get("label", f"Point {pt_padded}")
        agent = pt_obj.get("agent", "Unknown")
        items = pt_obj.get("items", [])

        point_statuses[pt_padded] = {
            "point": pt_padded,
            "label": label,
            "agent": agent,
            "status": status,
            "item_count": len(items),
            "source": "v2.facts" if pt_padded in covered_points_in_v2 else "point_annotations"
        }

        if status == "reported" and pt_padded not in covered_points_in_v2:
            fallback_points_used.append(pt_padded)
            for item_idx, item in enumerate(items):
                val = (item.get("value") or "").strip()
                raw_q = item.get("quote") or ""
                start_pos = item.get("start")
                end_pos = item.get("end")

                if not val:
                    continue

                sig = (pt_padded, val)
                if sig in seen_claim_signatures:
                    continue
                seen_claim_signatures.add(sig)

                # Match or create evidence
                ev_refs = []
                if raw_q:
                    ev_key = (raw_q.strip(), start_pos)
                    if ev_key in quote_to_ev_ref:
                        ev_refs.append(quote_to_ev_ref[ev_key])
                    else:
                        new_ev_ref = f"{doc_id}:ev_pa_{pt_padded}_{item_idx+1}"
                        evidence_map[new_ev_ref] = Evidence(
                            evidence_ref=new_ev_ref,
                            raw_quote=raw_q,
                            display_quote=sanitize_display_quote(raw_q),
                            offset_start=start_pos,
                            offset_end=end_pos
                        )
                        quote_to_ev_ref[ev_key] = new_ev_ref
                        ev_refs.append(new_ev_ref)

                role = POINT_ROLE_MAP.get(pt_padded, f"point_{pt_padded}")
                claim_id = f"{doc_id}:point_{pt_padded}:item_{item_idx+1}"
                claims.append(Claim(
                    claim_id=claim_id,
                    point=pt_padded,
                    label=label,
                    role=role,
                    content=val,
                    reporting_status="reported",
                    evidence_refs=ev_refs,
                    source_path=f"point_annotations.{pt_padded}.items[{item_idx}]"
                ))

    # Sort claims by point number
    claims.sort(key=lambda c: int(c.point) if c.point.isdigit() else 999)

    # 4. Separate Crime Scene Materials (Point 06 context + Point 01 problem statement)
    crime_scene_materials = [c for c in claims if c.point in ("01", "06")]

    # 5. Extract Spoiler Blacklist
    spoiler_blacklist = extract_initial_spoiler_blacklist(raw_json, point_annotations)

    # 6. Detect Research Paradigm
    paradigms, paradigm_display = detect_paradigm(point_annotations, routing_gates)

    # 7. Check Gate vs. Point conflicts and quality flags
    quality_flags = check_gate_point_conflicts(routing_gates, point_annotations)
    if fallback_points_used:
        quality_flags.append(QualityFlag(
            flag_code="V2_FACTS_PARTIAL_FALLBACK",
            severity="info",
            point=",".join(fallback_points_used),
            gate_name="v2.facts -> point_annotations",
            description=(
                f"v2.facts 仅覆盖了点位 {sorted(list(covered_points_in_v2))}，"
                f"适配器已自动触发回退机制，从 point_annotations 补齐了 Router 点位 {fallback_points_used}。"
            ),
            suggestion="无需人工干预，已完成统一 Claim 与 Evidence 映射及去重。"
        ))

    # Count sanitized quotes with newlines removed
    newline_cleaned_count = sum(1 for ev in evidence_map.values() if "\n" in ev.raw_quote)
    status_counts = {"reported": 0, "not_reported": 0, "not_applicable": 0}
    for st in point_statuses.values():
        s = st["status"]
        if s in status_counts:
            status_counts[s] += 1

    stats = {
        "total_points_checked": len(point_statuses),
        "reported_points_count": status_counts["reported"],
        "not_reported_points_count": status_counts["not_reported"],
        "not_applicable_points_count": status_counts["not_applicable"],
        "total_claims": len(claims),
        "total_evidence": len(evidence_map),
        "quotes_with_abnormal_newlines_cleaned": newline_cleaned_count,
        "v2_covered_points": sorted(list(covered_points_in_v2)),
        "fallback_covered_points": fallback_points_used,
    }

    return AdaptedDocument(
        document_id=doc_id,
        paper_id=paper_id,
        doc_sha256=doc_sha256,
        schema_version_source=schema_version,
        research_paradigm=paradigms,
        paradigm_display=paradigm_display,
        spoiler_blacklist=spoiler_blacklist,
        crime_scene_materials=crime_scene_materials,
        claims=claims,
        evidence=evidence_map,
        point_statuses=point_statuses,
        routing_gates=routing_gates,
        quality_flags=quality_flags,
        stats=stats
    )
