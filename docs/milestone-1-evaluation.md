> **Synthetic demonstration:** These values come from invented fixtures. They prove
> reproducibility and evaluation behaviour, not accuracy on Hakim's finances or
> generalisation to Monzo customers.

# Safe to Save baseline evaluation

Synthetic demonstration

Coverage: 2025-09-01T00:00:00+00:00 to 2026-05-01T00:00:00+00:00.
Checkpoints: 34 distinct; 102 baseline observations.

| Metric | comparable_position | conservative_rules | trailing_median |
| --- | --- | --- | --- |
| Scored / total | 0 / 34 | 0 / 34 | 4 / 34 |
| Withheld | 34 | 34 | 30 |
| Coverage ratio | 0.0000 | 0.0000 | 0.1176 |
| MAE (minor units) | null | null | 2250.0000 |
| Median absolute error (minor units) | null | null | 1600.0000 |
| Upper forecast coverage | null | null | 0.2500 |
| Floor violations | 0 | 0 | 3 |
| Violation rate | null | null | 0.7500 |
| Reversal count | 0 | 0 | 3 |
| Reversal total (minor units) | 0 | 0 | 7200 |
| Recommended total (minor units) | 0 | 0 | 3236600 |
| Useful savings (minor units) | 0 | 0 | 3229400 |
| Hindsight safe (minor units) | 0 | 0 | 3229400 |
| Useful yield | null | null | 1.0000 |
| Median weekly change (minor units) | null | null | 1600.0000 |

Withheld reasons:

- comparable_position: fewer_than_three_comparable_cycles = 16
- comparable_position: insufficient_trailing_history = 4
- comparable_position: missing_payday = 14
- conservative_rules: fewer_than_three_comparable_cycles = 16
- conservative_rules: insufficient_trailing_history = 4
- conservative_rules: missing_payday = 14
- trailing_median: fewer_than_three_earlier_cycles = 12
- trailing_median: insufficient_trailing_history = 4
- trailing_median: missing_payday = 14

Worst three shortfall/reversal cases:

- 2025-12-28 (trailing_median): reversal 4900 minor units; minimum balance 20100 minor units.
- 2026-01-11 (trailing_median): reversal 1600 minor units; minimum balance 23400 minor units.
- 2026-01-04 (trailing_median): reversal 700 minor units; minimum balance 24300 minor units.

Limitations: baseline results are not model promotion evidence. Checkpoints are independent counterfactuals; totals are overlapping opportunities, not cumulative savings. The floor reserve uses forecasts and does not guarantee realised safety. Reversal amounts include pre-existing floor deficits.
