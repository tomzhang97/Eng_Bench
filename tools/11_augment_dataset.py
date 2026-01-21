"""
11_augment_dataset.py - Publishable-Grade Augmentation (v6)

Publishable-grade fixes:
1. Entity quality filter: drop noisy/long entities
2. Drop ambiguous old-version locate queries
3. Better question phrasing for locate_bbox
"""

import json
import os
import random
import re
import hashlib

random.seed(42)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)

MICROTEXT_QUESTIONS = os.path.join(BASE_DIR, "microtext/annotations/microtext_questions.jsonl")
MICROTEXT_ITEMS = os.path.join(BASE_DIR, "microtext/annotations/microtext_items.jsonl")
VISUALDIFF_QUESTIONS = os.path.join(BASE_DIR, "visualdiff/annotations/visualdiff_questions.jsonl")
VISUALDIFF_PAIRS = os.path.join(BASE_DIR, "visualdiff/annotations/visualdiff_pairs.jsonl")

OUTPUT_MICROTEXT = os.path.join(BASE_DIR, "engbench_v1_microtext.jsonl")
OUTPUT_VISUALDIFF = os.path.join(BASE_DIR, "engbench_v1_visualdiff.jsonl")

COORD_FRAME = "aligned"

# Entity quality thresholds
MAX_ENTITY_LENGTH = 60
MIN_ENTITY_LENGTH = 2
GENERIC_TOKENS = {"gnd", "vcc", "connector", "data", "pin", "note", "page", "section", "table", "figure"}


def load_jsonl(path):
    data = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                data.append(json.loads(line))
    return data


def save_jsonl(data, path):
    with open(path, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def normalize_text(text):
    if not text:
        return ""
    cleaned = re.sub(r'^[\s,;:.\-+]+', '', text)
    cleaned = re.sub(r'[\s,;:.\-+]+$', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def split_multi_item_text(text):
    if not text:
        return []
    items = re.split(r'[,;]\s*', text)
    return [normalize_text(item) for item in items if normalize_text(item)]


def clean_entity(text):
    if not text:
        return ""
    cleaned = re.sub(r'^[-+\s]+', '', text)
    cleaned = re.sub(r'\s+-\s+', ' ', cleaned)
    # Remove excessive + signs
    cleaned = re.sub(r'\s*\+\s*', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = cleaned.strip('.,;:[](){}')
    return cleaned


def is_valid_entity(entity):
    """Filter out low-quality entities."""
    if not entity:
        return False
    
    # Length check
    if len(entity) < MIN_ENTITY_LENGTH or len(entity) > MAX_ENTITY_LENGTH:
        return False
    
    # Digits-only check
    if re.match(r'^[\d\s\.\-\+]+$', entity):
        return False
    
    # Mostly punctuation/digits (>50% non-alpha)
    alpha_count = sum(1 for c in entity if c.isalpha())
    if len(entity) > 0 and alpha_count / len(entity) < 0.4:
        return False
    
    # Generic tokens (case-insensitive)
    entity_lower = entity.lower()
    if entity_lower in GENERIC_TOKENS:
        return False
    
    # Too many tokens (likely sentence fragment)
    if len(entity.split()) > 10:
        return False
    
    # Bracket spam like "[3] + [3] + [3]"
    if entity.count('[') > 2 or entity.count(']') > 2:
        return False
    
    return True


def get_stable_pair_id(pair):
    doc_id = pair.get('doc_id', 'doc')
    ver_old = pair.get('version_id_old', 'A')
    ver_new = pair.get('version_id_new', 'B')
    return f"vdiff__{doc_id}__{ver_old}__to__{ver_new}"


def get_change_id(pair):
    pair_id = pair.get('pair_id', '')
    parts = pair_id.rsplit('__', 1)
    return parts[-1] if len(parts) > 1 else pair_id


def determine_change_subtype(pair):
    has_old = pair.get('bbox_old') is not None
    has_new = pair.get('bbox_new') is not None
    if has_old and not has_new:
        return "remove"
    elif not has_old and has_new:
        return "add"
    else:
        return "edit"


def compute_bbox_area(bbox):
    if not bbox or len(bbox) < 4:
        return 0
    return abs(bbox[2] - bbox[0]) * abs(bbox[3] - bbox[1])


def compute_difficulty(pair, question_type):
    bbox_new = pair.get('bbox_new', [0, 0, 100, 100])
    bbox_old = pair.get('bbox_old', [0, 0, 100, 100])
    area = max(compute_bbox_area(bbox_new), compute_bbox_area(bbox_old))
    
    if question_type == "change_subtype":
        return "Hard" if area < 10000 else "Medium"
    elif question_type == "yesno":
        return "Medium"
    else:
        if area < 5000:
            return "Hard"
        elif area < 20000:
            return "Medium"
        else:
            return "Easy"


# =============================================================================
# MICROTEXT
# =============================================================================

def augment_microtext(questions_path, items_path, output_path):
    print(f"Augmenting Microtext: {questions_path}...")
    questions = load_jsonl(questions_path)
    items = {item['item_id']: item for item in load_jsonl(items_path)}
    
    augmented_data = []
    seen = set()
    
    for q in questions:
        target_item_ids = q.get('item_ids', [])
        if not target_item_ids:
            continue
        target_id = target_item_ids[0]
        if target_id not in items:
            continue
        item = items[target_id]
        
        doc_id = item['doc_id']
        page = item['page_index']
        bbox = item['bbox']
        text_raw = q.get('answer_text', '')
        
        text_items = split_multi_item_text(text_raw)
        if not text_items:
            text_items = [normalize_text(text_raw)] if normalize_text(text_raw) else []
        
        for idx, text_norm in enumerate(text_items):
            if not text_norm:
                continue
                
            query = f"Locate the tolerance value {text_norm} on page {page} in {doc_id.replace('_', ' ').title()}."
            
            dedup_key = hashlib.md5(f"{query}_{doc_id}".encode()).hexdigest()
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            
            if page == 2:
                split = "train"
            elif page == 3:
                split = "dev"
            else:
                split = "test"
            
            qid = f"{q['question_id']}_{idx}" if len(text_items) > 1 else q['question_id']
            
            new_entry = {
                "qid": qid,
                "doc_id": doc_id,
                "question_type": "locate_bbox",
                "question": query,
                "query_text_norm": text_norm,
                "answer": {
                    "doc": doc_id,
                    "page": page,
                    "bbox": bbox,
                    "text": text_norm,
                    "text_raw": text_raw if len(text_items) > 1 else text_norm
                },
                "evidence": [{
                    "doc_side": "B",
                    "page": page,
                    "bbox": bbox,
                    "text": text_norm
                }],
                "coord_frame": COORD_FRAME,
                "category": "Lookup",
                "type": "Value Extraction",
                "difficulty": "Easy",
                "split": split
            }
            augmented_data.append(new_entry)
    
    splits = {"train": 0, "dev": 0, "test": 0}
    for q in augmented_data:
        splits[q['split']] += 1
    print(f"Microtext splits: {splits}")
    print(f"Saved {len(augmented_data)} microtext questions to {output_path}")
    save_jsonl(augmented_data, output_path)


# =============================================================================
# VISUALDIFF
# =============================================================================

def generate_visualdiff_questions(pair, entity):
    """Generate questions - ONLY new-version locate queries (drop ambiguous old-version)."""
    doc_id = pair.get('doc_id', 'document')
    context = doc_id.capitalize()
    ver_old = pair.get('version_id_old', 'A')
    ver_new = pair.get('version_id_new', 'B')
    page_new = pair.get('page_index_new', 0)
    change_subtype = determine_change_subtype(pair)
    
    questions = []
    
    # yesno positive
    questions.append({
        "question_type": "yesno",
        "question": f"On page {page_new}, did component {entity} change between {ver_old} and {ver_new}? Answer yes/no and cite the region.",
        "answer": {"yes": True, "page": page_new},
        "is_positive": True
    })
    
    # locate_bbox (NEW version only - drop old-version to avoid ambiguity)
    if pair.get('bbox_new'):
        questions.append({
            "question_type": "locate_bbox",
            "question": f"On page {page_new} of {context}, locate the change region for {entity} in the updated version ({ver_new}). Return the bounding box.",
            "answer": {"doc_side": "B", "page": page_new, "bbox": pair['bbox_new']}
        })
    
    # change_subtype
    questions.append({
        "question_type": "change_subtype",
        "question": f"On page {page_new}, classify the change to {entity} between {ver_old} and {ver_new}. Is it added, removed, or edited?",
        "answer": {"change_subtype": change_subtype, "entity": entity, "page": page_new}
    })
    
    return questions


def generate_negative_yesno(pairs_list, target_count, split_map):
    negatives = []
    neg_counter = 0
    
    entity_pages = {}
    pages_by_pair = {}
    
    for pair in pairs_list:
        entity = clean_entity(pair.get('change_desc_gt', ''))
        if not entity or "TODO" in entity or not is_valid_entity(entity):
            continue
            
        stable_pair_id = get_stable_pair_id(pair)
        
        if stable_pair_id not in pages_by_pair:
            pages_by_pair[stable_pair_id] = set()
        for k in ("page_index_old", "page_index_new"):
            if pair.get(k) is not None:
                pages_by_pair[stable_pair_id].add(int(pair[k]))
        
        page = pair.get('page_index_new', 0)
        if entity not in entity_pages:
            entity_pages[entity] = set()
        entity_pages[entity].add(page)
    
    entities = list(entity_pages.keys())
    if not entities:
        return negatives
    
    for pair in pairs_list:
        if len(negatives) >= target_count:
            break
            
        entity = clean_entity(pair.get('change_desc_gt', ''))
        if not entity or "TODO" in entity or not is_valid_entity(entity):
            continue
            
        stable_pair_id = get_stable_pair_id(pair)
        ver_old = pair.get('version_id_old', 'A')
        ver_new = pair.get('version_id_new', 'B')
        
        all_pages = pages_by_pair.get(stable_pair_id, set())
        unchanged_pages = all_pages - entity_pages.get(entity, set())
        
        for other_page in list(unchanged_pages)[:2]:
            if len(negatives) >= target_count:
                break
            
            neg_counter += 1
            unique_change_id = f"neg_{entity[:10].replace(' ', '_')}_{other_page}_{neg_counter:04d}"
            
            neg_q = {
                "qid": f"neg_{pair.get('pair_id', '')}_{other_page}_{neg_counter}",
                "pair_id": stable_pair_id,
                "change_id": unique_change_id,
                "question_type": "yesno",
                "question": f"On page {other_page}, did component {entity} change between {ver_old} and {ver_new}? Answer yes/no.",
                "answer": {"yes": False, "page": other_page},
                "evidence": [],
                "evidence_optional": True,
                "coord_frame": COORD_FRAME,
                "is_negative": True,
                "category": "Reasoning",
                "type": "Visual Inspection",
                "difficulty": "Hard",
                "split": split_map.get(stable_pair_id, "train"),
            }
            negatives.append(neg_q)
    
    return negatives


def augment_visualdiff(questions_path, pairs_path, output_path):
    print(f"Augmenting Visual Diff: {questions_path}...")
    questions = load_jsonl(questions_path)
    pairs_list = load_jsonl(pairs_path)
    pairs = {p['pair_id']: p for p in pairs_list}
    
    split_map = {}
    for q in questions:
        pair_id = q.get('pair_id')
        if pair_id and pair_id in pairs:
            stable_pair_id = get_stable_pair_id(pairs[pair_id])
            split_map[stable_pair_id] = q.get('split', 'train')
    
    augmented_data = []
    seen = set()
    filtered_count = 0
    
    for q in questions:
        pair_id = q.get('pair_id')
        if not pair_id or pair_id not in pairs:
            continue
            
        pair = pairs[pair_id]
        desc = pair.get('change_desc_gt', '')
        if "TODO" in str(desc) or not desc:
            continue 
        
        entity = clean_entity(desc)
        
        # Entity quality filter
        if not is_valid_entity(entity):
            filtered_count += 1
            continue
        
        stable_pair_id = get_stable_pair_id(pair)
        change_id = get_change_id(pair)
        
        question_variants = generate_visualdiff_questions(pair, entity)
        selected_q = random.choice(question_variants)
        
        evidence_list = []
        if pair.get('bbox_old'):
            evidence_list.append({
                "doc_side": "A",
                "version_id": pair.get('version_id_old'),
                "page": pair['page_index_old'],
                "bbox": pair['bbox_old'],
                "note": "old_version"
            })
        if pair.get('bbox_new'):
            evidence_list.append({
                "doc_side": "B",
                "version_id": pair.get('version_id_new'),
                "page": pair['page_index_new'],
                "bbox": pair['bbox_new'],
                "note": "new_version"
            })
        
        dedup_key = hashlib.md5(f"{selected_q['question']}_{stable_pair_id}".encode()).hexdigest()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        
        difficulty = compute_difficulty(pair, selected_q['question_type'])
        
        new_entry = {
            "qid": q['question_id'],
            "pair_id": stable_pair_id,
            "change_id": change_id,
            "question_type": selected_q['question_type'],
            "question": selected_q['question'],
            "answer": selected_q['answer'],
            "evidence": evidence_list,
            "coord_frame": COORD_FRAME,
            "category": "Reasoning",
            "type": "Visual Inspection",
            "difficulty": difficulty,
            "split": q.get('split', 'train')
        }
        
        augmented_data.append(new_entry)
    
    print(f"Filtered {filtered_count} low-quality entities")
    
    num_yesno_positives = sum(1 for q in augmented_data if q['question_type'] == 'yesno' and q['answer'].get('yes') == True)
    target_negatives = num_yesno_positives
    
    negatives = generate_negative_yesno(pairs_list, target_negatives, split_map)
    
    for neg in negatives[:target_negatives]:
        dedup_key = hashlib.md5(f"{neg['question']}_{neg['pair_id']}".encode()).hexdigest()
        if dedup_key not in seen:
            seen.add(dedup_key)
            augmented_data.append(neg)

    # Stats
    yesno_yes = sum(1 for q in augmented_data if q['question_type'] == 'yesno' and q['answer'].get('yes') == True)
    yesno_no = sum(1 for q in augmented_data if q['question_type'] == 'yesno' and q['answer'].get('yes') == False)
    locate_bbox = sum(1 for q in augmented_data if q['question_type'] == 'locate_bbox')
    change_subtype = sum(1 for q in augmented_data if q['question_type'] == 'change_subtype')
    
    diff_easy = sum(1 for q in augmented_data if q['difficulty'] == 'Easy')
    diff_med = sum(1 for q in augmented_data if q['difficulty'] == 'Medium')
    diff_hard = sum(1 for q in augmented_data if q['difficulty'] == 'Hard')
    
    unique_pairs = len(set(q['pair_id'] for q in augmented_data))
    
    print(f"Unique pair_ids: {unique_pairs}")
    print(f"Question types: yesno={yesno_yes+yesno_no} (yes={yesno_yes}, no={yesno_no}), locate_bbox={locate_bbox}, change_subtype={change_subtype}")
    print(f"Difficulty: Easy={diff_easy}, Medium={diff_med}, Hard={diff_hard}")
    print(f"Yes/No balance: {100*yesno_no/(yesno_yes+yesno_no+1e-6):.1f}% no")
    print(f"Saved {len(augmented_data)} visual_diff questions to {output_path}")
    save_jsonl(augmented_data, output_path)


if __name__ == "__main__":
    augment_microtext(MICROTEXT_QUESTIONS, MICROTEXT_ITEMS, OUTPUT_MICROTEXT)
    augment_visualdiff(VISUALDIFF_QUESTIONS, VISUALDIFF_PAIRS, OUTPUT_VISUALDIFF)
