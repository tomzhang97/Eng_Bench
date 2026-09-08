#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error(`invalid arguments near ${key ?? "end"}`);
    }
    args[key.slice(2)] = value;
  }
  for (const required of ["manifest", "output", "preview", "report"]) {
    if (!args[required]) throw new Error(`missing --${required}`);
  }
  return args;
}

function readJsonl(text) {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

function dataUrl(buffer) {
  return `data:image/png;base64,${buffer.toString("base64")}`;
}

function localEvidence(manifestPath, value) {
  const packRoot = path.dirname(manifestPath);
  const normalized = String(value || "").replaceAll("\\", "/");
  const marker = "/derived/review_packs/";
  const absolute = path.resolve(normalized);
  if (path.isAbsolute(normalized)) return absolute;
  const packName = path.basename(packRoot);
  const segments = normalized.split("/");
  const packIndex = segments.lastIndexOf(packName);
  if (packIndex >= 0) return path.join(packRoot, ...segments.slice(packIndex + 1));
  if (normalized.includes(marker)) {
    return path.join(packRoot, normalized.split(marker)[1].split("/").slice(1).join(path.sep));
  }
  return path.join(packRoot, path.basename(path.dirname(normalized)), path.basename(normalized));
}

function suggestion(row) {
  return [
    `机器变化类型：${row.change_type || "unknown"}`,
    `机器描述：${row.description || row.change_desc_gt || "（空）"}`,
    `数据划分：${row.reserved_split || row.split || "provisional_review"}`,
  ].join("\n");
}

const args = parseArgs(process.argv.slice(2));
const manifestPath = path.resolve(args.manifest);
const rows = readJsonl(await fs.readFile(manifestPath, "utf8"));
if (rows.length < 1 || rows.length > 25) {
  throw new Error(`expected 1-25 confirmation rows, got ${rows.length}`);
}

const workbook = Workbook.create();
const sheetName = `工程确认${rows.length}条`;
const sheet = workbook.worksheets.add(sheetName);
const machine = workbook.worksheets.add("机器数据_勿改");
sheet.showGridLines = false;
sheet.freezePanes.freezeRows(6);
sheet.mergeCells("A1:G1");
sheet.mergeCells("A2:G2");
sheet.mergeCells("A3:G3");
sheet.mergeCells("A4:G4");
sheet.getRange("A1:G6").values = [
  ["Eng_Bench Gold v2.0 - VisualDiff 新家族工程确认", null, null, null, null, null, null],
  ["逐行查看 OLD/NEW 图片，只填写黄色区域。valid=机器结论完全正确；edit=有真实变化但机器描述需要改；reject_unclear=无变化、仅偏移/渲染或证据不足；needs_full_page=必须看整页。", null, null, null, null, null, null],
  ["edit 时必须在 E 列写清楚红框内实际发生的工程变化。完全相同或只有整体轻微偏移，不算工程变化，应选 reject_unclear。", null, null, null, null, null, null],
  ["这些结果只用于候选审核；不会自动写入 Gold。不要修改图片、机器建议或机器数据工作表。", null, null, null, null, null, null],
  ["完成进度", null, null, null, null, "目标", null],
  ["#", "OLD / NEW 证据", "机器建议", "human_status", "修正后的工程描述", "备注", "状态"],
];
const endRow = 6 + rows.length;
sheet.getRange("G5").formulas = [[
  `=(COUNTIF(D7:D${endRow},"valid")+COUNTIF(D7:D${endRow},"edit")+COUNTIF(D7:D${endRow},"reject_unclear")+COUNTIF(D7:D${endRow},"needs_full_page"))&" / ${rows.length}"`,
]];
sheet.getRange(`A7:G${endRow}`).values = rows.map((row, index) => [
  index + 1,
  "",
  suggestion(row),
  "",
  "",
  "",
  null,
]);
sheet.getRange("G7").formulas = [[`=IF(D7="","未答","完成")`]];
sheet.getRange(`G7:G${endRow}`).fillDown();

sheet.getRange("A1:G1").format = {
  fill: "#134E4A",
  font: { bold: true, color: "#FFFFFF", size: 18 },
  verticalAlignment: "center",
};
sheet.getRange("A2:G4").format = {
  fill: "#ECFDF5",
  font: { color: "#134E4A", size: 10 },
  wrapText: true,
  verticalAlignment: "center",
};
sheet.getRange("A5:G5").format = {
  fill: "#CCFBF1",
  font: { bold: true, color: "#134E4A", size: 11 },
  verticalAlignment: "center",
};
sheet.getRange("F5:G5").format.horizontalAlignment = "center";
sheet.getRange("A6:G6").format = {
  fill: "#0F766E",
  font: { bold: true, color: "#FFFFFF", size: 11 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  borders: { preset: "outside", style: "medium", color: "#0F766E" },
};
sheet.getRange(`A7:G${endRow}`).format = {
  font: { color: "#111827", size: 10 },
  verticalAlignment: "center",
  borders: {
    insideHorizontal: { style: "thin", color: "#D1D5DB" },
    bottom: { style: "thin", color: "#D1D5DB" },
  },
};
sheet.getRange(`A7:A${endRow}`).format.horizontalAlignment = "center";
sheet.getRange(`B7:B${endRow}`).format.fill = "#F8FAFC";
sheet.getRange(`C7:C${endRow}`).format = { wrapText: true, verticalAlignment: "center" };
sheet.getRange(`D7:F${endRow}`).format = {
  fill: "#FFF2CC",
  wrapText: true,
  verticalAlignment: "center",
};
sheet.getRange(`D7:D${endRow}`).format = {
  fill: "#FFF2CC",
  font: { bold: true, color: "#7C2D12", size: 10 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
sheet.getRange(`G7:G${endRow}`).format = {
  fill: "#FEE2E2",
  font: { bold: true, color: "#991B1B", size: 10 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
sheet.getRange(`D7:D${endRow}`).dataValidation = {
  rule: { type: "list", values: ["valid", "edit", "reject_unclear", "needs_full_page"] },
};
for (const [text, fill, color] of [
  ["valid", "#DCFCE7", "#166534"],
  ["edit", "#FEF3C7", "#92400E"],
  ["reject_unclear", "#FEE2E2", "#991B1B"],
  ["needs_full_page", "#E0E7FF", "#3730A3"],
]) {
  sheet.getRange(`D7:D${endRow}`).conditionalFormats.add("containsText", {
    text,
    format: { fill, font: { color, bold: true } },
  });
}
sheet.getRange(`G7:G${endRow}`).conditionalFormats.add("containsText", {
  text: "完成",
  format: { fill: "#DCFCE7", font: { color: "#166534", bold: true } },
});

sheet.getRange("A1:G1").format.rowHeightPx = 42;
sheet.getRange("A2:G4").format.rowHeightPx = 42;
sheet.getRange("A5:G6").format.rowHeightPx = 28;
sheet.getRange(`A7:G${endRow}`).format.rowHeightPx = 190;
sheet.getRange(`A1:A${endRow}`).format.columnWidthPx = 44;
sheet.getRange(`B1:B${endRow}`).format.columnWidthPx = 520;
sheet.getRange(`C1:C${endRow}`).format.columnWidthPx = 370;
sheet.getRange(`D1:D${endRow}`).format.columnWidthPx = 145;
sheet.getRange(`E1:E${endRow}`).format.columnWidthPx = 330;
sheet.getRange(`F1:F${endRow}`).format.columnWidthPx = 220;
sheet.getRange(`G1:G${endRow}`).format.columnWidthPx = 82;

for (let index = 0; index < rows.length; index += 1) {
  const panelPath = localEvidence(manifestPath, rows[index].panel_path);
  const panel = await fs.readFile(panelPath);
  sheet.images.add({
    dataUrl: dataUrl(panel),
    anchor: {
      from: { row: 6 + index, col: 1, rowOffset: 8, colOffset: 8 },
      extent: { widthPx: 500, heightPx: 174 },
    },
  });
}

const machineHeaders = [
  "review_index",
  "pair_id",
  "project_id",
  "change_type",
  "machine_description",
  "reserved_split",
  "panel_path",
  "old_page_path",
  "new_page_path",
  "source_candidate_id",
];
machine.getRange(`A1:J${rows.length + 1}`).values = [
  machineHeaders,
  ...rows.map((row, index) => [
    index + 1,
    String(row.pair_id || ""),
    String(row.project_id || ""),
    String(row.change_type || ""),
    String(row.description || row.change_desc_gt || ""),
    String(row.reserved_split || row.split || ""),
    String(row.panel_path || ""),
    String(row.old_page_path || ""),
    String(row.new_page_path || ""),
    String(row.source_candidate_id || ""),
  ]),
];
machine.getRange("A1:J1").format = {
  fill: "#334155",
  font: { bold: true, color: "#FFFFFF", size: 9 },
};
machine.getRange(`A1:J${rows.length + 1}`).format.font = { size: 9 };
machine.getRange(`A1:J${rows.length + 1}`).format.autofitColumns();

const keyCheck = await workbook.inspect({
  kind: "table",
  range: `${sheetName}!A1:G${endRow}`,
  include: "values,formulas",
  tableMaxRows: endRow,
  tableMaxCols: 7,
});
const errorCheck = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "VisualDiff family confirmation formula error scan",
});
const preview = await workbook.render({
  sheetName,
  range: `A1:G${endRow}`,
  scale: 0.75,
  format: "png",
});
await fs.mkdir(path.dirname(path.resolve(args.preview)), { recursive: true });
await fs.writeFile(args.preview, new Uint8Array(await preview.arrayBuffer()));
await fs.mkdir(path.dirname(path.resolve(args.output)), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(args.output);

const report = {
  goal: "Gold v2.0 Global",
  workbook: path.resolve(args.output),
  sheet: sheetName,
  rows: rows.length,
  embeddedImages: sheet.images.items.length,
  goldRowsModified: 0,
  keyCheck: keyCheck.ndjson,
  errorCheck: errorCheck.ndjson,
};
await fs.writeFile(args.report, `${JSON.stringify(report, null, 2)}\n`, "utf8");
process.stdout.write(`${JSON.stringify({ rows: report.rows, embeddedImages: report.embeddedImages, goldRowsModified: 0 }, null, 2)}\n`);
