import { env } from "../config/env";
import { ApiError } from "./errors";
import { captureRequestSession } from "./session";

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions {
  method?: HttpMethod;
  body?: unknown;
  token?: string | null;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

function joinUrl(path: string): string {
  const normalizedPath = path.replace(/^\/+/, "");
  return `${env.apiBaseUrl}/${normalizedPath}`;
}

async function parseError(response: Response): Promise<never> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const payload = await response.json();
    const message =
      typeof payload?.detail === "string"
        ? payload.detail
        : typeof payload?.message === "string"
          ? payload.message
          : response.statusText;
    throw new ApiError(message || "Request failed", response.status, payload);
  }
  const text = await response.text();
  throw new ApiError(text || response.statusText || "Request failed", response.status, text);
}

export async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const session = captureRequestSession(options.token);
  session.assertCurrent();
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options.headers ?? {}),
  };

  let payload: BodyInit | undefined;
  if (options.body !== undefined) {
    if (options.body instanceof FormData) {
      payload = options.body;
    } else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(options.body);
    }
  }

  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }

  const response = await fetch(joinUrl(path), {
    method,
    headers,
    body: payload,
    signal: options.signal,
  });
  session.assertCurrent();

  if (!response.ok) {
    try {
      return await parseError(response);
    } finally {
      session.assertCurrent();
      if (response.status === 401) session.expire();
    }
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const data = (await response.json()) as T;
  session.assertCurrent();
  return data;
}
