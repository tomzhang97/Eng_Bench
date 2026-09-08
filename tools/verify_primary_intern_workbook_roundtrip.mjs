#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const PREVIEWS = [
  ["00说明", "A1:J30", "00_instructions.png"],
  ["主审_MicroText", "A1:J12", "01_microtext.png"],
  ["主审_VisualDiff", "A1:J11", "02_visualdiff.png"],
  ["工程任务清单", "A1:H15", "03_engineering.png"],
  ["专项_英文描述15", "A1:L21", "04_specialist_visual.png"],
  ["专项_MicroText3", "A1:J9", "05_specialist_micro.png"],
  ["机器数据_勿改", "A1:O8", "06_machine.png"],
  ["机器数据_专项勿改", "A1:I8", "07_specialist_machine.png"],
];

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || !value) throw new Error(`invalid argument near ${key}`);
    args[key.slice(2)] = value;
  }
  for (const required of ["workbook", "payload", "preview-dir", "report"]) {
    if (!args[required]) throw new Error(`missing --${required}`);
  }
  return args;
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
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: "primary workbook round-trip formula error scan",
    maxChars: 4000,
  });
  if (!errors.ndjson.includes("matched 0 entries")) {
    throw new Error(`round-trip formula error scan failed: ${errors.ndjson}`);
  }
  await fs.mkdir(previewDir, { recursive: true });
  for (const [sheetName, range, filename] of PREVIEWS) {
    const rendered = await workbook.render({
      sheetName,
      range,
      scale: 0.75,
      format: "png",
    });
    await fs.writeFile(
      path.join(previewDir, filename),
      new Uint8Array(await rendered.arrayBuffer()),
    );
  }
  await fs.writeFile(path.join(previewDir, "formula_error_scan.ndjson"), errors.ndjson, "utf8");
  const report = {
    status: "PASS",
    goal: payload.goal,
    workbook: workbookPath,
    workbook_sha256: crypto.createHash("sha256").update(workbookBytes).digest("hex"),
    payload: payloadPath,
    payload_counts: payload.counts,
    sheets_rendered: PREVIEWS.map(([sheetName]) => sheetName),
    formula_error_matches: 0,
    safe_to_merge_gold: false,
    gold_rows_modified: 0,
  };
  await fs.mkdir(path.dirname(reportPath), { recursive: true });
  await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

await main();
