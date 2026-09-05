/* Scrolling as study data. MUSE reports scrolls per task as the measure of
   how much of a pane a participant had to search through, and a pane that
   does not report them cannot be compared with one that does. Streaming every
   wheel tick would flood the log, so each pane counts its scroll events and
   sends one `scroll` telemetry event every few seconds carrying the count —
   enough for a timeline, cheap enough to leave on. */
import { useCallback, useEffect, useRef } from 'react';
import api from './api';

const FLUSH_MS = 3000;

export function useScrollTelemetry(sessionId, pane) {
  const count = useRef(0);
  const timer = useRef(null);
  const sid = useRef(sessionId);
  sid.current = sessionId;

  const flush = useCallback(() => {
    timer.current = null;
    const n = count.current;
    count.current = 0;
    if (n && sid.current) api.telemetry(sid.current, 'scroll', { pane, n });
  }, [pane]);

  // whatever is still counted when the pane goes away is sent, not dropped
  useEffect(() => () => {
    if (timer.current) { clearTimeout(timer.current); flush(); }
  }, [flush]);

  return useCallback(() => {
    count.current += 1;
    if (!timer.current) timer.current = setTimeout(flush, FLUSH_MS);
  }, [flush]);
}
