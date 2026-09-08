#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const COLORS = {
  teal: "#155E63",
  tealLight: "#DDEEEF",
  amber: "#FFF2CC",
  amberLight: "#FFF9E6",
  blue: "#DBEAFE",
  red: "#FEE2E2",
  green: "#DCFCE7",
  line: "#D8DEE4",
  text: "#17202A",
  muted: "#56616B",
  white: "#FFFFFF",
};

const FONT = "Microsoft YaHei";
const VISUAL_TYPES = [
  "text_change",
  "dimension_change",
  "symbol_component_change",
  "connection_wiring_change",
  "addition",
  "deletion",
  "geometry_change",
  "layout_only_no_change",
  "unclear",
];

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
  for (const required of ["payload", "output", "preview-dir", "report"]) {
    if (!args[required]) throw new Error(`missing --${required}`);
  }
  return args;
}

function pngDimensions(buffer) {
  if (buffer.length < 24 || buffer[0] !== 0x89 || buffer.toString("ascii", 1, 4) !== "PNG") {
    throw new Error("corrected evidence is not a PNG");
  }
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

function fitImage(dimensions, maxWidth, maxHeight) {
  const scale = Math.min(maxWidth / dimensions.width, maxHeight / dimensions.height);
  return {
    widthPx: Math.max(40, Math.round(dimensions.width * scale)),
    heightPx: Math.max(40, Math.round(dimensions.height * scale)),
  };
}

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function titleStyle(range) {
  range.format = {
    font: { bold: true, color: COLORS.teal, size: 16, name: FONT },
    verticalAlignment: "center",
    wrapText: true,
  };
}

function headerStyle(range) {
  range.format = {
    fill: COLORS.teal,
    font: { bold: true, color: COLORS.white, size: 10, name: FONT },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.white },
  };
}

function bodyStyle(range) {
  range.format = {
    font: { color: COLORS.text, size: 10, name: FONT },
    verticalAlignment: "center",
    wrapText: true,
    borders: {
      bottom: { style: "thin", color: COLORS.line },
    },
  };
}

function addDecisionFormatting(range) {
  for (const [code, fill] of Object.entries({
    1: COLORS.green,
    2: COLORS.blue,
    3: COLORS.red,
    4: COLORS.amber,
  })) {
    range.conditionalFormats.add("cellIs", {
      operator: "equal",
      formula: Number(code),
      format: { fill, font: { bold: true, color: COLORS.text } },
    });
  }
}

function addStatusFormatting(range) {
  range.conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"完成"',
    format: { fill: COLORS.green, font: { bold: true, color: "#166534" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "需",
    format: { fill: COLORS.red, font: { bold: true, color: "#991B1B" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "未完成",
    format: { fill: COLORS.amber, font: { bold: true, color: "#7C4A03" } },
  });
}

function writeInstructions(workbook, payload) {
  const counts = payload.counts;
  const placeholderMode = payload.mode === "active_visualdiff_placeholder_semantic_review";
  const sheet = workbook.worksheets.add("00说明");
  sheet.showGridLines = false;
  sheet.mergeCells("A2:J2");
  sheet.getRange("A2").values = [["Eng_Bench Gold v2.0 VisualDiff 校正证据复审"]];
  titleStyle(sheet.getRange("A2:J2"));
  sheet.getRange("A3:J3").format.borders = {
    bottom: { style: "medium", color: COLORS.teal },
  };

  sheet.getRange("A5:J5").values = [[
    "本轮需复审", counts.total,
    placeholderMode ? "双边文本建议" : "机器安全关闭",
    placeholderMode ? counts.text_grounded_proposals : counts.machine_confirmed_no_engineering_change,
    placeholderMode ? "单边文本需核对" : "人工已确认无变化",
    placeholderMode ? counts.unilateral_text_review_required : counts.human_confirmed_no_change,
    placeholderMode ? "纯图形判断" : "无需再做人审",
    placeholderMode ? counts.graphic_review_required : counts.machine_closed_no_more_human,
    "当前正式门禁", `${counts.formal_gate_pass} / ${counts.formal_gate_total}`,
  ]];
  headerStyle(sheet.getRange("A5:J5"));
  sheet.getRange("B5:D5").format.fill = COLORS.tealLight;
  sheet.getRange("F5:H5").format.fill = COLORS.tealLight;
  sheet.getRange("J5").format.fill = COLORS.amber;
  sheet.getRange("B5:J5").format.font = { bold: true, color: COLORS.text, size: 11, name: FONT };

  sheet.mergeCells("A7:J7");
  sheet.getRange("A7").values = [[
    placeholderMode
      ? "背景：这些训练集 VisualDiff 行的当前描述仍是占位符，而且旧红框映射存在错位风险。本文件按当前几何持有清单重建四联证据。机器文字建议只是线索，不能直接当作答案。"
      : "背景：旧工作簿中的部分 OLD/NEW 红框不是同一位置，可能把错位误判成变化。本文件已经用页级单应性重新对齐证据。旧判断不直接沿用，请按校正后的同坐标图片重新判断。",
  ]];
  sheet.getRange("A7:J7").format = {
    fill: COLORS.amber,
    font: { bold: true, color: "#7C4A03", size: 11, name: FONT },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange("A7:J7").format.rowHeightPx = 48;

  const steps = placeholderMode ? [
    ["1", "打开复审表", "进入“VisualDiff校正复审”。每行有四联证据和机器建议。当前 Gold 描述是占位符，因此每行都需要独立判断。"],
    ["2", "核对同一工程位置", "先比较第2栏 OLD corrected region 与第3栏 NEW corresponding region；第1栏显示原 OLD 映射，第4栏是 NEW 更大上下文。若第2、3栏不是同一工程对象，选4并说明需要重新定位。"],
    ["3", "核对机器建议", "只有 OLD 和 NEW 都抽到文字时，机器才给双边文字变化建议。单边文字统一标为 unclear，因为可能只是裁剪或对齐漂移；必须看图确认。"],
    ["4", "填判断代码", "1=真实工程变化且机器类型、描述都正确；2=真实变化但类型或描述需改；3=无工程变化、仅版式/字体/轻微偏移；4=证据不足或不是同一位置。"],
    ["5", "补全必填项", "所有判断都要写至少6个字的工程依据。选2必须填写正确类型和完整描述；选4必须在备注中写明缺少什么范围、页码或定位信息。"],
    ["6", "检查并交回", "J列必须全部显示“完成”。不要删除、插入、排序或复制行，不要改“机器数据_勿改”。保持原文件名和 .xlsx 格式交回。"],
  ] : [
    ["1", "打开复审表", "进入“VisualDiff校正复审”。每行证据图有四栏。不要只看第一栏，也不要复制旧答案。"],
    ["2", "只比第2和第3栏", "第2栏 OLD aligned at NEW 是校正后的旧图同坐标；第3栏 NEW crop 是新图。主要比较这两栏。第1栏和第4栏仅帮助诊断原红框为什么错位。"],
    ["3", "填判断代码", "1=确有工程变化且机器类型、描述都正确；2=确有工程变化但类型或描述错误；3=无工程变化、两图相同、仅渲染/字体/轻微偏移；4=校正后仍证据不足。"],
    ["4", "补全必填项", "所有判断都要写至少6个字的工程依据。选2必须填写正确变化类型和正确变化描述。选4还要在备注中写清需要哪种更大范围或上下文。"],
    ["5", "完成检查", "J列必须全部显示“完成”。不要删除、插入、排序或复制行；不要改“机器数据_勿改”。"],
    ["6", "保存交回", "建议每完成50条保存一次。保持原文件名和 .xlsx 格式，整份文件交回。复审结果回收后仍需机器做身份、来源、去重、泄漏和严格验证，才可能进入 Gold。"],
  ];
  sheet.getRange("A9:C14").values = steps;
  bodyStyle(sheet.getRange("A9:C14"));
  sheet.getRange("A9:A14").format = {
    fill: COLORS.teal,
    font: { bold: true, color: COLORS.white, size: 12, name: FONT },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("B9:B14").format = {
    fill: COLORS.tealLight,
    font: { bold: true, color: COLORS.teal, size: 10, name: FONT },
    verticalAlignment: "center",
  };
  sheet.getRange("A9:C14").format.rowHeightPx = 54;

  sheet.getRange("E9:F18").values = [
    ["变化类型", "判断示例"],
    ["text_change", "标注文字改变，且工程含义改变"],
    ["dimension_change", "尺寸、数值或公差改变"],
    ["symbol_component_change", "符号、设备或元件改变"],
    ["connection_wiring_change", "电气连线、管线或连接关系改变"],
    ["addition", "同一坐标新增有效工程内容"],
    ["deletion", "同一坐标删除有效工程内容"],
    ["geometry_change", "几何、边界或形状发生工程变化"],
    ["layout_only_no_change", "只有位置、字体、清晰度或渲染变化"],
    ["unclear", "仍无法可靠判断"],
  ];
  headerStyle(sheet.getRange("E9:F9"));
  bodyStyle(sheet.getRange("E10:F18"));
  sheet.getRange("E10:E18").format = {
    fill: COLORS.tealLight,
    font: { bold: true, color: COLORS.teal, size: 9, name: "Consolas" },
    verticalAlignment: "center",
  };

  sheet.mergeCells("E20:J22");
  sheet.getRange("E20").values = [[
    placeholderMode
      ? "关键规则：先确认第2栏和第3栏是同一工程对象。完全相同、仅旋转/字体/渲染/轻微偏移，或差异只在红框外，选3。两栏不是同一位置或框内信息不完整，选4。单边文字绝不能自动当作新增或删除。"
      : "关键规则：第2栏与第3栏完全一样或只有极小偏移时选3。第2栏为空、第3栏有内容，可能是真新增；第2栏有内容、第3栏为空，可能是真删除，不能因为一边空白就直接选3。差异只发生在红框外也选3。",
  ]];
  sheet.getRange("E20:J22").format = {
    fill: COLORS.amberLight,
    font: { bold: true, color: "#7C4A03", size: 10, name: FONT },
    wrapText: true,
    verticalAlignment: "center",
  };

  sheet.getRange("A:A").format.columnWidthPx = 64;
  sheet.getRange("B:B").format.columnWidthPx = 180;
  sheet.getRange("C:C").format.columnWidthPx = 540;
  sheet.getRange("D:D").format.columnWidthPx = 90;
  sheet.getRange("E:E").format.columnWidthPx = 210;
  sheet.getRange("F:F").format.columnWidthPx = 360;
  sheet.getRange("G:H").format.columnWidthPx = 150;
  sheet.getRange("I:I").format.columnWidthPx = 165;
  sheet.getRange("J:J").format.columnWidthPx = 100;
  sheet.getRange("A2:J22").format.verticalAlignment = "center";
  sheet.tabColor = COLORS.teal;
}

function machineSummary(row, placeholderMode) {
  const description = row.change_description || "(机器未提供描述)";
  const oldBasis = row.original_reviewer_basis || "(原主审未写依据)";
  if (placeholderMode) {
    return [
      `机器建议类型：${row.change_type}`,
      `机器建议描述：${description}`,
      `证据分流：${row.triage_lane}`,
      "当前状态：未审核占位符，不能直接进入 Gold",
    ].join("\n");
  }
  return [
    `机器类型：${row.change_type}`,
    `机器描述：${description}`,
    `原主审判断：${row.original_reviewer_decision_code} (${row.original_reviewer_status})`,
    `原主审依据：${oldBasis}`,
  ].join("\n");
}

function correctedTextSummary(row) {
  return [
    `OLD aligned：${row.corrected_old_text || "(空)"}`,
    `NEW：${row.corrected_new_text || "(空)"}`,
    `文字层关系：${row.corrected_text_relation || "unknown"}`,
  ].join("\n");
}

async function addEvidenceImages(sheet, rows, startRow) {
  for (let offset = 0; offset < rows.length; offset += 1) {
    const row = rows[offset];
    const buffer = await fs.readFile(row.evidence_path);
    if (sha256(buffer) !== row.evidence_sha256) {
      throw new Error(`evidence hash changed: ${row.record_id}`);
    }
    const extent = fitImage(pngDimensions(buffer), 950, 184);
    const excelRow = startRow + offset;
    sheet.images.add({
      dataUrl: `data:image/png;base64,${buffer.toString("base64")}`,
      anchor: {
        from: { row: excelRow - 1, col: 1, rowOffsetPx: 10, colOffsetPx: 10 },
        extent,
      },
    });
    if ((offset + 1) % 50 === 0) {
      process.stdout.write(`embedded corrected evidence: ${offset + 1}/${rows.length}\n`);
    }
  }
}

async function writeReviewSheet(workbook, payload) {
  const rows = payload.rows;
  const placeholderMode = payload.mode === "active_visualdiff_placeholder_semantic_review";
  const sheet = workbook.worksheets.add("VisualDiff校正复审");
  sheet.showGridLines = false;
  sheet.tabColor = COLORS.teal;
  const startRow = 9;
  const lastRow = startRow + rows.length - 1;

  sheet.mergeCells("A2:J2");
  sheet.getRange("A2").values = [[`VisualDiff 校正证据复审 - ${rows.length} 条`]];
  titleStyle(sheet.getRange("A2:J2"));
  sheet.getRange("A3:J3").format.borders = {
    bottom: { style: "medium", color: COLORS.teal },
  };

  sheet.getRange("A5:D5").values = [["已判断", "", "完成", ""]];
  headerStyle(sheet.getRange("A5:D5"));
  sheet.getRange("B5").formulas = [[`=COUNT(E${startRow}:E${lastRow})&" / ${rows.length}"`]];
  sheet.getRange("D5").formulas = [[`=COUNTIF(J${startRow}:J${lastRow},"完成")&" / ${rows.length}"`]];
  sheet.getRange("B5:D5").format.fill = COLORS.tealLight;
  sheet.getRange("B5:D5").format.font = { bold: true, color: COLORS.text, size: 11, name: FONT };

  sheet.mergeCells("A6:J6");
  sheet.getRange("A6").values = [[
    placeholderMode
      ? "正式比较第2栏 OLD corrected region 与第3栏 NEW corresponding region；第1栏是原 OLD 映射，第4栏是 NEW 更大上下文。若第2、3栏不是同一工程对象，选4。"
      : "每行只把第2栏 OLD aligned at NEW 与第3栏 NEW crop 当作正式对比证据。第1栏 OLD current crop 和第4栏 OLD proposed crop 只用于解释旧红框错位。",
  ]];
  sheet.getRange("A6:J6").format = {
    fill: COLORS.amber,
    font: { bold: true, color: "#7C4A03", size: 11, name: FONT },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange("A6:J6").format.rowHeightPx = 42;
  sheet.mergeCells("A7:J7");
  sheet.getRange("A7").values = [[
    "判断：1=真实变化且机器答案全对；2=真实变化但需改，必须填F和G；3=无工程变化；4=证据仍不足，必须在H写需要的上下文。所有情况都必须在I写至少6个字的工程依据。",
  ]];
  sheet.getRange("A7:J7").format = {
    fill: COLORS.tealLight,
    font: { bold: true, color: COLORS.teal, size: 10, name: FONT },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange("A7:J7").format.rowHeightPx = 42;

  sheet.getRange("A8:J8").values = [[
    "#",
    "四联校正证据图片",
    placeholderMode ? "机器建议 + 占位符状态" : "机器答案 + 原主审反馈",
    "校正文字层",
    "判断 1/2/3/4",
    "正确变化类型（仅2）",
    "正确变化描述（仅2）",
    "需要的上下文/备注（4必填）",
    "工程依据（全部必填）",
    "完成状态",
  ]];
  headerStyle(sheet.getRange("A8:J8"));
  sheet.getRange("A8:J8").format.rowHeightPx = 54;

  const values = rows.map((row) => [
    row.review_index,
    "",
    machineSummary(row, placeholderMode),
    correctedTextSummary(row),
    "",
    "",
    "",
    "",
    "",
    "",
  ]);
  sheet.getRangeByIndexes(startRow - 1, 0, rows.length, 10).values = values;
  bodyStyle(sheet.getRange(`A${startRow}:J${lastRow}`));
  sheet.getRange(`E${startRow}:I${lastRow}`).format.fill = COLORS.amberLight;
  sheet.getRange(`E${startRow}:E${lastRow}`).format = {
    fill: COLORS.amber,
    font: { bold: true, color: COLORS.text, size: 14, name: FONT },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange(`E${startRow}:E${lastRow}`).dataValidation = {
    rule: { type: "list", values: [1, 2, 3, 4] },
  };
  sheet.getRange(`F${startRow}:F${lastRow}`).dataValidation = {
    rule: { type: "list", values: VISUAL_TYPES },
  };

  const formulas = rows.map((row, offset) => {
    const excelRow = startRow + offset;
    return [
      `=IF(E${excelRow}="","未完成",IF(LEN(TRIM(I${excelRow}))<6,"需填至少6字工程依据",IF(E${excelRow}=2,IF(F${excelRow}="","需填正确类型",IF(G${excelRow}="","需填正确描述","完成")),IF(E${excelRow}=4,IF(LEN(TRIM(H${excelRow}))<4,"需说明缺少的上下文","完成"),"完成"))))`,
    ];
  });
  sheet.getRange(`J${startRow}:J${lastRow}`).formulas = formulas;
  addDecisionFormatting(sheet.getRange(`E${startRow}:E${lastRow}`));
  addStatusFormatting(sheet.getRange(`J${startRow}:J${lastRow}`));

  sheet.getRange("A:A").format.columnWidthPx = 56;
  sheet.getRange("B:B").format.columnWidthPx = 980;
  sheet.getRange("C:C").format.columnWidthPx = 400;
  sheet.getRange("D:D").format.columnWidthPx = 260;
  sheet.getRange("E:E").format.columnWidthPx = 118;
  sheet.getRange("F:F").format.columnWidthPx = 200;
  sheet.getRange("G:G").format.columnWidthPx = 320;
  sheet.getRange("H:H").format.columnWidthPx = 260;
  sheet.getRange("I:I").format.columnWidthPx = 320;
  sheet.getRange("J:J").format.columnWidthPx = 120;
  sheet.getRange(`A${startRow}:J${lastRow}`).format.rowHeightPx = 210;
  sheet.freezePanes.freezeRows(8);
  sheet.freezePanes.freezeColumns(1);
  await addEvidenceImages(sheet, rows, startRow);
}

function writeMachineSheet(workbook, rows) {
  const sheet = workbook.worksheets.add("机器数据_勿改");
  sheet.showGridLines = false;
  const headers = [
    "review_index",
    "original_primary_index",
    "record_id",
    "project_id",
    "reserved_split",
    "evidence_path",
    "evidence_sha256",
    "machine_change_type_raw",
    "normalized_change_type",
    "corrected_old_text",
    "corrected_new_text",
    "corrected_text_relation",
    "original_reviewer_decision_code",
    "original_reviewer_status",
    "homography_path",
    "bbox_old_recommended_from_new",
    "bbox_new_current",
    "requires_new_human_review",
    "safe_to_merge_gold",
  ];
  const values = rows.map((row) => [
    row.review_index,
    row.original_primary_index,
    row.record_id,
    row.project_id,
    row.reserved_split,
    row.evidence_path,
    row.evidence_sha256,
    row.machine_change_type_raw,
    row.change_type,
    row.corrected_old_text,
    row.corrected_new_text,
    row.corrected_text_relation,
    row.original_reviewer_decision_code,
    row.original_reviewer_status,
    row.homography_path,
    JSON.stringify(row.bbox_old_recommended_from_new),
    JSON.stringify(row.bbox_new_current),
    true,
    false,
  ]);
  sheet.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
  sheet.getRangeByIndexes(1, 0, values.length, headers.length).values = values;
  headerStyle(sheet.getRange("A1:S1"));
  sheet.getRange(`A2:S${values.length + 1}`).format = {
    font: { color: COLORS.muted, size: 9, name: "Consolas" },
    verticalAlignment: "center",
    wrapText: false,
  };
  sheet.getRange("A:S").format.columnWidthPx = 150;
  sheet.getRange("C:D").format.columnWidthPx = 360;
  sheet.getRange("F:G").format.columnWidthPx = 420;
  sheet.getRange("O:Q").format.columnWidthPx = 300;
  sheet.freezePanes.freezeRows(1);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const payloadPath = path.resolve(args.payload);
  const outputPath = path.resolve(args.output);
  const previewDir = path.resolve(args["preview-dir"]);
  const reportPath = path.resolve(args.report);
  const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
  const rows = payload.rows;
  if (!Array.isArray(rows) || rows.length !== payload.counts.total || rows.some((row) => row.task !== "visualdiff")) {
    throw new Error("payload is not a complete VisualDiff rereview set");
  }
  if (new Set(rows.map((row) => row.record_id)).size !== rows.length) {
    throw new Error("payload contains duplicate record identities");
  }
  if (rows.some((row) => row.safe_to_merge_gold !== false || row.requires_new_human_review !== true)) {
    throw new Error("payload is not fail-closed");
  }
  if (!args.overwrite) {
    try {
      await fs.access(outputPath);
      throw new Error(`output already exists: ${outputPath}`);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.mkdir(previewDir, { recursive: true });

  const workbook = Workbook.create();
  writeInstructions(workbook, payload);
  await writeReviewSheet(workbook, payload);
  writeMachineSheet(workbook, rows);
  workbook.recalculate();

  const instructionInspect = await workbook.inspect({
    kind: "table",
    range: "00说明!A2:J22",
    include: "values,formulas",
    tableMaxRows: 24,
    tableMaxCols: 10,
  });
  await fs.writeFile(path.join(previewDir, "instruction_inspect.ndjson"), instructionInspect.ndjson, "utf8");
  const reviewInspect = await workbook.inspect({
    kind: "table",
    range: "VisualDiff校正复审!A5:J12",
    include: "values,formulas",
    tableMaxRows: 10,
    tableMaxCols: 10,
  });
  await fs.writeFile(path.join(previewDir, "review_start_inspect.ndjson"), reviewInspect.ndjson, "utf8");
  const formulaErrors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 300 },
    summary: "final formula error scan",
  });
  await fs.writeFile(path.join(previewDir, "formula_error_scan.ndjson"), formulaErrors.ndjson, "utf8");
  if (!formulaErrors.ndjson.includes("matched 0 entries")) {
    throw new Error("formula error scan found one or more errors");
  }

  const lastRow = 8 + rows.length;
  const middleRow = 9 + Math.floor(rows.length / 2);
  const previews = [
    ["00说明", "A1:J22", "00_instructions.png"],
    ["VisualDiff校正复审", "A1:J12", "01_review_start.png"],
    ["VisualDiff校正复审", `A${middleRow}:J${Math.min(lastRow, middleRow + 2)}`, "02_review_middle.png"],
    ["VisualDiff校正复审", `A${Math.max(9, lastRow - 2)}:J${lastRow}`, "03_review_end.png"],
    ["机器数据_勿改", "A1:S8", "04_machine_data.png"],
  ];
  for (const [sheetName, range, filename] of previews) {
    const rendered = await workbook.render({ sheetName, range, scale: 0.75, format: "png" });
    await fs.writeFile(path.join(previewDir, filename), new Uint8Array(await rendered.arrayBuffer()));
  }

  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(outputPath);
  const outputBuffer = await fs.readFile(outputPath);
  const report = {
    status: "PASS",
    goal: payload.goal,
    mode: payload.mode,
    output: outputPath,
    output_bytes: outputBuffer.length,
    output_sha256: sha256(outputBuffer),
    sheets: ["00说明", "VisualDiff校正复审", "机器数据_勿改"],
    counts: payload.counts,
    embedded_image_target: rows.length,
    formula_error_matches: 0,
    gold_rows_modified: 0,
    safe_to_merge_gold: false,
  };
  await fs.mkdir(path.dirname(reportPath), { recursive: true });
  await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + "\n", "utf8");
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

await main();
