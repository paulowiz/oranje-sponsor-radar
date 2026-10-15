export async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Request failed (${response.status}). Please try again.`);
  return response.json() as Promise<T>;
}
