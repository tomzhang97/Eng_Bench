# Eng_Bench Gap Analysis: Path to Global Reusability

**Date**: 2026-01-21
**Status**: Comprehensive evaluation against MuSiQue and HotpotQA benchmarks
**Current State**: v0.9 Silver (Automated Seeds + Incomplete Annotations)

## Executive Summary

Eng_Bench is a specialized engineering document understanding benchmark with strong infrastructure but **critical gaps preventing global reusability**. Comparing to comprehensive datasets like MuSiQue (25K samples) and HotpotQA (113K samples), Eng_Bench lacks:

1. **Complete annotations** (64% have placeholder answers)
2. **Scale** (1,545 samples vs 25K-113K)
3. **Diversity** (2 document sources vs broad coverage)
4. **Reasoning transparency** (no supporting facts/evidence chains)
5. **Community accessibility** (no HuggingFace/standard distribution)

**Critical Finding**: 952 of 1,488 visual-diff annotations (64%) contain "CHANGE_DESC_GT_TODO" placeholders, making the dataset unusable for training or meaningful evaluation.

---

## 1. Dataset Completeness

### What's Missing

#### 1.1 Incomplete Ground Truth Annotations
- **Critical Issue**: 952/1,488 (64%) visual-diff pairs have placeholder answers: `"CHANGE_DESC_GT_TODO"`
- **Impact**: Cannot train models or establish meaningful baselines
- **MuSiQue/HotpotQA**: 100% complete, human-verified annotations

**Example from Eng_Bench**:
```json
{
  "pair_id": "vdiff__viola__pcbV1.0__to__pcbV1.1__0000",
  "change_desc_gt": "CHANGE_DESC_GT_TODO",  // ❌ Placeholder
  "change_type": ["symbol"],
  "severity": "unknown"  // ❌ Not annotated
}
```

**Compare to HotpotQA**:
```json
{
  "id": "5a8b57f25542995d1e6f1371",
  "answer": "Chief of Staff of the United States Army",  // ✅ Complete
  "supporting_facts": {  // ✅ Evidence provided
    "title": ["William Mulligan (general)", "Army War College"],
    "sent_id": [0, 2]
  },
  "level": "hard",  // ✅ Difficulty rated
  "type": "bridge"  // ✅ Question type classified
}
```

#### 1.2 Missing Metadata Richness
**Eng_Bench gaps**:
- No difficulty levels (easy/medium/hard)
- No reasoning complexity scores
- Many null fields: `object_id`, `entity_id`, `board_id`
- Generic severity: "unknown" (not human-verified)
- Limited change_type taxonomy

**MuSiQue/HotpotQA include**:
- Difficulty levels (easy/medium/hard)
- Question types (bridge, comparison, intersection)
- Reasoning complexity (2-hop, 3-hop, 4-hop)
- Supporting facts with sentence-level granularity
- Answer types (span, entity, yes/no)

---

## 2. Scale and Diversity

### Current State
| Dataset | Total Samples | Train | Dev/Test | Document Sources | Domains |
|---------|--------------|-------|----------|------------------|---------|
| **Eng_Bench** | 1,545 | 526 | 1,019 | 2 (BBB, Viola) | 1 (Electronics) |
| **MuSiQue** | 25,000 | 19,918 | 2,417/2,665 | 5+ sources | General knowledge |
| **HotpotQA** | 113,000 | 90,564 | 7,405/7,405 | Wikipedia (all) | General knowledge |

### What's Missing

#### 2.1 Limited Document Diversity
**Current sources**:
- `bbb`: BeagleBone Black schematics (single board family)
- `viola`: Toradex Viola datasheets (single board family)
- `tolerances_table_iso`: ISO tolerance tables (57 samples)

**Impact**:
- Cannot generalize to other engineering domains (mechanical, civil, chemical)
- Cannot generalize to different document types (CAD, architectural, process diagrams)
- Overfits to PCB/electronics domain

**What comprehensive datasets have**:
- **MuSiQue**: Multiple QA sources (SQuAD, Natural Questions, TREx, MLQA, ZeRoRe)
- **HotpotQA**: Full Wikipedia coverage (5.2M+ articles available)

#### 2.2 Insufficient Sample Size
- **1,545 samples** is insufficient for:
  - Training modern vision-language models (need 10K+)
  - Establishing reliable performance distributions
  - Supporting meaningful cross-domain evaluation
  - Creating robust train/dev/test splits

**Industry standard**: 10K-100K+ samples for production benchmarks

---

## 3. Task Design and Reasoning

### What's Missing

#### 3.1 No Explicit Reasoning Chains
**Eng_Bench approach**:
- Asks: "What changed between V1.0 and V1.1?"
- Expects: Direct answer
- Provides: Bounding boxes only

**Missing**:
- Step-by-step reasoning requirements
- Multi-hop inference chains
- Explicit evidence selection
- Reasoning complexity levels

**HotpotQA approach**:
```json
{
  "question": "Which magazine was started first Arthur's Magazine or First for Women?",
  "supporting_facts": {
    "title": ["Arthur's Magazine", "First for Women"],  // ✅ Evidence required
    "sent_id": [0, 0]  // ✅ Specific sentences
  },
  "type": "comparison"  // ✅ Reasoning type
}
```

**MuSiQue approach**:
- Decomposes into atomic reasoning steps
- Each step depends on previous answers
- Provides full reasoning graph (DAG structure)
- Includes unanswerable contrast questions

#### 3.2 No Question Type Diversity
**Eng_Bench has**:
- Visual diff detection (single type)
- Microtext OCR (single type)

**MuSiQue/HotpotQA have**:
- Bridge questions (connect two entities)
- Comparison questions (compare attributes)
- Intersection questions (find common entities)
- Yes/no questions
- Span extraction
- Numerical reasoning

#### 3.3 No Unanswerable Questions
**Critical gap**: All Eng_Bench questions are answerable
- Cannot test model calibration
- Cannot evaluate hallucination detection
- Cannot measure confidence estimation

**MuSiQue-Full**: Includes unanswerable contrast questions to test robustness

---

## 4. Evaluation Framework

### What's Missing

#### 4.1 No Standard Metrics
**Eng_Bench mentions**:
- mAP@0.5 (for detection)
- Exact Match (for microtext)
- "Traceability Score" (undefined)

**Missing**:
- F1 scores
- Supporting fact metrics
- Reasoning correctness
- Human evaluation correlation
- Calibration metrics

**HotpotQA defines**:
- Answer EM (Exact Match)
- Answer F1
- Supporting Fact EM
- Supporting Fact F1
- Joint EM/F1 (answer + supporting facts)

#### 4.2 No Baselines or Leaderboards
**Eng_Bench has**:
- No published baselines
- No human performance benchmarks
- No model comparison infrastructure
- No public leaderboard

**MuSiQue/HotpotQA have**:
- Multiple baseline models (BERT, GPT, specialized architectures)
- Human performance upper bounds
- Active leaderboards
- Regular benchmark challenges

---

## 5. Accessibility and Usability

### What's Missing

#### 5.1 No Standard Distribution
**Current state**:
- Raw JSONL files in GitHub repo
- Manual loading required
- No versioning strategy
- No standard data loaders

**What comprehensive datasets have**:
- **HuggingFace Datasets**: One-line loading
  ```python
  from datasets import load_dataset
  dataset = load_dataset("hotpotqa/hotpot_qa")
  ```
- **Kaggle**: Public datasets with kernels
- **Papers with Code**: Integrated benchmarks
- **Standard splits**: Predefined train/dev/test

#### 5.2 Unclear Licensing
**Eng_Bench**: `[To be determined]`
- Cannot use for commercial applications
- Cannot redistribute
- Cannot extend or modify
- Legal uncertainty

**HotpotQA**: CC BY-SA 4.0 (permissive, clear)
**MuSiQue**: CC BY 4.0 (permissive, clear)

#### 5.3 Limited Documentation
**Missing**:
- Annotation guidelines (partial in CVAT_TAXONOMY.md)
- Inter-annotator agreement statistics
- Quality control procedures
- Error analysis
- Known limitations
- Use case examples
- Tutorial notebooks

---

## 6. Domain Generalizability

### Critical Limitation: Single-Domain Focus

**Eng_Bench is specialized for**:
- Electronics/PCB documents only
- Specific document types (schematics, datasheets)
- Visual diff detection (narrow task)

**Impact**:
- Cannot generalize to other engineering domains
- Cannot serve as general document understanding benchmark
- Limited research impact outside niche area

**What makes MuSiQue/HotpotQA globally reusable**:
- **Domain-agnostic**: Works across all knowledge domains
- **Task-agnostic**: Tests general reasoning, not domain-specific skills
- **Source-diverse**: Multiple data sources prevent overfitting
- **Skill-general**: Tests reading comprehension, not domain expertise

---

## 7. Data Quality and Validation

### What's Missing

#### 7.1 No Human Evaluation Baselines
**Questions**:
- What is human performance on this task?
- How do experts compare to novices?
- What is inter-annotator agreement?

**Without this**:
- Cannot establish performance ceiling
- Cannot validate task difficulty
- Cannot calibrate model performance

#### 7.2 No Quality Control Metrics
**Missing**:
- Inter-annotator agreement (IAA) scores
- Annotation time per sample
- Rejection/revision rates
- Conflict resolution procedures
- Expert validation protocols

**MuSiQue/HotpotQA report**:
- Multiple annotators per sample
- Agreement statistics
- Expert validation
- Quality filtering criteria

#### 7.3 Automated Seed Data Issues
**v0.9 Silver status**:
- Annotations "derived from automated seeds (mocked as human labels)"
- Placeholders everywhere ("CHANGE_DESC_GT_TODO", "unknown")
- No human verification

**Risk**: Dataset may contain systematic errors from automation

---

## 8. Recommended Improvements (Priority Order)

### 🔴 Critical (Blockers for Release)

1. **Complete all ground truth annotations** (952 TODOs)
   - Human annotation of change descriptions
   - Verify bounding boxes
   - Classify change types and severity
   - Estimate: 950+ hours of expert time

2. **Establish clear licensing** (CC-BY-4.0 recommended)
   - Verify source document licenses
   - Clear derivative work terms
   - Commercial use policy

3. **Create human performance baselines**
   - Expert annotator performance
   - Inter-annotator agreement
   - Time per sample benchmarks

### 🟡 High Priority (For Reusability)

4. **Expand document diversity** (10x minimum)
   - Add 5+ engineering document types
   - Include non-electronics domains (mechanical, civil, chemical)
   - Add 10+ document families per domain
   - Target: 15,000+ samples

5. **Add reasoning transparency**
   - Supporting facts/evidence spans
   - Multi-hop reasoning chains
   - Reasoning complexity levels

6. **Expand task diversity**
   - Question type taxonomy (comparison, bridge, counting)
   - Unanswerable questions (20-30% of dataset)
   - Multiple difficulty levels
   - Numerical reasoning tasks

7. **Create standard distribution**
   - HuggingFace Datasets integration
   - Standard data loaders (PyTorch, TensorFlow)
   - Versioned releases (semantic versioning)

### 🟢 Medium Priority (For Adoption)

8. **Build evaluation infrastructure**
   - Reference implementations
   - Baseline model results
   - Public leaderboard
   - Evaluation server

9. **Improve documentation**
   - Annotation guidelines (full)
   - Tutorial notebooks
   - Use case examples
   - Known limitations

10. **Add metadata richness**
    - Difficulty ratings
    - Reasoning complexity scores
    - Answer type taxonomy
    - Document type tags

---

## 9. Comparison Summary

| Feature | Eng_Bench (v0.9) | MuSiQue | HotpotQA | Gap |
|---------|-----------------|---------|----------|-----|
| **Scale** | 1,545 | 25,000 | 113,000 | 16x-73x smaller |
| **Completion** | 36% | 100% | 100% | 64% incomplete |
| **Document Sources** | 2 | 5+ | Wikipedia | Very limited |
| **Domains** | 1 | General | General | Single-domain |
| **Question Types** | 2 | Multi-hop | Bridge/Compare | Limited variety |
| **Reasoning Chains** | ❌ | ✅ | ✅ | Missing |
| **Supporting Facts** | ❌ | ✅ | ✅ | Missing |
| **Difficulty Levels** | ❌ | Implicit | ✅ | Missing |
| **Unanswerable Qs** | ❌ | ✅ | ❌ | Missing |
| **Human Baselines** | ❌ | ✅ | ✅ | Missing |
| **HuggingFace** | ❌ | ✅ | ✅ | Not integrated |
| **License** | TBD | CC-BY-4.0 | CC-BY-SA-4.0 | Unclear |
| **Leaderboard** | ❌ | ✅ | ✅ | Missing |
| **Documentation** | Partial | ✅ | ✅ | Incomplete |

---

## 10. Strategic Recommendations

### Path Forward: Two Options

#### Option A: Niche Benchmark (Achievable)
**Goal**: Become the standard for engineering document understanding
**Scope**: Deep, narrow focus on engineering domains
**Requirements**:
1. Complete all annotations (6 months)
2. Expand to 5K samples across 5 engineering domains (12 months)
3. Build evaluation infrastructure (3 months)
4. Integrate with HuggingFace (1 month)
5. Establish licensing (1 month)

**Outcome**: Valuable specialized benchmark for engineering AI

#### Option B: General Document Understanding (Ambitious)
**Goal**: Compete with MuSiQue/HotpotQA as general benchmark
**Scope**: Broad, document-agnostic reasoning
**Requirements**:
1. All Option A requirements
2. Expand to 50K+ samples (36 months)
3. Add 20+ document types across domains
4. Multi-hop reasoning chains
5. Comprehensive metadata
6. Active community building

**Outcome**: General-purpose visual reasoning benchmark

### Recommended: **Option A** (Niche Excellence)
**Rationale**:
- Engineering document understanding is valuable and underserved
- Achievable with focused resources
- Clearer success criteria
- Faster time to impact
- Can evolve to Option B later

---

## 11. Conclusion

**Eng_Bench has strong foundations**:
- ✅ Solid technical infrastructure
- ✅ Clear schema design
- ✅ Automated pipeline
- ✅ Validation tools

**But critical gaps prevent global reusability**:
- ❌ 64% incomplete annotations
- ❌ Insufficient scale (100x smaller)
- ❌ Limited diversity (single domain)
- ❌ No reasoning transparency
- ❌ Missing evaluation framework
- ❌ Poor accessibility

**To become globally reusable like MuSiQue/HotpotQA, Eng_Bench needs**:
1. **Complete all annotations** (critical blocker)
2. **10x scale increase** minimum
3. **Domain expansion** beyond electronics
4. **Standard distribution** (HuggingFace)
5. **Evaluation infrastructure** (baselines, leaderboard)
6. **Clear licensing** (permissive)

**Estimated effort**: 24-36 months with dedicated team

**Current state**: Research prototype, not production benchmark

---

## References

- [MuSiQue: Multihop Questions via Single-hop Question Composition (TACL 2022)](https://aclanthology.org/2022.tacl-1.31/)
- [MuSiQue GitHub Repository](https://github.com/StonyBrookNLP/musique)
- [HotpotQA: A Dataset for Diverse, Explainable Multi-hop Question Answering](https://hotpotqa.github.io/)
- [HotpotQA on HuggingFace](https://huggingface.co/datasets/hotpotqa/hotpot_qa)
- [HotpotQA Paper (EMNLP 2018)](https://aclanthology.org/D18-1259/)

---

**Prepared by**: Claude (Sonnet 4.5)
**Review Status**: Ready for team discussion
**Next Steps**: Prioritize critical improvements and establish timeline
