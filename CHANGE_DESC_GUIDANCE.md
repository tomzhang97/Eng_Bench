# change_desc_gt Guidance

## Purpose

The `change_desc_gt` field provides a **gold standard natural language description** of each change for Q/A evaluation.

## Where to Set It

### Option 1: CVAT Annotation (Recommended)
Add `change_desc_gt` as a text attribute in CVAT:
- Set during annotation for important changes
- Leave blank for minor/obvious changes
- Script 07 will use these values

### Option 2: Seed Enrichment
If seeds contain `change_desc` field:
- Script 07 will use seed descriptions as fallback
- Useful for pre-populated metadata

### Option 3: Post-Processing
Manual editing of `visualdiff_pairs.jsonl`:
- Search for `"CHANGE_DESC_GT_TODO"`
- Replace with actual descriptions
- Focus on test set first

## Writing Guidelines

### Good Examples
✅ "The value of R47 changed from 10kΩ to 4.7kΩ."
✅ "Connector J5 was relocated from top-left to bottom-right quadrant."
✅ "Title block revision date updated from 2020-03-15 to 2021-08-22."

### Bad Examples
❌ "Changed" (too vague)
❌ "See ECO-1234" (not self-contained)
❌ "R47 modification per engineering request" (lacks specifics)

## Best Practices

1. **Be specific**: Include component IDs, values, locations
2. **Be concise**: 1-2 sentences maximum
3. **Be factual**: Describe what changed, not why
4. **Be complete**: Include both old and new states when relevant

## Priority for v1

Focus on:
1. **Test set changes** (used for evaluation)
2. **Non-titleblock changes** (more interesting)
3. **High severity changes** (critical for engineering)

Titleblock and low-severity changes can use default `"CHANGE_DESC_GT_TODO"` for v1.
