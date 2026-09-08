# Optimization objectives

The optimizer does not combine business priorities into a weighted score. It
solves objectives sequentially. When a stage is proven optimal, the exact value is
constrained before the next stage is solved. Therefore a lower-priority stage may
choose among equally good higher-priority solutions but may not worsen a fixed
higher-priority optimum.

## Ordinary 17-stage hierarchy

| Stage | Direction and implemented name | Purpose and protected priorities |
|---:|---|---|
| 1 | Maximize `minimum_covered_flights` | Staff as many flights as possible to the minimum. Every later concern must preserve this count. |
| 2 | Maximize `minimum_staffed_qualification_compliant_flights` | Among stage-1 ties, maximize minimum crews that also have all required qualifications. It cannot sacrifice a minimum-staffed flight. |
| 3 | Maximize `minimum_staffed_individual_qualification_coverage` | Preserve as many individual PUSH/CLOSE_OUT requirements as possible on minimum-staffed crews when full compliance ties. Stages 1–2 remain fixed. |
| 4 | Minimize `total_minimum_shortfall` | Reduce the sum of missing people across all flights without reducing the number of minimum-covered or qualification-protected flights. |
| 5 | Minimize `largest_minimum_shortfall` | Limit the worst single-flight shortage after total shortage is optimal. It cannot move shortage reduction away from earlier goals. |
| 6 | Minimize `known_unsatisfied_required_breaks` | Protect required between-assignment breaks for evaluable employees. Staffing and qualification outcomes from stages 1–5 remain fixed. |
| 7 | Maximize `preferred_staffed_flights` | Bring as many flights as possible to preferred staffing only after minimum staffing, qualification, shortage, and break priorities are secured. |
| 8 | Minimize `total_preferred_shortfall` | Reduce total distance from preferred staffing among stage-7 ties without damaging any preceding result. |
| 9 | Maximize `partial_crew_individual_qualification_coverage` | Retain PUSH/CLOSE_OUT coverage on crews that remain below minimum, while all higher operational outcomes stay fixed. |
| 10 | Minimize `raw_flight_count_spread` | Narrow the highest-to-lowest assignment-count range among participating employees. It cannot trade away staffing, qualifications, or breaks. |
| 11 | Minimize `total_pairwise_flight_count_difference` | Improve the interior raw-count distribution when the range is tied, preserving the optimum range and every operational stage. |
| 12 | Minimize `maximum_consecutive_flight_streak` | Reduce the worst consecutive run after raw-count fairness is fixed. It cannot worsen assignment-count balance. |
| 13 | Minimize `total_employee_longest_streaks` | Improve schedule-wide streak distribution among solutions with the same worst streak, preserving stages 1–12. |
| 14 | Minimize `total_shift_adjusted_flight_count_deviation` | Allocate flight counts proportionally to scheduled availability after raw counts and streaks are protected. |
| 15 | Minimize `adjusted_workload_spread` | Narrow the maximum-minus-minimum adjusted workload, accounting for service class and exact-three staffing, without changing any earlier optimum. |
| 16 | Minimize `total_pairwise_adjusted_workload_difference` | Improve the interior adjusted-workload distribution when its range is tied, preserving stages 1–15. |
| 17 | Maximize `total_continuity_retention` | Retain employees between plausible nearby flights only as the final tie-breaker; continuity can never damage staffing, qualifications, breaks, fairness, streaks, or workload. |

The names above are the public `ObjectiveValue.name` values and appear in the
same order as `OptimizationResult.objective_values` for a fully completed ordinary
solve.

## Emergency-pass hierarchy

An emergency pass uses the same hierarchy but inserts one stage immediately after
ordinary stage 6:

| Emergency stage | Direction and name | Purpose |
|---:|---|---|
| 7 | Minimize `total_emergency_lead_assignments` | Use the fewest Lead assignments after critical staffing, qualification, shortage, and break outcomes are protected. |

The ordinary stages that follow are shifted by one, so a fully completed emergency
pass reports 18 objective values. Lead use is not hidden inside a weighted score:
it is minimized explicitly and also reconstructed into public intervention records
and warnings.

## Time budgets and proof state

The configured solver time is one total budget shared across sequential stages in
an attempt. Each `ObjectiveValue` records whether its value was proven optimal.
If time expires, the optimizer preserves the last safely available solution and
fixed proven stages where possible. It does not label an unfinished hierarchy as
fully optimal. See [Operational readiness](OPERATIONAL_READINESS.md) for how proof
state affects warnings and readiness.
