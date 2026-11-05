'use strict';
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {
      // Home Screen installation still works when offline support is unavailable.
    });
  });
}
const connectionStatus = document.getElementById('connection-status');
function updateConnectionStatus() {
  if (connectionStatus) connectionStatus.hidden = navigator.onLine;
}
window.addEventListener('online', updateConnectionStatus);
window.addEventListener('offline', updateConnectionStatus);
updateConnectionStatus();
