import { useEffect, useId, useRef, useState } from 'react';
import type { InputHTMLAttributes, KeyboardEvent } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { formatPersianDate, localToday, parsePersianDate, persianDigits, persianIso, persianMonthInfo, persianParts, shiftCivilDate } from '../../lib/persianDate';

const months = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];
const weekdays = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه'];

interface DateInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type' | 'min' | 'max'> {
  /** Gregorian civil date on the wire, regardless of the selected UI calendar. */
  value: string;
  onChange: (value: string) => void;
  locale?: string;
  min?: string;
  max?: string;
  onValidityChange?: (valid: boolean) => void;
  onCalendarToggle?: () => void;
}

/** Persian editing + calendar; other languages retain the native Gregorian input. */
export function DateInput({ value, onChange, locale = document.documentElement.lang, min, max, onValidityChange, onCalendarToggle, className = 'form-input', id, disabled, ...props }: DateInputProps) {
  const generatedId = useId();
  const inputId = id || generatedId;
  const calendarId = `${inputId}-calendar`;
  const errorId = `${inputId}-error`;
  const persian = locale.toLowerCase().startsWith('fa');
  const [draft, setDraft] = useState(() => formatPersianDate(value));
  const [invalid, setInvalid] = useState(false);
  const [open, setOpen] = useState(false);
  const initial = persianParts(value) || persianParts(localToday())!;
  const [month, setMonth] = useState({ year: initial.jy, month: initial.jm });
  const [focusedDate, setFocusedDate] = useState(value || localToday());
  const toggle = useRef<HTMLButtonElement>(null);
  const calendar = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { setDraft(formatPersianDate(value)); setInvalid(false); onValidityChange?.(true); }, [value, onValidityChange]);
  useEffect(() => {
    input.current?.setCustomValidity(invalid ? 'تاریخ شمسی معتبر و در محدوده مجاز وارد کنید.' : '');
  }, [invalid]);
  useEffect(() => {
    if (open) calendar.current?.querySelector<HTMLButtonElement>(`[data-date="${focusedDate}"]`)?.focus({ preventScroll: true });
  }, [open, focusedDate, month]);

  if (!persian) return <input {...props} ref={input} id={inputId} className={className} type="date" value={value} min={min} max={max} disabled={disabled} onChange={event => onChange(event.target.value)} />;

  const allowed = (date: string) => (!min || date >= min) && (!max || date <= max);
  const close = () => { setOpen(false); onCalendarToggle?.(); toggle.current?.focus({ preventScroll: true }); };
  const select = (date: string) => {
    if (date && !allowed(date)) return;
    setDraft(formatPersianDate(date)); setInvalid(false); onValidityChange?.(true); onChange(date); close();
  };
  const edit = (text: string) => {
    setDraft(text);
    const parsed = props.required && !text.trim() ? null : parsePersianDate(text, min, max);
    setInvalid(parsed === null); onValidityChange?.(parsed !== null);
    if (parsed !== null) onChange(parsed);
  };
  const show = () => {
    if (open) { close(); return; }
    const anchor = value || (min && localToday() < min ? min : max && localToday() > max ? max : localToday());
    const parts = persianParts(anchor) || persianParts(localToday())!;
    setMonth({ year: parts.jy, month: parts.jm }); setFocusedDate(anchor); setOpen(true); onCalendarToggle?.();
  };
  const info = persianMonthInfo(month.year, month.month);
  const monthTarget = (offset: number) => {
    const absolute = month.year * 12 + month.month - 1 + offset;
    const year = Math.floor(absolute / 12), nextMonth = ((absolute % 12) + 12) % 12 + 1;
    if (year < 1 || year > 3177) return null;
    const last = persianMonthInfo(year, nextMonth).days;
    if ((min && persianIso(year, nextMonth, last) < min) || (max && persianIso(year, nextMonth, 1) > max)) return null;
    return { year, month: nextMonth, last };
  };
  const moveMonth = (offset: number) => {
    const next = monthTarget(offset); if (!next) return;
    const day = Math.min(persianParts(focusedDate)?.jd || 1, next.last);
    let date = persianIso(next.year, next.month, day);
    if (min && date < min) date = min;
    if (max && date > max) date = max;
    setMonth(next); setFocusedDate(date);
  };
  const onDayKey = (event: KeyboardEvent<HTMLButtonElement>, date: string) => {
    const column = ((info.offset + (persianParts(date)?.jd || 1) - 1) % 7);
    const offsets: Record<string, number> = { ArrowLeft: 1, ArrowRight: -1, ArrowDown: 7, ArrowUp: -7, Home: -column, End: 6 - column };
    if (event.key === 'PageUp' || event.key === 'PageDown') { event.preventDefault(); moveMonth(event.key === 'PageUp' ? -1 : 1); return; }
    if (!(event.key in offsets)) return;
    event.preventDefault();
    const next = shiftCivilDate(date, offsets[event.key]), parts = persianParts(next);
    if (!parts || !allowed(next) || parts.jy < 1 || parts.jy > 3177) return;
    setMonth({ year: parts.jy, month: parts.jm }); setFocusedDate(next);
  };
  const cells = Array.from({ length: Math.ceil((info.offset + info.days) / 7) * 7 }, (_, index) => index - info.offset + 1);
  const error = `تاریخ شمسی معتبر با قالب سال/ماه/روز وارد کنید.${min ? ` از ${formatPersianDate(min)}` : ''}${max ? ` تا ${formatPersianDate(max)}` : ''}`;
  return (
    <div className="ui-date-input" onKeyDown={event => {
      if (open && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    }}>
      <div className="ui-date-field">
        <input {...props} ref={input} id={inputId} className={className} type="text" dir="ltr" inputMode="numeric" autoComplete="off" placeholder="۱۴۰۵/۰۱/۰۱" value={draft} disabled={disabled}
          aria-invalid={invalid} aria-describedby={[props['aria-describedby'], invalid && errorId].filter(Boolean).join(' ') || undefined}
          onChange={event => edit(event.target.value)} onBlur={() => { if (!invalid) setDraft(formatPersianDate(value)); }} />
        <button ref={toggle} type="button" className="ui-date-calendar-toggle" disabled={disabled} aria-label="انتخاب تاریخ شمسی" aria-expanded={open} aria-controls={open ? calendarId : undefined} onClick={show}><CalendarDays size={16} aria-hidden /></button>
      </div>
      {invalid && <span id={errorId} role="alert" className="ui-date-error">{error}</span>}
      {open && <div ref={calendar} id={calendarId} className="ui-date-calendar" dir="rtl">
        <div className="ui-date-calendar-header">
          <button type="button" className="btn btn-icon" aria-label="ماه قبل" disabled={!monthTarget(-1)} onClick={() => moveMonth(-1)}><ChevronRight size={16} /></button>
          <strong id={`${calendarId}-month`} aria-live="polite">{months[month.month - 1]} {persianDigits(month.year)}</strong>
          <button type="button" className="btn btn-icon" aria-label="ماه بعد" disabled={!monthTarget(1)} onClick={() => moveMonth(1)}><ChevronLeft size={16} /></button>
        </div>
        <table role="grid" aria-labelledby={`${calendarId}-month`}>
          <thead><tr>{weekdays.map((day, index) => <th key={day} scope="col" aria-label={day}>{['ش', 'ی', 'د', 'س', 'چ', 'پ', 'ج'][index]}</th>)}</tr></thead>
          <tbody>{Array.from({ length: cells.length / 7 }, (_, row) => <tr key={row}>{cells.slice(row * 7, row * 7 + 7).map((day, column) => <td key={column}>
            {day >= 1 && day <= info.days && (() => { const date = persianIso(month.year, month.month, day); return <button type="button" data-date={date} tabIndex={date === focusedDate ? 0 : -1} aria-label={formatPersianDate(date)} aria-pressed={date === value} aria-current={date === localToday() ? 'date' : undefined} disabled={!allowed(date)} onFocus={() => setFocusedDate(date)} onClick={() => select(date)} onKeyDown={event => onDayKey(event, date)}>{persianDigits(day)}</button>; })()}
          </td>)}</tr>)}</tbody>
        </table>
        <div className="ui-date-calendar-footer"><button type="button" className="btn btn-sm" disabled={!allowed(localToday())} onClick={() => select(localToday())}>امروز</button>{!props.required && <button type="button" className="btn btn-sm" onClick={() => select('')}>پاک کردن</button>}</div>
      </div>}
    </div>
  );
}
