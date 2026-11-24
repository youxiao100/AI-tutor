"""Organize an uploaded paper JSON into an 8-stage blueprint without an external model.

Stage 1–7 prompts stay clear of the spoiler blacklist. Expert anchors still come from
the paper, and are only shown by the answering flow after the student confirms a stage.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Sequence

from backend.question_bank.models import (
    AdaptedDocument,
    ChoiceOption,
    DiagnosticRubricItem,
    StageBlueprint,
    TrainingBlueprint,
)
from backend.question_bank.question_builder import (
    PARADIGM_RULES,
    audit_and_scrub_spoilers,
    build_expert_anchors_for_stage,
)

_SKIP_SPOILERS = {"交叉", "筛选", "辩证", "目的地"}


def _scrub(text: str, blacklist: Sequence[str]) -> str:
    cleaned = text or ""
    terms = [term for term in blacklist if term and len(term) >= 2 and term not in _SKIP_SPOILERS]
    for term in sorted(terms, key=len, reverse=True):
        cleaned = re.sub(re.escape(term), "这一现象", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(这一现象[，、；\s]*){2,}", "这一现象", cleaned)
    return cleaned.strip()


def _clip(text: str, limit: int = 90) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip("，,；; ") + "…"


def _scene(doc: AdaptedDocument) -> str:
    pieces: List[str] = []
    pool = list(doc.crime_scene_materials) + [claim for claim in doc.claims if claim.point in {"01", "06"}]
    seen = set()
    for claim in pool:
        content = re.sub(r"\s+", "", claim.content or "")
        if len(content) < 8 or content in seen:
            continue
        seen.add(content)
        pieces.append(_clip(claim.content, 80))
        if len(pieces) >= 2:
            break
    raw = "；".join(pieces) if pieces else "标注里写下的那个还没被解决的矛盾"
    cleaned = _scrub(raw, doc.spoiler_blacklist).rstrip("。．.；; ")
    if len(re.sub(r"\s+", "", cleaned)) < 8:
        return "标注里写下的那个还没被解决的矛盾"
    return cleaned


def _paradigm_key(doc: AdaptedDocument) -> str:
    primary = doc.research_paradigm[0] if doc.research_paradigm else ""
    if primary in PARADIGM_RULES:
        return primary
    return "theoretical_conceptual"


def _option(key: str, dimension: str, text: str, hook: str) -> ChoiceOption:
    return ChoiceOption(key=key, dimension=dimension, text=text, follow_up_hook=hook)


def _rubrics(stage_number: int, pairs: Sequence[tuple]) -> List[DiagnosticRubricItem]:
    return [
        DiagnosticRubricItem(rubric_id=f"s{stage_number}_r{index}", criterion=criterion, probe_prompt=probe)
        for index, (criterion, probe) in enumerate(pairs, start=1)
    ]


def _stage5_options(paradigm: str) -> List[ChoiceOption]:
    if paradigm == "quantitative_empirical":
        rows = [
            ("A", "变量操作化", "先把你要比较的概念变成可以观察、可以记录的变量。", "这些变量分别怎么观察？哪一个最容易量错？"),
            ("B", "抽样与数据", "先说明数据从哪来、谁被纳入、谁被排除。", "这套抽样漏掉的人，会不会正好是你的结论最站不住的地方？"),
            ("C", "因果识别", "先讲你怎么区分「相关」和「你的方案确实造成了变化」。", "如果有一个替代解释，你的设计怎么把它挡住？"),
        ]
    elif paradigm == "qualitative":
        rows = [
            ("A", "案例选择", "说明为什么是这些案例，而不是更方便的那些。", "这些案例覆盖了矛盾的哪一面，又故意不覆盖哪一面？"),
            ("B", "访谈与材料", "写出你要问的核心问题，以及除了访谈还看什么材料。", "受访者顺着你的问法回答时，你会漏掉什么？"),
            ("C", "编码策略", "说明你怎么把材料收成可以复核的判断。", "另一个人用你的规则再编一次，最可能在哪一步分歧？"),
        ]
    elif paradigm == "bibliometric_review":
        rows = [
            ("A", "数据库选型", "先选定要检索的库，并说明为什么不是只搜一个库。", "只留在这个库里，会系统性漏掉哪一类研究？"),
            ("B", "检索式", "写出检索词怎么组，才对得上你的问题和缺口。", "哪一个词放宽或收紧，结果会明显变样？"),
            ("C", "纳入排除", "写出什么文献进来、什么文献退出，以及退出的理由。", "被你排除的那一类文献里，有没有可能藏着反例？"),
        ]
    elif paradigm == "design_science":
        rows = [
            ("A", "表示结构", "先定义系统里的一条记录长什么样，以及它如何指回原始材料。", "输入一条新材料时，它怎样变成你定义的记录？"),
            ("B", "运行机制", "说明系统靠什么步骤自己往下走，而不是只等别人检索。", "这一步的输入是什么，处理是什么，输出又是什么？"),
            ("C", "度量或判据", "给一个能判断「这步做的是正功还是负功」的标准。", "这个标准在什么情况下会把有价值的例外误删掉？"),
        ]
    else:
        rows = [
            ("A", "概念边界", "先划清你的核心概念包含什么、不包含什么。", "边界外面最容易被你误算进来的是哪一种情况？"),
            ("B", "命题体系", "把主张收成少数几句可以反驳的命题。", "哪一句命题一旦被反例击中，整套主张就会松动？"),
            ("C", "框架整合", "说明不同理论各自解释了矛盾的哪一部分。", "两套理论在哪一个判断上会互相打架？"),
        ]
    rows.append(("E", "自定义部件", "其他（自己写出这一关的核心部件）。", "请写明输入、处理方式和输出。"))
    return [_option(*row) for row in rows]


def _stage7_options(paradigm: str) -> List[ChoiceOption]:
    if paradigm == "quantitative_empirical":
        rows = [
            ("A", "检验选择", "说明你准备用什么检验来判断结果是否站得住。", "如果主检验不显著，你的备用解释是什么？"),
            ("B", "信效度", "说明测量是否稳定，以及它是不是量到了你声称的概念。", "哪个变量最可能看起来有效，其实量的是别的东西？"),
            ("C", "稳健性", "换一种模型、样本或变量定义后，结论还在不在。", "你最担心哪一种替换会把结论翻掉？"),
        ]
    elif paradigm == "qualitative":
        rows = [
            ("A", "饱和", "说明你怎么判断再收集材料已经增加不了新判断。", "什么新材料出现时，你会承认还没饱和？"),
            ("B", "三角互证", "用不同来源核对同一个判断。", "两个来源冲突时，你信哪一个，为什么？"),
            ("C", "反思性", "写出你自己的位置可能怎样塑造了这些判断。", "哪一条结论最可能只是你的问法带出来的？"),
        ]
    elif paradigm == "bibliometric_review":
        rows = [
            ("A", "偏倚审查", "检查检索和发表过程会不会只留下某一类结果。", "如果负面结果更难发表，你的结论会偏到哪一边？"),
            ("B", "跨库敏感性", "换一个库或一套检索词，再看结论变不变。", "哪一个库最可能搜出和你主结论相反的文献？"),
            ("C", "证据合成", "说明你怎么把多篇研究收成一个判断，而不是逐篇复述。", "研究之间不能直接比较时，你还合成什么？"),
        ]
    elif paradigm == "design_science":
        rows = [
            ("A", "规模走查", "先走一条小样本或原型路径，看结构和机制是否接得上。", "原型上最先断掉的会是记录结构，还是运行步骤？"),
            ("B", "分阶段试点", "先在一个小范围里试用，再决定要不要扩大。", "第一批使用者为什么适合，又不至于把问题简化没了？"),
            ("C", "边界坦白", "直接写出这套方案现在做不到的知识类型或开销。", "哪一种材料或场景是你现在明确不处理的？"),
        ]
    else:
        rows = [
            ("A", "逻辑自洽", "检查命题之间会不会互相否定。", "哪两句主张放在一起会冲突？"),
            ("B", "思想实验", "用一个极端情境试你的命题还成不成立。", "这个情境里，命题首先在哪一句失守？"),
            ("C", "适用边界", "写出这套理论不打算解释的现象。", "你划出的边界，是不是刚好把反例推到了外面？"),
        ]
    rows.append(("E", "自定义验证", "其他（自己写出验证路线，并坦白一个局限）。", "验证要对应你上一关的设计，并写出一个还做不到的地方。"))
    return [_option(*row) for row in rows]


def _scrub_student_facing(stage: StageBlueprint, blacklist: Sequence[str]) -> None:
    if stage.stage_number >= 8:
        return
    stage.opening_prompt_template = _scrub(stage.opening_prompt_template, blacklist)
    stage.subtitle = _scrub(stage.subtitle, blacklist)
    if stage.paradigm_focus:
        stage.paradigm_focus = _scrub(stage.paradigm_focus, blacklist)
    for option in stage.options:
        option.dimension = _scrub(option.dimension, blacklist)
        option.text = _scrub(option.text, blacklist)
        option.follow_up_hook = _scrub(option.follow_up_hook, blacklist)
    for rubric in stage.diagnostic_rubrics:
        rubric.criterion = _scrub(rubric.criterion, blacklist)
        rubric.probe_prompt = _scrub(rubric.probe_prompt, blacklist)


def build_live_blueprint(doc: AdaptedDocument) -> TrainingBlueprint:
    """Build a fresh 8-stage blueprint from this document's own scene, paradigm, and anchors."""
    paradigm = _paradigm_key(doc)
    rule = PARADIGM_RULES[paradigm]
    scene = _scene(doc)
    paper_name = doc.paper_id or doc.document_id
    stages = [
        StageBlueprint(
            stage_number=1,
            stage_code="problem_discovery",
            title="第 1 关：发现问题",
            subtitle="先锁定一个具体对象，并解释矛盾为什么存在",
            paradigm_focus=rule["stage5_focus"],
            forbidden_demands=["不设唯一标准答案", "不要求复述论文里的专有名词"],
            opening_prompt_template=(
                f"材料里的情境是：{scene}。"
                "如果让你立项研究，真正出问题的环节在哪里？"
                "请指出具体对象，并解释为什么表面上看起来已经改善，困难却还在。"
            ),
            options_type_label="收敛选择题（四维等权切面）",
            options=[
                _option("A", "资源入口层", "问题主要在入口分散，材料和渠道没有接在一起。", "假如明天入口合成一个，困难会减轻，还是材料变多之后更难判断？"),
                _option("B", "知识表示层", "问题主要在材料仍是整块堆着，里面的判断没有被提取和关联。", "整块材料在哪一个具体环节卡住了使用者？"),
                _option("C", "认知负荷层", "问题主要在供给超过人的阅读和判断带宽。", "供给变快以后，哪一种负担被转嫁给了使用者？"),
                _option("D", "信任治理层", "问题主要在结果难以核对，使用者不敢直接拿去用。", "为了核对一条结果，使用者不得不退回原文。这缺的是什么？"),
                _option("E", "自定义切面", "其他（自己指出问题环节）。", "请写出你锁定的对象，以及矛盾怎样产生。"),
            ],
            diagnostic_rubrics=_rubrics(1, [
                ("是否锁定了具体研究对象，而不是只说信息很多或技术很强？", "你准备把研究对象落在哪一个具体环节：入口、表示、判断负担，还是核对过程？"),
                ("是否解释了「看起来变快或变多了，为什么还是难用」？", "改善已经发生了，为什么困难还在？负担或瓶颈从什么转到了什么？"),
            ]),
            expert_anchors=build_expert_anchors_for_stage(doc, 1),
            same_topic_comparison_guide="对照本关论文锚点里实际写出的问题，不按措辞像不像打分。",
            structural_skeleton_guide="如果你的入口或信任判断已经离开论文的侧重点，对照的是它怎样把现象收成一个可研究的矛盾，不是要你改口。",
        ),
        StageBlueprint(
            stage_number=2,
            stage_code="gap_identification",
            title="第 2 关：识别缺口",
            subtitle="指出一个现有做法，并说明它的结构局限",
            paradigm_focus="找出现有方案差的那一口气，而不是笼统说不够好",
            forbidden_demands=["不接受只说「不够智能」或「准确率还不高」"],
            opening_prompt_template=(
                "你已经锁定的问题是：{{第1关学生问题}}。"
                "评审会说：现有做法已经在处理这件事。你要指出一个具体的现有方案，并说明它在结构上差在哪里。"
            ),
            options_type_label="辨析选择题",
            options=[
                _option("A", "语境断裂", "现有抽取或摘要把内容切碎了，脱离了原来的论证，也难以溯源。", "孤立的一条结论要能被使用，还缺什么语境？"),
                _option("B", "静态僵化", "现有库依赖人工维护，不能随着新的矛盾一起更新。", "新材料不断出现时，静态的一套记录为什么会过时？"),
                _option("C", "激励缺失", "贡献发生在很细的粒度上，回报却仍停在整篇材料。", "如果细粒度贡献得不到回报，供给会怎样变少？"),
                _option("D", "成本失控", "整理或计算的成本已经大于它省下的判断成本。", "成本超过收益时，这套干预还算不算在解决问题？"),
                _option("E", "自定义靶子", "其他（自己点名一个现有方案和它的局限）。", "请点名靶子，并说明为什么只靠加大规模解决不了。"),
            ],
            diagnostic_rubrics=_rubrics(2, [
                ("是否点名了一个现有方案作为靶子？", "你拿哪一种现有做法当靶子？不要只说现有技术不够好。"),
                ("是否说出了结构上的局限，而不是只说不够智能或准确率不高？", "如果只是规模不够，加大投入就能解决。你说的局限为什么加规模也还在？"),
            ]),
            expert_anchors=build_expert_anchors_for_stage(doc, 2),
            same_topic_comparison_guide="对照论文对既有工作的批评，看它点名了什么、局限落在哪一层。",
            structural_skeleton_guide="靶子可以不同。对照论文如何把局限拆成几个不能互相替代的层面。",
        ),
        StageBlueprint(
            stage_number=3,
            stage_code="theory_selection",
            title="第 3 关：选择理论",
            subtitle="借一个理论透镜，并把它映射回前面的矛盾",
            paradigm_focus="理论要用来解释已经锁定的问题，而不是贴一个名称",
            forbidden_demands=["不接受只抛出一个理论名称"],
            opening_prompt_template=(
                "为了处理 {{第2关研究缺口}}，不能只继续堆做法。"
                "你会借哪一种理论或跨学科透镜来重新定义这个问题？它具体解释了哪一个矛盾？"
            ),
            options_type_label="灵感卡片",
            options=[
                _option("A", "秩序与控制", "把混乱看成无序增加，把系统的任务看成维持可使用的秩序。", "在你的问题里，什么在增加无序，什么动作在恢复秩序？"),
                _option("B", "认识论", "把知识看成可以离开载体被检验的对象，而不只是库存。", "哪一种检验，是现在的载体做不到的？"),
                _option("C", "演化和系统", "把集合看成会变化、会保留、也会淘汰的系统。", "新的矛盾出现时，系统靠什么保留或淘汰旧判断？"),
                _option("D", "成本与行为", "从获取成本、判断成本和收益的权衡解释使用者为什么卡住。", "哪一项成本在条件变好之后反而变大了？"),
                _option("E", "自定义透镜", "其他（自己提出一个理论，并说明映射）。", "这个理论对应你前面哪一个矛盾，而不是只换了一层说法？"),
            ],
            diagnostic_rubrics=_rubrics(3, [
                ("是否说出了一个理论、透镜或框架？", "你借用的理论是什么？请给一个名称或一句明确的透镜。"),
                ("是否说明这个理论怎样解释前面的矛盾？", "因为这个理论，前面的矛盾应该被理解成什么？请写出因为和所以。"),
            ]),
            expert_anchors=build_expert_anchors_for_stage(doc, 3),
            same_topic_comparison_guide="对照论文使用的理论透镜，看它解释的是哪一个矛盾。",
            structural_skeleton_guide="透镜可以不同。对照论文如何把一个现象收成理论语言，而不是背它的理论名称。",
        ),
        StageBlueprint(
            stage_number=4,
            stage_code="proposition_formation",
            title="第 4 关：形成命题",
            subtitle="写成一两句可以被反驳的命题",
            paradigm_focus="命题要能被反例击中，而不是永远正确的口号",
            forbidden_demands=["不接受「应该更好地服务」这类无法反驳的空话"],
            opening_prompt_template=(
                "结合你选定的 {{第3关理论视角}}，把主张收成一两句命题。"
                "它要能被反驳：在什么条件下成立，不满足时又为什么不成立。"
            ),
            options_type_label="命题句式",
            options=[
                _option("A", "对象转向", "主张管理或研究对象必须从一种单元转到另一种单元。", "旧单元和新单元各是什么？转不过去时，命题哪里失败？"),
                _option("B", "判据型", "主张只有当收益大于代价时，这套做法才成立。", "代价超过收益时，你的命题要求系统停止做什么？"),
                _option("C", "角色转向", "主张系统从被动等待查询，转到能主动发现矛盾或提出下一步。", "如果系统仍然只等查询，你的命题还成立吗？"),
                _option("E", "自定义命题", "其他（自己写命题）。", "请写成「只有当……才……」或「应当从……转向……」。"),
            ],
            diagnostic_rubrics=_rubrics(4, [
                ("命题是否写成可反驳的判断，而不是口号？", "请改成一句可以找到反例的话：只有当什么条件成立，主张才成立。"),
                ("命题是否接得上你选的理论？", "这句命题用到了理论里的哪一个判断？如果去掉理论，这句话还剩下什么？"),
            ]),
            expert_anchors=build_expert_anchors_for_stage(doc, 4),
            same_topic_comparison_guide="对照论文的核心命题，看它承诺了什么、又把什么排除在外。",
            structural_skeleton_guide="命题内容可以不同。对照论文怎样避免写成无法反驳的口号。",
        ),
        StageBlueprint(
            stage_number=5,
            stage_code="mechanism_design",
            title="第 5 关：设计方案",
            subtitle=rule["stage5_focus"],
            paradigm_focus=rule["stage5_focus"],
            forbidden_demands=[f"这一关不要求：{item}" for item in rule["forbidden"]],
            opening_prompt_template=(
                "把命题 {{第4关研究命题}} 做成一套能往下做的设计。"
                f"按当前范式，这一关重点是：{rule['stage5_focus']}。"
                "请写出至少两个部件，并说明输入、处理和输出。"
            ),
            options_type_label="方案支架",
            options=_stage5_options(paradigm),
            diagnostic_rubrics=_rubrics(5, [
                ("是否写出了表示、结构或材料单位？", "这一关的核心对象在系统或研究设计里具体长什么样？"),
                ("是否说出了输入、处理和输出，或对应的运行步骤？", "从一条新的输入进来，到你声称的输出，中间哪一步不能省？"),
            ]),
            expert_anchors=build_expert_anchors_for_stage(doc, 5),
            same_topic_comparison_guide="对照论文在这一关对应点位上写出的设计，不把未报告的实验说成缺陷。",
            structural_skeleton_guide="部件可以不同。对照论文是否把设计写成输入、变化和输出，而不是只重复命题。",
        ),
        StageBlueprint(
            stage_number=6,
            stage_code="credibility_and_boundaries",
            title="第 6 关：可信与边界",
            subtitle="指出方案自己会放大的风险，并给出机制上的刹车",
            paradigm_focus="刹车要落在方案内部，而不是只呼吁加强管理",
            forbidden_demands=["不接受只说加强监管、提高素养或加强伦理规范"],
            opening_prompt_template=(
                "假设你的 {{第5关设计方案}} 已经按设计跑起来了。"
                "它最可能放大哪一种风险？你准备在方案里面加什么刹车，而不是只呼吁外部加强管理？"
            ),
            options_type_label="风险与刹车",
            options=[
                _option("A", "错误放大", "自动步骤会把一个不可靠的判断越推越远。", "你在哪一步核对原始材料或请人终审？"),
                _option("B", "单一标准", "筛选只留下主流或高分结果，例外被当成噪声去掉。", "你给例外留了什么分区、阈值或扰动？"),
                _option("C", "权属与回报", "细粒度成果被拿走重组，原贡献者得不到回报。", "溯源和回报具体记在哪一层记录上？"),
                _option("D", "使用差距", "只有一部分人能理解或用得起这套方案。", "不同起点的使用者，界面或步骤上有什么不同？"),
                _option("E", "自定义风险", "其他（自己指出风险和刹车）。", "刹车必须是方案里的一个步骤、规则或阈值。"),
            ],
            diagnostic_rubrics=_rubrics(6, [
                ("是否指出一个会被自己的方案放大的风险？", "方案越成功，哪一种副作用会一起变大？"),
                ("是否给出方案内部的刹车，而不是只说加强监管？", "这个刹车是核对、分区、阈值、溯源，还是别的具体步骤？"),
            ]),
            expert_anchors=build_expert_anchors_for_stage(doc, 6),
            same_topic_comparison_guide="对照论文写出的风险和对应机制。没写到的风险不要说成论文的缺陷。",
            structural_skeleton_guide="风险类型可以不同。对照论文是否把刹车写进了方案，而不是留在口号里。",
        ),
        StageBlueprint(
            stage_number=7,
            stage_code="validation_planning",
            title="第 7 关：验证与边界",
            subtitle=rule["stage7_focus"],
            paradigm_focus=rule["stage7_focus"],
            forbidden_demands=[f"这一关不要求：{item}" for item in rule["forbidden"]],
            opening_prompt_template=(
                "你的方案现在还停在纸面上。针对它，你会怎样验证它确实有效，并且做得到？"
                f"按当前范式，重点是：{rule['stage7_focus']}。"
                "请同时坦白一个现在还做不到的局限。"
            ),
            options_type_label="验证路径",
            options=_stage7_options(paradigm),
            diagnostic_rubrics=_rubrics(7, [
                ("验证路线是否对得上前面的设计，而不是另起一套？", "你准备分哪几步检查前面那个设计，而不是泛泛说以后再研究？"),
                ("是否写出至少一个现在还做不到的局限或边界？", "哪一种材料、规模或情形是这套方案目前明确处理不了的？"),
            ]),
            expert_anchors=build_expert_anchors_for_stage(doc, 7),
            same_topic_comparison_guide="对照论文自己承认的边界和验证安排。未报告的检验不要编造成已经做过。",
            structural_skeleton_guide="验证手段可以不同。对照论文是否把做不到的部分写成后续问题，而不是瞒过去。",
        ),
        StageBlueprint(
            stage_number=8,
            stage_code="reflection_and_innovation",
            title="第 8 关：对照与修订",
            subtitle="对照你的产出和论文论证，决定借什么、改什么",
            paradigm_focus="对照不是打分，也不要求改成论文的结论",
            forbidden_demands=["不以和论文的相似度为分数"],
            opening_prompt_template=(
                f"下面可以对照《{paper_name}》的论证链。请回答两件事："
                "你的方案在哪一处更贴近落地，或补上了论文没写的盲区？"
                "如果要借论文里的一个机制来改你的方案，你借哪一个，准备怎样改？也可以说明你为什么不借。"
            ),
            options_type_label="修订方向",
            options=[
                _option("A", "借用表示", "借论文对材料或知识单位的定义，补我自己的结构。", "你保留它的哪一部分，又会按你的场景删掉什么？"),
                _option("B", "借用运行", "借论文让系统继续运转的步骤或判据。", "哪一步你现在就能做，哪一步还要改？"),
                _option("C", "借用治理", "借论文处理风险、权属或使用差距的办法。", "把它放进你的方案，第一步最小的版本是什么？"),
                _option("D", "保持独立", "我的切口更窄，也更容易先做出来，所以不改成论文的总方案。", "你的方案比论文更容易先落地的那一刀在哪里？"),
                _option("E", "综合修订", "其他（自己写修订）。", "写出你保留的、借来的，以及你拒绝的。"),
            ],
            diagnostic_rubrics=_rubrics(8, [
                ("是否说出自己方案相对论文的一个不同点或盲区？", "不要先否定自己。你的方案哪一处是论文没有展开的？"),
                ("是否决定借一个机制，或有理由地不借？", "你借哪一个结构或步骤来改自己的方案？如果决定不借，理由是什么？"),
            ]),
            expert_anchors=build_expert_anchors_for_stage(doc, 8),
            same_topic_comparison_guide="把你确认过的各关和论文锚点并排看。相同不代表更好，不同也不代表错误。",
            structural_skeleton_guide="对照的是痛点、缺口、理论、命题、机制、边界、验证是否各自接得上。",
        ),
    ]
    for stage in stages:
        _scrub_student_facing(stage, doc.spoiler_blacklist)
    violations = audit_and_scrub_spoilers(stages, doc.spoiler_blacklist[:15])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return TrainingBlueprint(
        blueprint_id=f"bp_live_{doc.document_id}",
        document_id=doc.document_id,
        paper_id=doc.paper_id,
        generated_by="本地实时整理（按这份 JSON 的情境、范式和点位生成，未调用外部模型）",
        generated_at=now,
        research_paradigm=doc.research_paradigm,
        paradigm_adaptation_note=(
            f"按上传 JSON 识别为{doc.paradigm_display}。"
            f"第 5 关侧重：{rule['stage5_focus']}。"
            f"第 7 关侧重：{rule['stage7_focus']}。"
            f"不要求：{'、'.join(rule['forbidden'])}。"
        ),
        spoiler_blacklist=list(doc.spoiler_blacklist),
        stages=stages,
        spoiler_audit_passed=len(violations) == 0,
        spoiler_violations=violations,
    )
