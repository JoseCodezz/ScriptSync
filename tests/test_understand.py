"""Run from the repo root:  python -m unittest tests.test_understand -v"""
import unittest

from assistant.understand import analyze, build_notices, drug_gaps, find_phi

AGENTS = [
    {"id": "agent-a", "role": "brand", "brand": "Simvastatin label agent", "drug": "simvastatin", "aliases": ["zocor"]},
    {"id": "agent-b", "role": "brand", "brand": "Clarithromycin label agent", "drug": "clarithromycin"},
    {"id": "attack-x", "role": "attacker", "brand": "Simvastatin label agent", "drug": "fakedrug"},
]


class PhiTests(unittest.TestCase):
    def test_clean_questions_pass(self):
        for q in ["What do I need to know about CYP3A interactions?",
                  "Is there anything on kidney dosing?",
                  "Patient is on simvastatin. I am considering clarithromycin. What should I know?",
                  "Starting dose 10 mg once daily, 2 tablets, 30 days"]:
            self.assertEqual(find_phi(q), [], q)

    def test_identifiers_are_caught(self):
        cases = {
            "SSN 123-45-6789": "a Social Security number",
            "call 540-555-0199": "a phone number",
            "jane.doe@example.com": "an email address",
            "DOB 04/12/1961": "a full date (possible date of birth)",
            "date of birth is unknown": "a date of birth",
            "MRN 445566": "a medical record number",
            "the patient named Smith": "a patient name",
            "lives at 12 Main Street": "a street address",
        }
        for text, expected in cases.items():
            self.assertIn(expected, find_phi(text), text)


class AnalyzeTests(unittest.TestCase):
    def test_plain_interaction_question(self):
        a = analyze("What do I need to know about CYP3A interactions?", AGENTS)
        self.assertEqual(a["drugsMentioned"], [])
        self.assertFalse(a["switching"])
        self.assertFalse(a["adviceSeeking"])
        self.assertEqual(build_notices(a), [])

    def test_known_drugs_and_alias(self):
        a = analyze("Patient is on Zocor. I am considering clarithromycin.", AGENTS)
        self.assertEqual([d["drug"] for d in a["drugsMentioned"]], ["simvastatin", "clarithromycin"])

    def test_attackers_are_never_recognized(self):
        self.assertEqual(analyze("tell me about fakedrug", AGENTS)["drugsMentioned"], [])

    def test_advice_seeking(self):
        for q in ["Should I switch to clarithromycin?", "Which is better here?",
                  "Is it safe to co-prescribe these?", "What do you recommend?"]:
            a = analyze(q, AGENTS)
            self.assertTrue(a["adviceSeeking"], q)
            self.assertEqual(build_notices(a)[0]["type"], "advice")

    def test_switching_flags_unknown_drug(self):
        a = analyze("How do I switch from simvastatin to atorvastatin?", AGENTS)
        self.assertTrue(a["switching"])
        self.assertIn("switching", a["topics"])
        self.assertEqual(a["drugsWithoutAgent"], ["atorvastatin"])
        gaps = drug_gaps(a)
        self.assertEqual(gaps[0]["topic"], "drug: atorvastatin")
        self.assertIn("Not covered", gaps[0]["message"])

    def test_switching_between_two_known_drugs_has_no_unknowns(self):
        a = analyze("Switching from simvastatin to clarithromycin, what do the labels say?", AGENTS)
        self.assertTrue(a["switching"])
        self.assertEqual(a["drugsWithoutAgent"], [])

    def test_switching_filler_words_are_not_drugs(self):
        for q in ["Can I switch to a lower dose?", "What do I need to know when switching to another option?",
                  "Changing to twice daily dosing"]:
            self.assertEqual(analyze(q, AGENTS)["drugsWithoutAgent"], [], q)

    def test_unknown_drugs_only_from_switching_questions(self):
        self.assertEqual(analyze("Tell me about atorvastatin", AGENTS)["drugsWithoutAgent"], [])


if __name__ == "__main__":
    unittest.main()
