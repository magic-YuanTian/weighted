import React, { useState } from 'react';
import { PILOT } from './pilot';


export default function BriefStage({ busy, onExtract }) {
  const [brief, setBrief] = useState('');

  return (
    <div className="stage single">
      <div className="pane" style={{ maxWidth: 820, margin: '0 auto', width: '100%' }}>
        <h3>Task brief</h3>
        <textarea
          className="briefinput"
          value={brief}
          placeholder="Paste the assignment…"
          onChange={(e) => setBrief(e.target.value)}
        />
        <div className="startrow">
          <button
            className="startbtn"
            disabled={busy || !brief.trim()}
            onClick={() => onExtract(brief.trim())}
          >
            {busy ? 'extracting…' : 'Extract requirements →'}
          </button>
          <button className="linkbtn" onClick={() => setBrief(PILOT)}>load an example task</button>
        </div>
      </div>
    </div>
  );
}
