import { ApiClientError, isApiErrorEnvelope } from "./errors";

export type TokenProvider = () => Promise<string | null>;

export interface ApiClientOptions {
  baseUrl: string;
  tokenProvider?: TokenProvider;
  fetchImpl?: typeof fetch;
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

function joinUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, "")}/${path.replace(/^\//, "")}`;
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly tokenProvider?: TokenProvider;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl;
    this.tokenProvider = options.tokenProvider;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");

    if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
    }

    const token = this.tokenProvider ? await this.tokenProvider() : null;
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await this.fetchImpl(joinUrl(this.baseUrl, path), {
      ...options,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      headers,
    });

    const body = await readBody(response);
    if (!response.ok) {
      throw new ApiClientError(
        response.status,
        isApiErrorEnvelope(body) ? body.error : null,
        typeof body === "string" ? body : undefined,
      );
    }

    return body as T;
  }

  get<T>(path: string, options?: Omit<RequestOptions, "method" | "body">): Promise<T> {
    return this.request<T>(path, { ...options, method: "GET" });
  }

  post<T>(path: string, body: unknown, options?: Omit<RequestOptions, "method" | "body">): Promise<T> {
    return this.request<T>(path, { ...options, method: "POST", body });
  }
}
