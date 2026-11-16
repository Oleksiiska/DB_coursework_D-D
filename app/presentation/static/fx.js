// Ефекти інтерфейсу D&D Adventures: жарини у повітрі, лавовий повзунок,
// блискавка на дію користувача й «вогняний розлам» між розділами.
// Жоден ефект не впливає на дані: за prefers-reduced-motion усе вимикається
// або зводиться до миттєвої зміни.
"use strict";

(function () {
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const DPR = () => Math.min(window.devicePixelRatio || 1, 2);

  function fitCanvas(canvas) {
    const ratio = DPR();
    canvas.width = Math.round(window.innerWidth * ratio);
    canvas.height = Math.round(window.innerHeight * ratio);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    return ctx;
  }

  // ------------------------------------------------------------ жарини
  // Невелика кількість іскор, що повільно підіймаються від нижнього краю.
  // Пауза, коли вкладка прихована; вимкнено за reduced motion.
  function embers() {
    const canvas = document.getElementById("embers");
    if (!canvas) return;
    let ctx = fitCanvas(canvas);
    const sparks = [];
    const spawn = (anywhere) => ({
      x: Math.random() * window.innerWidth,
      y: anywhere ? Math.random() * window.innerHeight : window.innerHeight + 10,
      r: 0.6 + Math.random() * 1.8,
      vy: 0.25 + Math.random() * 0.6,
      drift: (Math.random() - 0.5) * 0.4,
      phase: Math.random() * Math.PI * 2,
      life: 0.4 + Math.random() * 0.6,
    });
    for (let i = 0; i < 38; i++) sparks.push(spawn(true));
    window.addEventListener("resize", () => (ctx = fitCanvas(canvas)));
    let last = performance.now();
    function frame(now) {
      const dt = Math.min(50, now - last) / 16.7;
      last = now;
      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
      if (!document.hidden && !reduced()) {
        ctx.globalCompositeOperation = "lighter";
        for (const s of sparks) {
          s.y -= s.vy * dt;
          s.phase += 0.03 * dt;
          s.x += (s.drift + Math.sin(s.phase) * 0.25) * dt;
          const fade = Math.min(1, s.y / (window.innerHeight * 0.85));
          const alpha = Math.max(0, s.life * fade * (0.55 + 0.45 * Math.sin(s.phase * 3)));
          const glow = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.r * 5);
          glow.addColorStop(0, `rgba(255,214,140,${alpha})`);
          glow.addColorStop(0.35, `rgba(255,120,40,${alpha * 0.6})`);
          glow.addColorStop(1, "rgba(255,80,20,0)");
          ctx.fillStyle = glow;
          ctx.beginPath();
          ctx.arc(s.x, s.y, s.r * 5, 0, Math.PI * 2);
          ctx.fill();
          if (s.y < -20) Object.assign(s, spawn(false));
        }
        ctx.globalCompositeOperation = "source-over";
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------ блискавка
  // Ламана від верхнього краю до цілі: середня точка зміщується на половину
  // довжини відрізка, доки відрізки не стануть короткими; кілька гілок.
  function boltPath(x1, y1, x2, y2, spread) {
    let points = [[x1, y1], [x2, y2]];
    let offset = spread;
    while (offset > 3) {
      const next = [points[0]];
      for (let i = 1; i < points.length; i++) {
        const [ax, ay] = points[i - 1];
        const [bx, by] = points[i];
        const mx = (ax + bx) / 2 + (Math.random() - 0.5) * offset;
        const my = (ay + by) / 2 + (Math.random() - 0.5) * offset * 0.35;
        next.push([mx, my], [bx, by]);
      }
      points = next;
      offset /= 2;
    }
    return points;
  }

  function strokePath(ctx, points, width, color, blur) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.shadowColor = "rgba(157,184,255,0.9)";
    ctx.shadowBlur = blur;
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (const [x, y] of points.slice(1)) ctx.lineTo(x, y);
    ctx.stroke();
    ctx.restore();
  }

  let striking = false;
  function strike(target) {
    // ціль — елемент сторінки або точка {x, y} у координатах вікна
    const isElement = target && typeof target.getBoundingClientRect === "function";
    const rect = isElement ? target.getBoundingClientRect() : null;
    const tx = rect ? rect.left + rect.width / 2 : target && target.x != null ? target.x : window.innerWidth / 2;
    const ty = rect ? rect.top + rect.height / 2 : target && target.y != null ? target.y : window.innerHeight / 2;
    if (reduced()) {
      if (isElement) target.animate([{ boxShadow: "0 0 0 0 rgba(157,184,255,0)" }, { boxShadow: "0 0 0 6px rgba(157,184,255,.45)" },
        { boxShadow: "0 0 0 0 rgba(157,184,255,0)" }], { duration: 500 });
      return;
    }
    if (striking) return;
    striking = true;
    const canvas = document.getElementById("storm");
    const ctx = fitCanvas(canvas);
    const sx = tx + (Math.random() - 0.5) * Math.min(420, window.innerWidth * 0.5);
    const main = boltPath(sx, -10, tx, ty, Math.max(120, Math.hypot(tx - sx, ty) * 0.35));
    const branches = [];
    for (let i = 0; i < 3; i++) {
      const from = main[Math.floor(main.length * (0.2 + Math.random() * 0.5))];
      const angle = (Math.random() < 0.5 ? -1 : 1) * (0.4 + Math.random() * 0.6);
      const length = 60 + Math.random() * 140;
      branches.push(boltPath(from[0], from[1], from[0] + Math.sin(angle) * length, from[1] + Math.cos(angle) * length, 40));
    }
    const sparks = Array.from({ length: 34 }, () => {
      const a = Math.random() * Math.PI * 2;
      const v = 2 + Math.random() * 6;
      return { x: tx, y: ty, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 2, life: 1 };
    });
    const flash = document.getElementById("flash");
    if (flash) {
      flash.style.setProperty("--fx", `${tx}px`);
      flash.style.setProperty("--fy", `${ty}px`);
      flash.animate([{ opacity: 0 }, { opacity: 1, offset: 0.08 }, { opacity: 0.15, offset: 0.25 }, { opacity: 0.7, offset: 0.33 }, { opacity: 0 }],
        { duration: 700, easing: "ease-out" });
    }
    // струшується лише вміст розділу: transform на body зсунув би полотна з position: fixed
    document.getElementById("view")?.animate([
      { transform: "translate(0,0)" }, { transform: "translate(-4px,2px)" }, { transform: "translate(3px,-3px)" },
      { transform: "translate(-2px,1px)" }, { transform: "translate(0,0)" }], { duration: 320, delay: 60 });
    const started = performance.now();
    function frame(now) {
      const t = now - started;
      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
      // спалах: два удари — 0–90 мс і 170–260 мс, потім згасання
      const on = t < 90 || (t > 170 && t < 260);
      const alpha = on ? 1 : Math.max(0, 1 - (t - 260) / 340) * (t > 260 ? 0.6 : 0.25);
      if (alpha > 0) {
        ctx.globalAlpha = alpha;
        strokePath(ctx, main, 12, "rgba(120,150,255,0.22)", 40);
        strokePath(ctx, main, 4.5, "rgba(190,210,255,0.75)", 18);
        strokePath(ctx, main, 1.8, "#ffffff", 6);
        for (const b of branches) {
          strokePath(ctx, b, 3, "rgba(170,195,255,0.5)", 12);
          strokePath(ctx, b, 1, "#f4f7ff", 4);
        }
        ctx.globalAlpha = 1;
      }
      if (t > 60) {
        ctx.globalCompositeOperation = "lighter";
        for (const s of sparks) {
          s.x += s.vx;
          s.y += s.vy;
          s.vy += 0.25;
          s.life -= 0.022;
          if (s.life <= 0) continue;
          ctx.fillStyle = `rgba(${s.life > 0.5 ? "255,236,190" : "255,140,60"},${s.life})`;
          ctx.fillRect(s.x, s.y, 2.2, 2.2);
        }
        ctx.globalCompositeOperation = "source-over";
      }
      if (t < 900) requestAnimationFrame(frame);
      else {
        ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
        striking = false;
      }
    }
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------ перехід між розділами
  // Старий вміст «згорає», новий розкривається колом від натиснутого пункту
  // меню, а по краю кола йде вогняне кільце (окремий елемент поверх сторінки).
  function transition(view, swap, origin, big) {
    if (reduced() || !view.firstElementChild) {
      swap();
      return Promise.resolve();
    }
    const x = origin ? origin.x : window.innerWidth / 2;
    const y = origin ? origin.y : 120;
    const out = view.animate(
      big
        ? [{ opacity: 1, filter: "none" }, { opacity: 0, filter: "brightness(1.7) sepia(.8) saturate(2.4) blur(2px)" }]
        : [{ opacity: 1 }, { opacity: 0 }],
      { duration: big ? 260 : 140, easing: "ease-in", fill: "forwards" });
    return out.finished.then(() => {
      swap();
      out.cancel();
      if (!big) {
        return view.animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], { duration: 200, easing: "ease-out" }).finished;
      }
      // коло розкриття покриває видиму частину розділу; вогняне кільце має той самий
      // радіус і ту саму криву, тож іде точно по краю кола
      const rect = view.getBoundingClientRect();
      const cx = x - rect.left;
      const cy = y - rect.top;
      const w = rect.width;
      const h = Math.max(window.innerHeight - rect.top, 1);
      const radius = Math.ceil(Math.max(Math.hypot(cx, cy), Math.hypot(w - cx, cy), Math.hypot(cx, h - cy), Math.hypot(w - cx, h - cy)));
      const timing = { duration: 820, easing: "cubic-bezier(.3,.05,.25,1)" };
      const ring = document.getElementById("rift");
      if (ring) {
        ring.style.setProperty("--rx", `${x}px`);
        ring.style.setProperty("--ry", `${y}px`);
        ring.style.width = ring.style.height = `${radius * 2}px`;
        ring.animate([{ transform: "translate(-50%,-50%) scale(0)" }, { transform: "translate(-50%,-50%) scale(1)" }], timing);
        ring.animate([{ opacity: 1 }, { opacity: 1, offset: 0.75 }, { opacity: 0 }], { duration: timing.duration });
      }
      const at = `${Math.round(cx)}px ${Math.round(cy)}px`;
      return view.animate([{ clipPath: `circle(0px at ${at})` }, { clipPath: `circle(${radius}px at ${at})` }], timing).finished;
    });
  }

  // ------------------------------------------------------------ лавовий повзунок
  // Над потоком лави здіймаються жарини: що вища швидкість, то гарячіша лава
  // (змінна --heat) і більше іскор.
  function lava(root) {
    const input = root.querySelector("input[type=range]");
    const canvas = root.querySelector("canvas");
    const ctx = canvas ? canvas.getContext("2d") : null;
    const sparks = [];
    let heat = 0;
    function set() {
      const p = (Number(input.value) - Number(input.min)) / (Number(input.max) - Number(input.min));
      heat = Math.max(0, Math.min(1, p));
      root.style.setProperty("--p", heat.toFixed(4));
      root.style.setProperty("--heat", heat.toFixed(3));
    }
    input.addEventListener("input", set);
    set();
    if (!ctx) return { set };
    function resize() {
      const ratio = DPR();
      canvas.width = canvas.clientWidth * ratio;
      canvas.height = canvas.clientHeight * ratio;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }
    resize();
    window.addEventListener("resize", resize);
    function frame() {
      if (!root.isConnected) return; // розділ закрито — анімацію зупинено
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      if (!reduced() && !document.hidden) {
        const surface = h - 22;
        const width = (w - 24) * heat;
        if (Math.random() < 0.08 + heat * 0.9) {
          sparks.push({ x: 12 + Math.random() * Math.max(4, width), y: surface, vy: 0.4 + Math.random() * (0.6 + heat * 1.8),
            vx: (Math.random() - 0.5) * 0.5, life: 1, r: 0.8 + Math.random() * (1 + heat * 1.6) });
        }
        ctx.globalCompositeOperation = "lighter";
        for (const s of sparks) {
          s.y -= s.vy;
          s.x += s.vx;
          s.life -= 0.012 + (1 - heat) * 0.01;
          if (s.life <= 0) continue;
          const g = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.r * 4);
          g.addColorStop(0, `rgba(255,230,170,${s.life})`);
          g.addColorStop(0.4, `rgba(255,120,30,${s.life * 0.55})`);
          g.addColorStop(1, "rgba(255,60,0,0)");
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.arc(s.x, s.y, s.r * 4, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.globalCompositeOperation = "source-over";
        for (let i = sparks.length - 1; i >= 0; i--) if (sparks[i].life <= 0) sparks.splice(i, 1);
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    return { set };
  }

  window.FX = {
    init() {
      embers();
    },
    strike,
    transition,
    lava,
  };
})();
