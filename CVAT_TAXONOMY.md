# CVAT Annotation Taxonomy

## Labels (MVP)

Use one of these labels for each change region:

- `TEXT_EDIT` - Text content changed
- `ADD_TEXT` - New text added
- `REMOVE_TEXT` - Text removed
- `SYMBOL_CHANGE` - Symbol/component changed
- `VALUE_CHANGE` - Value/specification changed
- `LAYOUT_SHIFT` - Position/layout changed
- `UNKNOWN` - Unclear change type

## Attributes (Recommended)

Add these attributes to each annotation for better analysis:

### change_id (CRITICAL)
- Format: `change_001`, `change_002`, etc.
- **Purpose**: Groups old/new annotations for the same logical change
- **Required**: Set the SAME change_id for both old and new annotations of one change
- Example: If R47 changed, both the old and new R47 boxes get `change_id: "change_047"`

### change_type
- `text` - Text-only change
- `symbol` - Symbol/component change
- `wiring` - Wiring/connection change
- `table` - Table content change
- `value` - Value/specification change
- `layout` - Position/layout change
- `titleblock` - Title block/metadata change
- `unknown` - Unclear

### severity
- `low` - Minor cosmetic change
- `medium` - Moderate functional change
- `high` - Critical design change

### is_titleblock
- `true` - Change is in title block area
- `false` - Change is in main content

### Optional (for richer metadata)
- `entity_id` - Component ID (e.g., "R47", "J5")
- `entity_kind` - Component type (e.g., "resistor", "connector")
- `eco_id` - ECO reference if known
- `change_desc_gt` - Gold description of the change
- `notes` - Any additional notes

## Cleanup Protocol

1. **Delete false positives**: Noise, page borders, irrelevant title block changes
2. **Tighten boxes**: One change region = one box
3. **Merge overlapping**: Combine boxes for the same logical change
4. **Set labels consistently**: Use taxonomy above
5. **Add attributes**: Especially `is_titleblock` for easy filtering
