import unittest
from engine.analyzer import build_prompt

class PromptBuilderTests(unittest.TestCase):
    def test_prompt_discloses_semantic_limits(self):
        report = {
            "metadata": {"width": 1920, "height": 1080, "fps": 30, "duration_seconds": 4},
            "visual_summary": {"visual_change_label": "low sampled visual change",
                               "average_frame_change_percent": 1.2,
                               "average_brightness_0_255": 120},
            "scenes": [{"start_seconds": 0, "end_seconds": 4,
                        "description": "Estimated mid-lit segment; visually stable across 2 sampled frames."}],
            "audio": {"present": False}
        }
        prompt = build_prompt(report)
        self.assertIn("not semantic scene recognition", prompt)
        self.assertIn("1920x1080", prompt)
        self.assertIn("Do not add objects", prompt)

if __name__ == "__main__":
    unittest.main()
