import unittest

from datashield.rules import QUESTIONS, evaluate


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.answers = {question["key"]: 2 for question in QUESTIONS}
        self.answers.update({
            "has_personal_data": 1,
            "customers_ru": 1,
            "eu_clients": 0, "eu_activity": 0,
            "large_scale_monitoring": 0,
            "special_data": 0,
            "biometric_data": 0,
            "marketing": 0,
            "profiling": 0,
            "processors": 0,
            "cross_border": 0,
        })

    def test_no_personal_data_means_no_requirements(self):
        self.answers["has_personal_data"] = 0
        self.assertEqual(evaluate(self.answers), [])

    def test_completed_measure_stays_visible(self):
        self.answers["has_policy"] = 1
        requirements = evaluate(self.answers)
        policy = next(item for item in requirements if item["key"] == "public_policy")
        self.assertEqual(policy["question"], "has_policy")

    def test_gdpr_requirements_follow_territorial_answers(self):
        baseline = {item["key"] for item in evaluate(self.answers)}
        self.assertNotIn("gdpr_records", baseline)
        self.answers["eu_clients"] = 1
        with_gdpr = {item["key"] for item in evaluate(self.answers)}
        self.assertIn("gdpr_records", with_gdpr)

    def test_monitoring_scale_alone_does_not_imply_gdpr(self):
        self.answers.update(large_scale_monitoring=1, eu_activity=0, eu_clients=0)
        self.assertNotIn("gdpr_records", {item["key"] for item in evaluate(self.answers)})
        self.answers["eu_activity"] = 1
        self.assertIn("gdpr_records", {item["key"] for item in evaluate(self.answers)})

    def test_special_context_adds_specific_requirements(self):
        self.answers.update(processors=1, cross_border=1, marketing=1, special_data=1)
        keys = {item["key"] for item in evaluate(self.answers)}
        self.assertTrue({
            "processor_contracts", "cross_border_transfer", "direct_marketing",
            "sensitive_data_safeguards",
        }.issubset(keys))

    def test_result_is_deterministic_and_input_is_not_mutated(self):
        original = dict(self.answers)
        first = evaluate(self.answers)
        second = evaluate(self.answers)
        self.assertEqual(first, second)
        self.assertEqual(self.answers, original)
        self.assertEqual(len(first), len({item["key"] for item in first}))
        for item in first:
            self.assertTrue(item["action"])
            self.assertTrue(item["legal_url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
