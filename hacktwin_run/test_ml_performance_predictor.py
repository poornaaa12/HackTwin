"""Unit tests for the group-validated cycle-count predictor."""

import json
import tempfile
import unittest
from pathlib import Path

from ml_performance_predictor import extract_static_features, predict, train_model


class TestMLPerformancePredictor(unittest.TestCase):
    PROGRAMS = [
        "0000000000000000\n1111110000010000\n0000000000000001\n1110001100000111\n",
        "0000000000000010\n1111110000010000\n0000000000000001\n1110001100000111\n",
        "0000000000000011\n1111110000010000\n0000000000000001\n1110001100000111\n",
        "0000000000000100\n1111110000010000\n0000000000000001\n1110001100000111\n",
    ]

    def test_static_feature_extractor(self):
        features = extract_static_features(self.PROGRAMS[0], ram0=3, ram1=5)
        self.assertEqual(features["instruction_count"], 4)
        self.assertEqual(features["backward_jump_count"], 1)
        self.assertEqual(features["ram0_initial"], 3)
        self.assertEqual(features["ram1_initial"], 5)

    def test_rejects_non_hack_input(self):
        with self.assertRaises(ValueError):
            extract_static_features("not a Hack word")

    def test_grouped_training_writes_validation_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            programs = root / "programs"
            programs.mkdir()
            rows = []
            for i, source in enumerate(self.PROGRAMS):
                name = f"g{i}.hack"
                (programs / name).write_text(source)
                for scenario, ram in enumerate((0, 20)):
                    rows.append({
                        "program_id": f"g{i}", "program_hash": f"group-{i}",
                        "hack_file": f"programs/{name}", "cycles_to_halt": 10 + i * 5 + scenario,
                        "initial_memory": {"RAM[0]": ram, "RAM[1]": 0},
                    })
            dataset = root / "training_data.jsonl"
            dataset.write_text("".join(json.dumps(row) + "\n" for row in rows))
            model_path = root / "model.joblib"
            report = train_model(dataset, model_path)
            self.assertEqual(report["sample_count"], 8)
            self.assertEqual(report["program_group_count"], 4)
            self.assertTrue(model_path.exists())
            self.assertTrue(model_path.with_suffix(".validation.json").exists())
            result = predict(model_path, programs / "g0.hack")
            self.assertIn("predicted_cycles_to_terminal_loop", result)
            self.assertIn("cross_validation_90pct_error_band", result)


if __name__ == "__main__":
    unittest.main()
