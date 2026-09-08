#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || !value) throw new Error(`invalid arguments near ${key || "end"}`);
    args[key.slice(2)] = value;
  }
  for (const required of ["input", "output-dir", "report"]) {
    if (!args[required]) throw new Error(`missing --${required}`);
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const inputPath = path.resolve(args.input);
  const outputDir = path.resolve(args["output-dir"]);
  const reportPath = path.resolve(args.report);
  await fs.mkdir(outputDir, { recursive: true });
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
  const overview = await workbook.inspect({
    kind: "sheet",
    include: "id,name",
    maxChars: 10000,
  });
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "final formula error scan",
  });
  const checks = {};
  for (const [sheetId, range] of [
    ["00说明", "A1:J30"],
    ["主审_MicroText", "A1:J10"],
    ["主审_VisualDiff", "A1:J10"],
    ["工程任务清单", "A1:H10"],
    ["专项_英文描述15", "A1:L10"],
    ["专项_MicroText3", "A1:J9"],
    ["机器数据_勿改", "A1:O5"],
    ["机器数据_专项勿改", "A1:I5"],
  ]) {
    const inspected = await workbook.inspect({
      kind: "table",
      sheetId,
      range,
      include: "values,formulas",
      tableMaxRows: 30,
      tableMaxCols: 15,
      maxChars: 20000,
    });
    checks[sheetId] = inspected.ndjson;
    const rendered = await workbook.render({ sheetName: sheetId, range, scale: 0.75, format: "png" });
    await fs.writeFile(
      path.join(outputDir, `${String(Object.keys(checks).length).padStart(2, "0")}_${sheetId}.png`),
      new Uint8Array(await rendered.arrayBuffer()),
    );
  }
  await fs.writeFile(path.join(outputDir, "sheet_overview.ndjson"), overview.ndjson, "utf8");
  await fs.writeFile(path.join(outputDir, "formula_error_scan.ndjson"), errors.ndjson, "utf8");
  await fs.writeFile(path.join(outputDir, "key_range_checks.json"), JSON.stringify(checks, null, 2) + "\n", "utf8");
  const report = {
    status: errors.ndjson.includes("matched 0 entries") ? "PASS" : "FAIL",
    input: inputPath,
    rendered_sheets: Object.keys(checks),
    rendered_sheet_count: Object.keys(checks).length,
    formula_error_scan: errors.ndjson,
  };
  await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + "\n", "utf8");
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  if (report.status !== "PASS") process.exitCode = 1;
}

await main();
