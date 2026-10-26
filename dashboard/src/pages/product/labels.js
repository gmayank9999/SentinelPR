/** Button text for starting another analysis, from the state of the one being viewed. */
export function analyseLabel(meta) {
  if (!meta || meta.status === "none") return "Analyse";
  return meta.status === "failed" || meta.status === "cancelled" ? "Retry analysis" : "Re-analyse";
}
