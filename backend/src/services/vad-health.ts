export interface VadHealthStatus {
  available: boolean;
  engine: 'silero' | null;
  error_code: 'vad.unavailable' | 'vad.timeout' | null;
}

export class VadHealthMonitor {
  constructor(private readonly now: () => number = Date.now) {}
  check(): Promise<VadHealthStatus> {
    return Promise.resolve({ available: false, engine: null, error_code: 'vad.unavailable' });
  }
}

export const vadHealth = new VadHealthMonitor();
