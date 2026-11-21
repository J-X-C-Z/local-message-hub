export function createReaderRefresh(send, { debounceMs = 500, maxDelayMs = 2000, fallbackMs = 30000 } = {}) {
  let timer, pending = null, firstQueued = null, busy = true, stopped = false;
  function schedule() {
    clearTimeout(timer);
    if (stopped || busy || !pending) return;
    const delay = Math.max(0, Math.min(debounceMs, firstQueued + maxDelayMs - Date.now()));
    timer = setTimeout(() => {
      const command = pending;
      pending = null; firstQueued = null;
      busy = true;
      if (!send(command)) busy = false;
    }, delay).unref();
  }
  function request(command = 'refresh') {
    if (stopped) return;
    firstQueued ??= Date.now();
    if (!pending || command === 'attachments') pending = command;
    schedule();
  }
  const fallback = setInterval(() => request(), fallbackMs).unref();
  return {
    request,
    done() { busy = false; schedule(); },
    stop() { stopped = true; clearTimeout(timer); clearInterval(fallback); },
  };
}
