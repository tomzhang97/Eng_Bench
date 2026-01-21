
import json
from pathlib import Path

# Paths
base_dir = Path(r"c:\Users\Tom\Desktop\TraceRAG\Eng_Bench\visualdiff\annotations")
manual_jsonl_path = base_dir / "diff_pairs.viola_v1_1_to_v1_2_manual.jsonl"
coco_json_path = base_dir / "cvat_export_v1.1_to_v1.2" / "annotations" / "instances_default.json"

def load_json(path):
    with open(path, 'r') as f:
        return json.load(f)

def load_jsonl(path):
    data = []
    with open(path, 'r') as f:
        for line in f:
            if line.strip():
                data.append(json.loads(line))
    return data

def main():
    print("Loading COCO data...")
    coco_data = load_json(coco_json_path)
    
    # Build image map: id -> (width, height, file_name)
    image_map = {img['id']: (img['width'], img['height'], img['file_name']) for img in coco_data['images']}
    
    # Build page to image id map (reverse of what we used in conversion)
    # In conversion, we did: page_num = int(filename.split('/')[-1].replace('.png', '').replace('p', ''))
    # So we can map page number back to image ID by checking filenames
    page_to_img_meta = {}
    for img_id, (w, h, fname) in image_map.items():
        try:
            page_str = fname.split('/')[-1].replace('.png', '').replace('p', '')
            page_num = int(page_str)
            page_to_img_meta[page_num] = (w, h, fname)
        except ValueError:
            print(f"Warning: Could not parse page number from {fname}")

    print("Loading manual annotations...")
    annotations = load_jsonl(manual_jsonl_path)
    
    violations = 0
    for ann in annotations:
        page = ann['page']
        bbox = ann['bbox_xyxy'] # [x1, y1, x2, y2]
        
        if page not in page_to_img_meta:
            print(f"Error: Page {page} not found in COCO images.")
            violations += 1
            continue
            
        img_w, img_h, fname = page_to_img_meta[page]
        x1, y1, x2, y2 = bbox
        
        is_valid = True
        msg = []
        
        if x1 < 0 or y1 < 0:
            is_valid = False
            msg.append("Negative coordinates")
        
        if x2 > img_w:
            is_valid = False
            msg.append(f"x2 ({x2}) > widths ({img_w})")
            
        if y2 > img_h:
            is_valid = False
            msg.append(f"y2 ({y2}) > height ({img_h})")
            
        if x1 >= x2:
            is_valid = False
            msg.append(f"x1 >= x2 ({x1} >= {x2})")
            
        if y1 >= y2:
            is_valid = False
            msg.append(f"y1 >= y2 ({y1} >= {y2})")
            
        if not is_valid:
            print(f"Violation in {ann['change_id']} (Page {page}, {fname}): {', '.join(msg)}. BBox: {bbox}")
            violations += 1
            
    if violations == 0:
        print(f"Success! All {len(annotations)} annotations are valid within image boundaries.")
    else:
        print(f"Found {violations} violations.")

if __name__ == "__main__":
    main()
