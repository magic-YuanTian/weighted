import React from 'react';

/* A render crash inside a fixed-position full-screen app looks exactly like
   "the button does nothing": React unmounts the tree and the last painted
   frame stays on screen. In a study that is unrecoverable — the session dies
   and nobody knows why. So: catch it, show it, and keep the text copyable. */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    this.setState({ info });
    // eslint-disable-next-line no-console
    console.error('[wt4] render crash', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="wt4" style={{ padding: 28, overflow: 'auto' }}>
        <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>The interface crashed while rendering.</h3>
        <p style={{ fontSize: 13, color: 'var(--ink-2)', margin: '0 0 12px' }}>
          The run itself is server-side and unharmed — reload the page and the session
          continues from where it stopped.
        </p>
        <pre style={{
          fontFamily: 'var(--mono)', fontSize: 11.5, whiteSpace: 'pre-wrap',
          background: 'var(--surface-2)', border: '1px solid var(--bad)',
          borderRadius: 4, padding: 12, color: 'var(--bad)',
        }}>
          {String(this.state.error && this.state.error.stack ? this.state.error.stack : this.state.error)}
          {this.state.info ? `\n${this.state.info.componentStack}` : ''}
        </pre>
        <button className="startbtn" onClick={() => window.location.reload()}>Reload</button>
      </div>
    );
  }
}
