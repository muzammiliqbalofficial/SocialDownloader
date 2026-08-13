import { describeError, type ErrorSpec } from "./errors";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface ComponentHealth {
  status: "ok" | "down" | "unknown";
  detail: string | null;
  latency_ms: number | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  environment: string;
  components: Record<string, ComponentHealth>;
}

export interface FeatureFlags {
  transcription: boolean;
  summarization: boolean;
  batch: boolean;
}

/** A failure already translated into the shared taxonomy, ready to render. */
export class ApiError extends Error {
  readonly code: string;
  readonly spec: ErrorSpec;
  readonly requestId: string | null;

  constructor(code: string, requestId: string | null, detail?: string | null) {
    const spec = describeError(code);
    super(detail ?? spec.message);
    this.name = "ApiError";
    this.code = code;
    this.spec = spec;
    this.requestId = requestId;
  }
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch {
    // Network-level failure: no envelope to read, so synthesise one rather
    // than letting a raw TypeError reach the UI.
    throw new ApiError("UPSTREAM_TIMEOUT", null);
  }

  const requestId = response.headers.get("X-Request-ID");

  if (!response.ok) {
    let code = "INTERNAL_ERROR";
    let detail: string | null = null;
    try {
      const body = await response.json();
      code = body?.error?.code ?? code;
      detail = body?.error?.detail ?? null;
    } catch {
      // Non-JSON error body (a proxy timeout page, say). The status-derived
      // default above is the best available answer.
    }
    throw new ApiError(code, requestId, detail);
  }

  return (await response.json()) as T;
}

export const getHealth = () => apiFetch<HealthResponse>("/api/health");
export const getFeatures = () => apiFetch<FeatureFlags>("/api/features");
