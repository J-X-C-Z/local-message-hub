'use strict';

const form = document.querySelector('#unlock-form');
const button = form.querySelector('button');
const input = form.querySelector('input');
const error = document.querySelector('#lock-error');
let pending = false;
window.lucide?.createIcons({attrs: {'stroke-width': 1.7}});

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (pending) return;
  const passphrase = new FormData(form).get('passphrase');
  pending = true;
  button.disabled = true;
  button.textContent = '正在验证…';
  input.disabled = true;
  form.setAttribute('aria-busy', 'true');
  error.textContent = '';
  try {
    const response = await fetch('/api/account-safety/unlock', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({passphrase})
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(result.error || '解锁失败');
    }
    location.replace('/');
  } catch (problem) {
    error.textContent = problem.message;
    input.value = '';
    input.focus();
  } finally {
    pending = false;
    button.disabled = false;
    button.textContent = '解锁';
    input.disabled = false;
    form.removeAttribute('aria-busy');
  }
});
