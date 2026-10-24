"use strict";
// First-run screens in English or Spanish (FTUE #198): follows the system language. English strings are the keys,
// so a missing translation simply stays in English. window.t() for text built in JS, applyI18n() for the page.
(() => {
  const es = /^es\b/i.test(navigator.language || "");
  const ES = {
    "Welcome to Mnemos": "Te damos la bienvenida a Mnemos",
    "One memory for all your AI apps. Tell them your name once; they will remember it.": "Una sola memoria para todas tus IAs. Decile tu nombre una vez y lo van a recordar.",
    "What should your AIs call you?": "¿Cómo querés que te llamen tus IAs?",
    "Your name (optional)": "Tu nombre (opcional)",
    "Your memory keeps things apart in separate spaces:": "Tu memoria separa las cosas en espacios distintos:",
    "You can rename them later in Spaces.": "Podés cambiarles el nombre después en Espacios.",
    "Start": "Empezar",
    "Home": "Inicio", "Memory": "Memoria", "Apps": "Apps", "Skills": "Skills", "Spaces": "Espacios", "Import": "Importar",
    "App memories": "Memorias de las apps", "Graph": "Grafo", "Settings": "Ajustes",
    "Checking…": "Revisando…", "memories": "memorias", "apps connected": "apps conectadas", "skills": "skills",
    "See it work: tell one AI, ask another": "Probalo: contale a una IA, preguntale a otra",
    "Skip": "Saltar", "Copy": "Copiar", "Copied ✓": "Copiado ✓",
    "In one AI app, say:": "En una app de IA, decí:",
    "Remember that my favourite colour is green.": "Acordate de que mi color favorito es el verde.",
    "Mnemos saves it.": "Mnemos lo guarda.", "Waiting for the save…": "Esperando que se guarde…",
    "In a": "En otra", "different": "app de IA distinta", "AI app (or a new chat), ask:": "(o en un chat nuevo), preguntá:",
    "What is my favourite colour?": "¿Cuál es mi color favorito?",
    "Next step": "Próximo paso", "A suggestion for your memory": "Una sugerencia para tu memoria", "Latest": "Lo último",
    "All activity": "Toda la actividad",
    "Mnemos is working ✓": "Mnemos está funcionando ✓", "Almost there": "Ya casi",
    "Connect an AI app so it can use your memory": "Conectá una app de IA para que use tu memoria",
    "See apps": "Ver apps", "Find what my AIs know": "Buscar lo que saben mis IAs", "Copy the question": "Copiar la pregunta",
    "Not now": "Ahora no", "See all in Memory": "Ver todo en Memoria",
    "Nothing yet. What your AI apps read or save shows up here.": "Todavía nada. Acá aparece lo que tus IAs leen o guardan.",
    "What do you know about me?": "¿Qué sabés de mí?",
    "Open my memory": "Abrir mi memoria",
    "In the Mnemos window, press Connect next to the AI apps you use, then restart them. They show up here after their first message.": "En la ventana de Mnemos, tocá Conectar al lado de las IAs que usás y reinicialas. Aparecen acá después de su primer mensaje.",
    "Your memory is empty. Mnemos can find what your AI apps already know about you; nothing is saved until you review it.": "Tu memoria está vacía. Mnemos puede buscar lo que tus IAs ya saben de vos; no se guarda nada hasta que lo revises.",
    "Try it: in any connected AI, ask": "Probalo: en cualquier IA conectada, preguntá",
  };
  window.MNEMOS_LANG = es ? "es" : "en";
  window.t = (s) => (es && ES[s]) || s;
  window.applyI18n = (root) => {
    if (!es) return;
    const w = document.createTreeWalker(root || document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const k = n.nodeValue.trim();
      if (k && ES[k]) n.nodeValue = n.nodeValue.replace(k, ES[k]);
    }
    (root || document).querySelectorAll("[placeholder]").forEach((el) => { if (ES[el.placeholder]) el.placeholder = ES[el.placeholder]; });
    document.documentElement.lang = "es";
  };
  document.addEventListener("DOMContentLoaded", () => ["welcome", "tabs", "view-home"].forEach((id) => { const el = document.getElementById(id); if (el) window.applyI18n(el); }));
})();
