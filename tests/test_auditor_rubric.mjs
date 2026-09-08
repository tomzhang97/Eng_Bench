import assert from "node:assert/strict";
import fs from "node:fs";
import { rubric, taskQuestion } from "../tools/auditor_rubric.mjs";

const guide = fs.readFileSync(new URL("../docs/HUMAN_REVIEW_CATEGORY_GUIDE.md", import.meta.url), "utf8");
const categories = [...guide.matchAll(/^\| `([a-z_]+)` \|/gm)].map(match => match[1]);
assert.equal(categories.length, 10);
for (const name of categories) assert.ok(rubric.categories[name], `missing category ${name}`);
assert.match(taskQuestion({task: "microtext", corrected_category: "pin_label", machine_suggestion: "C3"}), /R101\/C3\/Q2A/);
assert.match(taskQuestion({task: "microtext", category: "equipment_tag"}), /SUMP PUMP/);
assert.match(taskQuestion({task: "microtext", category: "component_value"}), /芯片型号不是/);
assert.match(taskQuestion({task: "microtext"}), /unknown_microtext/);
assert.match(taskQuestion({task: "visualdiff"}), /2=无（相同\/仅偏移排版渲染）/);
assert.throws(() => taskQuestion({task: "unsupported"}));
console.log("PASS: 10 guide categories, category-specific hints and unchanged VisualDiff code meanings");
