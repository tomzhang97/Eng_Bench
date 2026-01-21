
import json
import os
from pathlib import Path

# Paths
base_dir = Path(r"c:\Users\Tom\Desktop\TraceRAG\Eng_Bench\visualdiff\annotations")
manual_jsonl_path = base_dir / "diff_pairs.viola_v1_1_to_v1_2_manual.jsonl"
pairs_jsonl_path = base_dir / "visualdiff_pairs.jsonl"
questions_jsonl_path = base_dir / "visualdiff_questions.jsonl"

# Constants
PROJECT_ID = "vdiff__viola__pcbV1.1__to__pcbV1.2"
DOC_ID = "viola"
VERSION_ID_OLD = "pcbV1.1"
VERSION_ID_NEW = "pcbV1.2"
SPLIT = "test"  # Default to test split for new data

# Label Mapping
LABEL_MAPPING = {
    "SYMBOL_CHANGE": ["symbol"],
    "TEXT_EDIT": ["text"],
    "ADD_TEXT": ["addition", "text"],
    "REMOVE_TEXT": ["deletion", "text"],
    "VALUE_CHANGE": ["value"],
    "LAYOUT_SHIFT": ["layout"],
    "UNKNOWN": ["unknown"]
}

def load_jsonl(path):
    data = []
    if path.exists():
        with open(path, 'r') as f:
            for line in f:
                if line.strip():
                    data.append(json.loads(line))
    return data

def append_jsonl(path, records):
    with open(path, 'a') as f:
        for record in records:
            f.write(json.dumps(record) + '\n')

def main():
    print(f"Loading annotations from {manual_jsonl_path}...")
    manual_data = load_jsonl(manual_jsonl_path)
    
    print(f"Loading existing pairs from {pairs_jsonl_path}...")
    existing_pairs = set()
    if pairs_jsonl_path.exists():
        with open(pairs_jsonl_path, 'r') as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    existing_pairs.add(rec['pair_id'])

    new_pairs = []
    new_questions = []
    
    print(f"Processing {len(manual_data)} annotations...")
    
    for idx, item in enumerate(manual_data):
        # Generate ID
        pair_id = f"{PROJECT_ID}__{idx:04d}"
        
        if pair_id in existing_pairs:
            continue # Skip duplicates
            
        page = item['page']
        bbox_xyxy = [int(x) for x in item['bbox_xyxy']] # Round to int
        label = item['label']
        change_types = LABEL_MAPPING.get(label, ["unknown"])
        
        # Construct Pair Record
        pair_record = {
            "pair_id": pair_id,
            "project_id": PROJECT_ID,
            "doc_id": DOC_ID,
            "board_id": None,
            "eco_id": None,
            "version_id_old": VERSION_ID_OLD,
            "version_id_new": VERSION_ID_NEW,
            "page_index_old": page,
            "page_index_new": page,
            "page_id_old": f"viola__{VERSION_ID_OLD}__p{page:04d}",
            "page_id_new": f"viola__{VERSION_ID_NEW}__p{page:04d}",
            "bbox_old": bbox_xyxy,
            "bbox_new": bbox_xyxy,
            "object_id_old": None,
            "object_id_new": None,
            "change_type": change_types,
            "severity": "unknown",
            "is_titleblock": False,
            "entity_id": None,
            "entity_kind": None,
            "change_desc_gt": "CHANGE_DESC_GT_TODO",
            "notes": "",
            "source": {
                "coco_project": "viola_v1.1_to_v1.2",
                "coco_image_old": "vdiff__viola__pcbV1.1__to__pcbV1.2__old.png", # Placeholder
                "coco_image_new": "vdiff__viola__pcbV1.1__to__pcbV1.2__new.png", # Placeholder
                "original_label": label
            },
            "split": SPLIT
        }
        new_pairs.append(pair_record)
        
        # Construct Question Record
        question_id = f"q_{pair_id}"
        question_text = "What changed in this region between pcbV1.1 and pcbV1.2?"
        
        # Refine question based on type
        if "symbol" in change_types:
             question_text = "What changed about the symbol or representation of this component between pcbV1.1 and pcbV1.2?"
        elif "value" in change_types:
             question_text = "How did the specification or value for this component change between pcbV1.1 and pcbV1.2?"
             
        question_record = {
            "question_id": question_id,
            "pair_id": pair_id,
            "doc_id": DOC_ID,
            "version_id_old": VERSION_ID_OLD,
            "version_id_new": VERSION_ID_NEW,
            "query_text": question_text,
            "answer_text": "CHANGE_DESC_GT_TODO",
            "answer_type": "diff_description",
            "requires_connectivity": False,
            "requires_position": False,
            "split": SPLIT
        }
        new_questions.append(question_record)

    print(f"Generated {len(new_pairs)} new pair records.")
    print(f"Generated {len(new_questions)} new question records.")
    
    if new_pairs:
        print(f"Appending to {pairs_jsonl_path}...")
        append_jsonl(pairs_jsonl_path, new_pairs)
        
    if new_questions:
        print(f"Appending to {questions_jsonl_path}...")
        append_jsonl(questions_jsonl_path, new_questions)
        
    print("Done!")

if __name__ == "__main__":
    main()
