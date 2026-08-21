import React from 'react';

/* What the model will actually receive on the next step. Everything durable is
   editable elsewhere; everything ephemeral is at least visible here. A
   participant can verify there is no hidden memory — which is the point. */
export default function ContextInspector({ ctx, onClose }) {
  const reminders = (ctx.parts || []).find((p) => p.key === 'reminders');
  return (
    <div className="overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="panel" role="dialog" aria-label="Next step context">
        <header>
          <h4>Next step context</h4>
          <span className="tok">≈ {ctx.total} tokens</span>
          <button className="closebtn" onClick={onClose}>esc ✕</button>
        </header>
        <div className="rows">
          {(ctx.parts || []).map((p) => (
            <div className="crow" key={p.key}>
              <span>
                {p.label}
                {p.detail && <small>{p.detail}</small>}
              </span>
              <span className="n">≈ {p.tokens}</span>
            </div>
          ))}
          <pre className="injected">
            {reminders && reminders.text
              ? reminders.text
              : 'No reminders injected — nothing is pinned.\n\n'
                + 'Press "pin" on a requirement and this block is appended '
                + 'immediately before every action the agent takes.'}
          </pre>
        </div>
      </div>
    </div>
  );
}
