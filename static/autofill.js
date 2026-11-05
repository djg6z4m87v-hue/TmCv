/* Runs only in the applicant's browser; no network requests or submission clicks. */
function careerpilotFill(data) {
  if (location.protocol !== 'https:' || location.hostname !== data.host) {
    alert('Open the saved employer application website (' + data.host + ') before running Careerpilot autofill. Update the application link in Careerpilot if the employer uses a different website.');
    return {filled: 0, blocked: true};
  }
  const privateQuestion = /password|ethnic|gender|disab|religio|sexual|criminal|convict|visa|sponsor|right to work|consent|certif|declar|signature|terms|salary|date of birth/i;
  let filled = 0;
  const pending = [];
  for (const e of document.querySelectorAll('input,textarea,select')) {
    if (e.disabled || e.readOnly || !e.getClientRects().length || /^(hidden|file|submit|button|reset|password)$/i.test(e.type)) continue;
    const label = (Array.from(e.labels || []).map(l => l.innerText || l.textContent).join(' ') || e.getAttribute('aria-label') || e.placeholder || e.name || e.id || '').trim();
    const key = [label, e.name, e.id, e.autocomplete, e.type].join(' ').replace(/[_\-]/g, ' ');
    if (privateQuestion.test(key)) { if (e.required) pending.push(label); continue; }
    const exact = Object.prototype.hasOwnProperty.call(data.answers, label);
    let value = exact ? data.answers[label] : undefined;
    if (value === undefined && !/checkbox|radio/.test(e.type)) {
      if (/first.?name|given.?name/i.test(key)) value = data.first;
      else if (/last.?name|surname|family.?name/i.test(key)) value = data.last;
      else if (/full.?name|your name|^name\b/i.test(key)) value = data.name;
      else if (/e.?mail/i.test(key)) value = data.email;
      else if (/phone|mobile|telephone|\btel\b/i.test(key)) value = data.phone;
      else if (/cover.?letter|supporting statement|additional information/i.test(key)) value = data.letter;
      else if (/linkedin/i.test(key)) value = data.linkedin;
      else if (/current (?:company|employer)/i.test(key)) value = data.current_company;
      else if (/^(?:town|city)(?:\s|$)/i.test(key)) value = data.location;
    }
    // Preserve existing answers, including autofilled browser values.
    if (e.type === 'checkbox' || e.type === 'radio') {
      if (exact && e.type === 'checkbox' && typeof value === 'boolean' && !e.checked && value) { e.click(); filled++; }
      else if (exact && e.type === 'radio' && String(value) === e.value && !e.checked) { e.click(); filled++; }
      else if (e.required && !e.checked) pending.push(label);
      continue;
    }
    if (e.value && e.value.trim()) continue;
    if (value === undefined || value === null || String(value).trim() === '') { if (e.required) pending.push(label); continue; }
    if (e.tagName === 'SELECT') {
      const options = Array.from(e.options).filter(o => o.value === String(value) || o.text === String(value));
      if (options.length !== 1) { if (e.required) pending.push(label); continue; }
      value = options[0].value;
    }
    const prototype = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(e, String(value));
    e.dispatchEvent(new Event('input', {bubbles:true}));
    e.dispatchEvent(new Event('change', {bubbles:true}));
    filled++;
  }
  alert('Careerpilot filled ' + filled + ' fields. Review every answer, upload your CV, complete remaining questions and submit on the employer website.' + (pending.length ? '\nRequired questions to check: ' + [...new Set(pending)].slice(0,8).join('; ') : ''));
  return {filled, pending};
}
