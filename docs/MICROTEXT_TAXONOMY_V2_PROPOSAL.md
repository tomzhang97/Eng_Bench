# Microtext Taxonomy v2 Staging Policy

This proposal resolves the shape of the current taxonomy backlog without
promoting any held row into active gold.

## Canonical Engineering Extension

The 2026-08-20 capacity audit replaces the older small-backlog proposal. Two
categories already supported by the miner, reviewer workflow, and question
templates are canonical for Gold v2.0 staging:

| Category | Definition | Gold v2.0 floor |
| --- | --- | ---: |
| `component_value` | Complete passive-component values for resistance, capacitance, or inductance, such as `4K7`, `10uF`, or `1M`. | 300 |
| `process_label` | Complete process-step, unit-operation, or material-stream labels on process-flow diagrams. | 150 |

Machine qualification is deliberately narrow. `component_value` must pass the
full-span component-value profile. `process_label` must pass the strict process
label filter. Every row still requires evidence, a locked split, source-rights
checks, and human acceptance. Rows failing these rules remain provisional and
do not count toward canonical balance capacity.

## Historical Minimal Extension

Add two categories:

| Category | Definition | Current staged rows |
| --- | --- | ---: |
| `standard_id` | Civil/structural standard drawing IDs, standard sheet codes, and standard type identifiers such as `BC-700M`, `BD-600M`, or `DI-1`. | 116 |
| `electrical_value` | Electrical component values and ratings such as `1K`, `1M`, `6-20V`, or `50mA`. | 13 |

Two P&ID rows map to the existing `equipment_tag` category and do not require a
new label.

## Why This Is Minimal

- `standard_id` separates drawing/document identifiers from P&ID instrument tags.
- `electrical_value` separates electrical ratings from geometric dimensions and process values.
- The former proposal deferred `component_value` when only 13 mixed electrical
  values were available. The current full-span pool contains more than 1,900
  component-value candidates across dozens of sources, while voltage/current,
  dimension, clipped, and prose-like rows are explicitly excluded or held.

## Promotion Rules

1. Every row still requires focused human category confirmation.
2. The 116 PennDOT-derived rows remain blocked until redistribution rights are documented.
3. The rights-clear rows may enter a focused taxonomy-confirmation packet.
4. New categories remain non-Gold until they pass the category floor, source,
   split, evidence, human-review, and strict-promotion gates.
5. Do not recategorize existing active-gold rows automatically.

## Current Backlog

- Total taxonomy holds: `131`
- Rights and taxonomy hold: `116`
- New-category confirmation ready: `13`
- Existing-category confirmation ready: `2`
- Unresolved: `0`

Row-level decisions are stored in the generated taxonomy hold plan CSV. No
active annotation file is changed by this process.
