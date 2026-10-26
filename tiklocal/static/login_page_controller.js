const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;
const input = document.getElementById('password');
const toggle = document.querySelector('.password-toggle');
if (window.matchMedia('(min-width: 761px)').matches) input.focus({ preventScroll: true });
toggle.addEventListener('click', () => {
  const showing = input.type === 'text';
  input.type = showing ? 'password' : 'text';
  toggle.setAttribute('aria-pressed', String(!showing));
  toggle.setAttribute('aria-label', showing ? T('login.show_password') : T('login.hide_password'));
  input.focus();
});
