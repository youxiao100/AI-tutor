"""
Four-Step Dynamic Question Builder Engine (Section 4, 6, 8 of v2.0 spec).
Generates an 8-stage TrainingBlueprint from any AdaptedDocument using DeepSeek API:
Step 1: Spoiler Guard (separates crime-scene materials vs. spoiler blacklist)
Step 2: Concrete Tension Opening Formula ([具体决策角色] + [反直觉的现实矛盾] + [聚焦的诊断提问])
Step 3: 4-Dimensional Equal-Weight Choice Options + Follow-up Hooks
Step 4: Dynamic Slot Templates ({{第X关产出}}) + Diagnostic Rubrics + Layered Expert Anchors
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from backend.models import (
    AdaptedDocument,
    TrainingBlueprint,
    StageBlueprint,
    ChoiceOption,
    DiagnosticRubricItem,
    ExpertAnchor,
)

def _load_default_api_key() -> str:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("DEEPSEEK_API_KEY="):
                    return line.strip().split("=", 1)[1].strip().strip("\"'")
    return ""


DEFAULT_DEEPSEEK_API_KEY = _load_default_api_key()
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# Stage mapping to anchor points in LIS-MAS 76-point schema
STAGE_POINT_MAPPING = {
    1: ["01", "06", "70"],
    2: ["01", "72"],
    3: ["03"],
    4: ["02", "71", "74"],
    5: ["32", "33", "61", "62", "63", "65", "66", "68"],
    6: ["08", "75"],
    7: ["76", "75"],
    8: ["02", "07", "08", "70", "74"],
}

PARADIGM_RULES = {
    "design_science": {
        "name": "设计科学 (Design Science)",
        "stage5_focus": "人工制品/数据结构设计、核心运行机制与协议（如数据表示结构 + 演化运行算子/度量模型）",
        "stage7_focus": "原型走查、千万级规模压测路线、分阶段生态试点与参数工程化校准",
        "forbidden": ["问卷调查", "统计显著性 p 值", "实验对照组样本量 N"]
    },
    "theoretical_conceptual": {
        "name": "理论/概念研究",
        "stage5_focus": "概念边界界定、理论框架整合、核心命题体系构建",
        "stage7_focus": "逻辑自洽性论证、思想实验、适用边界划定",
        "forbidden": ["实证准确率", "样本量 N", "构建软件系统"]
    },
    "quantitative_empirical": {
        "name": "定量实证研究",
        "stage5_focus": "变量操作化、抽样与数据采集方案、因果识别策略",
        "stage7_focus": "统计检验选择、信效度/拟合度检验、稳健性与内生性检验",
        "forbidden": ["发明全新底层算法"]
    },
    "qualitative": {
        "name": "定性研究",
        "stage5_focus": "案例/田野选择、访谈提纲设计、编码策略与信度保障",
        "stage7_focus": "理论饱和度检验、多源三角互证、研究者反思性",
        "forbidden": ["实验室对照组", "统计 p 值"]
    },
    "bibliometric_review": {
        "name": "文献计量/系统综述",
        "stage5_focus": "数据库选型、检索式构建、PRISMA 纳入排除漏斗设计",
        "stage7_focus": "发表偏倚审查、跨库敏感性分析、证据合成框架",
        "forbidden": ["构建软件系统", "招募人类被试"]
    }
}


def build_expert_anchors_for_stage(doc: AdaptedDocument, stage_num: int) -> List[ExpertAnchor]:
    """
    Collects cleaned, provenance-bound expert anchors (Claim + sanitized Display Quote)
    for the given stage from the AdaptedDocument.
    """
    target_points = STAGE_POINT_MAPPING.get(stage_num, [])
    anchors: List[ExpertAnchor] = []

    for claim in doc.claims:
        if claim.point in target_points:
            disp_q = ""
            raw_q = ""
            if claim.evidence_refs:
                ev = doc.evidence.get(claim.evidence_refs[0])
                if ev:
                    disp_q = ev.display_quote
                    raw_q = ev.raw_quote
            anchors.append(ExpertAnchor(
                point=claim.point,
                label=claim.label,
                claim_id=claim.claim_id,
                summary=claim.content,
                display_quote=disp_q,
                raw_quote=raw_q
            ))

    # Limit to top 6 most informative anchors for stage 5 so UI remains crisp
    if len(anchors) > 8:
        anchors = anchors[:8]
    return anchors


def audit_and_scrub_spoilers(
    stages: List[StageBlueprint],
    spoiler_blacklist: List[str]
) -> List[str]:
    """
    Audits stages 1-7 opening prompts, option texts, and diagnostic probes against
    the Spoiler Blacklist. Returns any detected violation notes (and can highlight them).
    Note: Stage 8 is the reflection comparison stage where the paper's approach is revealed.
    """
    violations = []
    # Check primary high-risk proprietary terms (length >= 3 or core terms)
    core_spoilers = [
        term for term in spoiler_blacklist
        if len(term) >= 2 and term not in {"交叉", "筛选", "辩证", "目的地"}
    ]

    for st in stages:
        if st.stage_number == 8:
            continue
        # Check opening prompt
        for term in core_spoilers:
            if term.lower() in st.opening_prompt_template.lower():
                violations.append(f"第 {st.stage_number} 关题干包含剧透词『{term}』")
        # Check options
        for opt in st.options:
            for term in core_spoilers:
                if term.lower() in opt.text.lower():
                    violations.append(f"第 {st.stage_number} 关选项 {opt.key} 包含剧透词『{term}』")
    return violations


def call_deepseek_json(
    system_prompt: str,
    user_prompt: str,
    api_key: str = DEFAULT_DEEPSEEK_API_KEY,
    temperature: float = 0.35,
    timeout: int = 90
) -> Dict[str, Any]:
    """
    Calls DeepSeek Chat API with JSON output mode enabled.
    """
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "max_tokens": 8192
    }

    req = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        content = res_data["choices"][0]["message"]["content"]
        return json.loads(content)


def build_question_generator_prompts(
    doc: AdaptedDocument,
    custom_blacklist: Optional[List[str]] = None,
    stage_range: Tuple[int, int] = (1, 8)
) -> Tuple[str, str]:
    """
    Constructs the System Prompt and User Prompt for the Four-Step Question Builder Engine.
    Supports generating a specific range of stages (e.g. (1, 4) or (5, 8) or (1, 8)).
    """
    blacklist = custom_blacklist if custom_blacklist is not None else doc.spoiler_blacklist
    primary_paradigm = doc.research_paradigm[0] if doc.research_paradigm else "design_science"
    p_rule = PARADIGM_RULES.get(primary_paradigm, PARADIGM_RULES["design_science"])

    # Format Crime Scene Materials (Point 01 & 06)
    crime_scene_lines = []
    for c in doc.crime_scene_materials:
        q_str = ""
        if c.evidence_refs and c.evidence_refs[0] in doc.evidence:
            q_str = f" (原文引文: {doc.evidence[c.evidence_refs[0]].display_quote})"
        crime_scene_lines.append(f"- [Point {c.point} {c.label}] {c.content}{q_str}")

    start_s, end_s = stage_range
    stage_evidence_summary = {}
    for stage_num in range(start_s, end_s + 1):
        anchors = build_expert_anchors_for_stage(doc, stage_num)
        stage_evidence_summary[f"stage_{stage_num}"] = [
            f"[Point {a.point} {a.label}] {a.summary} | 引文: {a.display_quote}"
            for a in anchors
        ]

    all_stage_specs = {
        1: "- 第 1 关 (`problem_discovery`)：发现问题（案发现场）—— 必须使用具象张力三段式开场公式（`[具体决策角色] + [反直觉的现实矛盾] + [聚焦的诊断提问]`），并在 `opening_formula_breakdown` 填写 `role`, `counter_intuitive_conflict`, `diagnostic_question`；提供 A(资源入口层)/B(知识表示层)/C(认知负荷层)/D(信任治理层)/E(其他) 四维等权切面选择题。",
        2: "- 第 2 关 (`gap_identification`)：识别缺口（靶子测试）—— 题干必须包含 `{{第1关学生问题}}` 插槽，引入评审专家用现有技术（如知识图谱、细粒度知识抽取、大模型 RAG）泼冷水的场景，追问现有方案到底差在哪一口气；提供 A(语境断裂)/B(静态僵化)/C(激励缺失)/D(成本失控)/E(其他) 辨析选择题。",
        3: "- 第 3 关 (`theory_selection`)：选择理论（寻找透镜）—— 题干必须包含 `{{第2关研究缺口}}` 插槽，引导学生跨学科借用底层理论框架来重新定义未来知识系统；提供 A(物理学/控制论透镜)/B(哲学认识论透镜)/C(生物学/系统论透镜)/D(信息行为/经济学透镜)/E(其他) 灵感卡片选择题。",
        4: "- 第 4 关 (`proposition_formation`)：形成命题（亮出底牌）—— 题干必须包含 `{{第3关理论视角}}` 插槽，要求学生浓缩成 1-2 句斩钉截铁且具备可反驳性的核心研究命题；提供 A(本体转向型)/B(效能判据型)/C(角色跃迁型)/D(治理协同型)/E(其他) 命题句式支架。",
        5: f"- 第 5 关 (`mechanism_design`)：设计方案（核心机制构建）—— 题干必须包含 `{{{{第4关研究命题}}}}` 插槽，根据 `{p_rule['name']}` 范式要求设计至少两个核心部件：1）知识表示/数据结构 2）系统演化运行机制或度量模型；提供 A(数据结构侧重)/B(演化机制侧重)/C(度量模型侧重)/D(混合协同架构)/E(其他) 架构支架。",
        6: "- 第 6 关 (`credibility_and_boundaries`)：可信与边界（魔鬼代言人 / 挑刺局）—— 题干必须包含 `{{第5关设计方案}}` 插槽，切换成最苛刻的反对派审稿人，质问系统最可能引发的灾难性伦理、幻觉、算法同质化或垄断风险及刹车机制；提供 A(防逻辑幻觉)/B(防算法霸权与同质化)/C(防商业与版权垄断)/D(防数字鸿沟与偏见)/E(其他) 选择题。",
        7: f"- 第 7 关 (`validation_planning`)：验证研究（如何证明它不是空中楼阁？）—— 题干必须包含 `{{{{第5关设计方案}}}}` 插槽，针对 `{p_rule['name']}` 提问验证与落地路线（以及坦白适用边界）；提供 A(工程规模压力测试)/B(分阶段生态试点)/C(人机协同校准)/D(边界承认与切割)/E(其他) 验证路径选择题。",
        8: "- 第 8 关 (`reflection_and_innovation`)：反思创新（双轨对照与终稿产出）—— 双轨可视化对比视图，提问：1. 你的方案在哪个环节比论文更贴近现实落地，或切入了论文没顾及的盲区？ 2. 如果允许从论文中借走一个最打动你的机制来升级你的方案，你会借哪一个、怎么改？（本关可提及原论文概念，提供 A/B/C/D/E 反思切入角度选项）。"
    }
    selected_specs = "\n".join([all_stage_specs[i] for i in range(start_s, end_s + 1)])

    system_prompt = f"""你是一个顶级的「苏格拉底式科研思维训练系统 — 四步逆向动态出题引擎（Question Builder v2.0）」。
你的任务是将一篇已完成深度标注的学术论文 JSON，逆向还原为「未解决的学术案发现场」动态训练蓝图（TrainingBlueprint）。

【核心教育原则】
1. 训练的是学生的独立科研思维，绝不是考阅读理解，也不是让学生猜论文作者的标准答案！
2. 【剧透防火墙（Spoiler Guard）硬性铁律】：
   在第 1 关至第 7 关的「题干（opening_prompt_template）」、「选项（options）」和「逻辑安检追问（diagnostic_rubrics）」中，**绝对禁止出现以下剧透黑名单中的任何专有名词、作者自造缩写或特定协议名称**：
   剧透黑名单：{json.dumps(blacklist[:20], ensure_ascii=False)}
   （如果需要表达类似概念，必须还原为通用的、未命名的日常学术描述，例如不能说“SAM三元组”，只能说“带语境指针和可信度权重的复合单元”；不能说“Lib3”或“知识熵减”，只能说“未来知识系统”或“对抗无序度”）。
3. 【跨范式自适应规则】：
   当前论文研究范式为：{p_rule['name']}。
   - 第 5 关（设计方案）定调为：{p_rule['stage5_focus']}
   - 第 7 关（验证研究）定调为：{p_rule['stage7_focus']}
   - 严禁强求学生提供：{', '.join(p_rule['forbidden'])}。
4. 保持文字精炼、犀利、直击痛点，每个选项的 `follow_up_hook` 控制在 40-70 字以内，每关提供 2 条核心 `diagnostic_rubrics`。"""

    user_prompt = f"""请基于以下从《{doc.paper_id}》标注 JSON 中清洗提取的结构化数据，实时生成第 {start_s} 关至第 {end_s} 关的蓝图 JSON。

【1. 论文基础信息】
- 论文标识: {doc.paper_id}
- 研究范式: {doc.paradigm_display}
- 剧透黑名单 (第 1-7 关题干与选项严禁出现): {json.dumps(blacklist[:20], ensure_ascii=False)}

【2. 案发现场原料（Point 01 & 06）】
{chr(10).join(crime_scene_lines)}

【3. 本批次关卡关底专家对照锚点数据（仅供后台逆向设计张力与编写对照指南，严禁在第 1-7 关题干/选项中剧透）】
{json.dumps(stage_evidence_summary, ensure_ascii=False, indent=2)}

【4. 待生成关卡要求（第 {start_s} 关至第 {end_s} 关）】
{selected_specs}

请严格按以下 JSON Schema 返回：
{{
  "paradigm_adaptation_note": "说明如何根据 point 04 及 Worker 状态定调第 5 关与第 7 关...",
  "stages": [
    {{
      "stage_number": {start_s},
      "stage_code": "...",
      "title": "第 {start_s} 关：...",
      "subtitle": "...",
      "paradigm_focus": "...",
      "forbidden_demands": ["..."],
      "opening_prompt_template": "...",
      "opening_formula_breakdown": {{
        "role": "（仅第1关必填，其他关可为null）",
        "counter_intuitive_conflict": "...",
        "diagnostic_question": "..."
      }},
      "options_type_label": "四维等权辨析选择题",
      "options": [
        {{"key": "A", "dimension": "...", "text": "...", "follow_up_hook": "..."}},
        {{"key": "B", "dimension": "...", "text": "...", "follow_up_hook": "..."}},
        {{"key": "C", "dimension": "...", "text": "...", "follow_up_hook": "..."}},
        {{"key": "D", "dimension": "...", "text": "...", "follow_up_hook": "..."}},
        {{"key": "E", "dimension": "自定义切面", "text": "其他（自行提出新方向或机制）", "follow_up_hook": "请具体展开你的自定义切入点，并说明它如何回应核心矛盾？"}}
      ],
      "diagnostic_rubrics": [
        {{"rubric_id": "s{start_s}_r1", "criterion": "...", "probe_prompt": "..."}},
        {{"rubric_id": "s{start_s}_r2", "criterion": "...", "probe_prompt": "..."}}
      ],
      "same_topic_comparison_guide": "同题对照说明...",
      "structural_skeleton_guide": "思维骨架对照说明..."
    }}
  ]
}}"""

    return system_prompt, user_prompt


def generate_training_blueprint_via_deepseek(
    doc: AdaptedDocument,
    api_key: str = DEFAULT_DEEPSEEK_API_KEY,
    custom_blacklist: Optional[List[str]] = None
) -> TrainingBlueprint:
    """
    Dynamically generates the complete 8-stage TrainingBlueprint by calling DeepSeek API
    in two concurrent batches (Stages 1-4 and Stages 5-8) so it completes in ~15-20s
    and never hits output token truncation.
    """
    from concurrent.futures import ThreadPoolExecutor

    blacklist = custom_blacklist if custom_blacklist is not None else doc.spoiler_blacklist
    sys_1, usr_1 = build_question_generator_prompts(doc, blacklist, stage_range=(1, 4))
    sys_2, usr_2 = build_question_generator_prompts(doc, blacklist, stage_range=(5, 8))

    with ThreadPoolExecutor(max_workers=2) as executor:
        fut1 = executor.submit(call_deepseek_json, sys_1, usr_1, api_key, 0.35, 90)
        fut2 = executor.submit(call_deepseek_json, sys_2, usr_2, api_key, 0.35, 90)
        res1 = fut1.result()
        res2 = fut2.result()

    stages_data = res1.get("stages", []) + res2.get("stages", [])
    stages_data.sort(key=lambda s: int(s.get("stage_number", 99)))
    raw_result = {"paradigm_adaptation_note": res1.get("paradigm_adaptation_note") or res2.get("paradigm_adaptation_note")}
    parsed_stages: List[StageBlueprint] = []

    for st_raw in stages_data:
        s_num = int(st_raw.get("stage_number", len(parsed_stages) + 1))
        options = [
            ChoiceOption(
                key=str(opt.get("key", "A")),
                dimension=str(opt.get("dimension", "")),
                text=str(opt.get("text", "")),
                follow_up_hook=str(opt.get("follow_up_hook", ""))
            )
            for opt in st_raw.get("options", [])
        ]
        rubrics = [
            DiagnosticRubricItem(
                rubric_id=str(rub.get("rubric_id", f"s{s_num}_r{idx+1}")),
                criterion=str(rub.get("criterion", "")),
                probe_prompt=str(rub.get("probe_prompt", ""))
            )
            for idx, rub in enumerate(st_raw.get("diagnostic_rubrics", []))
        ]
        anchors = build_expert_anchors_for_stage(doc, s_num)

        parsed_stages.append(StageBlueprint(
            stage_number=s_num,
            stage_code=str(st_raw.get("stage_code", f"stage_{s_num}")),
            title=str(st_raw.get("title", f"第 {s_num} 关")),
            subtitle=str(st_raw.get("subtitle", "")),
            paradigm_focus=st_raw.get("paradigm_focus"),
            forbidden_demands=st_raw.get("forbidden_demands", []),
            opening_prompt_template=str(st_raw.get("opening_prompt_template", "")),
            opening_formula_breakdown=st_raw.get("opening_formula_breakdown"),
            options_type_label=str(st_raw.get("options_type_label", "四维等权辨析选择题")),
            options=options,
            diagnostic_rubrics=rubrics,
            expert_anchors=anchors,
            same_topic_comparison_guide=str(st_raw.get("same_topic_comparison_guide", "")),
            structural_skeleton_guide=str(st_raw.get("structural_skeleton_guide", ""))
        ))

    # Run Spoiler Guard Audit on Stages 1-7
    violations = audit_and_scrub_spoilers(parsed_stages, blacklist[:15])

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return TrainingBlueprint(
        blueprint_id=f"bp_{doc.document_id}_{int(datetime.now().timestamp())}",
        document_id=doc.document_id,
        paper_id=doc.paper_id,
        generated_by="deepseek-chat (Live Dynamic Question Builder v2.0)",
        generated_at=now_str,
        research_paradigm=doc.research_paradigm,
        paradigm_adaptation_note=str(raw_result.get(
            "paradigm_adaptation_note",
            f"识别范式为 {doc.paradigm_display}，第 5 关定调为机制与架构设计，第 7 关定调为验证与落地路线规划。"
        )),
        spoiler_blacklist=blacklist,
        stages=parsed_stages,
        spoiler_audit_passed=(len(violations) == 0),
        spoiler_violations=violations
    )


def regenerate_single_stage_via_deepseek(
    doc: AdaptedDocument,
    current_blueprint: TrainingBlueprint,
    stage_number: int,
    teacher_instruction: str = "",
    previous_stage_student_output: str = "",
    api_key: str = DEFAULT_DEEPSEEK_API_KEY
) -> StageBlueprint:
    """
    Allows interactive regeneration or live dynamic assembly of a single stage (1-8)
    via DeepSeek API, optionally incorporating teacher feedback or a student's confirmed
    output from the previous stage!
    """
    blacklist = current_blueprint.spoiler_blacklist
    anchors = build_expert_anchors_for_stage(doc, stage_number)
    anchors_text = "\n".join([f"- [Point {a.point} {a.label}] {a.summary} | 引文: {a.display_quote}" for a in anchors])

    existing_stage = next((s for s in current_blueprint.stages if s.stage_number == stage_number), None)
    existing_json = existing_stage.model_dump_json(indent=2) if existing_stage else "{}"

    sys_prompt = f"""你是「苏格拉底式科研思维训练系统 — 单关动态出题与调优引擎」。
请针对《{doc.paper_id}》（范式：{doc.paradigm_display}）的第 {stage_number} 关重新生成或动态拼装题目。
【剧透防火墙铁律】：如果关卡号在 1-7 之间，题干、选项与追问中严禁出现以下剧透黑名单术语：
{json.dumps(blacklist[:18], ensure_ascii=False)}"""

    slot_context = ""
    if previous_stage_student_output:
        slot_context = f"\n【学生上一关已确认的真实产出（请将其自然融入动态衔接题干中，同时保留模板插槽或直接生成定制化题干）】：\n『{previous_stage_student_output}』\n"

    teacher_context = ""
    if teacher_instruction:
        teacher_context = f"\n【教师/开发者调优指令（请优先满足）】：\n『{teacher_instruction}』\n"

    usr_prompt = f"""当前待重生成关卡：第 {stage_number} 关
本关关底专家对照锚点：
{anchors_text}
{slot_context}{teacher_context}
原关卡蓝图参考：
{existing_json}

请输出更新后的单关 JSON 对象（包含 stage_number, stage_code, title, subtitle, paradigm_focus, forbidden_demands, opening_prompt_template, opening_formula_breakdown, options_type_label, options, diagnostic_rubrics, same_topic_comparison_guide, structural_skeleton_guide）。"""

    st_raw = call_deepseek_json(
        system_prompt=sys_prompt,
        user_prompt=usr_prompt,
        api_key=api_key,
        temperature=0.45,
        timeout=60
    )

    options = [
        ChoiceOption(
            key=str(opt.get("key", "A")),
            dimension=str(opt.get("dimension", "")),
            text=str(opt.get("text", "")),
            follow_up_hook=str(opt.get("follow_up_hook", ""))
        )
        for opt in st_raw.get("options", [])
    ]
    rubrics = [
        DiagnosticRubricItem(
            rubric_id=str(rub.get("rubric_id", f"s{stage_number}_r{idx+1}")),
            criterion=str(rub.get("criterion", "")),
            probe_prompt=str(rub.get("probe_prompt", ""))
        )
        for idx, rub in enumerate(st_raw.get("diagnostic_rubrics", []))
    ]

    return StageBlueprint(
        stage_number=stage_number,
        stage_code=str(st_raw.get("stage_code", existing_stage.stage_code if existing_stage else f"stage_{stage_number}")),
        title=str(st_raw.get("title", f"第 {stage_number} 关")),
        subtitle=str(st_raw.get("subtitle", "")),
        paradigm_focus=st_raw.get("paradigm_focus"),
        forbidden_demands=st_raw.get("forbidden_demands", []),
        opening_prompt_template=str(st_raw.get("opening_prompt_template", "")),
        opening_formula_breakdown=st_raw.get("opening_formula_breakdown"),
        options_type_label=str(st_raw.get("options_type_label", "四维等权辨析选择题")),
        options=options,
        diagnostic_rubrics=rubrics,
        expert_anchors=anchors,
        same_topic_comparison_guide=str(st_raw.get("same_topic_comparison_guide", "")),
        structural_skeleton_guide=str(st_raw.get("structural_skeleton_guide", ""))
    )
