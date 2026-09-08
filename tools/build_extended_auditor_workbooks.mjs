#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";
import { rubric, taskQuestion } from "./auditor_rubric.mjs";

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error(`invalid arguments near ${key ?? "end"}`);
    }
    values[key.slice(2)] = value;
  }
  for (const required of ["payload", "output-dir", "preview-dir", "report"]) {
    if (!values[required]) throw new Error(`missing --${required}`);
  }
  return values;
}

function dataUrl(buffer) {
  return `data:image/png;base64,${buffer.toString("base64")}`;
}

function machineRow(row, position) {
  return [
    String(position),
    String(row.primary_index ?? ""),
    String(row.task ?? ""),
    String(row.record_id || row.candidate_id || row.pair_id || ""),
    String(row.display_index ?? ""),
    String(row.machine_suggestion ?? ""),
    String(row.evidence_path ?? ""),
    String(row.change_description ?? ""),
    String(row.corrected_text ?? ""),
    String(row.corrected_category ?? ""),
    String(row.reserved_split ?? ""),
    String(row.source_group ?? ""),
    String(row.evidence_sha256 ?? ""),
    String(row.assignment_origin ?? ""),
  ];
}

async function buildAuditor(auditor, outputDir, previewDir) {
  const workbook = Workbook.create();
  const sheetName = auditor.sheet_name || "审核24条";
  const machineName = auditor.machine_sheet_name || "机器数据_勿改";
  const sheet = workbook.worksheets.add(sheetName);
  const machine = workbook.worksheets.add(machineName);
  const rows = auditor.rows;
  if (rows.length !== 24) throw new Error(`auditor ${auditor.number}: expected 24 rows`);
  const continuationInstruction = Number(auditor.preserved_answers || 0) > 0
    ? "只填写黄色 D 列：输入 1/2/3 后按 Enter。已有答案请保留，只补空白格。"
    : "本轮24题全部为全新任务。只填写黄色 D 列：输入 1/2/3 后按 Enter。";

  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(6);
  sheet.mergeCells("A1:E1");
  sheet.mergeCells("A2:E2");
  sheet.mergeCells("A3:E3");
  sheet.mergeCells("A4:E4");
  sheet.getRange("A1:E6").values = [
    [`Eng_Bench Gold v2.0 独立复审 - 复审员 ${String(auditor.number).padStart(2, "0")}`, null, null, null, null],
    [continuationInstruction, null, null, null, null],
    ["MicroText：1=标签、文字、类别全部正确；2=任一项错误；3=看不清。VisualDiff：1=有工程变化；2=无；3=看不清。", null, null, null, null],
    [rubric.summary, null, null, null, null],
    ["完成进度", null, null, "目标", null],
    ["#", "证据图片", "判断问题", "答案 1/2/3", "状态"],
  ];
  sheet.getRange("E5").formulas = [[`=COUNTIF(D7:D30,">0")&" / 24"`]];

  const visibleValues = rows.map((row) => [
    String(row.display_index),
    "",
    taskQuestion(row),
    row.preserved_answer_code ? Number(row.preserved_answer_code) : null,
    null,
  ]);
  sheet.getRange("A7:E30").values = visibleValues;
  sheet.getRange("E7").formulas = [[`=IF(OR(D7=1,D7=2,D7=3),"完成","未答")`]];
  sheet.getRange("E7:E30").fillDown();

  sheet.getRange("A1:E1").format = {
    fill: "#134E4A",
    font: { bold: true, color: "#FFFFFF", size: 18 },
    verticalAlignment: "center",
    horizontalAlignment: "left",
  };
  sheet.getRange("A2:E4").format = {
    fill: "#ECFDF5",
    font: { color: "#134E4A", size: 10 },
    verticalAlignment: "center",
    horizontalAlignment: "left",
    wrapText: true,
  };
  sheet.getRange("A5:E5").format = {
    fill: "#CCFBF1",
    font: { bold: true, color: "#134E4A", size: 11 },
    verticalAlignment: "center",
  };
  sheet.getRange("D5:E5").format.horizontalAlignment = "center";
  sheet.getRange("A6:E6").format = {
    fill: "#0F766E",
    font: { bold: true, color: "#FFFFFF", size: 11 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "outside", style: "medium", color: "#0F766E" },
  };
  sheet.getRange("A7:E30").format = {
    font: { color: "#111827", size: 10 },
    verticalAlignment: "center",
    borders: {
      insideHorizontal: { style: "thin", color: "#D1D5DB" },
      bottom: { style: "thin", color: "#D1D5DB" },
    },
  };
  sheet.getRange("A7:A30").format.horizontalAlignment = "center";
  sheet.getRange("B7:B30").format.fill = "#F8FAFC";
  sheet.getRange("C7:C30").format = {
    wrapText: true,
    verticalAlignment: "center",
    horizontalAlignment: "left",
  };
  sheet.getRange("D7:D30").format = {
    fill: "#FFF2CC",
    font: { bold: true, color: "#7C2D12", size: 14 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("E7:E30").format = {
    fill: "#FEE2E2",
    font: { bold: true, color: "#991B1B", size: 10 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("D7:D30").dataValidation = {
    rule: { type: "list", values: [1, 2, 3] },
  };
  sheet.getRange("D7:D30").conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: 1,
    format: { fill: "#DCFCE7", font: { color: "#166534", bold: true } },
  });
  sheet.getRange("D7:D30").conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: 2,
    format: { fill: "#FEE2E2", font: { color: "#991B1B", bold: true } },
  });
  sheet.getRange("D7:D30").conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: 3,
    format: { fill: "#FFEDD5", font: { color: "#9A3412", bold: true } },
  });
  sheet.getRange("E7:E30").conditionalFormats.add("containsText", {
    text: "完成",
    format: { fill: "#DCFCE7", font: { color: "#166534", bold: true } },
  });

  sheet.getRange("A1:E1").format.rowHeightPx = 42;
  sheet.getRange("A2:E3").format.rowHeightPx = 36;
  sheet.getRange("A4:E4").format.rowHeightPx = 54;
  sheet.getRange("A5:E6").format.rowHeightPx = 28;
  sheet.getRange("A7:E30").format.rowHeightPx = 160;
  sheet.getRange("A1:A30").format.columnWidthPx = 44;
  sheet.getRange("B1:B30").format.columnWidthPx = 420;
  sheet.getRange("C1:C30").format.columnWidthPx = 440;
  sheet.getRange("D1:D30").format.columnWidthPx = 105;
  sheet.getRange("E1:E30").format.columnWidthPx = 82;

  for (let index = 0; index < rows.length; index += 1) {
    const evidence = await fs.readFile(rows[index].evidence_path);
    sheet.images.add({
      dataUrl: dataUrl(evidence),
      anchor: {
        from: { row: 6 + index, col: 1, rowOffset: 6, colOffset: 6 },
        extent: { widthPx: 405, heightPx: 148 },
      },
    });
  }

  const headers = [
    "canonical_position",
    "primary_index",
    "task",
    "record_id",
    "display_index",
    "machine_suggestion",
    "evidence_path",
    "original_change_description",
    "original_corrected_text",
    "original_corrected_category",
    "reserved_split",
    "source_group",
    "evidence_sha256",
    "assignment_origin",
  ];
  machine.getRange("A1:N25").values = [
    headers,
    ...rows.map((row, index) => machineRow(row, index + 1)),
  ];
  machine.getRange("A1:N1").format = {
    fill: "#334155",
    font: { bold: true, color: "#FFFFFF" },
  };
  machine.getRange("A1:N25").format.font = { size: 9 };
  machine.getRange("A1:N25").format.wrapText = false;
  machine.getRange("A1:N25").format.autofitColumns();

  const keyCheck = await workbook.inspect({
    kind: "table",
    range: `${sheetName}!A1:E10`,
    include: "values,formulas",
    tableMaxRows: 10,
    tableMaxCols: 5,
  });
  const errorCheck = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: `auditor ${auditor.number} formula error scan`,
  });

  const previewRanges = ["A1:E12", "A13:E21", "A22:E30"];
  const previewFiles = [];
  for (let index = 0; index < previewRanges.length; index += 1) {
    const blob = await workbook.render({
      sheetName,
      range: previewRanges[index],
      scale: 0.8,
      format: "png",
    });
    const preview = path.join(
      previewDir,
      `auditor_${String(auditor.number).padStart(2, "0")}_part_${index + 1}.png`,
    );
    await fs.writeFile(preview, new Uint8Array(await blob.arrayBuffer()));
    previewFiles.push(preview);
  }
  const machinePreview = await workbook.render({
    sheetName: machineName,
    range: "A1:F6",
    scale: 0.8,
    format: "png",
  });
  const machinePreviewPath = path.join(
    previewDir,
    `auditor_${String(auditor.number).padStart(2, "0")}_machine.png`,
  );
  await fs.writeFile(
    machinePreviewPath,
    new Uint8Array(await machinePreview.arrayBuffer()),
  );

  const output = path.join(outputDir, auditor.workbook);
  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(output);
  return {
    auditor: auditor.number,
    workbook: output,
    rows: rows.length,
    preservedAnswers: auditor.preserved_answers,
    previewFiles,
    machinePreview: machinePreviewPath,
    keyCheck: keyCheck.ndjson,
    errorCheck: errorCheck.ndjson,
  };
}

const args = parseArgs(process.argv.slice(2));
const payload = JSON.parse(await fs.readFile(args.payload, "utf8"));
await fs.mkdir(args["output-dir"], { recursive: true });
await fs.mkdir(args["preview-dir"], { recursive: true });
const reports = [];
for (const auditor of payload.auditors) {
  reports.push(await buildAuditor(auditor, args["output-dir"], args["preview-dir"]));
}
const report = {
  goal: "Gold v2.0 Global",
  workbooks: reports.length,
  rows: reports.reduce((sum, item) => sum + item.rows, 0),
  preservedAnswers: reports.reduce((sum, item) => sum + item.preservedAnswers, 0),
  goldRowsModified: 0,
  reports,
};
await fs.writeFile(args.report, `${JSON.stringify(report, null, 2)}\n`, "utf8");
process.stdout.write(`${JSON.stringify({
  goal: report.goal,
  workbooks: report.workbooks,
  rows: report.rows,
  preservedAnswers: report.preservedAnswers,
  goldRowsModified: 0,
}, null, 2)}\n`);
