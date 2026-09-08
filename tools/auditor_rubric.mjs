import fs from "node:fs";

export const rubric = JSON.parse(fs.readFileSync(new URL("./auditor_microtext_rubric.json", import.meta.url), "utf8"));

export function categoryHint(row) {
  const category = row.corrected_category || row.category || "unknown_microtext";
  return `类别：${category}\n${rubric.categories[category] || rubric.categories.unknown_microtext}`;
}

export function taskQuestion(row) {
  if (row.task === "microtext") {
    return [
      "完整目标工程标签的文字和类别都正确吗？",
      `机器建议：${row.machine_suggestion || "（空）"}`,
      categoryHint(row),
      "1=全部正确；2=任一项错误/目标明确缺字；3=看不清/证据不足",
    ].join("\n");
  }
  if (row.task === "visualdiff") {
    const activeGoldRecheck = row.assignment_origin === "paired_active_gold_release_recheck";
    return [
      activeGoldRecheck
        ? "新旧图片是否有真实工程变化，而且当前Gold描述准确吗？"
        : "新旧图片之间是否存在真实工程含义变化？",
      `${activeGoldRecheck ? "当前Gold描述" : "机器提示"}：${row.machine_suggestion || "（空）"}`,
      activeGoldRecheck
        ? "1=有真实变化且描述准确；2=无真实变化或描述不准；3=看不清"
        : "1=有工程变化；2=无（相同/仅偏移排版渲染）；3=看不清",
    ].join("\n");
  }
  throw new Error(`unknown task ${row.task}`);
}
