"""The reading aids only describe the label's text; they never change it.

Run from the repo root:  python -m unittest tests.test_readable -v
No network: labels are read from labels/*.json and the agent call is stubbed.
"""
import copy
import json
import unittest
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from assistant.readable import add_reading_aids, annotate, question_terms

ROOT = Path(__file__).resolve().parents[1]
LABELS = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "labels").glob("*.json"))}
QUESTIONS = [
    "Can simvastatin be taken with clarithromycin?", "What is the max dose of simvastatin", "Is it safe in pregnancy?",
    "side effects", "grapefruit juice", "kidney problems and clarithromycin", "Tell me about simvastatin",
    "how is it absorbed", "overdose", "", "1234 !!! ???",
]


def passages():
    for drug, label in LABELS.items():
        for p in label["sections"]:
            yield drug, p


def piece(text, span):
    return text[span[0]:span[1]]


class OffsetsOnlyDescribeTheOriginal(unittest.TestCase):
    def test_every_passage_every_question(self):
        seen = 0
        for drug, p in passages():
            for q in QUESTIONS:
                text = p["text"]
                v = annotate(text, p["section"], p["title"], q, [drug], [])
                where = f"{drug} {p['section']} q={q!r}"
                for name in ("heads", "refs", "hits"):
                    for s, e in v[name]:
                        self.assertTrue(0 <= s < e <= len(text), f"{name} out of range: {where}")
                if v["key"]:
                    s, e = v["key"]
                    self.assertTrue(0 <= s < e <= len(text), where)
                    self.assertEqual(piece(text, v["key"]), piece(text, v["key"]).strip(), where)
                    self.assertLessEqual(e - s, 320, where)
                self.assertEqual(v["breaks"], sorted(set(v["breaks"])), where)
                self.assertEqual(v["breaks"][0], 0, where)
                self.assertTrue(all(0 <= b < len(text) for b in v["breaks"]), where)
                # cutting at the breaks and joining the pieces gives back the passage, character for character
                cuts = v["breaks"] + [len(text)]
                self.assertEqual("".join(text[a:b] for a, b in zip(cuts, cuts[1:])), text, where)
                seen += 1
        self.assertGreater(seen, 300)

    def test_hits_never_highlight_the_drugs_own_name(self):
        for drug, p in passages():
            v = annotate(p["text"], p["section"], p["title"], f"Is {drug} safe in pregnancy?", [drug], ["pregnancy"])
            for span in v["hits"]:
                self.assertNotIn(drug, piece(p["text"], span).lower())

    def test_reading_aids_leave_the_answer_unchanged(self):
        sources = [{"agent": "a", "drug": drug, "answers": [dict(p) for p in label["sections"][:5]]}
                   for drug, label in LABELS.items()]
        merged = {"question": "Can simvastatin be taken with clarithromycin?", "sources": sources}
        before = copy.deepcopy(sources)
        add_reading_aids(merged, {"topics": [], "drugsMentioned": [{"drug": "simvastatin"}]})
        for old, new in zip(before, merged["sources"]):
            for a, b in zip(old["answers"], new["answers"]):
                self.assertIn("view", b)
                self.assertEqual({k: v for k, v in b.items() if k != "view"}, a)


class WhatItPicks(unittest.TestCase):
    def simvastatin(self, section):
        return next(p for p in LABELS["simvastatin"]["sections"] if p["section"] == section)

    def test_interaction_passage_has_structure_and_its_key_is_the_prohibition(self):
        p = self.simvastatin("7.1")
        v = annotate(p["text"], p["section"], p["title"], "Can simvastatin be taken with clarithromycin?", ["simvastatin"], [])
        heads = [piece(p["text"], s) for s in v["heads"]]
        for expected in ("Clinical Impact:", "Intervention:", "Examples:"):
            self.assertIn(expected, heads)
        self.assertIn("contraindicated", piece(p["text"], v["key"]))
        refs = [piece(p["text"], s) for s in v["refs"]]
        self.assertTrue(refs and all(r.startswith("[see ") for r in refs))

    def test_a_pointer_word_is_not_mistaken_for_a_statement(self):
        # "CONTRAINDICATIONS" inside "[see CONTRAINDICATIONS ( 4 )]" must not make a sentence a key point on its own
        text = "Take one tablet each evening with water and food [see CONTRAINDICATIONS ( 4 )]."
        self.assertIsNone(annotate(text, "9", "Dosing", "", [], [])["key"])

    def test_pregnancy_question_finds_the_discontinue_sentence_and_highlights_the_topic(self):
        p = self.simvastatin("8.1")
        v = annotate(p["text"], p["section"], p["title"], "Is simvastatin safe in pregnancy?", ["simvastatin"], ["pregnancy"])
        self.assertIn("Discontinue", piece(p["text"], v["key"]))
        self.assertTrue(v["hits"] and all("pregna" in piece(p["text"], s).lower() for s in v["hits"]))

    def test_a_dose_question_highlights_the_amounts(self):
        p = self.simvastatin("2.2")
        v = annotate(p["text"], p["section"], p["title"], "What is the max dose of simvastatin", ["simvastatin"], [])
        self.assertIn("mg", piece(p["text"], v["key"]))
        self.assertIn("20 mg", [piece(p["text"], s) for s in v["hits"]])

    def test_no_key_point_beats_a_weak_guess(self):
        text = "The tablets are white and round and have a line down the middle for easy handling."
        self.assertIsNone(annotate(text, "9", "Description", "", [], [])["key"])

    def test_a_flattened_table_is_not_offered_as_a_key_point(self):
        text = ("Adult Dosage Guidelines Infection Clarithromycin Tablets Dosage Duration Acute Bacterial Exacerbation "
                "of Chronic Bronchitis 250 to 500 mg 7 days")
        self.assertIsNone(annotate(text, "9", "Dosage", "what dose", [], [], )["key"])

    def test_abbreviations_do_not_split_a_sentence(self):
        text = "Avoid grapefruit juice (e.g., in large amounts). Drink water."
        v = annotate(text, "9", "X", "", [], [])
        self.assertEqual(v["breaks"], [0, text.index("Drink")])

    def test_question_terms_skip_the_drug_name_and_filler(self):
        self.assertEqual(question_terms("Can simvastatin be taken with grapefruit juice?", ["simvastatin"]), ["grapefr", "juice"])
        self.assertEqual(question_terms("simvastatin", ["Simvastatin"]), [])


class ThroughTheApi(unittest.TestCase):
    def test_ask_returns_a_view_for_each_passage_and_the_text_is_unchanged(self):
        warnings.simplefilter("ignore")
        import assistant.server as server
        p = next(x for x in LABELS["simvastatin"]["sections"] if x["section"] == "7.1")
        answer = {"section": p["section"], "title": p["title"], "text": p["text"], "tags": p["tags"]}
        result = {"status": "verified", "agent": "simvastatin", "brand": "Simvastatin", "drug": "simvastatin",
                  "ansName": "x", "answers": [dict(answer)], "verification": {"ok": True, "mode": "live-dns", "checks": []},
                  "signature": {"mode": "demo-hmac"}}
        with patch.object(server, "query_agent", AsyncMock(return_value=result)):
            r = TestClient(server.app).post("/ask", json={"question": "Can simvastatin be taken with clarithromycin?"})
        self.assertEqual(r.status_code, 200)
        got = r.json()["sources"][0]["answers"][0]
        self.assertEqual(got["text"], p["text"])
        self.assertTrue(got["view"]["heads"] and got["view"]["key"])


if __name__ == "__main__":
    unittest.main()
