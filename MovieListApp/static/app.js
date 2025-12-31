async function searchMovies(title, year) {
  const params = new URLSearchParams();
  if (title) params.set('title', title);
  if (year) params.set('year', year);
  const res = await fetch('/api/search?' + params.toString());
  return res.json();
}

// diagnostic: confirm script loaded
try { console.log('app.js loaded'); } catch (e) {}

// Convert runtime in minutes (number or numeric string) to H:MM display.
function formatRuntime(raw) {
  if (raw === null || raw === undefined) return '';
  const s = String(raw).trim();
  if (!s) return '';
  // Extract leading integer minutes (ignore trailing 'min' etc.)
  const n = parseInt(s.replace(/[^0-9]/g, ''), 10);
  if (Number.isNaN(n)) return '';
  const h = Math.floor(n / 60);
  const m = n % 60;
  return `${h}:${m.toString().padStart(2, '0')}`;
}

async function addMovie(id, list_name) {
  const res = await fetch('/api/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, list_name }),
  });
  return res.json();
}

async function getList(list_name) {
  const url = '/api/list?' + new URLSearchParams({ list_name });
  const res = await fetch(url);
  return res.json();
}

async function getLists() {
  const res = await fetch('/api/lists');
  return res.json();
}

async function createList(name) {
  const res = await fetch('/api/lists', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  return res.json();
}

async function deleteList(name) {
  const res = await fetch('/api/lists', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  return res.json();
}

async function renameList(oldName, newName) {
  const res = await fetch('/api/lists/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old: oldName, new: newName }),
  });
  return res.json();
}

function renderList(container, listing) {
  container.innerHTML = '';
  if (!Object.keys(listing).length) {
    container.innerHTML = '<p>No movies in list yet.</p>';
    return;
  }

  for (const year of Object.keys(listing)) {
    const yEl = document.createElement('div');
    yEl.className = 'year';
    const h = document.createElement('h3');
    h.textContent = year;
    yEl.appendChild(h);

    for (const genre of Object.keys(listing[year])) {
      const gEl = document.createElement('div');
      gEl.className = 'genre';
      const gh = document.createElement('h4');
      gh.textContent = genre;
      gEl.appendChild(gh);

      const ul = document.createElement('ul');
      for (const movie of listing[year][genre]) {
        const li = document.createElement('li');
        // movie title with runtime
        const titleSpan = document.createElement('span');
        const formatted = formatRuntime(movie.runtime);
        titleSpan.textContent = movie.title;
        li.appendChild(titleSpan);
        
        // runtime in separate span with sans-serif font
        if (formatted) {
          const runtimeSpan = document.createElement('span');
          runtimeSpan.className = 'runtime';
          runtimeSpan.textContent = ` (${formatted})`;
          li.appendChild(runtimeSpan);
        }

          // remove button (with confirmation)
          const removeBtn = document.createElement('button');
          removeBtn.type = 'button';
          removeBtn.textContent = 'Remove';
          removeBtn.style.marginLeft = '8px';
          removeBtn.addEventListener('click', async () => {
            if (!confirm(`Remove "${movie.title}" from ${genre} (${year})?`)) return;
            const select = document.getElementById('list-select');
            const list_name = select ? select.value : 'default';
            await fetch('/api/remove', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id: movie.id, year: year, genre: genre, list_name }),
            });
            if (window.refreshList) await window.refreshList();
          });
          li.appendChild(removeBtn);

          // edit button (opens inline modal)
          const editBtn = document.createElement('button');
          editBtn.type = 'button';
          editBtn.textContent = 'Edit';
          editBtn.style.marginLeft = '6px';
          editBtn.addEventListener('click', () => {
            // dispatch a custom event to open the modal (modal handlers are set up in DOMContentLoaded)
            const ev = new CustomEvent('openEditModal', { detail: { id: movie.id, title: movie.title, year: year, genre: genre, synopsis: movie.synopsis || '' } });
            window.dispatchEvent(ev);
          });
          li.appendChild(editBtn);

        // synopsis paragraph (optional)
        if (movie.synopsis && movie.synopsis.trim()) {
          const p = document.createElement('p');
          p.className = 'synopsis';
          p.textContent = movie.synopsis;
          li.appendChild(p);
        }

        ul.appendChild(li);
      }
      gEl.appendChild(ul);
      yEl.appendChild(gEl);
    }

    container.appendChild(yEl);
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  try { console.log('app.js DOMContentLoaded'); } catch (e) {}
  const form = document.getElementById('search-form');
  const titleInput = document.getElementById('title');
  const yearInput = document.getElementById('year');
  const results = document.getElementById('results');
  const listContainer = document.getElementById('movie-list');
  const clearBtn = document.getElementById('clear-list');
  const modal = document.getElementById('edit-modal');
  const modalTitle = document.getElementById('edit-title');
  const modalYear = document.getElementById('edit-year');
  const modalGenre = document.getElementById('edit-genre');
  const modalSynopsis = document.getElementById('edit-synopsis');
  const modalSynopsisCounter = document.getElementById('synopsis-counter');
  const modalSave = document.getElementById('edit-save');
  const modalCancel = document.getElementById('edit-cancel');

  let editingContext = null; // {id, origYear, origGenre}

  // expose refreshList globally so renderList can call it when needed
  window.refreshList = async function() {
    const select = document.getElementById('list-select');
    const list_name = select ? select.value : 'default';
    if (!list_name) {
      // no list selected — show a helpful prompt instead of loading default
      listContainer.innerHTML = '<p>Please pick a list from the dropdown menu.</p>';
      return;
    }
    const listing = await getList(list_name);
    renderList(listContainer, listing);
  };

  async function refreshListsDropdown() {
    const lists = await getLists();
    const select = document.getElementById('list-select');
    select.innerHTML = '';
    // placeholder prompt option
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Pick a List';
    select.appendChild(placeholder);

  // hide the internal 'default' list from the dropdown UI
  const names = (lists || []).map(r => (r.name || (Array.isArray(r) ? r[0] : r[0]))).filter(n => n !== 'default');
    for (const n of names) {
      const opt = document.createElement('option');
      opt.value = n;
      opt.textContent = n;
      select.appendChild(opt);
    }
  }

  // SSE stream for live updates
  let es = null;
  function openStreamFor(list_name) {
    if (es) {
      try { es.close(); } catch (e) {}
      es = null;
    }
    try {
      es = new EventSource('/stream?list_name=' + encodeURIComponent(list_name));
      es.onmessage = function(e) {
        // when server notifies, refresh the list
        window.refreshList();
      };
      es.onerror = function() {
        // try reconnect later; EventSource auto-reconnects
      };
    } catch (e) {
      // EventSource not supported or failed — fallback to polling every 5s
      if (es) try { es.close(); } catch (e) {}
      es = null;
      setInterval(() => {
        window.refreshList();
      }, 5000);
    }
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    results.innerHTML = '<p>Searching…</p>';
    const items = await searchMovies(titleInput.value, yearInput.value);
    if (items.error) {
      results.innerHTML = `<p class="error">${items.error}</p>`;
      return;
    }

    if (!items.length) {
      results.innerHTML = '<p>No results found.</p>';
      return;
    }

    // render results with add buttons
  results.innerHTML = '';
    const ul = document.createElement('ul');
    for (const it of items) {
      const li = document.createElement('li');
      const formatted = formatRuntime(it.runtime);
      const runtimeText = formatted ? ` — ${formatted}` : '';
      li.textContent = `${it.title} (${it.start_year || 'Unknown'})${runtimeText} — ${it.genres || ''}`;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = 'Add';
      btn.addEventListener('click', async () => {
        const select = document.getElementById('list-select');
        const list_name = select ? select.value : 'default';
        await addMovie(it.id, list_name);
        refreshList();
      });
      li.appendChild(btn);
      ul.appendChild(li);
    }
    results.appendChild(ul);
    // If the user didn't include a year, show a helpful suggestion under results
    try {
      if (!yearInput.value) {
        const note = document.createElement('p');
        note.className = 'search-note';
        note.textContent = 'For more accurate results, please include a year in your search.';
        results.appendChild(note);
      }
    } catch (e) {
      console.warn('Could not append search note', e);
    }
  });

  // wire up modal open event
  window.addEventListener('openEditModal', (e) => {
    const d = e.detail || {};
    editingContext = { id: d.id, origYear: d.year, origGenre: d.genre };
    modalTitle.value = d.title || '';
    modalYear.value = d.year || '';
    modalGenre.value = d.genre || '';
    if (modalSynopsis) {
      modalSynopsis.value = d.synopsis || '';
      if (modalSynopsisCounter) modalSynopsisCounter.textContent = `Characters: ${modalSynopsis.value.length}/250`;
    }
    modal.style.display = 'flex';
  });

  if (modalCancel) {
    modalCancel.addEventListener('click', () => {
      modal.style.display = 'none';
      editingContext = null;
    });
  } else {
    console.warn('modalCancel element not found');
  }

  if (modalSave) {
    modalSave.addEventListener('click', async () => {
      if (!editingContext) return;
      const payload = { id: editingContext.id };
    const newTitle = modalTitle.value && modalTitle.value.trim();
    const newYear = modalYear.value && modalYear.value.trim();
    const newGenre = modalGenre.value && modalGenre.value.trim();
    const newSynopsisRaw = modalSynopsis ? modalSynopsis.value : '';
    const newSynopsis = newSynopsisRaw ? newSynopsisRaw.trim().slice(0,250) : null;
    if (newTitle) payload.new_title = newTitle;
    if (newYear) payload.new_year = newYear;
    if (newGenre) payload.new_genre = newGenre;
    if (newSynopsis !== null) payload.new_synopsis = newSynopsis;
    // include selected list
    const select = document.getElementById('list-select');
    const list_name = select ? select.value : 'default';
    payload.list_name = list_name;
    await fetch('/api/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    modal.style.display = 'none';
    editingContext = null;
    await window.refreshList();
    });
  } else {
    console.warn('modalSave element not found');
  }

  // live counter for synopsis textarea
  if (modalSynopsis) {
    modalSynopsis.addEventListener('input', () => {
      if (modalSynopsisCounter) modalSynopsisCounter.textContent = `Characters: ${modalSynopsis.value.length}/250`;
    });
  }

  // clear list button
  if (clearBtn) {
    clearBtn.addEventListener('click', async () => {
      if (!confirm('Clear entire saved list? This cannot be undone.')) return;
      const select = document.getElementById('list-select');
      const list_name = select ? select.value : 'default';
      await fetch('/api/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ list_name }) });
      await window.refreshList();
    });
  } else {
    console.warn('clearBtn not found');
  }

  // list controls
  const select = document.getElementById('list-select');
  const newBtn = document.getElementById('new-list');
  const renameBtn = document.getElementById('rename-list');
  const deleteBtn = document.getElementById('delete-list');

  if (newBtn) {
    newBtn.addEventListener('click', async () => {
      try { console.log('New list button clicked'); } catch (e) {}
      const name = prompt('New list name:');
      if (!name) return;
      try {
        await createList(name.trim());
        await refreshListsDropdown();
        select.value = name.trim();
        await window.refreshList();
        openStreamFor(name.trim());
      } catch (err) {
        console.error('createList failed', err);
        alert('Could not create list: ' + (err && err.message ? err.message : err));
      }
    });
  }

  renameBtn.addEventListener('click', async () => {
    const oldName = select.value;
    if (!oldName || oldName === 'default') { alert('Cannot rename default list'); return; }
    const newName = prompt('Rename list to:', oldName);
    if (!newName || newName === oldName) return;
    await renameList(oldName, newName.trim());
    await refreshListsDropdown();
    select.value = newName.trim();
    await window.refreshList();
  });

  deleteBtn.addEventListener('click', async () => {
    const name = select.value;
    if (!name || name === 'default') { alert('Cannot delete default list'); return; }
    if (!confirm(`Delete list ${name}? This will remove all movies in that list.`)) return;
    await deleteList(name);
    await refreshListsDropdown();
    select.value = '';
    await window.refreshList();
  });

  select.addEventListener('change', async () => {
    await window.refreshList();
    // if no list selected, close any existing stream; otherwise open for selected list
    if (!select.value) {
      if (es) {
        try { es.close(); } catch (e) {}
        es = null;
      }
      return;
    }
    openStreamFor(select.value);
  });

  // initial load — show placeholder and don't auto-open a stream
  await refreshListsDropdown();
  document.getElementById('list-select').value = '';
  await window.refreshList();
});
