import { execFile } from 'child_process';
import { env } from '../core/config';
import { VAD_SCRIPT, vadPython } from './vad-runner';

export interface VadHealthStatus {
  available: boolean;
  engine: 'silero' | null;
  error_code: 'vad.unavailable' | 'vad.timeout' | null;
}

export class VadHealthMonitor {
  private cached: { key:string; until:number; value:VadHealthStatus } | null = null;
  private pending: { key:string; promise:Promise<VadHealthStatus> } | null = null;
  constructor(private readonly now: () => number = Date.now) {}
  check(): Promise<VadHealthStatus> {
    const python = vadPython();
    const expectedHash = env('VAD_MODEL_SHA256', '').trim();
    const key = JSON.stringify([python, env('VAD_MODEL_PATH', ''), expectedHash]);
    if(this.cached?.key===key && this.now()<this.cached.until) return Promise.resolve(this.cached.value);
    if(this.pending?.key===key) return this.pending.promise;
    const promise = this.probe(python, expectedHash).then(value => {
      this.cached = {key,until:this.now()+(value.available ? 60000 : 5000),value};
      return value;
    }).finally(() => { if(this.pending?.promise===promise) this.pending=null; });
    this.pending = {key,promise};
    return promise;
  }

  private probe(python:string, expectedHash:string): Promise<VadHealthStatus> {
    const unavailable:VadHealthStatus = {available:false,engine:null,error_code:'vad.unavailable'};
    return new Promise(resolve => {
      execFile(python,[VAD_SCRIPT,'--doctor'],{encoding:'utf8',timeout:15000,maxBuffer:16384,shell:false},(error,stdout) => {
        if(error) {
          const timedOut = error.killed || String(error.code)==='ETIMEDOUT';
          resolve({...unavailable,error_code:timedOut ? 'vad.timeout' : 'vad.unavailable'});
          return;
        }
        try {
          const result = JSON.parse(stdout) as Record<string,unknown>;
          const providers = result.providers;
          const valid = result.status==='ok' && result.engine==='silero' && result.threads===1 &&
            result.context_samples===64 && typeof result.inference_frames==='number' && result.inference_frames>=2 &&
            Array.isArray(providers) && providers.length===1 && providers[0]==='CPUExecutionProvider' &&
            typeof result.model_sha256==='string' && /^[a-f0-9]{64}$/.test(result.model_sha256) &&
            (!expectedHash || result.model_sha256===expectedHash);
          resolve(valid ? {available:true,engine:'silero',error_code:null} : unavailable);
        } catch { resolve(unavailable); }
      });
    });
  }
}

export const vadHealth = new VadHealthMonitor();
