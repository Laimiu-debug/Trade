import React from 'react';
import ReactDOM from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import App from './App';
import { AiBusyProvider } from './AiBusy';
import { ToastProvider } from './components';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <HashRouter>
      <ToastProvider>
        <AiBusyProvider>
          <App />
        </AiBusyProvider>
      </ToastProvider>
    </HashRouter>
  </React.StrictMode>,
);
