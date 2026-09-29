import json

from safe_to_save.report import render_json, render_markdown
from tests.factories import withheld_observation

METADATA = {"data_label": "Synthetic demonstration", "coverage_start": "2026-01-01",
            "coverage_end": "2026-05-01", "dataset_id": "synthetic-v2"}


def test_json_is_stable_sorted_and_null_denominators_are_preserved():
    result = render_json(METADATA, (withheld_observation(),))
    payload = json.loads(result)
    assert payload["baselines"]["conservative_rules"]["coverage_ratio"] == "0.0000"
    assert payload["baselines"]["conservative_rules"]["mean_absolute_error_minor"] is None
    assert result == json.dumps(payload, sort_keys=True, indent=2) + "\n"
    assert result == render_json(dict(reversed(list(METADATA.items()))), (withheld_observation(),))


def test_empty_json_exact_string():
    assert render_json({}, ()) == '{\n  "baselines": {},\n  "metadata": {},\n  "observations": []\n}\n'


def test_markdown_exact_small_fixture():
    assert render_markdown(METADATA, (withheld_observation(),)) == (
        "# Safe to Save baseline evaluation\n\n"
        "Synthetic demonstration\n\n"
        "Coverage: 2026-01-01 to 2026-05-01.\n"
        "Checkpoints: 1 distinct; 1 baseline observations.\n\n"
        "| Metric | conservative_rules |\n| --- | --- |\n"
        "| Scored / total | 0 / 1 |\n| Withheld | 1 |\n"
        "| Coverage ratio | 0.0000 |\n| MAE (minor units) | null |\n"
        "| Median absolute error (minor units) | null |\n"
        "| Upper forecast coverage | null |\n| Floor violations | 0 |\n"
        "| Violation rate | null |\n| Reversal count | 0 |\n"
        "| Reversal total (minor units) | 0 |\n"
        "| Recommended total (minor units) | 0 |\n"
        "| Useful savings (minor units) | 0 |\n"
        "| Hindsight safe (minor units) | 0 |\n| Useful yield | null |\n"
        "| Median weekly change (minor units) | null |\n\n"
        "Withheld reasons:\n\n- conservative_rules: insufficient_history = 1\n\n"
        "Worst three shortfall/reversal cases:\n\nNone.\n\n"
        "Limitations: baseline results are not model promotion evidence. "
        "Checkpoints are independent counterfactuals; totals are overlapping opportunities, "
        "not cumulative savings. The floor reserve uses forecasts and does not guarantee "
        "realised safety. Reversal amounts include pre-existing floor deficits.\n"
    )
