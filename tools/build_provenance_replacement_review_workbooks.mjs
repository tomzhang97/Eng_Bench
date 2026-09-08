#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

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
  pin_label: "引脚/端子/元件标签",
  dimension_value: "尺寸值",
  instrument_tag: "仪表标签",
};

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) throw new Error(`unexpected argument: ${key}`);
    if (key === "--overwrite") {
      result.overwrite = true;
      continue;
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`missing value for ${key}`);
    result[key.slice(2)] = value;
    index += 1;
  }
  for (const required of ["payload", "output-dir", "preview-dir"]) {
    if (!result[required]) throw new Error(`missing --${required}`);
  }
  return result;
}

function readJsonl(text) {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

function pngDimensions(buffer) {
  if (buffer.length < 24 || buffer[0] !== 0x89 || buffer.toString("ascii", 1, 4) !== "PNG") {
    throw new Error("evidence image is not a PNG");
  }
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

const imageCache = new Map();

async function imageData(imagePath) {
  if (!imageCache.has(imagePath)) {
    const buffer = await fs.readFile(imagePath);
    imageCache.set(imagePath, {
      dataUrl: `data:image/png;base64,${buffer.toString("base64")}`,
      ...pngDimensions(buffer),
    });
  }
  return imageCache.get(imagePath);
}

function fitImage(image, maxWidth, maxHeight) {
  const scale = Math.min(maxWidth / image.width, maxHeight / image.height);
  return {
    widthPx: Math.max(24, Math.round(image.width * scale)),
    heightPx: Math.max(24, Math.round(image.height * scale)),
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

function styleInstruction(range, fill, color) {
  range.format = {
    fill,
    font: { bold: true, color, size: 10, name: "Microsoft YaHei" },
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
    borders: {
      insideHorizontal: { style: "thin", color: COLORS.line },
      bottom: { style: "thin", color: COLORS.line },
    },
  };
}

function addDecisionFormatting(range) {
  for (const [code, fill] of [
    [1, COLORS.green],
    [2, COLORS.yellow],
    [3, COLORS.red],
    [4, COLORS.blue],
  ]) {
    range.conditionalFormats.add("cellIs", {
      operator: "equal",
      formula: code,
      format: { fill, font: { bold: true, color: COLORS.text } },
    });
  }
}

function addStatusFormatting(range, firstRow) {
  range.conditionalFormats.addCustom(`=$I${firstRow}="完成"`, {
    fill: COLORS.green,
    font: { bold: true, color: COLORS.text },
  });
  range.conditionalFormats.add("containsText", {
    text: "需填写改正",
    format: { fill: COLORS.yellow, font: { bold: true, color: COLORS.text } },
  });
  range.conditionalFormats.add("containsText", {
    text: "未完成",
    format: { fill: COLORS.red, font: { bold: true, color: COLORS.text } },
  });
}

function setupTop(sheet, { title, instruction, rule, examples, rowCount, lastRow }) {
  sheet.showGridLines = false;
  for (const row of [1, 2, 3, 4]) sheet.mergeCells(`A${row}:J${row}`);
  sheet.getRange("A1").values = [[title]];
  styleTitle(sheet.getRange("A1:J1"));
  sheet.getRange("A1:J1").format.rowHeightPx = 38;

  sheet.getRange("A2").values = [[instruction]];
  styleInstruction(sheet.getRange("A2:J2"), COLORS.yellow, "#7C4A03");
  sheet.getRange("A2:J2").format.rowHeightPx = 38;

  sheet.getRange("A3").values = [[rule]];
  styleInstruction(sheet.getRange("A3:J3"), COLORS.tealLight, COLORS.teal);
  sheet.getRange("A3:J3").format.rowHeightPx = 38;

  sheet.getRange("A4").values = [[examples]];
  styleInstruction(sheet.getRange("A4:J4"), "#EEF2F6", "#334155");
  sheet.getRange("A4:J4").format.rowHeightPx = 46;

  sheet.getRange("A5:D5").values = [["进度", "", "交回检查", ""]];
  styleHeader(sheet.getRange("A5:D5"));
  sheet.getRange("B5").formulas = [[`=COUNTIF(I7:I${lastRow},"完成")&" / ${rowCount}"`]];
  sheet.getRange("D5").formulas = [[
    `=IF(COUNTIF(I7:I${lastRow},"完成")=${rowCount},"可以交回","还差 "&(${rowCount}-COUNTIF(I7:I${lastRow},"完成"))&" 条")`,
  ]];
  sheet.getRange("A5:D5").format.rowHeightPx = 28;
}

async function addImages(sheet, rows, packRoot, field, maxWidth, maxHeight) {
  for (let index = 0; index < rows.length; index += 1) {
    const imagePath = path.resolve(packRoot, rows[index][field]);
    const image = await imageData(imagePath);
    const extent = fitImage(image, maxWidth, maxHeight);
    sheet.images.add({
      dataUrl: image.dataUrl,
      anchor: {
        from: { row: index + 6, col: 1, rowOffsetPx: 7, colOffsetPx: 7 },
        extent,
      },
    });
    if ((index + 1) % 100 === 0 || index + 1 === rows.length) {
      console.log(`embedded ${index + 1}/${rows.length} images in ${sheet.name}`);
    }
  }
}

function addMachineSheet(workbook, rows, task) {
  const sheet = workbook.worksheets.add("机器数据_勿改");
  sheet.showGridLines = false;
  const header = [
    "index",
    "record_id",
    "replacement_split",
    "source_unit",
    "source_candidate_id",
    "source_doc_or_pair",
    "source_image_or_panel",
    "bbox",
    "safe_to_merge_gold",
    "review_status",
    "description_rewrite_required",
  ];
  const values = rows.map((row, index) => [
    index + 1,
    row.candidate_id || row.pair_id || "",
    row.replacement_for_split || "",
    row.replacement_source_unit || "",
    row.source_candidate_id || "",
    task === "microtext" ? row.doc_id || "" : row.project_id || "",
    task === "microtext" ? row.crop_path || "" : row.panel_path || "",
    JSON.stringify(task === "microtext" ? row.bbox || [] : [row.bbox_old || [], row.bbox_new || []]),
    String(row.safe_to_merge_gold),
    row.review_status || "",
    String(Boolean(row.description_rewrite_required)),
  ]);
  sheet.getRangeByIndexes(0, 0, values.length + 1, header.length).values = [header, ...values];
  styleHeader(sheet.getRange("A1:K1"));
  sheet.getRange(`A2:K${values.length + 1}`).format = {
    font: { size: 9, name: "Consolas", color: COLORS.text },
    wrapText: false,
  };
  sheet.getRange("A:A").format.columnWidthPx = 60;
  sheet.getRange("B:B").format.columnWidthPx = 260;
  sheet.getRange("C:E").format.columnWidthPx = 130;
  sheet.getRange("F:K").format.columnWidthPx = 240;
  sheet.freezePanes.freezeRows(1);
  return sheet;
}

async function buildMicrotext(payload, outputDir, previewDir) {
  const rows = readJsonl(await fs.readFile(payload.manifest, "utf8"));
  const packRoot = path.dirname(payload.manifest);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(payload.sheet);
  const firstRow = 7;
  const lastRow = firstRow + rows.length - 1;

  setupTop(sheet, {
    title: `Eng_Bench Gold v2.0 Global - MicroText 来源替换优先审核 ${rows.length} 条`,
    instruction: "只看 B 列图片、C/D 机器建议，在黄色 E 列填 1/2/3/4。只有填 2 才填写 F/G；不要排序、删行或修改 ID。",
    rule: "1=全部正确；2=样本有效但文字或类别错误；3=样本无效/截图不完整/不是工程标签；4=证据不足，需要二次裁决。",
    examples: "类别示例：pin_label=GPIO14/J3/Q1/SCL；dimension_value=10'-0\"/R0.8/4.00°；instrument_tag=FT-101/PIT-202。截图少字母但机器补全了截图外文字，不能填 1。",
    rowCount: rows.length,
    lastRow,
  });

  sheet.getRange("A6:J6").values = [[
    "#", "证据图片", "机器文字", "机器类别", "决定 1/2/3/4",
    "改正文字（仅2）", "改正类别（仅2）", "备注", "完成", "Candidate ID",
  ]];
  styleHeader(sheet.getRange("A6:J6"));
  sheet.getRange("A6:J6").format.rowHeightPx = 34;

  const values = rows.map((row, index) => [
    index + 1,
    "",
    row.proposed_text || row.target_text || "",
    `${row.category || "unknown"}\n${CATEGORY_LABELS[row.category] || "未知类别"}`,
    "",
    "",
    "",
    "",
    "",
    row.candidate_id || "",
  ]);
  sheet.getRangeByIndexes(6, 0, rows.length, 10).values = values;
  styleData(sheet.getRange(`A${firstRow}:J${lastRow}`));
  sheet.getRange(`B${firstRow}:B${lastRow}`).format.fill = COLORS.white;
  sheet.getRange(`E${firstRow}:E${lastRow}`).format.fill = COLORS.yellow;
  sheet.getRange(`F${firstRow}:G${lastRow}`).format.fill = COLORS.paleYellow;
  sheet.getRange(`I${firstRow}:I${lastRow}`).format.fill = COLORS.white;
  sheet.getRange(`A${firstRow}:J${lastRow}`).format.rowHeightPx = 116;

  sheet.getRange(`E${firstRow}:E${lastRow}`).dataValidation = {
    rule: { type: "list", values: [1, 2, 3, 4] },
  };
  sheet.getRange(`G${firstRow}:G${lastRow}`).dataValidation = {
    rule: { type: "list", values: ["pin_label", "dimension_value", "instrument_tag", "other"] },
  };
  addDecisionFormatting(sheet.getRange(`E${firstRow}:E${lastRow}`));
  sheet.getRange(`I${firstRow}:I${lastRow}`).formulas = rows.map((_, index) => {
    const row = firstRow + index;
    return [`=IF(E${row}="","未完成",IF(E${row}=2,IF(AND(F${row}<>"",G${row}<>""),"完成","需填写改正"),"完成"))`];
  });
  addStatusFormatting(sheet.getRange(`I${firstRow}:I${lastRow}`), firstRow);

  sheet.getRange("A:A").format.columnWidthPx = 48;
  sheet.getRange("B:B").format.columnWidthPx = 330;
  sheet.getRange("C:C").format.columnWidthPx = 180;
  sheet.getRange("D:D").format.columnWidthPx = 155;
  sheet.getRange("E:E").format.columnWidthPx = 100;
  sheet.getRange("F:F").format.columnWidthPx = 190;
  sheet.getRange("G:G").format.columnWidthPx = 155;
  sheet.getRange("H:H").format.columnWidthPx = 180;
  sheet.getRange("I:I").format.columnWidthPx = 105;
  sheet.getRange("J:J").format.columnWidthPx = 280;
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(2);
  await addImages(sheet, rows, packRoot, "crop_path", 305, 96);
  addMachineSheet(workbook, rows, "microtext");

  await verifyAndExport(workbook, payload.sheet, rows.length, outputDir, payload.workbook, previewDir);
  return { workbook: payload.workbook, rows: rows.length, embedded_images: sheet.images.items.length };
}

async function buildVisualdiff(payload, outputDir, previewDir) {
  const rows = readJsonl(await fs.readFile(payload.manifest, "utf8"));
  const packRoot = path.dirname(payload.manifest);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(payload.sheet);
  const firstRow = 7;
  const lastRow = firstRow + rows.length - 1;
  const rewriteRequired = rows.map((row) => Boolean(row.description_rewrite_required));
  const rewriteRequiredCount = rewriteRequired.filter(Boolean).length;

  setupTop(sheet, {
    title: `Eng_Bench Gold v2.0 Global - VisualDiff 来源替换优先审核 ${rows.length} 条`,
    instruction: `看 B 列红色 OLD 与青色 NEW，再核对 C/D。黄色 E 列填 1/2/3/4；其中 ${rewriteRequiredCount} 条标为【必须人工重写】，仅在确有变化时填 2 和 F/G。`,
    rule: "1=框内真实工程变化且类型/描述全对；2=真实变化但机器类型或描述错误；3=相同/整体偏移/渲染差异/无工程变化；4=位置不对应或证据不足。",
    examples: "类型示例：text=文字替换；addition=新增；deletion=删除；symbol=符号/元件；geometry=几何/连线；layout=具体对象相对移动；value=数值。文字含义改变也可算真实工程变化。",
    rowCount: rows.length,
    lastRow,
  });

  sheet.getRange("A6:J6").values = [[
    "#", "OLD / NEW 证据", "机器类型", "机器描述", "决定 1/2/3/4",
    "改正类型（仅2）", "改正描述（仅2）", "备注", "完成", "Pair ID",
  ]];
  styleHeader(sheet.getRange("A6:J6"));
  sheet.getRange("A6:J6").format.rowHeightPx = 34;

  const values = rows.map((row, index) => [
    index + 1,
    "",
    row.change_type || "unknown",
    `${rewriteRequired[index] ? "【必须人工重写】\n" : ""}${row.description || row.change_desc_gt || "（机器未提供描述）"}`,
    "",
    "",
    "",
    rewriteRequired[index] ? "确有变化：选2并重写；无变化：选3；位置不对应/证据不足：选4" : "",
    "",
    row.pair_id || "",
  ]);
  for (let index = 0; index < rows.length; index += 1) {
    if (rewriteRequired[index] && (values[index][4] !== "" || !values[index][7])) {
      throw new Error(`mandatory rewrite controls are misaligned at VisualDiff row ${index + 1}`);
    }
  }
  sheet.getRangeByIndexes(6, 0, rows.length, 10).values = values;
  styleData(sheet.getRange(`A${firstRow}:J${lastRow}`));
  sheet.getRange(`B${firstRow}:B${lastRow}`).format.fill = COLORS.white;
  sheet.getRange(`E${firstRow}:E${lastRow}`).format.fill = COLORS.yellow;
  sheet.getRange(`F${firstRow}:G${lastRow}`).format.fill = COLORS.paleYellow;
  for (let index = 0; index < rows.length; index += 1) {
    if (rewriteRequired[index]) {
      const excelRow = firstRow + index;
      sheet.getRange(`D${excelRow}`).format.fill = COLORS.blue;
      sheet.getRange(`H${excelRow}`).format.fill = COLORS.blue;
    }
  }
  sheet.getRange(`I${firstRow}:I${lastRow}`).format.fill = COLORS.white;
  sheet.getRange(`A${firstRow}:J${lastRow}`).format.rowHeightPx = 166;

  sheet.getRange(`E${firstRow}:E${lastRow}`).dataValidation = {
    rule: { type: "list", values: [1, 2, 3, 4] },
  };
  sheet.getRange(`F${firstRow}:F${lastRow}`).dataValidation = {
    rule: { type: "list", values: ["text", "addition", "deletion", "symbol", "geometry", "layout", "value", "other"] },
  };
  addDecisionFormatting(sheet.getRange(`E${firstRow}:E${lastRow}`));
  sheet.getRange(`I${firstRow}:I${lastRow}`).formulas = rows.map((_, index) => {
    const row = firstRow + index;
    if (rewriteRequired[index]) {
      return [`=IF(E${row}="","未完成",IF(E${row}=1,"不能直接选1",IF(E${row}=2,IF(AND(F${row}<>"",G${row}<>""),"完成","需填写改正"),"完成")))`];
    }
    return [`=IF(E${row}="","未完成",IF(E${row}=2,IF(AND(F${row}<>"",G${row}<>""),"完成","需填写改正"),"完成"))`];
  });
  addStatusFormatting(sheet.getRange(`I${firstRow}:I${lastRow}`), firstRow);

  sheet.getRange("A:A").format.columnWidthPx = 48;
  sheet.getRange("B:B").format.columnWidthPx = 350;
  sheet.getRange("C:C").format.columnWidthPx = 150;
  sheet.getRange("D:D").format.columnWidthPx = 300;
  sheet.getRange("E:E").format.columnWidthPx = 100;
  sheet.getRange("F:F").format.columnWidthPx = 150;
  sheet.getRange("G:G").format.columnWidthPx = 300;
  sheet.getRange("H:H").format.columnWidthPx = 180;
  sheet.getRange("I:I").format.columnWidthPx = 105;
  sheet.getRange("J:J").format.columnWidthPx = 310;
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(2);
  await addImages(sheet, rows, packRoot, "panel_path", 325, 145);
  addMachineSheet(workbook, rows, "visualdiff");

  await verifyAndExport(workbook, payload.sheet, rows.length, outputDir, payload.workbook, previewDir);
  return {
    workbook: payload.workbook,
    rows: rows.length,
    embedded_images: sheet.images.items.length,
    description_rewrite_required: rewriteRequiredCount,
  };
}

async function verifyAndExport(workbook, sheetName, rowCount, outputDir, workbookName, previewDir) {
  const lastRow = rowCount + 6;
  const keyRange = await workbook.inspect({
    kind: "table",
    range: `${sheetName}!A1:J12`,
    include: "values,formulas",
    tableMaxRows: 12,
    tableMaxCols: 10,
    maxChars: 8000,
  });
  console.log(keyRange.ndjson);
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: `${workbookName} formula error scan`,
    maxChars: 3000,
  });
  if (!errors.ndjson.includes("matched 0 entries")) {
    throw new Error(`${workbookName} formula error scan failed: ${errors.ndjson}`);
  }
  await fs.mkdir(previewDir, { recursive: true });
  for (const [name, range] of [
    ["top", "A1:J14"],
    ["tail", `A${Math.max(7, lastRow - 4)}:J${lastRow}`],
    ["machine", "A1:J8"],
  ]) {
    const preview = await workbook.render({
      sheetName: name === "machine" ? "机器数据_勿改" : sheetName,
      range,
      scale: 1,
      format: "png",
    });
    await fs.writeFile(
      path.join(previewDir, `${path.parse(workbookName).name}_${name}.png`),
      new Uint8Array(await preview.arrayBuffer()),
    );
  }
  await fs.mkdir(outputDir, { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path.join(outputDir, workbookName));

  const roundTrip = await SpreadsheetFile.importXlsx(
    await FileBlob.load(path.join(outputDir, workbookName)),
  );
  const roundTripErrors = await roundTrip.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: `${workbookName} round-trip formula error scan`,
    maxChars: 3000,
  });
  if (!roundTripErrors.ndjson.includes("matched 0 entries")) {
    throw new Error(`${workbookName} round-trip error scan failed: ${roundTripErrors.ndjson}`);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const outputDir = path.resolve(args["output-dir"]);
  const previewDir = path.resolve(args["preview-dir"]);
  const payload = JSON.parse(await fs.readFile(path.resolve(args.payload), "utf8"));
  const workbookNames = [payload.microtext, payload.visualdiff]
    .filter((item) => item && Number(item.rows) > 0)
    .map((item) => item.workbook);
  if (workbookNames.length === 0) throw new Error("payload contains no non-empty workbook");
  if (!args.overwrite) {
    for (const workbookName of workbookNames) {
      try {
        await fs.access(path.join(outputDir, workbookName));
        throw new Error(`${workbookName} already exists; pass --overwrite to rebuild`);
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }
  }
  const results = [];
  if (payload.microtext && Number(payload.microtext.rows) > 0) {
    results.push(await buildMicrotext(payload.microtext, outputDir, previewDir));
  }
  if (payload.visualdiff && Number(payload.visualdiff.rows) > 0) {
    results.push(await buildVisualdiff(payload.visualdiff, outputDir, previewDir));
  }
  const report = {
    goal: payload.goal,
    date_label: payload.date_label,
    workbooks: results,
    total_rows: results.reduce((sum, item) => sum + item.rows, 0),
    total_embedded_images: results.reduce((sum, item) => sum + item.embedded_images, 0),
    formula_error_scans: "pass",
    previews: results.length * 3,
  };
  await fs.writeFile(
    path.join(outputDir, "WORKBOOK_BUILD_REPORT.json"),
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8",
  );
  const batchReportPath = path.join(outputDir, "next_review_batch_build_report.json");
  try {
    const batchReport = JSON.parse(await fs.readFile(batchReportPath, "utf8"));
    batchReport.workbooks_pending = false;
    batchReport.workbook_build_report = "WORKBOOK_BUILD_REPORT.json";
    batchReport.workbook_rows = report.total_rows;
    batchReport.workbook_embedded_images = report.total_embedded_images;
    await fs.writeFile(batchReportPath, `${JSON.stringify(batchReport, null, 2)}\n`, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  console.log(JSON.stringify(report, null, 2));
}

await main();
