#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const COLORS = {
  teal: "#155E63",
  tealLight: "#DDEEEF",
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

const MICRO_CATEGORIES = [
  "dimension_value",
  "equipment_tag",
  "instrument_tag",
  "pin_label",
  "component_value",
  "pipe_line_tag",
  "process_value",
  "process_label",
  "room_label",
  "tolerance_value",
  "unknown_microtext",
  "reject_not_engineering_text",
];

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
    throw new Error("evidence image is not a PNG");
  }
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
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

function addDecisionFormatting(range) {
  for (const [code, fill] of Object.entries({
    1: COLORS.green,
    2: COLORS.blue,
    3: COLORS.red,
    4: COLORS.yellow,
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
    text: "需填",
    format: { fill: COLORS.red, font: { bold: true, color: "#991B1B" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "必须",
    format: { fill: COLORS.red, font: { bold: true, color: "#991B1B" } },
  });
}

async function addEvidenceImages(sheet, rows, startRow, maxWidth, maxHeight) {
  for (let offset = 0; offset < rows.length; offset += 1) {
    const buffer = await fs.readFile(rows[offset].evidence_path);
    const dimensions = pngDimensions(buffer);
    const extent = fitImage(dimensions, maxWidth, maxHeight);
    const excelRow = startRow + offset;
    sheet.images.add({
      dataUrl: `data:image/png;base64,${buffer.toString("base64")}`,
      anchor: {
        from: { row: excelRow - 1, col: 1, rowOffsetPx: 7, colOffsetPx: 7 },
        extent,
      },
    });
    if ((offset + 1) % 100 === 0) {
      process.stdout.write(`embedded ${sheet.name}: ${offset + 1}/${rows.length}\n`);
    }
  }
}

function writeInstructions(workbook, counts) {
  const microLastRow = 6 + counts.microtext;
  const visualLastRow = 6 + counts.visualdiff;
  const engineeringLastRow = 6 + counts.engineering_total;
  const sheet = workbook.worksheets.add("00说明");
  sheet.showGridLines = false;
  sheet.mergeCells("A1:J2");
  sheet.getRange("A1").values = [["Eng_Bench Gold v2.0 Global - 主审实习生追赶批次"]];
  styleTitle(sheet.getRange("A1:J2"));

  sheet.getRange("A4:J4").values = [[
    "主审任务", counts.total,
    "MicroText", counts.microtext,
    "VisualDiff", counts.visualdiff,
    "工程知识动作", counts.engineering_actions_total || counts.engineering_total,
    "当前正式门禁", "3 / 9",
  ]];
  styleHeader(sheet.getRange("A4:J4"));
  sheet.getRange("B4:D4").format.fill = COLORS.tealLight;
  sheet.getRange("F4:H4").format.fill = COLORS.tealLight;
  sheet.getRange("J4").format.fill = COLORS.yellow;
  sheet.getRange("B4:D4").format.font = { bold: true, color: COLORS.teal, size: 11, name: "Microsoft YaHei" };
  sheet.getRange("F4:H4").format.font = { bold: true, color: COLORS.teal, size: 11, name: "Microsoft YaHei" };
  sheet.getRange("J4").format.font = { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" };

  sheet.mergeCells("A6:J6");
  sheet.getRange("A6").values = [[
    `交付目标：只使用本文件完成 ${counts.total.toLocaleString("en-US")} 条主审和 ${counts.specialist_total || 0} 条独立工程专项，共 ${counts.total_human_actions || counts.total} 个动作。主审中 ${counts.engineering_total.toLocaleString("en-US")} 条必须写工程依据；旧工作簿已经作废，不要再填。审核结果不会自动进入 Gold；回收后还要经过身份、来源、去重、泄漏和严格验证。`,
  ]];
  sheet.getRange("A6:J6").format = {
    fill: COLORS.yellow,
    font: { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange("A6:J6").format.rowHeightPx = 48;

  const steps = [
    ["1", "先做主审", "依次打开“主审_MicroText”和“主审_VisualDiff”。每行只看证据图、机器内容和红框内事实，在黄色“判断”格输入 1/2/3/4。"],
    ["2", "判断代码", "1=样本有效且机器文字/类别/描述都正确；2=样本有效但机器内容需要修改；3=样本无效、截图不完整、被删除线划掉，或 VisualDiff 无真实框内变化；4=证据不足，必须看更大范围或请工程人员判断。"],
    ["3", "选择 2 时", "MicroText：在“正确文字/正确类别”至少填写一项，未变字段可留空。VisualDiff：填写规范变化类型和/或正确变化描述。"],
    ["4", "工程知识专项", `主表中“工程深审原因”不为空的 ${counts.engineering_total} 行必须填写工程依据；另完成“专项_英文描述15”和“专项_MicroText3”共 ${counts.specialist_total || 0} 条。英文专项核对英文草稿和变化类型；MicroText 专项核对文字、类别和工程语义。`],
    ["5", "完成检查", `主审、工程任务清单和两个专项表的“完成状态”必须全部显示“完成”。其中 ${counts.mandatory_description_rewrite || 0} 条强制描述重写必须选 2，并填写类型、描述和工程依据。不要删除、插入、排序或复制行。`],
    ["6", "保存交回", "建议每完成 100 条按 Ctrl+S。使用桌面版 Excel/WPS，保持原文件名和 .xlsx 格式，整份文件交回。"],
  ];
  sheet.getRange("A8:C13").values = steps;
  styleData(sheet.getRange("A8:C13"));
  sheet.getRange("A8:A13").format = {
    fill: COLORS.teal,
    font: { bold: true, color: COLORS.white, size: 12, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("B8:B13").format = {
    fill: COLORS.tealLight,
    font: { bold: true, color: COLORS.teal, size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
  };
  sheet.getRange("C8:C13").format.rowHeightPx = 52;

  sheet.mergeCells("A15:C15");
  sheet.getRange("A15").values = [["MicroText 类别速查（工程深审必须按图纸语义判断，不能只看字符串形状）"]];
  styleHeader(sheet.getRange("A15:C15"));
  const categories = [
    ["dimension_value", "尺寸值", "R10、11.8°、3'-3\"、Ø25"],
    ["equipment_tag", "设备标签", "PUMP-101、MOTOR、BOILER NO.1"],
    ["instrument_tag", "仪表位号", "PT-101、FT-202、LIC-3"],
    ["pin_label", "引脚/端子/元件标号", "L1、Q3、R10、GP14、3V3；需结合上下文"],
    ["component_value", "元器件参数", "10k、100 nF、1 µH"],
    ["pipe_line_tag", "管线/介质/服务标签", "DRAIN、OIL TANK VENT、6\"-CW-101"],
    ["process_value", "工艺运行值", "10 bar、250 °C、40 gpm"],
    ["process_label", "工艺/系统/区域文字", "FEED、RETURN、FILTER SECTION"],
    ["room_label", "房间/空间标签", "DECK、KITCHEN、HALL、PLATFORM"],
    ["tolerance_value", "公差值", "±0.05、+0.1/-0.0"],
    ["unknown_microtext", "机器无法确定", "必须由人判断有效性并改成规范类别，或判 3"],
    ["reject_not_engineering_text", "非有效工程文字", "页眉、网址、扫描噪声、截断字符、被划掉文字"],
  ];
  sheet.getRange("A16:C27").values = categories;
  styleData(sheet.getRange("A16:C27"));
  sheet.getRange("A16:A27").format = { fill: COLORS.tealLight, font: { bold: true, color: COLORS.teal, size: 9, name: "Consolas" } };

  sheet.mergeCells("E15:J15");
  sheet.getRange("E15").values = [["VisualDiff 变化类型速查（只判断 OLD/NEW 红框内）"]];
  styleHeader(sheet.getRange("E15:J15"));
  const changes = [
    ["text_change", "文字内容改变且工程含义改变"],
    ["dimension_change", "尺寸、数值或公差改变"],
    ["symbol_component_change", "符号、设备或元件发生改变"],
    ["connection_wiring_change", "连线、管线或连接关系改变"],
    ["addition", "红框内新增有效工程内容"],
    ["deletion", "红框内删除有效工程内容"],
    ["geometry_change", "几何形状/边界改变且具有工程含义"],
    ["layout_only_no_change", "仅位置、字体、清晰度、渲染或极小偏移；没有工程变化"],
    ["unclear", "证据不足，不能可靠判断"],
  ];
  sheet.getRange("E16:F24").values = changes;
  styleData(sheet.getRange("E16:F24"));
  sheet.getRange("E16:E24").format = { fill: COLORS.tealLight, font: { bold: true, color: COLORS.teal, size: 9, name: "Consolas" } };

  sheet.mergeCells("E26:J27");
  sheet.getRange("E26").values = [[
    "易错规则：两图完全一样、仅整体轻微偏移、只换字体/清晰度、或差异发生在红框外，都选 3。截图缺字母时，即使机器从上下文猜对完整词，也选 3；明确被删除线划掉的旧标签也选 3。",
  ]];
  sheet.getRange("E26:J27").format = {
    fill: COLORS.paleYellow,
    font: { bold: true, color: "#7C4A03", size: 10, name: "Microsoft YaHei" },
    wrapText: true,
    verticalAlignment: "center",
  };

  sheet.mergeCells("A29:J29");
  sheet.getRange("A29").values = [["实时进度（由主表自动计算）"]];
  styleHeader(sheet.getRange("A29:J29"));
  sheet.getRange("A30:J30").values = [["已填写判断", "", "主审完成", "", "工程深审", "", "英文专项", "", "MicroText专项", ""]];
  styleHeader(sheet.getRange("A30:J30"));
  sheet.getRange("B30").formulas = [[`=COUNT('主审_MicroText'!E7:E${microLastRow})+COUNT('主审_VisualDiff'!E7:E${visualLastRow})&" / ${counts.total}"`]];
  sheet.getRange("D30").formulas = [[`=COUNTIF('主审_MicroText'!J7:J${microLastRow},"完成")+COUNTIF('主审_VisualDiff'!J7:J${visualLastRow},"完成")&" / ${counts.total}"`]];
  sheet.getRange("F30").formulas = [[`=COUNTIF('工程任务清单'!H7:H${engineeringLastRow},"完成")&" / ${counts.engineering_total}"`]];
  sheet.getRange("H30").formulas = [[`=COUNTIF('专项_英文描述15'!L7:L${6 + (counts.specialist_visualdiff_english || 0)},"完成")&" / ${counts.specialist_visualdiff_english || 0}"`]];
  sheet.getRange("J30").formulas = [[`=COUNTIF('专项_MicroText3'!J7:J${6 + (counts.specialist_microtext_balance || 0)},"完成")&" / ${counts.specialist_microtext_balance || 0}"`]];
  sheet.getRange("B30:J30").format.fill = COLORS.grey;
  sheet.getRange("B30:J30").format.font = { bold: true, color: COLORS.text, size: 11, name: "Microsoft YaHei" };

  sheet.getRange("A:A").format.columnWidthPx = 55;
  sheet.getRange("B:B").format.columnWidthPx = 145;
  sheet.getRange("C:C").format.columnWidthPx = 540;
  sheet.getRange("D:D").format.columnWidthPx = 92;
  sheet.getRange("E:E").format.columnWidthPx = 190;
  sheet.getRange("F:F").format.columnWidthPx = 390;
  sheet.getRange("G:J").format.columnWidthPx = 80;
  sheet.freezePanes.freezeRows(4);
}

async function writeSpecialistVisualSheet(workbook, rows) {
  const sheet = workbook.worksheets.add("专项_英文描述15");
  sheet.showGridLines = false;
  const startRow = 7;
  const lastRow = startRow + rows.length - 1;
  sheet.mergeCells("A1:L1");
  sheet.getRange("A1").values = [[`VisualDiff 英文描述专项 - ${rows.length} 条（独立于主审任务）`]];
  styleTitle(sheet.getRange("A1:L1"));
  sheet.getRange("A1:L1").format.rowHeightPx = 40;
  sheet.mergeCells("A2:L2");
  sheet.getRange("A2").values = [["逐行对照 OLD/NEW 图、中文已审描述和英文草稿。accepted=英文草稿准确；edited=真实变化成立但英文需改；rejected=图不支持变化；needs_full_page=裁剪不足。"]];
  sheet.getRange("A2:L2").format = { fill: COLORS.yellow, font: { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.mergeCells("A3:L3");
  sheet.getRange("A3").values = [["Type required=YES 的行必须在 Corrected type 选择规范变化类型。edited 必须填写完整 Corrected English；rejected/needs_full_page 必须写 Notes。英文只描述图中可见事实，不推断设计意图。"]];
  sheet.getRange("A3:L3").format = { fill: COLORS.tealLight, font: { bold: true, color: COLORS.teal, size: 10, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A4:D4").values = [["已填写", "", "完成", ""]];
  styleHeader(sheet.getRange("A4:D4"));
  sheet.getRange("B4").formulas = [[`=COUNTA(H${startRow}:H${lastRow})&" / ${rows.length}"`]];
  sheet.getRange("D4").formulas = [[`=COUNTIF(L${startRow}:L${lastRow},"完成")&" / ${rows.length}"`]];
  sheet.getRange("B4:D4").format.fill = COLORS.grey;
  sheet.getRange("B4:D4").format.font = { bold: true, color: COLORS.text, size: 11, name: "Microsoft YaHei" };
  sheet.mergeCells("A5:L5");
  sheet.getRange("A5").values = [["示例：The resistor value annotation changed from 10R to 220R. 保留图中位号、网络名和数值原样；不要只写 text changed、different 或 layout changed。"]];
  sheet.getRange("A5:L5").format = { fill: COLORS.paleYellow, font: { color: "#7C4A03", size: 10, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A6:L6").values = [["#", "OLD / NEW 证据", "OLD -> NEW 文字", "中文已审描述", "机器类型", "Type required", "Proposed English", "审核状态", "Corrected English", "Corrected type", "Notes", "完成状态"]];
  styleHeader(sheet.getRange("A6:L6"));
  sheet.getRange("A6:L6").format.rowHeightPx = 48;
  const values = rows.map((row) => [
    row.specialist_index,
    "",
    `${row.old_text || "(空)"} -> ${row.new_text || "(空)"}`,
    row.chinese_description,
    row.current_change_type,
    row.type_required ? "YES" : "NO",
    row.proposed_english_description,
    "",
    "",
    "",
    "",
    "",
  ]);
  sheet.getRangeByIndexes(startRow - 1, 0, rows.length, 12).values = values;
  styleData(sheet.getRange(`A${startRow}:L${lastRow}`));
  sheet.getRange(`H${startRow}:K${lastRow}`).format.fill = COLORS.paleYellow;
  sheet.getRange(`H${startRow}:H${lastRow}`).dataValidation = { rule: { type: "list", values: ["accepted", "edited", "rejected", "needs_full_page"] } };
  sheet.getRange(`J${startRow}:J${lastRow}`).dataValidation = { rule: { type: "list", values: VISUAL_TYPES } };
  const formulas = rows.map((row, offset) => {
    const excelRow = startRow + offset;
    return [`=IF(H${excelRow}="","未完成",IF(OR(H${excelRow}="rejected",H${excelRow}="needs_full_page"),IF(K${excelRow}="","需填备注","完成"),IF(AND(F${excelRow}="YES",J${excelRow}=""),"需确认类型",IF(AND(H${excelRow}="edited",I${excelRow}=""),"需填修订英文","完成"))))`];
  });
  sheet.getRange(`L${startRow}:L${lastRow}`).formulas = formulas;
  addStatusFormatting(sheet.getRange(`L${startRow}:L${lastRow}`));
  sheet.getRange("A:A").format.columnWidthPx = 52;
  sheet.getRange("B:B").format.columnWidthPx = 400;
  sheet.getRange("C:C").format.columnWidthPx = 240;
  sheet.getRange("D:D").format.columnWidthPx = 360;
  sheet.getRange("E:F").format.columnWidthPx = 160;
  sheet.getRange("G:G").format.columnWidthPx = 430;
  sheet.getRange("H:H").format.columnWidthPx = 160;
  sheet.getRange("I:I").format.columnWidthPx = 430;
  sheet.getRange("J:J").format.columnWidthPx = 200;
  sheet.getRange("K:K").format.columnWidthPx = 280;
  sheet.getRange("L:L").format.columnWidthPx = 115;
  sheet.getRange(`A${startRow}:L${lastRow}`).format.rowHeightPx = 170;
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(1);
  await addEvidenceImages(sheet, rows, startRow, 380, 150);
}

async function writeSpecialistMicroSheet(workbook, rows) {
  const sheet = workbook.worksheets.add("专项_MicroText3");
  sheet.showGridLines = false;
  const startRow = 7;
  const lastRow = startRow + rows.length - 1;
  sheet.mergeCells("A1:J1");
  sheet.getRange("A1").values = [[`MicroText 类别平衡专项 - ${rows.length} 条（必须写工程依据）`]];
  styleTitle(sheet.getRange("A1:J1"));
  sheet.getRange("A1:J1").format.rowHeightPx = 40;
  sheet.mergeCells("A2:J2");
  sheet.getRange("A2").values = [["同时检查截图边界、文字逐字准确性、类别和工程语义。accepted=全对；edited=有效但文字或类别需改；rejected=无效；needs_full_page=需要整页。"]];
  sheet.getRange("A2:J2").format = { fill: COLORS.yellow, font: { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.mergeCells("A3:J3");
  sheet.getRange("A3").values = [["这 3 条补充 instrument_tag、dimension_value 和 equipment_tag。accepted/edited 都要在工程依据中说明为什么该字符串在当前图纸上下文中属于该类别。"]];
  sheet.getRange("A3:J3").format = { fill: COLORS.tealLight, font: { bold: true, color: COLORS.teal, size: 10, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A4:D4").values = [["已填写", "", "完成", ""]];
  styleHeader(sheet.getRange("A4:D4"));
  sheet.getRange("B4").formulas = [[`=COUNTA(E${startRow}:E${lastRow})&" / ${rows.length}"`]];
  sheet.getRange("D4").formulas = [[`=COUNTIF(J${startRow}:J${lastRow},"完成")&" / ${rows.length}"`]];
  sheet.getRange("B4:D4").format.fill = COLORS.grey;
  sheet.getRange("B4:D4").format.font = { bold: true, color: COLORS.text, size: 11, name: "Microsoft YaHei" };
  sheet.mergeCells("A5:J5");
  sheet.getRange("A5").values = [["类别示例：PR-02=instrument_tag；3/4\"=dimension_value；Hydrotreater=equipment_tag。若截图缺字或上下文不足，不要根据机器答案补全。"]];
  sheet.getRange("A5:J5").format = { fill: COLORS.paleYellow, font: { color: "#7C4A03", size: 10, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A6:J6").values = [["#", "证据图片", "Proposed text", "Proposed category", "审核状态", "Corrected text", "Corrected category", "工程依据", "Notes", "完成状态"]];
  styleHeader(sheet.getRange("A6:J6"));
  sheet.getRange("A6:J6").format.rowHeightPx = 48;
  const values = rows.map((row) => [row.specialist_index, "", row.proposed_text, row.category, "", "", "", "", "", ""]);
  sheet.getRangeByIndexes(startRow - 1, 0, rows.length, 10).values = values;
  styleData(sheet.getRange(`A${startRow}:J${lastRow}`));
  sheet.getRange(`E${startRow}:I${lastRow}`).format.fill = COLORS.paleYellow;
  sheet.getRange(`E${startRow}:E${lastRow}`).dataValidation = { rule: { type: "list", values: ["accepted", "edited", "rejected", "needs_full_page"] } };
  sheet.getRange(`G${startRow}:G${lastRow}`).dataValidation = { rule: { type: "list", values: MICRO_CATEGORIES } };
  const formulas = rows.map((row, offset) => {
    const excelRow = startRow + offset;
    return [`=IF(E${excelRow}="","未完成",IF(OR(E${excelRow}="rejected",E${excelRow}="needs_full_page"),IF(I${excelRow}="","需填备注","完成"),IF(E${excelRow}="edited",IF(COUNTA(F${excelRow}:G${excelRow})=0,"需填写修改",IF(H${excelRow}="","需填工程依据","完成")),IF(H${excelRow}="","需填工程依据","完成"))))`];
  });
  sheet.getRange(`J${startRow}:J${lastRow}`).formulas = formulas;
  addStatusFormatting(sheet.getRange(`J${startRow}:J${lastRow}`));
  sheet.getRange("A:A").format.columnWidthPx = 52;
  sheet.getRange("B:B").format.columnWidthPx = 400;
  sheet.getRange("C:D").format.columnWidthPx = 190;
  sheet.getRange("E:E").format.columnWidthPx = 165;
  sheet.getRange("F:G").format.columnWidthPx = 190;
  sheet.getRange("H:I").format.columnWidthPx = 320;
  sheet.getRange("J:J").format.columnWidthPx = 115;
  sheet.getRange(`A${startRow}:J${lastRow}`).format.rowHeightPx = 170;
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(1);
  await addEvidenceImages(sheet, rows, startRow, 380, 150);
}

function microPrompt(row) {
  const text = row.proposed_text || "(机器未提供文字)";
  return `机器文字：${text}\n机器类别：${row.category}\n问题：截图完整、文字准确且类别符合工程语义吗？`;
}

function visualPrompt(row) {
  const oldText = row.old_text || "(空)";
  const newText = row.new_text || "(空)";
  const desc = row.change_description || "(机器未提供描述)";
  const mandatory = row.mandatory_description_rewrite ? "【强制工程描述重写：必须选2并重写】\n" : "";
  return `${mandatory}机器类型：${row.change_type}\n机器文字：${oldText} -> ${newText}\n机器描述：${desc}\n问题：红框内有真实工程变化，且机器类型/描述正确吗？`;
}

async function writeMicroSheet(workbook, rows) {
  const sheet = workbook.worksheets.add("主审_MicroText");
  sheet.showGridLines = false;
  const startRow = 7;
  const lastRow = startRow + rows.length - 1;
  sheet.mergeCells("A1:J1");
  const engineeringCount = rows.filter((row) => row.engineering_required).length;
  sheet.getRange("A1").values = [[`MicroText 主审 - ${rows.length} 条（其中工程知识专项 ${engineeringCount} 条）`]];
  styleTitle(sheet.getRange("A1:J1"));
  sheet.getRange("A1:J1").format.rowHeightPx = 40;
  sheet.mergeCells("A2:J2");
  sheet.getRange("A2").values = [["每行：看 B 图 + C 机器内容，在黄色 E 列填 1/2/3/4。选 2 时在 F/G 至少改一项；H 有工程深审原因时必须填写 I 工程依据。"]];
  sheet.getRange("A2:J2").format = { fill: COLORS.yellow, font: { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A2:J2").format.rowHeightPx = 40;
  sheet.mergeCells("A3:J3");
  sheet.getRange("A3").values = [["1=全对；2=样本有效但文字/类别需改；3=无效、截图不完整、噪声或被划掉；4=证据不足。不要根据机器文字补全截图中看不到的字母。"]];
  sheet.getRange("A3:J3").format = { fill: COLORS.tealLight, font: { bold: true, color: COLORS.teal, size: 10, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A4:D4").values = [["已判断", "", "完成", ""]];
  styleHeader(sheet.getRange("A4:D4"));
  sheet.getRange("B4").formulas = [[`=COUNT(E${startRow}:E${lastRow})&" / ${rows.length}"`]];
  sheet.getRange("D4").formulas = [[`=COUNTIF(J${startRow}:J${lastRow},"完成")&" / ${rows.length}"`]];
  sheet.getRange("B4:D4").format.fill = COLORS.grey;
  sheet.getRange("B4:D4").format.font = { bold: true, color: COLORS.text, size: 11, name: "Microsoft YaHei" };
  sheet.mergeCells("A5:J5");
  sheet.getRange("A5").values = [["工程深审：判断它是否是有效工程标注、属于哪类、在当前图纸上下文中代表什么。工程依据要写出可复查的理由，例如“PT-101 符合仪表位号格式，位于 P&ID 测压支路”。"]];
  sheet.getRange("A5:J5").format = { fill: COLORS.paleYellow, font: { color: "#7C4A03", size: 10, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A5:J5").format.rowHeightPx = 36;
  sheet.getRange("A6:J6").values = [["#", "证据图片", "机器内容 + 审核问题", "机器类别", "判断 1/2/3/4", "正确文字（仅 2）", "正确类别（仅 2）", "工程深审原因", "工程依据/说明", "完成状态"]];
  styleHeader(sheet.getRange("A6:J6"));
  sheet.getRange("A6:J6").format.rowHeightPx = 48;
  const values = rows.map((row) => [
    row.primary_index,
    "",
    microPrompt(row),
    row.category,
    "",
    "",
    "",
    row.engineering_reason || "",
    "",
    "",
  ]);
  sheet.getRangeByIndexes(startRow - 1, 0, rows.length, 10).values = values;
  styleData(sheet.getRange(`A${startRow}:J${lastRow}`));
  sheet.getRange(`E${startRow}:E${lastRow}`).format = { fill: COLORS.yellow, font: { bold: true, color: COLORS.text, size: 14, name: "Microsoft YaHei" }, horizontalAlignment: "center", verticalAlignment: "center" };
  sheet.getRange(`F${startRow}:G${lastRow}`).format.fill = COLORS.paleYellow;
  sheet.getRange(`I${startRow}:I${lastRow}`).format.fill = COLORS.grey;
  sheet.getRange(`E${startRow}:E${lastRow}`).dataValidation = { rule: { type: "list", values: [1, 2, 3, 4] } };
  sheet.getRange(`G${startRow}:G${lastRow}`).dataValidation = { rule: { type: "list", values: MICRO_CATEGORIES } };
  sheet.getRange(`H${startRow}:H${lastRow}`).conditionalFormats.addCustom(
    `=$H${startRow}<>""`,
    { fill: COLORS.blue, font: { bold: true, color: "#1E3A8A" } },
  );
  sheet.getRange(`I${startRow}:I${lastRow}`).conditionalFormats.addCustom(
    `=$H${startRow}<>""`,
    { fill: COLORS.yellow, font: { color: COLORS.text } },
  );
  const formulas = rows.map((row, offset) => {
    const excelRow = startRow + offset;
    return [`=IF(E${excelRow}="","未完成",IF(AND(E${excelRow}=2,COUNTA(F${excelRow}:G${excelRow})=0),"需填写修改",IF(AND(H${excelRow}<>"",I${excelRow}=""),"需填工程依据","完成")))`];
  });
  sheet.getRange(`J${startRow}:J${lastRow}`).formulas = formulas;
  addDecisionFormatting(sheet.getRange(`E${startRow}:E${lastRow}`));
  addStatusFormatting(sheet.getRange(`J${startRow}:J${lastRow}`));
  sheet.getRange("A:A").format.columnWidthPx = 52;
  sheet.getRange("B:B").format.columnWidthPx = 370;
  sheet.getRange("C:C").format.columnWidthPx = 420;
  sheet.getRange("D:D").format.columnWidthPx = 155;
  sheet.getRange("E:E").format.columnWidthPx = 120;
  sheet.getRange("F:F").format.columnWidthPx = 180;
  sheet.getRange("G:G").format.columnWidthPx = 175;
  sheet.getRange("H:H").format.columnWidthPx = 260;
  sheet.getRange("I:I").format.columnWidthPx = 300;
  sheet.getRange("J:J").format.columnWidthPx = 105;
  sheet.getRange(`A${startRow}:J${lastRow}`).format.rowHeightPx = 126;
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(1);
  await addEvidenceImages(sheet, rows, startRow, 350, 106);
}

async function writeVisualSheet(workbook, rows) {
  const sheet = workbook.worksheets.add("主审_VisualDiff");
  sheet.showGridLines = false;
  const startRow = 7;
  const lastRow = startRow + rows.length - 1;
  sheet.mergeCells("A1:J1");
  const engineeringCount = rows.filter((row) => row.engineering_required).length;
  const mandatoryRewriteCount = rows.filter((row) => row.mandatory_description_rewrite).length;
  sheet.getRange("A1").values = [[`VisualDiff 主审 - ${rows.length} 条（工程知识专项 ${engineeringCount} 条；强制描述重写 ${mandatoryRewriteCount} 条）`]];
  styleTitle(sheet.getRange("A1:J1"));
  sheet.getRange("A1:J1").format.rowHeightPx = 40;
  sheet.mergeCells("A2:J2");
  sheet.getRange("A2").values = [[`只比较 OLD/NEW 红框内：E 列填 1/2/3/4。普通行选 2 时在 F/G 至少改一项；${mandatoryRewriteCount} 条【强制工程描述重写】必须选 2，同时填写 F、G、I。`]];
  sheet.getRange("A2:J2").format = { fill: COLORS.yellow, font: { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A2:J2").format.rowHeightPx = 40;
  sheet.mergeCells("A3:J3");
  sheet.getRange("A3").values = [["1=真实工程变化且机器类型/描述正确；2=真实变化但类型/描述需改；3=两图相同、仅版式/渲染/轻微偏移、变化在框外或无有效变化；4=证据不足。"]];
  sheet.getRange("A3:J3").format = { fill: COLORS.tealLight, font: { bold: true, color: COLORS.teal, size: 10, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A4:D4").values = [["已判断", "", "完成", ""]];
  styleHeader(sheet.getRange("A4:D4"));
  sheet.getRange("B4").formulas = [[`=COUNT(E${startRow}:E${lastRow})&" / ${rows.length}"`]];
  sheet.getRange("D4").formulas = [[`=COUNTIF(J${startRow}:J${lastRow},"完成")&" / ${rows.length}"`]];
  sheet.getRange("B4:D4").format.fill = COLORS.grey;
  sheet.getRange("B4:D4").format.font = { bold: true, color: COLORS.text, size: 11, name: "Microsoft YaHei" };
  sheet.mergeCells("A5:J5");
  sheet.getRange("A5").values = [["工程深审：说明变化如何影响尺寸、连接、设备/仪表、工艺或施工含义。若只是文字位置或清晰度改变，应选 3，并写明“无工程语义变化”。"]];
  sheet.getRange("A5:J5").format = { fill: COLORS.paleYellow, font: { color: "#7C4A03", size: 10, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A5:J5").format.rowHeightPx = 36;
  sheet.getRange("A6:J6").values = [["#", "OLD / NEW 证据图片", "机器内容 + 审核问题", "机器变化类型", "判断 1/2/3/4", "正确变化类型（仅 2）", "正确变化描述（仅 2）", "工程深审原因", "工程依据/说明", "完成状态"]];
  styleHeader(sheet.getRange("A6:J6"));
  sheet.getRange("A6:J6").format.rowHeightPx = 48;
  const values = rows.map((row) => [
    row.primary_index,
    "",
    visualPrompt(row),
    row.change_type,
    "",
    "",
    "",
    row.engineering_reason || "",
    "",
    "",
  ]);
  sheet.getRangeByIndexes(startRow - 1, 0, rows.length, 10).values = values;
  styleData(sheet.getRange(`A${startRow}:J${lastRow}`));
  sheet.getRange(`E${startRow}:E${lastRow}`).format = { fill: COLORS.yellow, font: { bold: true, color: COLORS.text, size: 14, name: "Microsoft YaHei" }, horizontalAlignment: "center", verticalAlignment: "center" };
  sheet.getRange(`F${startRow}:G${lastRow}`).format.fill = COLORS.paleYellow;
  sheet.getRange(`I${startRow}:I${lastRow}`).format.fill = COLORS.grey;
  sheet.getRange(`E${startRow}:E${lastRow}`).dataValidation = { rule: { type: "list", values: [1, 2, 3, 4] } };
  sheet.getRange(`F${startRow}:F${lastRow}`).dataValidation = { rule: { type: "list", values: VISUAL_TYPES } };
  sheet.getRange(`H${startRow}:H${lastRow}`).conditionalFormats.addCustom(
    `=$H${startRow}<>""`,
    { fill: COLORS.blue, font: { bold: true, color: "#1E3A8A" } },
  );
  sheet.getRange(`I${startRow}:I${lastRow}`).conditionalFormats.addCustom(
    `=$H${startRow}<>""`,
    { fill: COLORS.yellow, font: { color: COLORS.text } },
  );
  const formulas = rows.map((row, offset) => {
    const excelRow = startRow + offset;
    if (row.mandatory_description_rewrite) {
      return [`=IF(E${excelRow}="","未完成",IF(OR(E${excelRow}=3,E${excelRow}=4),IF(I${excelRow}="","需填工程依据","完成"),IF(E${excelRow}<>2,"必须选2重写",IF(F${excelRow}="","需填写类型",IF(G${excelRow}="","需填写描述",IF(I${excelRow}="","需填工程依据","完成"))))))`];
    }
    return [`=IF(E${excelRow}="","未完成",IF(AND(E${excelRow}=2,COUNTA(F${excelRow}:G${excelRow})=0),"需填写修改",IF(AND(H${excelRow}<>"",I${excelRow}=""),"需填工程依据","完成")))`];
  });
  sheet.getRange(`J${startRow}:J${lastRow}`).formulas = formulas;
  addDecisionFormatting(sheet.getRange(`E${startRow}:E${lastRow}`));
  addStatusFormatting(sheet.getRange(`J${startRow}:J${lastRow}`));
  sheet.getRange("A:A").format.columnWidthPx = 52;
  sheet.getRange("B:B").format.columnWidthPx = 400;
  sheet.getRange("C:C").format.columnWidthPx = 450;
  sheet.getRange("D:D").format.columnWidthPx = 170;
  sheet.getRange("E:E").format.columnWidthPx = 120;
  sheet.getRange("F:F").format.columnWidthPx = 190;
  sheet.getRange("G:G").format.columnWidthPx = 280;
  sheet.getRange("H:H").format.columnWidthPx = 260;
  sheet.getRange("I:I").format.columnWidthPx = 300;
  sheet.getRange("J:J").format.columnWidthPx = 105;
  sheet.getRange(`A${startRow}:J${lastRow}`).format.rowHeightPx = 170;
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(1);
  await addEvidenceImages(sheet, rows, startRow, 380, 150);
}

function writeEngineeringSheet(workbook, rows) {
  const sheet = workbook.worksheets.add("工程任务清单");
  sheet.showGridLines = false;
  const engineering = rows.filter((row) => row.engineering_required);
  sheet.mergeCells("A1:H1");
  sheet.getRange("A1").values = [[`工程知识专项 - ${engineering.length} 条（主审答案之外必须写工程依据）`]];
  styleTitle(sheet.getRange("A1:H1"));
  sheet.getRange("A1:H1").format.rowHeightPx = 40;
  sheet.mergeCells("A2:H2");
  sheet.getRange("A2").values = [["本页不重复填写答案。按“目标工作表”和“主审行位置”定位，在主表完成判断、必要修改和工程依据；本页状态会自动更新。"]];
  sheet.getRange("A2:H2").format = { fill: COLORS.yellow, font: { bold: true, color: "#7C4A03", size: 11, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A3:D3").values = [["工程任务完成", "", "总数", engineering.length]];
  styleHeader(sheet.getRange("A3:D3"));
  sheet.getRange("B3").formulas = [[`=COUNTIF(H7:H${6 + engineering.length},"完成")&" / ${engineering.length}"`]];
  sheet.getRange("B3:D3").format.fill = COLORS.grey;
  sheet.getRange("B3:D3").format.font = { bold: true, color: COLORS.text, size: 11, name: "Microsoft YaHei" };
  sheet.mergeCells("A4:H4");
  sheet.getRange("A4").values = [["最低要求：写出可复查的工程理由，不要只写“对/错”。例如类别判断依据、位号格式、图纸语境、连接关系、尺寸含义或为什么仅是版式变化。"]];
  sheet.getRange("A4:H4").format = { fill: COLORS.tealLight, font: { bold: true, color: COLORS.teal, size: 10, name: "Microsoft YaHei" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A6:H6").values = [["工程序号", "任务", "主审序号", "来源/图纸族", "需要工程判断的原因", "目标工作表", "主审行位置", "完成状态"]];
  styleHeader(sheet.getRange("A6:H6"));
  sheet.getRange("A6:H6").format.rowHeightPx = 44;
  const values = engineering.map((row) => {
    const sheetName = row.task === "microtext" ? "主审_MicroText" : "主审_VisualDiff";
    const taskRows = rows.filter((item) => item.task === row.task);
    const taskOffset = taskRows.findIndex((item) => item.record_id === row.record_id);
    const excelRow = 7 + taskOffset;
    return [
      row.engineering_index,
      row.task === "microtext" ? "MicroText" : "VisualDiff",
      row.primary_index,
      row.source_group,
      row.engineering_reason,
      sheetName,
      `到第 ${excelRow} 行`,
      "",
      excelRow,
    ];
  });
  sheet.getRangeByIndexes(6, 0, values.length, 8).values = values.map((row) => row.slice(0, 8));
  styleData(sheet.getRange(`A7:H${6 + engineering.length}`));
  for (let offset = 0; offset < engineering.length; offset += 1) {
    const excelRow = 7 + offset;
    const row = values[offset];
    const targetSheet = row[5];
    const targetExcelRow = row[8];
    sheet.getRange(`H${excelRow}`).formulas = [[`='${targetSheet}'!J${targetExcelRow}`]];
  }
  sheet.getRange(`G7:G${6 + engineering.length}`).format = { fill: COLORS.blue, font: { bold: true, color: "#1E3A8A", size: 10, name: "Microsoft YaHei" }, horizontalAlignment: "center", verticalAlignment: "center" };
  addStatusFormatting(sheet.getRange(`H7:H${6 + engineering.length}`));
  sheet.getRange("A:A").format.columnWidthPx = 85;
  sheet.getRange("B:B").format.columnWidthPx = 105;
  sheet.getRange("C:C").format.columnWidthPx = 95;
  sheet.getRange("D:D").format.columnWidthPx = 310;
  sheet.getRange("E:E").format.columnWidthPx = 460;
  sheet.getRange("F:F").format.columnWidthPx = 145;
  sheet.getRange("G:G").format.columnWidthPx = 130;
  sheet.getRange("H:H").format.columnWidthPx = 110;
  sheet.getRange(`A7:H${6 + engineering.length}`).format.rowHeightPx = 52;
  sheet.freezePanes.freezeRows(6);
}

function writeMachineSheet(workbook, rows) {
  const sheet = workbook.worksheets.add("机器数据_勿改");
  sheet.showGridLines = false;
  const headers = [
    "primary_index", "task", "record_id", "reserved_split", "source_group",
    "capacity_cohort", "evidence_path", "engineering_required", "engineering_index",
    "auditor_overlap", "retained_from_previous_primary", "safe_to_merge_gold",
    "carried_from_previous_workbook", "provenance_replacement", "mandatory_description_rewrite",
  ];
  const values = rows.map((row) => [
    row.primary_index,
    row.task,
    row.record_id,
    row.reserved_split,
    row.source_group,
    row.capacity_cohort,
    row.evidence_path,
    row.engineering_required,
    row.engineering_index || "",
    row.auditor_overlap,
    row.retained_from_previous_primary,
    false,
    Boolean(row.carried_from_previous_workbook),
    Boolean(row.provenance_replacement),
    Boolean(row.mandatory_description_rewrite),
  ]);
  sheet.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
  sheet.getRangeByIndexes(1, 0, values.length, headers.length).values = values;
  styleHeader(sheet.getRange("A1:O1"));
  sheet.getRange(`A2:O${values.length + 1}`).format = {
    font: { color: COLORS.text, size: 9, name: "Consolas" },
    wrapText: false,
  };
  sheet.getRange("A:O").format.columnWidthPx = 145;
  sheet.getRange("C:C").format.columnWidthPx = 330;
  sheet.getRange("E:G").format.columnWidthPx = 320;
  sheet.freezePanes.freezeRows(1);
}

function writeSpecialistMachineSheet(workbook, visualRows, microRows) {
  const sheet = workbook.worksheets.add("机器数据_专项勿改");
  sheet.showGridLines = false;
  const headers = ["specialist_index", "task", "record_id", "reserved_split", "source_group", "evidence_path", "evidence_sha256", "type_required", "safe_to_merge_gold"];
  const values = [...visualRows, ...microRows].map((row) => [
    row.specialist_index,
    row.task,
    row.record_id,
    row.reserved_split,
    row.source_group,
    row.evidence_path,
    row.evidence_sha256,
    Boolean(row.type_required),
    false,
  ]);
  sheet.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
  sheet.getRangeByIndexes(1, 0, values.length, headers.length).values = values;
  styleHeader(sheet.getRange("A1:I1"));
  sheet.getRange(`A2:I${values.length + 1}`).format = { font: { color: COLORS.text, size: 9, name: "Consolas" }, wrapText: false };
  sheet.getRange("A:I").format.columnWidthPx = 150;
  sheet.getRange("C:G").format.columnWidthPx = 330;
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
  const specialistVisual = payload.specialist?.visualdiff_english || [];
  const specialistMicro = payload.specialist?.microtext_balance || [];
  const micro = rows.filter((row) => row.task === "microtext");
  const visual = rows.filter((row) => row.task === "visualdiff");
  if (
    rows.length !== payload.counts.total
    || micro.length !== payload.counts.microtext
    || visual.length !== payload.counts.visualdiff
    || rows.filter((row) => row.engineering_required).length !== payload.counts.engineering_total
    || specialistVisual.length !== payload.counts.specialist_visualdiff_english
    || specialistMicro.length !== payload.counts.specialist_microtext_balance
  ) {
    throw new Error(`unexpected payload mix: ${rows.length}/${micro.length}/${visual.length}`);
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
  writeInstructions(workbook, payload.counts);
  await writeMicroSheet(workbook, micro);
  await writeVisualSheet(workbook, visual);
  writeEngineeringSheet(workbook, rows);
  await writeSpecialistVisualSheet(workbook, specialistVisual);
  await writeSpecialistMicroSheet(workbook, specialistMicro);
  writeMachineSheet(workbook, rows);
  writeSpecialistMachineSheet(workbook, specialistVisual, specialistMicro);

  const inspect = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: "formula error scan",
  });
  await fs.writeFile(path.join(previewDir, "formula_error_scan.ndjson"), inspect.ndjson, "utf8");
  const microLastRow = 6 + micro.length;
  const visualLastRow = 6 + visual.length;
  const engineeringLastRow = 6 + payload.counts.engineering_total;
  const previews = [
    ["00说明", "A1:J30", "00_instructions.png"],
    ["主审_MicroText", "A1:J12", "01_microtext_start.png"],
    ["主审_MicroText", `A${Math.max(7, microLastRow - 7)}:J${microLastRow}`, "02_microtext_end.png"],
    ["主审_VisualDiff", "A1:J11", "03_visualdiff_start.png"],
    ["主审_VisualDiff", `A${Math.max(7, visualLastRow - 7)}:J${visualLastRow}`, "04_visualdiff_end.png"],
    ["工程任务清单", "A1:H15", "05_engineering_start.png"],
    ["工程任务清单", `A${Math.max(7, engineeringLastRow - 8)}:H${engineeringLastRow}`, "06_engineering_end.png"],
    ["专项_英文描述15", "A1:L21", "07_specialist_visual.png"],
    ["专项_MicroText3", "A1:J9", "08_specialist_micro.png"],
    ["机器数据_勿改", "A1:O8", "09_machine_start.png"],
    ["机器数据_专项勿改", "A1:I8", "10_specialist_machine.png"],
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
    output: outputPath,
    output_bytes: outputBuffer.length,
    output_sha256: crypto.createHash("sha256").update(outputBuffer).digest("hex"),
    sheets: ["00说明", "主审_MicroText", "主审_VisualDiff", "工程任务清单", "专项_英文描述15", "专项_MicroText3", "机器数据_勿改", "机器数据_专项勿改"],
    counts: payload.counts,
    embedded_image_target: rows.length + specialistVisual.length + specialistMicro.length,
    gold_rows_modified: 0,
  };
  await fs.mkdir(path.dirname(reportPath), { recursive: true });
  await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + "\n", "utf8");
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

await main();
