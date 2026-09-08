#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const PRIMARY_NAME = "PRIMARY_REVIEW_498.xlsx";
const PRIMARY_SHEET = "\u5ba1\u6838498\u6761";
const AUDITOR_SHEET = "\u5ba1\u683812\u6761";

const CATEGORY_DETAILS = {
  dimension_value: {
    label: "\u5c3a\u5bf8\u503c",
    examples: "11.8\u00b0\u3001R10\u30013'-3\"\u30010.7500",
  },
  equipment_tag: {
    label: "\u8bbe\u5907\u6807\u7b7e",
    examples: "MOTOR\u3001PUMP\u3001BOILER NO. 1",
  },
  pin_label: {
    label: "\u5f15\u811a/\u7aef\u5b50/\u5143\u4ef6\u6807\u7b7e",
    examples: "L1\u3001R10\u3001Q3\u3001GP14\u30013V3",
  },
  pipe_line_tag: {
    label: "\u7ba1\u7ebf\u6807\u7b7e",
    examples: "DRAIN\u3001OIL TANK VENT\u3001PG05001-6\"",
  },
  room_label: {
    label: "\u623f\u95f4/\u533a\u57df\u6807\u7b7e",
    examples: "DECK\u3001KITCHEN\u3001HALL\u3001PLATFORM",
  },
};

const UNKNOWN_CATEGORY = {
  label: "\u672a\u77e5\u7c7b\u522b",
  examples: "\u65e0\u6cd5\u5bf9\u7167\u4e0a\u8ff0 5 \u7c7b",
};

const CATEGORY_QUICK_GUIDE =
  "\u7c7b\u522b\u4f8b\u5b50\uff1a\u5c3a\u5bf8=11.8\u00b0/R10/3'-3\"\uff1b\u8bbe\u5907=MOTOR/PUMP\uff1b\u5f15\u811a/\u5143\u4ef6=L1/Q3/GP14\uff1b" +
  "\u7ba1\u7ebf=DRAIN/PG05001-6\"\uff1b\u623f\u95f4/\u533a\u57df=DECK/KITCHEN\u3002R10 \u5fc5\u987b\u7ed3\u5408\u56fe\u4e2d\u4e0a\u4e0b\u6587\u3002";

const VISUALDIFF_TEXT_RULE =
  "\u5dee\u5f02\u6587\u5b57\uff1a\u5c3a\u5bf8/\u6807\u7b7e/\u623f\u540d/\u5de5\u7a0b\u6ce8\u91ca\u7684\u542b\u4e49\u6539\u53d8=\u771f\u5b9e\u5de5\u7a0b\u53d8\u5316\uff1b\u53ea\u6362\u5b57\u4f53/\u4f4d\u7f6e/\u6e05\u6670\u5ea6=\u65e0\u5de5\u7a0b\u53d8\u5316\u3002";

const CHANGE_LABELS = {
  dimension_change_candidate: "\u5c3a\u5bf8\u53d8\u5316",
  geometry_change_candidate: "\u51e0\u4f55\u53d8\u5316",
  geometry_or_dimension_change_candidate: "\u51e0\u4f55/\u5c3a\u5bf8\u53d8\u5316",
  schematic_change_candidate: "\u56fe\u5f62/\u7ebf\u8def\u53d8\u5316",
  text_added_candidate: "\u65b0\u589e\u6587\u5b57",
  text_change_candidate: "\u6587\u5b57\u6539\u53d8",
  text_removed_candidate: "\u5220\u9664\u6587\u5b57",
};

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) throw new Error(`unexpected argument: ${key}`);
    if (key === "--overwrite") {
      args.overwrite = true;
      continue;
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`missing value for ${key}`);
    args[key.slice(2)] = value;
    index += 1;
  }
  for (const required of ["source-dir", "payload", "output-dir", "preview-dir"]) {
    if (!args[required]) throw new Error(`missing --${required}`);
  }
  return args;
}

function parseMicrotext(row) {
  const lines = String(row.machine_suggestion ?? "").split(/\r?\n/);
  const text = (lines[0] ?? "").trim() || "(\u7a7a)";
  const categoryLine = lines.find((line) => line.includes("\u7c7b\u522b")) ?? "";
  const category = categoryLine.split(/[\uff1a:]/, 2)[1]?.trim() || "unknown";
  const details = CATEGORY_DETAILS[category] ?? UNKNOWN_CATEGORY;
  return {
    text,
    category,
    categoryLabel: details.label,
    categoryExamples: details.examples,
  };
}

function categoryDisplay(item) {
  return `${item.categoryLabel}\uff08\u4f8b\uff1a${item.categoryExamples}\uff09`;
}

function parseVisualDiff(row) {
  const lines = String(row.machine_suggestion ?? "").split(/\r?\n/);
  const changeType = (lines.shift() ?? "").trim();
  return {
    changeLabel: CHANGE_LABELS[changeType] ?? "\u672a\u5206\u7c7b\u53d8\u5316",
    description: String(row.change_description ?? "").trim() || "(\u65e0\u63cf\u8ff0)",
  };
}

function compactPrimaryPrompt(row) {
  if (row.task === "microtext") {
    const item = parseMicrotext(row);
    return `\u6587\u5b57\uff1a\u300c${item.text}\u300d\n\u7c7b\u522b\uff1a${categoryDisplay(item)}`;
  }
  const item = parseVisualDiff(row);
  return `\u53d8\u5316\uff1a${item.changeLabel}\n\u63cf\u8ff0\uff1a${item.description}`;
}

function compactAuditorPrompt(row) {
  if (row.task === "microtext") {
    const item = parseMicrotext(row);
    return `\u300c${item.text}\u300d  |  ${categoryDisplay(item)}`;
  }
  return "\u6846\u5185\u6709\u771f\u5b9e\u5de5\u7a0b\u53d8\u5316\uff1f";
}

function setPrimaryPresentation(sheet, rows) {
  if (rows.length !== 498) throw new Error(`expected 498 primary rows, got ${rows.length}`);
  const microCount = rows.filter((row) => row.task === "microtext").length;
  if (microCount !== 369) throw new Error(`expected 369 MicroText rows, got ${microCount}`);

  sheet.getRange("A1:G1").values = [[
    "Eng_Bench Gold v2.0 Global - \u4e3b\u5ba1 498 \u6761\uff08\u6bcf\u884c\u4e00\u4e2a\u6570\u5b57\uff09",
  ]];
  sheet.getRange("A2:G2").values = [[
    "\u770b B \u56fe + C \u673a\u5668\u7b54\u6848\uff0cD \u5217\u6309\u6570\u5b57 + Enter\uff1a1\u6b63\u786e  2\u6539\u6b63  3\u5254\u9664  4\u4e0d\u6e05\u695a\u3002\u53ea\u6709\u9009 2 \u624d\u586b E/F\u3002",
  ]];
  sheet.getRange("A3:G3").values = [[
    "\u6587\u5b57\u9898\uff1a\u622a\u56fe\u5b8c\u6574 + \u6587\u5b57/\u7c7b\u522b\u5168\u5bf9\u624d\u6309 1\u3002\u5dee\u5f02\u9898\uff1a\u6846\u5185\u6709\u771f\u5b9e\u53d8\u5316 + \u7c7b\u578b/\u63cf\u8ff0\u5168\u5bf9\u624d\u6309 1\u3002",
  ]];
  sheet.mergeCells("A4:G4");
  sheet.getRange("A4").values = [[CATEGORY_QUICK_GUIDE]];
  sheet.getRange("A5:G5").values = [[
    `${VISUALDIFF_TEXT_RULE} \u7f3a\u5b57/\u5212\u6389/\u65e0\u6548\uff0c\u6216\u4e24\u56fe\u76f8\u540c/\u8f7b\u5fae\u6574\u4f53\u504f\u79fb/\u6846\u5916\u53d8\u5316 = 3\uff1b\u9700\u8981\u66f4\u5927\u56fe = 4\u3002`,
  ]];
  sheet.getRange("A6:G6").values = [[
    "#",
    "\u8bc1\u636e\u56fe\u7247",
    "\u53ea\u56de\u7b54\u8fd9\u4e2a\u95ee\u9898",
    "\u6309 1/2/3/4",
    "\u6b63\u786e\u6587\u5b57 / \u53d8\u5316\u63cf\u8ff0\uff08\u4ec5 2\uff09",
    "\u6b63\u786e\u7c7b\u522b\uff08\u4ec5\u6587\u5b57\u9898\u4e14 2\uff09",
    "\u5b8c\u6210",
  ]];
  sheet.getRange("C7:C504").values = rows.map((row) => [compactPrimaryPrompt(row)]);

  sheet.getRange("A:A").format.columnWidthPx = 42;
  sheet.getRange("B:B").format.columnWidthPx = 300;
  sheet.getRange("C:C").format.columnWidthPx = 335;
  sheet.getRange("D:D").format.columnWidthPx = 102;
  sheet.getRange("E:E").format.columnWidthPx = 205;
  sheet.getRange("F:F").format.columnWidthPx = 132;
  sheet.getRange("G:G").format.columnWidthPx = 72;
  sheet.getRange("A1:G1").format.rowHeightPx = 32;
  sheet.getRange("A2:G2").format.rowHeightPx = 30;
  sheet.getRange("A3:G3").format.rowHeightPx = 28;
  sheet.getRange("A4:G4").format.rowHeightPx = 42;
  sheet.getRange("A5:G5").format.rowHeightPx = 42;
  sheet.getRange("A6:G6").format.rowHeightPx = 38;
  sheet.getRange("A7:G375").format.rowHeightPx = 108;
  sheet.getRange("A376:G504").format.rowHeightPx = 150;
  sheet.getRange("C7:C504").format = {
    fill: "#F4F6F8",
    font: { color: "#17202A", size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("A4:G4").format = {
    fill: "#EAF4EA",
    font: { color: "#174A2B", size: 9, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(1);
}

function setAuditorPresentation(sheet, auditor) {
  const rows = auditor.rows;
  if (rows.length !== 12) throw new Error(`auditor ${auditor.number}: expected 12 rows`);
  const number = String(auditor.number).padStart(2, "0");
  sheet.getRange("A1:E1").values = [[
    `Eng_Bench Gold v2.0 Global - \u590d\u6838 ${number}\uff08\u53ea\u6709 12 \u6761\uff09`,
  ]];
  sheet.getRange("A2:E2").values = [[
    "\u770b B \u56fe + C \u5185\u5bb9\uff0cD \u5217\u6309\u6570\u5b57 + Enter\uff1a1\u5bf9  2\u9519  3\u4e0d\u6e05\u695a\u3002\u4e0d\u6539\u5176\u4ed6\u683c\u3002",
  ]];
  sheet.getRange("A3:E3").values = [[
    "\u6587\u5b57\uff1a\u622a\u56fe\u5b8c\u6574 + \u6587\u5b57/\u7c7b\u522b\u5168\u5bf9\u624d\u6309 1\u3002\u5dee\u5f02\uff1a\u6846\u5185\u6709\u771f\u5b9e\u53d8\u5316\u624d\u6309 1\uff1b\u76f8\u540c/\u8f7b\u5fae\u504f\u79fb/\u6846\u5916 = 2\u3002",
  ]];
  sheet.mergeCells("A4:E4");
  sheet.getRange("A4").values = [[CATEGORY_QUICK_GUIDE]];
  sheet.getRange("A5:E5").values = [[
    `${VISUALDIFF_TEXT_RULE} 12 / 12 \u540e Ctrl+S\uff0c\u539f\u6587\u4ef6\u540d\u4ea4\u56de\u3002`,
  ]];
  sheet.getRange("A6:E6").values = [[
    "#",
    "\u8bc1\u636e\u56fe\u7247",
    "\u53ea\u56de\u7b54\u8fd9\u4e2a\u95ee\u9898",
    "\u7b54\u6848 1/2/3",
    "\u5b8c\u6210",
  ]];
  sheet.getRange("C7:C18").values = rows.map((row) => [compactAuditorPrompt(row)]);

  sheet.getRange("A:A").format.columnWidthPx = 42;
  sheet.getRange("B:B").format.columnWidthPx = 292;
  sheet.getRange("C:C").format.columnWidthPx = 278;
  sheet.getRange("D:D").format.columnWidthPx = 96;
  sheet.getRange("E:E").format.columnWidthPx = 58;
  sheet.getRange("A1:E1").format.rowHeightPx = 34;
  sheet.getRange("A2:E2").format.rowHeightPx = 28;
  sheet.getRange("A3:E3").format.rowHeightPx = 32;
  sheet.getRange("A4:E4").format.rowHeightPx = 44;
  sheet.getRange("A5:E5").format.rowHeightPx = 40;
  sheet.getRange("A6:E6").format.rowHeightPx = 32;
  sheet.getRange("A7:E18").format.rowHeightPx = 116;
  sheet.getRange("C7:C18").format = {
    fill: "#F4F6F8",
    font: { color: "#17202A", size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("A4:E4").format = {
    fill: "#EAF4EA",
    font: { color: "#174A2B", size: 9, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.freezePanes.freezeRows(6);
}

async function savePreview(workbook, sheetName, range, outputPath, scale = 1) {
  const rendered = await workbook.render({ sheetName, range, scale, format: "png" });
  await fs.writeFile(outputPath, new Uint8Array(await rendered.arrayBuffer()));
}

async function compactWorkbook(source, destination, previewDir, role, rows, auditor = null) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
  const sheet = workbook.worksheets.getItemAt(0);
  if (role === "primary") {
    if (sheet.name !== PRIMARY_SHEET) throw new Error(`${source}: unexpected primary sheet ${sheet.name}`);
    setPrimaryPresentation(sheet, rows);
  } else {
    if (sheet.name !== AUDITOR_SHEET) throw new Error(`${source}: unexpected auditor sheet ${sheet.name}`);
    setAuditorPresentation(sheet, auditor);
  }

  const formulaErrors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: `${role} formula error scan`,
  });
  if (formulaErrors.ndjson.includes('"kind":"match"')) {
    throw new Error(`${source}: formula error found`);
  }
  await fs.mkdir(previewDir, { recursive: true });
  const inspect = await workbook.inspect({
    kind: "table",
    range: role === "primary" ? `${PRIMARY_SHEET}!A1:G11` : `${AUDITOR_SHEET}!A1:E18`,
    tableMaxRows: role === "primary" ? 11 : 18,
    tableMaxCols: role === "primary" ? 7 : 5,
  });
  await fs.writeFile(path.join(previewDir, "inspect.ndjson"), `${inspect.ndjson}\n${formulaErrors.ndjson}`, "utf8");
  if (role === "primary") {
    await savePreview(workbook, PRIMARY_SHEET, "A1:G11", path.join(previewDir, "start.png"), 1.2);
    await savePreview(workbook, PRIMARY_SHEET, "A372:G379", path.join(previewDir, "transition.png"), 1.0);
    await savePreview(workbook, PRIMARY_SHEET, "A499:G504", path.join(previewDir, "end.png"), 1.0);
  } else {
    await savePreview(workbook, AUDITOR_SHEET, "A1:E18", path.join(previewDir, "review.png"), 0.9);
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(destination);
  await fs.rm(`${destination}.inspect.ndjson`, { force: true });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const sourceDir = path.resolve(args["source-dir"]);
  const outputDir = path.resolve(args["output-dir"]);
  const previewDir = path.resolve(args["preview-dir"]);
  const payload = JSON.parse(await fs.readFile(path.resolve(args.payload), "utf8"));
  if (path.normalize(sourceDir) === path.normalize(outputDir)) {
    throw new Error("source and output directories must differ");
  }
  if (args.overwrite) {
    await fs.rm(outputDir, { recursive: true, force: true });
    await fs.rm(previewDir, { recursive: true, force: true });
  }
  await fs.mkdir(outputDir, { recursive: true });
  await fs.mkdir(previewDir, { recursive: true });

  await compactWorkbook(
    path.join(sourceDir, PRIMARY_NAME),
    path.join(outputDir, PRIMARY_NAME),
    path.join(previewDir, "PRIMARY_REVIEW_498"),
    "primary",
    payload.primary.rows,
  );
  for (const auditor of payload.auditors) {
    await compactWorkbook(
      path.join(sourceDir, auditor.workbook),
      path.join(outputDir, auditor.workbook),
      path.join(previewDir, path.parse(auditor.workbook).name),
      "auditor",
      auditor.rows,
      auditor,
    );
  }

  const report = {
    goal: "Gold v2.0 Global",
    source_dir: sourceDir,
    output_dir: outputDir,
    primary_rows: payload.primary.rows.length,
    primary_microtext_rows: payload.primary.rows.filter((row) => row.task === "microtext").length,
    primary_visualdiff_rows: payload.primary.rows.filter((row) => row.task === "visualdiff").length,
    auditors: payload.auditors.length,
    rows_per_auditor: 12,
    independent_rows: payload.auditors.reduce((total, item) => total + item.rows.length, 0),
    assignment_changed: false,
    gold_rows_modified: 0,
    workflow: "one-screen compact numeric-key audit",
  };
  await fs.writeFile(path.join(outputDir, "compact_build_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

await main();
