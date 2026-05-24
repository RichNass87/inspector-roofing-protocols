import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const root = path.resolve(__dirname, "..");

export const paths = {
  root,
  schema: path.join(root, "schemas", "roof_inspection.schema.json"),
  taxonomy: path.join(root, "taxonomies", "damage_labels.json"),
  checklist: path.join(root, "checklists", "report_checklist.md"),
  reportTemplate: path.join(root, "templates", "roof_report.md"),
  photoManifestTemplate: path.join(root, "templates", "photo_manifest.csv"),
};
