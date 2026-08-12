/* Casita — single-page app, vanilla JS, no build step.

   Rendering is deliberately dumb: every mutation refetches the slice of state it
   touched and re-renders the current tab. For a house of four people and a few
   hundred rows that's instant, and it means there's exactly one source of truth
   (the server) instead of a client cache to keep honest. */

'use strict';

// ---------------------------------------------------------------- helpers ---

const $ = (sel) => document.querySelector(sel);
const state = {
  tab: 'home',
  me: null,
  members: [],
  house: 'Casita',
  authRequired: true,
  today: null,
  data: {},
};

/** Escape anything that came from a roommate before it touches innerHTML. */
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  // A 401 anywhere else means the session lapsed, so bounce to the gate. On the
  // login call itself it just means a bad passcode, and the form wants to show
  // the server's own wording rather than "Not signed in".
  if (res.status === 401 && !path.endsWith('/login')) {
    showGate();
    throw new Error('Not signed in');
  }
  if (!res.ok) {
    let detail = 'Something went wrong';
    try {
      const payload = await res.json();
      // FastAPI validation errors arrive as a list of objects, not a string.
      detail = typeof payload.detail === 'string' ? payload.detail : detail;
    } catch { /* non-JSON error body; keep the generic message */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

const GET = (p) => api('GET', p);
const POST = (p, b) => api('POST', p, b);
const PUT = (p, b) => api('PUT', p, b);
const DEL = (p) => api('DELETE', p);

let toastTimer;
/** `action` is an optional {label, run} — it turns the toast into an Undo. */
function toast(message, action) {
  const node = $('#toast');
  node.textContent = message;
  if (action) {
    const button = document.createElement('button');
    button.className = 'toast-action';
    button.textContent = action.label;
    button.addEventListener('click', () => {
      node.hidden = true;
      clearTimeout(toastTimer);
      action.run();
    });
    node.append(button);
  }
  node.classList.toggle('actionable', Boolean(action));
  node.hidden = false;
  clearTimeout(toastTimer);
  // An undo needs long enough to notice the mistake and reach for it.
  toastTimer = setTimeout(() => { node.hidden = true; }, action ? 7000 : 2200);
}

/** Reverse the last completion of a chore, then refresh. */
async function undoCompletion(choreId) {
  try {
    await POST(`/api/chores/${choreId}/undo`, {});
    toast('Put back');
  } catch (err) {
    toast(err.message);
  }
  render();
}

function money(cents) {
  const sign = cents < 0 ? '-' : '';
  const n = Math.abs(cents);
  return `${sign}$${Math.floor(n / 100)}.${String(n % 100).padStart(2, '0')}`;
}

/** Parse a typed dollar amount into integer cents, or null if it's not one. */
function toCents(text) {
  const cleaned = String(text).replace(/[^0-9.]/g, '');
  if (!cleaned) return null;
  const value = Number.parseFloat(cleaned);
  if (!Number.isFinite(value) || value <= 0) return null;
  return Math.round(value * 100);
}

const DAY_MS = 86400000;

function parseDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function todayDate() {
  return state.today ? parseDate(state.today) : new Date();
}

function isoDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

/** "Today", "Tomorrow", "3 days late", "Fri 14 Aug". */
function relativeDay(iso) {
  const days = Math.round((parseDate(iso) - todayDate()) / DAY_MS);
  if (days === 0) return 'Today';
  if (days === 1) return 'Tomorrow';
  if (days === -1) return 'Yesterday';
  if (days < 0) return `${Math.abs(days)} days late`;
  if (days <= 6) return parseDate(iso).toLocaleDateString(undefined, { weekday: 'long' });
  return parseDate(iso).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

function timeLabel(t) {
  if (!t) return 'All day';
  const [h, m] = t.split(':').map(Number);
  const date = new Date();
  date.setHours(h, m, 0, 0);
  return date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

function avatar(member, size) {
  if (!member) return `<span class="avatar" title="Unassigned">·</span>`;
  const style = `--c: var(--${esc(member.color)}); ${size ? `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.55)}px;` : ''}`;
  return `<span class="avatar" style="${style}" title="${esc(member.name)}">${esc(member.emoji)}</span>`;
}

const isMe = (member) => !!(member && state.me && member.id === state.me.id);

// ------------------------------------------------------------------ sheet ---

function openSheet(title, html, onMount) {
  $('#sheet-title').textContent = title;
  $('#sheet-body').innerHTML = html;
  $('#sheet').hidden = false;
  if (onMount) onMount($('#sheet-body'));
}

function closeSheet() {
  $('#sheet').hidden = true;
  $('#sheet-body').innerHTML = '';
}

$('#sheet-close').addEventListener('click', closeSheet);
$('#sheet').addEventListener('click', (e) => {
  if (e.target.id === 'sheet') closeSheet();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#sheet').hidden) closeSheet();
});

// ------------------------------------------------------------------- gate ---

function showGate() {
  $('#app').hidden = true;
  $('#whoami').hidden = true;
  $('#gate').hidden = false;
  $('#gate-title').textContent = state.house;
}

$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const error = $('#login-error');
  error.hidden = true;
  try {
    await POST('/api/login', { passcode: $('#passcode').value });
    $('#passcode').value = '';
    await boot();
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
});

function showWhoami() {
  $('#app').hidden = true;
  $('#gate').hidden = true;
  $('#whoami').hidden = false;
  $('#whoami-list').innerHTML = state.members.map((m) => `
    <button class="who-btn" data-member="${m.id}">
      ${avatar(m, 32)}<strong>${esc(m.name)}</strong>
    </button>`).join('')
    || '<p class="muted">No roommates yet — add the first one.</p>';
}

$('#whoami-list').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-member]');
  if (!btn) return;
  await POST('/api/identify', { member_id: Number(btn.dataset.member) });
  await boot();
});

$('#whoami-add').addEventListener('click', () => {
  $('#whoami').hidden = true;
  $('#app').hidden = false;
  memberForm();
});

// ------------------------------------------------------------------- boot ---

async function boot() {
  const me = await GET('/api/me');
  state.house = me.house_name;
  state.authRequired = me.auth_required;
  state.today = me.today;
  document.title = me.house_name;
  $('#house-name').textContent = me.house_name;

  if (!me.authenticated) { showGate(); return; }

  state.members = me.members;
  state.me = me.member;

  // A house with no roommates yet goes straight to onboarding.
  if (state.members.length === 0) {
    $('#gate').hidden = true;
    $('#whoami').hidden = true;
    $('#app').hidden = false;
    renderMeChip();
    await onboarding();
    return;
  }
  if (!state.me) { showWhoami(); return; }

  $('#gate').hidden = true;
  $('#whoami').hidden = true;
  $('#app').hidden = false;
  renderMeChip();
  await render();
}

function renderMeChip() {
  $('#me-chip').innerHTML = state.me
    ? `${avatar(state.me, 24)}<span>${esc(state.me.name)}</span>`
    : '<span>Sign in</span>';
}

$('#me-chip').addEventListener('click', settingsSheet);

// ------------------------------------------------------------------- tabs ---

$('#tabbar').addEventListener('click', (e) => {
  const tab = e.target.closest('[data-tab]');
  if (!tab) return;
  state.tab = tab.dataset.tab;
  // Always open the recap on the current month rather than wherever the last
  // browse left off.
  if (state.tab === 'recap') state.recapMonth = null;
  render();
});

function syncTabs() {
  document.querySelectorAll('.tab').forEach((t) => {
    t.classList.toggle('active', t.dataset.tab === state.tab);
  });
}

const VIEWS = {
  home: renderHome,
  chores: renderChores,
  shopping: renderShopping,
  calendar: renderCalendar,
  money: renderMoney,
  recap: renderRecap,
};

async function render() {
  syncTabs();
  $('#view').innerHTML = '<p class="muted">Loading…</p>';
  try {
    await VIEWS[state.tab]();
  } catch (err) {
    $('#view').innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
  window.scrollTo({ top: 0 });
}

// ------------------------------------------------------------------- home ---

function greeting() {
  const hour = new Date().getHours();
  if (hour < 5) return 'Still up';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

async function renderHome() {
  const [summary, notes] = await Promise.all([GET('/api/summary'), GET('/api/notes')]);
  state.data.summary = summary;

  const needsDoing = [...summary.overdue, ...summary.due_today];
  const balance = summary.my_balance_cents;

  $('#view').innerHTML = `
    <div class="section">
      <h2 style="font-size:22px">${greeting()}${state.me ? `, ${esc(state.me.name)}` : ''}</h2>
      <p class="muted">${parseDate(summary.today).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</p>
    </div>

    <div class="section stat-grid">
      <div class="stat"><div class="k">Needs doing</div>
        <div class="v ${needsDoing.length ? 'bad' : 'good'}">${needsDoing.length}</div></div>
      <div class="stat"><div class="k">Shopping list</div>
        <div class="v">${summary.shopping_count}</div></div>
      <div class="stat"><div class="k">Later this week</div>
        <div class="v">${summary.this_week.length}</div></div>
      <div class="stat"><div class="k">${balance >= 0 ? "You're owed" : 'You owe'}</div>
        <div class="v ${balance > 0 ? 'good' : balance < 0 ? 'bad' : ''}">${money(Math.abs(balance))}</div></div>
    </div>

    <div class="section">
      <div class="section-head"><h2>🔥 Needs doing</h2>
        <button class="btn btn-sm btn-ghost" data-goto="chores">All chores</button></div>
      <div class="stack">
        ${needsDoing.length
          ? needsDoing.map(choreRow).join('')
          : '<div class="empty">Nothing overdue. The house is in good shape. ✨</div>'}
      </div>
    </div>

    <div class="section">
      <div class="section-head"><h2>📅 Coming up</h2>
        <button class="btn btn-sm btn-ghost" data-goto="calendar">Calendar</button></div>
      <div class="stack">
        ${summary.upcoming_events.length
          ? summary.upcoming_events.map(eventRow).join('')
          : '<div class="empty">Nothing on the calendar.</div>'}
      </div>
    </div>

    <div class="section">
      <div class="section-head"><h2>📌 House board</h2>
        <button class="btn btn-sm" data-action="add-note">+ Post</button></div>
      <div class="stack">
        ${notes.length
          ? notes.map(noteCard).join('')
          : '<div class="empty">Anything the house should know — wifi password, quiet hours, a rant about the dishes.</div>'}
      </div>
    </div>

  `;
}

function noteCard(note) {
  return `
    <div class="note ${note.pinned ? 'pinned' : ''}">
      <div>${esc(note.body)}</div>
      <div class="note-meta">
        ${note.author ? avatar(note.author, 20) : ''}
        <span>${note.author ? esc(note.author.name) : 'Someone'}</span>
        <span>·</span>
        <span>${new Date(note.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>
        <button class="icon-btn" style="margin-left:auto" data-pin="${note.id}" title="Pin">${note.pinned ? '📌' : '📍'}</button>
        <button class="icon-btn" data-del-note="${note.id}" title="Delete">🗑</button>
      </div>
    </div>`;
}

// ----------------------------------------------------------------- chores ---

function choreRow(chore) {
  const who = chore.assignee;
  const mine = isMe(who);
  const statusPill = chore.status === 'overdue'
    ? `<span class="pill overdue">${esc(relativeDay(chore.due_on))}</span>`
    : `<span class="pill ${chore.status === 'today' ? 'today' : ''}">${esc(relativeDay(chore.due_on))}</span>`;

  return `
    <div class="row ${chore.status}" data-chore="${chore.id}">
      <button class="tick" data-complete="${chore.id}" aria-label="Mark ${esc(chore.name)} done">✓</button>
      <span class="row-emoji">${esc(chore.emoji)}</span>
      <div class="row-main" data-open-chore="${chore.id}">
        <div class="row-title">${esc(chore.name)}</div>
        <div class="row-sub">
          ${statusPill}
          <span class="pill">${esc(chore.cadence_label)}</span>
          ${/* Only shown when someone has deliberately claimed it. An
                unassigned chore says nothing rather than "Anyone", so the
                default state is quiet. */ ''}
          ${who ? `<span class="pill ${mine ? 'mine' : ''}">${esc(who.emoji)} ${mine ? 'You' : esc(who.name)}</span>` : ''}
        </div>
      </div>
    </div>`;
}

async function renderChores() {
  const chores = await GET('/api/chores');
  state.data.chores = chores;

  const groups = [
    ['Overdue', chores.filter((c) => c.status === 'overdue')],
    ['Today', chores.filter((c) => c.status === 'today')],
    ['This week', chores.filter((c) => c.status === 'soon')],
    ['Later', chores.filter((c) => c.status === 'later')],
  ].filter(([, list]) => list.length);

  $('#view').innerHTML = `
    <div class="section-head">
      <h2>🧹 Chores</h2>
      <button class="btn btn-sm btn-primary" data-action="add-chore">+ New chore</button>
    </div>
    ${groups.length ? groups.map(([label, list]) => `
      <div class="day-group">
        <div class="day-label">${label} · ${list.length}</div>
        <div class="stack">${list.map(choreRow).join('')}</div>
      </div>`).join('')
      : `<div class="empty">No chores yet.<br><br>
           <button class="btn btn-primary" data-action="starter-chores">Add a starter set</button></div>`}
  `;
}

function choreForm(existing) {
  const c = existing || {
    name: '', emoji: '🧹', notes: '', cadence: 'weekly', interval_n: 1,
    due_on: isoDate(todayDate()), rotation_mode: 'anyone', assignee: null, rotation: [],
  };
  const ringIds = (c.rotation || []).map((m) => m.id);

  const html = `
    <div class="field">
      <label>What needs doing</label>
      <div style="display:flex;gap:8px">
        <input id="f-emoji" value="${esc(c.emoji)}" style="width:64px;text-align:center" aria-label="Emoji" />
        <input id="f-name" value="${esc(c.name)}" placeholder="Take out the trash" />
      </div>
    </div>
    <div class="field-row">
      <div class="field">
        <label>How often</label>
        <select id="f-cadence">
          ${['daily', 'weekly', 'monthly', 'once'].map((v) => `
            <option value="${v}" ${c.cadence === v ? 'selected' : ''}>
              ${{ daily: 'Every day', weekly: 'Every week', monthly: 'Every month', once: 'One-off' }[v]}
            </option>`).join('')}
        </select>
      </div>
      <div class="field">
        <label>Repeat every</label>
        <input id="f-interval" type="number" min="1" max="365" value="${c.interval_n}" />
      </div>
    </div>
    <div class="field">
      <label>Next due</label>
      <input id="f-due" type="date" value="${esc(c.due_on)}" />
    </div>
    <div class="field">
      <label>Who does it <span style="font-weight:400">— optional</span></label>
      <div class="chip-select" id="f-mode">
        ${[['anyone', 'Nobody in particular'], ['fixed', 'One person'], ['rotate', 'Take turns']].map(([v, label]) => `
          <button type="button" class="chip ${c.rotation_mode === v ? 'on' : ''}" data-mode="${v}">${label}</button>`).join('')}
      </div>
    </div>
    <div class="field" id="f-people-wrap" ${c.rotation_mode === 'anyone' ? 'hidden' : ''}>
      <label id="f-people-label"></label>
      <div class="chip-select" id="f-people">
        ${state.members.map((m) => `
          <button type="button" class="chip" data-person="${m.id}">
            <span class="ord"></span>${esc(m.emoji)} ${esc(m.name)}
          </button>`).join('')}
      </div>
    </div>
    <div class="field">
      <label>Notes (optional)</label>
      <textarea id="f-notes" placeholder="Bins go out Tuesday night">${esc(c.notes)}</textarea>
    </div>
    <button class="btn btn-primary btn-block" id="f-save">${existing ? 'Save changes' : 'Add chore'}</button>
    ${existing ? `<button class="btn btn-ghost btn-danger btn-block" id="f-delete">Delete chore</button>` : ''}
  `;

  openSheet(existing ? 'Edit chore' : 'New chore', html, (root) => {
    let mode = c.rotation_mode;
    // Ordered: for a rotation the order of taps is the order of turns.
    let picked = mode === 'rotate' ? [...ringIds] : (c.assignee ? [c.assignee.id] : []);

    const paint = () => {
      root.querySelectorAll('[data-mode]').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
      root.querySelector('#f-people-wrap').hidden = mode === 'anyone';
      root.querySelector('#f-people-label').textContent =
        mode === 'rotate' ? 'Tap in the order they take turns' : 'Whose job is it';
      root.querySelectorAll('[data-person]').forEach((b) => {
        const id = Number(b.dataset.person);
        const at = picked.indexOf(id);
        b.classList.toggle('on', at !== -1);
        b.querySelector('.ord').textContent = mode === 'rotate' && at !== -1 ? `${at + 1}` : '';
      });
    };

    root.querySelector('#f-mode').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-mode]');
      if (!btn) return;
      mode = btn.dataset.mode;
      if (mode === 'anyone') picked = [];
      if (mode === 'fixed' && picked.length > 1) picked = picked.slice(0, 1);
      paint();
    });

    root.querySelector('#f-people').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-person]');
      if (!btn) return;
      const id = Number(btn.dataset.person);
      if (mode === 'fixed') {
        picked = picked[0] === id ? [] : [id];
      } else {
        const at = picked.indexOf(id);
        if (at === -1) picked.push(id); else picked.splice(at, 1);
      }
      paint();
    });

    root.querySelector('#f-save').addEventListener('click', async () => {
      const name = root.querySelector('#f-name').value.trim();
      if (!name) { toast('Give it a name'); return; }
      const payload = {
        name,
        emoji: root.querySelector('#f-emoji').value.trim() || '🧹',
        notes: root.querySelector('#f-notes').value,
        cadence: root.querySelector('#f-cadence').value,
        interval_n: Math.max(1, Number(root.querySelector('#f-interval').value) || 1),
        due_on: root.querySelector('#f-due').value || null,
        rotation_mode: mode,
        assignee_id: mode === 'fixed' ? (picked[0] ?? null) : null,
        rotation_member_ids: mode === 'rotate' ? picked : [],
      };
      if (mode === 'rotate' && picked.length < 2) { toast('Pick at least two people to take turns'); return; }
      if (mode === 'fixed' && !picked.length) { toast('Pick who does it'); return; }

      try {
        if (existing) await PUT(`/api/chores/${existing.id}`, payload);
        else await POST('/api/chores', payload);
        closeSheet();
        toast(existing ? 'Saved' : 'Chore added');
        render();
      } catch (err) { toast(err.message); }
    });

    const delBtn = root.querySelector('#f-delete');
    if (delBtn) {
      delBtn.addEventListener('click', async () => {
        if (!confirm(`Delete "${c.name}"? This can't be undone.`)) return;
        await DEL(`/api/chores/${existing.id}`);
        closeSheet();
        toast('Deleted');
        render();
      });
    }

    paint();
  });
}

function choreDetail(chore) {
  const html = `
    <div class="card" style="margin-bottom:14px">
      <div class="row-title" style="font-size:18px">${esc(chore.emoji)} ${esc(chore.name)}</div>
      <div class="row-sub" style="margin-top:8px">
        <span class="pill ${chore.status}">${esc(relativeDay(chore.due_on))}</span>
        <span class="pill">${esc(chore.cadence_label)}</span>
        ${chore.assignee ? `<span class="pill">${esc(chore.assignee.emoji)} ${esc(chore.assignee.name)}</span>` : '<span class="pill">Anyone</span>'}
      </div>
      ${chore.notes ? `<p class="muted" style="margin-top:10px;white-space:pre-wrap">${esc(chore.notes)}</p>` : ''}
      ${chore.rotation_mode === 'rotate' && chore.next_up
        ? `<p class="muted" style="margin-top:10px">Next up after this: ${esc(chore.next_up.emoji)} ${esc(chore.next_up.name)}</p>` : ''}
    </div>
    <button class="btn btn-primary btn-block" data-do="complete">✓ Mark done</button>
    <div class="fab-row" style="margin-top:10px">
      <button class="btn btn-block" style="margin-top:0" data-do="snooze">Snooze a day</button>
      <button class="btn btn-block" style="margin-top:0" data-do="edit">Edit</button>
    </div>
    <button class="btn btn-ghost btn-block" data-do="undo">↩ Undo last completion</button>
    <p class="swipe-hint">Puts the due date and whose turn it is back to what they were.</p>
    ${state.members.length ? `
      <div class="field" style="margin-top:16px">
        <label>Hand it to someone</label>
        <div class="chip-select">
          ${state.members.map((m) => `
            <button class="chip ${chore.assignee && chore.assignee.id === m.id ? 'on' : ''}" data-give="${m.id}">
              ${esc(m.emoji)} ${esc(m.name)}
            </button>`).join('')}
        </div>
      </div>` : ''}
  `;

  openSheet('Chore', html, (root) => {
    root.addEventListener('click', async (e) => {
      const give = e.target.closest('[data-give]');
      if (give) {
        await POST(`/api/chores/${chore.id}/reassign`, { member_id: Number(give.dataset.give) });
        closeSheet(); toast('Reassigned'); render();
        return;
      }
      const action = e.target.closest('[data-do]');
      if (!action) return;
      if (action.dataset.do === 'complete') {
        await POST(`/api/chores/${chore.id}/complete`, {});
        closeSheet();
        toast('Nice — done ✨', { label: 'Undo', run: () => undoCompletion(chore.id) });
        render();
      } else if (action.dataset.do === 'undo') {
        closeSheet();
        await undoCompletion(chore.id);
      } else if (action.dataset.do === 'snooze') {
        await POST(`/api/chores/${chore.id}/snooze`, { days: 1 });
        closeSheet(); toast('Pushed to tomorrow'); render();
      } else if (action.dataset.do === 'edit') {
        choreForm(chore);
      }
    });
  });
}

const STARTER_CHORES = [
  { name: 'Take out the trash', emoji: '🗑️', cadence: 'weekly' },
  { name: 'Dishes / empty dishwasher', emoji: '🍽️', cadence: 'daily' },
  { name: 'Clean the bathroom', emoji: '🚽', cadence: 'weekly' },
  { name: 'Vacuum common areas', emoji: '🧹', cadence: 'weekly' },
  { name: 'Wipe down the kitchen', emoji: '🧽', cadence: 'weekly' },
  { name: 'Recycling out', emoji: '♻️', cadence: 'weekly', interval_n: 2 },
];

async function addStarterChores() {
  const ids = state.members.map((m) => m.id);
  for (const [index, chore] of STARTER_CHORES.entries()) {
    await POST('/api/chores', {
      ...chore,
      interval_n: chore.interval_n || 1,
      // Stagger due dates so day one isn't a wall of six overdue chores.
      due_on: isoDate(new Date(todayDate().getTime() + index * DAY_MS)),
      rotation_mode: ids.length > 1 ? 'rotate' : 'anyone',
      rotation_member_ids: ids.length > 1 ? ids : [],
    });
  }
  toast('Starter chores added');
  render();
}

// --------------------------------------------------------------- shopping ---

async function renderShopping() {
  const items = await GET('/api/shopping');
  const open = items.filter((i) => !i.purchased);
  const done = items.filter((i) => i.purchased);

  const byCategory = {};
  open.forEach((i) => { (byCategory[i.category] ||= []).push(i); });

  $('#view').innerHTML = `
    <div class="section-head"><h2>🛒 Shopping</h2>
      ${done.length ? '<button class="btn btn-sm btn-ghost" data-action="clear-bought">Clear bought</button>' : ''}
    </div>
    <form class="quickadd" id="quick-shop">
      <input id="quick-shop-input" placeholder="Add an item…" aria-label="Add an item" />
      <button class="btn btn-primary" type="submit">Add</button>
    </form>
    ${open.length ? Object.entries(byCategory).map(([cat, list]) => `
      <div class="day-group">
        <div class="day-label">${esc(cat)} · ${list.length}</div>
        <div class="stack">${list.map(shopRow).join('')}</div>
      </div>`).join('')
      : '<div class="empty">List is empty. Add whatever the house is out of.</div>'}
    ${done.length ? `
      <div class="day-group">
        <div class="day-label">In the cart · ${done.length}</div>
        <div class="stack">${done.map(shopRow).join('')}</div>
      </div>` : ''}
  `;
  $('#quick-shop').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = $('#quick-shop-input');
    const name = input.value.trim();
    if (!name) return;
    input.value = '';
    await POST('/api/shopping', { name });
    renderShopping();
  });
}

function shopRow(item) {
  return `
    <div class="row ${item.purchased ? 'done' : ''}">
      <button class="tick ${item.purchased ? 'on' : ''}" data-buy="${item.id}" aria-label="Toggle ${esc(item.name)}">✓</button>
      <div class="row-main">
        <div class="row-title">${esc(item.name)}${item.is_staple ? ' <span class="pill">staple</span>' : ''}</div>
        ${/* Who added it and who bought it are still recorded, but the list
              itself stays anonymous — it's a shopping list, not a scoreboard.
              The numbers surface once a month in the Recap tab. */ ''}
        ${item.note ? `<div class="row-sub"><span class="pill">${esc(item.note)}</span></div>` : ''}
      </div>
      <button class="icon-btn" data-del-shop="${item.id}" aria-label="Remove">🗑</button>
    </div>`;
}

// --------------------------------------------------------------- calendar ---

function eventRow(event) {
  return `
    <div class="row">
      <span class="row-emoji">📅</span>
      <div class="row-main">
        <div class="row-title">${esc(event.title)}</div>
        <div class="row-sub">
          <span class="pill">${esc(relativeDay(event.starts_on))}</span>
          <span class="pill">${esc(timeLabel(event.starts_at))}</span>
          ${event.location ? `<span class="pill">📍 ${esc(event.location)}</span>` : ''}
          ${event.created_by ? `<span class="pill">${esc(event.created_by.emoji)}</span>` : ''}
        </div>
      </div>
      <button class="icon-btn" data-del-event="${event.id}" aria-label="Delete">🗑</button>
    </div>`;
}

async function renderCalendar() {
  const events = await GET('/api/events');
  const byDay = {};
  events.forEach((e) => { (byDay[e.starts_on] ||= []).push(e); });

  $('#view').innerHTML = `
    <div class="section-head"><h2>📅 House calendar</h2>
      <button class="btn btn-sm btn-primary" data-action="add-event">+ Event</button></div>
    ${events.length ? Object.entries(byDay).map(([day, list]) => `
      <div class="day-group">
        <div class="day-label">${esc(relativeDay(day))} · ${parseDate(day).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</div>
        <div class="stack">${list.map(eventRow).join('')}</div>
      </div>`).join('')
      : '<div class="empty">Nothing scheduled. Add a party, a landlord visit, a trip.</div>'}
  `;
}

function eventForm() {
  const html = `
    <div class="field"><label>What's happening</label>
      <input id="e-title" placeholder="Landlord inspection" /></div>
    <div class="field"><label>Date</label>
      <input id="e-date" type="date" value="${isoDate(todayDate())}" /></div>
    <div class="field-row">
      <div class="field"><label>Start (optional)</label><input id="e-start" type="time" /></div>
      <div class="field"><label>End (optional)</label><input id="e-end" type="time" /></div>
    </div>
    <div class="field"><label>Where (optional)</label><input id="e-loc" placeholder="Kitchen" /></div>
    <div class="field"><label>Notes (optional)</label><textarea id="e-notes"></textarea></div>
    <button class="btn btn-primary btn-block" id="e-save">Add to calendar</button>
  `;
  openSheet('New event', html, (root) => {
    root.querySelector('#e-save').addEventListener('click', async () => {
      const title = root.querySelector('#e-title').value.trim();
      if (!title) { toast('Give it a title'); return; }
      try {
        await POST('/api/events', {
          title,
          starts_on: root.querySelector('#e-date').value,
          starts_at: root.querySelector('#e-start').value || null,
          ends_at: root.querySelector('#e-end').value || null,
          location: root.querySelector('#e-loc').value,
          notes: root.querySelector('#e-notes').value,
        });
        closeSheet(); toast('Added'); render();
      } catch (err) { toast(err.message); }
    });
  });
}

// ------------------------------------------------------------------ money ---

async function renderMoney() {
  const [expenses, led] = await Promise.all([GET('/api/expenses'), GET('/api/ledger')]);

  $('#view').innerHTML = `
    <div class="section-head"><h2>💸 Money</h2>
      <button class="btn btn-sm btn-primary" data-action="add-expense">+ Expense</button></div>

    <div class="section">
      <div class="card">
        ${led.balances.length ? led.balances.map((b) => `
          <div class="balance-row">
            ${avatar(b.member)}
            <strong>${isMe(b.member) ? 'You' : esc(b.member.name)}</strong>
            <span class="balance-amt ${b.net_cents > 0 ? 'good' : b.net_cents < 0 ? 'bad' : ''}">
              ${b.net_cents === 0 ? 'settled' : money(b.net_cents)}
            </span>
          </div>`).join('')
          : '<p class="muted">No shared costs logged yet.</p>'}
      </div>
    </div>

    ${led.transfers.length ? `
      <div class="section">
        <div class="section-head"><h2>🤝 Settling up</h2>
          <button class="btn btn-sm btn-ghost" data-action="settle-all">Mark all settled</button></div>
        <div class="card">
          ${led.transfers.map((t) => `
            <div class="settle-line">
              ${avatar(t.from_member, 22)} <strong>${isMe(t.from_member) ? 'You' : esc(t.from_member.name)}</strong>
              <span class="arrow">→</span>
              ${avatar(t.to_member, 22)} <strong>${isMe(t.to_member) ? 'you' : esc(t.to_member.name)}</strong>
              <span class="balance-amt">${money(t.amount_cents)}</span>
            </div>`).join('')}
        </div>
      </div>` : ''}

    <div class="section">
      <div class="section-head"><h2>Recent</h2></div>
      <div class="stack">
        ${expenses.length ? expenses.map(expenseRow).join('')
          : '<div class="empty">Log rent, utilities, the Costco run — anything the house shares.</div>'}
      </div>
    </div>
  `;
}

function expenseRow(expense) {
  const perHead = expense.shares.length
    ? `${money(Math.round(expense.amount_cents / expense.shares.length))} each`
    : '';
  return `
    <div class="row ${expense.settled ? 'done' : ''}">
      <span class="row-emoji">🧾</span>
      <div class="row-main">
        <div class="row-title">${esc(expense.description)}</div>
        <div class="row-sub">
          <span class="pill">${esc(money(expense.amount_cents))}</span>
          ${expense.paid_by ? `<span class="pill">${esc(expense.paid_by.emoji)} ${isMe(expense.paid_by) ? 'You' : esc(expense.paid_by.name)} paid</span>` : ''}
          <span class="pill">${esc(perHead)}</span>
          ${expense.settled ? '<span class="pill">settled</span>' : ''}
        </div>
      </div>
      <button class="icon-btn" data-del-expense="${expense.id}" aria-label="Delete">🗑</button>
    </div>`;
}

function expenseForm() {
  const payerId = state.me ? state.me.id : (state.members[0] && state.members[0].id);
  const html = `
    <div class="field"><label>What was it for</label>
      <input id="x-desc" placeholder="Internet bill" /></div>
    <div class="field"><label>Amount</label>
      <input id="x-amt" inputmode="decimal" placeholder="0.00" /></div>
    <div class="field"><label>Who paid</label>
      <div class="chip-select" id="x-payer">
        ${state.members.map((m) => `
          <button type="button" class="chip ${m.id === payerId ? 'on' : ''}" data-payer="${m.id}">
            ${esc(m.emoji)} ${esc(m.name)}
          </button>`).join('')}
      </div></div>
    <div class="field"><label>Split between</label>
      <div class="chip-select" id="x-split">
        ${state.members.map((m) => `
          <button type="button" class="chip on" data-split="${m.id}">${esc(m.emoji)} ${esc(m.name)}</button>`).join('')}
      </div></div>
    <div class="field"><label>Date</label>
      <input id="x-date" type="date" value="${isoDate(todayDate())}" /></div>
    <button class="btn btn-primary btn-block" id="x-save">Add expense</button>
  `;

  openSheet('New expense', html, (root) => {
    let payer = payerId;
    const split = new Set(state.members.map((m) => m.id));

    root.querySelector('#x-payer').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-payer]');
      if (!btn) return;
      payer = Number(btn.dataset.payer);
      root.querySelectorAll('[data-payer]').forEach((b) => b.classList.toggle('on', Number(b.dataset.payer) === payer));
    });

    root.querySelector('#x-split').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-split]');
      if (!btn) return;
      const id = Number(btn.dataset.split);
      if (split.has(id)) split.delete(id); else split.add(id);
      btn.classList.toggle('on', split.has(id));
    });

    root.querySelector('#x-save').addEventListener('click', async () => {
      const description = root.querySelector('#x-desc').value.trim();
      const cents = toCents(root.querySelector('#x-amt').value);
      if (!description) { toast('What was it for?'); return; }
      if (!cents) { toast('Enter an amount'); return; }
      if (!payer) { toast('Who paid?'); return; }
      if (!split.size) { toast('Split between at least one person'); return; }
      try {
        await POST('/api/expenses', {
          description,
          amount_cents: cents,
          paid_by_id: payer,
          spent_on: root.querySelector('#x-date').value || null,
          split_between_ids: [...split],
        });
        closeSheet(); toast('Logged'); render();
      } catch (err) { toast(err.message); }
    });
  });
}

// ------------------------------------------------------------------ recap ---

/** Shift a "YYYY-MM" string by whole months. */
function monthShift(month, delta) {
  const [year, index] = month.split('-').map(Number);
  const date = new Date(year, index - 1 + delta, 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

const thisMonth = () => (state.today || isoDate(new Date())).slice(0, 7);

async function renderRecap() {
  const month = state.recapMonth || thisMonth();
  const recap = await GET(`/api/recap?month=${month}`);
  const atCurrent = month >= thisMonth();
  const { totals } = recap;

  $('#view').innerHTML = `
    <div class="month-nav">
      <button class="icon-btn" data-month="${monthShift(month, -1)}" aria-label="Previous month">‹</button>
      <h2>${esc(recap.label)}</h2>
      <button class="icon-btn" data-month="${monthShift(month, 1)}" ${atCurrent ? 'disabled' : ''} aria-label="Next month">›</button>
    </div>

    ${recap.is_current
      ? '<div class="banner">This month is still going — the numbers keep moving until it ends.</div>'
      : ''}

    <div class="recap-total">
      <strong>${totals.chores}</strong> chores done
      <span>·</span>
      <strong>${totals.items}</strong> shop runs
      <span>·</span>
      <strong>${money(totals.spent_cents)}</strong> spent
    </div>

    ${recap.rows.length
      ? recap.rows.map(recapCard).join('')
      : `<div class="empty">Nothing logged in ${esc(recap.label)} yet.<br><br>
           Tick chores off as you do them and they'll show up here at the end of the month.</div>`}
  `;
}

function recapCard(row, index) {
  const chores = row.chore_names
    .map((c) => `<span class="pill">${esc(c.name)}${c.count > 1 ? ` ×${c.count}` : ''}</span>`)
    .join('');

  return `
    <div class="recap-card">
      <div class="recap-top">
        ${avatar(row.member, 34)}
        <span class="name">${isMe(row.member) ? 'You' : esc(row.member.name)}</span>
        ${index === 0 && row.chores_done ? '<span class="recap-rank">most chores 🌿</span>' : ''}
      </div>
      <div class="recap-metrics">
        <div class="metric"><div class="n">${row.chores_done}</div><div class="l">chores</div></div>
        <div class="metric"><div class="n">${row.items_bought}</div><div class="l">bought</div></div>
        <div class="metric"><div class="n">${money(row.paid_cents)}</div><div class="l">paid</div></div>
      </div>
      ${chores ? `<div class="chore-tags">${chores}</div>` : ''}
    </div>`;
}

// -------------------------------------------------------------- roommates ---

function memberForm(existing) {
  const palette = ['violet', 'amber', 'emerald', 'rose', 'sky', 'orange', 'lime', 'pink'];
  const m = existing || { name: '', emoji: '🙂', color: 'violet' };
  const html = `
    <div class="field"><label>Name</label>
      <div style="display:flex;gap:8px">
        <input id="m-emoji" value="${esc(m.emoji)}" style="width:64px;text-align:center" aria-label="Emoji" />
        <input id="m-name" value="${esc(m.name)}" placeholder="Sam" />
      </div></div>
    <div class="field"><label>Colour</label>
      <div class="chip-select" id="m-colors">
        ${palette.map((c) => `
          <button type="button" class="chip ${m.color === c ? 'on' : ''}" data-color="${c}">
            <span class="avatar" style="--c:var(--${c});width:18px;height:18px"></span>${c}
          </button>`).join('')}
      </div></div>
    <button class="btn btn-primary btn-block" id="m-save">${existing ? 'Save' : 'Add roommate'}</button>
  `;
  openSheet(existing ? 'Edit roommate' : 'Add roommate', html, (root) => {
    let color = m.color;
    root.querySelector('#m-colors').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-color]');
      if (!btn) return;
      color = btn.dataset.color;
      root.querySelectorAll('[data-color]').forEach((b) => b.classList.toggle('on', b.dataset.color === color));
    });
    root.querySelector('#m-save').addEventListener('click', async () => {
      const name = root.querySelector('#m-name').value.trim();
      if (!name) { toast('Name?'); return; }
      const payload = { name, emoji: root.querySelector('#m-emoji').value.trim() || '🙂', color };
      const saved = existing
        ? await PUT(`/api/members/${existing.id}`, payload)
        : await POST('/api/members', payload);
      closeSheet();
      // The first roommate added on a fresh device is almost certainly you.
      if (!existing && !state.me) await POST('/api/identify', { member_id: saved.id });
      await boot();
      toast(existing ? 'Saved' : `${name} added`);
    });
  });
}

function settingsSheet() {
  const html = `
    <div class="field"><label>You are</label>
      <div class="chip-select" id="s-who">
        ${state.members.map((m) => `
          <button class="chip ${isMe(m) ? 'on' : ''}" data-who="${m.id}">${esc(m.emoji)} ${esc(m.name)}</button>`).join('')}
      </div></div>
    <div class="field"><label>Roommates</label>
      <div class="stack">
        ${state.members.map((m) => `
          <div class="row">
            ${avatar(m, 30)}
            <div class="row-main"><div class="row-title">${esc(m.name)}</div></div>
            <button class="icon-btn" data-edit-member="${m.id}">✏️</button>
            <button class="icon-btn" data-remove-member="${m.id}">🗑</button>
          </div>`).join('')}
      </div>
      <button class="btn btn-block" id="s-add">+ Add roommate</button>
    </div>
    <button class="btn btn-ghost btn-block" id="s-out">Sign this device out</button>
  `;
  openSheet('Settings', html, (root) => {
    root.querySelector('#s-who').addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-who]');
      if (!btn) return;
      await POST('/api/identify', { member_id: Number(btn.dataset.who) });
      closeSheet();
      await boot();
      toast('Switched');
    });
    root.querySelector('#s-add').addEventListener('click', () => memberForm());
    root.addEventListener('click', async (e) => {
      const edit = e.target.closest('[data-edit-member]');
      if (edit) {
        memberForm(state.members.find((m) => m.id === Number(edit.dataset.editMember)));
        return;
      }
      const remove = e.target.closest('[data-remove-member]');
      if (remove) {
        const member = state.members.find((m) => m.id === Number(remove.dataset.removeMember));
        if (!confirm(`Remove ${member.name}? Their past chores and expenses stay on the record.`)) return;
        await DEL(`/api/members/${member.id}`);
        closeSheet();
        await boot();
        toast('Removed');
      }
    });
    root.querySelector('#s-out').addEventListener('click', async () => {
      await POST('/api/logout', {});
      state.me = null;
      location.reload();
    });
  });
}

async function onboarding() {
  $('#view').innerHTML = `
    <div class="section" style="text-align:center;padding-top:24px">
      <div style="font-size:44px">🏡</div>
      <h2 style="font-size:22px;margin-top:8px">Welcome to ${esc(state.house)}</h2>
      <p class="muted" style="margin-top:6px">Start by adding everyone who lives here.</p>
      <button class="btn btn-primary btn-block" data-action="add-member" style="max-width:280px;margin:18px auto 0">
        + Add the first roommate
      </button>
    </div>`;
  syncTabs();
}

// --------------------------------------------------- delegated view events ---

$('#view').addEventListener('click', async (e) => {
  const goto = e.target.closest('[data-goto]');
  if (goto) { state.tab = goto.dataset.goto; render(); return; }

  const monthBtn = e.target.closest('[data-month]');
  if (monthBtn) {
    if (monthBtn.disabled) return;
    state.recapMonth = monthBtn.dataset.month;
    renderRecap();
    return;
  }

  const action = e.target.closest('[data-action]');
  if (action) {
    const map = {
      'add-chore': () => choreForm(),
      'add-event': eventForm,
      'add-expense': expenseForm,
      'add-member': () => memberForm(),
      'starter-chores': addStarterChores,
      'add-note': noteForm,
      'clear-bought': async () => { await POST('/api/shopping/clear', {}); toast('Cleared'); render(); },
      'settle-all': async () => {
        if (!confirm('Mark everything as settled? Balances reset to zero.')) return;
        await POST('/api/expenses/settle', {}); toast('All square'); render();
      },
    };
    if (map[action.dataset.action]) { map[action.dataset.action](); return; }
  }

  const complete = e.target.closest('[data-complete]');
  if (complete) {
    const id = Number(complete.dataset.complete);
    complete.classList.add('on');           // optimistic; render() reconciles
    try {
      await POST(`/api/chores/${id}/complete`, {});
      toast('Nice — done ✨', { label: 'Undo', run: () => undoCompletion(id) });
    } catch (err) { toast(err.message); }
    render();
    return;
  }

  const open = e.target.closest('[data-open-chore]');
  if (open) {
    const chore = (state.data.chores || []).find((c) => c.id === Number(open.dataset.openChore))
      || [...(state.data.summary?.overdue || []), ...(state.data.summary?.due_today || [])]
        .find((c) => c.id === Number(open.dataset.openChore));
    if (chore) choreDetail(chore);
    return;
  }

  const buy = e.target.closest('[data-buy]');
  if (buy) { await POST(`/api/shopping/${buy.dataset.buy}/toggle`, {}); renderShopping(); return; }

  const delShop = e.target.closest('[data-del-shop]');
  if (delShop) { await DEL(`/api/shopping/${delShop.dataset.delShop}`); renderShopping(); return; }

  const delEvent = e.target.closest('[data-del-event]');
  if (delEvent) {
    if (!confirm('Delete this event?')) return;
    await DEL(`/api/events/${delEvent.dataset.delEvent}`); render(); return;
  }

  const delExpense = e.target.closest('[data-del-expense]');
  if (delExpense) {
    if (!confirm('Delete this expense? Balances will be recalculated.')) return;
    await DEL(`/api/expenses/${delExpense.dataset.delExpense}`); render(); return;
  }

  const pin = e.target.closest('[data-pin]');
  if (pin) { await POST(`/api/notes/${pin.dataset.pin}/pin`, {}); render(); return; }

  const delNote = e.target.closest('[data-del-note]');
  if (delNote) {
    if (!confirm('Delete this post?')) return;
    await DEL(`/api/notes/${delNote.dataset.delNote}`); render();
  }
});

function noteForm() {
  const html = `
    <div class="field"><label>Post to the house board</label>
      <textarea id="n-body" placeholder="Wifi is CasitaGuest / hunter2&#10;Landlord comes Thursday" style="min-height:120px"></textarea></div>
    <label class="chip" style="display:inline-flex">
      <input type="checkbox" id="n-pin" style="width:auto;min-height:0" /> Pin to top
    </label>
    <button class="btn btn-primary btn-block" id="n-save">Post</button>
  `;
  openSheet('New post', html, (root) => {
    root.querySelector('#n-save').addEventListener('click', async () => {
      const body = root.querySelector('#n-body').value.trim();
      if (!body) { toast('Say something'); return; }
      await POST('/api/notes', { body, pinned: root.querySelector('#n-pin').checked });
      closeSheet(); toast('Posted'); render();
    });
  });
}

// ------------------------------------------------------------------- start ---

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* offline shell is a bonus, not a requirement */ });
  });
}

boot().catch(() => showGate());
