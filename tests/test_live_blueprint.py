"""Uploading a paper JSON organizes a blueprint immediately, without the Lib3 script."""

import json
import unittest
from pathlib import Path

from backend.question_bank.adapter import adapt_lis_mas_json
from backend.question_bank.live_blueprint import build_live_blueprint
from backend.answering import public_view, start_attempt

ROOT = Path(__file__).resolve().parents[1]
SPOILERS = ["Lib3", "知识熵减", "W123-CVP", "ICSP", "SAM三元组", "波普尔世界3"]


class LiveBlueprintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "data" / "lib3_sample.json").read_text(encoding="utf-8"))
        cls.doc = adapt_lis_mas_json(raw, doc_id="doc_uploaded")
        cls.blueprint = build_live_blueprint(cls.doc)

    def test_upload_shape_is_eight_stages_from_this_document(self):
        blueprint = self.blueprint
        self.assertEqual(blueprint.document_id, "doc_uploaded")
        self.assertTrue(blueprint.blueprint_id.startswith("bp_live_"))
        self.assertEqual([stage.stage_number for stage in blueprint.stages], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertIn("实时整理", blueprint.generated_by)
        self.assertNotIn("首席架构师", blueprint.stages[0].opening_prompt_template)

    def test_early_stages_do_not_repeat_demo_spoilers(self):
        blob = "\n".join(
            "\n".join([
                stage.opening_prompt_template,
                stage.subtitle,
                stage.paradigm_focus or "",
                *[option.text + option.follow_up_hook for option in stage.options],
                *[item.criterion + item.probe_prompt for item in stage.diagnostic_rubrics],
            ])
            for stage in self.blueprint.stages
            if stage.stage_number < 8
        )
        for term in SPOILERS:
            self.assertNotIn(term, blob)
        self.assertTrue(self.blueprint.spoiler_audit_passed, self.blueprint.spoiler_violations)

    def test_design_science_validation_does_not_demand_a_survey(self):
        stage = self.blueprint.stages[6]
        blob = stage.opening_prompt_template + "\n".join(option.text for option in stage.options)
        self.assertNotIn("问卷", blob)
        self.assertNotIn("p 值", blob)

    def test_answering_can_start_on_the_live_blueprint(self):
        attempt = start_attempt(self.blueprint, "breakthrough_session")
        view = public_view(attempt, self.blueprint)
        self.assertEqual(view["source_label"], "刚上传的 JSON")
        self.assertEqual(view["stage"]["stage_number"], 1)
        self.assertIsNone(view["paper_label"])
        blob = json.dumps(view, ensure_ascii=False)
        for term in SPOILERS:
            self.assertNotIn(term, blob)


if __name__ == "__main__":
    unittest.main()
