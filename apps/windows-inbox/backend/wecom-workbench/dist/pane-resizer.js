const layout = document.getElementById('messages-view');
const list = document.getElementById('conversation-panel');
const handle = document.getElementById('conversation-resizer');
const key = 'inbox.conversationWidth.v1';
const defaultWidth = 348;
const minWidth = 220;
const maxWidth = 560;
const chatAndDivider = 321;
let preferredWidth = defaultWidth;
let drag = null;

try {
  const savedWidth = Number(localStorage.getItem(key));
  if (savedWidth >= minWidth && savedWidth <= maxWidth) preferredWidth = savedWidth;
} catch {}

function limits() {
  const available = layout.clientWidth - chatAndDivider;
  return Math.max(minWidth, Math.min(maxWidth, available));
}

function showWidth(width = preferredWidth) {
  const maximum = limits();
  const visibleWidth = Math.max(minWidth, Math.min(maximum, Math.round(width)));
  layout.style.setProperty('--conversation-width', `${visibleWidth}px`);
  handle.setAttribute('aria-valuemin', minWidth);
  handle.setAttribute('aria-valuemax', maximum);
  handle.setAttribute('aria-valuenow', visibleWidth);
  handle.setAttribute('aria-valuetext', `${visibleWidth} 像素`);
  return visibleWidth;
}

function save(width) {
  preferredWidth = Math.max(minWidth, Math.min(maxWidth, Math.round(width)));
  showWidth();
  try {
    if (preferredWidth === defaultWidth) localStorage.removeItem(key);
    else localStorage.setItem(key, String(preferredWidth));
  } catch {}
}

handle.addEventListener('pointerdown', event => {
  if (drag || event.button !== 0 || !event.isPrimary) return;
  handle.focus({ preventScroll: true });
  event.preventDefault();
  handle.setPointerCapture(event.pointerId);
  drag = { id: event.pointerId, x: event.clientX, start: list.offsetWidth, width: list.offsetWidth, moved: false };
  handle.classList.add('is-dragging');
  layout.classList.add('is-resizing');
});

handle.addEventListener('pointermove', event => {
  if (drag?.id !== event.pointerId) return;
  const delta = event.clientX - drag.x;
  if (!drag.moved && Math.abs(delta) < 2) return;
  drag.moved = true;
  drag.width = showWidth(drag.start + delta);
});

function finish(event) {
  if (drag?.id !== event.pointerId) return;
  const completed = drag;
  drag = null;
  handle.classList.remove('is-dragging');
  layout.classList.remove('is-resizing');
  if (event.type === 'pointerup' && completed.moved) save(completed.width);
  else showWidth();
}

for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) handle.addEventListener(type, finish);
handle.addEventListener('dblclick', () => save(defaultWidth));
handle.addEventListener('keydown', event => {
  const step = event.shiftKey ? 30 : 10;
  const actions = {
    ArrowLeft: () => list.offsetWidth - step,
    ArrowRight: () => list.offsetWidth + step,
    Home: () => minWidth,
    End: () => limits(),
    Enter: () => defaultWidth,
  };
  if (!Object.hasOwn(actions, event.key)) return;
  event.preventDefault();
  const width = actions[event.key]();
  save(event.key === 'Enter' ? width : Math.min(width, limits()));
});

showWidth();
new ResizeObserver(() => showWidth(drag?.moved ? drag.width : preferredWidth)).observe(layout);
