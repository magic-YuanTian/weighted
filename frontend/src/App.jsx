import React from 'react';
import AgentApp from './agent/AgentApp';
import ErrorBoundary from './agent/ErrorBoundary';

function App() {
  return <ErrorBoundary><AgentApp /></ErrorBoundary>;
}

export default App;
