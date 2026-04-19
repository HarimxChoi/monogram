/* Monogram dashboard — client JS.
 * Decrypted into place after password unlock. All logic local.
 */
(function () {
  'use strict';

  // ── wiki search (Cmd/Ctrl+K) ──
  const searchInput = document.querySelector('.wiki__search input');
  const wikiItems = Array.from(document.querySelectorAll('.wiki-item'));

  function filterWiki(query) {
    const q = (query || '').trim().toLowerCase();
    wikiItems.forEach((el) => {
      if (!q) { el.style.display = ''; return; }
      const hay = el.textContent.toLowerCase();
      el.style.display = hay.includes(q) ? '' : 'none';
    });
  }
  if (searchInput) {
    searchInput.addEventListener('input', (e) => filterWiki(e.target.value));
  }
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      if (searchInput) searchInput.focus();
    }
    if (e.key === 'Escape' && document.activeElement === searchInput) {
      searchInput.value = '';
      filterWiki('');
      searchInput.blur();
    }
  });

  // ── refresh button ──
  const refreshBtn = document.querySelector('[data-action="refresh"]');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.disabled = true;
      try {
        // Self-host mode exposes /api/refresh; other modes just reload.
        const resp = await fetch('/api/refresh', { method: 'POST' });
        if (resp.ok) {
          // The server returns a fresh encrypted shell. Reload to pick it up.
          window.location.reload();
        } else {
          window.location.reload();
        }
      } catch {
        window.location.reload();
      } finally {
        refreshBtn.disabled = false;
      }
    });
  }

  // ── auto-refresh toggle ──
  const autoBtn = document.querySelector('[data-action="auto-refresh"]');
  let autoHandle = null;
  let autoOn = false;
  if (autoBtn) {
    autoBtn.addEventListener('click', () => {
      autoOn = !autoOn;
      autoBtn.textContent = autoOn ? 'Auto · On ▾' : 'Auto · Off ▾';
      if (autoOn) {
        autoHandle = setInterval(() => window.location.reload(), 120000);
      } else if (autoHandle) {
        clearInterval(autoHandle);
        autoHandle = null;
      }
    });
  }
})();
