"""Answering flow against the built-in Lib3 blueprint. No network, no similarity score."""

import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "answering-test-pass")

from backend.adapter import adapt_lis_mas_json
from backend.answering import (
    acknowledge_compare,
    choose_option,
    confirm_output,
    neutralize_hook,
    public_view,
    render_prompt,
    request_inspiration,
    research_state,
    scrub_spoilers,
    start_attempt,
    submit_text,
    tutor_contains_praise,
)
from backend.golden_blueprint import build_golden_lib3_blueprint

ROOT = Path(__file__).resolve().parents[1]
SPOILERS = ["Lib3", "知识熵减", "W123-CVP", "ICSP", "SAM三元组", "波普尔世界3"]
GOOD_STAGE_1 = (
    "真正卡住的不是检索入口，而是文献仍以整篇为管理单元，核心知识没有被提取出来。"
    "检索变快以后，筛选和判断的负担反而转嫁给了读者。"
)
GOOD_STAGE_2 = (
    "我拿知识图谱和细粒度抽取当靶子。它们把知识从原文论证脉络里切碎，学者无法溯源，"
    "这是结构失真，不是单纯不够智能。"
)


class AnsweringFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "data" / "lib3_sample.json").read_text(encoding="utf-8"))
        cls.doc = adapt_lis_mas_json(raw, doc_id="doc_0001")
        cls.blueprint = build_golden_lib3_blueprint(cls.doc)

    def test_two_vague_rounds_open_equal_choices_without_praise(self):
        attempt = start_attempt(self.blueprint, "breakthrough_session")
        submit_text(attempt, self.blueprint, "缺乏统一管理")
        first = public_view(attempt, self.blueprint)
        self.assertEqual(first["phase"], "probe")
        self.assertIsNone(first["options"])
        self.assertFalse(tutor_contains_praise(first["tutor_message"]))

        submit_text(attempt, self.blueprint, "缺乏统一管理")
        second = public_view(attempt, self.blueprint)
        self.assertEqual(second["phase"], "choose")
        self.assertEqual([item["key"] for item in second["options"]], ["A", "B", "C", "D", "E"])
        self.assertIn("没有标准答案", second["tutor_message"])
        self.assertFalse(tutor_contains_praise(second["tutor_message"]))
        self.assertTrue(all("is_correct" not in item for item in second["options"]))

    def test_spoilers_stay_hidden_until_compare(self):
        attempt = start_attempt(self.blueprint, "breakthrough_session")
        view = public_view(attempt, self.blueprint)
        self.assertIsNone(view["comparison"])
        self.assertIsNone(view["paper_label"])
        blob = json.dumps(view, ensure_ascii=False)
        for term in SPOILERS:
            self.assertNotIn(term, blob)
        submit_text(attempt, self.blueprint, "缺乏统一管理")
        probed = json.dumps(public_view(attempt, self.blueprint), ensure_ascii=False)
        for term in SPOILERS:
            self.assertNotIn(term, probed)

    def test_option_a_uses_structural_comparison(self):
        attempt = start_attempt(self.blueprint, "breakthrough_session")
        submit_text(attempt, self.blueprint, "缺乏统一管理")
        submit_text(attempt, self.blueprint, "缺乏统一管理")
        choose_option(attempt, self.blueprint, "A")
        hook = public_view(attempt, self.blueprint)["tutor_message"]
        self.assertFalse(tutor_contains_praise(hook))
        submit_text(
            attempt,
            self.blueprint,
            "如果只做统一入口，检索更快，文献更多，判断负担会转嫁给读者，所以入口不是根上的矛盾。",
        )
        self.assertEqual(public_view(attempt, self.blueprint)["phase"], "synthesize")
        self.assertEqual(research_state(attempt)["stages"], [])
        confirm_output(attempt, self.blueprint, "我先把问题放在统一检索入口，但这还解释不了为什么更快反而更焦虑。")
        view = public_view(attempt, self.blueprint)
        self.assertEqual(view["phase"], "compare")
        self.assertEqual(view["comparison"]["mode"], "structural_skeleton")
        self.assertIn("独立", view["comparison"]["note"])
        self.assertEqual(len(research_state(attempt)["stages"]), 1)

    def test_specific_answer_confirms_and_fills_the_next_prompt(self):
        attempt = start_attempt(self.blueprint, "breakthrough_session")
        submit_text(attempt, self.blueprint, GOOD_STAGE_1)
        view = public_view(attempt, self.blueprint)
        self.assertEqual(view["phase"], "synthesize")
        self.assertEqual(research_state(attempt)["stages"], [])
        confirm_output(attempt, self.blueprint, GOOD_STAGE_1)
        self.assertEqual(public_view(attempt, self.blueprint)["comparison"]["mode"], "same_topic")
        acknowledge_compare(attempt, self.blueprint, "先保持这个判断。")
        nxt = public_view(attempt, self.blueprint)
        self.assertEqual(nxt["stage"]["stage_number"], 2)
        self.assertIn(GOOD_STAGE_1, nxt["stage"]["prompt"])
        self.assertEqual(nxt["stage"]["prompt_note"], "")
        blob = json.dumps(nxt, ensure_ascii=False)
        for term in SPOILERS:
            self.assertNotIn(term, blob)

    def test_breakthrough_report_is_not_a_similarity_score(self):
        attempt = start_attempt(self.blueprint, "breakthrough_session")
        submit_text(attempt, self.blueprint, GOOD_STAGE_1)
        confirm_output(attempt, self.blueprint, GOOD_STAGE_1)
        acknowledge_compare(attempt, self.blueprint, "")
        submit_text(attempt, self.blueprint, GOOD_STAGE_2)
        confirm_output(attempt, self.blueprint, GOOD_STAGE_2)
        acknowledge_compare(attempt, self.blueprint, "缺口就停在语境和溯源。")
        view = public_view(attempt, self.blueprint)
        self.assertEqual(view["phase"], "report")
        self.assertFalse(view["report"]["scored_by_similarity"])
        names = [item["name"] for item in view["report"]["dimensions"]]
        self.assertIn("问题清晰性", names)
        self.assertIn("逻辑解释力", names)
        self.assertNotIn("方案可行性", names)
        clarity = next(item for item in view["report"]["dimensions"] if item["name"] == "问题清晰性")
        self.assertEqual(clarity["level"], 3)
        self.assertIn(GOOD_STAGE_1, view["report"]["text"])
        self.assertIn("不按和论文的相似度", view["report"]["text"])

    def test_architecture_slice_uses_background_and_inspiration_cards(self):
        attempt = start_attempt(self.blueprint, "architecture_session")
        view = public_view(attempt, self.blueprint)
        self.assertEqual(view["stage"]["stage_number"], 3)
        self.assertIn("案发现场", view["stage"]["prompt_note"])
        self.assertIn("不是你的结论", view["stage"]["prompt"])
        blob = json.dumps(view, ensure_ascii=False)
        for term in SPOILERS:
            self.assertNotIn(term, blob)
        request_inspiration(attempt, self.blueprint)
        cards = public_view(attempt, self.blueprint)
        self.assertEqual(cards["phase"], "choose")
        self.assertGreaterEqual(len(cards["options"]), 4)
        card_blob = json.dumps(cards, ensure_ascii=False)
        for term in SPOILERS:
            self.assertNotIn(term, card_blob)

    def test_stage7_does_not_demand_a_survey_or_p_value(self):
        stage = next(item for item in self.blueprint.stages if item.stage_number == 7)
        attempt = {
            "index": 1,
            "finished": False,
            "stage_order": [6, 7],
            "stages": {},
        }
        prompt, _used = render_prompt(attempt, stage)
        blob = "\n".join([
            prompt,
            *[item.probe_prompt for item in stage.diagnostic_rubrics],
            *[item.text for item in stage.options],
            "按你自己的方案写验证路线。不需要编造已经完成的实验，也不把没做的检验说成缺陷；把还做不到的边界写出来即可。",
        ])
        self.assertNotIn("问卷", blob)
        self.assertNotIn("p 值", blob)
        self.assertNotIn("显著性", blob)

    def test_hook_praise_is_removed(self):
        cleaned = neutralize_hook("很好！如果你把协议引进来，第一步最小原型做什么？")
        self.assertNotIn("很好", cleaned)
        self.assertIn("第一步", cleaned)
        cleaned = neutralize_hook("非常务实的工程验证！当知识单元扩大以后，哪一个环节先碰到算力瓶颈？")
        self.assertNotIn("非常务实", cleaned)
        self.assertIn("算力", cleaned)
        self.assertEqual(scrub_spoilers("这里出现 Lib3 和知识熵减。", self.blueprint.spoiler_blacklist).count("〔对照后再看〕"), 2)


if __name__ == "__main__":
    unittest.main()
