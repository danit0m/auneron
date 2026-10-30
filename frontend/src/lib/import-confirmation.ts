/** Import intent IDs are stable for retries; callers create one per accepted preview. */
export function createImportIntentId(): string {
  const provider = globalThis.crypto;
  if (!provider) throw new Error("Não foi possível preparar a confirmação neste navegador.");
  if (typeof provider.randomUUID === "function") return provider.randomUUID();
  const bytes = provider.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function confirmationParams(fileName: string, preview: { file_sha256: string; content_sha256: string }) {
  return {
    format: fileName.toLowerCase().endsWith(".xlsx") ? "xlsx" : "csv",
    expected_file_sha256: preview.file_sha256,
    expected_content_sha256: preview.content_sha256,
  };
}
