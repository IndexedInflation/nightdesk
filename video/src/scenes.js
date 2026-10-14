// Public video diagrams use the narration only, never private account screenshots.
(function () {
  const { D, BEATS } = Stage;
  const escape = (text) => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icons = {what:'☾', setup:'↓', day:'↗', tests:'✓', privacy:'⌂', finish:'◎'};
  window.SCENES = {};
  for (const scene of D.script.scenes) {
    window.SCENES[scene.id] = () => '<div class="area">' + scene.beats.map((beat, i) => {
      const next = scene.beats[i + 1];
      const statement = escape(beat.say).replace(/\*([^*]+)\*/g, '<span class="amber">$1</span>');
      return `<div class="panel" data-at="${beat.id}" data-fx="fade" ${next ? `data-out="${next.id}"` : ''}>
        <div class="center" style="gap:34px">
        <div class="tag">Chapter ${scene.chapter} · ${escape(scene.title)}</div>
        <div style="font-size:130px;color:#f2b24c">${icons[scene.id] || '○'}</div>
        <h2 style="max-width:1450px;font-size:70px;line-height:1.25">${statement}</h2>
        <div class="sub" style="font-size:28px;color:#a7b3c5">Practice money · Your computer · Your choice</div>
        </div></div>`;
    }).join('') + '</div>';
  }
})();
