/** `<input type="datetime-local">` reads and writes local time with no
 * timezone offset; the API wants a real ISO instant. These two functions are
 * the only place that conversion happens, so every form does it identically. */

export function nowAsDatetimeLocalValue(): string {
  const now = new Date();
  now.setSeconds(0, 0);
  const offsetMs = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offsetMs).toISOString().slice(0, 16);
}

export function datetimeLocalValueToIso(value: string): string {
  return new Date(value).toISOString();
}
