#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";


function arg(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) {
    throw new Error(`missing required argument ${name}`);
  }
  return process.argv[index + 1];
}


function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}


function statusFormula(excelRow, mandatoryRewrite) {
  if (mandatoryRewrite) {
    return `=IF(E${excelRow}="","未完成",IF(OR(E${excelRow}=3,E${excelRow}=4),IF(LEN(SUBSTITUTE(TRIM(I${excelRow})," ",""))<6,"需填工程依据","完成"),IF(E${excelRow}<>2,"必须选2重写",IF(F${excelRow}="","需填写类型",IF(G${excelRow}="","需填写描述",IF(LEN(SUBSTITUTE(TRIM(I${excelRow})," ",""))<6,"需填工程依据","完成"))))))`;
  }
  return `=IF(E${excelRow}="","未完成",IF(AND(E${excelRow}=2,COUNTA(F${excelRow}:G${excelRow})=0),"需填写修改",IF(AND(H${excelRow}<>"",LEN(SUBSTITUTE(TRIM(I${excelRow})," ",""))<6),"需填工程依据","完成")))`;
}


function microStatusFormula(excelRow) {
  return `=IF(E${excelRow}="","未完成",IF(AND(E${excelRow}=2,COUNTA(F${excelRow}:G${excelRow})=0),"需填写修改",IF(AND(H${excelRow}<>"",LEN(SUBSTITUTE(TRIM(I${excelRow})," ",""))<6),"需填工程依据","完成")))`;
}


async function main() {
  const inputWorkbook = path.resolve(arg("--input-workbook"));
  const payloadPath = path.resolve(arg("--payload"));
  const planPath = path.resolve(arg("--normalization-plan"));
  const outputWorkbook = path.resolve(arg("--output-workbook"));
  const reportPath = path.resolve(arg("--report"));
  const previewDir = path.resolve(arg("--preview-dir"));

  const inputBytes = await fs.readFile(inputWorkbook);
  const payloadBytes = await fs.readFile(payloadPath);
  const plan = JSON.parse(await fs.readFile(planPath, "utf8"));
  const payload = JSON.parse(payloadBytes.toString("utf8"));
  const summary = plan.summary || {};
  if (sha256(inputBytes) !== summary.input_workbook_sha256) {
    throw new Error("input workbook hash does not match normalization plan");
  }
  if (sha256(payloadBytes) !== summary.payload_sha256) {
    throw new Error("payload hash does not match normalization plan");
  }

  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputWorkbook));
  const visualRows = (payload.rows || []).filter((row) => row.task === "visualdiff");
  const visualSheet = workbook.worksheets.getItem("主审_VisualDiff");
  const lastVisualRow = visualRows.length + 6;
  const editable = visualSheet.getRange(`E7:I${lastVisualRow}`);
  const matrix = editable.values;

  for (const repair of plan.metadata_repairs || []) {
    if (repair.sheet !== "主审_VisualDiff") throw new Error("unexpected repair sheet");
    const offset = Number(repair.row) - 7;
    if (String(matrix[offset][3] ?? "") !== String(repair.source_value ?? "")) {
      throw new Error(`repair source drift at ${repair.cell}`);
    }
    matrix[offset][3] = repair.target_value;
  }
  for (const action of plan.visual_feedback_normalizations || []) {
    const offset = Number(action.row) - 7;
    if (
      String(matrix[offset][0] ?? "") !== action.source_decision_code ||
      String(matrix[offset][1] ?? "") !== action.source_corrected_change_type ||
      String(matrix[offset][2] ?? "") !== action.source_corrected_description
    ) {
      throw new Error(`normalization source drift at row ${action.row}`);
    }
    matrix[offset][0] = Number(action.target_decision_code);
    matrix[offset][1] = null;
    matrix[offset][2] = null;
  }
  editable.values = matrix;

  const formulas = visualRows.map((row, offset) => [
    statusFormula(offset + 7, Boolean(row.mandatory_description_rewrite)),
  ]);
  visualSheet.getRange(`J7:J${lastVisualRow}`).formulas = formulas;

  const microRows = (payload.rows || []).filter((row) => row.task === "microtext");
  const microSheet = workbook.worksheets.getItem("主审_MicroText");
  const microLastRow = microRows.length + 6;
  microSheet.getRange(`J7:J${microLastRow}`).formulas = microRows.map((row, offset) => [
    microStatusFormula(offset + 7),
  ]);
  const microIndex = new Map(microRows.map((row, index) => [row.record_id, index + 7]));
  const visualIndex = new Map(visualRows.map((row, index) => [row.record_id, index + 7]));
  const engineeringRows = (payload.rows || [])
    .filter((row) => Boolean(row.engineering_required))
    .sort((left, right) => Number(left.engineering_index) - Number(right.engineering_index));
  const engineeringFormulas = engineeringRows.map((row) => {
    const targetSheet = row.task === "microtext" ? "主审_MicroText" : "主审_VisualDiff";
    const targetRow = row.task === "microtext"
      ? microIndex.get(row.record_id)
      : visualIndex.get(row.record_id);
    if (!targetRow) throw new Error(`engineering target missing: ${row.record_id}`);
    return [`='${targetSheet}'!J${targetRow}`];
  });
  workbook.worksheets.getItem("工程任务清单")
    .getRange(`H7:H${engineeringRows.length + 6}`).formulas = engineeringFormulas;

  for (const row of plan.rework_rows || []) {
    const taskSheet = row.task === "microtext"
      ? workbook.worksheets.getItem("主审_MicroText")
      : visualSheet;
    const taskRows = (payload.rows || []).filter((item) => item.task === row.task);
    const index = taskRows.findIndex((item) => item.record_id === row.record_id);
    if (index < 0) throw new Error(`rework identity missing: ${row.record_id}`);
    const excelRow = index + 7;
    taskSheet.getRange(`I${excelRow}`).format = {
      fill: "#FCE8E6",
      font: { color: "#9C0006", bold: true },
      borders: { preset: "outside", style: "medium", color: "#C00000" },
      wrapText: true,
    };
  }

  const guide = workbook.worksheets.getItem("00说明");
  guide.getRange("A1").values = [["Eng_Bench Gold v2.0 Global - 主审最终续作批次"]];
  guide.getRange("A6").values = [[
    `本轮已严格处理回传：恢复 ${summary.metadata_repairs} 个被误删的机器工程原因，并将 ${summary.visual_feedback_normalizations} 个填错栏位的 VisualDiff 结论归一化。主表已有 ${summary.main_ready_after_normalization}/${summary.main_rows} 条有效决定；专项 18/18 已完成。现在只需筛选“完成状态=未完成”，完成剩余 ${summary.remaining_human_actions} 行。审核结果不会自动进入 Gold。`,
  ]];
  guide.getRange("A7").values = [[
    "VisualDiff 新规则：两图不是同一位置/对象，选 4 并在 I 列说明；同一区域但红框偏移、裁剪或渲染造成假差异且无真实工程变化，选 3 并在 I 列说明。不要把 unclear 或 layout_only_no_change 填入 F 列。",
  ]];
  guide.getRange("J4").values = [["5 / 9"]];
  guide.getRange("C12").values = [[
    "主审、工程任务清单和两个专项表的“完成状态”必须全部显示“完成”。337 条强制描述重写若有真实变化必须选 2 并填写类型、描述和工程依据；若无真实变化选 3，位置/对象不对应或证据不足选 4。不要删除、插入、排序或复制行。",
  ]];
  guide.getRange("E23").values = [["decision_3_no_change"]];
  guide.getRange("F23").values = [["同一区域仅位置、字体、清晰度、渲染或极小偏移；没有工程变化"]];
  guide.getRange("E24").values = [["decision_4_needs_context"]];
  guide.getRange("F24").values = [["两图位置/对象不对应，或证据不足，不能可靠判断"]];
  visualSheet.getRange("A2").values = [[
    `只处理 J 列不是“完成”的 ${summary.main_rework_after_normalization} 行。E=1 机器结论完全正确；E=2 有真实变化但类型/描述需改；E=3 同一区域无真实工程变化；E=4 两图位置/对象不对应或证据不足。E=3/4 不填 F/G，但工程深审行必须在 I 写清依据。`,
  ]];
  workbook.worksheets.getItem("工程任务清单").getRange("A2").values = [[
    "专项任务已完成。本页只用于定位主审工程行；筛选 H 列非“完成”，再到对应主审表补全 I 列工程依据。",
  ]];
  const engineeringLastRow = engineeringRows.length + 6;
  guide.getRange("B30").formulas = [[
    `=COUNT('主审_MicroText'!E7:E${microLastRow})+COUNT('主审_VisualDiff'!E7:E${lastVisualRow})&" / ${payload.rows.length}"`,
  ]];
  guide.getRange("D30").formulas = [[
    `=COUNTIF('主审_MicroText'!J7:J${microLastRow},"完成")+COUNTIF('主审_VisualDiff'!J7:J${lastVisualRow},"完成")&" / ${payload.rows.length}"`,
  ]];
  guide.getRange("F30").formulas = [[
    `=COUNTIF('工程任务清单'!H7:H${engineeringLastRow},"完成")&" / ${engineeringRows.length}"`,
  ]];
  guide.getRange("H30").formulas = [[`=COUNTIF('专项_英文描述15'!L7:L21,"完成")&" / 15"`]];
  guide.getRange("J30").formulas = [[`=COUNTIF('专项_MicroText3'!J7:J9,"完成")&" / 3"`]];

  const formulaErrors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "normalized primary continuation formula error scan",
  });
  const formulaScan = formulaErrors.ndjson.trim();
  if (formulaScan && !formulaScan.includes("matched 0 entries")) {
    throw new Error(`formula error scan returned matches: ${formulaErrors.ndjson.slice(0, 1200)}`);
  }

  await fs.mkdir(path.dirname(outputWorkbook), { recursive: true });
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(outputWorkbook);
  const outputBytes = await fs.readFile(outputWorkbook);

  await fs.mkdir(previewDir, { recursive: true });
  const previewSpecs = [
    ["00说明", "A1:J16", "00_instructions.png"],
    ["主审_VisualDiff", "A1:J12", "01_visualdiff_instructions.png"],
  ];
  const previews = [];
  for (const [sheetName, range, filename] of previewSpecs) {
    const blob = await workbook.render({ sheetName, range, scale: 1, format: "png" });
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const destination = path.join(previewDir, filename);
    await fs.writeFile(destination, bytes);
    previews.push({ sheet: sheetName, range, path: destination, bytes: bytes.length });
  }

  const report = {
    goal: "Gold v2.0 Global",
    input_workbook: inputWorkbook,
    input_sha256: sha256(inputBytes),
    payload: payloadPath,
    payload_sha256: sha256(payloadBytes),
    normalization_plan: planPath,
    output_workbook: outputWorkbook,
    output_bytes: outputBytes.length,
    output_sha256: sha256(outputBytes),
    metadata_repairs: (plan.metadata_repairs || []).length,
    visual_feedback_normalizations: (plan.visual_feedback_normalizations || []).length,
    remaining_human_actions: summary.remaining_human_actions,
    previews,
    formula_errors: 0,
    safe_to_merge_gold: false,
    gold_rows_modified: 0,
  };
  await fs.mkdir(path.dirname(reportPath), { recursive: true });
  await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(report, null, 2));
}


await main();
