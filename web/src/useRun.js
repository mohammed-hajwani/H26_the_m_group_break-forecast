import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fold } from "./model.js";

// How long each kind of event holds the screen during a replay at 1x, in ms. Real runs take minutes; a
// replay compresses them but keeps the order and the beats (a matrix job lands, a patch is judged).
const BEAT = {
  run: 400, resume: 300, stage: 420, dependency: 40, risk: 30, baseline_attempt: 520, baseline_reference: 300,
  replacement: 420, cell: 110, forecast: 170, probe: 240, job: 420, forecast_score: 900, diagnosis: 520,
  patch: 950, repair_done: 380, exit_plan: 1400, end: 0,
};

export function useRun() {
  const [runs, setRuns] = useState([]);
  const [runId, setRunId] = useState(null);
  const [mode, setMode] = useState("idle"); // idle | replay | live
  const [events, setEvents] = useState([]);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [logs, setLogs] = useState([]);
  const [liveState, setLiveState] = useState(null); // running | finished | stopped
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const esRef = useRef(null);

  const refreshRuns = useCallback(async () => {
    try {
      const r = await fetch("/api/runs");
      if (!r.ok) throw new Error(`server answered ${r.status}`);
      setRuns(await r.json());
    } catch (e) {
      setError(`Cannot reach bf serve: ${e.message}`);
    }
  }, []);

  useEffect(() => { refreshRuns(); }, [refreshRuns]);

  const closeStream = () => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
  };

  // `at` opens the replay paused at that event (deep links: ?run=<id>&at=<n>); otherwise it plays from the start.
  const openReplay = useCallback(async (id, at = null) => {
    closeStream();
    setError(null);
    setLoading(true);
    setPlaying(false);
    setLogs([]);
    setLiveState(null);
    try {
      const r = await fetch(`/api/runs/${encodeURIComponent(id)}/events`);
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || `server answered ${r.status}`);
      setRunId(id);
      setMode("replay");
      setEvents(data.events);
      const start = at == null ? 0 : Math.max(0, Math.min(Number(at) || 0, data.events.length));
      setPlayhead(start);
      setPlaying(at == null);
    } catch (e) {
      setError(`Could not load run ${id}: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const follow = useCallback((id) => {
    closeStream();
    setRunId(id);
    setMode("live");
    setEvents([]);
    setPlayhead(0);
    setPlaying(false);
    setLogs([]);
    setLiveState("running");
    const es = new EventSource(`/api/live/${encodeURIComponent(id)}/stream`);
    esRef.current = es;
    es.addEventListener("run", (msg) => {
      const ev = JSON.parse(msg.data);
      setEvents((prev) => {
        const next = [...prev, ev];
        setPlayhead(next.length);
        return next;
      });
    });
    es.addEventListener("log", (msg) => {
      const { line } = JSON.parse(msg.data);
      setLogs((prev) => (prev.length > 300 ? [...prev.slice(-250), line] : [...prev, line]));
    });
    es.addEventListener("closed", (msg) => {
      const { exit } = JSON.parse(msg.data);
      setLiveState(exit === 0 || exit === null ? "finished" : "stopped");
      es.close();
      esRef.current = null;
      refreshRuns();
    });
    es.onerror = () => {
      // The browser retries on its own; only report if the stream is gone for good.
      if (es.readyState === EventSource.CLOSED) setError("Lost the live stream from bf serve.");
    };
  }, [refreshRuns]);

  const startLive = useCallback(async (body) => {
    setError(null);
    setLoading(true);
    try {
      const r = await fetch("/api/live", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || `server answered ${r.status}`);
      follow(data.run);
      refreshRuns();
      return true;
    } catch (e) {
      setError(`Could not start the run: ${e.message}`);
      return false;
    } finally {
      setLoading(false);
    }
  }, [follow, refreshRuns]);

  const stopLive = useCallback(async () => {
    if (!runId) return;
    await fetch(`/api/live/${encodeURIComponent(runId)}/stop`, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: "{}" });
  }, [runId]);

  // Replay clock: advance one event per beat.
  useEffect(() => {
    if (mode !== "replay" || !playing) return undefined;
    if (playhead >= events.length) {
      setPlaying(false);
      return undefined;
    }
    const beat = (BEAT[events[playhead].kind] ?? 200) / speed;
    const t = setTimeout(() => setPlayhead((p) => Math.min(p + 1, events.length)), beat);
    return () => clearTimeout(t);
  }, [mode, playing, playhead, events, speed]);

  useEffect(() => () => closeStream(), []);

  const model = useMemo(() => fold(events, playhead), [events, playhead]);

  return {
    runs, runId, mode, events, playhead, playing, speed, logs, liveState, error, loading, model,
    refreshRuns, openReplay, follow, startLive, stopLive, setError,
    play: () => { if (playhead >= events.length) setPlayhead(0); setPlaying(true); },
    pause: () => setPlaying(false),
    restart: () => { setPlayhead(0); setPlaying(true); },
    seek: (i) => { setPlaying(false); setPlayhead(i); },
    setSpeed,
  };
}
