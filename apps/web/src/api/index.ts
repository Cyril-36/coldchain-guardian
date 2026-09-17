import { ApiClient } from "./client";
import { createApiEndpoints } from "./endpoints";

const baseUrl = import.meta.env.VITE_API_BASE_URL as string | undefined;

if (!baseUrl) {
  throw new Error("VITE_API_BASE_URL is required");
}

export const apiClient = new ApiClient({ baseUrl });
export const api = createApiEndpoints(apiClient);

export * from "./client";
export * from "./errors";
export * from "./endpoints";
