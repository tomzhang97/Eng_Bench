#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || !value) throw new Error(`invalid argument near ${key || "end"}`);
    args[key.slice(2)] = value;
  }
  for (const required of ["workbook", "payload", "preview-dir", "report"]) {
    if (!args[required]) throw new Error(`missing --${required}`);
  }
  return args;
}

function flattened(matrix) {
  return matrix.flat().map((value) => value ?? "");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const workbookPath = path.resolve(args.workbook);
  const payloadPath = path.resolve(args.payload);
  const previewDir = path.resolve(args["preview-dir"]);
  const reportPath = path.resolve(args.report);
  const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
  const workbookBytes = await fs.readFile(workbookPath);
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
  workbook.recalculate();

  const expectedSheets = ["00说明", "VisualDiff校正复审", "机器数据_勿改"];
  const overview = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 5000 });
  for (const sheetName of expectedSheets) {
    if (!overview.ndjson.includes(`\"name\":\"${sheetName}\"`)) {
      throw new Error(`missing worksheet after round trip: ${sheetName}`);
    }
  }

  const rows = payload.rows;
  const startRow = 9;
  const lastRow = startRow + rows.length - 1;
  const reviewSheet = workbook.worksheets.getItem("VisualDiff校正复审");
  const machineSheet = workbook.worksheets.getItem("机器数据_勿改");
  const decisionValues = flattened(reviewSheet.getRange(`E${startRow}:E${lastRow}`).values);
  const statusFormulas = flattened(reviewSheet.getRange(`J${startRow}:J${lastRow}`).formulas);
  const machineIds = flattened(machineSheet.getRange(`C2:C${rows.length + 1}`).values).map(String);
  const expectedIds = rows.map((row) => row.record_id);
  const embeddedImages = reviewSheet.images.items.length;
  if (decisionValues.some((value) => value !== "")) {
    throw new Error("one or more reviewer decision cells are not blank");
  }
  if (statusFormulas.some((value) => typeof value !== "string" || !value.startsWith("="))) {
    throw new Error("one or more completion formulas are missing");
  }
  if (JSON.stringify(machineIds) !== JSON.stringify(expectedIds)) {
    throw new Error("machine-data identities do not match the payload order");
  }
  if (embeddedImages !== rows.length) {
    throw new Error(`expected ${rows.length} embedded images, found ${embeddedImages}`);
  }

  const formulaErrors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 300 },
    summary: "round-trip formula error scan",
    maxChars: 5000,
  });
  if (!formulaErrors.ndjson.includes("matched 0 entries")) {
    throw new Error(`round-trip formula error scan failed: ${formulaErrors.ndjson}`);
  }

  await fs.mkdir(previewDir, { recursive: true });
  const middleRow = startRow + Math.floor(rows.length / 2);
  const previews = [
    ["00说明", "A1:J22", "00_instructions.png"],
    ["VisualDiff校正复审", "A1:J12", "01_review_start.png"],
    ["VisualDiff校正复审", `A${middleRow}:J${Math.min(lastRow, middleRow + 2)}`, "02_review_middle.png"],
    ["VisualDiff校正复审", `A${Math.max(startRow, lastRow - 2)}:J${lastRow}`, "03_review_end.png"],
    ["机器数据_勿改", "A1:S8", "04_machine_data.png"],
  ];
  for (const [sheetName, range, filename] of previews) {
    const rendered = await workbook.render({ sheetName, range, scale: 0.75, format: "png" });
    await fs.writeFile(path.join(previewDir, filename), new Uint8Array(await rendered.arrayBuffer()));
  }
  await fs.writeFile(path.join(previewDir, "sheet_overview.ndjson"), overview.ndjson, "utf8");
  await fs.writeFile(path.join(previewDir, "formula_error_scan.ndjson"), formulaErrors.ndjson, "utf8");

  const report = {
    status: "PASS",
    goal: payload.goal,
    workbook: workbookPath,
    workbook_sha256: crypto.createHash("sha256").update(workbookBytes).digest("hex"),
    payload: payloadPath,
    rows: rows.length,
    embedded_images: embeddedImages,
    blank_decision_cells: decisionValues.filter((value) => value === "").length,
    completion_formulas: statusFormulas.filter((value) => typeof value === "string" && value.startsWith("=")).length,
    machine_identity_matches: machineIds.length,
    formula_error_matches: 0,
    sheets_rendered: [...new Set(previews.map(([sheetName]) => sheetName))],
    safe_to_merge_gold: false,
    gold_rows_modified: 0,
  };
  await fs.mkdir(path.dirname(reportPath), { recursive: true });
  await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + "\n", "utf8");
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

await main();
