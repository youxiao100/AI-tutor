"""Student answering on top of a TrainingBlueprint.

The workshop already builds the questions. This module runs the student side:
free response, one rubric probe at a time, equal-weight choices after two vague
rounds, and a confirmed stage output. Nothing is scored by similarity to the paper,
and paper-specific terms stay hidden until the comparison step.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from backend.question_bank.models import StageBlueprint, TrainingBlueprint


MICRO_SESSIONS: Dict[str, Dict[str, object]] = {
    "full_8_stages": {
        "label": "全流程 8 关",
        "detail": "从案发现场走到双轨对照，形成一份完整构思。",
        "stages": [1, 2, 3, 4, 5, 6, 7, 8],
    },
    "breakthrough_session": {
        "label": "破题局（第 1–2 关）",
        "detail": "只练问题与缺口，大约一轮课堂能做完。",
        "stages": [1, 2],
    },
    "architecture_session": {
        "label": "架构局（第 3–5 关）",
        "detail": "从理论透镜写到方案。前面的关卡用案发现场背景补齐。",
        "stages": [3, 4, 5],
    },
    "devil_advocate_session": {
        "label": "挑刺局（第 6–7 关）",
        "detail": "只练风险刹车和验证边界。",
        "stages": [6, 7],
    },
}

# Stage 1 options A/D are the forks called out in the blueprint (入口整合 / 信任治理).
FORK_KEYS = {1: {"A", "D"}}

BACKGROUND_FILL = (
    "检索已经可以在一秒内找到文献，但读者仍然看不过来，也难以直接拿去开展研究。"
    "这只是案发现场背景，不是你的结论。"
)

SLOT_STAGE = {
    "第1关学生问题": 1,
    "第2关研究缺口": 2,
    "第3关理论视角": 3,
    "第4关研究命题": 4,
    "第5关设计方案": 5,
    "previous_stage_output": "previous",
}

RUBRIC_TERMS: Dict[str, List[List[str]]] = {
    "problem_discovery": [
        ["检索", "文献", "知识", "认知", "入口", "数据库", "读者", "单元", "表示", "负荷", "信任", "验证", "带宽", "学者", "排序"],
        ["反而", "为什么", "矛盾", "瓶颈", "负担", "过载", "焦虑", "转嫁", "越搜", "看不过来"],
    ],
    "gap_identification": [
        ["知识图谱", "RAG", "抽取", "知识元", "大模型", "现有", "图谱", "向量", "检索系统"],
        ["语境", "溯源", "静态", "演化", "版权", "激励", "成本", "结构", "局限", "失真", "脉络", "粒度", "更新"],
    ],
    "theory_selection": [
        ["熵", "理论", "控制论", "哲学", "认识论", "生物", "进化", "系统论", "成本", "透镜", "框架", "经济", "认知"],
        ["因为", "所以", "解释", "对应", "映射", "用来", "意味着", "从而", "才能"],
    ],
    "proposition_formation": [
        ["只有", "应当", "转向", "本质", "而非", "不是", "必须", "才成立"],
        ["因为", "所以", "如果", "否则", "才能", "一旦"],
    ],
    "mechanism_design": [
        ["结构", "表示", "单元", "数据", "字段", "节点", "指针"],
        ["机制", "输入", "处理", "输出", "运行", "演化", "循环", "协议"],
    ],
    "credibility_and_boundaries": [
        ["风险", "幻觉", "垄断", "偏见", "同质", "版权", "灾难", "副作用"],
        ["校验", "审核", "协议", "刹车", "分区", "阈值", "锚定", "溯源", "终审", "指纹", "扰动"],
    ],
    "validation_planning": [
        ["测试", "试点", "校准", "验证", "阶段", "压测", "联盟", "路线"],
        ["局限", "边界", "做不到", "尚未", "不适用", "硬伤", "默会", "算力", "坦白", "留待"],
    ],
    "reflection_and_innovation": [
        ["我", "落地", "盲区", "优势", "贴近", "顾及", "独立"],
        ["借", "吸收", "改进", "改造", "坚持", "升级", "局限"],
    ],
}

PRAISE_WORDS = ("非常", "极具", "很好", "精妙", "扎实", "硬核", "有底气", "直观", "太棒", "深刻", "洞察")
PRAISE_HITS = ("深刻", "洞察", "太棒", "非常好", "极具洞察", "很好！", "很好。")

LEVEL_LABELS = ["还没形成", "只碰到现象", "说清了一半", "安检条件齐了"]
DIMENSIONS = [
    ("问题清晰性", [1]),
    ("逻辑解释力", [2]),
    ("理论适配性", [3, 4]),
    ("方案可行性", [5]),
    ("证据与边界意识", [6, 7]),
    ("批判与独立反思", [8]),
]

OPENING_TUTOR = (
    "先用自己的话答。这一关没有标准答案，也不拿你和论文的相似度打分。"
    "说不清时我只追问一个缺口；连续两轮都停在笼统说法上，再给你几条价值相等的方向。"
)
CHOOSE_TUTOR = (
    "连续两轮都停在笼统或重复的说法上，再追问同一个点没有意义。"
    "下面这些方向学术价值相等，没有标准答案。选一条，再用自己的话说明理由。"
)
SYNTH_TUTOR = (
    "能从你自己的话里站住的部分，我按原话整理成阶段产出。"
    "改到你认可再确认；没确认的不会写入研究状态。"
)
COMPARE_TUTOR = (
    "下面是这一关重构出来的论文锚点，用来对照论证结构，不是标准答案。"
    "你的方向和论文不同也不算错。可以写一句反思，也可以直接进入下一关。"
)
INSPIRATION_TUTOR = (
    "这些是跨范式的通用卡片，不是这篇论文的答案，也没有哪一张更正确。"
    "选一张，再用自己的话说明它如何对上你的问题。"
)


def _stage_map(blueprint: TrainingBlueprint) -> Dict[int, StageBlueprint]:
    return {stage.stage_number: stage for stage in blueprint.stages}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def is_vague(text: str) -> bool:
    compact = _compact(text)
    if len(compact) < 18:
        return True
    canned = (
        "缺乏统一管理",
        "不够智能",
        "信息很多",
        "没有想法",
        "不知道怎么",
        "都可以",
        "随便说说",
        "需要加强监管",
        "加强伦理",
        "更好地服务读者",
    )
    return any(compact == item or (compact.startswith(item) and len(compact) < len(item) + 6) for item in canned)


def has_any(text: str, words: List[str]) -> bool:
    haystack = text or ""
    return any(word in haystack for word in words)


def failed_rubric_ids(stage: StageBlueprint, text: str) -> List[str]:
    checks = RUBRIC_TERMS.get(stage.stage_code, [])
    failed: List[str] = []
    for index, rubric in enumerate(stage.diagnostic_rubrics):
        terms = checks[index] if index < len(checks) else []
        if terms and not has_any(text, terms):
            failed.append(rubric.rubric_id)
    if stage.stage_code == "credibility_and_boundaries" and len(stage.diagnostic_rubrics) >= 2:
        hard = RUBRIC_TERMS["credibility_and_boundaries"][1]
        slogan = ["加强监管", "伦理规范", "提高素养", "加强法律", "加强管理"]
        if has_any(text, slogan) and not has_any(text, hard):
            rubric_id = stage.diagnostic_rubrics[1].rubric_id
            if rubric_id not in failed:
                failed.append(rubric_id)
    if stage.stage_code == "proposition_formation" and stage.diagnostic_rubrics:
        if re.search(r"更好地服务|应该更好|非常重要", text):
            claim_terms = RUBRIC_TERMS["proposition_formation"][0]
            if not has_any(text, claim_terms):
                rubric_id = stage.diagnostic_rubrics[0].rubric_id
                if rubric_id not in failed:
                    failed.append(rubric_id)
    return failed


def neutralize_hook(hook: str) -> str:
    """Drop compliment lead-ins from blueprint hooks. Keep the actual question."""
    parts = re.split(r"(?<=[！。])", (hook or "").strip())
    kept: List[str] = []
    for index, part in enumerate(parts):
        compliment = any(word in part for word in PRAISE_WORDS) and "？" not in part and "?" not in part
        if compliment and (index == 0 or "？" not in part):
            continue
        kept.append(part)
    text = "".join(kept).strip()
    if not text:
        return "用你自己的话写清楚：这个方向忽略了什么，你准备怎么补。"
    return text


def scrub_spoilers(text: str, blacklist: List[str]) -> str:
    if not text:
        return text
    skip = {"交叉", "筛选", "辩证", "目的地"}
    terms = [term for term in blacklist if term and len(term) >= 2 and term not in skip]
    terms.sort(key=len, reverse=True)
    cleaned = text
    for term in terms:
        cleaned = re.sub(re.escape(term), "〔对照后再看〕", cleaned, flags=re.IGNORECASE)
    return cleaned


def _order_for(blueprint: TrainingBlueprint, micro_session: str) -> List[int]:
    spec = MICRO_SESSIONS.get(micro_session)
    if spec is None:
        raise ValueError("没有这种训练切片")
    raw = list(spec["stages"])
    custom = (blueprint.micro_sessions or {}).get(micro_session)
    if custom:
        raw = [int(number) for number in custom]
    have = _stage_map(blueprint)
    order = [number for number in raw if number in have]
    if not order:
        raise ValueError("这套蓝图里没有该切片的关卡")
    return order


def _new_record(stage_number: int) -> dict:
    return {
        "stage_number": stage_number,
        "status": "open",
        "vague_streak": 0,
        "probe_count": 0,
        "justify_once": False,
        "last_student_text": "",
        "substantive_text": "",
        "chosen_key": None,
        "chosen_dimension": "",
        "chosen_text": "",
        "justification": "",
        "draft_output": "",
        "confirmed_output": "",
        "open_questions": [],
        "passed_rubric_ids": [],
        "comparison_mode": None,
        "reflection": "",
        "messages": [],
    }


def _current_number(attempt: dict) -> int:
    if attempt.get("finished"):
        return attempt["stage_order"][-1]
    return attempt["stage_order"][attempt["index"]]


def _current_record(attempt: dict) -> dict:
    return attempt["stages"][_current_number(attempt)]


def _current_stage(attempt: dict, blueprint: TrainingBlueprint) -> StageBlueprint:
    found = _stage_map(blueprint).get(_current_number(attempt))
    if found is None:
        raise ValueError("当前关卡不在蓝图里")
    return found


def _previous_number(attempt: dict) -> Optional[int]:
    index = attempt["index"]
    if attempt.get("finished"):
        index = len(attempt["stage_order"])
    if index <= 0:
        return None
    return attempt["stage_order"][index - 1]


def _confirmed(attempt: dict, stage_number: int) -> str:
    record = attempt["stages"].get(stage_number)
    if not record:
        return ""
    return (record.get("confirmed_output") or "").strip()


def _slot_value(attempt: dict, slot_name: str) -> Tuple[str, bool]:
    target = SLOT_STAGE.get(slot_name.strip(), "previous")
    if target == "previous":
        previous = _previous_number(attempt)
        if previous is None:
            return BACKGROUND_FILL, True
        text = _confirmed(attempt, previous)
        if text:
            return text, False
        return BACKGROUND_FILL, True
    text = _confirmed(attempt, int(target))
    if text:
        return text, False
    return BACKGROUND_FILL, True


def render_prompt(attempt: dict, stage: StageBlueprint) -> Tuple[str, bool]:
    used_background = False

    def replace(match: re.Match) -> str:
        nonlocal used_background
        value, filled = _slot_value(attempt, match.group(1))
        if filled:
            used_background = True
        return value

    text = re.sub(r"\{\{([^}]+)\}\}", replace, stage.opening_prompt_template)
    if stage.stage_number >= 2 and "{{" not in stage.opening_prompt_template:
        previous = _previous_number(attempt)
        if previous is not None:
            value, filled = _slot_value(attempt, "previous_stage_output")
            if filled:
                used_background = True
                text = f"这一关前面没有你确认过的产出。背景只保留这一句：{value}\n\n{text}"
            else:
                text = f"你已经确认的上一步是：「{value}」\n\n{text}"
    return text, used_background


def _locked(attempt: dict) -> bool:
    if attempt.get("phase") in {"compare", "report"}:
        return False
    return _current_number(attempt) < 8


def _say(attempt: dict, record: dict, text: str, blueprint: TrainingBlueprint) -> str:
    if _locked(attempt):
        text = scrub_spoilers(text, blueprint.spoiler_blacklist)
    attempt["tutor_message"] = text
    record["messages"].append({"role": "tutor", "text": text})
    return text


def _note_passes(record: dict, stage: StageBlueprint, failed: List[str]) -> None:
    record["passed_rubric_ids"] = [item.rubric_id for item in stage.diagnostic_rubrics if item.rubric_id not in failed]
    record["open_questions"] = [item.criterion for item in stage.diagnostic_rubrics if item.rubric_id in failed]


def _draft_from(record: dict) -> str:
    parts: List[str] = []
    if record.get("chosen_text"):
        parts.append(f"【方向】{record.get('chosen_dimension') or ''}：{record['chosen_text']}")
    substantive = (record.get("substantive_text") or "").strip()
    if substantive and substantive not in (record.get("chosen_text") or ""):
        parts.append(substantive)
    if not parts:
        parts.append((record.get("last_student_text") or "").strip())
    return "\n".join(part for part in parts if part).strip()


def _enter_synthesize(attempt: dict, record: dict, stage: StageBlueprint, blueprint: TrainingBlueprint) -> None:
    record["draft_output"] = _draft_from(record)
    attempt["phase"] = "synthesize"
    extra = ""
    if record["open_questions"]:
        extra = "还有没说清的点，先记成未解疑点，不判你错：" + "；".join(record["open_questions"])
    _say(attempt, record, SYNTH_TUTOR + extra, blueprint)


def _enter_choose(attempt: dict, record: dict, blueprint: TrainingBlueprint, tutor: str = CHOOSE_TUTOR) -> None:
    attempt["phase"] = "choose"
    _say(attempt, record, tutor, blueprint)


def _enter_probe(attempt: dict, record: dict, stage: StageBlueprint, blueprint: TrainingBlueprint, failed: List[str], student_text: str) -> None:
    rubric = next(item for item in stage.diagnostic_rubrics if item.rubric_id == failed[0])
    snippet = _compact(student_text)[:36] or "这段话"
    message = f"你写到「{snippet}」。这一关还没过的是：{rubric.criterion}{rubric.probe_prompt}"
    attempt["phase"] = "probe"
    record["probe_count"] = int(record.get("probe_count") or 0) + 1
    _say(attempt, record, message, blueprint)


def _enter_compare(attempt: dict, record: dict, blueprint: TrainingBlueprint) -> None:
    record["comparison_mode"] = "structural_skeleton" if attempt.get("diverged") else "same_topic"
    record["status"] = "confirmed"
    if _current_number(attempt) >= 8:
        _enter_report(attempt, blueprint)
        return
    attempt["phase"] = "compare"
    _say(attempt, record, COMPARE_TUTOR, blueprint)


def _enter_report(attempt: dict, blueprint: TrainingBlueprint) -> None:
    attempt["phase"] = "report"
    attempt["finished"] = True
    record = _current_record(attempt)
    message = "这一切片里你确认过的产出已经收成报告。分数只看每关安检过没过，不看和论文像不像。"
    _say(attempt, record, message, blueprint)


def _diagnose(stage: StageBlueprint, text: str, vague: bool) -> List[str]:
    if vague:
        return [item.rubric_id for item in stage.diagnostic_rubrics]
    return failed_rubric_ids(stage, text)


def _after_student_answer(attempt: dict, blueprint: TrainingBlueprint, text: str, blob: Optional[str] = None) -> None:
    stage = _current_stage(attempt, blueprint)
    record = _current_record(attempt)
    repeated = bool(record.get("last_student_text")) and _compact(record["last_student_text"]) == _compact(text)
    vague = is_vague(text) or repeated
    record["last_student_text"] = text
    record["messages"].append({"role": "student", "text": text})
    if vague:
        record["vague_streak"] = int(record.get("vague_streak") or 0) + 1
    else:
        record["vague_streak"] = 0
        record["substantive_text"] = text
    judged = blob if blob is not None else text
    failed = _diagnose(stage, judged, vague)
    _note_passes(record, stage, failed)
    if not failed:
        _enter_synthesize(attempt, record, stage, blueprint)
        return
    if record["vague_streak"] >= 2 or int(record.get("probe_count") or 0) >= 3:
        _enter_choose(attempt, record, blueprint)
        return
    _enter_probe(attempt, record, stage, blueprint, failed, text)


def start_attempt(blueprint: TrainingBlueprint, micro_session: str) -> dict:
    order = _order_for(blueprint, micro_session)
    spec = MICRO_SESSIONS[micro_session]
    attempt = {
        "document_id": blueprint.document_id,
        "micro_session": micro_session,
        "micro_label": spec["label"],
        "stage_order": order,
        "index": 0,
        "phase": "explore",
        "diverged": False,
        "tutor_message": OPENING_TUTOR,
        "finished": False,
        "stages": {},
    }
    record = _new_record(order[0])
    record["messages"].append({"role": "tutor", "text": OPENING_TUTOR})
    attempt["stages"][order[0]] = record
    return attempt


def submit_text(attempt: dict, blueprint: TrainingBlueprint, text: str) -> None:
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("请先写一点内容")
    if len(cleaned) > 2000:
        raise ValueError("单次回答请控制在 2000 字以内")
    phase = attempt.get("phase")
    if phase not in {"explore", "probe", "justify"}:
        raise ValueError("这一步不用文字作答")
    record = _current_record(attempt)
    if phase == "justify":
        stage = _current_stage(attempt, blueprint)
        record["messages"].append({"role": "student", "text": cleaned})
        if is_vague(cleaned) and not record.get("justify_once"):
            record["justify_once"] = True
            record["vague_streak"] = int(record.get("vague_streak") or 0) + 1
            hook = ""
            for option in stage.options:
                if option.key == record.get("chosen_key"):
                    hook = option.follow_up_hook
                    break
            _say(attempt, record, neutralize_hook(hook), blueprint)
            return
        record["justification"] = cleaned
        record["substantive_text"] = cleaned
        blob = f"{record.get('chosen_text') or ''}\n{cleaned}"
        failed = _diagnose(stage, blob, is_vague(cleaned))
        _note_passes(record, stage, failed)
        _enter_synthesize(attempt, record, stage, blueprint)
        return
    _after_student_answer(attempt, blueprint, cleaned)


def choose_option(attempt: dict, blueprint: TrainingBlueprint, key: str, custom_text: str = "") -> None:
    if attempt.get("phase") != "choose":
        raise ValueError("现在还不是选择题")
    stage = _current_stage(attempt, blueprint)
    picked = (key or "").strip().upper()
    option = next((item for item in stage.options if item.key.upper() == picked), None)
    if option is None:
        raise ValueError("没有这个选项")
    custom = (custom_text or "").strip()
    if option.key.upper() == "E" and len(_compact(custom)) < 4:
        raise ValueError("选「其他」时，请写明你自己的方向")
    record = _current_record(attempt)
    record["chosen_key"] = option.key
    record["chosen_dimension"] = option.dimension
    record["chosen_text"] = custom if option.key.upper() == "E" else option.text
    if picked in FORK_KEYS.get(stage.stage_number, set()):
        attempt["diverged"] = True
    attempt["phase"] = "justify"
    record["messages"].append({"role": "student", "text": f"选择 {option.key}. {record['chosen_text']}"})
    _say(attempt, record, neutralize_hook(option.follow_up_hook), blueprint)


def request_inspiration(attempt: dict, blueprint: TrainingBlueprint) -> None:
    stage = _current_stage(attempt, blueprint)
    if stage.stage_code not in {"theory_selection", "mechanism_design"}:
        raise ValueError("这一关没有灵感卡片")
    if attempt.get("phase") not in {"explore", "probe"}:
        raise ValueError("现在不能抽卡片")
    record = _current_record(attempt)
    _enter_choose(attempt, record, blueprint, INSPIRATION_TUTOR)


def confirm_output(attempt: dict, blueprint: TrainingBlueprint, confirmed_output: str) -> None:
    if attempt.get("phase") != "synthesize":
        raise ValueError("还没有到确认产出这一步")
    text = (confirmed_output or "").strip()
    if len(_compact(text)) < 8:
        raise ValueError("确认前请保留一句你自己的产出")
    if len(text) > 2000:
        raise ValueError("阶段产出请控制在 2000 字以内")
    record = _current_record(attempt)
    record["confirmed_output"] = text
    record["messages"].append({"role": "student", "text": text})
    _enter_compare(attempt, record, blueprint)


def acknowledge_compare(attempt: dict, blueprint: TrainingBlueprint, reflection: str = "") -> None:
    if attempt.get("phase") != "compare":
        raise ValueError("还没到对照这一步")
    record = _current_record(attempt)
    note = (reflection or "").strip()
    if len(note) > 2000:
        raise ValueError("反思请控制在 2000 字以内")
    record["reflection"] = note
    if note:
        record["messages"].append({"role": "student", "text": note})
    if attempt["index"] + 1 >= len(attempt["stage_order"]):
        _enter_report(attempt, blueprint)
        return
    attempt["index"] += 1
    number = _current_number(attempt)
    record = _new_record(number)
    attempt["stages"][number] = record
    attempt["phase"] = "explore"
    if number >= 8:
        message = "前面积累的产出都在左侧。右侧是论文论证链，现在可以对照。请回答这一关的两问。"
    else:
        message = OPENING_TUTOR
    _say(attempt, record, message, blueprint)


def research_state(attempt: dict) -> dict:
    stages = []
    for number in attempt["stage_order"]:
        record = attempt["stages"].get(number)
        if not record or record.get("status") != "confirmed" or not record.get("confirmed_output"):
            continue
        stages.append({
            "stage_number": number,
            "confirmed_output": record["confirmed_output"],
            "open_questions": list(record.get("open_questions") or []),
            "comparison_mode": record.get("comparison_mode"),
            "reflection": record.get("reflection") or "",
        })
    return {
        "document_id": attempt["document_id"],
        "micro_session": attempt["micro_session"],
        "diverged": bool(attempt.get("diverged")),
        "stages": stages,
    }


def _stage_score(record: Optional[dict]) -> int:
    if not record or record.get("status") != "confirmed":
        return 0
    passed = len(record.get("passed_rubric_ids") or [])
    if passed >= 2:
        return 3
    if passed == 1:
        return 2
    return 1


def _comparison_block(attempt: dict, blueprint: TrainingBlueprint) -> Optional[dict]:
    if _locked(attempt):
        return None
    stage = _current_stage(attempt, blueprint)
    mode = "structural_skeleton" if attempt.get("diverged") else "same_topic"
    if attempt.get("phase") == "report" or _current_number(attempt) >= 8:
        numbers = list(attempt["stage_order"])
    else:
        numbers = [_current_number(attempt)]
    stages_payload = []
    stage_lookup = _stage_map(blueprint)
    for number in numbers:
        source = stage_lookup.get(number)
        if source is None:
            continue
        stages_payload.append({
            "stage_number": number,
            "title": source.title,
            "anchors": [
                {
                    "point": anchor.point,
                    "label": anchor.label,
                    "summary": anchor.summary,
                    "display_quote": anchor.display_quote,
                }
                for anchor in source.expert_anchors[:6]
            ],
        })
    if mode == "structural_skeleton":
        note = "你的方向已经独立于这篇论文。这里对照的是论证骨架，不是要你改成作者的结论。"
        guide = stage.structural_skeleton_guide
    else:
        note = "你处理的问题和这篇论文相近。对照只帮助你看结构，不按像不像打分。"
        guide = stage.same_topic_comparison_guide
    return {
        "mode": mode,
        "mode_label": "思维骨架对照" if mode == "structural_skeleton" else "同题对照",
        "note": note,
        "guide": guide,
        "stages": stages_payload,
    }


def _report(attempt: dict, blueprint: TrainingBlueprint) -> Optional[dict]:
    if attempt.get("phase") != "report":
        return None
    state = research_state(attempt)
    order = set(attempt["stage_order"])
    dimensions = []
    for name, numbers in DIMENSIONS:
        present = [number for number in numbers if number in order]
        if not present:
            continue
        values = [_stage_score(attempt["stages"].get(number)) for number in present]
        level = int(round(sum(values) / len(values)))
        level = max(0, min(3, level))
        dimensions.append({"name": name, "level": level, "label": LEVEL_LABELS[level]})
    lines = [
        "个人科研构思报告",
        f"训练切片：{attempt['micro_label']}",
        "评分只看每关逻辑安检过没过，不按和论文的相似度计分。",
        "",
    ]
    if not _locked(attempt):
        lines.append(f"论文：{blueprint.paper_id}")
        lines.append("")
    for number in attempt["stage_order"]:
        record = attempt["stages"].get(number)
        source = _stage_map(blueprint)[number]
        lines.append(source.title)
        if record and record.get("confirmed_output"):
            lines.append(record["confirmed_output"])
        else:
            lines.append("（未确认）")
        if record and record.get("open_questions"):
            lines.append("未解疑点：" + "；".join(record["open_questions"]))
        if record and record.get("comparison_mode"):
            lines.append("对照模式：" + ("思维骨架对照" if record["comparison_mode"] == "structural_skeleton" else "同题对照"))
        if record and record.get("reflection"):
            lines.append("反思：" + record["reflection"])
        lines.append("")
    lines.append("描述性评价")
    for item in dimensions:
        lines.append(f"- {item['name']}：{item['level']}/3 {item['label']}")
    return {
        "scored_by_similarity": False,
        "dimensions": dimensions,
        "text": "\n".join(lines).strip() + "\n",
        "research_state": state,
    }


def _progress(attempt: dict) -> List[dict]:
    items = []
    for index, number in enumerate(attempt["stage_order"]):
        record = attempt["stages"].get(number)
        if attempt.get("finished") and record and record.get("status") == "confirmed":
            status = "done"
        elif index == attempt["index"] and not attempt.get("finished"):
            status = "current"
        elif record and record.get("status") == "confirmed" and index < attempt["index"]:
            status = "done"
        else:
            status = "pending"
        items.append({"stage_number": number, "status": status})
    return items


def public_view(attempt: dict, blueprint: TrainingBlueprint) -> dict:
    stage = _current_stage(attempt, blueprint)
    record = _current_record(attempt)
    locked = _locked(attempt)
    prompt, used_background = render_prompt(attempt, stage)
    if locked:
        prompt = scrub_spoilers(prompt, blueprint.spoiler_blacklist)
    options = None
    if attempt.get("phase") == "choose":
        options = []
        for option in stage.options:
            text = option.text
            dimension = option.dimension
            if locked:
                text = scrub_spoilers(text, blueprint.spoiler_blacklist)
                dimension = scrub_spoilers(dimension, blueprint.spoiler_blacklist)
            options.append({"key": option.key, "dimension": dimension, "text": text})
    criteria = [item.criterion for item in stage.diagnostic_rubrics]
    if locked:
        criteria = [scrub_spoilers(item, blueprint.spoiler_blacklist) for item in criteria]
    messages = []
    for item in record.get("messages") or []:
        text = item["text"]
        if locked and item.get("role") == "tutor":
            text = scrub_spoilers(text, blueprint.spoiler_blacklist)
        messages.append({"role": item.get("role"), "text": text})
    titles = {item.stage_number: item.title for item in blueprint.stages}
    progress = _progress(attempt)
    for item in progress:
        item["title"] = titles.get(item["stage_number"], f"第 {item['stage_number']} 关")
    phase_labels = {
        "explore": "自由作答",
        "probe": "单点追问",
        "choose": "等权辨析",
        "justify": "说明理由",
        "synthesize": "确认产出",
        "compare": "专家对照",
        "report": "构思报告",
    }
    stage_note = ""
    if stage.stage_code == "validation_planning":
        stage_note = "按你自己的方案写验证路线。不需要编造已经完成的实验，也不把没做的检验说成缺陷；把还做不到的边界写出来即可。"
    return {
        "active": True,
        "paper_label": None if locked else blueprint.paper_id,
        "micro_session": attempt["micro_session"],
        "micro_label": attempt["micro_label"],
        "phase": attempt["phase"],
        "phase_label": phase_labels.get(attempt["phase"], attempt["phase"]),
        "tutor_message": scrub_spoilers(attempt.get("tutor_message") or "", blueprint.spoiler_blacklist) if locked else (attempt.get("tutor_message") or ""),
        "diverged": bool(attempt.get("diverged")),
        "can_inspiration": stage.stage_code in {"theory_selection", "mechanism_design"} and attempt.get("phase") in {"explore", "probe"},
        "stage": {
            "stage_number": stage.stage_number,
            "title": stage.title,
            "subtitle": stage.subtitle,
            "prompt": prompt,
            "prompt_note": "前面的关卡不是你亲自作答的，题干里只放了案发现场背景。" if used_background else "",
            "options_type_label": stage.options_type_label if attempt.get("phase") == "choose" else "",
            "criteria": criteria,
            "note": stage_note,
        },
        "options": options,
        "options_note": "没有标准答案，这几条学术价值相等。" if options else "",
        "draft_output": record.get("draft_output") if attempt.get("phase") == "synthesize" else "",
        "open_questions": list(record.get("open_questions") or []) if attempt.get("phase") == "synthesize" else [],
        "messages": messages,
        "progress": progress,
        "research_state": research_state(attempt),
        "comparison": _comparison_block(attempt, blueprint),
        "report": _report(attempt, blueprint),
        "document_id": blueprint.document_id,
        "source_label": _source_label(blueprint),
    }


def _source_label(blueprint: TrainingBlueprint) -> str:
    if str(blueprint.blueprint_id).startswith("bp_live_"):
        return "刚上传的 JSON"
    return "内置演示蓝图"


def inactive_view(
    paradigm_display: str,
    document_id: str = "",
    source_label: str = "",
    stage_titles: Optional[list] = None,
) -> dict:
    sessions = []
    for key, spec in MICRO_SESSIONS.items():
        sessions.append({
            "id": key,
            "label": spec["label"],
            "detail": spec["detail"],
        })
    return {
        "active": False,
        "paradigm_display": paradigm_display,
        "document_id": document_id,
        "source_label": source_label,
        "stage_titles": stage_titles or [],
        "sessions": sessions,
    }


def tutor_contains_praise(text: str) -> bool:
    return any(token in (text or "") for token in PRAISE_HITS)
