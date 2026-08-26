import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [inputPath, outputDir] = process.argv.slice(2);
if (!inputPath || !outputDir) {
  throw new Error("usage: verify_workbook.mjs <input.xlsx> <output-dir>");
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const summary = await workbook.inspect({
  kind: "workbook,sheet,table,formula",
  maxChars: 8000,
  tableMaxRows: 8,
  tableMaxCols: 10,
  options: { maxResults: 100 }
});

await fs.mkdir(outputDir, { recursive: true });
await fs.writeFile(path.join(outputDir, "inspection.ndjson"), summary.ndjson, "utf8");

const sheets = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 3000 });
const sheetNames = [...sheets.ndjson.matchAll(/"name":"([^"]+)"/g)].map((match) => match[1]);
if (!sheetNames.length) throw new Error("workbook has no worksheets");

for (const sheetName of sheetNames) {
  const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1.4, format: "png" });
  const bytes = new Uint8Array(await preview.arrayBuffer());
  await fs.writeFile(path.join(outputDir, `${sheetName}.png`), bytes);
}

const errorTokens = ["#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A"];
const foundErrors = errorTokens.filter((token) => summary.ndjson.includes(token));
if (foundErrors.length) throw new Error(`formula errors: ${foundErrors.join(", ")}`);
process.stdout.write(JSON.stringify({ ok: true, renderedSheets: sheetNames.length, sheetNames, foundErrors }));
