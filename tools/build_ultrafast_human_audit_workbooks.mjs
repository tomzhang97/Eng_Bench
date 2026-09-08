#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const COLORS = {
  teal: "#155E63",
  tealLight: "#DDEEEF",
  gold: "#B45309",
  yellow: "#FFF2CC",
  paleYellow: "#FFF9E6",
  green: "#DCFCE7",
  red: "#FEE2E2",
  blue: "#DBEAFE",
  grey: "#F4F6F8",
  line: "#D8DEE4",
  text: "#17202A",
  white: "#FFFFFF",
};

const CATEGORY_LABELS = {
  dimension_value: "尺寸值",
  equipment_tag: "设备标签",
  pin_label: "引脚/端子标签",
  pipe_line_tag: "管线标签",
  room_label: "房间/区域标签",
};

const CHANGE_LABELS = {
  dimension_change_candidate: "尺寸变化",
  geometry_change_candidate: "几何变化",
  geometry_or_dimension_change_candidate: "几何/尺寸变化",
  schematic_change_candidate: "图形/线路变化",
  text_added_candidate: "新增文字",
  text_change_candidate: "文字改变",
  text_removed_candidate: "删除文字",
};

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) throw new Error(`unexpected argument: ${key}`);
    if (
      key === "--overwrite" ||
      key === "--auditors-only" ||
      key === "--universal-primary" ||
      key === "--single-sheet-primary" ||
      key === "--lean-single-sheet-primary"
    ) {
      args[key.slice(2)] = true;
      continue;
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`missing value for ${key}`);
    args[key.slice(2)] = value;
    index += 1;
  }
  for (const required of ["payload", "output-dir", "preview-dir"]) {
    if (!args[required]) throw new Error(`missing --${required}`);
  }
  if (args["single-sheet-primary"] && args["lean-single-sheet-primary"]) {
    throw new Error("choose either --single-sheet-primary or --lean-single-sheet-primary");
  }
  return args;
}

function pngDimensions(buffer) {
  if (
    buffer.length < 24 ||
    buffer[0] !== 0x89 ||
    buffer.toString("ascii", 1, 4) !== "PNG"
  ) {
    throw new Error("evidence image is not a PNG");
  }
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

const evidenceCache = new Map();

async function evidence(pathname) {
  if (!evidenceCache.has(pathname)) {
    const buffer = await fs.readFile(pathname);
    const { width, height } = pngDimensions(buffer);
    evidenceCache.set(pathname, {
      dataUrl: `data:image/png;base64,${buffer.toString("base64")}`,
      width,
      height,
      sha256: crypto.createHash("sha256").update(buffer).digest("hex"),
    });
  }
  return evidenceCache.get(pathname);
}

function fitImage(dimensions, maxWidth, maxHeight) {
  const scale = Math.min(maxWidth / dimensions.width, maxHeight / dimensions.height);
  return {
    widthPx: Math.max(18, Math.round(dimensions.width * scale)),
    heightPx: Math.max(18, Math.round(dimensions.height * scale)),
  };
}

function styleTitle(range) {
  range.format = {
    fill: COLORS.teal,
    font: { bold: true, color: COLORS.white, size: 18, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
}

function styleHeader(range) {
  range.format = {
    fill: COLORS.teal,
    font: { bold: true, color: COLORS.white, size: 10, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.line },
  };
}

function styleData(range) {
  range.format = {
    fill: COLORS.grey,
    font: { color: COLORS.text, size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: COLORS.line },
  };
}

function addDecisionFormatting(range, codeMap) {
  for (const [code, fill] of Object.entries(codeMap)) {
    range.conditionalFormats.add("cellIs", {
      operator: "equal",
      formula: Number(code),
      format: { fill, font: { bold: true, color: COLORS.text } },
    });
  }
}

function rowStatusFormula(decisionCell, requiredCells, editedCode) {
  const required = requiredCells
    ? `IF(${decisionCell}=${Number(editedCode)},IF(COUNTA(${requiredCells})>0,"完成","需填写改正"),"完成")`
    : '"完成"';
  return `=IF(${decisionCell}="","未完成",${required})`;
}

async function addEvidenceImages(sheet, rows, startExcelRow, maxWidth, maxHeight) {
  for (let offset = 0; offset < rows.length; offset += 1) {
    const item = await evidence(rows[offset].evidence_path);
    const extent = fitImage(item, maxWidth, maxHeight);
    const excelRow = startExcelRow + offset;
    sheet.images.add({
      dataUrl: item.dataUrl,
      anchor: {
        from: { row: excelRow - 1, col: 1, rowOffsetPx: 7, colOffsetPx: 7 },
        extent,
      },
    });
  }
}

async function addAuditorEvidenceImages(sheet, rows, startExcelRow) {
  for (let offset = 0; offset < rows.length; offset += 1) {
    const row = rows[offset];
    const item = await evidence(row.evidence_path);
    const maxWidth = 350;
    const maxHeight = row.task === "visualdiff" ? 185 : 130;
    const extent = fitImage(item, maxWidth, maxHeight);
    const excelRow = startExcelRow + offset;
    sheet.images.add({
      dataUrl: item.dataUrl,
      anchor: {
        from: { row: excelRow - 1, col: 1, rowOffsetPx: 7, colOffsetPx: 7 },
        extent,
      },
    });
  }
}

function machineRows(rows) {
  return rows.map((row, index) => [
    index + 1,
    row.primary_index,
    row.task,
    row.candidate_id || row.pair_id,
    row.display_index,
    row.machine_suggestion,
    row.evidence_path,
    row.change_description || "",
    row.corrected_text || "",
    row.corrected_category || "",
  ]);
}

function addMachineSheet(workbook, rows) {
  const sheet = workbook.worksheets.add("机器数据_勿改");
  sheet.showGridLines = false;
  const values = [
    [
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
    ],
    ...machineRows(rows),
  ];
  sheet.getRangeByIndexes(0, 0, values.length, values[0].length).values = values;
  styleHeader(sheet.getRange("A1:J1"));
  sheet.getRange(`A2:J${values.length}`).format = {
    font: { size: 9, name: "Consolas", color: COLORS.text },
    wrapText: false,
  };
  sheet.getRange("A:J").format.columnWidthPx = 120;
  sheet.getRange("F:J").format.columnWidthPx = 260;
  return sheet;
}

const SINGLE_PRIMARY_SHEET = "审核498条";

function parseMicrotextSuggestion(machineSuggestion) {
  const lines = String(machineSuggestion ?? "").split(/\r?\n/);
  const text = (lines[0] ?? "").trim() || "（空）";
  const categoryLine = lines.find((line) => line.includes("类别")) ?? "";
  const category = categoryLine.split(/[：:]/, 2)[1]?.trim() || "unknown";
  return {
    text,
    category,
    categoryLabel: CATEGORY_LABELS[category] ?? "未知类别",
  };
}

function singlePrimarySuggestion(row) {
  if (row.task === "microtext") {
    const item = parseMicrotextSuggestion(row.machine_suggestion);
    return `【文字题】图片完整，且图中文字为“${item.text}”、类别为“${item.categoryLabel}”吗？`;
  }
  const lines = String(row.machine_suggestion ?? "").split(/\r?\n/);
  const changeType = (lines.shift() ?? "").trim();
  const changeLabel = CHANGE_LABELS[changeType] ?? "未分类变化";
  const evidence = lines.join(" | ").trim();
  const description = row.change_description
    ? row.change_description
    : "（机器未给描述）";
  const evidenceText = evidence ? ` | 依据：${evidence}` : "";
  return `【差异题】框内有真实工程变化，且机器判断完全正确吗？\n机器判断：${changeLabel}${evidenceText} | 描述：${description}`;
}

async function addSinglePrimaryEvidenceImages(sheet, rows, startExcelRow) {
  for (let offset = 0; offset < rows.length; offset += 1) {
    const row = rows[offset];
    const item = await evidence(row.evidence_path);
    const maxHeight = row.task === "microtext" ? 98 : 142;
    const extent = fitImage(item, 300, maxHeight);
    const excelRow = startExcelRow + offset;
    sheet.images.add({
      dataUrl: item.dataUrl,
      anchor: {
        from: { row: excelRow - 1, col: 1, rowOffsetPx: 7, colOffsetPx: 7 },
        extent,
      },
    });
  }
}

async function writeSinglePrimary(workbook, rows) {
  const sheet = workbook.worksheets.add(SINGLE_PRIMARY_SHEET);
  sheet.showGridLines = false;
  const firstDataRow = 7;
  const lastDataRow = firstDataRow + rows.length - 1;
  const microRows = rows.filter((row) => row.task === "microtext").length;
  const lastMicroRow = firstDataRow + microRows - 1;
  const firstVisualRow = lastMicroRow + 1;

  sheet.mergeCells("A1:H1");
  sheet.getRange("A1").values = [[
    "Eng_Bench Gold v2.0 Global - 主审实习生连续审核 498 条",
  ]];
  styleTitle(sheet.getRange("A1:H1"));
  sheet.getRange("A1:H1").format.rowHeightPx = 38;

  sheet.mergeCells("A2:H2");
  sheet.getRange("A2").values = [[
    "最快操作：看 B 列图片和 D 列机器内容，在黄色 E 列按 1/2/3/4 + Enter。通常只按 1；只有选 2 才填写自动变黄的修改格。",
  ]];
  sheet.getRange("A2:H2").format = {
    fill: COLORS.yellow,
    font: { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("A2:H2").format.rowHeightPx = 42;

  sheet.mergeCells("A3:H3");
  sheet.getRange("A3").values = [[
    "1=全对；2=样本有效但机器内容有错；3=样本无效或无真实变化；4=看不清、需要更大范围。不要删除、排序或新增行。",
  ]];
  sheet.getRange("A3:H3").format = {
    fill: COLORS.tealLight,
    font: { bold: true, color: COLORS.teal, size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("A3:H3").format.rowHeightPx = 34;

  sheet.getRange("A4:D4").values = [["进度", "", "交回检查", ""]];
  styleHeader(sheet.getRange("A4:D4"));
  sheet.getRange("B4").formulas = [[`=COUNTIF(E${firstDataRow}:E${lastDataRow},"<>")&" / ${rows.length}"`]];
  sheet.getRange("D4").formulas = [[
    `=IF(COUNTIF(H${firstDataRow}:H${lastDataRow},"完成")=${rows.length},"可以交回","还差 "&(${rows.length}-COUNTIF(H${firstDataRow}:H${lastDataRow},"完成"))&" 条")`,
  ]];
  sheet.getRange("B4:D4").format = {
    fill: COLORS.grey,
    font: { bold: true, color: COLORS.text, size: 10, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: COLORS.line },
  };
  sheet.getRange("D4").conditionalFormats.add("containsText", {
    text: "可以交回",
    format: { fill: COLORS.green, font: { bold: true, color: "#166534" } },
  });

  sheet.mergeCells("A5:H5");
  sheet.getRange("A5").values = [[
    "易错项：截图少字母，即使机器猜出完整词也选 3；明确删除线划掉的标签选 3；VisualDiff 两图相同、仅整体轻微偏移/渲染差异或变化在框外，也选 3。",
  ]];
  sheet.getRange("A5:H5").format = {
    fill: COLORS.paleYellow,
    font: { color: "#7C4A03", size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("A5:H5").format.rowHeightPx = 34;

  sheet.getRange("A6:H6").values = [[
    "#",
    "证据图片",
    "类型（勿改）",
    "只看这里：问题 + 机器内容（勿改）",
    "按 1/2/3/4",
    "正确文字 / 变化描述（仅 2）",
    "正确类别（仅文字题且 2）",
    "完成",
  ]];
  styleHeader(sheet.getRange("A6:H6"));
  sheet.getRange("A6:H6").format.rowHeightPx = 48;

  const values = rows.map((row) => [
    Number(row.primary_index),
    "",
    row.task === "microtext" ? "文字" : "差异",
    singlePrimarySuggestion(row),
    "",
    "",
    "",
    "",
  ]);
  sheet.getRangeByIndexes(firstDataRow - 1, 0, values.length, 8).values = values;
  styleData(sheet.getRange(`A${firstDataRow}:H${lastDataRow}`));
  sheet.getRange(`E${firstDataRow}:E${lastDataRow}`).format = {
    fill: COLORS.yellow,
    font: { bold: true, size: 14, name: "Microsoft YaHei", color: COLORS.text },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange(`F${firstDataRow}:G${lastDataRow}`).format.fill = COLORS.grey;
  sheet.getRange(`C${firstDataRow}:C${lastDataRow}`).format = {
    fill: COLORS.tealLight,
    font: { bold: true, size: 10, name: "Microsoft YaHei", color: COLORS.teal },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange(`E${firstDataRow}:E${lastDataRow}`).dataValidation = {
    rule: { type: "list", values: [1, 2, 3, 4] },
  };
  sheet.getRange(`G${firstDataRow}:G${lastMicroRow}`).dataValidation = {
    rule: {
      type: "list",
      values: ["dimension_value", "equipment_tag", "pin_label", "pipe_line_tag", "room_label"],
    },
  };
  sheet.getRange(`F${firstDataRow}:F${lastDataRow}`).conditionalFormats.addCustom(
    `=$E${firstDataRow}=2`,
    { fill: COLORS.paleYellow, font: { color: COLORS.text } },
  );
  sheet.getRange(`G${firstDataRow}:G${lastMicroRow}`).conditionalFormats.addCustom(
    `=$E${firstDataRow}=2`,
    { fill: COLORS.paleYellow, font: { color: COLORS.text } },
  );
  if (firstVisualRow <= lastDataRow) {
    sheet.getRange(`G${firstVisualRow}:G${lastDataRow}`).format = {
      fill: "#E5E7EB",
      font: { color: "#9CA3AF", size: 10, name: "Microsoft YaHei" },
      verticalAlignment: "center",
    };
  }
  for (let offset = 0; offset < rows.length; offset += 1) {
    const excelRow = firstDataRow + offset;
    const correctionCheck = rows[offset].task === "microtext"
      ? `COUNTA(F${excelRow}:G${excelRow})>0`
      : `F${excelRow}<>""`;
    sheet.getRange(`H${excelRow}`).formulas = [[
      `=IF(E${excelRow}="","未完成",IF(E${excelRow}=2,IF(${correctionCheck},"完成","需填写修改"),"完成"))`,
    ]];
  }
  addDecisionFormatting(sheet.getRange(`E${firstDataRow}:E${lastDataRow}`), {
    1: COLORS.green,
    2: COLORS.blue,
    3: COLORS.red,
    4: COLORS.yellow,
  });
  sheet.getRange(`H${firstDataRow}:H${lastDataRow}`).conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"完成"',
    format: { fill: COLORS.green, font: { bold: true, color: "#166534" } },
  });
  sheet.getRange(`H${firstDataRow}:H${lastDataRow}`).conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"需填写修改"',
    format: { fill: COLORS.red, font: { bold: true, color: "#991B1B" } },
  });

  sheet.getRange("A:A").format.columnWidthPx = 48;
  sheet.getRange("B:B").format.columnWidthPx = 315;
  sheet.getRange("C:C").format.columnWidthPx = 68;
  sheet.getRange("D:D").format.columnWidthPx = 445;
  sheet.getRange("E:E").format.columnWidthPx = 145;
  sheet.getRange("F:F").format.columnWidthPx = 250;
  sheet.getRange("G:G").format.columnWidthPx = 155;
  sheet.getRange("H:H").format.columnWidthPx = 105;
  if (microRows > 0) {
    sheet.getRange(`A${firstDataRow}:H${firstDataRow + microRows - 1}`).format.rowHeightPx = 112;
  }
  if (microRows < rows.length) {
    sheet.getRange(`A${firstDataRow + microRows}:H${lastDataRow}`).format.rowHeightPx = 158;
  }
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(1);
  await addSinglePrimaryEvidenceImages(sheet, rows, firstDataRow);
  return sheet;
}

async function writeLeanSinglePrimary(workbook, rows) {
  const sheet = workbook.worksheets.add(SINGLE_PRIMARY_SHEET);
  sheet.showGridLines = false;
  const firstDataRow = 7;
  const lastDataRow = firstDataRow + rows.length - 1;
  const microRows = rows.filter((row) => row.task === "microtext").length;
  const lastMicroRow = firstDataRow + microRows - 1;
  const firstVisualRow = lastMicroRow + 1;

  sheet.mergeCells("A1:G1");
  sheet.getRange("A1").values = [[
    `Eng_Bench Gold v2.0 Global - \u4e3b\u5ba1\u5b9e\u4e60\u751f\u5feb\u901f\u5ba1\u6838 ${rows.length} \u6761`,
  ]];
  styleTitle(sheet.getRange("A1:G1"));
  sheet.getRange("A1:G1").format.rowHeightPx = 38;

  sheet.mergeCells("A2:G2");
  sheet.getRange("A2").values = [[
    "\u6bcf\u884c\u53ea\u505a\u4e00\u4ef6\u4e8b\uff1a\u770b B \u5217\u56fe\u7247 + C \u5217\u95ee\u9898\uff0c\u5728\u9ec4\u8272 D \u5217\u8f93\u5165 1/2/3/4 + Enter\u3002\u53ea\u6709\u9009 2 \u624d\u586b\u5199\u81ea\u52a8\u53d8\u9ec4\u7684\u4fee\u6539\u683c\u3002",
  ]];
  sheet.getRange("A2:G2").format = {
    fill: COLORS.yellow,
    font: { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("A2:G2").format.rowHeightPx = 42;

  sheet.mergeCells("A3:G3");
  sheet.getRange("A3").values = [[
    "1=\u5168\u5bf9\uff1b2=\u6837\u672c\u6709\u6548\u4f46\u6587\u5b57/\u7c7b\u522b/\u63cf\u8ff0\u6709\u9519\uff1b3=\u6837\u672c\u65e0\u6548\u6216\u65e0\u771f\u5b9e\u53d8\u5316\uff1b4=\u8bc1\u636e\u4e0d\u8db3\u3001\u9700\u8981\u66f4\u5927\u8303\u56f4\u3002\u4e0d\u8981\u731c\uff0c\u4e0d\u8981\u5220\u9664\u3001\u6392\u5e8f\u6216\u65b0\u589e\u884c\u3002",
  ]];
  sheet.getRange("A3:G3").format = {
    fill: COLORS.tealLight,
    font: { bold: true, color: COLORS.teal, size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("A3:G3").format.rowHeightPx = 34;

  sheet.getRange("A4:D4").values = [["\u8fdb\u5ea6", "", "\u4ea4\u56de\u68c0\u67e5", ""]];
  styleHeader(sheet.getRange("A4:D4"));
  sheet.getRange("B4").formulas = [[`=COUNT(D${firstDataRow}:D${lastDataRow})&" / ${rows.length}"`]];
  sheet.getRange("D4").formulas = [[
    `=IF(COUNTIF(G${firstDataRow}:G${lastDataRow},"\u5b8c\u6210")=${rows.length},"\u53ef\u4ee5\u4ea4\u56de","\u8fd8\u5dee "&(${rows.length}-COUNTIF(G${firstDataRow}:G${lastDataRow},"\u5b8c\u6210"))&" \u6761")`,
  ]];
  sheet.getRange("B4:D4").format = {
    fill: COLORS.grey,
    font: { bold: true, color: COLORS.text, size: 10, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: COLORS.line },
  };
  sheet.getRange("D4").conditionalFormats.add("containsText", {
    text: "\u53ef\u4ee5\u4ea4\u56de",
    format: { fill: COLORS.green, font: { bold: true, color: "#166534" } },
  });

  sheet.mergeCells("A5:G5");
  sheet.getRange("A5").values = [[
    "\u6613\u9519\uff1a\u622a\u56fe\u5c11\u5b57\u6bcd\u9009 3\uff1b\u5220\u9664\u7ebf\u5212\u6389\u7684\u6807\u7b7e\u9009 3\uff1bVisualDiff \u4e24\u56fe\u76f8\u540c\u3001\u4ec5\u6574\u4f53\u8f7b\u5fae\u504f\u79fb/\u6e32\u67d3\u5dee\u5f02\u6216\u53d8\u5316\u5728\u6846\u5916\u4e5f\u9009 3\u3002",
  ]];
  sheet.getRange("A5:G5").format = {
    fill: COLORS.paleYellow,
    font: { color: "#7C4A03", size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("A5:G5").format.rowHeightPx = 34;

  sheet.getRange("A6:G6").values = [[
    "#",
    "\u8bc1\u636e\u56fe\u7247",
    "\u53ea\u56de\u7b54\u8fd9\u4e2a\u95ee\u9898",
    "\u6309 1/2/3/4",
    "\u6b63\u786e\u6587\u5b57 / \u53d8\u5316\u63cf\u8ff0\uff08\u4ec5 2\uff09",
    "\u6b63\u786e\u7c7b\u522b\uff08\u4ec5\u6587\u5b57\u9898\u4e14 2\uff09",
    "\u5b8c\u6210",
  ]];
  styleHeader(sheet.getRange("A6:G6"));
  sheet.getRange("A6:G6").format.rowHeightPx = 48;

  const values = rows.map((row) => [
    Number(row.primary_index),
    "",
    singlePrimarySuggestion(row),
    "",
    "",
    "",
    "",
  ]);
  sheet.getRangeByIndexes(firstDataRow - 1, 0, values.length, 7).values = values;
  styleData(sheet.getRange(`A${firstDataRow}:G${lastDataRow}`));
  sheet.getRange(`D${firstDataRow}:D${lastDataRow}`).format = {
    fill: COLORS.yellow,
    font: { bold: true, size: 14, name: "Microsoft YaHei", color: COLORS.text },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange(`E${firstDataRow}:F${lastDataRow}`).format.fill = COLORS.grey;
  sheet.getRange(`D${firstDataRow}:D${lastDataRow}`).dataValidation = {
    rule: { type: "list", values: [1, 2, 3, 4] },
  };
  sheet.getRange(`F${firstDataRow}:F${lastMicroRow}`).dataValidation = {
    rule: {
      type: "list",
      values: ["dimension_value", "equipment_tag", "pin_label", "pipe_line_tag", "room_label"],
    },
  };
  sheet.getRange(`E${firstDataRow}:F${lastDataRow}`).conditionalFormats.addCustom(
    `=$D${firstDataRow}=2`,
    { fill: COLORS.paleYellow, font: { color: COLORS.text } },
  );
  if (firstVisualRow <= lastDataRow) {
    sheet.getRange(`F${firstVisualRow}:F${lastDataRow}`).format = {
      fill: "#E5E7EB",
      font: { color: "#9CA3AF", size: 10, name: "Microsoft YaHei" },
      verticalAlignment: "center",
    };
  }
  for (let offset = 0; offset < rows.length; offset += 1) {
    const excelRow = firstDataRow + offset;
    const correctionCheck = rows[offset].task === "microtext"
      ? `COUNTA(E${excelRow}:F${excelRow})>0`
      : `E${excelRow}<>""`;
    sheet.getRange(`G${excelRow}`).formulas = [[
      `=IF(D${excelRow}="","\u672a\u5b8c\u6210",IF(D${excelRow}=2,IF(${correctionCheck},"\u5b8c\u6210","\u9700\u586b\u5199\u4fee\u6539"),"\u5b8c\u6210"))`,
    ]];
  }
  addDecisionFormatting(sheet.getRange(`D${firstDataRow}:D${lastDataRow}`), {
    1: COLORS.green,
    2: COLORS.blue,
    3: COLORS.red,
    4: COLORS.yellow,
  });
  sheet.getRange(`G${firstDataRow}:G${lastDataRow}`).conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"\u5b8c\u6210"',
    format: { fill: COLORS.green, font: { bold: true, color: "#166534" } },
  });
  sheet.getRange(`G${firstDataRow}:G${lastDataRow}`).conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"\u9700\u586b\u5199\u4fee\u6539"',
    format: { fill: COLORS.red, font: { bold: true, color: "#991B1B" } },
  });

  sheet.getRange("A:A").format.columnWidthPx = 48;
  sheet.getRange("B:B").format.columnWidthPx = 315;
  sheet.getRange("C:C").format.columnWidthPx = 500;
  sheet.getRange("D:D").format.columnWidthPx = 135;
  sheet.getRange("E:E").format.columnWidthPx = 250;
  sheet.getRange("F:F").format.columnWidthPx = 155;
  sheet.getRange("G:G").format.columnWidthPx = 105;
  if (microRows > 0) {
    sheet.getRange(`A${firstDataRow}:G${firstDataRow + microRows - 1}`).format.rowHeightPx = 112;
  }
  if (microRows < rows.length) {
    sheet.getRange(`A${firstDataRow + microRows}:G${lastDataRow}`).format.rowHeightPx = 158;
  }
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(1);
  await addSinglePrimaryEvidenceImages(sheet, rows, firstDataRow);
  return sheet;
}

function writePrimaryStart(workbook, counts, universalPrimary = false) {
  const sheet = workbook.worksheets.add("开始");
  sheet.showGridLines = false;
  sheet.mergeCells("A1:F2");
  sheet.getRange("A1").values = [[
    universalPrimary
      ? "Eng_Bench Gold v2.0 Global - 主审实习生统一数字键审核"
      : "Eng_Bench Gold v2.0 Global - 主审实习生数字键审核",
  ]];
  styleTitle(sheet.getRange("A1:F2"));
  sheet.getRange("A1:F2").format.rowHeightPx = 34;

  sheet.mergeCells("A4:F5");
  sheet.getRange("A4").values = [[
    universalPrimary
      ? "两类任务都用同一套数字：1=通过，2=修改，3=剔除，4=看不清。每行看图和机器建议，在黄色格键入 1 个数字 + Enter；只有选 2 时才修改右侧内容。"
      : "每行只做一件事：看图 → 看机器建议 → 在黄色格键入 1 个数字 + Enter。MicroText 用 1/2/3/4，VisualDiff 用 1/2/3。不用切换中英文输入法，也不用点下拉；只有 MicroText 选 2 时才填改正。",
  ]];
  sheet.getRange("A4:F5").format = {
    fill: COLORS.yellow,
    font: { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };

  sheet.getRange("A7:B7").values = [["进度", "当前"]];
  styleHeader(sheet.getRange("A7:B7"));
  sheet.getRange("A8:A13").values = [
    ["总数"],
    ["MicroText"],
    ["VisualDiff"],
    ["已完成"],
    ["待完成"],
    ["是否可以交回"],
  ];
  sheet.getRange("B8:B10").values = [[counts.primary], [counts.primary_microtext], [counts.primary_visualdiff]];
  sheet.getRange("B11").formulas = [[
    `=COUNTIF('MicroText'!$G$2:$G$${counts.primary_microtext + 1},"完成")+COUNTIF('VisualDiff'!$F$2:$F$${counts.primary_visualdiff + 1},"完成")`,
  ]];
  sheet.getRange("B12").formulas = [["=B8-B11"]];
  sheet.getRange("B13").formulas = [["=IF(B12=0,\"可以交回\",\"还不能交回\")"]];
  sheet.getRange("A8:B13").format = {
    fill: COLORS.grey,
    font: { size: 10, name: "Microsoft YaHei", color: COLORS.text },
    borders: { preset: "all", style: "thin", color: COLORS.line },
  };
  sheet.getRange("A8:A13").format.font = { bold: true, color: COLORS.text, name: "Microsoft YaHei" };
  sheet.getRange("B13").conditionalFormats.add("containsText", {
    text: "可以交回",
    format: { fill: COLORS.green, font: { bold: true, color: "#166534" } },
  });
  sheet.getRange("B13").conditionalFormats.add("containsText", {
    text: "还不能交回",
    format: { fill: COLORS.red, font: { bold: true, color: "#991B1B" } },
  });

  sheet.mergeCells("A14:F14");
  sheet.getRange("A14").values = [[
    universalPrimary
      ? "省时原则：正常行只按 1；只有选 2 的行才改右侧内容。3 直接剔除，4 留给机器侧补看整页。"
      : "省时原则：正常行只按数字 1；只有需要修改的 MicroText 行才补改正内容。",
  ]];
  sheet.getRange("A14:F14").format = {
    fill: COLORS.tealLight,
    font: { bold: true, color: COLORS.teal, size: 11, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("A14:F14").format.rowHeightPx = 28;

  sheet.getRange("A16:F16").values = [["任务", "答案代码", "含义", "何时需要补充", "关键规则", "做完后"]];
  styleHeader(sheet.getRange("A16:F16"));
  const decisionRows = universalPrimary
    ? [
        ["两类任务", "1", "通过", "不用", "MicroText 全对；或 VisualDiff 有真实变化且描述准确", "下一行"],
        ["两类任务", "2", "修改", "只改右侧错误内容", "样本有效，但机器文字、类别或变化描述有错", "下一行"],
        ["两类任务", "3", "剔除", "不用", "MicroText 无效；或 VisualDiff 没有有效变化", "下一行"],
        ["两类任务", "4", "看不清", "不用", "证据不足，必须看更大范围或整页；不要猜", "下一行"],
      ]
    : [
        ["MicroText", "1", "通过", "不用", "图片完整；文字和类别都正确", "下一行"],
        ["MicroText", "2", "修改", "只填错的文字或类别", "图片有效，但机器文字或类别错", "下一行"],
        ["MicroText", "3", "剔除", "不用", "明确缺字、模糊、无效或被删除线划掉", "下一行"],
        ["MicroText", "4", "看不清", "不用", "需要更大范围或完整页面才能判断；不要猜", "下一行"],
        ["VisualDiff", "1", "有真实变化", "描述错误时改 E 列", "对比区域内有真实工程变化", "下一行"],
        ["VisualDiff", "2", "无有效变化", "不用", "相同、轻微整体偏移、仅渲染差异、或差异在区域外", "下一行"],
        ["VisualDiff", "3", "看不清", "不用", "无法可靠判断；不要猜", "下一行"],
      ];
  const lastDecisionRow = 16 + decisionRows.length;
  sheet.getRange(`A17:F${lastDecisionRow}`).values = decisionRows;
  sheet.getRange(`A17:F${lastDecisionRow}`).format = {
    fill: COLORS.grey,
    font: { size: 10, name: "Microsoft YaHei", color: COLORS.text },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "inside", style: "thin", color: COLORS.line },
  };

  sheet.getRange("A26:F26").merge();
  sheet.getRange("A26").values = [["交回前只检查三件事"]];
  sheet.getRange("A26:F26").format = {
    fill: COLORS.tealLight,
    font: { bold: true, color: COLORS.teal, size: 11, name: "Microsoft YaHei" },
  };
  sheet.getRange("A27:F30").merge(true);
  sheet.getRange("A27:A30").values = [
    ["1. MicroText 的 G 列和 VisualDiff 的 F 列全部显示“完成”。"],
    ["2. 本页 B13 显示“可以交回”。不要删除、排序或新增行。"],
    ["3. 保留原文件名 PRIMARY_REVIEW_498.xlsx，只交回这个 Excel。"],
    ["提示：裁剪少一个字母，即使机器猜出完整文字，也选 3；明确删除线划掉的标签也选 3。"],
  ];
  sheet.getRange("A27:F30").format = {
    fill: COLORS.grey,
    font: { size: 10, name: "Microsoft YaHei", color: COLORS.text },
    wrapText: true,
  };
  sheet.getRange("A:F").format.columnWidthPx = 160;
  sheet.getRange("C:C").format.columnWidthPx = 250;
  sheet.getRange("E:E").format.columnWidthPx = 280;
  sheet.getRange(`A17:F${lastDecisionRow}`).format.rowHeightPx = 38;
  sheet.getRange("A27:F30").format.rowHeightPx = 32;
  return sheet;
}

async function writePrimaryMicro(workbook, rows, universalPrimary = false) {
  const sheet = workbook.worksheets.add("MicroText");
  sheet.showGridLines = false;
  const lastRow = rows.length + 1;
  sheet.getRange("A1:G1").values = [[
    "#",
    "证据图片",
    "机器建议（文字 + 类别）",
    universalPrimary ? "答案：1通过 2修改 3剔除 4看不清" : "答案 1/2/3/4",
    "正确文字（仅 2 时）",
    "正确类别（仅 2 时）",
    "完成检查",
  ]];
  styleHeader(sheet.getRange("A1:G1"));
  sheet.getRange("A1:G1").format.rowHeightPx = universalPrimary ? 52 : 28;
  const values = rows.map((row) => [
    Number(row.display_index),
    "",
    row.machine_suggestion,
    "",
    row.corrected_text || "",
    row.corrected_category || "",
    "",
  ]);
  sheet.getRangeByIndexes(1, 0, values.length, 7).values = values;
  styleData(sheet.getRange(`A2:G${lastRow}`));
  sheet.getRange(`D2:D${lastRow}`).format = {
    fill: COLORS.yellow,
    font: { bold: true, size: 13, name: "Microsoft YaHei", color: COLORS.text },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange(`E2:F${lastRow}`).format.fill = COLORS.paleYellow;
  sheet.getRange(`D2:D${lastRow}`).dataValidation = {
    rule: { type: "list", values: [1, 2, 3, 4] },
  };
  sheet.getRange(`F2:F${lastRow}`).dataValidation = {
    rule: {
      type: "list",
      values: ["dimension_value", "equipment_tag", "pin_label", "pipe_line_tag", "room_label"],
    },
  };
  for (let excelRow = 2; excelRow <= lastRow; excelRow += 1) {
    sheet.getRange(`G${excelRow}`).formulas = [[
      rowStatusFormula(`D${excelRow}`, `E${excelRow}:F${excelRow}`, "2"),
    ]];
  }
  addDecisionFormatting(sheet.getRange(`D2:D${lastRow}`), {
    1: COLORS.green,
    2: COLORS.blue,
    3: COLORS.red,
    4: COLORS.yellow,
  });
  sheet.getRange(`G2:G${lastRow}`).conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"完成"',
    format: { fill: COLORS.green, font: { bold: true, color: "#166534" } },
  });
  sheet.getRange(`G2:G${lastRow}`).conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"需填写改正"',
    format: { fill: COLORS.red, font: { bold: true, color: "#991B1B" } },
  });
  sheet.getRange("A:A").format.columnWidthPx = 48;
  sheet.getRange("B:B").format.columnWidthPx = 250;
  sheet.getRange("C:C").format.columnWidthPx = 310;
  sheet.getRange("D:D").format.columnWidthPx = universalPrimary ? 140 : 105;
  sheet.getRange("E:E").format.columnWidthPx = 165;
  sheet.getRange("F:F").format.columnWidthPx = 150;
  sheet.getRange("G:G").format.columnWidthPx = 105;
  sheet.getRange(`A2:G${lastRow}`).format.rowHeightPx = 112;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(1);
  await addEvidenceImages(sheet, rows, 2, 235, 98);
  return sheet;
}

async function writePrimaryVisual(workbook, rows, universalPrimary = false) {
  const sheet = workbook.worksheets.add("VisualDiff");
  sheet.showGridLines = false;
  const lastRow = rows.length + 1;
  sheet.getRange("A1:F1").values = [[
    "#",
    "OLD / NEW 证据图片",
    "机器提示（只看对比区域）",
    universalPrimary ? "答案：1通过 2修改 3剔除 4看不清" : "答案 1/2/3",
    universalPrimary ? "机器变化描述（仅 2 时修改）" : "变化描述（选 1 时必须准确）",
    "完成检查",
  ]];
  styleHeader(sheet.getRange("A1:F1"));
  sheet.getRange("A1:F1").format.rowHeightPx = universalPrimary ? 52 : 28;
  const values = rows.map((row) => [
    Number(row.display_index),
    "",
    row.machine_suggestion,
    "",
    row.change_description || "",
    "",
  ]);
  sheet.getRangeByIndexes(1, 0, values.length, 6).values = values;
  styleData(sheet.getRange(`A2:F${lastRow}`));
  sheet.getRange(`D2:D${lastRow}`).format = {
    fill: COLORS.yellow,
    font: { bold: true, size: 13, name: "Microsoft YaHei", color: COLORS.text },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange(`E2:E${lastRow}`).format.fill = COLORS.paleYellow;
  sheet.getRange(`D2:D${lastRow}`).dataValidation = {
    rule: { type: "list", values: universalPrimary ? [1, 2, 3, 4] : [1, 2, 3] },
  };
  for (let excelRow = 2; excelRow <= lastRow; excelRow += 1) {
    const formula = universalPrimary
      ? `=IF(D${excelRow}="","未完成",IF(OR(D${excelRow}=1,D${excelRow}=2),IF(COUNTA(E${excelRow})>0,"完成","需填写改正"),"完成"))`
      : rowStatusFormula(`D${excelRow}`, `E${excelRow}`, "1");
    sheet.getRange(`F${excelRow}`).formulas = [[formula]];
  }
  addDecisionFormatting(
    sheet.getRange(`D2:D${lastRow}`),
    universalPrimary
      ? { 1: COLORS.green, 2: COLORS.blue, 3: COLORS.red, 4: COLORS.yellow }
      : { 1: COLORS.green, 2: COLORS.red, 3: COLORS.yellow },
  );
  sheet.getRange(`F2:F${lastRow}`).conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"完成"',
    format: { fill: COLORS.green, font: { bold: true, color: "#166534" } },
  });
  sheet.getRange(`F2:F${lastRow}`).conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"需填写改正"',
    format: { fill: COLORS.red, font: { bold: true, color: "#991B1B" } },
  });
  sheet.getRange("A:A").format.columnWidthPx = 52;
  sheet.getRange("B:B").format.columnWidthPx = 340;
  sheet.getRange("C:C").format.columnWidthPx = 315;
  sheet.getRange("D:D").format.columnWidthPx = universalPrimary ? 140 : 105;
  sheet.getRange("E:E").format.columnWidthPx = 290;
  sheet.getRange("F:F").format.columnWidthPx = 105;
  sheet.getRange(`A2:F${lastRow}`).format.rowHeightPx = 158;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(1);
  await addEvidenceImages(sheet, rows, 2, 325, 142);
  return sheet;
}

async function buildPrimary(
  payload,
  outputPath,
  previewDir,
  universalPrimary = false,
  singleSheetPrimary = false,
  leanSingleSheetPrimary = false,
) {
  const rows = payload.primary.rows;
  const micro = rows.filter((row) => row.task === "microtext");
  const visual = rows.filter((row) => row.task === "visualdiff");
  const workbook = Workbook.create();
  if (leanSingleSheetPrimary) {
    await writeLeanSinglePrimary(workbook, rows);
  } else if (singleSheetPrimary) {
    await writeSinglePrimary(workbook, rows);
  } else {
    writePrimaryStart(workbook, payload.counts, universalPrimary);
    await writePrimaryMicro(workbook, micro, universalPrimary);
    await writePrimaryVisual(workbook, visual, universalPrimary);
  }
  addMachineSheet(workbook, rows);

  const checks = [];
  if (leanSingleSheetPrimary) {
    checks.push((await workbook.inspect({
      kind: "table",
      range: `${SINGLE_PRIMARY_SHEET}!A1:G10`,
      tableMaxRows: 10,
      tableMaxCols: 7,
    })).ndjson);
  } else if (singleSheetPrimary) {
    checks.push((await workbook.inspect({
      kind: "table",
      range: `${SINGLE_PRIMARY_SHEET}!A1:H10`,
      tableMaxRows: 10,
      tableMaxCols: 8,
    })).ndjson);
  } else {
    checks.push((await workbook.inspect({ kind: "table", range: "开始!A1:F30", tableMaxRows: 30, tableMaxCols: 6 })).ndjson);
    checks.push((await workbook.inspect({ kind: "table", range: "MicroText!A1:G5", tableMaxRows: 5, tableMaxCols: 7 })).ndjson);
    checks.push((await workbook.inspect({ kind: "table", range: "VisualDiff!A1:F5", tableMaxRows: 5, tableMaxCols: 6 })).ndjson);
  }
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: "formula error scan",
  });
  checks.push(errors.ndjson);
  await fs.mkdir(previewDir, { recursive: true });
  await fs.writeFile(path.join(previewDir, "inspect.ndjson"), checks.join("\n"), "utf8");
  const previewSpecs = leanSingleSheetPrimary
    ? [[SINGLE_PRIMARY_SHEET, "A1:G12", "review_start.png"]]
    : singleSheetPrimary
    ? [[SINGLE_PRIMARY_SHEET, "A1:H12", "review_start.png"]]
    : [
        ["开始", "A1:F30", "start.png"],
        ["MicroText", "A1:G6", "microtext.png"],
        ["VisualDiff", "A1:F5", "visualdiff.png"],
      ];
  for (const [sheetName, range, fileName] of previewSpecs) {
    const rendered = await workbook.render({ sheetName, range, scale: 1, format: "png" });
    await fs.writeFile(path.join(previewDir, fileName), new Uint8Array(await rendered.arrayBuffer()));
  }
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(outputPath);
}

function auditorPrompt(row) {
  if (row.task === "microtext") {
    const item = parseMicrotextSuggestion(row.machine_suggestion);
    const examples = {
      dimension_value: "例：11.8°、R10、3'-3\"、0.7500",
      equipment_tag: "例：MOTOR、PUMP、BOILER NO. 1",
      pin_label: "例：L1、R10、Q3、GP14、3V3",
      pipe_line_tag: "例：DRAIN、OIL TANK VENT、PG05001-6\"",
      room_label: "例：DECK、KITCHEN、HALL、PLATFORM",
    };
    return `「${item.text}」  |  ${item.categoryLabel}（${examples[item.category] ?? "按工程用途判断"}）`;
  }
  return "OLD/NEW 红框内有真实工程变化吗？（相同、轻微整体偏移、仅渲染差异或框外变化=2）";
}

async function buildAuditor(auditor, outputPath, previewDir) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("审核12条");
  sheet.showGridLines = false;
  sheet.mergeCells("A1:E1");
  sheet.getRange("A1").values = [[`Eng_Bench Gold v2.0 Global - 复核 ${String(auditor.number).padStart(2, "0")}（只有 12 条）`]];
  styleTitle(sheet.getRange("A1:E1"));
  sheet.getRange("A1:E1").format.rowHeightPx = 40;
  sheet.mergeCells("A2:E2");
  sheet.getRange("A2").values = [["看 B 图 + C 内容，D 列按数字 + Enter：1对  2错  3不清楚。不改其他格。"]];
  sheet.getRange("A2:E2").format = {
    fill: COLORS.yellow,
    font: { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.mergeCells("A3:E3");
  sheet.getRange("A3").values = [["文字：截图完整 + 文字/类别全对才按 1。差异：框内有真实变化才按 1；相同/轻微整体偏移/框外 = 2。"]];
  sheet.getRange("A3:E3").format = {
    fill: COLORS.tealLight,
    font: { bold: true, color: COLORS.teal, size: 10, name: "Microsoft YaHei" },
    wrapText: true,
  };
  sheet.mergeCells("A4:E4");
  sheet.getRange("A4").values = [["类别例子：尺寸=11.8°/R10/3'-3\"；设备=MOTOR/PUMP；引脚/元件=L1/Q3/GP14；管线=DRAIN/PG05001-6\"；房间/区域=DECK/KITCHEN。R10 必须结合图中上下文。"]];
  sheet.getRange("A4:E4").format = {
    fill: "#E8F5E9",
    font: { bold: true, size: 9, name: "Microsoft YaHei", color: "#14532D" },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.mergeCells("A5:D5");
  sheet.getRange("A5").values = [["差异文字：尺寸/标签/房名/工程注释的含义改变=真实工程变化；只换字体/位置/清晰度=无工程变化。E 列全部为 ✓ 后 Ctrl+S，原文件名交回。"]];
  sheet.getRange("E5").formulas = [["=COUNTIF(D7:D18,\"<>\")&\" / 12\""]];
  sheet.getRange("A5:E5").format = {
    fill: COLORS.green,
    font: { bold: true, color: "#166534", size: 10, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("E5").format.horizontalAlignment = "center";
  sheet.getRange("A5:E5").format.rowHeightPx = 28;
  sheet.getRange("A6:E6").values = [["#", "证据图片", "只回答这个问题", "答案 1/2/3", "完成"]];
  styleHeader(sheet.getRange("A6:E6"));
  const values = auditor.rows.map((row) => [
    Number(row.display_index),
    "",
    auditorPrompt(row),
    "",
    "",
  ]);
  sheet.getRange("A7:E18").values = values;
  styleData(sheet.getRange("A7:E18"));
  sheet.getRange("D7:D18").format = {
    fill: COLORS.yellow,
    font: { bold: true, size: 14, name: "Microsoft YaHei", color: COLORS.text },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("D7:D18").dataValidation = {
    rule: { type: "list", values: [1, 2, 3] },
  };
  for (let excelRow = 7; excelRow <= 18; excelRow += 1) {
    sheet.getRange(`E${excelRow}`).formulas = [[
      `=IF(D${excelRow}="","未答","✓")`,
    ]];
  }
  addDecisionFormatting(sheet.getRange("D7:D18"), {
    1: COLORS.green,
    2: COLORS.red,
    3: COLORS.yellow,
  });
  sheet.getRange("E7:E18").conditionalFormats.add("containsText", {
    text: "未答",
    format: { fill: COLORS.red, font: { bold: true, color: "#991B1B" } },
  });
  sheet.getRange("A:A").format.columnWidthPx = 45;
  sheet.getRange("B:B").format.columnWidthPx = 380;
  sheet.getRange("C:C").format.columnWidthPx = 455;
  sheet.getRange("D:D").format.columnWidthPx = 105;
  sheet.getRange("E:E").format.columnWidthPx = 70;
  for (let offset = 0; offset < auditor.rows.length; offset += 1) {
    const excelRow = 7 + offset;
    sheet.getRange(`A${excelRow}:E${excelRow}`).format.rowHeightPx =
      auditor.rows[offset].task === "visualdiff" ? 205 : 148;
  }
  sheet.getRange("A2:E3").format.rowHeightPx = 38;
  sheet.getRange("A4:E4").format.rowHeightPx = 42;
  sheet.freezePanes.freezeRows(6);
  await addAuditorEvidenceImages(sheet, auditor.rows, 7);
  addMachineSheet(workbook, auditor.rows);

  const inspect = await workbook.inspect({
    kind: "table",
    range: "审核12条!A1:E18",
    tableMaxRows: 18,
    tableMaxCols: 5,
  });
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: "formula error scan",
  });
  await fs.mkdir(previewDir, { recursive: true });
  await fs.writeFile(path.join(previewDir, "inspect.ndjson"), `${inspect.ndjson}\n${errors.ndjson}`, "utf8");
  const rendered = await workbook.render({
    sheetName: "审核12条",
    range: "A1:E18",
    scale: 0.8,
    format: "png",
  });
  await fs.writeFile(path.join(previewDir, "review.png"), new Uint8Array(await rendered.arrayBuffer()));
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(outputPath);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const payload = JSON.parse(await fs.readFile(args.payload, "utf8"));
  const outputDir = path.resolve(args["output-dir"]);
  const previewDir = path.resolve(args["preview-dir"]);
  if (args.overwrite) {
    await fs.rm(outputDir, { recursive: true, force: true });
    await fs.rm(previewDir, { recursive: true, force: true });
  }
  await fs.mkdir(outputDir, { recursive: true });
  await fs.mkdir(previewDir, { recursive: true });

  if (!args["auditors-only"]) {
    await buildPrimary(
      payload,
      path.join(outputDir, payload.primary.workbook),
      path.join(previewDir, "PRIMARY_REVIEW_498"),
      Boolean(args["universal-primary"]),
      Boolean(args["single-sheet-primary"]),
      Boolean(args["lean-single-sheet-primary"]),
    );
  }
  for (const auditor of payload.auditors) {
    await buildAuditor(
      auditor,
      path.join(outputDir, auditor.workbook),
      path.join(previewDir, path.parse(auditor.workbook).name),
    );
  }
  const report = {
    goal: payload.goal,
    output_dir: outputDir,
    primary_rows: payload.counts.primary,
    primary_workbook_written: !args["auditors-only"],
    auditors: payload.counts.auditors,
    rows_per_auditor: 12,
    workbook_count: payload.auditors.length + (args["auditors-only"] ? 0 : 1),
    evidence_images_written:
      payload.counts.auditor_rows + (args["auditors-only"] ? 0 : payload.counts.primary),
    visible_primary_sheets:
      args["single-sheet-primary"] || args["lean-single-sheet-primary"] ? 1 : 3,
    visible_auditor_sheets_each: 1,
    primary_decision_scheme: args["lean-single-sheet-primary"]
      ? "lean_single_sheet_universal_1_accept_2_edit_3_reject_4_unclear"
      : args["single-sheet-primary"]
      ? "single_sheet_universal_1_accept_2_edit_3_reject_4_unclear"
      : args["universal-primary"]
      ? "universal_1_accept_2_edit_3_reject_4_unclear"
      : "task_specific_numeric",
    gold_rows_modified: 0,
  };
  await fs.writeFile(path.join(outputDir, "build_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(report, null, 2));
}

await main();
