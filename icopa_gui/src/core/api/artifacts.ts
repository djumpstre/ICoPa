import { env } from "../config/env";
import { ApiError } from "./errors";
import { captureRequestSession } from "./session";

interface ArtifactOptions {
  token: string | null;
  signal?: AbortSignal;
}

async function requestArtifact<T>(
  url: string,
  options: ArtifactOptions,
  read: (response: Response) => Promise<T>,
): Promise<T | null> {
  const backend = new URL(env.apiBaseUrl, window.location.origin);
  const target = new URL(url, `${backend.origin}/`);
  if (!["http:", "https:"].includes(target.protocol) || target.origin !== backend.origin || target.username || target.password) {
    throw new Error("Artifact URL must belong to the configured Hub.");
  }
  const session = captureRequestSession(options.token);
  session.assertCurrent();
  const response = await fetch(target.href, {
    signal: options.signal,
    redirect: "error",
    credentials: "omit",
    headers: options.token ? { Authorization: `Bearer ${options.token}` } : undefined,
  });
  session.assertCurrent();
  if (response.status === 401) {
    session.expire();
    throw new ApiError("Authentication required", 401);
  }
  if (!response.ok) return null;
  const result = await read(response);
  session.assertCurrent();
  return result;
}

export async function requestArtifactText(url: string, options: ArtifactOptions): Promise<string> {
  return (await requestArtifact(url, options, (response) => response.text())) ?? "";
}

export function requestArtifactJson(url: string, options: ArtifactOptions): Promise<unknown | null> {
  return requestArtifact(url, options, (response) => response.json());
}
