interface SessionLifecycle {
  token: () => string | null;
  revision: () => number;
  expire: () => void;
}

let lifecycle: SessionLifecycle | undefined;

export class StaleSessionError extends Error {
  constructor() {
    super("The session changed while this request was running.");
    this.name = "StaleSessionError";
  }
}

export function configureSessionLifecycle(value: SessionLifecycle) {
  lifecycle = value;
  return () => {
    if (lifecycle === value) lifecycle = undefined;
  };
}

export function captureRequestSession(token?: string | null) {
  const current = lifecycle;
  const revision = current?.revision();
  const assertCurrent = () => {
    if (token && current && (token !== current.token() || revision !== current.revision())) {
      throw new StaleSessionError();
    }
  };
  return {
    assertCurrent,
    expire: () => {
      assertCurrent();
      if (token) current?.expire();
    },
  };
}
