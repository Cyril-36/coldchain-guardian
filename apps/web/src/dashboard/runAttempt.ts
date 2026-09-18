export interface RunCreationAttempt {
  scenarioId: string;
  seed: number;
  idempotencyKey: string;
}

export type RunIdentityFactory = () => Pick<RunCreationAttempt, "seed" | "idempotencyKey">;

const createIdentity: RunIdentityFactory = () => ({
  seed: crypto.getRandomValues(new Uint32Array(1))[0],
  idempotencyKey: crypto.randomUUID(),
});

export function createRunAttempt(existing: RunCreationAttempt | null, scenarioId: string, identityFactory: RunIdentityFactory = createIdentity): RunCreationAttempt {
  if (existing) return existing;
  return { scenarioId, ...identityFactory() };
}
