import React, { useEffect, useState } from 'react';
import { PILOT } from './pilot';
import api from './api';


export default function BriefStage({ busy, onExtract }) {
  const [brief, setBrief] = useState('');
  const [tasks, setTasks] = useState([]);
  const [picked, setPicked] = useState('');

  // The benchmark tasks live on disk, not in the bundle: several of them ship
  // without a licence, so they stay out of the repo. No tasks, no picker.
  useEffect(() => {
    let live = true;
    api.presets()
      .then((d) => { if (live) setTasks(d.tasks || []); })
      .catch(() => {});
    return () => { live = false; };
  }, []);

  function choose(id) {
    setPicked(id);
    const t = tasks.find((x) => x.id === id);
    if (t) setBrief(t.brief);
  }

  return (
    <div className="stage single">
      <div className="pane" style={{ maxWidth: 820, margin: '0 auto', width: '100%' }}>
        <h3>Task brief</h3>

        {tasks.length > 0 && (
          <div className="taskpicker">
            <label htmlFor="taskpick">Benchmark task</label>
            <select
              id="taskpick"
              value={picked}
              disabled={busy}
              onChange={(e) => choose(e.target.value)}
            >
              <option value="">Choose one, or paste your own below…</option>
              {tasks.map((t) => (
                <option key={t.id} value={t.id}>
                  {`Task ${t.n} · ${t.domain} · ${t.label}`}
                </option>
              ))}
            </select>
          </div>
        )}

        <textarea
          className="briefinput"
          value={brief}
          placeholder="Paste the assignment…"
          onChange={(e) => { setBrief(e.target.value); setPicked(''); }}
        />
        <div className="startrow">
          <button
            className="startbtn"
            disabled={busy || !brief.trim()}
            onClick={() => onExtract(brief.trim())}
          >
            {busy ? 'extracting…' : 'Extract requirements →'}
          </button>
          <button className="linkbtn" onClick={() => { setBrief(PILOT); setPicked(''); }}>
            load an example task
          </button>
        </div>
      </div>
    </div>
  );
}
