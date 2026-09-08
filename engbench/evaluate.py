def compute_iou(bbox1, bbox2):
    x1, y1, x2, y2 = bbox1
    x1_p, y1_p, x2_p, y2_p = bbox2
    
    xi1 = max(x1, x1_p)
    yi1 = max(y1, y1_p)
    xi2 = min(x2, x2_p)
    yi2 = min(y2, y2_p)
    
    inter_area = max(0, xi2 - xi1) * max(0, yi2 - yi1)
    
    box1_area = (x2 - x1) * (y2 - y1)
    box2_area = (x2_p - x1_p) * (y2_p - y1_p)
    
    union_area = box1_area + box2_area - inter_area
    if union_area == 0:
        return 0.0
    return inter_area / union_area

def evaluate(predictions, dataset_dict):
    """
    Evaluates predictions against the loaded dataset dictionary.
    predictions -> list of dicts:
     {"question_id": "...", "answer": "...", "evidence_bboxes": [[x1,y1,x2,y2], ...]}
    """
    qs_by_id = {q["question_id"]: q for q in dataset_dict["questions"]}
    
    total = 0
    em_correct = 0
    f1_sum = 0
    correct_ev_rate_sum = 0
    iou_sum = 0.0
    has_ev_count = 0
    
    for pred in predictions:
        qid = pred["question_id"]
        if qid not in qs_by_id:
            continue
            
        gt_q = qs_by_id[qid]
        total += 1
        
        # 1. Answer Quality
        pred_ans = str(pred.get("answer", "")).strip().lower()
        gt_ans = str(gt_q.get("answer", "")).strip().lower()
        
        is_em = (pred_ans == gt_ans)
        if is_em:
            em_correct += 1
            f1_sum += 1.0 # simplistic F1
        else:
            # simple token F1
            pred_toks = set(pred_ans.split())
            gt_toks = set(gt_ans.split())
            common = pred_toks.intersection(gt_toks)
            if len(common) > 0:
                prec = len(common) / len(pred_toks)
                rec = len(common) / len(gt_toks)
                f1_sum += 2 * (prec * rec) / (prec + rec)
                
        # 2. Evidence Quality
        gt_evs = [dataset_dict["evidence"][ev_id] for ev_id in gt_q.get("evidence_ids", []) if ev_id in dataset_dict["evidence"]]
        pred_evs = pred.get("evidence_bboxes", [])
        
        has_correct_ev = False
        best_iou = 0.0
        
        if len(gt_evs) > 0:
            has_ev_count += 1
            for p_bbox in pred_evs:
                for gt_ev in gt_evs:
                    iou = compute_iou(p_bbox, gt_ev["bbox"])
                    if iou > best_iou:
                        best_iou = iou
                    if iou >= 0.5:
                        has_correct_ev = True
            iou_sum += best_iou
                
        if is_em and has_correct_ev:
            correct_ev_rate_sum += 1

    if total == 0:
        return {}

    return {
        "Answer EM": em_correct / total if total > 0 else 0,
        "Answer F1": f1_sum / total if total > 0 else 0,
        "Evidence IoU": iou_sum / has_ev_count if has_ev_count > 0 else 0,
        "Answer-with-correct-evidence rate": correct_ev_rate_sum / total if total > 0 else 0,
        "Total": total
    }
