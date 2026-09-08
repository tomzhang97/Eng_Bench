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


function byTask(rows, task) {
  return rows.filter((row) => row.task === task);
}


function applyCarryover(sheet, payloadRows, decisionsById, task) {
  const lastRow = payloadRows.length + 6;
  const editable = sheet.getRange(`E7:I${lastRow}`);
  const matrix = editable.values;
  let matched = 0;
  let ready = 0;
  let rework = 0;
  const reworkRows = [];
  for (let offset = 0; offset < payloadRows.length; offset += 1) {
    const payload = payloadRows[offset];
    const decision = decisionsById.get(payload.record_id);
    if (!decision) continue;
    matched += 1;
    matrix[offset][0] = Number(decision.decision_code);
    if (task === "microtext") {
      matrix[offset][1] = decision.corrected_text || null;
      matrix[offset][2] = decision.corrected_category || null;
    } else {
      matrix[offset][1] = decision.corrected_change_type || null;
      matrix[offset][2] = decision.corrected_description || null;
    }
    matrix[offset][4] = decision.engineering_basis || null;
    if (decision.ready) {
      ready += 1;
    } else {
      rework += 1;
      reworkRows.push(offset + 7);
    }
  }
  editable.values = matrix;
  for (const rowNumber of reworkRows) {
    sheet.getRange(`I${rowNumber}`).format = {
      fill: "#FCE8E6",
      font: { color: "#9C0006", bold: true },
      borders: { preset: "outside", style: "medium", color: "#C00000" },
      wrapText: true,
    };
    sheet.getRange(`J${rowNumber}`).formulas = [[
      `=IF(AND(E${rowNumber}<>"",LEN(SUBSTITUTE(TRIM(I${rowNumber})," ",""))>=6),"完成","未完成")`,
    ]];
  }
  return { matched, ready, rework, reworkRows };
}


async function renderPreviews(workbook, outputDir, microLastRow, visualLastRow, firstReworkRow) {
  await fs.mkdir(outputDir, { recursive: true });
  const specs = [
    ["00说明", "A1:J30", "00_instructions.png"],
    ["主审_MicroText", "A1:J12", "01_microtext_start.png"],
    ["主审_MicroText", `A${Math.max(7, microLastRow - 6)}:J${microLastRow}`, "02_microtext_end.png"],
    ["主审_VisualDiff", "A1:J12", "03_visualdiff_start.png"],
    ["主审_VisualDiff", `A${Math.max(7, firstReworkRow - 2)}:J${firstReworkRow + 2}`, "04_visualdiff_rework.png"],
    ["主审_VisualDiff", `A${Math.max(7, visualLastRow - 6)}:J${visualLastRow}`, "05_visualdiff_end.png"],
    ["工程任务清单", "A1:H12", "06_engineering_start.png"],
    ["专项_英文描述15", "A1:L21", "07_english_specialist.png"],
    ["专项_MicroText3", "A1:J9", "08_microtext_specialist.png"],
    ["机器数据_勿改", "A1:O12", "09_machine_main.png"],
    ["机器数据_专项勿改", "A1:I19", "10_machine_specialist.png"],
  ];
  const outputs = [];
  for (const [sheetName, range, name] of specs) {
    const blob = await workbook.render({ sheetName, range, scale: 1, format: "png" });
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const destination = path.join(outputDir, name);
    await fs.writeFile(destination, bytes);
    outputs.push({ sheet: sheetName, range, path: destination, bytes: bytes.length });
  }
  return outputs;
}


async function main() {
  const inputWorkbook = path.resolve(arg("--input-workbook"));
  const payloadPath = path.resolve(arg("--payload"));
  const reconciliationPath = path.resolve(arg("--reconciliation"));
  const outputWorkbook = path.resolve(arg("--output-workbook"));
  const reportPath = path.resolve(arg("--report"));
  const previewDir = path.resolve(arg("--preview-dir"));

  const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
  const reconciliation = JSON.parse(await fs.readFile(reconciliationPath, "utf8"));
  const decisions = reconciliation.rows || [];
  const summary = reconciliation.summary || {};
  const decisionsById = new Map(decisions.map((row) => [row.record_id, row]));
  if (decisionsById.size !== decisions.length) {
    throw new Error("reconciliation contains duplicate record IDs");
  }
  if (summary.matched_current_rows !== decisions.length) {
    throw new Error("reconciliation matched-row count does not match its rows");
  }
  const mainRows = payload.rows || [];
  const microRows = byTask(mainRows, "microtext");
  const visualRows = byTask(mainRows, "visualdiff");
  const specialistTotal = Number(payload.counts?.specialist_total || 0);
  if (mainRows.length !== 4000 || microRows.length !== 2478 || visualRows.length !== 1522) {
    throw new Error("unexpected current primary payload shape");
  }

  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputWorkbook));
  const requiredSheets = [
    "00说明",
    "主审_MicroText",
    "主审_VisualDiff",
    "工程任务清单",
    "专项_英文描述15",
    "专项_MicroText3",
    "机器数据_勿改",
    "机器数据_专项勿改",
  ];
  for (const sheetName of requiredSheets) {
    workbook.worksheets.getItem(sheetName);
  }

  const micro = applyCarryover(
    workbook.worksheets.getItem("主审_MicroText"),
    microRows,
    decisionsById,
    "microtext",
  );
  const visual = applyCarryover(
    workbook.worksheets.getItem("主审_VisualDiff"),
    visualRows,
    decisionsById,
    "visualdiff",
  );
  const matched = micro.matched + visual.matched;
  const ready = micro.ready + visual.ready;
  const rework = micro.rework + visual.rework;
  const remaining = mainRows.length - ready + specialistTotal;
  if (
    matched !== summary.matched_current_rows ||
    ready !== summary.ready_carryover_rows ||
    rework !== summary.carryover_rework_rows ||
    remaining !== summary.remaining_human_actions
  ) {
    throw new Error("workbook carryover totals do not match reconciliation contract");
  }

  const guide = workbook.worksheets.getItem("00说明");
  guide.getRange("A1").values = [["Eng_Bench Gold v2.0 Global - 主审续作批次"]];
  guide.getRange("A6").values = [[
    `已安全保留上一份回收表中 ${matched.toLocaleString("en-US")} 条仍属于当前任务的答案，其中 ${ready.toLocaleString("en-US")} 条已通过回收校验，${rework} 条仍需补工程依据。旧表中 24 条已被新版任务替换，不会复制。现在只需筛选“完成状态=未完成”，继续完成剩余 ${remaining.toLocaleString("en-US")} 个动作；不要重做已完成行。审核结果仍不会自动进入 Gold。`,
  ]];
  guide.getRange("A13").values = [["6"]];
  guide.getRange("B13").values = [["保存交回"]];
  guide.getRange("C13").values = [[
    "建议每完成 100 条按 Ctrl+S。使用桌面版 Excel/WPS，保持当前文件名和 .xlsx 格式；只交回这一份续作工作簿。",
  ]];

  const microSheet = workbook.worksheets.getItem("主审_MicroText");
  microSheet.getRange("A2").values = [[
    "上一份有效答案已保留。先在 J 列筛选“未完成”，只处理这些行：看 B 图 + C 机器内容，在黄色 E 列填 1/2/3/4；选 2 时在 F/G 至少改一项；H 有工程原因时必须填写 I。",
  ]];
  const visualSheet = workbook.worksheets.getItem("主审_VisualDiff");
  visualSheet.getRange("A2").values = [[
    `上一份有效答案已保留。先在 J 列筛选“未完成”，只处理这些行；其中 ${rework} 条已有判断但缺有效工程依据，I 列以红色标出。337 条【强制工程描述重写】必须选 2，并填写 F、G、I。`,
  ]];
  workbook.worksheets.getItem("工程任务清单").getRange("A2").values = [[
    "本页不重复填写答案。先筛选 H 列“未完成”，按目标工作表和主审行位置定位；上一份已通过的工程依据已经保留。",
  ]];

  const formulaErrors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "continuation workbook formula error scan",
  });
  const formulaScan = formulaErrors.ndjson.trim();
  if (formulaScan && !formulaScan.includes("matched 0 entries")) {
    throw new Error(`formula error scan returned matches: ${formulaErrors.ndjson.slice(0, 1200)}`);
  }

  await fs.mkdir(path.dirname(outputWorkbook), { recursive: true });
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(outputWorkbook);
  const outputBytes = await fs.readFile(outputWorkbook);
  const previews = await renderPreviews(
    workbook,
    previewDir,
    microRows.length + 6,
    visualRows.length + 6,
    visual.reworkRows[0] || 7,
  );
  const report = {
    goal: "Gold v2.0 Global",
    input_workbook: inputWorkbook,
    input_payload: payloadPath,
    reconciliation: reconciliationPath,
    output_workbook: outputWorkbook,
    output_bytes: outputBytes.length,
    output_sha256: sha256(outputBytes),
    main_rows: mainRows.length,
    specialist_rows: specialistTotal,
    matched_returned_rows: matched,
    ready_carryover_rows: ready,
    carryover_rework_rows: rework,
    remaining_human_actions: remaining,
    microtext: micro,
    visualdiff: visual,
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
