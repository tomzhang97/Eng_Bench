# Microtext Split Policy

**Status:** frozen for the current Silver-lineage release candidate; updated
after the 2026-07-31 reviewed-return integration and provenance unlock.

The active microtext set has 2,235 reviewed rows across 39 source documents. The
release split is document-disjoint and family-disjoint:

| Split | Documents | Rows | Category profile |
| --- | --- | ---: | --- |
| Train | 21 documents | 1146 | pin labels, facility-plan dimensions, room labels, civil labels, P&ID equipment/instrument/line/process labels |
| Dev | 10 documents | 730 | pin labels, civil dimensions, and P&ID equipment/instrument labels |
| Test | 8 documents | 359 | dimensions, room labels, equipment/instrument tags, tolerance values |

## Rationale

- Keep revisions from the same source family in the same split.
- Use the test split as a non-PCB holdout, rather than another near-duplicate
  schematic-label split.
- Preserve all reviewed rows; no microtext gold rows are dropped by this split
  freeze or the reviewed non-PCB expansion tranches.

## Caveat

The current microtext set is improved but still category-imbalanced: train and
dev include non-PCB dimensions, train has release-safe Wikimedia Commons P&ID
and process-flow slices, dev includes Peel Region and `pid_041` P&ID rows, and
test now includes a public-domain LOC HABS measured-drawing family. The train
and dev splits still remain dominated by PCB
pin labels, but P&ID is no longer absent from train.
This is acceptable for the stronger-complete gate, but a final public gold
release should continue expanding non-PCB train/dev coverage and add more
release-safe full P&ID/process rows before treating split-level category metrics as stable.
The ISO tolerance-table rows have repaired bboxes and a visual review pack, but
they remain outside active gold until source rights/provenance are cleared.

## Next Expansion Targets

Prioritize browser-validated `intake_first` sources that add non-PCB row volume:

- `arch_011` and `civil_009`: Library of Congress HABS/HAER/HALS measured drawings.
- More WSDOT/Caltrans/LOC civil plan sheets beyond the DS-2/DS-6/SD-1
  tranche.
- Additional P&ID/process drawings with release-safe rights beyond the Peel
  legend and Wikimedia Commons examples; the current San Diego pump-station
  guideline source is valid but low-yield, while GOV.UK/SA Water/DOE sources
  need OCR or rights/proprietary-marking clearance before gold promotion.
- More public-domain mechanical drawing pages from `mechanical_drawing_cornell`
  only if they add new values without duplicating existing dimension crops.
