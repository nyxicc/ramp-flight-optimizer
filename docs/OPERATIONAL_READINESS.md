# Operational readiness

An optimization result has several independent status dimensions. Reading only
`status == OPTIMAL` is not enough to decide whether the operation is fully staffed.

## Solver status

`OptimizationStatus` hides solver-specific numeric constants:

| Status | Meaning |
|---|---|
| `OPTIMAL` | The last attempted objective was proven optimal under the current fixed higher-priority values. Inspect all objective records to determine whether the whole hierarchy completed. |
| `FEASIBLE` | A usable mathematical assignment was found, but the active objective was not proven optimal. |
| `INFEASIBLE` | No assignment satisfies the hard constraints for that attempted model. |
| `UNKNOWN` | No usable proof or solution was returned within the available solve conditions. |

## Objective proof state

Each completed or interrupted lexicographic stage has an `ObjectiveValue` with a
name, value, stage number, and `proven_optimal` flag. The report's “all objectives
proven optimal” answer is true only when objective records exist and every record
is proven. `OptimizationAttemptSummary` separately records stage count, attempt
runtime, usable state, and whether that attempt became the final result.

A timeout may therefore return a usable schedule while adding
`SOLVER_RESULT_NOT_PROVEN_OPTIMAL`. Lower-priority stages may be absent or
unproven; already fixed higher-priority outcomes remain protected.

## Readiness categories

| Readiness | Interpretation |
|---|---|
| `READY` | A usable schedule has no reported warnings. |
| `READY_WITH_WARNINGS` | The schedule is operationally usable but has a non-critical condition that must remain visible, such as an adopted emergency Lead. |
| `MANUAL_INTERVENTION_REQUIRED` | A usable partial schedule exists, but at least one critical staffing, qualification, break, or related operational warning needs supervisor action. |
| `NO_USABLE_SCHEDULE` | The solve returned no assignment safe to present as a usable schedule. |

These categories are derived from the final structured result, not from whether
CP-SAT merely returned `OPTIMAL`. For example, the fictional shortage scenario is
mathematically optimal because no better legal crew exists, but it is
`MANUAL_INTERVENTION_REQUIRED` because minimum staffing remains unmet.

## Flight staffing states

Every flight reports one of:

- `PREFERRED_STAFFED`: preferred and minimum staffing are met;
- `MINIMUM_STAFFED`: minimum is met but preferred is not;
- `BELOW_MINIMUM`: minimum is not met and the exact shortfall is reported.

Departure-only movements and turns also report PUSH and CLOSE_OUT coverage.
Qualification warnings may coexist with an otherwise minimum-staffed crew.
Preferred shortfall is a quality target and does not by itself mean the schedule
is below its minimum safety target.

## Protected-break outcomes

Employee results expose `SATISFIED`, `UNSATISFIED`,
`NOT_EVALUABLE_BETWEEN_ASSIGNMENTS`, or `NOT_APPLICABLE`. A known unsatisfied
required break creates `REQUIRED_BREAK_NOT_MET` and requires manual intervention.
Non-evaluable employees are reported separately rather than mislabeled as having
taken a break.

## Emergency Lead recovery

Emergency recovery is disabled unless explicitly configured. When enabled:

1. the ordinary Agent-only pass runs first;
2. a second Lead-inclusive pass is attempted only for a qualifying critical
   shortage and only when a usable comparison is possible;
3. Lead assignments are explicitly minimized;
4. the emergency outcome is adopted only if it improves the implemented critical
   comparison without making protected outcomes worse;
5. every adopted Lead intervention lists its employee, flight, and measurable
   reason: minimum staffing, PUSH coverage, or CLOSE_OUT coverage.

The result exposes both `EmergencyStaffingStatus` and a detailed
`EmergencyPassDisposition`, including not enabled, not needed, adopted, adopted
with shortage remaining, and several not-adopted outcomes. Adopted Lead use adds
`EMERGENCY_LEAD_USED`; critical remaining need can add Lead-specific or general
shortage warnings.

## Warning codes

Warnings are stable structured records with code, severity, message, and optional
flight or employee identity. Current codes cover:

- minimum staffing, PUSH qualification, and CLOSE_OUT qualification not met;
- required break not met;
- emergency Lead used or still required for staffing/qualification;
- critical shortage remaining and manual intervention required;
- solver result not proven optimal;
- emergency recovery attempted but not adopted;
- no usable schedule.

Warnings are deduplicated and deterministically ordered for reporting. A CLI
caller should preserve them rather than turning a valid partial result into an
exception.

## Partial-result and CLI behavior

`OPTIMAL` or `FEASIBLE` plus any readiness other than `NO_USABLE_SCHEDULE` is a
usable CLI result. It exits `0`, including `MANUAL_INTERVENTION_REQUIRED`, because
the command worked and returned actionable structured output. No usable schedule
or supported runtime/output failure exits `1`; invalid CLI usage exits `2`.

Operational supervisors must review `operational_readiness`, warnings, flight
shortfalls, qualification coverage, break outcomes, emergency interventions, and
objective proof state together. This optimizer is decision support, not an
automatic declaration that a live operation is safe.
