from pathlib import Path
import importlib.util

spec = importlib.util.spec_from_file_location("runner", Path(__file__).parents[1] / "run_bluesky_eval.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

def test_dataset():
    root = Path(__file__).parents[1]
    qs, ans = runner.validate_frozen_data(root/"data/questions.csv", root/"data/answer_key.csv")
    assert len(qs) == 48
    assert sum(v["correct_answer"] == "X" for v in ans.values()) == 25
    assert sum(v["correct_answer"] == "Y" for v in ans.values()) == 23

def test_parser():
    text = "Some analysis.\nPrediction: X\nClose call: No"
    assert runner.extract_answer(text)[:2] == ("X", "No")
    text = "Prediction: Y\nClose call: Yes"
    pred, close, method, strict = runner.extract_answer(text)
    assert (pred, close, strict) == ("Y", "Yes", True)

def test_prompt_has_no_gold():
    root = Path(__file__).parents[1]
    qs, _ = runner.validate_frozen_data(root/"data/questions.csv", root/"data/answer_key.csv")
    p = runner.make_prompt(qs[0])
    assert "reposts?" in p
    assert "winner_reposts" not in p
    assert "correct_answer" not in p
