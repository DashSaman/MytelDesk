/** Pure rendering checks; no PBX process, filesystem or database mutations. */
jest.mock('../src/db/db-manager', () => ({ getSetting: jest.fn(async (_k: string, d: unknown) => d) }));
import { renderRecordingConf } from '../src/services/dialplan';

describe('native Issabel recording fallback', () => {
  const conf = renderRecordingConf({ format: 'wav', vad: true, pbx: 'Issabel' });
  it('uses native sub-record-check arguments for direction and destination', () => {
    expect(conf).toContain('Set(OPDESK_NATIVE_KIND=${ARG1})');
    expect(conf).toContain('"${OPDESK_NATIVE_KIND}"="out"');
    expect(conf).toContain('${IF($["${ARG2}"!=""]?${ARG2}:');
  });
  it('preserves existing native recordings instead of starting another recorder', () => {
    const start = conf.indexOf('[opdesk-record]');
    const nativeGuard = conf.indexOf('"${REC_STATUS}"="RECORDING" | "${CDR(recordingfile)}"!=""', start);
    expect(nativeGuard).toBeGreaterThan(start);
    expect(nativeGuard).toBeLessThan(conf.indexOf('MixMonitor(', start));
    expect(conf).not.toContain('Set(__REC_STATUS=');
    expect(conf).not.toContain('Set(__CALLFILENAME=');
  });
  it('checks native callee opt-out only for extension calls, not queue/group/trunk numbers', () => {
    expect(conf).toContain('Set(OPDESK_POLICY_DST=${IF($["${OPDESK_NATIVE_KIND}"="exten"]?${ARG2}:${IF($["${OPDESK_NATIVE_KIND}"=""]?${EXTTOCALL}:)})})');
    expect(conf).toContain('${DB(opdesk/rec/ext/${OPDESK_POLICY_DST})}');
    expect(conf).not.toContain('${DB(opdesk/rec/ext/${EXTTOCALL})}');
  });
  it('filters every untrusted filename component without changing caller identity', () => {
    for (const [name, value] of [['OPDESK_FILE_CID', '${CALLERID(num)}'], ['OPDESK_FILE_DST', '${OPDESK_DST}'], ['OPDESK_FILE_UID', '${UNIQUEID}']]) {
      expect(conf).toContain(`Set(${name}=\${FILTER(0-9A-Za-z_.+\\-,${value})})`);
    }
    expect(conf).toContain('Set(OPDESK_BASE=${OPDESK_TS}-${OPDESK_FILE_CID}-${OPDESK_FILE_DST}-${OPDESK_FILE_UID})');
    expect(conf).not.toContain('Set(CALLERID(');
    expect(conf).not.toContain('Set(UNIQUEID=');
    const parameters = conf.split('\n').find(line => line.includes(',MixMonitor('));
    expect(parameters).toBe(' same => n,MixMonitor(${OPDESK_MIX},br(${OPDESK_SP1})t(${OPDESK_SP2}))');
  });
});
