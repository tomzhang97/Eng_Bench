import os
import json
from PIL import Image

def main():
    base_dir = r"c:\Users\Tom\Desktop\Eng_Bench"
    input_file = os.path.join(base_dir, "eng_bench.jsonl")
    out_dir = os.path.join(base_dir, "neurips_data")
    os.makedirs(out_dir, exist_ok=True)
    
    docs_out = os.path.join(out_dir, "docs.jsonl")
    ev_out = os.path.join(out_dir, "evidence.jsonl")
    qs_out = os.path.join(out_dir, "questions.jsonl")
    
    docs = {}          # key: image_path, value: doc dict
    evidences = {}     # key: (image_path, tuple(bbox)), value: ev_id
    
    with open(input_file, 'r', encoding='utf-8') as f_in, \
         open(docs_out, 'w', encoding='utf-8') as f_docs, \
         open(ev_out, 'w', encoding='utf-8') as f_ev, \
         open(qs_out, 'w', encoding='utf-8') as f_qs:
         
        for line in f_in:
            if not line.strip():
                continue
            item = json.loads(line)
            
            # 1. Process Docs
            doc_refs = []
            for img_path in item.get('images', []):
                # e.g., images/viola__pcbV1.0/page_0000.png
                if img_path not in docs:
                    parts = img_path.replace("\\", "/").split('/')
                    # Simple heuristic: folder name has doc_id__version_id, filename has page_id
                    folder = parts[-2]
                    filename = parts[-1]
                    page_id = filename.split('.')[0]
                    
                    if '__' in folder:
                        doc_id, version_id = folder.split('__', 1)
                    else:
                        doc_id = folder
                        version_id = "v1"
                        
                    full_img_path = os.path.join(base_dir, img_path)
                    width, height = 0, 0
                    if os.path.exists(full_img_path):
                        with Image.open(full_img_path) as img:
                            width, height = img.size
                            
                    doc_obj = {
                        "doc_id": doc_id,
                        "version_id": version_id,
                        "page_id": page_id,
                        "image_path": img_path,
                        "width": width,
                        "height": height
                    }
                    docs[img_path] = doc_obj
                    f_docs.write(json.dumps(doc_obj) + '\n')
                
                doc_obj = docs[img_path]
                doc_ref = f"{doc_obj['doc_id']}__{doc_obj['version_id']}"
                if doc_ref not in doc_refs:
                    doc_refs.append(doc_ref)
                
            # 2. Process Evidence
            ev_ids = []
            for ev in item.get('evidence', []):
                img_idx = ev.get('image_index', 0)
                if img_idx < len(item['images']):
                    img_path = item['images'][img_idx]
                else:
                    img_path = item['images'][0]
                    
                bbox = tuple(ev['bbox'])
                ev_key = (img_path, bbox)
                
                if ev_key not in evidences:
                    doc_obj = docs[img_path]
                    ev_id = f"ev_{doc_obj['doc_id']}_{doc_obj['version_id']}_{doc_obj['page_id']}_{len(evidences)}"
                    
                    # infer type
                    meta = item.get('metadata', {})
                    ev_type = "region"
                    if "change_type" in meta and meta["change_type"]:
                        ev_type = meta["change_type"][0]
                    elif "category" in meta:
                        ev_type = meta["category"]
                        
                    ev_obj = {
                        "evidence_id": ev_id,
                        "doc_id": doc_obj["doc_id"],
                        "version_id": doc_obj["version_id"],
                        "page_id": doc_obj["page_id"],
                        "bbox": list(bbox),
                        "type": ev_type
                    }
                    evidences[ev_key] = ev_id
                    f_ev.write(json.dumps(ev_obj) + '\n')
                
                ev_ids.append(evidences[ev_key])
                
            # 3. Process QA
            task_type = item.get('task', 'unknown')
            is_microtext = task_type == 'microtext'
            requires_revision = task_type == 'visualdiff'
            requires_multi_page = len(set([d.split('/')[0] for d in item.get('images', [])])) > 1 # Rough approximation
            
            # the task requires standard visual_diff instead of visualdiff
            task_mapped = "microtext" if is_microtext else "visual_diff" if task_type == "visualdiff" else task_type
            
            # Determine if this belongs to the hard subset
            is_hard = is_microtext or len(ev_ids) > 1 or requires_revision
            
            q_obj = {
                "question_id": item['id'],
                "question": item['question'],
                "answer": item['answer'],
                "task_type": task_mapped,
                "doc_refs": doc_refs,
                "evidence_ids": ev_ids,
                "metadata": {
                    "split": item.get('split', 'test'),
                    "is_microtext": is_microtext,
                    "requires_multi_page": requires_multi_page,
                    "requires_revision": requires_revision,
                    "is_hard": is_hard,
                    "original_metadata": item.get('metadata', {})
                }
            }
            f_qs.write(json.dumps(q_obj) + '\n')

    print(f"Done. Processed {len(docs)} documents and {len(evidences)} evidence regions.")

if __name__ == "__main__":
    main()
