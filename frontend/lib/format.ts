export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(2)} MB`;
}

export function formatTime(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return m > 0 ? `${m} phút ${s} giây` : `${s} giây`;
}

export function fileNameFromDisposition(
  disposition: string | null,
  fallback: string
): string {
  if (!disposition || !disposition.includes("filename=")) return fallback;
  const m = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/.exec(disposition);
  if (m && m[1]) return m[1].replace(/['"]/g, "");
  return fallback;
}

export function toWsBase(httpBase: string): string {
  if (httpBase.startsWith("https://"))
    return httpBase.replace("https://", "wss://");
  return httpBase.replace("http://", "ws://");
}
