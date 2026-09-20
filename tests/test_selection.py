"""Which passages answer which question. Regression tests for the "everything returns section 7.1" bug.

Run from the repo root:  python -m unittest tests.test_selection -v   (no network, no API key)

Uses the real label files. Every question below is one a judge might plausibly type.
"""
import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant.merge import find_gaps, find_overlaps
from brand_agent.selector import keyword_select, select

ROOT = Path(__file__).resolve().parent.parent


def sections(drug: str) -> list[dict]:
    return json.loads((ROOT / "labels" / f"{drug}.json").read_text(encoding="utf-8"))["sections"]


def pick(drug: str, question: str) -> list[str]:
    result = keyword_select(question, sections(drug), drug)
    return [] if result.refuse else result.sections


class SimvastatinQuestions(unittest.TestCase):
    def test_side_effects_do_not_return_the_interactions_passage(self):
        got = pick("simvastatin", "What are the side effects of simvastatin?")
        self.assertIn("6.1", got)
        self.assertNotIn("7.1", got)

    def test_pregnancy(self):
        got = pick("simvastatin", "Is simvastatin safe in pregnancy?")
        self.assertEqual(got[0], "8.1")
        self.assertNotIn("7.1", got)

    def test_general_question_gets_the_indications(self):
        self.assertEqual(pick("simvastatin", "Tell me about simvastatin"), ["1"])
        self.assertEqual(pick("simvastatin", "simvastatin"), ["1"])

    def test_the_drug_name_alone_never_matches_a_topic(self):
        self.assertEqual(pick("simvastatin", "Is simvastatin ok?"), [])

    def test_uncovered_topics_are_refused_not_stretched(self):
        self.assertEqual(pick("simvastatin", "Can I take simvastatin with grapefruit juice?"), [])
        self.assertEqual(pick("simvastatin", "What is the best statin?"), [])

    def test_a_question_about_the_other_drug_is_refused(self):
        self.assertEqual(pick("simvastatin", "Tell me about clarithromycin"), [])

    def test_topics(self):
        self.assertIn("12.1", pick("simvastatin", "How does simvastatin work?"))
        self.assertIn("10", pick("simvastatin", "What happens in an overdose?"))
        self.assertIn("8.6", pick("simvastatin", "Do I adjust simvastatin for kidney disease?"))
        self.assertIn("8.7", pick("simvastatin", "Any liver problems with simvastatin?"))
        self.assertIn("8.4", pick("simvastatin", "Can children take simvastatin?"))
        self.assertIn("5.1", pick("simvastatin", "Is there a risk of myopathy?"))

    def test_the_interaction_question_still_works(self):
        got = pick("simvastatin", "Can simvastatin be taken with clarithromycin?")
        self.assertIn("7.1", got)
        self.assertIn("4", got)

    def test_at_most_four_passages_best_first(self):
        self.assertLessEqual(len(pick("simvastatin", "dose")), 4)


class ClarithromycinQuestions(unittest.TestCase):
    def test_topics(self):
        self.assertIn("6.1", pick("clarithromycin", "What are the side effects?"))
        self.assertIn("5.2", pick("clarithromycin", "Is there a QT prolongation risk?"))
        self.assertIn("8.4", pick("clarithromycin", "Is it okay for children?"))
        self.assertIn("8.1", pick("clarithromycin", "Can it be used during pregnancy?"))
        self.assertIn("2.6", pick("clarithromycin", "How do I dose it in renal impairment?"))

    def test_general_question(self):
        self.assertEqual(pick("clarithromycin", "Tell me about clarithromycin"), ["1.3"])


class SelectionMode(unittest.TestCase):
    def test_without_an_api_key_it_does_not_try_a_model_call(self):
        with patch.dict(os.environ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            selection, mode = asyncio.run(select("side effects?", None, "simvastatin", sections("simvastatin")))
        self.assertEqual(mode, "keyword-fallback (no ANTHROPIC_API_KEY)")
        self.assertIn("6.1", selection.sections)


class MergeKnowsTheNewTopics(unittest.TestCase):
    def source(self, brand, tags):
        return {"agent": brand, "brand": brand, "drug": brand,
                "answers": [{"section": "1", "text": "t", "labelVersion": "v", "tags": tags}]}

    def test_a_side_effects_question_is_a_gap_when_nobody_has_the_passage(self):
        sources = [self.source("A", ["dosing"])]
        gaps = find_gaps("What are the side effects?", sources, sources)
        self.assertEqual([g["topic"] for g in gaps], ["adverse-reactions"])

    def test_and_not_a_gap_when_someone_does(self):
        sources = [self.source("A", ["adverse-reactions"])]
        self.assertEqual(find_gaps("What are the side effects?", sources, sources), [])

    def test_broad_safety_tags_do_not_make_a_meaningless_overlap(self):
        two = [self.source("A", ["warnings", "adverse-reactions"]), self.source("B", ["warnings", "adverse-reactions"])]
        self.assertEqual(find_overlaps(two), [])

    def test_specific_topics_still_overlap(self):
        two = [self.source("A", ["pregnancy"]), self.source("B", ["pregnancy"])]
        self.assertEqual([o["tag"] for o in find_overlaps(two)], ["pregnancy"])


if __name__ == "__main__":
    unittest.main()
