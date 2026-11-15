import { d2g, g2d, isValidJalaaliDate, j2d, jalaaliMonthLength, toGregorian, toJalaali } from 'jalaali-js';

const pad = (value: number) => String(value).padStart(2, '0');
const iso = ({ gy, gm, gd }: { gy: number; gm: number; gd: number }) => `${String(gy).padStart(4, '0')}-${pad(gm)}-${pad(gd)}`;
export const persianDigits = (value: string | number) => String(value).replace(/\d/g, digit => '۰۱۲۳۴۵۶۷۸۹'[Number(digit)]);
const latinDigits = (value: string) => value.replace(/[۰-۹]/g, digit => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(digit))).replace(/[٠-٩]/g, digit => String('٠١٢٣٤٥٦٧٨٩'.indexOf(digit)));

/** Civil dates stay as numeric calendar parts. No UTC/local timestamp conversion. */
export function civilParts(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const [gy, gm, gd] = match.slice(1).map(Number);
  if (gy < 1 || gm < 1 || gm > 12 || gd < 1 || gd > 31) return null;
  const normalized = d2g(g2d(gy, gm, gd));
  return normalized.gy === gy && normalized.gm === gm && normalized.gd === gd ? normalized : null;
}

export function persianParts(value: string) {
  const parts = civilParts(value);
  if (!parts) return null;
  try { return toJalaali(parts.gy, parts.gm, parts.gd); } catch { return null; }
}

export function formatPersianDate(value: string): string {
  const parts = persianParts(value);
  return parts ? persianDigits(`${parts.jy}/${pad(parts.jm)}/${pad(parts.jd)}`) : '';
}

/** null means invalid; the empty string is an explicit clear for optional fields. */
export function parsePersianDate(value: string, min?: string, max?: string): string | null {
  const normalized = latinDigits(value.trim());
  if (!normalized) return '';
  const match = /^(\d{4})\/(\d{1,2})\/(\d{1,2})$/.exec(normalized);
  if (!match) return null;
  const [jy, jm, jd] = match.slice(1).map(Number);
  if (jy < 1 || !isValidJalaaliDate(jy, jm, jd)) return null;
  const result = iso(toGregorian(jy, jm, jd));
  return (min && result < min) || (max && result > max) ? null : result;
}

export function persianIso(year: number, month: number, day: number): string {
  return iso(toGregorian(year, month, day));
}

export function persianMonthInfo(year: number, month: number) {
  return { days: jalaaliMonthLength(year, month), offset: ((j2d(year, month, 1) + 2) % 7 + 7) % 7 };
}

export function shiftCivilDate(value: string, days: number): string {
  const parts = civilParts(value);
  return parts ? iso(d2g(g2d(parts.gy, parts.gm, parts.gd) + days)) : value;
}

export function localToday(): string {
  const now = new Date();
  return iso({ gy: now.getFullYear(), gm: now.getMonth() + 1, gd: now.getDate() });
}
