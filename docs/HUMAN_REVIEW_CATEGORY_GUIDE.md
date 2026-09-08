# Human Review Category Guide

Use this guide when filling microtext checklist CSVs. If the row category is wrong or `unknown_microtext`, put the correct value in `corrected_category`.

## Accepted Categories

| Category | Use For | Examples |
| --- | --- | --- |
| `pin_label` | PCB pins, connector labels, net labels, component reference labels | `J5`, `3V3`, `GPIO12`, `R101` |
| `component_value` | Full passive-component values or ratings printed beside schematic components | `10uF`, `4K7`, `1M`, `100nF` |
| `dimension_value` | Drawing dimensions, lengths, angles, radii, quantities tied to geometry | `12"`, `3 in.`, `45 deg`, `R 1/4` |
| `tolerance_value` | Tolerances, fit classes, plus/minus ranges | `+0.10/-0.05`, `H7/g6`, `+-0.002` |
| `room_label` | Architectural or facility room/area labels | `KITCHEN`, `BEDROOM`, `PUMP ROOM` |
| `instrument_tag` | P&ID/instrument/control tags or instrument abbreviations | `PIT`, `FIT-101`, `LIT`, `HS` |
| `equipment_tag` | Equipment names or equipment identifiers | `P-101`, `GENERATOR`, `SUMP PUMP`, `MIXER` |
| `pipe_line_tag` | Pipe, line, or stream identifiers | `6"-PW-101`, `L-204`, `SAN-2` |
| `process_value` | Process values shown on engineering diagrams | `150 psi`, `80 deg C`, `25 gpm` |
| `process_label` | Complete process-step, unit-operation, or material-stream names on process-flow diagrams | `Neutralisation`, `Methanol`, `Biodiesel` |

`component_value` is not a dimension or a pin label. Use it only when the
complete visible target is a passive-component value such as resistance,
capacitance, or inductance. A voltage/current fragment, geometric dimension,
reference designator, clipped value, or prose fragment must be corrected to a
different category or rejected.

## When To Reject

Reject instead of correcting when:

- the crop is not legible
- the proposed region is prose or a title block unrelated to the target category
- the answer requires guessing from nearby context
- the region is a duplicate of another row in the same checklist
- old/new visualdiff images are identical or the change is outside the box

## OCR-Less Rows

Rows with blank `proposed_text` usually came from image-only/raster proposals. For those:

1. Read the visible text from the crop or full page.
2. Set `review_status` to `edited`.
3. Put the visible text in `corrected_text`.
4. Put the category from the table above in `corrected_category`.
5. If you cannot read it, use `rejected` or `needs_full_page`.
