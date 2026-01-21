"""
Convert CVAT COCO 1.0 export to Eng_Bench JSONL format for visualdiff.
"""
import json
from pathlib import Path

# Paths
coco_json_path = Path(r"c:\Users\Tom\Desktop\TraceRAG\Eng_Bench\visualdiff\annotations\cvat_export_v1.1_to_v1.2\annotations\instances_default.json")
output_jsonl_path = Path(r"c:\Users\Tom\Desktop\TraceRAG\Eng_Bench\visualdiff\annotations\diff_pairs.viola_v1_1_to_v1_2_manual.jsonl")

# Load COCO JSON
with open(coco_json_path, 'r') as f:
    coco_data = json.load(f)

# Build category id to name mapping
category_map = {cat['id']: cat['name'] for cat in coco_data['categories']}

# Build image id to page number mapping (image filename is pXXXX.png)
image_map = {}
for img in coco_data['images']:
    # Extract page number from filename like "images/p0000.png"
    filename = img['file_name']
    page_str = filename.split('/')[-1].replace('.png', '').replace('p', '')
    page_num = int(page_str)
    image_map[img['id']] = page_num

# Pair ID for v1.1 to v1.2
pair_id = "vdiff__viola__pcbV1.1__to__pcbV1.2"

# Convert annotations to JSONL format
annotations_out = []
for idx, ann in enumerate(coco_data['annotations']):
    # COCO bbox is [x, y, width, height], convert to xyxy
    x, y, w, h = ann['bbox']
    bbox_xyxy = [x, y, x + w, y + h]
    
    label = category_map[ann['category_id']]
    page = image_map[ann['image_id']]
    
    change_id = f"{pair_id}_ann{idx:04d}"
    
    record = {
        "pair_id": pair_id,
        "change_id": change_id,
        "page": page,
        "bbox_xyxy": bbox_xyxy,
        "label": label,
        "auto_type": label,  # Source was manual annotation
        "source": "cvat_manual"
    }
    annotations_out.append(record)

# Write JSONL output
with open(output_jsonl_path, 'w') as f:
    for record in annotations_out:
        f.write(json.dumps(record) + '\n')

print(f"Converted {len(annotations_out)} annotations to {output_jsonl_path}")

# Print statistics
from collections import Counter
label_counts = Counter([a['label'] for a in annotations_out])
page_counts = Counter([a['page'] for a in annotations_out])

print("\nLabel distribution:")
for label, count in sorted(label_counts.items()):
    print(f"  {label}: {count}")

print(f"\nPages with annotations: {len(page_counts)}")
print(f"Total annotations: {len(annotations_out)}")
